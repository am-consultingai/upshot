"""A picture of Upshot's own window for feedback, never the screen (D87, D2).

``capture`` finds Upshot's window (``app.window.find_window``) and asks Windows to draw
it into a bitmap (``PrintWindow`` with ``PW_RENDERFULLCONTENT``, which works for a browser
window even when it is behind another). Nothing outside that window is in the picture.
The user sees it before sending and can remove it; the window can show meeting content,
and the feedback form says so. Off Windows there is no window to capture.
"""

from __future__ import annotations

import io
import sys

from app.log import get

log = get(__name__)

PW_RENDERFULLCONTENT = 0x00000002
MAX_SIDE = 1600


def capture() -> bytes | None:
    """PNG bytes of Upshot's window, or None (no window, not Windows, or it failed)."""
    if sys.platform != "win32":
        return None
    try:
        return _capture_windows()
    except Exception as exc:  # a missing screenshot must never stop the feedback
        log.info("could not capture Upshot's window: %s", exc)
        return None


def _capture_windows() -> bytes | None:  # pragma: no cover - Windows only
    import ctypes

    import win32gui
    import win32ui
    from PIL import Image

    from app import window

    hwnd = window.find_window()
    if not hwnd:
        return None
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        return None
    window_dc = win32gui.GetWindowDC(hwnd)
    source = win32ui.CreateDCFromHandle(window_dc)
    memory = source.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    try:
        bitmap.CreateCompatibleBitmap(source, width, height)
        memory.SelectObject(bitmap)
        user32 = getattr(ctypes, "windll").user32  # noqa: B009 - Windows only, untyped elsewhere
        if not user32.PrintWindow(hwnd, memory.GetSafeHdc(), PW_RENDERFULLCONTENT):
            return None
        info = bitmap.GetInfo()
        image = Image.frombuffer(
            "RGB",
            (info["bmWidth"], info["bmHeight"]),
            bitmap.GetBitmapBits(True),
            "raw",
            "BGRX",
            0,
            1,
        )
    finally:
        win32gui.DeleteObject(bitmap.GetHandle())
        memory.DeleteDC()
        source.DeleteDC()
        win32gui.ReleaseDC(hwnd, window_dc)
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()
