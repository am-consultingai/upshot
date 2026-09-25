"""The transcribe stage: one WAV per track → one segment list, and the language it was in."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

from app import glossary as glossary_module
from app import meta
from app.asr.backend import AsrBackend, Segment
from app.asr.diarize import LONG_TRACK_MINUTES, assign_speakers
from app.asr.language import spoken_language
from app.asr.models import ASR_LANGUAGE
from app.audio.echo import EchoModel
from app.audio.vad import read_wav
from app.audio.writer import ChunkRecord, recover, track_files, track_path
from app.log import get
from app.pipeline.artifacts import up_to_date
from app.pipeline.context import StageContext

log = get(__name__)

SEGMENTS_NAME = "segments.json"

#: Where the echo-cancelled copy of a track lives while it is being transcribed.
CLEAN_DIR = "clean"


def segments_path(folder: Path) -> Path:
    return Path(folder) / SEGMENTS_NAME


def clean_path(folder: Path, track: str) -> Path:
    return Path(folder) / "audio" / CLEAN_DIR / f"{track}.wav"


def _chunk_inputs(folder: Path) -> list[Path]:
    return sorted(track_files(folder).values())


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


def _measure_echo(ctx: StageContext) -> EchoModel | None:
    """Measure the leak between the tracks, and decide whether to subtract it.

    A microphone pointed at a virtual bus that also carries playback records the far side
    a second time. Nothing downstream can tell that apart from two people saying the same
    thing, and the user hears every remote voice twice — so it is measured here, where
    both tracks are on disk, rather than left to be discovered by ear.

    The model returned (if any) is what the ASR input and the playback mixer both
    subtract. The recording itself is never touched.
    """
    from app.audio.analysis import CROSSTALK_THRESHOLD
    from app.audio.echo import measure

    mode = str(ctx.config.get("audio.echo_cancel", "auto"))
    files = track_files(ctx.folder)
    if len(files) < 2:
        return None
    try:
        model = measure(
            files["me"],
            files["them"],
            scan_s=float(ctx.config.get("audio.echo_scan_s", 600)),
            window_s=float(ctx.config.get("audio.echo_window_s", 60)),
        )
    except Exception as exc:  # pragma: no cover - diagnostics must never fail a meeting
        log.debug("could not measure crosstalk: %s", exc)
        return None
    if model is None:
        ctx.metrics["crosstalk"] = 0.0
        return None

    log.info(
        "track crosstalk %.2f at %.0f ms (gain %.2f, removes %.0f%%)",
        model.correlation,
        model.delay_ms,
        model.gain,
        model.reduction * 100,
    )
    ctx.metrics["crosstalk"] = round(model.correlation, 3)

    threshold = float(ctx.config.get("audio.echo_min_correlation", CROSSTALK_THRESHOLD))
    leaking = model.correlation >= threshold
    if not leaking and mode != "on":
        # A partial leak — the near voice diluting the copy — is deliberately left alone:
        # flagging it would flag every meeting held without headphones (D36).
        return None
    if leaking:
        log.warning(
            "the two tracks are %.0f%% the same signal — the selected microphone is "
            "capturing system audio, so the far side is recorded twice",
            model.correlation * 100,
        )
    if mode == "off":
        meta.add_review_reason(ctx.folder, "microphone is also capturing system audio")
        return None
    meta.update(ctx.folder, echo=model.as_dict())
    meta.add_review_reason(
        ctx.folder, "microphone is also capturing system audio (removed on playback)"
    )
    ctx.metrics["echo"] = model.as_dict()
    return model


def _asr_inputs(ctx: StageContext, model: EchoModel | None) -> dict[str, Path]:
    """The files handed to the ASR: the near track with the far side subtracted.

    Written beside the recording rather than over it — ``audio/clean/me.wav``, which
    ``track_files`` does not glob — so the raw recording stays exactly what the device
    produced and the cleaned copy can be thrown away and rebuilt.
    """
    files = dict(track_files(ctx.folder))
    if model is None or "me" not in files or "them" not in files:
        return files
    from app.audio.echo import clean_track

    try:
        files["me"] = clean_track(files["me"], files["them"], clean_path(ctx.folder, "me"), model)
    except Exception as exc:  # pragma: no cover - never lose a meeting over this
        log.warning("could not subtract the echo; transcribing the raw track: %s", exc)
        return dict(track_files(ctx.folder))
    log.info("transcribing the echo-cancelled near track")
    return files


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
        raise FileNotFoundError(f"no track audio under {folder / 'audio'}")
    if up_to_date(output, inputs):
        log.info("transcript segments are current; skipping")
        return

    records = recover(folder)
    echo_model = _measure_echo(ctx)
    backend = backend_for(ctx)

    # One pass per track over the whole file. Lost audio was written as silence, so the
    # file's timeline is the meeting's timeline and the timestamps need no shifting. It
    # also gives the model the entire track as context instead of one-minute windows.
    segments: list[Segment] = []
    prompt = build_initial_prompt(ctx, None)
    try:
        for _track, wav in sorted(_asr_inputs(ctx, echo_model).items()):
            ctx.checkpoint()
            track_segments = backend.transcribe(
                # Always Hebrew, whatever was spoken (D60): English comes out as English.
                wav,
                language=ASR_LANGUAGE,
                initial_prompt=prompt,
                word_timestamps=True,
            )
            segments.extend(track_segments)
    finally:
        _discard_clean(folder)
    segments.sort(key=lambda segment: (segment.start, segment.track))
    segments = [segment.shifted(0.0, new_id=index) for index, segment in enumerate(segments)]

    segments = _diarize(ctx, segments, records)

    # What the meeting was in, read from what was said: it picks the summary's language.
    decision = spoken_language(segment.text for segment in segments)
    ctx.dao.update_meeting(
        ctx.meeting.id, language=decision.language, language_conf=decision.confidence
    )
    log.info(
        "meeting language %s (%s, %.0f%% of the letters)",
        decision.language,
        decision.source,
        decision.confidence * 100,
    )

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


def _discard_clean(folder: Path) -> None:
    """The cleaned copy is derived: keeping it would silently double the near track."""
    directory = Path(folder) / "audio" / CLEAN_DIR
    if not directory.exists():
        return
    for path in directory.glob("*.wav"):
        path.unlink(missing_ok=True)
    with contextlib.suppress(OSError):
        directory.rmdir()


def _diarize(
    ctx: StageContext, segments: list[Segment], records: list[ChunkRecord]
) -> list[Segment]:
    """Split THEM into THEM_1/2/3 over the whole track, when diarization is enabled.

    Diarizing per chunk would be worthless: the speaker ids would not agree across chunk
    boundaries. So the loopback track is reassembled on the meeting's timeline (gaps
    become silence) and clustered once.
    """
    from app.asr.diarize import make_diarizer, speaker_count

    diarizer = make_diarizer(ctx.config)
    if diarizer is None:
        return segments
    rate = ctx.config.sample_rate
    wav = track_path(ctx.folder, "them")
    if not wav.exists():
        return segments
    track, track_rate = read_wav(wav)
    if track_rate != rate:  # pragma: no cover - written at the storage rate
        return segments
    if not len(track):
        return segments
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
