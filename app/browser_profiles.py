"""The browser's profiles, so Google's sign-in opens in the one the user chooses.

Upshot's window is the default browser in app mode (``app/window.py``). A page opened from
it with ``window.open`` lands in the profile that window runs in, whichever window has the
focus, so connecting Google Calendar kept opening in a profile the user did not want
(ClickUp z8tj1hca86). Chrome and Edge list their profiles, with each one's display name and
signed-in Google account, in ``Local State``; starting the browser with
``--profile-directory=<dir>`` opens a URL in that profile.

Chromium browsers other than Chrome and Edge, and Firefox, are not listed: the page then
opens the link itself, and offers it to copy.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path, PureWindowsPath

from app.log import get

log = get(__name__)

#: Executable name -> (display name, its user-data folder under %LOCALAPPDATA%).
KNOWN: dict[str, tuple[str, tuple[str, ...]]] = {
    "chrome.exe": ("Chrome", ("Google", "Chrome", "User Data")),
    "msedge.exe": ("Edge", ("Microsoft", "Edge", "User Data")),
}


@dataclass(frozen=True)
class Profile:
    #: The profile's folder name: what ``--profile-directory`` takes ("Default", "Profile 2").
    id: str
    #: What the browser calls it ("Work", "Person 1").
    name: str
    #: The Google account signed in to it, if any.
    email: str = ""

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def browser_name(exe: str | None) -> str | None:
    if not exe:
        return None
    known = KNOWN.get(PureWindowsPath(exe).name.lower())
    return known[0] if known else None


def user_data_dir(exe: str | None, local_app_data: str | None = None) -> Path | None:
    if not exe:
        return None
    known = KNOWN.get(PureWindowsPath(exe).name.lower())
    base = local_app_data or os.environ.get("LOCALAPPDATA")
    if not known or not base:
        return None
    return Path(base).joinpath(*known[1])


def read_profiles(local_state: Path) -> list[Profile]:
    """Every profile ``Local State`` lists, in the browser's own order. [] if unreadable."""
    try:
        data = json.loads(local_state.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.info("could not read the browser's profiles from %s: %s", local_state, exc)
        return []
    profile = data.get("profile") if isinstance(data, dict) else None
    cache = profile.get("info_cache") if isinstance(profile, dict) else None
    if not isinstance(cache, dict):
        return []
    order = profile.get("profiles_order") if isinstance(profile, dict) else None
    ids = [str(i) for i in order if i in cache] if isinstance(order, list) else []
    ids += [str(i) for i in cache if i not in ids]
    out: list[Profile] = []
    for profile_id in ids:
        info = cache.get(profile_id)
        if not isinstance(info, dict):
            continue
        out.append(
            Profile(
                profile_id,
                str(info.get("name") or info.get("shortcut_name") or profile_id),
                str(info.get("user_name") or ""),
            )
        )
    return out


def profiles_of(exe: str | None, local_app_data: str | None = None) -> list[Profile]:
    folder = user_data_dir(exe, local_app_data)
    return read_profiles(folder / "Local State") if folder else []


def open_in_profile(exe: str, profile_id: str, url: str) -> None:
    """Open ``url`` in a normal window of that profile (a new tab if one is open)."""
    subprocess.Popen(
        [exe, f"--profile-directory={profile_id}", url],
        close_fds=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
