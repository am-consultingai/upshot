# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller one-dir build.

Hidden imports are declared explicitly: PyInstaller's static analysis cannot see
CTranslate2's extension loading, onnxruntime's capi DLLs, the comtypes-generated
interfaces pycaw builds at runtime, keyring's Windows backend, or pystray's win32 backend.
Missing one of these produces an executable that starts and then fails at the first
meeting — which is why `--selftest imports` runs against the freeze in the build.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

ROOT = Path(SPECPATH).parent

hiddenimports = [
    "ctranslate2",
    "onnxruntime",
    "onnxruntime.capi",
    "onnxruntime.capi._pybind_state",
    "faster_whisper",
    "keyring.backends.Windows",
    "pystray._win32",
    "PIL._tkinter_finder",
    "pyaudiowpatch",
    "pycaw",
    "comtypes",
    "comtypes.stream",
    "win32timezone",
    "windows_toasts",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "anyio._backends._asyncio",
]

datas = [
    (str(ROOT / "app" / "llm" / "prompts"), "app/llm/prompts"),
    (str(ROOT / "app" / "db" / "schema.sql"), "app/db"),
    (str(ROOT / "app" / "db" / "migrations"), "app/db/migrations"),
]
if (ROOT / "frontend" / "dist").exists():
    datas.append((str(ROOT / "frontend" / "dist"), "frontend/dist"))
if (ROOT / "vendor" / "ffmpeg.exe").exists():
    datas.append((str(ROOT / "vendor" / "ffmpeg.exe"), "."))
datas += collect_data_files("faster_whisper")  # the bundled Silero VAD model

binaries = collect_dynamic_libs("onnxruntime") + collect_dynamic_libs("ctranslate2")

a = Analysis(
    [str(ROOT / "app" / "tray.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "torch", "tests"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="upshot",
    console=False,
    icon=str(ROOT / "packaging" / "icon.ico") if (ROOT / "packaging" / "icon.ico").exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="upshot",
)
