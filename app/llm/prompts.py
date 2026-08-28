"""Prompts are versioned files, and the version used is recorded in ``meta.json``."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from app import paths

VERSION_RE = re.compile(r"^\s*version:\s*(\S+)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    text: str


def prompts_dir() -> Path:
    return paths.resource("app", "llm", "prompts")


@cache
def load(name: str) -> Prompt:
    path = prompts_dir() / f"{name}.md"
    raw = path.read_text(encoding="utf-8")
    match = VERSION_RE.search(raw)
    version = match.group(1) if match else "0"
    body = VERSION_RE.sub("", raw, count=1).strip()
    return Prompt(name=name, version=version, text=body)


def versions(*names: str) -> dict[str, str]:
    return {name: load(name).version for name in names}
