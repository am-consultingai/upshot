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
        sessions = audio_utilities.GetAllSessions()  # type: ignore[attr-defined]
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
