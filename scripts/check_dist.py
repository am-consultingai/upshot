"""Refuse a build that carries a GPL component Upshot may not ship.

    uv run python scripts/check_dist.py dist/upshot

Run by ``packaging/build.ps1`` after PyInstaller. Upshot is not under the GPL, so a GPL
library in the install folder is a licence violation, whichever package brought it in.
PyAV did exactly that: its Windows wheel bundles libx264 and libx265, and every build
shipped them until PyAV was removed (``pyproject.toml``). Two checks:

- file names: the known GPL encoders and PyAV's library folder;
- inside every ``.dll``/``.pyd``/``.exe``: FFmpeg's configure line, which an FFmpeg build
  carries whatever its file is called. ``--enable-gpl`` alone is not enough: PyAV's
  FFmpeg links x264 and x265 with ``--enable-version3`` and no ``--enable-gpl`` at all.
"""

from __future__ import annotations

import fnmatch
import re
import sys
from pathlib import Path

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
#: Configure flags that make an FFmpeg build GPL or non-free, in its configure line.
GPL_FLAGS = re.compile(rb"--enable-(gpl|nonfree|libx264|libx265|libxvid|libfdk-aac)\b")
BINARY_SUFFIXES = {".dll", ".pyd", ".exe"}


def problems(root: Path) -> list[str]:
    found = []
    for path in sorted(root.rglob("*")):
        name = path.name.lower()
        if any(fnmatch.fnmatch(name, pattern) for pattern in FORBIDDEN_NAMES):
            found.append(f"{path.relative_to(root)}: a GPL component")
        elif path.is_file() and path.suffix.lower() in BINARY_SUFFIXES:
            flag = gpl_flag(path)
            if flag:
                found.append(f"{path.relative_to(root)}: built with {flag}")
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
        print(f"[ok  ] {args[0]}: no GPL component")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
