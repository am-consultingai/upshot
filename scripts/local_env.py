"""This machine's own values for the developer scripts, from ``.env`` at the repository root.

``.env`` is gitignored and never committed; ``.env.example`` names every key it may hold.
Nothing a script needs on one machine only (where a signing key or a token lives) is
written into the code: it comes from here, or the script stops and says which key to set.

    KEY=value          # a line each; blank lines and lines starting with # are ignored
    KEY="value"        # quotes around a value are taken off
    KEY=~/somewhere    # a path may start with ~
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"


def read(path: Path | None = None) -> dict[str, str]:
    """Every ``KEY=value`` in ``path`` (default: ``.env`` at the repository root); nothing
    when the file does not exist."""
    path = ENV_FILE if path is None else path
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not key.replace("_", "").isalnum():
            raise SystemExit(f"{path.name}:{number}: expected KEY=value")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def get(key: str, path: Path | None = None) -> str | None:
    """The value of ``key``, or None when it is not set (or set empty)."""
    return read(path).get(key) or None


def path(key: str, env_file: Path | None = None) -> Path | None:
    """``key``'s value as a path, ``~`` expanded, or None when it is not set."""
    value = get(key, env_file)
    return Path(value).expanduser() if value else None


def require_path(key: str, what: str, env_file: Path | None = None) -> Path:
    """``key``'s value as a path; stops with what to set when it is missing."""
    found = path(key, env_file)
    if found is None:
        raise SystemExit(
            f"{key} is not set: add it to .env at the repository root ({what};"
            " .env.example lists every key)"
        )
    return found
