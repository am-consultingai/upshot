"""Which ASR backend is wired — chosen by config."""

from __future__ import annotations

from app.asr.backend import AsrBackend
from app.asr.models import HEBREW
from app.config import Config
from app.log import get

log = get(__name__)


def make_backend(config: Config, role: str = HEBREW) -> AsrBackend:
    """The backend for one model role; exactly one is built per meeting (R3)."""
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
        fake = FakeAsr(
            language=str(config.get("asr.fake_language", "he")),
            repetitions=int(config.get("asr.fake_repetitions", 1)),
        )
        fake.select_role(role)
        return fake
    if kind == "local":
        from app.asr.local import LocalAsr

        log.info("asr.backend=local, role=%s", role)
        return LocalAsr(config, role=role)
    if kind == "remote":
        from app.asr.local import LocalAsr
        from app.asr.remote import RemoteAsr

        url = str(config.get("asr.remote_url") or config.get("worker_url") or "")
        if not url:
            return LocalAsr(config, role=role)
        return RemoteAsr(url, LocalAsr(config, role=role), role=role)
    raise ValueError(f"unknown asr.backend {kind!r}")
