"""Microsoft's Visual C++ runtime in the freeze: which files, and where they come from.

The app ships the runtime beside its executables, as Microsoft's "app-local" deployment. The
files are Visual Studio's own redistributable copies, which AM Consulting may redistribute
under its Visual Studio Community licence (Distributable Code): the x64 CRT and OpenMP
folders of ``VC\\Redist\\MSVC``, from the Visual Studio package that carries them, published
on Microsoft's download server and listed in the Visual Studio 2026 channel manifest
(``https://aka.ms/vs/18/stable/channel``). ``packaging/build.ps1`` downloads it into
``vendor/msvc-redist``; both specs take every runtime DLL from there (``use_redist``), and
``scripts/check_dist.py`` refuses a freeze whose copies are not these files.

Not whatever the build machine has on its PATH. PyInstaller used to pick up a JDK's copies
of ``vcruntime140``/``msvcp140`` (14.38, not Visual Studio's files) beside System32's
``vcomp140`` (14.50), and the Universal C runtime (``ucrtbase.dll``, ``api-ms-win-*``) from
the same JDK, which Upshot had no right to redistribute (licence review 2026-10-21, H1).

The Universal C runtime is not shipped at all: Windows 10 and later carry it themselves,
and the installer requires Windows 10 (``MinVersion`` in ``installer.iss``).

Copies of the Visual C++ runtime a package brings inside its own folder (numpy's
``numpy.libs``, winrt's) are that package's redistribution and are left as they are: only the
top of ``_internal`` is ours. A Universal C runtime file is refused anywhere.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

PACKAGE = "Microsoft.VC.14.50.18.0.CRT.Redist.X64.base"
VERSION = "14.50.35719"
URL = (
    "https://download.visualstudio.microsoft.com/download/pr/a424e95b-20f6-4af6-844f-9d9a806080a7/"
    "a4ab48822362df6e478eb4ed33bf825a07a6faacd6879c1e3d59929cd38b984b/"
    f"{PACKAGE}.vsix"
)
SHA256 = "a4ab48822362df6e478eb4ed33bf825a07a6faacd6879c1e3d59929cd38b984b"

#: Every DLL in the package's ``x64/Microsoft.VC145.CRT`` and ``x64/Microsoft.VC145.OpenMP``
#: folders, by lower-case name, with its SHA-256.
FILES: dict[str, str] = {
    "concrt140.dll": "b2faf3b85b23c840b654e57d5497a0ad31acd02fb01856cad4725a1715d5f78e",
    "msvcp140.dll": "def46aa6a8f72f27bafac0c43334419486a4d1dcdb6c479a8ef7034b3e1fa4cb",
    "msvcp140_1.dll": "2dd670f874562fbdca5b022df1943d70a57ba91fde559280e3a1daebe4db2380",
    "msvcp140_2.dll": "1d60da3ac2b06482912ca852fa7047436e6e474b4cfffa3bf77f4598cfbf454c",
    "msvcp140_atomic_wait.dll": "e7963645e0d1db08e300614d4c5fa7194bd8173e9ab7a5558859e6b232ed3241",
    "msvcp140_codecvt_ids.dll": "ae8d922b00cdd93e3ebecc37beb46c800f383ebdeb9f9e5b84e04a72428b6fb3",
    "vccorlib140.dll": "6b8d8a76c3e6664293407553650e60b94df9aaafc7c92057ea83032bd228e44f",
    "vcomp140.dll": "31af29c03643f8396a6f26bcd601c6369d26493d7d78b714827ab2801bd284c7",
    "vcruntime140.dll": "184146852727a9db4eea06178716bec3cdbb1015c911f6b0f915b184ad7775b2",
    "vcruntime140_1.dll": "e6bfb3662ab4b1969a73441dbe35c96d51441b6bff8cf1fe7430bd5b246ca605",
    "vcruntime140_threads.dll": "a6222020b500a9a86b36e040c2dbd0e459716db1bf2810a11cd7512ea9b8d89b",
}

#: The Visual C++ runtime's file names, whatever version: what must come from ``FILES``.
RUNTIME_NAME = re.compile(
    r"^(vcruntime140\w*|msvcp140\w*|concrt140|vccorlib140|vcomp140)\.dll$", re.I
)
#: The Universal C runtime, which Windows 10 and later carry and the freeze must not.
UCRT_NAME = re.compile(r"^(ucrtbase|api-ms-win-[a-z0-9-]+)\.dll$", re.I)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def use_redist(binaries: list[tuple[str, str, str]], redist: Path, *, openmp: bool = False) -> list:
    """PyInstaller's binaries, with the runtime at the top of ``_internal`` taken from
    ``redist`` (``vendor/msvc-redist``) and the Universal C runtime left out. ``openmp``
    adds ``vcomp140.dll`` when PyInstaller did not collect it (it may take a DLL in System32
    for part of Windows, and a clean machine need not have it)."""
    out = []
    names = set()
    for dest, src, kind in binaries:
        name = Path(dest).name.lower()
        top = Path(dest).parent == Path(".")
        if top and UCRT_NAME.match(name):
            continue
        if top and RUNTIME_NAME.match(name):
            src = str(_redist_file(redist, name))
            names.add(name)
        out.append((dest, src, kind))
    if openmp and "vcomp140.dll" not in names:
        out.append(("vcomp140.dll", str(_redist_file(redist, "vcomp140.dll")), "BINARY"))
    return out


def _redist_file(redist: Path, name: str) -> Path:
    if name not in FILES:
        raise SystemExit(f"{name}: not in {PACKAGE} (packaging/msvc_runtime.py)")
    path = redist / name
    if not path.is_file():
        raise SystemExit(f"{path} is missing; delete vendor/msvc-redist and build again")
    if sha256(path) != FILES[name]:
        raise SystemExit(
            f"{path} is not {PACKAGE}'s copy; delete vendor/msvc-redist and build again"
        )
    return path
