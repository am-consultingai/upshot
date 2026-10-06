"""Refuse a build that carries a secret: a Sentry auth token or an update signing key (D87).

    uv run python scripts/scan_secrets.py app frontend\\dist vendor dist\\upshot \
        [--secret-file <path>]...

The official build ships its Sentry DSNs and the update keys' *public* halves; none of
them is secret. What must never ship: the Sentry auth token, which can create releases
and upload files to the Sentry organization, and the update signing keys
(``scripts/release_key.py``), which can make every installed copy install a program.
Only the publish step on the build machine holds them. ``build.ps1`` runs this before
Inno Setup packs the build, so a secret that found its way in stops the build instead of
shipping in every installer.

It scans both what goes into the freeze and what comes out: PyInstaller compresses Python
modules into its archives, so a secret in a ``.py`` file is only visible in ``app/``,
while data files (``build_info.json`` and the like) are visible in ``dist/upshot``.

Two checks, over every file's bytes:

- the shape of any Sentry token (``sntrys_`` organization, ``sntryu_`` user, followed by
  a long run of token characters), whoever's it is;
- the exact value of each of this machine's secrets: ``SENTRY_AUTH_TOKEN`` and every
  ``--secret-file`` that exists (``packaging/release.local.json`` lists them). A missing
  file is not an error: on another machine it is simply not there to leak.

A secret is never printed: a finding names the scanned file and where the secret came from.
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
#: A value shorter than this is not a secret; matching it would flag ordinary bytes.
MIN_SECRET_LEN = 20


def known_secrets(
    secret_files: Iterable[Path], env: dict[str, str] | None = None
) -> list[tuple[str, bytes]]:
    """(where it came from, its value) for every secret this machine holds."""
    env = dict(os.environ) if env is None else env
    candidates = [("SENTRY_AUTH_TOKEN", env.get("SENTRY_AUTH_TOKEN", ""))]
    for path in secret_files:
        try:
            candidates.append((path.name, path.expanduser().read_text(encoding="utf-8")))
        except OSError:
            continue
    found: list[tuple[str, bytes]] = []
    for label, value in candidates:
        secret = value.strip().encode()
        if len(secret) >= MIN_SECRET_LEN and all(secret != known for _, known in found):
            found.append((label, secret))
    return found


def files_under(root: Path) -> Iterator[Path]:
    if root.is_file():
        yield root
        return
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            yield path


def scan(root: Path, secrets: list[tuple[str, bytes]]) -> list[tuple[Path, str]]:
    findings: list[tuple[Path, str]] = []
    for path in files_under(root):
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if TOKEN_SHAPE.search(data):
            findings.append((path, "a Sentry auth token"))
        for label, secret in secrets:
            if secret in data:
                findings.append((path, f"the secret from {label}"))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", type=Path, nargs="+", help="folders or files to scan")
    parser.add_argument("--secret-file", type=Path, action="append", default=[])
    args = parser.parse_args(argv)
    missing = [path for path in args.paths if not path.exists()]
    for path in missing:
        print(f"scan_secrets: {path} does not exist", file=sys.stderr)
    if missing:
        return 2
    secrets = known_secrets(args.secret_file)
    findings = [finding for root in args.paths for finding in scan(root, secrets)]
    for path, what in findings:
        print(f"scan_secrets: {what} in {path}", file=sys.stderr)
    if findings:
        return 1
    checks = "the token shape" + (f" and {len(secrets)} known secret(s)" if secrets else "")
    names = ", ".join(str(path) for path in args.paths)
    print(f"scan_secrets: {names}: clean ({checks})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
