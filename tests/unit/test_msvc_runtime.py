"""Microsoft's Visual C++ runtime in the freeze comes from Visual Studio's redistributable
files, never from the build machine's PATH, and the Universal C runtime is not shipped
(packaging/msvc_runtime.py, scripts/check_dist.py; licence review H1)."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "packaging"))
import check_dist  # noqa: E402
import msvc_runtime  # noqa: E402
import third_party_notices as notices  # noqa: E402


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def redist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A vendor/msvc-redist whose files stand in for Microsoft's, pinned as FILES."""
    folder = tmp_path / "msvc-redist"
    folder.mkdir()
    files = {}
    for name in ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll", "vcomp140.dll"):
        (folder / name).write_bytes(name.encode())
        files[name] = _sha(name.encode())
    monkeypatch.setattr(msvc_runtime, "FILES", files)
    return folder


def test_the_runtime_comes_from_the_redist_folder_not_the_path(redist: Path) -> None:
    binaries = [
        ("vcruntime140.dll", r"C:\Program Files\Microsoft\jdk\bin\vcruntime140.dll", "BINARY"),
        ("MSVCP140.dll", r"C:\Windows\System32\msvcp140.dll", "BINARY"),
        ("python313.dll", r"C:\Python313\python313.dll", "BINARY"),
    ]
    out = msvc_runtime.use_redist(binaries, redist)
    assert out == [
        ("vcruntime140.dll", str(redist / "vcruntime140.dll"), "BINARY"),
        ("MSVCP140.dll", str(redist / "msvcp140.dll"), "BINARY"),
        ("python313.dll", r"C:\Python313\python313.dll", "BINARY"),
    ]


def test_the_universal_c_runtime_is_left_out(redist: Path) -> None:
    binaries = [("ucrtbase.dll", "x", "BINARY"), ("api-ms-win-crt-heap-l1-1-0.dll", "x", "BINARY"),
                ("api-ms-win-core-file-l1-2-0.dll", "x", "BINARY")]  # fmt: skip
    assert msvc_runtime.use_redist(binaries, redist) == []


def test_a_packages_own_copy_is_left_as_it_is(redist: Path) -> None:
    """numpy and winrt carry their own msvcp140 in their folders: their redistribution."""
    binaries = [
        # Forward slashes, which every platform reads as a folder (CI runs on Linux).
        ("numpy.libs/msvcp140-a4c2.dll", "n", "BINARY"),
        ("winrt/MSVCP140.dll", "w", "BINARY"),
    ]
    assert msvc_runtime.use_redist(binaries, redist) == binaries


def test_openmp_is_added_when_pyinstaller_left_it_out(redist: Path) -> None:
    out = msvc_runtime.use_redist([], redist, openmp=True)
    assert out == [("vcomp140.dll", str(redist / "vcomp140.dll"), "BINARY")]
    assert msvc_runtime.use_redist([("VCOMP140.DLL", "s", "BINARY")], redist, openmp=True) == [
        ("VCOMP140.DLL", str(redist / "vcomp140.dll"), "BINARY")
    ]


def test_a_tampered_or_missing_redist_file_stops_the_build(redist: Path) -> None:
    (redist / "msvcp140.dll").write_bytes(b"not Microsoft's")
    with pytest.raises(SystemExit, match=r"is not Microsoft\.VC"):
        msvc_runtime.use_redist([("msvcp140.dll", "x", "BINARY")], redist)
    (redist / "vcruntime140.dll").unlink()
    with pytest.raises(SystemExit, match="missing"):
        msvc_runtime.use_redist([("vcruntime140.dll", "x", "BINARY")], redist)
    with pytest.raises(SystemExit, match="not in"):
        msvc_runtime.use_redist([("msvcp140_9.dll", "x", "BINARY")], redist)


def test_the_pinned_package_is_visual_studios() -> None:
    assert msvc_runtime.URL.startswith("https://download.visualstudio.microsoft.com/")
    assert msvc_runtime.SHA256 in msvc_runtime.URL and msvc_runtime.URL.endswith(
        f"{msvc_runtime.PACKAGE}.vsix"
    )
    assert {"vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll", "vcomp140.dll"} <= set(
        msvc_runtime.FILES
    )
    assert all(len(h) == 64 for h in msvc_runtime.FILES.values())


# ------------------------------------------------------------------ check_dist


def _freeze(tmp_path: Path, files: dict[str, bytes]) -> Path:
    for name, data in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return tmp_path


def test_visual_studios_copies_pass(tmp_path: Path) -> None:
    dist = _freeze(
        tmp_path, {"_internal/vcruntime140.dll": b"vc", "mcp/_internal/VCRUNTIME140.dll": b"vc"}
    )
    assert check_dist.problems(dist, {"vcruntime140.dll": _sha(b"vc")}) == []


def test_another_copy_of_the_runtime_fails(tmp_path: Path) -> None:
    """A JDK's vcruntime140, for one: Microsoft-signed, but not Visual Studio's file."""
    dist = _freeze(tmp_path, {"_internal/msvcp140.dll": b"jdk"})
    assert check_dist.problems(dist, {"msvcp140.dll": _sha(b"vs")}) == [
        f"_internal/msvcp140.dll: not Visual Studio's redistributable copy ({msvc_runtime.PACKAGE})"
    ]


@pytest.mark.parametrize(
    "name", ["_internal/ucrtbase.dll", "mcp/_internal/api-ms-win-crt-heap-l1-1-0.dll"]
)
def test_the_universal_c_runtime_fails(tmp_path: Path, name: str) -> None:
    dist = _freeze(tmp_path, {name: b"MZ"})
    assert check_dist.problems(dist, {}) == [
        f"{name}: the Universal C runtime, which Windows 10 carries; the freeze must not"
    ]


def test_a_copy_beside_an_executable_is_checked(tmp_path: Path) -> None:
    """PyInstaller puts the runtime in _internal today; one beside upshot.exe is still ours."""
    dist = _freeze(tmp_path, {"vcruntime140.dll": b"jdk", "mcp/msvcp140.dll": b"jdk"})
    assert check_dist.problems(dist, {}) == [
        f"mcp/msvcp140.dll: not Visual Studio's redistributable copy ({msvc_runtime.PACKAGE})",
        f"vcruntime140.dll: not Visual Studio's redistributable copy ({msvc_runtime.PACKAGE})",
    ]


def test_a_packages_own_copy_is_not_checked(tmp_path: Path) -> None:
    dist = _freeze(tmp_path, {"_internal/numpy.libs/msvcp140-a4c2.dll": b"n",
                              "_internal/winrt/MSVCP140.dll": b"w"})  # fmt: skip
    assert check_dist.problems(dist, {}) == []


def test_the_notices_have_no_entry_for_the_universal_c_runtime() -> None:
    for name in msvc_runtime.FILES:
        assert notices.unowned(name) == notices.MSVC, name
    assert notices.unowned("ucrtbase.dll") is None
    assert notices.unowned("api-ms-win-crt-heap-l1-1-0.dll") is None


def test_the_installer_requires_windows_10() -> None:
    iss = (ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")
    assert "\nMinVersion=10.0\n" in iss
