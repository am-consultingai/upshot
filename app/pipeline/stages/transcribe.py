"""The transcribe stage: chunk WAVs → one segment list, language resolved once."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app import glossary as glossary_module
from app import meta
from app.asr.backend import AsrBackend, Segment
from app.asr.diarize import LONG_TRACK_MINUTES, assign_speakers
from app.asr.language import resolve_language
from app.audio.vad import read_wav
from app.audio.writer import ChunkRecord, recover
from app.log import get
from app.pipeline.artifacts import up_to_date
from app.pipeline.context import StageContext

log = get(__name__)

SEGMENTS_NAME = "segments.json"


def segments_path(folder: Path) -> Path:
    return Path(folder) / SEGMENTS_NAME


def _chunk_inputs(folder: Path) -> list[Path]:
    audio = Path(folder) / "audio"
    if not audio.exists():
        return []
    return sorted(audio.glob("*/*.wav"))


def first_chunks(records: list[ChunkRecord], folder: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for record in sorted(records, key=lambda r: (r.track, r.seq)):
        out.setdefault(record.track, Path(folder) / "audio" / record.file)
    return out


def build_initial_prompt(ctx: StageContext, previous_sentence: str | None) -> str | None:
    entries = glossary_module.merge(
        glossary_module.from_db(ctx.dao),
        glossary_module.load_yaml(ctx.config.glossary_path),
    )
    participants = _participants(ctx)
    return glossary_module.initial_prompt(
        entries,
        participants=participants,
        previous_sentence=previous_sentence,
        max_tokens=int(ctx.config.get("asr.initial_prompt_max_tokens", 200)),
    )


def _participants(ctx: StageContext) -> tuple[str, ...]:
    payload = ctx.meeting.calendar_json
    if not payload:
        return ()
    try:
        loaded = json.loads(payload)
    except ValueError:
        return ()
    names = loaded.get("participants") if isinstance(loaded, dict) else None
    return tuple(str(name) for name in names or ())


def backend_for(ctx: StageContext) -> AsrBackend:
    services = ctx.services
    backend = getattr(services, "asr", None) if services is not None else None
    if backend is not None:
        return backend  # type: ignore[no-any-return]
    from app.asr.factory import make_backend

    return make_backend(ctx.config)


def run(ctx: StageContext) -> None:
    folder = ctx.folder
    output = segments_path(folder)
    inputs = _chunk_inputs(folder)
    if not inputs:
        raise FileNotFoundError(f"no chunk files under {folder / 'audio'}")
    if up_to_date(output, inputs):
        log.info("transcript segments are current; skipping")
        return

    records = recover(folder)
    backend = backend_for(ctx)
    decision = resolve_language(backend, first_chunks(records, folder), ctx.config)
    ctx.dao.update_meeting(
        ctx.meeting.id, language=decision.language, language_conf=decision.confidence
    )
    if decision.review_reason:
        meta.add_review_reason(folder, decision.review_reason)
    log.info(
        "language %s (%s, p=%.2f) pinned for the whole meeting",
        decision.language,
        decision.source,
        decision.confidence,
    )

    segments: list[Segment] = []
    previous_sentence: str | None = None
    for record in sorted(records, key=lambda r: (r.t0_ms, r.track, r.seq)):
        ctx.checkpoint()
        wav = folder / "audio" / record.file
        if not wav.exists():
            log.warning("chunk %s is in the manifest but missing on disk", record.file)
            continue
        prompt = build_initial_prompt(ctx, previous_sentence)
        chunk_segments = backend.transcribe(
            wav, language=decision.language, initial_prompt=prompt, word_timestamps=True
        )
        offset_s = record.t0_ms / 1000.0
        for segment in chunk_segments:
            segments.append(segment.shifted(offset_s, new_id=len(segments)))
        if chunk_segments:
            previous_sentence = chunk_segments[-1].text[-200:]

    segments = _diarize(ctx, segments, records)

    payload: dict[str, Any] = {
        "version": 1,
        "language": decision.language,
        "language_conf": decision.confidence,
        "language_source": decision.source,
        "model": _describe(backend),
        "segments": [segment.as_dict() for segment in segments],
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    meta.mirror(
        ctx.refresh(),
        language=decision.language,
        language_conf=decision.confidence,
        asr=_describe(backend),
        **({"diarization": ctx.metrics["diarization"]} if "diarization" in ctx.metrics else {}),
    )
    # ASR and the LLM are never resident together (DESIGN.md §20.4).
    backend.unload()
    ctx.metrics["segments"] = len(segments)
    ctx.metrics["language"] = decision.language


def _diarize(
    ctx: StageContext, segments: list[Segment], records: list[ChunkRecord]
) -> list[Segment]:
    """Split THEM into THEM_1/2/3 over the whole track, when diarization is enabled.

    Diarizing per chunk would be worthless: the speaker ids would not agree across chunk
    boundaries. So the loopback track is reassembled on the meeting's timeline (gaps
    become silence) and clustered once.
    """
    from app.asr.diarize import concat_track, make_diarizer, speaker_count

    diarizer = make_diarizer(ctx.config)
    if diarizer is None:
        return segments
    rate = ctx.config.sample_rate
    chunks: list[tuple[float, Any]] = []
    for record in sorted(records, key=lambda r: r.seq):
        if record.track != "them":
            continue
        wav = ctx.folder / "audio" / record.file
        if not wav.exists():
            continue
        pcm, chunk_rate = read_wav(wav)
        if chunk_rate != rate:  # pragma: no cover - chunks are written at the storage rate
            continue
        chunks.append((record.t0_ms / 1000.0, pcm))
    if not chunks:
        return segments
    track = concat_track(chunks, rate)
    minutes = len(track) / rate / 60
    if minutes > LONG_TRACK_MINUTES:
        log.warning("diarizing %.0f minutes in one pass; this is memory-hungry", minutes)
    ctx.checkpoint()
    turns = diarizer.diarize(track, rate)
    diarizer.unload()
    speakers = speaker_count(turns)
    log.info("diarization found %d speaker(s) in %d turn(s)", speakers, len(turns))
    ctx.metrics["diarization"] = {
        "backend": diarizer.name,
        "speakers": speakers,
        "turns": len(turns),
    }
    return assign_speakers(segments, turns)


def _describe(backend: AsrBackend) -> dict[str, Any]:
    describe = getattr(backend, "describe", None)
    if callable(describe):
        return dict(describe())
    return {"name": backend.name}


def load_segments(folder: Path) -> tuple[list[Segment], dict[str, Any]]:
    payload = json.loads(segments_path(folder).read_text(encoding="utf-8"))
    from app.asr.backend import Word

    segments = [
        Segment(
            id=int(item["id"]),
            track=str(item["track"]),
            speaker=str(item["speaker"]),
            start=float(item["start"]),
            end=float(item["end"]),
            text=str(item["text"]),
            words=tuple(
                Word(str(w["w"]), float(w["s"]), float(w["e"]), float(w.get("p", 1.0)))
                for w in item.get("words", [])
            ),
            avg_logprob=float(item.get("avg_logprob", 0.0)),
            no_speech_prob=float(item.get("no_speech_prob", 0.0)),
        )
        for item in payload.get("segments", [])
    ]
    return segments, payload
