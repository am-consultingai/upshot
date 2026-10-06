# -*- mode: python ; coding: utf-8 -*-
"""upshot-mcp.exe: the transcription bridge for Claude and other MCP clients (D86).

A console program, built one-dir into ``dist\\upshot\\mcp\\``, beside the app and not inside
its bundle, so the installer's copy of ``dist\\upshot`` carries it. One-dir, not onefile: a
client kills its stdio servers, and a killed onefile leaves its unpacked copy in %TEMP%
every time (D86). The standard library only, so the freeze is small and starts fast.

    pyinstaller --noconfirm --distpath dist\\upshot --workpath build\\upshot-mcp packaging\\upshot-mcp.spec
"""

import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
sys.path.insert(0, SPECPATH)
from version_info import version_resource  # noqa: E402

CLIENT = ROOT / "clients" / "python"

a = Analysis(
    [str(CLIENT / "upshot_mcp" / "__main__.py")],
    pathex=[str(CLIENT)],
    binaries=[],
    datas=[],
    hiddenimports=["upshot_mcp.cli", "upshot_mcp.tools", "upshot_mcp.protocol", "upshot_mcp.upshot"],
    hookspath=[],
    runtime_hooks=[],
    # Nothing of the app's, and none of the large standard-library corners it never uses.
    excludes=["app", "tkinter", "unittest", "pydoc", "doctest", "lib2to3", "sqlite3", "xmlrpc"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="upshot-mcp",
    console=True,
    version=version_resource(ROOT, name="upshot-mcp", description="Upshot transcription for Claude"),
    icon=str(ROOT / "packaging" / "icon.ico") if (ROOT / "packaging" / "icon.ico").exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="mcp",
)
