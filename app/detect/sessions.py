"""Render-session enumeration via pycaw — the reliable source for "who is making sound"."""

from __future__ import annotations

from dataclasses import dataclass

from app.log import get

log = get(__name__)


@dataclass
class PycawSessions:
    name = "pycaw"

    def render_processes(self) -> list[str]:
        try:  # pragma: no cover - Windows only
            from pycaw.pycaw import AudioUtilities
        except Exception as exc:
            log.debug("pycaw unavailable: %s", exc)
            return []
        return self._enumerate(AudioUtilities)

    @staticmethod
    def _enumerate(audio_utilities: object) -> list[str]:  # pragma: no cover - Windows only
        names: list[str] = []
        try:
            sessions = audio_utilities.GetAllSessions()  # type: ignore[attr-defined]
        except Exception as exc:  # COMError "Element not found": no playback device at all
            # A PC with its speakers disabled, or a desktop whose only headset is unplugged,
            # has no render endpoint. Raising here threw away the whole detector tick, the
            # microphone and window-title evidence with it, so no meeting was ever noticed
            # (the GitHub Windows runner, 2026-10-05). No sessions is the honest answer.
            log.debug("no audio sessions to enumerate: %s", exc)
            return names
        for session in sessions:
            process = getattr(session, "Process", None)
            state = getattr(session, "State", None)
            if process is None:
                continue
            # 1 == AudioSessionStateActive
            if state is not None and int(state) != 1:
                continue
            names.append(str(process.name()))
        return names
