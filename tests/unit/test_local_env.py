"""scripts/local_env.py: this machine's values for the developer scripts, from .env."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import local_env  # noqa: E402


def test_values_comments_quotes_and_home(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "# a comment\n\nA=one\nB = \"two words\"\nC='three'\nD=~/keys\nE=\n", encoding="utf-8"
    )
    assert local_env.read(env) == {"A": "one", "B": "two words", "C": "three", "D": "~/keys",
                                   "E": ""}  # fmt: skip
    assert local_env.get("E", env) is None
    assert local_env.path("D", env) == Path("~/keys").expanduser()
    assert local_env.path("MISSING", env) is None


def test_no_file_is_no_values(tmp_path: Path) -> None:
    assert local_env.read(tmp_path / ".env") == {}


def test_a_line_that_is_not_key_value_is_refused(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("just words\n", encoding="utf-8")
    with pytest.raises(SystemExit, match=r"\.env:1"):
        local_env.read(env)


def test_a_required_key_that_is_missing_names_itself(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="UPSHOT_RELEASE_KEY_DIR is not set"):
        local_env.require_path("UPSHOT_RELEASE_KEY_DIR", "the keys", tmp_path / ".env")


def test_every_key_the_code_reads_is_in_env_example() -> None:
    example = local_env.read(ROOT / ".env.example")
    code = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for folder in ("scripts", "packaging")
        for path in (ROOT / folder).rglob("*")
        if path.suffix in {".py", ".ps1", ".cmd"} and path.is_file()
    )
    import re

    used = set(re.findall(r"\bUPSHOT_[A-Z_]+\b", code)) & {
        k for k in re.findall(r"\b(UPSHOT_[A-Z_]+)\b", code)
        if f'"{k}"' in code or f"'{k}'" in code or f"%{k}%" in code
    }  # fmt: skip
    assert used <= set(example), sorted(used - set(example))
