"""Remote ASR: POST the WAV to another instance, with automatic local fallback."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.asr.backend import AsrBackend, Segment, Word, track_of
from app.log import get

log = get(__name__)


class RemoteAsr:
    """The ``remote-worker`` profile. A dead worker degrades to local, loudly."""

    name = "remote"

    def __init__(
        self,
        url: str,
        fallback: AsrBackend,
        *,
        timeout: float = 300.0,
        health_timeout: float = 2.0,
    ) -> None:
        self.url = url.rstrip("/")
        self.fallback = fallback
        self.timeout = timeout
        self.health_timeout = health_timeout
        self.used_fallback = 0
        self.warnings: list[str] = []

    # -- health ------------------------------------------------------------

    def healthy(self) -> bool:
        import httpx

        try:
            response = httpx.get(f"{self.url}/api/status", timeout=self.health_timeout)
            return response.status_code == 200
        except Exception as exc:
            self._warn(f"worker health check failed: {exc}")
            return False

    def _warn(self, message: str) -> None:
        log.warning("%s", message)
        self.warnings.append(message)

    # -- protocol ----------------------------------------------------------

    def transcribe(
        self,
        wav: Path,
        *,
        language: str = "he",
        initial_prompt: str | None = None,
        word_timestamps: bool = True,
    ) -> list[Segment]:
        if self.healthy():
            try:
                return self._post(wav, language, initial_prompt, word_timestamps)
            except Exception as exc:
                self._warn(f"remote transcription failed: {exc}")
        self.used_fallback += 1
        return self.fallback.transcribe(
            wav,
            language=language,
            initial_prompt=initial_prompt,
            word_timestamps=word_timestamps,
        )

    def _post(
        self, wav: Path, language: str, initial_prompt: str | None, word_timestamps: bool
    ) -> list[Segment]:
        import httpx

        with wav.open("rb") as handle:
            response = httpx.post(
                f"{self.url}/api/asr",
                files={"audio": (wav.name, handle, "audio/wav")},
                data={
                    "language": language,
                    "initial_prompt": initial_prompt or "",
                    "word_timestamps": str(word_timestamps).lower(),
                },
                timeout=self.timeout,
            )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        track = track_of(wav)
        return [
            Segment(
                id=int(item.get("id", index)),
                track=track,
                speaker="ME" if track == "me" else "THEM",
                start=float(item["start"]),
                end=float(item["end"]),
                text=str(item["text"]).strip(),
                words=tuple(
                    Word(str(w["w"]), float(w["s"]), float(w["e"]), float(w.get("p", 1.0)))
                    for w in item.get("words", [])
                ),
                avg_logprob=float(item.get("avg_logprob", 0.0)),
                no_speech_prob=float(item.get("no_speech_prob", 0.0)),
            )
            for index, item in enumerate(payload.get("segments", []))
        ]

    def detect_language(self, wav: Path) -> tuple[str, float]:
        return self.fallback.detect_language(wav)

    def unload(self) -> None:
        self.fallback.unload()
