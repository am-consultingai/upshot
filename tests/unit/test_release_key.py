"""scripts/release_key.py: update signing keys that never leave their file (D87)."""

from __future__ import annotations

import importlib.util
import stat
import sys
from pathlib import Path

import pytest

from app.updates.manifest import ManifestError, verify

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("release_key", ROOT / "scripts" / "release_key.py")
assert _spec and _spec.loader
release_key = importlib.util.module_from_spec(_spec)
sys.modules["release_key"] = release_key
_spec.loader.exec_module(release_key)


def test_a_new_key_is_owner_only_and_its_seed_is_never_printed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert release_key.main(["--dir", str(tmp_path / "keys"), "new", "test-key"]) == 0
    path = tmp_path / "keys" / "test-key.key"
    if sys.platform != "win32":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    seed = path.read_text(encoding="ascii").strip()
    out = capsys.readouterr().out
    assert seed not in out
    assert release_key.public_of(release_key.load(path)) in out


def test_a_key_is_never_overwritten(tmp_path: Path) -> None:
    release_key.main(["--dir", str(tmp_path), "new", "k"])
    before = (tmp_path / "k.key").read_bytes()
    with pytest.raises(SystemExit):
        release_key.main(["--dir", str(tmp_path), "new", "k"])
    assert (tmp_path / "k.key").read_bytes() == before


def test_a_name_cannot_reach_outside_the_key_folder(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        release_key.main(["--dir", str(tmp_path), "new", "../escape"])


def test_what_it_signs_verifies_with_its_public_key_and_nothing_else(tmp_path: Path) -> None:
    release_key.main(["--dir", str(tmp_path), "new", "a"])
    release_key.main(["--dir", str(tmp_path), "new", "b"])
    manifest = tmp_path / "stable.json"
    manifest.write_bytes(b'{"schema": 1}')
    assert release_key.main(["--dir", str(tmp_path), "sign", "a", str(manifest)]) == 0
    signature = (tmp_path / "stable.json.sig").read_bytes()
    public_a = release_key.public_of(release_key.load(tmp_path / "a.key"))
    public_b = release_key.public_of(release_key.load(tmp_path / "b.key"))
    verify(manifest.read_bytes(), signature, [public_a])
    with pytest.raises(ManifestError):
        verify(manifest.read_bytes(), signature, [public_b])


def test_verify_uses_the_keys_built_into_the_app(tmp_path: Path) -> None:
    release_key.main(["--dir", str(tmp_path), "new", "stranger"])
    manifest = tmp_path / "stable.json"
    manifest.write_bytes(b"{}")
    release_key.main(["--dir", str(tmp_path), "sign", "stranger", str(manifest)])
    assert release_key.main(["verify", str(manifest)]) == 1
