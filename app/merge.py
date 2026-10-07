"""Two recordings of one calendar meeting become one (D89).

D88 keeps a rejoin in the same recording while it can: while the first part is still
waiting to be transcribed. Once its transcription has begun, or for recordings made
before D88 (machine B's seven pieces of 2026-10-06), the parts are separate recordings of
the same meeting, and this joins them.

- The earlier recording is kept, under its own id. The later one's audio is appended to
  its files with the time between kept as silence, so every timestamp stays the meeting's.
- If both were transcribed, the later transcript is shifted onto that timeline and joined
  (its speakers renumbered after the earlier ones: diarization numbers each recording on
  its own, so THEM_1 in one is not THEM_1 in the other); then the transcript is assembled
  and summarized again. Otherwise the whole is transcribed.
- The later recording's id becomes an alias of the earlier, and its folder and rows go,
  only once everything above is written and the joined meeting is queued again. If its
  folder cannot go yet (Windows will not unlink a file something holds open), its rows
  go all the same and the folder is marked; the next start deletes it.
- A failure before the join is done puts the earlier recording back as it was: its audio
  files, manifest and transcript cut back or restored, and both recordings' jobs back.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from app import meta
from app.audio.writer import (
    HEADER_BYTES,
    MANIFEST_NAME,
    ChunkWriter,
    read_manifest,
    track_path,
)
from app.clock import iso, parse_iso
from app.log import get
from app.pipeline.states import MeetingState

if TYPE_CHECKING:
    from app.db.dao import Meeting
    from app.meetings import MeetingService

log = get(__name__)

#: Not merged while in any of these: being recorded, or not a meeting.
NOT_MERGEABLE = frozenset({MeetingState.RECORDING, MeetingState.ARMED, MeetingState.DISCARDED})
SPEAKER = re.compile(r"^(?P<side>[A-Z]+)_(?P<n>\d+)$")
#: Read and appended a minute at a time, whatever the recording's length.
BLOCK_SAMPLES = 16000 * 60


class MergeRefused(Exception):
    """The two cannot be merged now; the message says why."""


def tracks_of(folder: Path) -> list[str]:
    records, _ = read_manifest(folder)
    return sorted({r.track for r in records})


def _audio_ms(folder: Path) -> int:
    records, _ = read_manifest(folder)
    return max((r.t0_ms + r.dur_ms for r in records), default=0)


def _samples(folder: Path, track: str) -> int:
    path = track_path(folder, track)
    if not path.exists():
        return 0
    return max(0, (path.stat().st_size - HEADER_BYTES) // 2)


def preroll_ms(meeting: Meeting) -> int:
    """How much of the recording's audio comes before its ``started_at``: the pre-roll a
    detected call starts with, as the recorder wrote it down. None known (a recording made
    before it was), none subtracted."""
    return max(0, int(meta.read(meeting.path).get("preroll_ms") or 0))


def renumbered(speaker: str, after: dict[str, int]) -> str:
    """``THEM_2`` of the later recording, after the earlier's ``THEM_1..THEM_3``: ``THEM_5``."""
    found = SPEAKER.match(speaker)
    if not found:
        return speaker
    side = found["side"]
    return f"{side}_{int(found['n']) + after.get(side, 0)}"


def _highest(segments: list[Any]) -> dict[str, int]:
    top: dict[str, int] = {}
    for segment in segments:
        found = SPEAKER.match(segment.speaker)
        if found:
            top[found["side"]] = max(top.get(found["side"], 0), int(found["n"]))
    return top


def join_segments(first: Path, second: Path, offset_s: float) -> dict[str, Any]:
    """The earlier recording's segments.json with the later one's shifted and appended."""
    from app.pipeline.stages.transcribe import load_segments

    a, payload = load_segments(first)
    b, _ = load_segments(second)
    after = _highest(a)
    moved = [
        replace(segment.shifted(offset_s), speaker=renumbered(segment.speaker, after))
        for segment in b
    ]
    joined = [s.shifted(0.0, new_id=i) for i, s in enumerate([*a, *moved])]
    return {**payload, "segments": [s.as_dict() for s in joined]}


class Snapshot:
    """What a merge changes in the earlier recording's folder, as it was before: each
    track's length and header, the manifest and the transcript. The writer saves every
    chunk as it closes, so a merge that fails partway is undone by cutting back to this,
    never retried on top of what it had appended."""

    def __init__(self, folder: Path, tracks: list[str]) -> None:
        from app.pipeline.stages.transcribe import segments_path

        self.folder = folder
        self.files: dict[Path, tuple[int, bytes] | None] = {}
        for track in tracks:
            path = track_path(folder, track)
            if path.exists():
                with path.open("rb") as handle:
                    self.files[path] = (path.stat().st_size, handle.read(HEADER_BYTES))
            else:
                self.files[path] = None
        self.texts: dict[Path, bytes | None] = {}
        for path in (folder / "audio" / MANIFEST_NAME, segments_path(folder)):
            self.texts[path] = path.read_bytes() if path.exists() else None

    def restore(self) -> None:
        """Put each back, the manifest first: it is what the next writer believes. One
        that cannot be put back does not stop the rest; the first failure is raised."""
        failed: OSError | None = None
        for path, text in self.texts.items():
            try:
                if text is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(text)
            except OSError as exc:
                failed = failed or exc
        for path, was in self.files.items():
            try:
                if was is None:
                    path.unlink(missing_ok=True)
                    continue
                size, header = was
                with path.open("r+b") as handle:
                    handle.truncate(size)
                    handle.seek(0)
                    handle.write(header)
            except OSError as exc:
                failed = failed or exc
        if failed is not None:
            raise failed


def append_audio(into: Path, other: Path, gap_ms: int) -> int:
    """Append ``other``'s audio to ``into``'s files, after ``gap_ms`` of silence. Where the
    later recording starts on the joined timeline, in milliseconds."""
    tracks = sorted(set(tracks_of(into)) | set(tracks_of(other)))
    writer = ChunkWriter(into, tracks=tuple(tracks), resume=True)
    offset_ms = max((writer.state[t].t0_ms for t in tracks), default=0) + gap_ms
    try:
        for track in tracks:
            # Each track ends where the longest one does, then the gap: one timeline.
            behind = offset_ms - writer.state[track].t0_ms - writer.state[track].pending_gap_ms
            writer.note_gap(track, behind)
            path = track_path(other, track)
            total = _samples(other, track)
            if total == 0:
                continue
            with path.open("rb") as handle:
                handle.seek(HEADER_BYTES)
                remaining = total
                while remaining > 0:
                    count = min(BLOCK_SAMPLES, remaining)
                    data = np.frombuffer(handle.read(count * 2), dtype=np.int16)
                    if len(data) == 0:
                        break
                    writer.write_pcm(track, data)
                    remaining -= len(data)
        writer.close()
    except BaseException:
        writer.abandon()  # the merge undoes what it wrote; open files would stop that
        raise
    return offset_ms


def merge(service: MeetingService, first_id: str, second_id: str) -> Meeting:
    """Merge two recordings; the earlier survives. Raises :class:`MergeRefused`."""
    dao, queue = service.dao, service.queue
    a, b = dao.require_meeting(first_id), dao.require_meeting(second_id)
    if a.id == b.id:
        raise MergeRefused("a recording cannot be merged with itself")
    if parse_iso(b.started_at) < parse_iso(a.started_at):
        a, b = b, a
    for meeting in (a, b):
        if MeetingState(meeting.state) in NOT_MERGEABLE:
            raise MergeRefused(f"{meeting.id} is {meeting.state}")
        if not tracks_of(meeting.path):
            raise MergeRefused(f"{meeting.id} has no audio to merge")
    if not queue.withdraw(a.id):
        raise MergeRefused(f"{a.id} is being processed")
    if not queue.withdraw(b.id):
        service.resume_jobs(a.id)
        raise MergeRefused(f"{b.id} is being processed")

    a_end = parse_iso(a.ended_at) if a.ended_at else parse_iso(a.started_at)
    apart_ms = round((parse_iso(b.started_at) - a_end).total_seconds() * 1000)
    # The later audio starts before its start, by its pre-roll: placed that much earlier.
    gap_ms = max(0, apart_ms - preroll_ms(b))
    from app.pipeline.stages.transcribe import segments_path

    both_transcribed = segments_path(a.path).exists() and segments_path(b.path).exists()
    before = Snapshot(a.path, sorted(set(tracks_of(a.path)) | set(tracks_of(b.path))))
    try:
        offset_ms = append_audio(a.path, b.path, gap_ms)
        if both_transcribed:
            joined = join_segments(a.path, b.path, offset_ms / 1000)
            segments_path(a.path).write_text(
                json.dumps(joined, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        total_ms = _audio_ms(a.path)
        ends = [parse_iso(x) for x in (a.ended_at, b.ended_at) if x]
        fields: dict[str, Any] = {
            "ended_at": iso(max(ends)) if ends else a.ended_at,
            "duration_s": round(total_ms / 1000),
        }
        if not _matched(a) and _matched(b):
            fields["calendar_json"] = b.calendar_json
            fields["calendar_account_id"] = b.calendar_account_id
        dao.update_meeting(a.id, **fields)
    except Exception:
        # Both carry on as they were, and a later merge starts from there.
        try:
            before.restore()
        except OSError:
            log.exception("could not undo the failed merge of %s into %s", b.id, a.id)
        service.resume_jobs(a.id)
        service.resume_jobs(b.id)
        raise
    dao.add_alias(b.id, a.id)
    merged = dao.require_meeting(a.id)
    meta.mirror(merged)
    service.redo_after_merge(merged, transcribed=both_transcribed)
    _remove(service, dao.require_meeting(b.id))
    log.info(
        "merged %s into %s: %d s apart, %d s in all",
        b.id,
        a.id,
        gap_ms // 1000,
        total_ms // 1000,
    )
    return merged


def _remove(service: MeetingService, meeting: Meeting) -> None:
    """The merged-away recording goes. When its folder cannot yet, its rows go anyway, so
    nothing finds it, and the folder is marked for the next start to delete."""
    from app.meetings import DELETING_MARKER

    try:
        service.purge(meeting)
    except OSError as exc:
        log.warning("merged %s, but its folder stays until the next start: %s", meeting.id, exc)
        try:
            (meeting.path / DELETING_MARKER).write_text("", encoding="utf-8")
        except OSError:
            log.warning("could not mark %s for deletion", meeting.path)
        service.dao.delete_meeting(meeting.id)


def _matched(meeting: Meeting) -> bool:
    from app.meetings import calendar_payload

    return (calendar_payload(meeting).get("match") or {}).get("state") == "matched"


def started(meeting: Meeting) -> datetime:
    return parse_iso(meeting.started_at)
