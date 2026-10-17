"""The third-party notices: complete, correct, and checked against the freeze.

The generator is ``scripts/third_party_notices.py``; ``packaging/build.ps1`` runs it with
``--strict`` before PyInstaller and with ``--check dist\\upshot`` after it.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load() -> SimpleNamespace:
    spec = importlib.util.spec_from_file_location(
        "third_party_notices", ROOT / "scripts" / "third_party_notices.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # its dataclasses look themselves up there
    spec.loader.exec_module(module)
    return module  # type: ignore[return-value]


notices = load()


# ------------------------------------------------------------------ texts


def test_every_fallback_text_says_where_it_came_from() -> None:
    files = sorted((ROOT / "packaging" / "licenses").glob("*.txt"))
    assert files
    for path in files:
        lines = path.read_text(encoding="utf-8").splitlines()
        assert lines[1].startswith("Source: "), path.name
        assert len("\n".join(lines[3:]).strip()) > 200, f"{path.name} holds no licence text"


@pytest.mark.parametrize(
    ("name", "file"),
    [("ctranslate2", "ctranslate2.txt"), ("sherpa_onnx_core", "sherpa-onnx-core.txt"),
     ("@ai-sdk/provider-utils", "ai-sdk-provider-utils.txt"),
     ("winrt-runtime", "winrt.txt"), ("winrt-Windows.Foundation.Collections", "winrt.txt")],
)  # fmt: skip
def test_a_package_without_a_licence_file_takes_the_committed_one(name: str, file: str) -> None:
    (text,) = notices.fallback(name)
    assert text == (ROOT / "packaging" / "licenses" / file).read_text(encoding="utf-8")


def test_the_same_licence_shipped_twice_is_listed_once(tmp_path: Path) -> None:
    (tmp_path / "LICENSE").write_text("MIT text", encoding="utf-8")
    (tmp_path / "COPYING").write_text("MIT text", encoding="utf-8")
    (tmp_path / "NOTICE").write_text("a notice", encoding="utf-8")
    dist = SimpleNamespace(
        metadata={"Name": "twice"}, files=["LICENSE", "COPYING", "NOTICE"],
        locate_file=lambda f: tmp_path / str(f),
    )  # fmt: skip
    assert notices.python_licence_texts(dist) == ["MIT text", "a notice"]


def test_the_apache_text_starts_with_the_licence_itself() -> None:
    text = notices._apache()
    assert text.lstrip(" ").startswith("Apache License"), "no blank line before it"


def test_an_unknown_package_has_no_fallback() -> None:
    assert notices.fallback("no-such-package") == []


@pytest.mark.parametrize(
    ("declared", "expected"),
    [("MIT License", "MIT"), ("Apache Software License", "Apache-2.0"),
     ("3-Clause BSD License", "BSD-3-Clause"), ("LGPLv3", "LGPLv3"), ("BSD", "BSD"),
     ("PSF", "PSF-2.0"), ("Apache-2.0 license", "Apache-2.0"),
     ("Apache licensed, as found in the LICENSE file", "Apache-2.0"),
     ("BSD-3-Clause AND 0BSD", "BSD-3-Clause AND 0BSD")],
)  # fmt: skip
def test_licence_names_become_spdx(declared: str, expected: str) -> None:
    assert notices.spdx(declared) == expected


def test_an_unknown_licence_without_a_note_is_reported_not_a_traceback() -> None:
    assert notices.missing_texts([notices.Entry("x", "", "NOASSERTION", texts=["t"])]) == [
        "x: licence unknown (no note)"
    ]


def test_a_python_runtime_licence_without_openssl_s_or_libffi_s_text_fails_strict() -> None:
    python = notices.Entry("Python", "3.13", "PSF-2.0", texts=["PSF LICENSE AGREEMENT ..."])
    assert len(notices.missing_texts([python])) == 2
    python.texts = ["PSF ... libffi - Copyright (c) ... Apache License Version 2.0 ..."]
    assert notices.missing_texts([python]) == []


def fake_distribution(name: str, version: str, declared: str) -> SimpleNamespace:
    return SimpleNamespace(metadata={"Name": name, "License": declared}, version=version)


def test_a_vague_declaration_is_named_from_the_package_s_own_files() -> None:
    """No "only" is invented for "LGPLv3": pystray's header says "or any later version"."""
    pystray = fake_distribution("pystray", "0.19.5", "LGPLv3")
    assert notices.python_licence(pystray) == "LGPL-3.0-or-later"
    colorama = fake_distribution("colorama", "0.4.6", "BSD")
    assert notices.python_licence(colorama) == "BSD-3-Clause"


def test_another_version_of_such_a_package_is_read_afresh() -> None:
    """The table holds only for the version its claim was checked at."""
    assert notices.python_licence(fake_distribution("pystray", "0.20.0", "GPLv3")) == "GPLv3"


def test_apache_2_s_full_text_is_added_where_only_its_header_ships() -> None:
    header = ["Licensed under the Apache License, Version 2.0 (the License)"]
    texts = notices.with_full_apache("Apache-2.0", header)
    assert len(texts) == 2 and "TERMS AND CONDITIONS" in texts[1]
    full = notices.with_full_apache("Apache-2.0", texts)
    assert full == texts, "added once"
    assert notices.with_full_apache("MIT", header) == header


def test_each_fallback_s_pinned_version_is_the_one_installed() -> None:
    """A fallback text names the version it was fetched at; a bump must fetch it again."""
    from importlib import metadata

    for file, package in (("ctranslate2.txt", "ctranslate2"), ("tokenizers.txt", "tokenizers"),
                          ("sherpa-onnx-core.txt", "sherpa-onnx-core"),
                          ("flatbuffers.txt", "flatbuffers"), ("winrt.txt", "winrt-runtime"),
                          ("pyinstaller.txt", "pyinstaller")):  # fmt: skip
        try:
            installed = metadata.version(package)
        except metadata.PackageNotFoundError:
            continue
        source = (
            (ROOT / "packaging" / "licenses" / file).read_text(encoding="utf-8").splitlines()[1]
        )
        assert f"v{installed}" in source, f"{file} was fetched for another version than {installed}"


def test_lgpl_and_mpl_entries_say_where_their_source_is() -> None:
    line = notices.source_line("soxr", "1.1.0", "LGPL-2.1-or-later")
    version = notices.upshot_version()
    assert f"Upshot-{version}-sources.zip" in line and f"/releases/tag/v{version}" in line
    assert "pypi.org/project/soxr/1.1.0" in line
    assert notices.source_line("certifi", "1", "MPL-2.0")
    assert notices.source_line("numpy", "2", "BSD-3-Clause") == ""


def test_strict_fails_on_an_entry_without_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    items = [
        notices.Entry("has", "1", "MIT", texts=["MIT text"]),
        notices.Entry("lacks", "2", "MIT"),
    ]
    monkeypatch.setattr(notices, "entries", lambda: items)
    target = tmp_path / "N.txt"
    assert notices.main([str(target)]) == 0, "without --strict it only warns"
    assert notices.main([str(target), "--strict"]) == 1
    assert notices.missing_texts(items) == ["lacks 2"]
    data = json.loads(target.with_suffix(".json").read_text(encoding="utf-8"))
    assert [e["name"] for e in data["entries"]] == ["has", "lacks"]


# ------------------------------------------------------------------ what it lists


def test_the_bundle_s_packages_come_from_its_source_maps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    maps = tmp_path / "dist" / "sourcemaps"
    maps.mkdir(parents=True)
    (maps / "index.js.map").write_text(json.dumps({"sources": [
        "../../src/main.tsx",
        "../../node_modules/react/index.js",
        "../../node_modules/@ai-sdk/react/dist/index.mjs",
        "../../node_modules/streamdown/node_modules/marked/lib/marked.esm.js",
    ]}))  # fmt: skip
    monkeypatch.setattr(notices, "ROOT", tmp_path)
    assert notices.source_map_packages() == {
        "react", "@ai-sdk/react", "streamdown/node_modules/marked",
    }  # fmt: skip


@pytest.mark.parametrize(
    ("source", "folder"),
    [("../node_modules/react/index.js", "react"),
     ("../node_modules/@scope/pkg/dist/a.js", "@scope/pkg"),
     ("../node_modules/a/node_modules/@s/b/x.js", "a/node_modules/@s/b"),
     ("../src/main.tsx", None)],
)  # fmt: skip
def test_a_bundled_file_is_credited_to_the_package_folder_it_came_from(
    source: str, folder: str | None
) -> None:
    assert notices.package_dir(source) == folder


@pytest.mark.parametrize(
    ("output", "licence"),
    [("ffmpeg version n8.1\nconfiguration: --enable-version3 --disable-x\n", "LGPL-3.0-or-later"),
     ("ffmpeg version n8.1\nconfiguration: --disable-debug\n", "LGPL-2.1-or-later"),
     ("ffmpeg version n8.1\nconfiguration: --enable-gpl --enable-libx264\n", "GPL"),
     ("ffmpeg version n8.1\nconfiguration: --enable-nonfree\n", "LicenseRef-FFmpeg-nonfree"),
     ("ffmpeg version n8.1\n", "NOASSERTION"),
     ("", "NOASSERTION")],
)  # fmt: skip
def test_ffmpeg_s_licence_comes_only_from_its_configure_line(output: str, licence: str) -> None:
    assert notices.ffmpeg_licence(output) == licence


def test_an_ffmpeg_that_cannot_run_has_no_licence_and_fails_strict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "ffmpeg.exe").write_bytes(b"")
    monkeypatch.setattr(notices, "ROOT", tmp_path)

    def broken(*args: object, **kwargs: object) -> None:
        raise OSError("not a valid Win32 application")

    monkeypatch.setattr(notices.subprocess, "run", broken)
    entry = notices.ffmpeg_entry()
    assert entry.licence == "NOASSERTION"
    assert any("licence unknown" in m for m in notices.missing_texts([entry]))


def fake_vendor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, texts: tuple[str, ...]) -> Path:
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    (vendor / "ffmpeg.exe").write_bytes(b"")
    for name in texts:
        (vendor / name).write_text(f"text of {name}", encoding="utf-8")
    monkeypatch.setattr(notices, "ROOT", tmp_path)

    def ran(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args, 0, "ffmpeg version 8.1.3\nconfiguration: --x\n", ""
        )

    monkeypatch.setattr(notices.subprocess, "run", ran)
    return vendor


def test_all_three_of_ffmpeg_s_texts_reach_its_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_vendor(tmp_path, monkeypatch, notices.FFMPEG_TEXTS)
    notices.MISSING.clear()
    entry = notices.ffmpeg_entry()
    assert entry.texts == [f"text of {name}" for name in notices.FFMPEG_TEXTS]
    assert notices.MISSING == []


def test_a_missing_ffmpeg_notice_fails_strict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_vendor(tmp_path, monkeypatch, ("LICENSE-FFmpeg.txt", "LICENSE-zlib.txt"))
    notices.MISSING.clear()
    notices.ffmpeg_entry()
    assert [
        f"{notices.FFMPEG}: vendor\\LICENSE-mingw-w64-runtime.txt is missing"
    ] == notices.MISSING
    notices.MISSING.clear()


def test_the_source_names_the_exact_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vendor = fake_vendor(tmp_path, monkeypatch, notices.FFMPEG_TEXTS)
    assert "the ffmpeg-* release" in notices.ffmpeg_entry().source, "no marker: generic"
    (vendor / "ffmpeg.source.txt").write_text("ffmpeg-8.1.3-audio-1 61a9f586", encoding="utf-8")
    source = notices.ffmpeg_entry().source
    assert "/releases/tag/ffmpeg-8.1.3-audio-1 (its ffmpeg-sources.zip)" in source


def test_ffmpeg_s_licence_is_read_from_its_configure_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "ffmpeg.exe").write_bytes(b"")
    monkeypatch.setattr(notices, "ROOT", tmp_path)

    def version(stdout: str) -> None:
        def ran(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args, 0, stdout, "")

        monkeypatch.setattr(notices.subprocess, "run", ran)

    version("ffmpeg version n8.1\nconfiguration: --enable-version3 --disable-libx264\n")
    assert notices.ffmpeg_entry().licence == "LGPL-3.0-or-later"
    assert any("GNU GENERAL PUBLIC LICENSE" in t for t in notices.ffmpeg_entry().texts), (
        "the LGPL v3 incorporates the GPL v3"
    )
    version("ffmpeg version n8.1\nconfiguration: --disable-debug\n")
    assert notices.ffmpeg_entry().licence == "LGPL-2.1-or-later"
    (tmp_path / "vendor" / "ffmpeg.exe").unlink()
    assert notices.ffmpeg_entry().licence == "NOASSERTION", "nothing is guessed without it"


def test_the_extras_name_what_no_package_manager_installs() -> None:
    names = " ".join(e.name for e in notices.extra_entries())
    parts = [
        "Python", "PyInstaller", "FFmpeg", "ivrit-ai", "Systran", "pyannote", "3D-Speaker",
        "Silero VAD", "fonts", "Tailwind", "oneDNN", "compiled-in third-party code",
        "NVIDIA CUDA runtime", "NVIDIA cuBLAS", "Microsoft",
    ]  # fmt: skip
    for part in parts:
        assert part in names, part


# ------------------------------------------------------------------ --check


def fake_dist(tmp_path: Path, listed: list[str], files: list[str]) -> Path:
    dist = tmp_path / "upshot"
    internal = dist / "_internal"
    internal.mkdir(parents=True)
    (internal / "THIRD-PARTY-NOTICES.json").write_text(
        json.dumps({"entries": [{"name": n} for n in listed]}), encoding="utf-8"
    )
    for name in files:
        path = internal / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    return dist


EXTRAS = ["Python", "Microsoft Visual C++ and Universal C runtime",
          notices.FFMPEG]  # fmt: skip


def test_every_file_accounted_for_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notices, "owners", lambda: {"numpy/__init__.py": "numpy"})
    dist = fake_dist(tmp_path, ["numpy", *EXTRAS], [
        "numpy/__init__.py", "numpy/__init__.pyc", "python313.dll", "_ssl.pyd", "base_library.zip",
        "VCRUNTIME140.dll", "winrt/MSVCP140.dll", "ffmpeg.exe", "app/db/schema.sql",
        "frontend/dist/index.html",
    ])  # fmt: skip
    assert notices.check(dist) == []
    assert notices.main(["--check", str(dist)]) == 0


def test_a_file_no_one_accounts_for_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notices, "owners", lambda: {})
    dist = fake_dist(tmp_path, EXTRAS, ["mystery/lib.dll"])
    assert notices.check(dist) == [
        "mystery/lib.dll: no package installed it and no notices entry covers it"
    ]
    assert notices.main(["--check", str(dist)]) == 1


def test_an_unmapped_extension_inside_a_package_is_not_python_s_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only top-level .pyd files are the standard library's."""
    monkeypatch.setattr(notices, "owners", lambda: {})
    dist = fake_dist(tmp_path, EXTRAS, ["_ssl.pyd", "foo/_bar.pyd"])
    assert notices.check(dist) == [
        "foo/_bar.pyd: no package installed it and no notices entry covers it"
    ]


def test_the_mcp_bridge_s_freeze_is_checked_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(notices, "owners", lambda: {})
    dist = fake_dist(tmp_path, EXTRAS, [])
    (dist / "mcp" / "_internal").mkdir(parents=True)
    (dist / "mcp" / "_internal" / "stray.dll").write_bytes(b"x")
    assert notices.check(dist) == [
        "stray.dll: no package installed it and no notices entry covers it"
    ]


def test_a_package_the_notices_do_not_list_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(notices, "owners", lambda: {"av/__init__.py": "av"})
    dist = fake_dist(tmp_path, EXTRAS, ["av/__init__.py"])
    assert notices.check(dist) == ["av/__init__.py: from av, which the notices do not list"]


def test_a_freeze_without_the_notices_fails(tmp_path: Path) -> None:
    (tmp_path / "upshot" / "_internal").mkdir(parents=True)
    assert notices.check(tmp_path / "upshot")[0].endswith("THIRD-PARTY-NOTICES.json is missing")
