"""Paths as Claude gives them, as the Windows app reads them.

Launched from WSL (``--wsl-distro``), the bridge is a Windows process handed Linux paths:
``/mnt/<d>/…`` is the drive itself, and every other absolute path lives inside the
distribution, which Windows reaches as ``\\\\wsl.localhost\\<distro>\\…``. The app reads
both in place (D86).
"""

from __future__ import annotations

import os
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


def check_local(path: str) -> None:
    """The app's own rule (``check_path`` in ``app/transcription/api.py``), applied before
    this process opens or writes anything: a drive path or a WSL share. Any other UNC path
    would make the bridge itself sign in to that host over SMB, whatever the app refuses
    (the PR #1 review)."""
    text = path.strip()
    if text.startswith(("\\\\?\\", "\\\\.\\", "//?/", "//./")):
        raise PathError("device paths are not accepted")
    if text.startswith(("\\\\", "//")):
        host = re.split(r"[\\/]", text.lstrip("\\/"), maxsplit=1)[0].lower()
        if host not in ("wsl.localhost", "wsl$"):
            raise PathError("network paths are not accepted, only files on this computer")
        return
    if text.startswith("/") and os.name != "nt":
        return  # the bridge on Linux or macOS: a local path
    if not re.match(r"^[A-Za-z]:[\\/]", text):
        raise PathError(f"give the full path of a file on this computer, not {path!r}")


def is_absolute(path: str) -> bool:
    return path.startswith(("/", "\\\\")) or re.match(r"^[A-Za-z]:[\\/]", path) is not None


def stem(name: str) -> str:
    base = re.split(r"[\\/]", name)[-1]
    return base.rsplit(".", 1)[0] if "." in base.strip(".") else base or "transcript"
