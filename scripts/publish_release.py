"""Publish a release so installed copies update themselves (D87, A4).

    uv run python scripts/publish_release.py --installer <Upshot-X.Y.Z-Setup.exe> \
        [--channel stable|beta] [--rollout 10] [--critical] [--min-version X.Y.Z] \
        [--notes notes.json] [--dry-run --out <dir>]
    uv run python scripts/publish_release.py --set-rollout 50 [--channel stable]
    uv run python scripts/publish_release.py --halt [--channel stable]
    uv run python scripts/publish_release.py --show-signers <installer>

Run in WSL on the build machine, after ``packaging\\build.ps1 -Sign`` has produced the
installer. A release:

1. **Checks.** On ``main``, clean, and pushed; the tag ``vX.Y.Z`` (the version in
   ``pyproject.toml``) does not exist; the installer's name carries that version; the
   installer is Authenticode-signed by the pinned certificate
   (``signer_thumbprint`` in ``packaging/release.local.json``).
2. **Tags and releases.** ``git tag vX.Y.Z``, pushed; ``gh release create`` with the
   installer and its ``.sha256`` (a beta is marked pre-release).
3. **Writes and signs the manifest**, ``site/updates/<channel>.json`` and ``.sig``, with the
   update signing key (``scripts/release_key.py``), then checks it with the app's own
   verifier before anything is pushed.
4. **Publishes it**: commits ``site/updates/`` and pushes ``main``; GitHub Pages serves it.
5. **Creates the Sentry release** ``upshot@X.Y.Z``, so crash reports group by version.
6. **Reads it back** from the website and verifies it again, as an installed copy would.

``--set-rollout`` and ``--halt`` (rollout 0) re-sign and publish the current manifest only.
``--dry-run`` does the checks and writes a signed manifest to ``--out`` without tagging,
releasing, committing or pushing: for the machine B test (B7), which serves it locally,
with ``--download-base`` naming that server instead of GitHub Releases.
Nothing secret is printed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.serialization import pkcs7  # noqa: E402

from app.updates.keys import PUBLIC_KEYS  # noqa: E402
from app.updates.manifest import (  # noqa: E402
    CHANNELS,
    DOWNLOAD_PREFIX,
    SCHEMA,
    ManifestError,
    parse,
    verify,
)
from scripts import release_key  # noqa: E402

SITE_UPDATES = ROOT / "site" / "updates"
SITE_URL = "https://upshot.amconsultingai.com/updates/"
RELEASE_LOCAL = ROOT / "packaging" / "release.local.json"
SIGNING_KEY = "update-2026-10"
#: Pages takes a minute or two to deploy; the read-back waits this long at most.
READ_BACK_S = 300


class PublishError(Exception):
    """A check failed; nothing irreversible has been done yet unless the message says so."""


# ------------------------------------------------------------------ the installer


def authenticode_certificates(path: Path) -> list[x509.Certificate]:
    """The certificates in a PE file's embedded Authenticode signature (empty if unsigned).

    The signer's and the timestamping authority's: the pinned thumbprint must be among
    them. On Windows the app checks the signature itself (app/updates/authenticode.py);
    this is the release-side sanity check that the build was signed at all, and by whom.
    """
    data = path.read_bytes()
    if data[:2] != b"MZ":
        raise PublishError(f"{path.name} is not a Windows program")
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe : pe + 4] != b"PE\0\0":
        raise PublishError(f"{path.name} has no PE header")
    optional = pe + 24
    magic = struct.unpack_from("<H", data, optional)[0]
    directories = optional + (112 if magic == 0x20B else 96)
    offset, size = struct.unpack_from(
        "<II", data, directories + 4 * 8
    )  # IMAGE_DIRECTORY_ENTRY_SECURITY
    if not offset or not size:
        return []
    length, _revision, kind = struct.unpack_from("<IHH", data, offset)
    if kind != 0x0002:  # WIN_CERT_TYPE_PKCS_SIGNED_DATA
        raise PublishError(f"{path.name} has an unexpected certificate type {kind:#x}")
    return pkcs7.load_der_pkcs7_certificates(_der_sequence(data[offset + 8 : offset + length]))


def _der_sequence(blob: bytes) -> bytes:
    """The DER SEQUENCE at the start of ``blob``, without the zero padding Windows adds
    to align a WIN_CERTIFICATE to 8 bytes."""
    if len(blob) < 2 or blob[0] != 0x30:
        raise PublishError("the signature is not a DER sequence")
    first = blob[1]
    if first < 0x80:
        return blob[: 2 + first]
    count = first & 0x7F
    length = int.from_bytes(blob[2 : 2 + count], "big")
    return blob[: 2 + count + length]


def thumbprint(certificate: x509.Certificate) -> str:
    # SHA-1 because that is what Windows calls a certificate's thumbprint.
    return certificate.fingerprint(hashes.SHA1()).hex().upper()


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


# ------------------------------------------------------------------ the manifest


def build_manifest(
    *,
    channel: str,
    version: str,
    installer: Path,
    signer: str,
    rollout: int,
    critical: bool,
    min_version: str | None,
    notes: dict[str, str],
    now: datetime,
    download_base: str = DOWNLOAD_PREFIX,
) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "schema": SCHEMA,
        "channel": channel,
        "version": version,
        "published": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "url": f"{download_base}v{version}/{installer.name}"
        if download_base == DOWNLOAD_PREFIX
        else f"{download_base}{installer.name}",
        "size": installer.stat().st_size,
        "sha256": sha256_of(installer),
        "signer": signer,
        "rollout": rollout,
        "critical": critical,
        "notes": notes,
    }
    if min_version:
        manifest["min_version"] = min_version
    return manifest


def write_signed(
    manifest: dict[str, Any], directory: Path, key: Path, download_base: str = DOWNLOAD_PREFIX
) -> Path:
    """Write ``<channel>.json`` and its ``.sig``, then check both as an installed copy would."""
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{manifest['channel']}.json"
    target.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    release_key.sign(key, target)
    signature = target.with_name(target.name + ".sig").read_bytes()
    check_signed(target.read_bytes(), signature, manifest, download_base)
    return target


def check_signed(
    data: bytes, signature: bytes, expected: dict[str, Any], download_base: str = DOWNLOAD_PREFIX
) -> None:
    try:
        verify(data, signature, [key for _name, key in PUBLIC_KEYS])
        parsed = parse(data, channel=expected["channel"], download_prefix=download_base)
    except ManifestError as exc:
        raise PublishError(f"installed copies would refuse this manifest: {exc}") from exc
    if parsed.version != expected["version"] or parsed.sha256 != expected["sha256"]:
        raise PublishError("the signed manifest does not say what was meant")


# ------------------------------------------------------------------ git, gh, Sentry


def run(*command: str, capture: bool = False) -> str:
    result = subprocess.run(command, cwd=ROOT, check=False, text=True, capture_output=True)
    if result.returncode != 0:
        raise PublishError(
            f"{' '.join(command[:3])}: {result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout.strip() if capture else ""


def check_repository(tag: str | None) -> None:
    branch = run("git", "rev-parse", "--abbrev-ref", "HEAD", capture=True)
    if branch != "main":
        raise PublishError(f"releases are published from main, not {branch}")
    if run("git", "status", "--porcelain", capture=True):
        raise PublishError("the working tree has uncommitted changes")
    run("git", "fetch", "-q", "origin", "main")
    if run("git", "rev-parse", "HEAD", capture=True) != run(
        "git", "rev-parse", "origin/main", capture=True
    ):
        raise PublishError("main is not the same as origin/main: pull or push first")
    if tag and run("git", "tag", "--list", tag, capture=True):
        raise PublishError(f"{tag} already exists: a version is never published twice")


def project_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as fh:
        return str(tomllib.load(fh)["project"]["version"])


def local_settings() -> dict[str, Any]:
    try:
        return dict(json.loads(RELEASE_LOCAL.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        raise PublishError(
            f"{RELEASE_LOCAL.relative_to(ROOT)} is missing or broken: {exc}"
        ) from exc


def upload_sourcemaps(
    settings: dict[str, Any],
    version: str,
    commit: str,
    directory: Path,
    runner: Any = subprocess.run,
) -> str:
    """The front end's source maps, for this release only (D87, C5).

    ``build.ps1`` moves them out of the app into ``dist/sourcemaps`` with the bundles they
    describe. Sentry's own CLI uploads them under ``app:///assets``, the path the server
    gives every page frame, so a crash report from the interface reads as source. The
    token goes in the environment, never on the command line or the screen.
    """
    sentry = settings.get("sentry") or {}
    token_file = Path(
        os.path.expanduser(str(settings.get("sentry_token_file", "~/.config/sentry/token")))
    )
    project = (sentry.get("frontend") or {}).get("project")
    if not directory.is_dir() or not list(directory.glob("*.map")):
        return f"skipped (no source maps in {directory})"
    if not token_file.exists() or not project:
        return "skipped (no Sentry token or front-end project on this machine)"
    command = [
        "npx", "--yes", "@sentry/cli@2", "sourcemaps", "upload",
        "--org", str(sentry["org"]), "--project", str(project),
        "--release", f"upshot@{version}", "--dist", commit,
        "--url-prefix", "app:///assets", str(directory),
    ]  # fmt: skip
    env = {**os.environ, "SENTRY_AUTH_TOKEN": token_file.read_text(encoding="utf-8").strip()}
    result = runner(command, env=env, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return f"failed (exit {result.returncode}): interface reports arrive minified"
    return f"uploaded {len(list(directory.glob('*.map')))} maps"


def sentry_release(settings: dict[str, Any], version: str, commit: str) -> str:
    sentry = settings.get("sentry") or {}
    token_file = Path(
        os.path.expanduser(str(settings.get("sentry_token_file", "~/.config/sentry/token")))
    )
    if not token_file.exists():
        return "skipped (no Sentry token on this machine)"
    region = "us" if sentry.get("region", "us") == "us" else "de"
    projects = [p["project"] for p in (sentry.get("desktop"), sentry.get("frontend")) if p]
    body = json.dumps(
        {"version": f"upshot@{version}", "projects": projects, "refs": [], "ref": commit}
    ).encode()
    request = urllib.request.Request(
        f"https://{region}.sentry.io/api/0/organizations/{sentry['org']}/releases/",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token_file.read_text(encoding='utf-8').strip()}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return f"created ({response.status})"
    except urllib.error.HTTPError as exc:
        if exc.code == 208:
            return "already there"
        return f"failed ({exc.code}): crash reports still arrive, ungrouped by release"


def read_back(channel: str, expected: dict[str, Any], wait_s: float = READ_BACK_S) -> None:
    url = f"{SITE_URL}{channel}.json"
    deadline = time.monotonic() + wait_s
    while True:
        try:
            stamp = f"?t={int(time.time())}"
            with urllib.request.urlopen(url + stamp, timeout=15) as response:
                data = response.read()
            with urllib.request.urlopen(url + ".sig" + stamp, timeout=15) as response:
                signature = response.read()
            if (
                json.loads(data).get("version") == expected["version"]
                and json.loads(data).get("rollout") == expected["rollout"]
            ):
                check_signed(data, signature, expected)
                return
        except (urllib.error.URLError, ValueError):
            pass
        if time.monotonic() > deadline:
            raise PublishError(
                f"{url} does not show {expected['version']} yet: check the Pages workflow"
            )
        time.sleep(15)


# ------------------------------------------------------------------ the commands


def publish(args: argparse.Namespace) -> None:
    settings = local_settings()
    version = project_version()
    tag = f"v{version}"
    installer = Path(args.installer).expanduser()
    if installer.name != f"Upshot-{version}-Setup.exe":
        raise PublishError(f"{installer.name} is not the installer for {version} (pyproject)")
    pinned = str(settings.get("signer_thumbprint") or "").upper()
    if not pinned:
        raise PublishError(
            "no signer_thumbprint in packaging/release.local.json: "
            "run --show-signers on a signed installer and add the signing certificate's"
        )
    found = {thumbprint(c) for c in authenticode_certificates(installer)}
    if pinned not in found:
        raise PublishError(f"{installer.name} is not signed by the pinned certificate")
    notes = json.loads(Path(args.notes).read_text(encoding="utf-8")) if args.notes else {}
    rollout = args.rollout if args.rollout is not None else (100 if args.channel == "beta" else 10)
    manifest = build_manifest(
        channel=args.channel,
        version=version,
        installer=installer,
        signer=pinned,
        rollout=rollout,
        critical=args.critical,
        min_version=args.min_version,
        notes=notes,
        now=datetime.now(UTC),
        download_base=args.download_base or DOWNLOAD_PREFIX,
    )
    key = release_key.key_path(release_key.DEFAULT_DIR, args.key)
    if args.download_base and not args.dry_run:
        raise PublishError("--download-base is for a test server, with --dry-run only")
    if args.dry_run:
        base = args.download_base or DOWNLOAD_PREFIX
        out = write_signed(manifest, Path(args.out).expanduser(), key, base)
        print(f"dry run: {out} written and verified ({version}, rollout {rollout}%)")
        return

    check_repository(tag)
    commit = run("git", "rev-parse", "--short=12", "HEAD", capture=True)
    checksum = installer.with_name(installer.name + ".sha256")
    checksum.write_text(f"{manifest['sha256']}  {installer.name}\n", encoding="utf-8")
    write_signed(manifest, SITE_UPDATES, key)  # checked before anything leaves the machine
    run("git", "tag", "-a", tag, "-m", f"Upshot {version}")
    run("git", "push", "-q", "origin", tag)
    print(f"tagged {tag} ({commit})")
    release = ["gh", "release", "create", tag, str(installer), str(checksum)]
    release += ["--title", f"Upshot {version}", "--notes", notes.get("en", f"Upshot {version}")]
    if args.channel == "beta":
        release.append("--prerelease")
    run(*release)
    print(f"GitHub release {tag} created with {installer.name}")
    # Before the manifest: the first crash report from the new version already reads.
    maps = (
        Path(args.sourcemaps).expanduser() if args.sourcemaps else installer.parent / "sourcemaps"
    )
    print(f"Sentry source maps: {upload_sourcemaps(settings, version, commit, maps)}")
    publish_manifest(args.channel, f"updates: {version} on {args.channel} at {rollout}%")
    print(f"Sentry release upshot@{version}: {sentry_release(settings, version, commit)}")
    read_back(args.channel, manifest)
    print(f"published: {SITE_URL}{args.channel}.json serves {version} to {rollout}% of copies")


def change_rollout(args: argparse.Namespace, rollout: int) -> None:
    current = SITE_UPDATES / f"{args.channel}.json"
    if not current.exists():
        raise PublishError(f"{current.relative_to(ROOT)} does not exist: nothing is published")
    manifest = json.loads(current.read_text(encoding="utf-8"))
    check_repository(None)
    manifest["rollout"] = rollout
    key = release_key.key_path(release_key.DEFAULT_DIR, args.key)
    write_signed(manifest, SITE_UPDATES, key)
    word = "halted" if rollout == 0 else f"at {rollout}%"
    publish_manifest(args.channel, f"updates: {manifest['version']} on {args.channel} {word}")
    read_back(args.channel, manifest)
    print(f"{manifest['version']} on {args.channel}: {word}")


def publish_manifest(channel: str, message: str) -> None:
    files = [f"site/updates/{channel}.json", f"site/updates/{channel}.json.sig"]
    run("git", "add", *files)
    run("git", "commit", "-q", "-m", message)
    run("git", "push", "-q", "origin", "main")


def show_signers(path: Path) -> None:
    certificates = authenticode_certificates(path)
    if not certificates:
        print(f"{path.name}: not signed")
    for certificate in certificates:
        print(f"{thumbprint(certificate)}  {certificate.subject.rfc4514_string()}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--installer")
    parser.add_argument("--channel", choices=CHANNELS, default="stable")
    parser.add_argument("--rollout", type=int, choices=range(0, 101), metavar="0-100")
    parser.add_argument("--critical", action="store_true")
    parser.add_argument("--min-version")
    parser.add_argument("--notes", help='JSON: {"en": "...", "he": "..."}')
    parser.add_argument("--key", default=SIGNING_KEY)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--out", default="dist/updates-test")
    parser.add_argument(
        "--sourcemaps",
        help="the front end's source maps (default: the installer's folder, sourcemaps/)",
    )
    parser.add_argument(
        "--download-base",
        help="a test server instead of GitHub Releases (with --dry-run; the copy needs "
        "updates.download_prefix set to the same)",
    )
    parser.add_argument("--set-rollout", type=int, choices=range(0, 101), metavar="0-100")
    parser.add_argument("--halt", action="store_true")
    parser.add_argument("--show-signers", metavar="INSTALLER")
    args = parser.parse_args(argv)
    try:
        if args.show_signers:
            show_signers(Path(args.show_signers).expanduser())
        elif args.halt:
            change_rollout(args, 0)
        elif args.set_rollout is not None:
            change_rollout(args, args.set_rollout)
        elif args.installer:
            publish(args)
        else:
            parser.error("--installer, --set-rollout, --halt or --show-signers")
    except PublishError as exc:
        print(f"publish_release: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
