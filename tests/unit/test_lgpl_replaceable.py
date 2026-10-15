"""LGPL components ship so that the user can replace them.

The LGPL lets whoever receives Upshot run it with a modified copy of an LGPL library.
PyInstaller freezes pure-Python modules into its archive, where they cannot be swapped, so
the LGPL ones are collected as source instead: pystray (LGPL-3.0), and python-soxr's
wrapper (LGPL-2.1), whose compiled ``soxr_ext.pyd`` is a file of its own either way.
Measured with PyInstaller 6.22.2 on a minimal app (licence epic, proof P5): with
``module_collection_mode`` ``"py"`` both land in ``_internal`` as ``.py`` files beside any
extension, still work, and an edit to them changes the app.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
#: Pure-Python run-time dependencies under the LGPL (the licence audit's inventory).
LGPL_PACKAGES = ("pystray", "soxr")


def collection_modes() -> dict[str, str]:
    """``module_collection_mode`` as written in upshot.spec's ``Analysis(...)``."""
    tree = ast.parse((ROOT / "packaging" / "upshot.spec").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "Analysis":
            for keyword in node.keywords:
                if keyword.arg == "module_collection_mode":
                    return dict(ast.literal_eval(keyword.value))
            raise AssertionError("Analysis(...) sets no module_collection_mode")
    raise AssertionError("upshot.spec has no Analysis(...)")


@pytest.mark.parametrize("package", LGPL_PACKAGES)
def test_the_spec_collects_each_lgpl_package_as_source(package: str) -> None:
    assert collection_modes().get(package) == "py"
