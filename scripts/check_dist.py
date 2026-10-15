"""Refuse a build that carries a GPL component Upshot may not ship.

    uv run python scripts/check_dist.py dist/upshot

Run by ``packaging/build.ps1`` after PyInstaller. Upshot is not under the GPL, so a GPL
library in the install folder is a licence violation, whichever package brought it in.
PyAV did exactly that: its Windows wheel bundles libx264 and libx265, and every build
shipped them until PyAV was removed (``pyproject.toml``). Two checks:

- file names: the known GPL encoders and PyAV's library folder;
- inside every ``.dll``/``.pyd``/``.exe``: FFmpeg's ``--enable-gpl``, which an FFmpeg build
  carries in its configuration string whatever its file is called.
"""

from __future__ import annotations

import fnmatch
import sys
from pathlib import Path

#: Lower-case glob patterns, matched against every file and folder name.
FORBIDDEN_NAMES: tuple[str, ...] = (
    "av.libs",  # PyAV's bundled FFmpeg, with x264 and x265
    "libx264*",
    "libx265*",
    "*xvid*",
    "*fdk-aac*",
    "*fdk_aac*",
    "postproc-*",  # libpostproc is GPL-only
)
#: FFmpeg writes its configure line into its libraries and programs.
GPL_MARKER = b"--enable-gpl"
BINARY_SUFFIXES = {".dll", ".pyd", ".exe"}


def problems(root: Path) -> list[str]:
    found = []
    for path in sorted(root.rglob("*")):
        name = path.name.lower()
        if any(fnmatch.fnmatch(name, pattern) for pattern in FORBIDDEN_NAMES):
            found.append(f"{path.relative_to(root)}: a GPL component")
        elif (
            path.is_file()
            and path.suffix.lower() in BINARY_SUFFIXES
            and GPL_MARKER in path.read_bytes()
        ):
            found.append(f"{path.relative_to(root)}: built with --enable-gpl")
    return found


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
