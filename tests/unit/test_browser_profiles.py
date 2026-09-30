"""The browser's profiles, for opening Google's sign-in in the right one (z8tj1hca86)."""

from __future__ import annotations

import json
from pathlib import Path

from app import browser_profiles
from app.browser_profiles import Profile, browser_name, profiles_of, read_profiles

LOCAL_STATE = {
    "profile": {
        "info_cache": {
            "Default": {"name": "Person 1", "user_name": "dana.home@gmail.com"},
            "Profile 2": {"name": "Work", "user_name": "dana@company.com"},
            "Profile 5": {"name": "Guest-ish", "user_name": ""},
        },
        "profiles_order": ["Profile 2", "Default"],
    }
}


def write_state(root: Path, *parts: str) -> Path:
    folder = root.joinpath(*parts)
    folder.mkdir(parents=True)
    (folder / "Local State").write_text(json.dumps(LOCAL_STATE), encoding="utf-8")
    return folder


def test_profiles_are_read_in_the_browsers_order_with_their_accounts(tmp_path: Path) -> None:
    folder = write_state(tmp_path, "Google", "Chrome", "User Data")
    assert read_profiles(folder / "Local State") == [
        Profile("Profile 2", "Work", "dana@company.com"),
        Profile("Default", "Person 1", "dana.home@gmail.com"),
        Profile("Profile 5", "Guest-ish", ""),
    ]


def test_chrome_and_edge_each_have_their_own_folder(tmp_path: Path) -> None:
    write_state(tmp_path, "Google", "Chrome", "User Data")
    chrome = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    assert len(profiles_of(chrome, str(tmp_path))) == 3
    assert profiles_of(edge, str(tmp_path)) == [], "Edge has no Local State here"
    assert (browser_name(chrome), browser_name(edge)) == ("Chrome", "Edge")


def test_other_browsers_and_broken_files_have_no_profiles(tmp_path: Path) -> None:
    assert profiles_of(r"C:\Program Files\Mozilla Firefox\firefox.exe", str(tmp_path)) == []
    assert profiles_of(None, str(tmp_path)) == []
    folder = tmp_path / "Google" / "Chrome" / "User Data"
    folder.mkdir(parents=True)
    (folder / "Local State").write_text("{not json", encoding="utf-8")
    assert profiles_of("chrome.exe", str(tmp_path)) == []


def test_the_url_opens_in_the_chosen_profile(monkeypatch: object) -> None:
    started: list[list[str]] = []
    import pytest

    patch = pytest.MonkeyPatch()
    patch.setattr(browser_profiles.subprocess, "Popen", lambda cmd, **kw: started.append(cmd))
    try:
        browser_profiles.open_in_profile("chrome.exe", "Profile 2", "https://accounts.google.com/x")
    finally:
        patch.undo()
    assert started == [
        ["chrome.exe", "--profile-directory=Profile 2", "https://accounts.google.com/x"]
    ]
