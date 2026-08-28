"""Which ASR backend is wired — chosen by config."""

from __future__ import annotations

from app.asr.backend import AsrBackend
from app.config import Config


def make_backend(config: Config) -> AsrBackend:
    kind = str(config.get("asr.backend", "local"))
    if kind == "fake":
        from app.asr.fake import FakeAsr

        return FakeAsr(
            language=str(config.get("asr.fake_language", config.default_language)),
            confidence=float(config.get("asr.fake_confidence", 0.95)),
            repetitions=int(config.get("asr.fake_repetitions", 1)),
        )
    if kind == "local":
        from app.asr.local import LocalAsr

        return LocalAsr(config)
    if kind == "remote":
        from app.asr.local import LocalAsr
        from app.asr.remote import RemoteAsr

        url = str(config.get("asr.remote_url") or config.get("worker_url") or "")
        if not url:
            return LocalAsr(config)
        return RemoteAsr(url, LocalAsr(config))
    raise ValueError(f"unknown asr.backend {kind!r}")
