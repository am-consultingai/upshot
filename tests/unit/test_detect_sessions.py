"""Audio sessions on a PC with no playback device: none, not an error (2026-10-05)."""

from __future__ import annotations

from app.detect.sessions import PycawSessions


class _NoPlaybackDevice:
    @staticmethod
    def GetAllSessions() -> list[object]:
        # pycaw raises COMError "Element not found" when there is no render endpoint.
        raise OSError(-2147023728, "Element not found.")


def test_no_playback_device_means_no_sessions_not_a_failed_tick() -> None:
    assert PycawSessions._enumerate(_NoPlaybackDevice) == []
