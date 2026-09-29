"""Remote ASR: POST the WAV to another instance, with automatic local fallback.

The contract with the worker's ``POST /api/asr`` (multipart form; D80):

- ``audio``: the track's WAV;
- ``route``: ``hebrew`` or ``other``, which of the two large models to transcribe with
  (ivrit-ai large-v3 or stock Whisper large-v3). The worker holds both, installed by its
  own ``--prepare``, and never downloads one while transcribing (R12);
- ``language``: ``he`` on the Hebrew model; the meeting's language on the other, or empty
  to let Whisper decide per 30 s;
- ``multilingual``: ``true`` when ``language`` is empty for that reason;
- ``initial_prompt``, ``word_timestamps``: as before.

It answers ``{"segments": [...]}``. The language is decided here, on the client: the
worker needs no classifier. Whatever goes wrong, the local fallback transcribes with the
same model and the same arguments.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.asr.backend import AsrBackend, Segment, Word, track_of
from app.asr.models import HEBREW, MODELS
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
        role: str = HEBREW,
        timeout: float = 300.0,
        health_timeout: float = 2.0,
    ) -> None:
        self.url = url.rstrip("/")
        self.fallback = fallback
        #: Which large model the worker is asked for; the fallback holds the same one.
        self.role = role
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
        language: str | None = "he",
        initial_prompt: str | None = None,
        word_timestamps: bool = True,
        multilingual: bool = False,
    ) -> list[Segment]:
        if self.healthy():
            try:
                return self._post(wav, language, initial_prompt, word_timestamps, multilingual)
            except Exception as exc:
                self._warn(f"remote transcription failed: {exc}")
        self.used_fallback += 1
        return self.fallback.transcribe(
            wav,
            language=language,
            initial_prompt=initial_prompt,
            word_timestamps=word_timestamps,
            multilingual=multilingual,
        )

    def _post(
        self,
        wav: Path,
        language: str | None,
        initial_prompt: str | None,
        word_timestamps: bool,
        multilingual: bool = False,
    ) -> list[Segment]:
        import httpx

        with wav.open("rb") as handle:
            response = httpx.post(
                f"{self.url}/api/asr",
                files={"audio": (wav.name, handle, "audio/wav")},
                data={
                    "route": self.role,
                    "language": language or "",
                    "multilingual": str(multilingual).lower(),
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

    def describe(self) -> dict[str, Any]:
        model = MODELS[self.role]
        return {
            "name": "remote",
            "url": self.url,
            "role": self.role,
            "repo": model.repo,
            "revision": model.revision,
            "fallbacks": self.used_fallback,
        }

    def unload(self) -> None:
        self.fallback.unload()
