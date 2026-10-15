# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller one-dir build.

Hidden imports are declared explicitly: PyInstaller's static analysis cannot see
CTranslate2's extension loading, onnxruntime's capi DLLs, sherpa-onnx's native library, the comtypes-generated
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
    "sherpa_onnx",
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
    # The assistant's tool server (app/assistant/mcp.py) imports these inside a function.
    "mcp.server.mcpserver",
    "mcp.server.streamable_http",
    "mcp.server.streamable_http_manager",
    "sse_starlette",
]

datas = [
    (str(ROOT / "app" / "llm" / "prompts"), "app/llm/prompts"),
    (str(ROOT / "app" / "db" / "schema.sql"), "app/db"),
    (str(ROOT / "app" / "db" / "migrations"), "app/db/migrations"),
    # The Terms the app shows and records acceptance of (app/legal/terms.py, D83).
    (str(ROOT / "app" / "legal" / "terms.md"), "app/legal"),
    # NVIDIA's cuBLAS licence, for a GPU folder an earlier build unpacked without it
    # (app/asr/cuda_libs.py add_missing_licences).
    (str(ROOT / "app" / "asr" / "licenses"), "app/asr/licenses"),
]
# The version and commit, stamped by build.ps1 (app/version.py).
if (ROOT / "app" / "build_info.json").exists():
    datas.append((str(ROOT / "app" / "build_info.json"), "app"))
# The interface and ffmpeg are not optional. Without the first the app serves a page that
# says "run npm run build"; without the second no audio file can be imported. Both used
# to be skipped quietly when missing, and the build still "succeeded".
for required in (ROOT / "frontend" / "dist" / "index.html", ROOT / "vendor" / "ffmpeg.exe"):
    if not required.exists():
        raise SystemExit(f"upshot.spec: {required} is missing; run packaging/build.ps1")
datas.append((str(ROOT / "frontend" / "dist"), "frontend/dist"))
# Baked in, never committed (app/gcal/client.py). A build without it runs, and says in
# Settings that it cannot connect a calendar.
if (ROOT / "app" / "gcal" / "google_oauth_client.json").exists():
    datas.append((str(ROOT / "app" / "gcal" / "google_oauth_client.json"), "app/gcal"))
datas.append((str(ROOT / "vendor" / "ffmpeg.exe"), "."))
# Every third-party component and its licence, written by build.ps1
# (scripts/third_party_notices.py): the text, and the same entries as JSON for the app.
for notices in ("THIRD-PARTY-NOTICES.txt", "THIRD-PARTY-NOTICES.json"):
    if not (ROOT / "vendor" / notices).exists():
        raise SystemExit(f"upshot.spec: vendor/{notices} is missing; run packaging/build.ps1")
    datas.append((str(ROOT / "vendor" / notices), "."))
datas += collect_data_files("faster_whisper")  # the bundled Silero VAD model

binaries = (
    collect_dynamic_libs("onnxruntime")
    + collect_dynamic_libs("ctranslate2")
    # Diarization (app/asr/diarize.py): sherpa-onnx loads its native library at import.
    + collect_dynamic_libs("sherpa_onnx")
)

a = Analysis(
    [str(ROOT / "app" / "tray.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # pydantic's hook collects its mypy plugin, which pulls in mypy itself from a build
    # venv that also holds the dev tools; nothing at run time uses either.
    # "av": PyAV is never installed (pyproject.toml); should a venv still hold it, its GPL
    # x264 and x265 must not be collected (scripts/check_dist.py would refuse them anyway).
    excludes=["tkinter", "matplotlib", "torch", "tests", "mypy", "pydantic.mypy", "pydantic.v1.mypy", "av"],
    noarchive=False,
    # The LGPL lets the user run Upshot with their own copy of an LGPL library: pystray
    # (LGPL-3.0) and python-soxr's wrapper (LGPL-2.1, beside its soxr_ext.pyd) ship as
    # plain .py files in _internal, not inside the archive, where they could not be swapped.
    module_collection_mode={"pystray": "py", "soxr": "py"},
)
# ctranslate2's Windows wheel is built without cuDNN (WITH_CUDNN=OFF) yet carries NVIDIA's
# cudnn64_9.dll, which its __init__ loads only because it loads every DLL in its folder.
# Nothing calls it, and NVIDIA licenses it only for machines with an NVIDIA GPU; GPU
# transcription was identical and as fast without it on a GTX 1080 and an MX450.
a.binaries = [entry for entry in a.binaries if not Path(entry[0]).name.lower().startswith("cudnn")]
pyz = PYZ(a.pure)


import sys

sys.path.insert(0, SPECPATH)
from version_info import version_resource  # noqa: E402


exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="upshot",
    console=False,
    version=version_resource(ROOT, name="upshot", description="Upshot"),
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
