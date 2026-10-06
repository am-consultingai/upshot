"""Connecting Claude Desktop: the extension Upshot builds, and how it is handed to Claude (D86)."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from app.transcription import claude

BRIDGE = "C:\\Users\\someone\\AppData\\Local\\Programs\\Upshot\\mcp\\upshot-mcp.exe"


def test_the_extension_holds_no_program_and_runs_the_installed_bridge(tmp_path: Path) -> None:
    """Claude Desktop does not stop an extension's server on uninstall, so a program inside
    the extension stays locked and the uninstall fails half-way (bug z8tj1he4zz)."""
    bundle = claude.build_bundle(tmp_path / "x.mcpb", bridge=BRIDGE, version="1.4.2+abc123")
    with zipfile.ZipFile(bundle) as archive:
        assert sorted(archive.namelist()) == ["icon.png", "manifest.json"]
        manifest = json.loads(archive.read("manifest.json"))
        icon = Image.open(io.BytesIO(archive.read("icon.png")))
    assert manifest["server"]["type"] == "binary"
    assert manifest["server"]["mcp_config"]["command"] == BRIDGE
    assert manifest["version"] == "1.4.2"
    assert manifest["compatibility"] == {"platforms": ["win32"]}
    assert [tool["name"] for tool in manifest["tools"]] == [
        "transcribe_file", "get_transcription", "list_transcriptions", "cancel_transcription",
    ]  # fmt: skip
    assert icon.size == (512, 512) and icon.mode == "RGBA"
    assert not (tmp_path / "x.partial").exists()


@pytest.mark.parametrize(
    ("given", "semver"),
    [("1.0.0", "1.0.0"), ("2.3", "2.3.0"), ("dev", "0.0.0"), ("1.2.3-rc1", "1.2.3")],
)
def test_versions_become_semver(given: str, semver: str) -> None:
    assert claude._semver(given) == semver


def test_which_claude_desktop_is_found_from_folders_only(tmp_path: Path) -> None:
    env = {"LOCALAPPDATA": str(tmp_path)}
    assert not claude.find_claude_desktop(env).installed
    (tmp_path / "AnthropicClaude").mkdir()
    assert claude.find_claude_desktop(env).kind == "classic"
    (tmp_path / "Packages" / "Claude_publisherid").mkdir(parents=True)
    store = claude.find_claude_desktop(env)
    assert (store.kind, store.package_family) == ("store", "Claude_publisherid")


def test_each_claude_gets_the_file_its_own_way(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "upshot-transcribe.mcpb"
    opened: list[str] = []
    monkeypatch.setattr(
        claude.os, "startfile", lambda path: opened.append(f"open {path}"), raising=False
    )
    monkeypatch.setattr(
        claude, "activate", lambda app, args: opened.append(f"launch {app} {args}") or 1
    )

    claude.hand_to_claude(bundle, claude.ClaudeDesktop("classic"))
    claude.hand_to_claude(bundle, claude.ClaudeDesktop("store", "Claude_publisherid"))
    assert opened == [f"open {bundle}", f'launch Claude_publisherid!Claude "{bundle}"']
    with pytest.raises(LookupError, match="not installed"):
        claude.hand_to_claude(bundle, claude.ClaudeDesktop(None))


def test_the_fallback_copy_goes_where_a_file_picker_shows_it(tmp_path: Path) -> None:
    bundle = claude.build_bundle(
        tmp_path / "home" / "upshot-transcribe.mcpb", bridge=BRIDGE, version="1.0.0"
    )
    copy = claude.copy_to_downloads(bundle, tmp_path / "Downloads")
    assert copy == tmp_path / "Downloads" / "upshot-transcribe.mcpb"
    assert copy.read_bytes() == bundle.read_bytes()
