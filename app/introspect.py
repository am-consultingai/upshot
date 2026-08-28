"""Walk the application package. Used by the import self-test and by packaging."""

from __future__ import annotations

import importlib
import pkgutil

import app


def module_names() -> list[str]:
    names = ["app"]
    for info in pkgutil.walk_packages(app.__path__, prefix="app."):
        names.append(info.name)
    return sorted(names)


def import_all_modules() -> tuple[list[str], list[str]]:
    """Import every ``app.*`` module. Returns (imported, failures)."""
    imported: list[str] = []
    failures: list[str] = []
    for name in module_names():
        try:
            importlib.import_module(name)
            imported.append(name)
        except Exception as exc:
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
    return imported, failures
