"""Refuse a build that carries a GPL or Intel component Upshot may not ship.

    uv run python scripts/check_dist.py dist/upshot

Run by ``packaging/build.ps1`` after PyInstaller. Upshot is not under the GPL, so a GPL
library in the install folder is a licence violation, whichever package brought it in.
PyAV did exactly that: its Windows wheel bundles libx264 and libx265, and every build
shipped them until PyAV was removed (``pyproject.toml``). Two checks:

- file names: the known GPL encoders and PyAV's library folder;
- inside every ``.dll``/``.pyd``/``.exe``: FFmpeg's configure line, which an FFmpeg build
  carries whatever its file is called. ``--enable-gpl`` alone is not enough: PyAV's
  FFmpeg links x264 and x265 with ``--enable-version3`` and no ``--enable-gpl`` at all.

And Intel's runtime libraries (licence epic S12, D100): Upshot ships its own ctranslate2,
built with Microsoft's OpenMP and oneDNN, because PyPI's carries Intel's OpenMP runtime
(``libiomp5md.dll``, under Intel's developer-tools EULA) and oneMKL. A build that picked up
PyPI's wheel again (another Python, a lost source pin) has ``libiomp5md.dll`` and is refused;
ours needs Microsoft's ``vcomp140.dll`` beside it, which must then be in the freeze.
"""

from __future__ import annotations

import fnmatch
import re
import sys
from pathlib import Path

#: Intel's runtime libraries, by lower-case file name: OpenMP, and oneMKL's DLLs.
INTEL_NAMES: tuple[str, ...] = ("libiomp5*", "libiompstubs5*", "mkl_*.dll")
#: Lower-case glob patterns, matched against every file and folder name.
FORBIDDEN_NAMES: tuple[str, ...] = (
    "av.libs",  # PyAV's bundled FFmpeg, with x264 and x265
    "*x264*",
    "*x265*",
    "*xvid*",
    "*fdk-aac*",
    "*fdk_aac*",
    "*postproc-*",  # libpostproc is GPL-only
)
#: The external libraries FFmpeg's configure lists as GPL, GPLv3 or non-free
#: (EXTERNAL_LIBRARY_GPL_LIST, _GPLV3_LIST, _NONFREE_LIST, HWACCEL_LIBRARY_NONFREE_LIST),
#: the union over n6.1, n7.1 and n8.1. A build can link one without ever saying
#: --enable-gpl: PyAV's linked x264 and x265 under --enable-version3.
GPL_LIBRARIES = (
    "avisynth", "frei0r", "libcdio", "libdavs2", "libdvdnav", "libdvdread", "librubberband",
    "libvidstab", "libx264", "libx265", "libxavs", "libxavs2", "libxvid",  # GPL
    "libsmbclient",  # GPLv3
    "decklink", "libfdk_aac", "libmpeghdec", "libtls",  # non-free
    "cuda_nvcc", "cuda_sdk", "libnpp",  # non-free hardware acceleration
)  # fmt: skip
#: Those, and the switches themselves (postproc: GPL-only, until FFmpeg 8.0 removed it).
#: configure reads "-" and "_" alike in a name, and the binary keeps whichever was typed.
GPL_FLAGS = re.compile(
    rb"--enable-(gpl|nonfree|postproc|"
    + b"|".join(name.replace("_", "[-_]").encode() for name in GPL_LIBRARIES)
    + rb")\b"
)
BINARY_SUFFIXES = {".dll", ".pyd", ".exe"}


def problems(root: Path) -> list[str]:
    found = []
    names = set()
    for path in sorted(root.rglob("*")):
        name = path.name.lower()
        names.add(name)
        if any(fnmatch.fnmatch(name, pattern) for pattern in FORBIDDEN_NAMES):
            found.append(f"{path.relative_to(root)}: a GPL component")
        elif any(fnmatch.fnmatch(name, pattern) for pattern in INTEL_NAMES):
            found.append(f"{path.relative_to(root)}: an Intel runtime library")
        elif path.is_file() and path.suffix.lower() in BINARY_SUFFIXES:
            flag = gpl_flag(path)
            if flag:
                found.append(f"{path.relative_to(root)}: built with {flag}")
    if "ctranslate2.dll" in names and "vcomp140.dll" not in names:
        found.append("ctranslate2.dll needs Microsoft's vcomp140.dll, which the freeze lacks")
    return found


def gpl_flag(path: Path, chunk: int = 16 << 20) -> str | None:
    """The first GPL configure flag inside ``path``, read in chunks, not whole."""
    overlap = 64  # longer than any flag, so one split across two chunks is still seen
    tail = b""
    with path.open("rb") as handle:
        while block := handle.read(chunk):
            match = GPL_FLAGS.search(tail + block)
            if match:
                return match.group(0).decode()
            tail = block[-overlap:]
    return None


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or not Path(args[0]).is_dir():
        print("usage: check_dist.py <dist folder>", file=sys.stderr)
        return 2
    found = problems(Path(args[0]))
    for line in found:
        print(f"[FAIL] {line}")
    if not found:
        print(f"[ok  ] {args[0]}: no GPL or Intel component")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
