"""The update manifest: its format, its signature, and the offer it makes this copy (D87).

``site/updates/stable.json`` (and ``beta.json``) is written and signed by
``scripts/windows/publish_release.ps1``; ``<name>.json.sig`` holds the base64 Ed25519
signature over the file's exact bytes. A manifest is used only when that signature
verifies against a key in ``app.updates.keys``: whoever controls the website or the
repository still cannot make installed copies run their program.

Fields (``schema`` 1):

- ``version``: the release, ``MAJOR.MINOR.PATCH``.
- ``url``, ``size``, ``sha256``: the installer, from this repository's GitHub Releases.
- ``signer``: the SHA-1 thumbprint of the certificate that Authenticode-signed it.
- ``rollout``: 0-100, the share of installs offered it; each copy keeps its own bucket.
- ``min_version``: a copy older than this must update, whatever the rollout.
- ``critical``: install at the first safe moment, whatever the rollout.
- ``notes``: release notes by interface language (``en``, ``he``); ``notes_url`` optional.

A manifest with another ``schema`` is ignored, so an old copy never misreads a new format.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass, field
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from packaging.version import InvalidVersion, Version

SCHEMA = 1
CHANNELS = ("stable", "beta")
#: Where an installer may come from: this repository's releases, nothing else.
DOWNLOAD_PREFIX = "https://github.com/am-consultingai/upshot/releases/download/"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_THUMBPRINT = re.compile(r"^[0-9A-F]{40}$")
_VERSION = re.compile(r"^\d+\.\d+\.\d+$")


class ManifestError(ValueError):
    """The manifest is unsigned, wrongly signed, or not one this copy can use."""


@dataclass(frozen=True)
class Manifest:
    channel: str
    version: str
    url: str
    size: int
    sha256: str
    signer: str
    rollout: int
    min_version: str | None = None
    critical: bool = False
    published: str | None = None
    notes: dict[str, str] = field(default_factory=dict)
    notes_url: str | None = None

    @property
    def parsed_version(self) -> Version:
        return Version(self.version)

    def as_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "version": self.version,
            "size": self.size,
            "critical": self.critical,
            "min_version": self.min_version,
            "published": self.published,
            "notes": dict(self.notes),
            "notes_url": self.notes_url,
        }


def verify(data: bytes, signature: bytes, public_keys: list[str]) -> None:
    """Raise unless ``signature`` (base64 text) signs ``data`` with one of the keys."""
    try:
        raw = base64.b64decode(signature.strip(), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ManifestError("the signature is not base64") from exc
    for key in public_keys:
        try:
            Ed25519PublicKey.from_public_bytes(base64.b64decode(key)).verify(raw, data)
            return
        except InvalidSignature:
            continue
        except ValueError:
            continue
    raise ManifestError("the manifest is not signed by a known key")


def parse(data: bytes, *, channel: str, download_prefix: str = DOWNLOAD_PREFIX) -> Manifest:
    """Read a manifest whose signature has already been verified."""
    try:
        raw = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ManifestError("the manifest is not JSON") from exc
    if not isinstance(raw, dict):
        raise ManifestError("the manifest is not an object")
    if raw.get("schema") != SCHEMA:
        raise ManifestError(f"unknown manifest schema {raw.get('schema')!r}")
    if raw.get("channel") != channel:
        raise ManifestError(f"a {raw.get('channel')!r} manifest published as {channel!r}")
    version = _version(raw.get("version"), "version")
    min_version = raw.get("min_version")
    if min_version is not None:
        min_version = _version(min_version, "min_version")
    url = raw.get("url")
    if not isinstance(url, str) or not url.startswith(download_prefix) or not url.endswith(".exe"):
        raise ManifestError("the installer is not from this repository's releases")
    size = raw.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise ManifestError("size must be a positive integer")
    sha256 = raw.get("sha256")
    if not isinstance(sha256, str) or not _SHA256.match(sha256):
        raise ManifestError("sha256 must be 64 lowercase hex digits")
    signer = raw.get("signer")
    if not isinstance(signer, str) or not _THUMBPRINT.match(signer.upper()):
        raise ManifestError("signer must be a 40-digit certificate thumbprint")
    rollout = raw.get("rollout", 100)
    if not isinstance(rollout, int) or isinstance(rollout, bool) or not 0 <= rollout <= 100:
        raise ManifestError("rollout must be an integer from 0 to 100")
    critical = raw.get("critical", False)
    if not isinstance(critical, bool):
        raise ManifestError("critical must be true or false")
    notes = raw.get("notes") or {}
    if not isinstance(notes, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in notes.items()
    ):
        raise ManifestError("notes must map languages to text")
    notes_url = raw.get("notes_url")
    if notes_url is not None and (
        not isinstance(notes_url, str) or not notes_url.startswith("https://")
    ):
        raise ManifestError("notes_url must be an https address")
    published = raw.get("published")
    return Manifest(
        channel=channel,
        version=version,
        url=url,
        size=size,
        sha256=sha256,
        signer=signer.upper(),
        rollout=rollout,
        min_version=min_version,
        critical=critical,
        published=published if isinstance(published, str) else None,
        notes=dict(notes),
        notes_url=notes_url,
    )


def _version(value: object, name: str) -> str:
    if not isinstance(value, str) or not _VERSION.match(value):
        raise ManifestError(f"{name} must be MAJOR.MINOR.PATCH")
    try:
        Version(value)
    except InvalidVersion as exc:  # pragma: no cover - the pattern already rules it out
        raise ManifestError(f"{name} is not a version") from exc
    return value


@dataclass(frozen=True)
class Offer:
    """What this copy should do about the newest manifest it may use."""

    manifest: Manifest
    #: Must install whatever the rollout or the user's setting: critical, or too old.
    mandatory: bool


def choose(
    manifests: list[Manifest], current: str, bucket: int
) -> tuple[Offer | None, Manifest | None]:
    """The update to offer, and a newer one held back by the rollout (for the state only).

    The newest manifest newer than ``current`` wins, beta or stable. A copy with a
    channel turned off never sees that channel's manifest, so leaving beta never
    downgrades: the copy waits until stable passes it.
    """
    installed = Version(current)
    newer = sorted(
        (m for m in manifests if m.parsed_version > installed),
        key=lambda m: m.parsed_version,
        reverse=True,
    )
    if not newer:
        return None, None
    best = newer[0]
    too_old = any(m.min_version and installed < Version(m.min_version) for m in manifests)
    mandatory = best.critical or too_old
    if mandatory or bucket < best.rollout:
        return Offer(best, mandatory), None
    # The newest is held back for this copy; an older one fully out may still apply.
    for m in newer[1:]:
        if bucket < m.rollout:
            return Offer(m, m.critical), best
    return None, best
