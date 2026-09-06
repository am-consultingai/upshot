"""Play a meeting as one recording.

Two files per track is right for the pipeline — transcription needs to know who spoke —
and wrong for a listener, who wants to press play once and hear the meeting. This mixes
the tracks *on read*: nothing extra is written to disk, and because both tracks are mono
16-bit PCM on the same timeline, output byte N maps to input byte N in each file. Range
requests therefore stay exact and cheap: seeking to minute 22 reads only that window from
each track, never the whole file.

Summed in int32 and clipped to int16 rather than averaged. Averaging would make every
meeting 6 dB quieter to guard against a case that only arises when two people are loud
at the same instant.

When ``meta.json`` carries an echo model (D37) the far side is subtracted from the near
track before the two are summed. Without that, a microphone bus carrying playback would
put the far side into the mix twice, a fifth of a second apart — an audible slap echo on
every remote voice. The delay is under half a second, so the reference window is simply
read at a shifted offset and range requests stay as cheap as before.
"""

from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.audio.echo import EchoModel, cancel
from app.audio.writer import HEADER_BYTES, track_files, wav_header
from app.log import get

log = get(__name__)

WIDTH = 2  # bytes per sample; the storage format is mono 16-bit


@dataclass(frozen=True)
class Source:
    track: str
    path: Path
    data_start: int  # where PCM begins in the file
    data_bytes: int


@dataclass(frozen=True)
class MixLayout:
    header: bytes
    sources: tuple[Source, ...]
    data_bytes: int
    size: int
    rate: int
    echo: EchoModel | None = None

    @property
    def duration_s(self) -> float:
        return self.data_bytes / float(self.rate * WIDTH)


def _describe(path: Path) -> tuple[Source, int] | None:
    try:
        with wave.open(str(path), "rb") as handle:
            rate = handle.getframerate()
            frames = handle.getnframes()
            width = handle.getsampwidth() * handle.getnchannels()
    except Exception as exc:  # still being written, or truncated by a crash
        log.warning("skipping %s in the mix: %s", path.name, exc)
        return None
    if width != WIDTH:  # pragma: no cover - the writer only produces mono 16-bit
        log.warning("skipping %s in the mix: not mono 16-bit", path.name)
        return None
    length = frames * width
    source = Source(
        track=path.stem,
        path=path,
        data_start=path.stat().st_size - length,
        data_bytes=length,
    )
    return source, rate


def layout_for(folder: Path) -> MixLayout | None:
    """Describe both tracks as one mixed WAV. ``None`` when there is nothing to play."""
    described = [
        described for path in track_files(folder).values() if (described := _describe(path))
    ]
    if not described:
        return None
    rates = {rate for _source, rate in described}
    if len(rates) > 1:  # pragma: no cover - both tracks are written at the storage rate
        log.warning("tracks disagree on sample rate %s; mixing the first only", sorted(rates))
        described = described[:1]
    rate = described[0][1]
    sources = tuple(source for source, _rate in described)
    # The meeting is as long as its longest track; the shorter one is silence after it.
    data_bytes = max(source.data_bytes for source in sources)
    return MixLayout(
        header=wav_header(data_bytes=data_bytes, rate=rate),
        sources=sources,
        data_bytes=data_bytes,
        size=HEADER_BYTES + data_bytes,
        rate=rate,
        echo=_echo_for(folder, sources),
    )


def _echo_for(folder: Path, sources: tuple[Source, ...]) -> EchoModel | None:
    """The subtraction the transcribe stage measured, when both tracks are here to do it."""
    tracks = {source.track for source in sources}
    if not {"me", "them"} <= tracks:
        return None
    from app import meta

    return EchoModel.from_dict(meta.read(folder).get("echo"))


def _read(source: Source, byte_lo: int, frames: int) -> np.ndarray:
    """``frames`` samples starting at ``byte_lo``, zero-padded off either end.

    ``byte_lo`` may be negative: the echo reference is read at a negative offset for the
    first fraction of a second of a meeting, where nothing has played yet.
    """
    out = np.zeros(frames, dtype=np.int32)
    pad = max(0, -byte_lo)
    begin = byte_lo + pad
    length = min(frames * WIDTH - pad, source.data_bytes - begin)
    if length <= 0:
        return out
    with source.path.open("rb") as handle:
        handle.seek(source.data_start + begin)
        payload = handle.read(length)
    payload = payload[: len(payload) - (len(payload) & 1)]
    if not payload:
        return out
    samples = np.frombuffer(payload, dtype=np.int16).astype(np.int32)
    start = pad // WIDTH
    out[start : start + samples.size] = samples
    return out


def read_range(layout: MixLayout, start: int, end: int) -> bytes:
    """Bytes ``start``..``end`` inclusive of the mixed file."""
    start = max(0, start)
    end = min(end, layout.size - 1)
    if end < start:
        return b""

    out = bytearray()
    if start < HEADER_BYTES:
        out += layout.header[start : min(end + 1, HEADER_BYTES)]

    first = max(start, HEADER_BYTES) - HEADER_BYTES
    last = end - HEADER_BYTES
    if last < first:
        return bytes(out)

    # Mixing is sample-wise, so read a sample-aligned window and trim afterwards: a range
    # may legitimately begin or end mid-sample.
    skew = first & 1
    lo = first - skew
    count = (last - first + 1) + skew
    count += count & 1

    frames = count // WIDTH
    tracks = {source.track: _read(source, lo, frames) for source in layout.sources}
    if layout.echo is not None:
        far = next(source for source in layout.sources if source.track == "them")
        reference = _read(far, lo - layout.echo.delay * WIDTH, frames)
        tracks["me"] = cancel(tracks["me"], reference, layout.echo.gain)

    total = np.zeros(frames, dtype=np.int32)
    for samples in tracks.values():
        total += samples

    mixed = np.clip(total, -32768, 32767).astype(np.int16).tobytes()
    out += mixed[skew : skew + (last - first + 1)]
    return bytes(out)
