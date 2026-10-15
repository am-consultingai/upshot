"""THIRD-PARTY-NOTICES: every component the official app ships or fetches, and its licence.

    uv run python scripts/third_party_notices.py [output.txt] [--strict]
    uv run python scripts/third_party_notices.py --check <dist folder>

Run by ``packaging/build.ps1`` on the machine that builds, after ``uv sync``, ``npm ci``
and the interface build, because only there are the Windows-only packages installed and
the bundle's source maps written. It writes ``output.txt`` and, beside it, ``output.json``
(the same entries, for the app's licences page); the spec bundles both.

What it lists:

- the Python packages the app needs at run time: ``[project] dependencies`` in
  ``pyproject.toml`` and everything they require, whose markers hold on this machine;
- the JavaScript packages the interface bundle really contains, read from its source maps
  (``dist/sourcemaps`` or ``frontend/dist/assets``); without them, the dependency closure
  of ``frontend/package.json``;
- what no package manager knows about (``EXTRA``): the Python runtime, the PyInstaller
  bootloader, ffmpeg (its licence read from its own configure line), the speech models,
  Silero VAD, the fonts, Tailwind's CSS, Intel's OpenMP runtime, NVIDIA's cuBLAS and the
  Microsoft runtime.

Each entry carries its licence (SPDX where one fits) and the licence and notice texts the
package ships; where it ships none, the text is taken from ``packaging/licenses`` (each file
says where it came from). LGPL and MPL entries also say where their source is.

``--strict`` (the build) fails when an entry has no licence text. ``--check`` (the build,
after PyInstaller) maps every file in the freeze to the package that installed it, or to
an ``EXTRA`` entry, and fails on a file that maps to nothing or to a component the bundled
notices do not list.
"""

from __future__ import annotations

import fnmatch
import json
import re
import subprocess
import sys
import tomllib
from dataclasses import asdict, dataclass, field
from importlib import metadata
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
LICENCES = ROOT / "packaging" / "licenses"
LICENCE_FILE = re.compile(
    r"(^|/)(LICEN[CS]E|COPYING|NOTICE|AUTHORS|ThirdPartyNotices)[^/]*$|(^|/)licenses?/[^/]+$",
    re.I,
)
RULE = "=" * 78

#: Licence strings as package metadata writes them, and the SPDX identifier for each.
SPDX = {
    "mit license": "MIT",
    "mit": "MIT",
    "apache software license": "Apache-2.0",
    "apache 2.0": "Apache-2.0",
    "apache license 2.0": "Apache-2.0",
    "apache licensed, as found in the license file": "Apache-2.0",
    "3-clause bsd license": "BSD-3-Clause",
    "python software foundation license": "PSF-2.0",
    "mozilla public license 2.0 (mpl 2.0)": "MPL-2.0",
    "isc license (iscl)": "ISC",
    "apache-2.0 license": "Apache-2.0",
    "psf": "PSF-2.0",
}
#: Packages whose metadata declares no licence, or one too vague to name (a bare "BSD",
#: "LGPLv3"), as their own files state it, at the version checked. Each read from what
#: the package ships: pycaw's LICENSE (MIT), windows-toasts' LICENSE (Apache 2.0),
#: colorama's LICENSE.txt (three clauses), pystray's header ("version 3 ... or any later").
DECLARED_IN_FILE = {
    ("pycaw", "20251023"): "MIT",
    ("windows-toasts", "1.3.1"): "Apache-2.0",
    ("colorama", "0.4.6"): "BSD-3-Clause",
    ("pystray", "0.19.5"): "LGPL-3.0-or-later",
}
#: What the Python runtime's licence file must carry for the libraries the freeze ships
#: beside it (libcrypto-3/libssl-3, libffi-8): python.org's Windows build has OpenSSL's
#: Apache 2.0 text and libffi's notice; a uv-managed CPython's file has neither.
RUNTIME_TEXTS = (
    ("the Apache 2.0 text OpenSSL 3 is under", "Apache License"),
    ("libffi's notice", "libffi"),
)
#: Problems found while collecting, reported (and fatal under --strict) with the texts.
MISSING: list[str] = []
#: Copyleft families whose source must be offered with the binary (story S6).
SOURCE_OFFERED = ("LGPL", "MPL")


@dataclass
class Entry:
    name: str
    version: str
    licence: str
    note: str = ""
    texts: list[str] = field(default_factory=list)
    source: str = ""
    kind: str = "python"  # python | javascript | other


def spdx(declared: str) -> str:
    return SPDX.get(declared.strip().lower(), declared.strip())


#: Package families that share one licence file in ``packaging/licenses``.
SHARED_FALLBACKS = (("winrt-*", "winrt"),)


def fallback(name: str) -> list[str]:
    """``packaging/licenses/<name>.txt``; an npm scope's "@" and "/" become "-"."""
    stem = canonicalize_name(name.lstrip("@").replace("/", "-"))
    stem = next(
        (file for pattern, file in SHARED_FALLBACKS if fnmatch.fnmatch(stem, pattern)), stem
    )
    path = LICENCES / f"{stem}.txt"
    return [path.read_text(encoding="utf-8")] if path.is_file() else []


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
    known = DECLARED_IN_FILE.get((canonicalize_name(meta["Name"]), dist.version))
    if known:  # checked at that version only: another version is read afresh
        return known
    expression = meta.get("License-Expression")
    if expression:
        return str(expression)
    declared = (meta.get("License") or "").strip()
    if declared and len(declared) < 80 and "\n" not in declared:
        return spdx(declared)
    classifiers = [
        spdx(c.split("::")[-1].strip())
        for c in meta.get_all("Classifier") or []
        if c.startswith("License ::")
    ]
    return " OR ".join(classifiers) or "see the licence text below"


def python_licence_texts(dist: metadata.Distribution) -> list[str]:
    texts = []
    for file in dist.files or ():
        if LICENCE_FILE.search(str(file).replace("\\", "/")) and not str(file).endswith(".py"):
            try:
                texts.append(Path(str(dist.locate_file(file))).read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                continue
    unique = list(dict.fromkeys(texts))  # some wheels carry the same licence twice
    return unique or fallback(dist.metadata["Name"])


def source_line(name: str, version: str, licence: str) -> str:
    if any(family in licence for family in SOURCE_OFFERED):
        return f"https://pypi.org/project/{name}/{version}/#files (the source distribution)"
    return ""


def with_full_apache(licence: str, texts: list[str]) -> list[str]:
    """Apache-2.0 asks for a copy of the licence; some packages ship only its header."""
    if "Apache-2.0" in licence and not any("TERMS AND CONDITIONS" in t for t in texts):
        return [*texts, licence_file("apache-2.0.txt")[0]]
    return texts


def python_entries() -> list[Entry]:
    out = []
    for dist in python_packages():
        name, version = dist.metadata["Name"], dist.version
        licence = python_licence(dist)
        texts = with_full_apache(licence, python_licence_texts(dist))
        out.append(
            Entry(name, version, licence, texts=texts, source=source_line(name, version, licence))
        )
    return out


# ------------------------------------------------------------------ JavaScript


def package_dir(source: str) -> str | None:
    """The folder below ``node_modules`` of the package a bundled source file is from:
    ``react``, ``@ai-sdk/react``, or a nested copy like ``streamdown/node_modules/marked``."""
    if "node_modules/" not in source:
        return None
    parts = source.split("node_modules/", 1)[1].split("/")
    found: list[str] = []
    while parts:
        width = 2 if parts[0].startswith("@") else 1
        found.append("/".join(parts[:width]))
        parts = parts[width:]
        if parts[:1] != ["node_modules"]:
            break
        parts = parts[1:]
    return "/node_modules/".join(found)


def source_map_packages() -> set[str] | None:
    """The npm packages the interface bundle holds (their folders below node_modules), from
    its source maps; None without them."""
    maps = list((ROOT / "dist" / "sourcemaps").glob("*.js.map"))
    maps += list((ROOT / "frontend" / "dist" / "assets").glob("*.js.map"))
    if not maps:
        return None
    found: set[str] = set()
    for path in maps:
        for source in json.loads(path.read_text(encoding="utf-8")).get("sources", []):
            folder = package_dir(source)
            if folder:
                found.add(folder)
    return found


def js_packages() -> list[Path]:
    frontend = ROOT / "frontend"
    modules = frontend / "node_modules"
    bundled = source_map_packages()
    if bundled is not None:
        MISSING.extend(f"{name} (in the bundle, not in node_modules)"
                       for name in sorted(bundled) if not (modules / name).is_dir())  # fmt: skip
        return [modules / name for name in sorted(bundled) if (modules / name).is_dir()]
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
        wanted.extend(data.get("peerDependencies", {}))
    return [seen[name] for name in sorted(seen)]


def js_entry(folder: Path) -> Entry:
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
    name = str(data.get("name", folder.name))
    version = str(data.get("version", ""))
    texts = with_full_apache(str(licence), texts or fallback(name))
    return Entry(name, version, str(licence), texts=texts, kind="javascript")


# ------------------------------------------------------------------ everything else


def python_runtime_licence() -> list[str]:
    for name in ("LICENSE.txt", "LICENSE"):
        for base in (
            Path(sys.base_prefix),
            Path(sys.base_prefix) / "Lib",
            Path(sys.base_prefix)
            / "lib"
            / f"python{sys.version_info.major}.{sys.version_info.minor}",
        ):
            path = base / name
            if path.is_file():
                return [path.read_text(encoding="utf-8", errors="replace")]
    return []


def ffmpeg_licence(version_text: str) -> str:
    """From ``ffmpeg -version``: NOASSERTION unless its configure line was read."""
    configure = next(
        (line for line in version_text.splitlines() if line.startswith("configuration:")), None
    )
    if configure is None:
        return "NOASSERTION"
    if "--enable-nonfree" in configure:
        return "LicenseRef-FFmpeg-nonfree"  # not redistributable at all
    if "--enable-gpl" in configure:
        return "GPL"  # build.ps1 refuses such a build; the licence gate would too
    if "--enable-version3" in configure:
        return "LGPL-3.0-or-later"
    return "LGPL-2.1-or-later"


def ffmpeg_entry() -> Entry:
    """The licence follows from how this ffmpeg.exe was configured, never from a constant:
    without the binary (a CI job), or when it cannot be run, it is NOASSERTION."""
    exe = ROOT / "vendor" / "ffmpeg.exe"
    version = ""
    if exe.is_file():
        try:
            version = subprocess.run([str(exe), "-hide_banner", "-version"], capture_output=True,
                                     text=True, timeout=30).stdout  # fmt: skip
        except (OSError, subprocess.SubprocessError):
            version = ""
    licence = ffmpeg_licence(version)
    first = version.splitlines()[0] if version else "vendor\\ffmpeg.exe could not be read here."
    text = ROOT / "vendor" / "ffmpeg-LICENSE.txt"
    texts = [text.read_text(encoding="utf-8", errors="replace")] if text.is_file() else []
    if licence.startswith("LGPL-3"):
        texts += licence_file("gpl-3.0.txt")  # the LGPL v3 incorporates the GPL v3
    return Entry(
        "FFmpeg (BtbN FFmpeg-Builds, LGPL Windows build)", "", licence,
        note=first + "\nffmpeg.exe, run as a separate program to read imported audio and video"
        " files; it is not linked into Upshot, and you may replace it with your own build.",
        texts=texts,
        source="https://ffmpeg.org/download.html and https://github.com/BtbN/FFmpeg-Builds;"
        " AM Consulting will also provide the corresponding source on request at"
        " office@amconsultingai.com.",
        kind="other",
    )  # fmt: skip


def _version(package: str) -> str:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:  # the build machine has it; CI's Linux job not
        return ""


def licence_file(name: str) -> list[str]:
    return [(LICENCES / name).read_text(encoding="utf-8")]


def extra_entries() -> list[Entry]:
    fonts = [
        (ROOT / "frontend" / "src" / "fonts" / name).read_text(encoding="utf-8")
        for name in ("OFL-frankruhllibre.txt", "OFL-ibmplexsanshebrew.txt")
    ]
    tailwind = ROOT / "frontend" / "node_modules" / "tailwindcss" / "LICENSE"
    tailwind_json = tailwind.with_name("package.json")
    tailwind_version = (
        json.loads(tailwind_json.read_text(encoding="utf-8")).get("version", "")
        if tailwind_json.is_file()
        else ""
    )
    nvidia = ROOT / "app" / "asr" / "licenses" / "nvidia_cublas_cu12-12.9.1.4-License.txt"
    return [
        Entry("Python", ".".join(str(n) for n in sys.version_info[:3]), "PSF-2.0",
              "The Python runtime, bundled by PyInstaller, with the libraries it includes"
              " (OpenSSL 3 under Apache 2.0, libffi, bzip2, SQLite and others) and"
              " Microsoft's conditions for its Windows build; see its licence below.",
              python_runtime_licence(), kind="other"),
        Entry("PyInstaller bootloader", _version("pyinstaller"),
              "GPL-2.0-or-later WITH Bootloader-exception",
              "The bootloader that starts upshot.exe. The exception allows it to be used with"
              " software under any licence. https://pyinstaller.org/en/stable/license.html",
              licence_file("pyinstaller.txt"), kind="other"),
        ffmpeg_entry(),
        Entry("ivrit-ai/whisper-large-v3-turbo-ct2", "", "Apache-2.0",
              "Hebrew speech-recognition model by ivrit.ai, downloaded by the installer"
              " unmodified. https://huggingface.co/ivrit-ai/whisper-large-v3-turbo-ct2",
              [_apache()], kind="other"),
        Entry("Systran/faster-whisper-large-v3 and Systran/faster-whisper-small", "", "MIT",
              "OpenAI's Whisper models converted by SYSTRAN, downloaded by the installer"
              " unmodified. https://huggingface.co/Systran", licence_file("whisper.txt"),
              kind="other"),
        Entry("pyannote segmentation 3.0 (ONNX export by k2-fsa)", "", "MIT",
              "Speaker-diarization segmentation model, downloaded by the installer unmodified.",
              licence_file("pyannote-segmentation.txt"), kind="other"),
        Entry("3D-Speaker CAM++ zh-en speaker embedding (Alibaba DAMO, ONNX export by k2-fsa)",
              "", "Apache-2.0",
              "Speaker-embedding model for diarization, downloaded by the installer unmodified.",
              licence_file("3d-speaker.txt"), kind="other"),
        Entry("Silero VAD", "6", "MIT",
              "The voice-activity model inside faster-whisper (silero_vad_v6.onnx).",
              licence_file("silero-vad.txt"), kind="other"),
        Entry("Frank Ruhl Libre and IBM Plex Sans Hebrew fonts", "", "OFL-1.1",
              "Bundled with the interface.", fonts, kind="other"),
        Entry("Tailwind CSS", tailwind_version, "MIT", "Compiled into the interface's stylesheet.",
              [tailwind.read_text(encoding="utf-8")] if tailwind.is_file() else [],
              kind="other"),
        Entry("Intel OpenMP runtime (libiomp5md.dll, shipped inside ctranslate2)", "2025.3",
              "LicenseRef-Intel-EULA-Developer-Tools",
              "Intel's proprietary runtime, redistributed under Intel's terms below.",
              licence_file("intel-openmp.txt")
              + licence_file("intel-openmp-third-party-programs.txt"), kind="other"),
        Entry("NVIDIA cuBLAS (nvidia-cublas-cu12)", "12.9.1.4", "LicenseRef-NVIDIA-CUDA-EULA",
              "Downloaded by the installer from PyPI on machines with a suitable NVIDIA GPU,"
              " for GPU transcription; kept beside the libraries with this licence.",
              [nvidia.read_text(encoding="utf-8")] if nvidia.is_file() else [], kind="other"),
        Entry("Microsoft Visual C++ and Universal C runtime", "", "LicenseRef-Microsoft-Redist",
              "vcruntime140, msvcp140, ucrtbase and api-ms-win-* libraries, redistributed under"
              " the Visual Studio redistribution terms.", ["(Microsoft redistributable files;"
              " no notice is required.)"], kind="other"),
    ]  # fmt: skip


def _apache() -> str:
    """Apache-2.0's text (``packaging/licenses/apache-2.0.txt``, below its header)."""
    return licence_file("apache-2.0.txt")[0].split("\n\n", 1)[1]


# ------------------------------------------------------------------ the files


def entries() -> list[Entry]:
    MISSING.clear()
    js = []
    if (ROOT / "frontend" / "node_modules").is_dir():
        js = [js_entry(folder) for folder in js_packages()]
    return extra_entries() + python_entries() + js


def render(items: list[Entry] | None = None) -> str:
    items = entries() if items is None else items
    out = [
        "Upshot - third-party notices",
        "",
        "Upshot is made by AM Consulting. It includes, or downloads during installation,",
        "the third-party components listed below, each under its own licence. Nothing in",
        "the Upshot Terms of Service or the Upshot source-code licence limits your rights",
        "under these licences.",
        "",
    ]
    headings = {
        "other": "COMPONENTS NOT INSTALLED BY A PACKAGE MANAGER",
        "python": "PYTHON PACKAGES",
        "javascript": "JAVASCRIPT PACKAGES (THE INTERFACE)",
    }
    for kind, heading in headings.items():
        out.extend([RULE, heading, ""])
        for item in (i for i in items if i.kind == kind):
            out.extend([RULE, f"{item.name} {item.version}".rstrip(), f"Licence: {item.licence}"])
            if item.note:
                out.append(item.note)
            if item.source:
                out.append(f"Source: {item.source}")
            for text in item.texts:
                out.extend(["", text.strip()])
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def missing_texts(items: list[Entry]) -> list[str]:
    """What --strict refuses: no licence text, no known licence, or a Python runtime whose
    licence does not carry the libraries its note names."""
    out = [f"{i.name} {i.version}".rstrip() for i in items if not any(t.strip() for t in i.texts)]
    out += [f"{i.name}: licence unknown ({(i.note.splitlines() or ['no note'])[0]})"
            for i in items if i.licence == "NOASSERTION"]  # fmt: skip
    out += [f"{i.name} {i.version}: its licence text lacks {what}" for i in items
            if i.name == "Python" for what, marker in RUNTIME_TEXTS
            if not any(marker in t for t in i.texts)]  # fmt: skip
    return out + MISSING


# ------------------------------------------------------------------ --check


#: Files in the freeze that no package installed, and the EXTRA entry each belongs to.
UNOWNED: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Python", ("python3*.dll", "base_library.zip", "libcrypto-*.dll", "libssl-*.dll",
                "libffi-*.dll", "sqlite3.dll", "*.pyd")),
    ("Microsoft Visual C++ and Universal C runtime", ("vcruntime140*.dll", "msvcp140*.dll",
     "ucrtbase.dll", "api-ms-win-*.dll", "winrt/msvcp140.dll")),
    ("FFmpeg (BtbN FFmpeg-Builds, LGPL Windows build)", ("ffmpeg.exe",)),
    ("Upshot", ("app/*", "frontend/*", "THIRD-PARTY-NOTICES.*")),
)  # fmt: skip


def unowned(key: str) -> str | None:
    """The EXTRA entry a file no package installed belongs to, by ``UNOWNED``. A pattern
    without a "/" matches a top-level file only: fnmatch's "*" also matches "/", and a
    package's unmapped extension deep in a folder must not pass for Python's own."""
    for name, patterns in UNOWNED:
        for pattern in patterns:
            if "/" not in pattern and "/" in key:
                continue
            if fnmatch.fnmatch(key, pattern.lower()):
                return name
    return None


def owners() -> dict[str, str]:
    """Each installed file (lower case, below site-packages) → the distribution's name."""
    found: dict[str, str] = {}
    for dist in metadata.distributions():
        for file in dist.files or ():
            path = str(file).replace("\\", "/").lower()
            found.setdefault(path, dist.metadata["Name"])
            found.setdefault(path.split(".data/data/", 1)[-1], dist.metadata["Name"])
    return found


def check(dist: Path) -> list[str]:
    notices = dist / "_internal" / "THIRD-PARTY-NOTICES.json"
    if not notices.is_file():
        return [f"{notices} is missing"]
    listed = {
        canonicalize_name(e["name"]) for e in json.loads(notices.read_text("utf-8"))["entries"]
    }
    listed.add("upshot")
    owned = owners()
    problems: set[str] = set()
    for root in (dist / "_internal", dist / "mcp" / "_internal"):
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.is_dir():
                continue
            rel = path.relative_to(root).as_posix()
            key = rel.lower()
            owner = owned.get(key) or (owned.get(key[:-1]) if key.endswith(".pyc") else None)
            if owner is None:
                owner = unowned(key)
            if owner is None:
                problems.add(f"{rel}: no package installed it and no notices entry covers it")
            elif canonicalize_name(owner) not in listed:
                problems.add(f"{rel}: from {owner}, which the notices do not list")
    return sorted(problems)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["--check"]:
        if len(args) != 2:
            print("usage: third_party_notices.py --check <dist folder>", file=sys.stderr)
            return 2
        problems = check(Path(args[1]))
        for line in problems:
            print(f"[FAIL] {line}")
        if not problems:
            print(f"[ok  ] {args[1]}: every file is accounted for in the notices")
        return 1 if problems else 0
    strict = "--strict" in args
    paths = [a for a in args if not a.startswith("--")]
    target = Path(paths[0]) if paths else ROOT / "dist" / "THIRD-PARTY-NOTICES.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    items = entries()
    text = render(items)
    target.write_text(text, encoding="utf-8")
    target.with_suffix(".json").write_text(
        json.dumps({"entries": [asdict(i) for i in items]}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(f"{target}: {len(items)} entries")
    missing = missing_texts(items)
    for name in missing:
        print(f"[{'FAIL' if strict else 'warn'}] {name}")
    return 1 if strict and missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
