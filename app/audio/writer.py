"""Resample → mono → int16 → chunk WAVs → manifest (TECHNICAL-DESIGN.md §4.2–§4.4).

The durability order is load-bearing and asserted by a test:

    writeframes → flush → fsync(wav) → append manifest line → fsync(manifest)

A chunk does not exist until its manifest line is durable.
"""

from __future__ import annotations

import json
import os
import wave
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from app.audio.vad import FRAME_MS, EnergyGate
from app.log import get

log = get(__name__)

INT16_MAX = 32767
MANIFEST_NAME = "manifest.jsonl"


@dataclass(frozen=True)
class ChunkRecord:
    seq: int
    track: str
    file: str
    t0_ms: int
    dur_ms: int
    samples: int
    closed: bool = True
    gap_ms: int = 0

    def as_line(self) -> str:
        payload: dict[str, Any] = {
            "seq": self.seq,
            "track": self.track,
            "file": self.file,
            "t0_ms": self.t0_ms,
            "dur_ms": self.dur_ms,
            "samples": self.samples,
            "closed": self.closed,
        }
        if self.gap_ms:
            payload["gap_ms"] = self.gap_ms
        return json.dumps(payload, ensure_ascii=False)


class Resampler:
    """48 kHz float32 stereo from the device → 16 kHz mono int16 on disk.

    Streaming, so a 10 s meeting resampled block by block has exactly the same sample
    count as one resampled in a single call.
    """

    def __init__(self, in_rate: int = 48000, out_rate: int = 16000, channels: int = 2) -> None:
        self.in_rate = in_rate
        self.out_rate = out_rate
        self.channels = channels
        self._stream: Any = None
        if in_rate != out_rate:
            import soxr

            self._stream = soxr.ResampleStream(in_rate, out_rate, 1, dtype="float32", quality="HQ")

    def to_mono(self, data: np.ndarray | bytes) -> np.ndarray:
        audio = (
            np.frombuffer(data, dtype=np.float32) if isinstance(data, bytes | bytearray) else data
        )
        audio = np.asarray(audio, dtype=np.float32)
        if self.channels > 1:
            usable = (len(audio) // self.channels) * self.channels
            audio = audio[:usable].reshape(-1, self.channels).mean(axis=1)
        return audio.astype(np.float32)

    def process(self, data: np.ndarray | bytes, *, last: bool = False) -> np.ndarray:
        """Device bytes → int16 mono at the storage rate."""
        mono = self.to_mono(data)
        if self._stream is not None:
            mono = np.asarray(self._stream.resample_chunk(mono, last=last), dtype=np.float32)
        return self.to_int16(mono)

    def flush(self) -> np.ndarray:
        """Drain the resampler's delay line (~30 ms) at end of stream.

        Without this every meeting loses its last few hundred samples and the sample
        totals do not add up.
        """
        return self.process(np.zeros(0, dtype=np.float32), last=True)

    @staticmethod
    def to_int16(mono: np.ndarray) -> np.ndarray:
        clipped = np.clip(mono, -1.0, 1.0)
        scaled: np.ndarray = np.round(clipped * INT16_MAX).astype(np.int16)
        return scaled


@dataclass
class TrackState:
    track: str
    seq: int = 0
    t0_ms: int = 0
    samples_written: int = 0
    pending: list[np.ndarray] = field(default_factory=list)
    pending_len: int = 0
    pending_gap_ms: int = 0

    def take(self, count: int) -> np.ndarray:
        buffer = np.concatenate(self.pending) if self.pending else np.zeros(0, dtype=np.int16)
        head, tail = buffer[:count], buffer[count:]
        self.pending = [tail] if len(tail) else []
        self.pending_len = len(tail)
        return head


class ChunkWriter:
    """Writes one meeting's chunk files and its manifest."""

    def __init__(
        self,
        folder: Path,
        *,
        tracks: tuple[str, ...] = ("me", "them"),
        rate: int = 16000,
        chunk_s: float = 60.0,
        silence_search_s: float = 10.0,
        hard_cut_s: float = 70.0,
        gate: EnergyGate | None = None,
        min_silence_ms: int = 300,
    ) -> None:
        self.folder = Path(folder)
        self.tracks = tracks
        self.rate = rate
        self.chunk_s = chunk_s
        self.silence_search_s = silence_search_s
        self.hard_cut_s = hard_cut_s
        self.gate = gate or EnergyGate()
        self.min_silence_ms = min_silence_ms
        self.state = {track: TrackState(track) for track in tracks}
        self.records: list[ChunkRecord] = []
        self.events: list[str] = []
        self._manifest: Any = None
        self._opened = False

    # -- paths -------------------------------------------------------------

    @property
    def audio_dir(self) -> Path:
        return self.folder / "audio"

    @property
    def manifest_path(self) -> Path:
        return self.audio_dir / MANIFEST_NAME

    def _ensure_open(self) -> None:
        """Nothing touches the disk until there is something real to write."""
        if self._opened:
            return
        for track in self.tracks:
            (self.audio_dir / track).mkdir(parents=True, exist_ok=True)
        self._manifest = self.manifest_path.open("a", encoding="utf-8")
        self._opened = True

    # -- writing -----------------------------------------------------------

    def write_pcm(self, track: str, samples: np.ndarray) -> None:
        state = self.state[track]
        samples = np.asarray(samples, dtype=np.int16)
        if len(samples) == 0:
            return
        state.pending.append(samples)
        state.pending_len += len(samples)
        self._maybe_cut(track)

    def note_gap(self, track: str, gap_ms: int) -> None:
        """Committed audio was lost: preserve the timeline instead of shortening it."""
        if gap_ms <= 0:
            return
        self.state[track].pending_gap_ms += int(gap_ms)
        log.warning("gap of %d ms on track %s", gap_ms, track)

    def _samples(self, seconds: float) -> int:
        return int(seconds * self.rate)

    def _maybe_cut(self, track: str) -> None:
        state = self.state[track]
        search_start = self._samples(self.chunk_s - self.silence_search_s)
        hard = self._samples(self.hard_cut_s)
        while state.pending_len >= search_start:
            buffer = np.concatenate(state.pending) if state.pending else np.zeros(0, np.int16)
            cut = self._find_cut(buffer, search_start, hard)
            if cut is None:
                return
            self.state[track].pending = [buffer]
            self.state[track].pending_len = len(buffer)
            self._close_chunk(track, cut)

    def _find_cut(self, buffer: np.ndarray, search_start: int, hard: int) -> int | None:
        """Prefer a VAD-silence boundary in the window; hard-cut when there is none.

        The *energy* stage decides boundaries: it is the always-on stage (§4.7) and it
        is the one that treats any sustained sound as activity, which is what a chunk
        boundary must avoid cutting through.
        """
        window_end = min(len(buffer), hard)
        if window_end <= search_start:
            return None
        per_frame = self.rate * FRAME_MS // 1000
        region = buffer[search_start:window_end]
        flags = self.gate.voiced_frames(region, self.rate)
        min_frames = max(1, self.min_silence_ms // FRAME_MS)
        run = 0
        for index, voiced in enumerate(flags):
            if voiced:
                run = 0
                continue
            run += 1
            if run >= min_frames:
                middle = index - run // 2
                return search_start + (middle + 1) * per_frame
        if len(buffer) >= hard:
            return hard
        return None

    def _close_chunk(self, track: str, count: int) -> ChunkRecord:
        state = self.state[track]
        samples = state.take(count)
        self._ensure_open()
        state.seq += 1
        name = f"{state.seq:04d}.wav"
        path = self.audio_dir / track / name
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(self.rate)
            handle.writeframes(samples.tobytes())
        self.events.append(f"write:{track}/{name}")
        with path.open("rb+") as raw:
            raw.flush()
            os.fsync(raw.fileno())
        self.events.append(f"fsync-wav:{track}/{name}")

        gap_ms = state.pending_gap_ms
        state.pending_gap_ms = 0
        t0_ms = state.t0_ms + gap_ms
        dur_ms = round(len(samples) * 1000 / self.rate)
        record = ChunkRecord(
            seq=state.seq,
            track=track,
            file=f"{track}/{name}",
            t0_ms=t0_ms,
            dur_ms=dur_ms,
            samples=len(samples),
            closed=True,
            gap_ms=gap_ms,
        )
        assert self._manifest is not None
        self._manifest.write(record.as_line() + "\n")
        self._manifest.flush()
        self.events.append(f"append-manifest:{track}/{name}")
        os.fsync(self._manifest.fileno())
        self.events.append(f"fsync-manifest:{track}/{name}")

        state.t0_ms = t0_ms + dur_ms
        state.samples_written += len(samples)
        self.records.append(record)
        return record

    def flush_track(self, track: str) -> ChunkRecord | None:
        state = self.state[track]
        if state.pending_len == 0:
            return None
        return self._close_chunk(track, state.pending_len)

    def close(self) -> list[ChunkRecord]:
        for track in self.tracks:
            self.flush_track(track)
        if self._manifest is not None:
            self._manifest.flush()
            os.fsync(self._manifest.fileno())
            self._manifest.close()
            self._manifest = None
        return list(self.records)

    # -- reporting ---------------------------------------------------------

    def duration_ms(self, track: str) -> int:
        state = self.state[track]
        return state.t0_ms + round(state.pending_len * 1000 / self.rate)

    def totals(self) -> dict[str, int]:
        return {track: self.state[track].samples_written for track in self.tracks}


# --------------------------------------------------------------------------- recovery


def read_manifest(folder: Path) -> tuple[list[ChunkRecord], int]:
    """Parse the manifest, tolerating a torn final line (an unclean shutdown)."""
    path = Path(folder) / "audio" / MANIFEST_NAME
    if not path.exists():
        return [], 0
    records: list[ChunkRecord] = []
    torn = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            torn += 1
            continue
        try:
            records.append(
                ChunkRecord(
                    seq=int(payload["seq"]),
                    track=str(payload["track"]),
                    file=str(payload["file"]),
                    t0_ms=int(payload["t0_ms"]),
                    dur_ms=int(payload["dur_ms"]),
                    samples=int(payload["samples"]),
                    closed=bool(payload.get("closed", True)),
                    gap_ms=int(payload.get("gap_ms", 0)),
                )
            )
        except (KeyError, TypeError, ValueError):
            torn += 1
    return records, torn


def wav_duration(path: Path) -> tuple[int, int]:
    with wave.open(str(path), "rb") as handle:
        frames = handle.getnframes()
        rate = handle.getframerate()
    return frames, round(frames * 1000 / rate)


def recover(folder: Path) -> list[ChunkRecord]:
    """Everything on disk, whether or not the manifest survived.

    Chunks whose manifest line was lost are re-derived from their WAV headers, so a
    meeting is reconstructible from disk with the app dead.
    """
    folder = Path(folder)
    records, _torn = read_manifest(folder)
    known = {record.file for record in records}
    audio_dir = folder / "audio"
    if not audio_dir.exists():
        return records
    for track_dir in sorted(p for p in audio_dir.iterdir() if p.is_dir()):
        track = track_dir.name
        by_seq = {record.seq: record for record in records if record.track == track}
        cursor = 0
        for wav_path in sorted(track_dir.glob("*.wav")):
            seq = int(wav_path.stem)
            existing = by_seq.get(seq)
            if existing is not None:
                cursor = existing.t0_ms + existing.dur_ms
                continue
            if f"{track}/{wav_path.name}" in known:
                continue
            samples, dur_ms = wav_duration(wav_path)
            records.append(
                ChunkRecord(
                    seq=seq,
                    track=track,
                    file=f"{track}/{wav_path.name}",
                    t0_ms=cursor,
                    dur_ms=dur_ms,
                    samples=samples,
                    closed=True,
                )
            )
            cursor += dur_ms
    records.sort(key=lambda record: (record.track, record.seq))
    return records


def manifest_as_dicts(records: list[ChunkRecord]) -> list[dict[str, Any]]:
    return [asdict(record) for record in records]
