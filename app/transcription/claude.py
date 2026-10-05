"""Connecting Claude Desktop: the extension file, and handing it to Claude (D86, plan §4.6).

The extension (an MCP Bundle, ``.mcpb``) holds no program, only its manifest and icon. Its
command is the bridge this install put on disk, so nothing in Claude's extension folder is
ever running and Claude can always uninstall it: Claude Desktop does not stop an
extension's server on uninstall (bug z8tj1he4zz). It is built when the button is pressed,
so it names this install's real path wherever Upshot was installed.

Upshot never edits Claude's files. It hands Claude the extension file and Claude shows its
own install window:

- Claude Desktop from claude.ai registers ``.mcpb``, so opening the file is enough.
- The Microsoft Store build registers no file type, so the packaged app is launched with
  the file as its argument, which Claude handles the same way, running or not.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.log import get

log = get(__name__)

EXTENSION_NAME = "upshot-transcribe"
BUNDLE_NAME = f"{EXTENSION_NAME}.mcpb"
#: The Store package's family name starts with this, then ``_`` and the publisher hash.
STORE_PACKAGE_PREFIX = "Claude_"
#: The application id inside the Store package (its manifest's ``Application Id``).
STORE_APP_ID = "Claude"
TEAL = (15, 111, 104)

TOOLS = (
    ("transcribe_file", "Transcribe an audio or video file on this computer with Upshot."),
    ("get_transcription", "A transcription's progress, or its transcript once done."),
    ("list_transcriptions", "Recent file transcriptions in Upshot."),
    ("cancel_transcription", "Stop a waiting or running transcription."),
)


@dataclass(frozen=True)
class ClaudeDesktop:
    """Which Claude Desktop this computer has: ``classic`` (from claude.ai), ``store``
    (the Microsoft Store package, with its family name), or none."""

    kind: str | None
    package_family: str | None = None

    @property
    def installed(self) -> bool:
        return self.kind is not None


def find_claude_desktop(env: dict[str, str] | None = None) -> ClaudeDesktop:
    """Where Claude Desktop is installed, from folders and the file-type registration only:
    nothing of Claude's is opened or read."""
    env = dict(os.environ if env is None else env)
    local = env.get("LOCALAPPDATA")
    if sys.platform == "win32" and _mcpb_registered():
        return ClaudeDesktop("classic")
    if local:
        packages = Path(local) / "Packages"
        if packages.is_dir():
            for package in sorted(packages.glob(f"{STORE_PACKAGE_PREFIX}*")):
                return ClaudeDesktop("store", package.name)
        if (Path(local) / "AnthropicClaude").is_dir():
            return ClaudeDesktop("classic")
    return ClaudeDesktop(None)


def _mcpb_registered() -> bool:  # pragma: no cover - Windows only
    """Claude Desktop from claude.ai registers the ``.mcpb`` file type; the Store build
    does not. HKEY_CLASSES_ROOT merges the user's and the machine's registrations."""
    if sys.platform != "win32":
        return False
    import winreg

    try:
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, ".mcpb"))
    except OSError:
        return False
    return True


def manifest(bridge: str, version: str) -> dict[str, Any]:
    """The extension's ``manifest.json`` (MCPB manifest 0.2): a binary server for Windows
    whose command is the installed bridge."""
    return {
        "manifest_version": "0.2",
        "name": EXTENSION_NAME,
        "display_name": "Upshot transcription",
        "version": _semver(version),
        "description": "Transcribe audio and video files on this computer with Upshot.",
        "long_description": (
            "Lets Claude transcribe audio and video files with Upshot, which runs on this "
            "computer: files are read where they are and never leave it. Upshot must be "
            "running."
        ),
        "author": {"name": "AM Consulting", "url": "https://github.com/am-consultingai/upshot"},
        "icon": "icon.png",
        "server": {
            "type": "binary",
            # The program is not in the extension (bug z8tj1he4zz); the command is.
            "entry_point": "upshot-mcp.exe",
            "mcp_config": {"command": bridge, "args": [], "env": {}},
        },
        "tools": [{"name": name, "description": text} for name, text in TOOLS],
        "compatibility": {"platforms": ["win32"]},
        "license": "Proprietary",
    }


def _semver(version: str) -> str:
    """MCPB wants ``x.y.z``; a build calls itself ``1.0.0`` or ``1.0.0+abc`` or worse."""
    parts = [p for p in version.split("+")[0].split("-")[0].split(".") if p.isdigit()][:3]
    return ".".join([*parts, "0", "0", "0"][:3])


def build_bundle(target: Path, *, bridge: str, version: str) -> Path:
    """Write the extension file: the manifest and a 512 px icon, zipped."""
    from app import brand

    icon = io.BytesIO()
    brand.render(512, TEAL).save(icon, format="PNG")
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".partial")
    with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("manifest.json", json.dumps(manifest(bridge, version), indent=2))
        bundle.writestr("icon.png", icon.getvalue())
    partial.replace(target)
    return target


def hand_to_claude(bundle: Path, claude: ClaudeDesktop) -> None:
    """Give Claude the extension file; Claude shows its install window. Raises
    ``LookupError`` when Claude Desktop is not installed, ``OSError`` when the hand-off
    fails (the page then offers the file to install by hand)."""
    if claude.kind == "classic":
        startfile = getattr(os, "startfile", None)
        if startfile is None:  # pragma: no cover - Windows only
            raise OSError("opening the extension needs Windows")
        startfile(str(bundle))
        return
    if claude.kind == "store" and claude.package_family:
        activate(f"{claude.package_family}!{STORE_APP_ID}", f'"{bundle}"')
        return
    raise LookupError("Claude Desktop is not installed")


def activate(app_user_model_id: str, arguments: str) -> int:  # pragma: no cover - Windows only
    """Launch a packaged (Store) app with a command line, through
    ``IApplicationActivationManager.ActivateApplication``: what Windows itself does when
    such an app is opened. Returns the process id."""
    if sys.platform != "win32":
        raise OSError("launching a Store app needs Windows")
    import ctypes
    import uuid
    from ctypes import wintypes

    ole32 = ctypes.OleDLL("ole32")
    ole32.CoInitializeEx(None, 0x2)  # apartment-threaded; S_FALSE when already initialised

    def guid(text: str) -> ctypes.Array[ctypes.c_byte]:
        return (ctypes.c_byte * 16).from_buffer_copy(uuid.UUID(text).bytes_le)

    clsid = guid("45BA127D-10A8-46EA-8AB7-56EA9078943C")
    iid = guid("2e941141-7f97-4756-ba1d-9decde894a3d")
    manager = ctypes.c_void_p()
    ole32.CoCreateInstance(ctypes.byref(clsid), None, 0x4, ctypes.byref(iid), ctypes.byref(manager))
    try:
        vtable = ctypes.cast(manager, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        # IUnknown's three methods come first; ActivateApplication is the fourth.
        prototype = ctypes.WINFUNCTYPE(
            ctypes.HRESULT, ctypes.c_void_p, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int,
            ctypes.POINTER(wintypes.DWORD),
        )  # fmt: skip
        activate_application = prototype(vtable[3])
        pid = wintypes.DWORD()
        activate_application(manager, app_user_model_id, arguments, 0, ctypes.byref(pid))
        return int(pid.value)
    finally:
        release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])
        release(manager)


def copy_to_downloads(bundle: Path, downloads: Path | None = None) -> Path:
    """For installing by hand: a copy where a file picker shows it (AppData is hidden)."""
    downloads = downloads or _downloads_folder()
    downloads.mkdir(parents=True, exist_ok=True)
    target = downloads / BUNDLE_NAME
    shutil.copyfile(bundle, target)
    return target


def _downloads_folder() -> Path:
    if sys.platform == "win32":  # pragma: no cover - Windows only
        import ctypes
        import uuid
        from ctypes import wintypes

        folder = (ctypes.c_byte * 16).from_buffer_copy(
            uuid.UUID("374DE290-123F-4565-9164-39C4925E467B").bytes_le
        )
        out = wintypes.LPWSTR()
        if not ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(folder), 0, None, ctypes.byref(out)
        ):
            try:
                return Path(out.value or "")
            finally:
                ctypes.windll.ole32.CoTaskMemFree(out)
    return Path.home() / "Downloads"


def show_in_explorer(path: Path) -> None:  # pragma: no cover - Windows only
    """Explorer, with the file selected."""
    import subprocess

    subprocess.Popen(["explorer.exe", f"/select,{path}"])
