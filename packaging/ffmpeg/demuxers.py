"""The demuxers Upshot's ffmpeg is built with: exactly ``FORMATS`` in app/audio/ingest.py.

    python3 -I packaging/ffmpeg/demuxers.py app/audio/ingest.py

Read from the source text (``-I`` and no import of the app), so the build needs nothing
installed. FFmpeg's configure names one demuxer differently from the format name ingest
whitelists: "mpeg" is ``mpegps``.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

#: Format name (what ffmpeg reports, and ingest whitelists) → configure's demuxer name.
CONFIGURE_NAMES = {"mpeg": "mpegps"}


def formats(ingest: Path) -> tuple[str, ...]:
    tree = ast.parse(ingest.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else ([node.target] if isinstance(node, ast.AnnAssign) else [])
        )
        for target in targets:
            if isinstance(target, ast.Name) and target.id == "FORMATS" and node.value is not None:
                return tuple(ast.literal_eval(node.value))
    raise SystemExit(f"{ingest}: no FORMATS")


def demuxers(ingest: Path) -> list[str]:
    return [CONFIGURE_NAMES.get(name, name) for name in formats(ingest)]


if __name__ == "__main__":
    print(",".join(demuxers(Path(sys.argv[1]))))
