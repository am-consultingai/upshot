"""Paths as Claude gives them, as the Windows app reads them.

Launched from WSL (``--wsl-distro``), the bridge is a Windows process handed Linux paths:
``/mnt/<d>/…`` is the drive itself, and every other absolute path lives inside the
distribution, which Windows reaches as ``\\\\wsl.localhost\\<distro>\\…``. The app reads
both in place (D86).
"""

from __future__ import annotations

import re

_MNT = re.compile(r"^/mnt/([a-zA-Z])(?:/(.*))?$")


class PathError(ValueError):
    pass


def to_windows(path: str, wsl_distro: str | None) -> str:
    """The path the app should read; unchanged when the bridge was not launched from WSL."""
    text = (path or "").strip()
    if not text:
        raise PathError("give the full path of an audio or video file")
    if not wsl_distro or not text.startswith("/"):
        return text
    mounted = _MNT.match(text)
    if mounted:
        drive, rest = mounted.groups()
        return f"{drive.upper()}:\\" + (rest or "").replace("/", "\\")
    return f"\\\\wsl.localhost\\{wsl_distro}" + text.replace("/", "\\")


def is_absolute(path: str) -> bool:
    return path.startswith(("/", "\\\\")) or re.match(r"^[A-Za-z]:[\\/]", path) is not None


def stem(name: str) -> str:
    base = re.split(r"[\\/]", name)[-1]
    return base.rsplit(".", 1)[0] if "." in base.strip(".") else base or "transcript"
