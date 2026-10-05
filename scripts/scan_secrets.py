"""Refuse a build that carries a Sentry auth token (D87).

    uv run python scripts/scan_secrets.py app frontend\\dist vendor dist\\upshot \
        [--token-file <path>]

The official build ships its Sentry DSNs, which only let a program send events. The auth
token is different: it can create releases and upload files to the Sentry organization,
and only the publish step on the build machine may hold it. ``build.ps1`` runs this
before Inno Setup packs the build, so a token that found its way in stops the build
instead of shipping in every installer.

It scans both what goes into the freeze and what comes out: PyInstaller compresses Python
modules into its archives, so a token in a ``.py`` file is only visible in ``app/``, while
data files (``build_info.json`` and the like) are visible in ``dist/upshot``.

Two checks, over every file's bytes:

- the shape of any Sentry token (``sntrys_`` organization, ``sntryu_`` user, followed by
  a long run of token characters), whoever's it is;
- the exact value of this machine's token, from ``SENTRY_AUTH_TOKEN`` and from each
  ``--token-file`` that exists. A missing file is not an error: the shape check still runs.

The token is never printed: a finding names the file and which check matched.
Exit 0 when clean, 1 when anything matched, 2 when a path to scan does not exist.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path

#: Sentry's token prefixes and the base64-like body that follows them.
TOKEN_SHAPE = re.compile(rb"sntry[su]_[A-Za-z0-9+/=_-]{40,}")
#: A value shorter than this is not a token; matching it would flag ordinary bytes.
MIN_TOKEN_LEN = 20


def known_tokens(token_files: Iterable[Path], env: dict[str, str] | None = None) -> list[bytes]:
    env = dict(os.environ) if env is None else env
    found: list[bytes] = []
    candidates = [env.get("SENTRY_AUTH_TOKEN", "")]
    for path in token_files:
        try:
            candidates.append(path.expanduser().read_text(encoding="utf-8"))
        except OSError:
            continue
    for value in candidates:
        token = value.strip().encode()
        if len(token) >= MIN_TOKEN_LEN and token not in found:
            found.append(token)
    return found


def files_under(root: Path) -> Iterator[Path]:
    if root.is_file():
        yield root
        return
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            yield path


def scan(root: Path, tokens: list[bytes]) -> list[tuple[Path, str]]:
    findings: list[tuple[Path, str]] = []
    for path in files_under(root):
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if TOKEN_SHAPE.search(data):
            findings.append((path, "a Sentry auth token"))
        elif any(token in data for token in tokens):
            findings.append((path, "this machine's Sentry auth token"))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", type=Path, nargs="+", help="folders or files to scan")
    parser.add_argument("--token-file", type=Path, action="append", default=[])
    args = parser.parse_args(argv)
    missing = [path for path in args.paths if not path.exists()]
    for path in missing:
        print(f"scan_secrets: {path} does not exist", file=sys.stderr)
    if missing:
        return 2
    tokens = known_tokens(args.token_file)
    findings = [finding for root in args.paths for finding in scan(root, tokens)]
    for path, what in findings:
        print(f"scan_secrets: {what} in {path}", file=sys.stderr)
    if findings:
        return 1
    checks = "the token shape" + (f" and {len(tokens)} known token(s)" if tokens else "")
    names = ", ".join(str(path) for path in args.paths)
    print(f"scan_secrets: {names}: clean ({checks})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
