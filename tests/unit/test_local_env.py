"""scripts/local_env.py: this machine's values for the developer scripts, from .env."""

from __future__ import annotations

import re
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


#: Where the code reads a key from a .env: local_env in Python, Read-DotEnv's table in
#: PowerShell, the variables the sandbox kit's cmd checks after reading its .env.
READ_SITES = (
    re.compile(r"local_env\.(?:get|path|require_path)\(\s*[\"'](\w+)[\"']"),
    re.compile(r"\$dotEnv\[[\"'](\w+)[\"']\]"),
    re.compile(r'if "!(\w+)!"==""'),
)


def keys_read() -> set[str]:
    import subprocess

    found: set[str] = set()
    listed = subprocess.run(
        ["git", "ls-files", "scripts", "packaging"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()  # tracked files only: a developer's own gitignored scripts do not count
    for name in listed:
        path = ROOT / name
        if path.suffix in {".py", ".ps1", ".cmd"} and path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            for site in READ_SITES:
                found.update(site.findall(text))
    return found


def test_every_key_the_code_reads_from_env_is_in_env_example_and_back() -> None:
    example = set(local_env.read(ROOT / ".env.example"))
    read = keys_read()
    assert read, "no read site found: the patterns no longer match the code"
    assert read - example == set(), "read but not in .env.example"
    assert example - read == set(), "in .env.example but read nowhere"
    assert all(value == "" for value in local_env.read(ROOT / ".env.example").values())


def test_a_relative_path_is_from_the_repository_root_and_a_bom_is_read(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_bytes("\ufeffA=packaging/x.json\n".encode())
    assert local_env.path("A", env) == ROOT / "packaging" / "x.json"
