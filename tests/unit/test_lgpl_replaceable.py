"""LGPL components ship so that the user can replace them.

The LGPL lets whoever receives Upshot run it with a modified copy of an LGPL library.
pystray (LGPL-3.0) is pure Python, which PyInstaller would otherwise freeze into its
archive; soxr (LGPL-2.1) is an extension module, which is always a file of its own.
Measured with PyInstaller 6.22.2 (licence epic, proof P5): with ``module_collection_mode``
``"py"``, pystray lands as ``_internal/pystray/*.py`` and an edit there changes the app.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def analysis_keywords() -> dict[str, ast.expr]:
    tree = ast.parse((ROOT / "packaging" / "upshot.spec").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "Analysis":
            return {kw.arg: kw.value for kw in node.keywords if kw.arg}
    raise AssertionError("upshot.spec has no Analysis(...)")


def test_pystray_ships_as_source_files() -> None:
    modes = ast.literal_eval(analysis_keywords()["module_collection_mode"])
    assert modes["pystray"] == "py"
