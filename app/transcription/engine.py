"""File in, transcript out: the engine behind the transcription service (D86).

One audio or video file goes through the meeting's own helpers — the classifier, the
backend for its route, the installation check, whole-track diarization — so a file is
transcribed exactly as a meeting is (R7). It never creates a meeting: nothing here
touches the meeting tables, search or the summary (R4).

Two callbacks come from the caller (the worker):

- ``stop_check`` may raise at any moment, between ffmpeg polls and between segments: a
  cancel or a deletion.
- ``checkpoint`` is called only **between phases**, and is where a starting recording
  stops the job. Mid-phase it never does: a track cannot be resumed part-way, and stopping
  a long file mid-ASR would throw away hours of work (plan §4.2).
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.asr.backend import Segment
from app.asr.classify import LanguageDecision
from app.audio.ingest import to_wav, wav_duration_s
from app.config import Config
from app.log import get
from app.pipeline.stages.transcribe import (
    backend_for,
    check_installation,
    classifier_for,
    describe_backend,
)
from app.transcription.types import FileTranscript, Options

log = get(__name__)

WAV_NAME = "audio.wav"

#: ffmpeg is told to read local files only, so a playlist or concat file inside an upload
#: cannot make it fetch a URL (R1) or open something elsewhere.
INPUT_ARGS: tuple[str, ...] = ("-protocol_whitelist", "file")
#: The audio only: a video track is never decoded.
OUTPUT_ARGS: tuple[str, ...] = ("-vn",)

ProgressFn = Callable[[str, float], None]


def _nothing() -> None:
    return None


def transcribe_file(
    source: Path,
    workdir: Path,
    *,
    config: Config,
    options: Options,
    services: Any,
    stop_check: Callable[[], None],
    on_progress: ProgressFn,
    checkpoint: Callable[[], None] = _nothing,
) -> FileTranscript:
    """Transcribe ``source``. ``workdir`` holds the decoded WAV while it runs; the WAV is
    removed however the job ends, and ``source`` is never touched."""
    wav = Path(workdir) / WAV_NAME
    try:
        on_progress("decode", 0.0)
        to_wav(
            Path(source),
            wav,
            config=config,
            rate=config.sample_rate,
            input_args=INPUT_ARGS,
            extra_args=OUTPUT_ARGS,
            stop_check=stop_check,
        )
        duration = wav_duration_s(wav)
        on_progress("decode", 1.0)
        checkpoint()

        on_progress("check", 0.0)
        check_installation(config, services)
        on_progress("check", 1.0)
        checkpoint()

        on_progress("language", 0.0)
        decision = _language(wav, options, config, services, stop_check)
        on_progress("language", 1.0)
        checkpoint()

        on_progress("transcribe", 0.0)
        segments, model = _transcribe(
            wav, decision, options, config, services, stop_check, duration, on_progress
        )
        on_progress("transcribe", 1.0)
        checkpoint()

        diarized = False
        if options.diarize and segments:
            on_progress("diarize", 0.0)
            stop_check()
            segments, diarized = _diarize(wav, segments, config)
            stop_check()
            on_progress("diarize", 1.0)
    finally:
        with contextlib.suppress(OSError):
            wav.unlink(missing_ok=True)

    segments = relabel(segments)
    spoke = bool(segments)
    return FileTranscript(
        duration_s=duration,
        segments=tuple(segments),
        language=decision.language if spoke else None,
        language_conf=round(decision.p_top, 3) if spoke else None,
        language_source=("override" if decision.rule == "override" else "classifier")
        if spoke
        else "none",
        model=model,
        diarized=diarized,
    )


def _language(
    wav: Path,
    options: Options,
    config: Config,
    services: Any,
    stop_check: Callable[[], None],
) -> LanguageDecision:
    """The classifier on the file, as on a meeting's far track; or the caller's language."""
    if options.language != "auto":
        from app.asr.classify import override

        return override(options.language)
    classifier = classifier_for(config, services)
    hooked = hasattr(classifier, "stop_check")
    if hooked:
        setattr(classifier, "stop_check", stop_check)  # noqa: B010 - not on the protocol
    try:
        decision = classifier.classify({"them": wav})
    finally:
        if hooked:
            setattr(classifier, "stop_check", None)  # noqa: B010
    log.info("%s", decision.log_line())
    return decision


def _transcribe(
    wav: Path,
    decision: LanguageDecision,
    options: Options,
    config: Config,
    services: Any,
    stop_check: Callable[[], None],
    duration: float,
    on_progress: ProgressFn,
) -> tuple[list[Segment], dict[str, Any]]:
    backend = backend_for(config, services, decision.route)

    def on_segment(end_s: float) -> None:
        if duration > 0:
            on_progress("transcribe", min(1.0, end_s / duration))

    hooks = {"stop_check": stop_check, "on_segment": on_segment}
    hooked = [name for name in hooks if hasattr(backend, name)]
    for name in hooked:
        setattr(backend, name, hooks[name])
    try:
        segments = backend.transcribe(
            wav,
            language=decision.transcribe_language,
            initial_prompt=options.prompt or None,
            word_timestamps=True,
            multilingual=decision.multilingual,
        )
        model = describe_backend(backend)
    finally:
        # An injected backend is shared between jobs: a stale hook would report this
        # file's progress during the next meeting.
        for name in hooked:
            setattr(backend, name, None)
        # Before diarization, which needs the memory (the meeting stage frees it later).
        backend.unload()
    ordered = sorted(segments, key=lambda segment: segment.start)
    return [segment.shifted(0.0, new_id=index) for index, segment in enumerate(ordered)], model


def _diarize(wav: Path, segments: list[Segment], config: Config) -> tuple[list[Segment], bool]:
    """The whole file, clustered once, as a meeting's far track is (D85). Any length: the
    bound is ``transcription.max_hours`` at upload, not a skip here (R7)."""
    from app.asr.diarize import THEM, diarize_track, make_diarizer
    from app.audio.vad import read_wav

    diarizer = make_diarizer(config)
    if diarizer is None:
        return segments, False
    try:
        audio, rate = read_wav(wav)
        if not len(audio):
            return segments, False
        labelled, found = diarize_track(
            diarizer, audio, rate, segments, track="them", base=THEM, config=config
        )
    finally:
        diarizer.unload()
    return labelled, found is not None


def relabel(segments: list[Segment]) -> list[Segment]:
    """``THEM``/``THEM_n`` → ``S1``, ``S2``… in order of first appearance."""
    mapping: dict[str, str] = {}
    out: list[Segment] = []
    for segment in segments:
        label = mapping.setdefault(segment.speaker, f"S{len(mapping) + 1}")
        out.append(
            Segment(
                id=segment.id,
                track=segment.track,
                speaker=label,
                start=segment.start,
                end=segment.end,
                text=segment.text,
                words=segment.words,
                avg_logprob=segment.avg_logprob,
                no_speech_prob=segment.no_speech_prob,
            )
        )
    return out
