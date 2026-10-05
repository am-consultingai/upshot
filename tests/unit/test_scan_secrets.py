"""scripts/scan_secrets.py: a build carrying a Sentry auth token is refused (D87)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("scan_secrets", ROOT / "scripts" / "scan_secrets.py")
assert _spec and _spec.loader
scan_secrets = importlib.util.module_from_spec(_spec)
sys.modules["scan_secrets"] = scan_secrets
_spec.loader.exec_module(scan_secrets)

# Made up: the shape of an organization token, not a real one.
ORG_TOKEN = "sntrys_" + "eyJpYXQiOjE3NTk2NjAwMDAsInVybCI6Imh0dHBzOi8vZXhhbXBsZSJ9" + "_abcDEF123"
#: A token whose shape the pattern would not recognise, to test the exact-value check.
ODD_TOKEN = "custom-token-value-0123456789abcdef"
DSN = "https://0123456789abcdef0123456789abcdef@o1.ingest.example.io/42"


@pytest.fixture()
def build(tmp_path: Path) -> Path:
    root = tmp_path / "dist" / "upshot"
    (root / "app").mkdir(parents=True)
    (root / "upshot.exe").write_bytes(b"MZ\x90\x00" + b"\x00" * 64)
    (root / "app" / "build_info.json").write_text(
        '{"version": "0.2.0", "sentry": {"dsn": "' + DSN + '"}}', encoding="utf-8"
    )
    return root


def test_a_clean_build_passes_and_its_dsn_is_not_a_finding(
    build: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert scan_secrets.main([str(build)]) == 0
    assert "clean" in capsys.readouterr().out


def test_any_sentry_token_is_refused_and_never_printed(
    build: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (build / "_internal").mkdir()
    (build / "_internal" / "base_library.zip").write_bytes(
        b"\x00junk" + ORG_TOKEN.encode() + b"\x00"
    )
    assert scan_secrets.main([str(build)]) == 1
    err = capsys.readouterr().err
    assert "base_library.zip" in err
    assert ORG_TOKEN not in err


def test_this_machines_token_is_found_from_its_file_whatever_its_shape(
    build: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    token_file = tmp_path / "token"
    token_file.write_text(ODD_TOKEN + "\n", encoding="utf-8")
    (build / "app" / "settings.json").write_text('{"t": "' + ODD_TOKEN + '"}', encoding="utf-8")
    assert scan_secrets.main([str(build), "--secret-file", str(token_file)]) == 1
    err = capsys.readouterr().err
    assert "the secret from token" in err
    assert ODD_TOKEN not in err


def test_an_update_signing_key_in_the_build_is_refused(
    build: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import base64

    seed = base64.b64encode(bytes(range(32))).decode()  # the shape of a release_key.py key
    key_file = tmp_path / "update-2026-10.key"
    key_file.write_text(seed + "\n", encoding="ascii")
    (build / "app" / "keys.txt").write_text(seed, encoding="ascii")
    assert scan_secrets.main([str(build), "--secret-file", str(key_file)]) == 1
    err = capsys.readouterr().err
    assert "update-2026-10.key" in err and seed not in err


def test_the_token_from_the_environment_counts(tmp_path: Path) -> None:
    assert scan_secrets.known_secrets([], {"SENTRY_AUTH_TOKEN": ODD_TOKEN}) == [
        ("SENTRY_AUTH_TOKEN", ODD_TOKEN.encode())
    ]


def test_a_missing_secret_file_or_a_short_value_is_ignored(tmp_path: Path) -> None:
    assert scan_secrets.known_secrets([tmp_path / "absent"], {"SENTRY_AUTH_TOKEN": "short"}) == []


def test_a_token_in_the_source_is_found_even_though_the_freeze_compresses_it(
    build: Path, tmp_path: Path
) -> None:
    source = tmp_path / "app"
    source.mkdir()
    (source / "settings.py").write_text(f'TOKEN = "{ORG_TOKEN}"\n', encoding="utf-8")
    assert scan_secrets.main([str(source), str(build)]) == 1


def test_a_missing_path_is_an_error(build: Path, tmp_path: Path) -> None:
    assert scan_secrets.main([str(build), str(tmp_path / "nothing")]) == 2
