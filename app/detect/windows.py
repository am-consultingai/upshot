"""Window titles via EnumWindows — cheap, and often the meeting's actual name."""

from __future__ import annotations

from dataclasses import dataclass

from app.log import get

log = get(__name__)


@dataclass
class EnumWindowTitles:
    name = "enumwindows"
    visible_only: bool = True

    def titles(self) -> list[str]:
        try:  # pragma: no cover - Windows only
            import win32gui
        except Exception as exc:
            log.debug("pywin32 unavailable: %s", exc)
            return []
        return self._enumerate(win32gui)

    def _enumerate(self, win32gui: object) -> list[str]:  # pragma: no cover - Windows only
        found: list[str] = []

        def callback(handle: int, _extra: object) -> bool:
            if self.visible_only and not win32gui.IsWindowVisible(handle):  # type: ignore[attr-defined]
                return True
            title = win32gui.GetWindowText(handle)  # type: ignore[attr-defined]
            if title:
                found.append(str(title))
            return True

        win32gui.EnumWindows(callback, None)  # type: ignore[attr-defined]
        return found

    def foreground_title(self) -> str:
        try:  # pragma: no cover - Windows only
            import win32gui
        except Exception:
            return ""
        return str(
            win32gui.GetForegroundWindow()
            and win32gui.GetWindowText(win32gui.GetForegroundWindow())
        )
