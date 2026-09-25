"""Which ASR backend is wired — chosen by config."""

from __future__ import annotations

from pathlib import Path

from app.asr.backend import AsrBackend
from app.config import Config
from app.log import get

log = get(__name__)


def fetch_model(repo: str) -> Path:
    """A model not on disk is downloaded by the model manager, which the setup screen
    shows, rather than inside faster-whisper where nobody can see it."""
    from app.asr.model_manager import manager_for

    return manager_for(repo).ensure()


def make_backend(config: Config) -> AsrBackend:
    kind = str(config.get("asr.backend", "local"))
    if kind == "fake":
        from app.asr.fake import FakeAsr

        # Its output is plausible Hebrew, so a fake transcript is indistinguishable from
        # a real one once it reaches the UI. Say so loudly rather than let someone spend
        # an afternoon wondering why every recording says the same thing.
        log.warning(
            "asr.backend=fake — transcripts are canned text, NOT your audio. "
            "Set UP_ASR__BACKEND to 'local' for real transcription."
        )
        return FakeAsr(
            language=str(config.get("asr.fake_language", "he")),
            repetitions=int(config.get("asr.fake_repetitions", 1)),
        )
    if kind == "local":
        from app.asr.local import LocalAsr

        log.info("asr.backend=local, model=%s", config.get("asr.model_path") or "(default)")
        return LocalAsr(config, fetch=fetch_model)
    if kind == "remote":
        from app.asr.local import LocalAsr
        from app.asr.remote import RemoteAsr

        url = str(config.get("asr.remote_url") or config.get("worker_url") or "")
        if not url:
            return LocalAsr(config, fetch=fetch_model)
        return RemoteAsr(url, LocalAsr(config, fetch=fetch_model))
    raise ValueError(f"unknown asr.backend {kind!r}")
