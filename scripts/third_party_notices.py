"""Write THIRD-PARTY-NOTICES.txt: every component the official app ships and its licence.

    uv run python scripts/third_party_notices.py [output]

Run by ``packaging/build.ps1`` on the machine that builds, after ``uv sync`` and
``npm ci``, because only there are the Windows-only packages installed. The spec
bundles the file, so it lands in the install folder beside ``upshot.exe``.

What it lists:

- the Python packages the app needs at run time: ``[project] dependencies`` in
  ``pyproject.toml`` and everything they require, whose markers hold on this machine;
- the JavaScript packages the interface bundles: ``dependencies`` in
  ``frontend/package.json`` and everything they require;
- what no package manager knows about: ffmpeg, the speech models, the fonts, the
  Python runtime and the PyInstaller bootloader (``EXTRA``).

For each package, the licence its metadata declares, followed by the licence and notice
files it ships, since the MIT, BSD and Apache licences all ask for their text to travel
with the software.
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from importlib import metadata
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
LICENCE_FILE = re.compile(r"(^|/)(LICEN[CS]E|COPYING|NOTICE|AUTHORS)[^/]*$", re.I)
RULE = "=" * 78

#: Shipped with the app, but not through pip or npm.
EXTRA: tuple[tuple[str, str, str], ...] = (
    (
        "Python",
        "PSF-2.0",
        "The Python runtime, bundled by PyInstaller. https://docs.python.org/3/license.html",
    ),
    (
        "PyInstaller bootloader",
        "GPL-2.0-or-later WITH Bootloader-exception",
        "The bootloader that starts upshot.exe. The exception allows it to be used with"
        " software under any licence. https://pyinstaller.org/en/stable/license.html",
    ),
    (
        "FFmpeg (BtbN FFmpeg-Builds, LGPL Windows build)",
        "LGPL-2.1-or-later",
        "ffmpeg.exe, run as a separate program to read imported audio and video files;"
        " it is not linked into Upshot, and you may replace it with your own build."
        " Source code: https://ffmpeg.org/download.html and"
        " https://github.com/BtbN/FFmpeg-Builds. AM Consulting will also provide the"
        " corresponding source on request at office@amconsultingai.com.",
    ),
    (
        "ivrit-ai/whisper-large-v3-ct2",
        "Apache-2.0",
        "Hebrew speech-recognition model by ivrit.ai, downloaded by the installer"
        " unmodified. https://huggingface.co/ivrit-ai/whisper-large-v3-ct2",
    ),
    (
        "Systran/faster-whisper-large-v3 and Systran/faster-whisper-small",
        "MIT",
        "OpenAI's Whisper models converted by SYSTRAN, downloaded by the installer"
        " unmodified. https://huggingface.co/Systran",
    ),
    (
        "Frank Ruhl Libre and IBM Plex Sans Hebrew fonts",
        "OFL-1.1",
        "Bundled with the interface; the licence texts follow.",
    ),
)
#: Copied from the FFmpeg download by build.ps1, when the archive carries one.
FFMPEG_LICENCE = ROOT / "vendor" / "ffmpeg-LICENSE.txt"
FONT_LICENCES = (
    ROOT / "frontend" / "src" / "fonts" / "OFL-frankruhllibre.txt",
    ROOT / "frontend" / "src" / "fonts" / "OFL-ibmplexsanshebrew.txt",
)


# ------------------------------------------------------------------ Python


def python_packages() -> list[metadata.Distribution]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    env = default_environment()
    wanted = [Requirement(r) for r in project["dependencies"]]
    seen: dict[str, metadata.Distribution] = {}
    while wanted:
        req = wanted.pop()
        if req.marker is not None and not req.marker.evaluate({**env, "extra": ""}):
            continue
        name = canonicalize_name(req.name)
        if name in seen or name == "upshot":
            continue
        try:
            dist = metadata.distribution(req.name)
        except metadata.PackageNotFoundError:
            continue  # a marker this machine does not meet, or an optional extra
        seen[name] = dist
        wanted.extend(Requirement(r) for r in dist.requires or ())
    return sorted(seen.values(), key=lambda d: canonicalize_name(d.metadata["Name"]))


def python_licence(dist: metadata.Distribution) -> str:
    meta = dist.metadata
    expression = meta.get("License-Expression")
    if expression:
        return str(expression)
    classifiers = [
        c.split("::")[-1].strip()
        for c in meta.get_all("Classifier") or []
        if c.startswith("License ::")
    ]
    declared = (meta.get("License") or "").strip()
    if declared and len(declared) < 80 and "\n" not in declared:
        return declared
    return "; ".join(classifiers) or "see the licence text below"


def python_licence_texts(dist: metadata.Distribution) -> list[str]:
    texts = []
    for file in dist.files or ():
        if LICENCE_FILE.search(str(file)):
            try:
                texts.append(Path(str(dist.locate_file(file))).read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                continue
    return texts


# ------------------------------------------------------------------ JavaScript


def js_packages() -> list[Path]:
    frontend = ROOT / "frontend"
    modules = frontend / "node_modules"
    wanted = list(json.loads((frontend / "package.json").read_text())["dependencies"])
    seen: dict[str, Path] = {}
    while wanted:
        name = wanted.pop()
        if name in seen:
            continue
        folder = modules / name
        manifest = folder / "package.json"
        if not manifest.exists():
            continue
        seen[name] = folder
        data = json.loads(manifest.read_text(encoding="utf-8"))
        wanted.extend(data.get("dependencies", {}))
        wanted.extend(data.get("optionalDependencies", {}))
    return [seen[name] for name in sorted(seen)]


def js_entry(folder: Path) -> tuple[str, str, str, list[str]]:
    data = json.loads((folder / "package.json").read_text(encoding="utf-8"))
    licence = data.get("license") or "see the licence text below"
    if isinstance(licence, dict):
        licence = licence.get("type", "")
    texts = []
    for path in sorted(folder.iterdir()):
        if path.is_file() and LICENCE_FILE.search(path.name):
            try:
                texts.append(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                continue
    return str(data.get("name", folder.name)), str(data.get("version", "")), str(licence), texts


# ------------------------------------------------------------------ the file


def render() -> str:
    out = [
        "Upshot - third-party notices",
        "",
        "Upshot is made by AM Consulting. It includes, or downloads during installation,",
        "the third-party components listed below, each under its own licence. Nothing in",
        "the Upshot Terms of Service or the Upshot source-code licence limits your rights",
        "under these licences.",
        "",
    ]

    def entry(name: str, version: str, licence: str, note: str, texts: list[str]) -> None:
        out.extend([RULE, f"{name} {version}".rstrip(), f"Licence: {licence}"])
        if note:
            out.append(note)
        for text in texts:
            out.extend(["", text.strip()])
        out.append("")

    out.extend([RULE, "COMPONENTS NOT INSTALLED BY A PACKAGE MANAGER", ""])
    for name, licence, note in EXTRA:
        texts = []
        if licence == "OFL-1.1":
            texts = [p.read_text(encoding="utf-8") for p in FONT_LICENCES]
        elif name.startswith("FFmpeg") and FFMPEG_LICENCE.exists():
            texts = [FFMPEG_LICENCE.read_text(encoding="utf-8", errors="replace")]
        entry(name, "", licence, note, texts)

    out.extend([RULE, "PYTHON PACKAGES", ""])
    for dist in python_packages():
        entry(
            dist.metadata["Name"],
            dist.version,
            python_licence(dist),
            "",
            python_licence_texts(dist),
        )

    out.extend([RULE, "JAVASCRIPT PACKAGES (THE INTERFACE)", ""])
    for folder in js_packages():
        name, version, licence, texts = js_entry(folder)
        entry(name, version, licence, "", texts)
    return "\n".join(out).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    target = Path(args[0]) if args else ROOT / "dist" / "THIRD-PARTY-NOTICES.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    text = render()
    target.write_text(text, encoding="utf-8")
    count = text.count(RULE)
    print(f"{target}: {count} sections")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
