"""scripts/publish_release.py: a release reaches installed copies only signed and checked (D87)."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import struct
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
_spec = importlib.util.spec_from_file_location(
    "publish_release", ROOT / "scripts" / "publish_release.py"
)
assert _spec and _spec.loader
publish_release = importlib.util.module_from_spec(_spec)
sys.modules["publish_release"] = publish_release
_spec.loader.exec_module(publish_release)


def certificate(name: str = "Upshot test signing") -> x509.Certificate:
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.now(UTC)
    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )


def pe(signature: bytes | None = None, *, pe32_plus: bool = False) -> bytes:
    """A minimal PE file, optionally with a WIN_CERTIFICATE padded the way Windows pads it."""
    header = bytearray(0x200)
    header[:2] = b"MZ"
    struct.pack_into("<I", header, 0x3C, 0x80)
    header[0x80:0x84] = b"PE\0\0"
    optional = 0x80 + 24
    struct.pack_into("<H", header, optional, 0x20B if pe32_plus else 0x10B)
    body = bytes(header)
    if signature is None:
        return body
    entry = struct.pack("<IHH", 8 + len(signature), 0x0200, 0x0002) + signature
    entry += b"\0" * (-len(entry) % 8)
    directories = optional + (112 if pe32_plus else 96)
    patched = bytearray(body)
    struct.pack_into("<II", patched, directories + 4 * 8, len(body), len(entry))
    return bytes(patched) + entry


def signed_pe(cert: x509.Certificate, **kw: Any) -> bytes:
    blob = pkcs7_of([cert])
    return pe(blob, **kw)


def pkcs7_of(certs: list[x509.Certificate]) -> bytes:
    from cryptography.hazmat.primitives.serialization import pkcs7

    return pkcs7.serialize_certificates(certs, serialization.Encoding.DER)


@pytest.mark.parametrize("pe32_plus", [False, True])
def test_the_signers_are_read_from_the_pe_through_the_padding(
    tmp_path: Path, pe32_plus: bool
) -> None:
    cert = certificate()
    path = tmp_path / "Upshot-0.3.0-Setup.exe"
    path.write_bytes(signed_pe(cert, pe32_plus=pe32_plus))
    found = publish_release.authenticode_certificates(path)
    assert [publish_release.thumbprint(c) for c in found] == [publish_release.thumbprint(cert)]
    assert publish_release.thumbprint(cert) == cert.fingerprint(hashes.SHA1()).hex().upper()


def test_an_unsigned_or_foreign_file_is_told_apart(tmp_path: Path) -> None:
    unsigned = tmp_path / "a.exe"
    unsigned.write_bytes(pe())
    assert publish_release.authenticode_certificates(unsigned) == []
    text = tmp_path / "b.exe"
    text.write_bytes(b"#!/bin/sh\n")
    with pytest.raises(publish_release.PublishError, match="not a Windows program"):
        publish_release.authenticode_certificates(text)


# ------------------------------------------------------------------ publishing


class Release:
    """A signed installer, a pinned certificate, a signing key the test's app trusts."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: str) -> None:
        self.cert = certificate()
        self.installer = tmp_path / f"Upshot-{version}-Setup.exe"
        self.installer.write_bytes(signed_pe(self.cert) + b"payload" * 100)
        self.keys = tmp_path / "keys"
        monkeypatch.setattr(publish_release.release_key, "DEFAULT_DIR", self.keys)
        public = publish_release.release_key.new(self.keys / "update-2026-10.key")
        monkeypatch.setattr(publish_release, "PUBLIC_KEYS", (("test", public),))
        monkeypatch.setattr(publish_release, "project_version", lambda: version)
        self.settings: dict[str, Any] = {"signer_thumbprint": publish_release.thumbprint(self.cert)}
        monkeypatch.setattr(publish_release, "local_settings", lambda: self.settings)
        self.out = tmp_path / "out"

    def argv(self, *extra: str) -> list[str]:
        return ["--installer", str(self.installer), "--dry-run", "--out", str(self.out), *extra]


def test_a_dry_run_writes_a_manifest_the_app_accepts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = Release(tmp_path, monkeypatch, "0.3.0")
    notes = tmp_path / "notes.json"
    notes.write_text(json.dumps({"en": "Faster.", "he": "מהיר יותר."}), encoding="utf-8")
    assert publish_release.main(release.argv("--notes", str(notes), "--min-version", "0.2.0")) == 0
    data = (release.out / "stable.json").read_bytes()
    manifest = json.loads(data)
    assert manifest["version"] == "0.3.0" and manifest["rollout"] == 10, "stable starts at 10%"
    assert manifest["url"].endswith("/releases/download/v0.3.0/Upshot-0.3.0-Setup.exe")
    assert manifest["sha256"] == hashlib.sha256(release.installer.read_bytes()).hexdigest()
    assert manifest["size"] == release.installer.stat().st_size
    assert manifest["signer"] == release.settings["signer_thumbprint"]
    assert manifest["notes"]["he"] == "מהיר יותר." and manifest["min_version"] == "0.2.0"
    from app.updates.manifest import parse, verify

    verify(
        data, (release.out / "stable.json.sig").read_bytes(), [publish_release.PUBLIC_KEYS[0][1]]
    )
    assert parse(data, channel="stable").version == "0.3.0"


def test_a_beta_goes_to_every_beta_copy_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = Release(tmp_path, monkeypatch, "0.4.0")
    assert publish_release.main(release.argv("--channel", "beta")) == 0
    manifest = json.loads((release.out / "beta.json").read_text(encoding="utf-8"))
    assert manifest["channel"] == "beta" and manifest["rollout"] == 100


def test_an_installer_for_another_version_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    release = Release(tmp_path, monkeypatch, "0.3.0")
    monkeypatch.setattr(publish_release, "project_version", lambda: "0.3.1")
    assert publish_release.main(release.argv()) == 1
    assert "not the installer for 0.3.1" in capsys.readouterr().err


def test_without_a_pinned_certificate_nothing_is_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    release = Release(tmp_path, monkeypatch, "0.3.0")
    release.settings.pop("signer_thumbprint")
    assert publish_release.main(release.argv()) == 1
    assert "--show-signers" in capsys.readouterr().err
    assert not release.out.exists()


def test_an_installer_signed_by_another_certificate_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    release = Release(tmp_path, monkeypatch, "0.3.0")
    release.installer.write_bytes(signed_pe(certificate("Upshot test signing")))
    assert publish_release.main(release.argv()) == 1
    assert "not signed by the pinned certificate" in capsys.readouterr().err


def test_a_manifest_signed_with_a_key_the_app_does_not_trust_stops_the_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    release = Release(tmp_path, monkeypatch, "0.3.0")
    stranger = (
        Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    )
    monkeypatch.setattr(
        publish_release, "PUBLIC_KEYS", (("stranger", base64.b64encode(stranger).decode()),)
    )
    assert publish_release.main(release.argv()) == 1
    assert "installed copies would refuse this manifest" in capsys.readouterr().err


def test_releases_are_published_from_main_only(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = {"rev-parse": "updates-feedback"}

    def run(*command: str, capture: bool = False) -> str:
        return answers.get(command[1], "")

    monkeypatch.setattr(publish_release, "run", run)
    with pytest.raises(publish_release.PublishError, match="from main"):
        publish_release.check_repository("v0.3.0")


def test_a_dry_run_can_name_a_test_server_but_a_real_release_cannot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    release = Release(tmp_path, monkeypatch, "0.3.0")
    base = "http://127.0.0.1:8399/"
    assert publish_release.main(release.argv("--download-base", base)) == 0
    manifest = json.loads((release.out / "stable.json").read_text(encoding="utf-8"))
    assert manifest["url"] == f"{base}Upshot-0.3.0-Setup.exe"
    real = ["--installer", str(release.installer), "--download-base", base]
    assert publish_release.main(real) == 1
    assert "--dry-run only" in capsys.readouterr().err


def test_source_maps_are_uploaded_for_the_release_with_the_token_kept_off_the_command(
    tmp_path: Path,
) -> None:
    maps = tmp_path / "sourcemaps"
    maps.mkdir()
    (maps / "index-AbC.js").write_text("x", encoding="utf-8")
    (maps / "index-AbC.js.map").write_text("{}", encoding="utf-8")
    token = tmp_path / "token"
    token.write_text("sntrys_not_a_real_token_value\n", encoding="utf-8")
    settings = {
        "sentry": {"org": "acme", "frontend": {"project": "upshot-front"}},
        "sentry_token_file": str(token),
    }
    calls: list[tuple[list[str], dict[str, str]]] = []

    class Done:
        returncode = 0

    def runner(command: list[str], **kwargs: Any) -> Done:
        calls.append((command, kwargs["env"]))
        return Done()

    said = publish_release.upload_sourcemaps(settings, "0.3.0", "abcdef123456", maps, runner)
    assert said == "uploaded 1 maps"
    command, env = calls[0]
    assert command[:5] == ["npx", "--yes", "@sentry/cli@2", "sourcemaps", "upload"]
    assert command[command.index("--release") + 1] == "upshot@0.3.0"
    assert command[command.index("--dist") + 1] == "abcdef123456"
    assert command[command.index("--url-prefix") + 1] == "app:///assets"
    assert env["SENTRY_AUTH_TOKEN"] == "sntrys_not_a_real_token_value"
    assert not any("sntrys_" in part for part in command)


def test_without_maps_or_a_token_the_upload_is_skipped_not_failed(tmp_path: Path) -> None:
    said = publish_release.upload_sourcemaps({}, "0.3.0", "abc", tmp_path / "none")
    assert said.startswith("skipped")
