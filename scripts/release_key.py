"""The update signing keys: create one, sign a manifest, check a signature (D87).

    uv run python scripts/release_key.py new <name>            # a new key pair
    uv run python scripts/release_key.py public <name>         # print its public key
    uv run python scripts/release_key.py sign <name> <file>    # write <file>.sig
    uv run python scripts/release_key.py verify <file>         # check <file>.sig

A private key is the base64 Ed25519 seed in ``~/.config/upshot-release/<name>.key`` (or
``--dir``), readable by its owner only. It never enters the repository and is never
printed: whoever holds it can make every installed copy run their program. The public
key goes into ``app/updates/keys.py``.

Two keys are built into the app: the one that signs today, and a spare kept offline. If
the first is lost or leaks, the spare signs a release that replaces both, and no install
is stranded.
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

DEFAULT_DIR = Path("~/.config/upshot-release")


def key_path(directory: Path, name: str) -> Path:
    if not name.replace("-", "").replace("_", "").isalnum():
        raise SystemExit(f"release_key: a key name is letters, digits, - and _ ({name!r})")
    return directory.expanduser() / f"{name}.key"


def load(path: Path) -> Ed25519PrivateKey:
    seed = base64.b64decode(path.read_text(encoding="ascii").strip(), validate=True)
    return Ed25519PrivateKey.from_private_bytes(seed)


def public_of(private: Ed25519PrivateKey) -> str:
    raw = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return base64.b64encode(raw).decode("ascii")


def new(path: Path) -> str:
    if path.exists():
        raise SystemExit(f"release_key: {path} exists; a key is never overwritten")
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    private = Ed25519PrivateKey.generate()
    seed = private.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    # Created owner-only from the start, not narrowed after writing.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as fh:
        fh.write(base64.b64encode(seed).decode("ascii") + "\n")
    return public_of(private)


def sign(path: Path, target: Path) -> Path:
    signature = load(path).sign(target.read_bytes())
    out = target.with_name(target.name + ".sig")
    out.write_text(base64.b64encode(signature).decode("ascii") + "\n", encoding="ascii")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("new").add_argument("name")
    sub.add_parser("public").add_argument("name")
    signing = sub.add_parser("sign")
    signing.add_argument("name")
    signing.add_argument("file", type=Path)
    sub.add_parser("verify").add_argument("file", type=Path)
    args = parser.parse_args(argv)

    if args.command == "new":
        path = key_path(args.dir, args.name)
        public = new(path)
        print(f"created {path} (owner-only)")
        print(f"public key: {public}")
        return 0
    if args.command == "public":
        print(public_of(load(key_path(args.dir, args.name))))
        return 0
    if args.command == "sign":
        out = sign(key_path(args.dir, args.name), args.file)
        print(f"signed: {out}")
        return 0
    from app.updates.keys import PUBLIC_KEYS
    from app.updates.manifest import ManifestError, verify

    data = args.file.read_bytes()
    signature = args.file.with_name(args.file.name + ".sig").read_bytes()
    try:
        verify(data, signature, [key for _name, key in PUBLIC_KEYS])
    except ManifestError as exc:
        print(f"release_key: {args.file}: {exc}", file=sys.stderr)
        return 1
    print(f"{args.file}: signed by a key the app trusts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
