"""App home and bundled-resource resolution.

Resolves identically from source and from a PyInstaller freeze, so nothing anywhere
else in the application does ``__file__``-relative asset lookups.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "upshot"


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def _bundle_root() -> Path:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(str(meipass))
    return Path(__file__).resolve().parent.parent


def resource(*parts: str) -> Path:
    """A read-only asset shipped with the application (templates, prompts, ffmpeg)."""
    return _bundle_root().joinpath(*parts)


def app_home() -> Path:
    """Writable per-user state directory. ``UP_HOME`` overrides it everywhere."""
    env = os.environ.get("UP_HOME")
    if env:
        return Path(env).expanduser().resolve()
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local).joinpath(APP_NAME)
    return Path.home() / ".local" / "share" / APP_NAME


def ensure_app_home() -> Path:
    home = app_home()
    home.mkdir(parents=True, exist_ok=True)
    return home


def db_path() -> Path:
    return app_home() / "index.db"


def config_path() -> Path:
    return app_home() / "app_config.json"


def default_data_root() -> Path:
    return app_home() / "meetings"


def log_dir() -> Path:
    return app_home() / "logs"
