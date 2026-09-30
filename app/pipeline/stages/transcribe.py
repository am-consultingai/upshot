"""The transcribe stage: the meeting's language, then one WAV per track → one segment list.

The classifier (``app/asr/classify.py``) decides the language first; then exactly one
large model transcribes both tracks (R3): ivrit-ai large-v3 for Hebrew, stock large-v3
for everything else (D80).
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

from app import glossary as glossary_module
from app import meta
from app.asr.backend import AsrBackend, Segment
from app.asr.classify import Classifier, LanguageDecision
from app.asr.diarize import LONG_TRACK_MINUTES, assign_speakers
from app.asr.models import HEBREW
from app.audio.echo import EchoModel
from app.audio.vad import read_wav
from app.audio.writer import ChunkRecord, recover, track_files, track_path
from app.log import get
from app.pipeline.artifacts import up_to_date
from app.pipeline.context import StageContext

log = get(__name__)

SEGMENTS_NAME = "segments.json"

#: Where ``meeting.language`` came from: the classifier (``app/asr/classify.py``).
LANGUAGE_SOURCE = "classifier"

#: meta.json: the language someone chose with "Transcribe again as…" (R9).
OVERRIDE_KEY = "asr_language_override"

#: Where the echo-cancelled copy of a track lives while it is being transcribed.
CLEAN_DIR = "clean"


def segments_path(folder: Path) -> Path:
    return Path(folder) / SEGMENTS_NAME


def clean_path(folder: Path, track: str) -> Path:
    return Path(folder) / "audio" / CLEAN_DIR / f"{track}.wav"


def _chunk_inputs(folder: Path) -> list[Path]:
    return sorted(track_files(folder).values())


def build_initial_prompt(ctx: StageContext, previous_sentence: str | None) -> str | None:
    """Whisper's prompt: glossary terms and attendee names. None while the glossary is off."""
    if not glossary_module.ENABLED:
        return None
    entries = glossary_module.active(ctx.dao, ctx.config.glossary_path)
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


def classifier_for(ctx: StageContext) -> Classifier:
    """The injected classifier; a scripted one beside the fake ASR; otherwise Whisper small."""
    services = ctx.services
    injected = getattr(services, "classifier", None) if services is not None else None
    if injected is not None:
        return injected  # type: ignore[no-any-return]
    asr = getattr(services, "asr", None) if services is not None else None
    fake = getattr(asr, "name", "") == "fake" if asr is not None else False
    if fake or str(ctx.config.get("asr.backend", "local")) == "fake":
        from app.asr.fake import FakeClassifier

        language = getattr(asr, "language", None) or ctx.config.get("asr.fake_language", "he")
        return FakeClassifier(str(language))
    from app.asr.classify import IsolatedClassifier

    return IsolatedClassifier(ctx.config)


def _check_installation(ctx: StageContext) -> None:
    """All three models present, before anything is loaded: a model missing after
    install fails the job with a message naming it. It is never downloaded, and ivrit
    never stands in for stock Whisper (R12)."""
    services = ctx.services
    if services is not None and getattr(services, "asr", None) is not None:
        return  # an injected backend (tests) brings its own models
    if str(ctx.config.get("asr.backend", "local")) == "fake":
        return
    from app.asr.models import check_installed

    check_installed(ctx.config)


def _classify(ctx: StageContext, inputs: dict[str, Path]) -> LanguageDecision:
    """The meeting's language, from the files transcription will read. Stored and logged,
    never shown (R7). A language chosen with "Transcribe again as…" wins, every time."""
    chosen = meta.read(ctx.folder).get(OVERRIDE_KEY)
    if chosen:
        from app.asr.classify import override

        decision = override(str(chosen))
    else:
        classifier = classifier_for(ctx)
        if hasattr(classifier, "stop_check"):
            classifier.stop_check = ctx.stop_if_deleted  # a deletion stops it at once
        decision = classifier.classify(inputs)
    log.info("%s", decision.log_line())
    ctx.metrics["language_detection_s"] = round(decision.seconds, 2)
    return decision


def backend_for(ctx: StageContext, role: str = HEBREW) -> AsrBackend:
    """The one backend for this meeting, for the model role the classifier chose."""
    services = ctx.services
    backend = getattr(services, "asr", None) if services is not None else None
    if backend is not None:
        select = getattr(backend, "select_role", None)
        if callable(select):
            select(role)
        return backend  # type: ignore[no-any-return]
    from app.asr.factory import make_backend

    return make_backend(ctx.config, role)


def run(ctx: StageContext) -> None:
    folder = ctx.folder
    output = segments_path(folder)
    inputs = _chunk_inputs(folder)
    if not inputs:
        raise FileNotFoundError(f"no track audio under {folder / 'audio'}")
    if not ctx.force and up_to_date(output, inputs):
        log.info("transcript segments are current; skipping")
        return

    _check_installation(ctx)
    records = recover(folder)
    echo_model = _measure_echo(ctx)

    # One pass per track over the whole file. Lost audio was written as silence, so the
    # file's timeline is the meeting's timeline and the timestamps need no shifting. It
    # also gives the model the entire track as context instead of one-minute windows.
    segments: list[Segment] = []
    prompt = build_initial_prompt(ctx, None)
    try:
        asr_inputs = _asr_inputs(ctx, echo_model)
        ctx.checkpoint()
        decision = _classify(ctx, asr_inputs)
        backend = backend_for(ctx, decision.route)
        if hasattr(backend, "stop_check"):
            # Between segments, a deletion stops the track; the recorder does not (a track
            # cannot be resumed part-way, so it waits for the next checkpoint).
            backend.stop_check = ctx.stop_if_deleted
        for _track, wav in sorted(asr_inputs.items()):
            ctx.checkpoint()
            track_segments = backend.transcribe(
                wav,
                # "he" on ivrit; the language on stock large-v3, or None with
                # multilingual when the classifier was unsure: forcing one language on
                # an unclear meeting translates the rest into it.
                language=decision.transcribe_language,
                initial_prompt=prompt,
                word_timestamps=True,
                multilingual=decision.multilingual,
            )
            segments.extend(track_segments)
    finally:
        _discard_clean(folder)
    segments.sort(key=lambda segment: (segment.start, segment.track))
    segments = [segment.shifted(0.0, new_id=index) for index, segment in enumerate(segments)]

    segments = _diarize(ctx, segments, records)

    # What the meeting was in, as the classifier heard it: it picks the summary's language
    # and the page's direction.
    confidence = round(decision.p_top, 3)
    ctx.dao.update_meeting(ctx.meeting.id, language=decision.language, language_conf=confidence)
    asr = {**_describe(backend), "language_detection": decision.as_dict()}

    payload: dict[str, Any] = {
        "version": 1,
        "language": decision.language,
        "language_conf": confidence,
        "language_source": _source(decision),
        "model": _describe(backend),
        "asr": {"language_detection": decision.as_dict()},
        "segments": [segment.as_dict() for segment in segments],
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    meta.mirror(
        ctx.refresh(),
        language=decision.language,
        language_conf=confidence,
        language_source=_source(decision),
        asr=asr,
        **({"diarization": ctx.metrics["diarization"]} if "diarization" in ctx.metrics else {}),
    )
    # ASR and the LLM are never resident together (DESIGN.md §20.4).
    backend.unload()
    ctx.metrics["segments"] = len(segments)
    ctx.metrics["language"] = decision.language


def _source(decision: LanguageDecision) -> str:
    return "override" if decision.rule == "override" else LANGUAGE_SOURCE


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
