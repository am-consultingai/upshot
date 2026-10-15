"""The licence gate: what may ship, judged against packaging/licenses/policy.toml."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load(name: str) -> SimpleNamespace:
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module  # type: ignore[return-value]


gate = load("licence_gate")
POLICY = {
    "allowed": ["MIT", "Apache-2.0", "BSD-3-Clause"],
    "conditional": {"LGPL-3.0-only": ["pystray"]},
    "exception": [{"name": "Bootloader", "licence": "GPL-2.0-or-later WITH Bootloader-exception",
                   "why": "w", "approved": "a"}],
}  # fmt: skip


@pytest.mark.parametrize(
    ("licence", "name", "ok"),
    [
        ("MIT", "x", True),
        ("GPL-2.0-or-later", "x", False),
        ("AGPL-3.0-only", "x", False),
        ("MIT OR Apache-2.0", "x", True),
        ("GPL-3.0-only OR MIT", "x", True),  # dual-licensed: we take MIT
        ("(Apache-2.0 OR BSD-3-Clause)", "x", True),
        ("BSD-3-Clause AND MIT", "x", True),
        ("MIT AND GPL-2.0-only", "x", False),
        ("LGPL-3.0-only", "pystray", True),
        ("LGPL-3.0-only", "something-else", False),
        ("GPL-2.0-or-later WITH Bootloader-exception", "Bootloader", True),
        ("GPL-2.0-or-later WITH Bootloader-exception", "another", False),
        ("see the licence text below", "x", False),
        ("", "x", False),
    ],
)
def test_the_policy_decides(licence: str, name: str, ok: bool) -> None:
    assert gate.allowed(licence, name, POLICY) is ok


def test_the_committed_policy_is_valid_and_every_exception_says_why() -> None:
    policy = gate.load_policy()
    assert "GPL-2.0-or-later" not in policy["allowed"]
    assert all(item["why"] and item["approved"] for item in policy["exception"])


def test_an_exception_without_its_approval_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "policy.toml"
    path.write_text('allowed = []\n[[exception]]\nname = "x"\nlicence = "y"\nwhy = "z"\n')
    with pytest.raises(SystemExit, match="lacks"):
        gate.load_policy(path)


def test_a_gpl_package_fails_and_says_what_pulls_it_in(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = gate.notices.Entry
    monkeypatch.setattr(gate.notices, "extra_entries", lambda: [])
    monkeypatch.setattr(gate.notices, "python_entries", lambda: [
        entry("fine", "1", "MIT"), entry("copyleft", "2", "GPL-3.0-only"),
    ])  # fmt: skip
    problems = gate.licence_problems({"python"}, POLICY)
    assert len(problems) == 1
    assert problems[0].startswith("copyleft 2: 'GPL-3.0-only' is not allowed")
    assert "uv tree --invert --package copyleft" in problems[0]


def test_a_component_with_no_known_licence_is_skipped_only_when_it_is_absent(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    entry = gate.notices.Entry
    absent = entry("FFmpeg", "", "NOASSERTION", note="vendor\\ffmpeg.exe is not on this machine.")
    monkeypatch.setattr(gate.notices, "extra_entries", lambda: [absent])
    assert gate.licence_problems({"other"}, POLICY) == []
    assert "[skip] FFmpeg" in capsys.readouterr().out


def test_a_gpl_binary_in_a_run_time_package_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "pkg.libs").mkdir()
    (tmp_path / "pkg.libs" / "libx264-165.dll").write_bytes(b"MZ")
    (tmp_path / "pkg.libs" / "avcodec.dll").write_bytes(b"MZ --enable-version3 --enable-libx265")
    (tmp_path / "pkg.libs" / "fine.dll").write_bytes(b"MZ --enable-version3")
    dist = SimpleNamespace(
        metadata={"Name": "pkg"},
        files=["pkg.libs/libx264-165.dll", "pkg.libs/avcodec.dll", "pkg.libs/fine.dll"],
        locate_file=lambda f: tmp_path / f,
    )
    monkeypatch.setattr(gate.notices, "python_packages", lambda: [dist])
    assert gate.binary_problems() == [
        "pkg: pkg.libs/libx264-165.dll is a GPL component",
        "pkg: pkg.libs/avcodec.dll is built with --enable-libx265",
    ]
