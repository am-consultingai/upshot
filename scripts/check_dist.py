"""Refuse a build that carries a GPL or Intel component Upshot may not ship.

    uv run python scripts/check_dist.py dist/upshot

Run by ``packaging/build.ps1`` after PyInstaller. Upshot is not under the GPL, so a GPL
library in the install folder is a licence violation, whichever package brought it in.
PyAV did exactly that: its Windows wheel bundles libx264 and libx265, and every build
shipped them until PyAV was removed (``pyproject.toml``). Three checks:

- file names: the known GPL encoders and PyAV's library folder;
- inside every ``.dll``/``.pyd``/``.exe``: FFmpeg's configure line, which an FFmpeg build
  carries whatever its file is called. ``--enable-gpl`` alone is not enough: PyAV's
  FFmpeg links x264 and x265 with ``--enable-version3`` and no ``--enable-gpl`` at all;
- inside the same files: the symbols of GPL libraries a permissively licensed package can
  link in without its metadata saying so (``GPL_CODE``). PyPI's sherpa-onnx compiles eSpeak NG
  (GPL-3.0-or-later) and piper-phonemize into its module, for text-to-speech Upshot never
  uses; Upshot ships its own build without them (``packaging/sherpa-onnx``).

And Intel's runtime libraries (licence epic S12, D100): Upshot ships its own ctranslate2,
built with Microsoft's OpenMP and oneDNN, because PyPI's carries Intel's OpenMP runtime
(``libiomp5md.dll``, under Intel's developer-tools EULA) and oneMKL. A build that picked up
PyPI's wheel again (another Python, a lost source pin) has ``libiomp5md.dll`` and is refused;
ours needs Microsoft's ``vcomp140.dll`` beside it, which must then be in the freeze.

And Microsoft's runtime (licence review H1): every Visual C++ runtime DLL at the top of a
freeze's ``_internal`` must be Visual Studio's redistributable copy pinned in
``packaging/msvc_runtime.py``, not whatever the build machine had on its PATH, and the
Universal C runtime (``ucrtbase.dll``, ``api-ms-win-*``) must not be there at all: Windows 10,
which the installer requires, carries it, so it is refused anywhere. Copies of the Visual C++
runtime a package keeps in its own folder (numpy's, winrt's) are its own redistribution and
are not checked.
"""

from __future__ import annotations

import fnmatch
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packaging"))
import msvc_runtime

#: Intel's runtime libraries, by lower-case file name: OpenMP, and oneMKL's DLLs.
INTEL_NAMES: tuple[str, ...] = ("libiomp5*", "libiompstubs5*", "mkl_*.dll")
#: Lower-case glob patterns, matched against every file and folder name.
FORBIDDEN_NAMES: tuple[str, ...] = (
    "av.libs",  # PyAV's bundled FFmpeg, with x264 and x265
    "*x264*",
    "*x265*",
    "*xvid*",
    "*fdk-aac*",
    "*fdk_aac*",
    "*postproc-*",  # libpostproc is GPL-only
)
#: The external libraries FFmpeg's configure lists as GPL, GPLv3 or non-free
#: (EXTERNAL_LIBRARY_GPL_LIST, _GPLV3_LIST, _NONFREE_LIST, HWACCEL_LIBRARY_NONFREE_LIST),
#: the union over n6.1, n7.1 and n8.1. A build can link one without ever saying
#: --enable-gpl: PyAV's linked x264 and x265 under --enable-version3.
GPL_LIBRARIES = (
    "avisynth", "frei0r", "libcdio", "libdavs2", "libdvdnav", "libdvdread", "librubberband",
    "libvidstab", "libx264", "libx265", "libxavs", "libxavs2", "libxvid",  # GPL
    "libsmbclient",  # GPLv3
    "decklink", "libfdk_aac", "libmpeghdec", "libtls",  # non-free
    "cuda_nvcc", "cuda_sdk", "libnpp",  # non-free hardware acceleration
)  # fmt: skip
#: Those, and the switches themselves (postproc: GPL-only, until FFmpeg 8.0 removed it).
#: configure reads "-" and "_" alike in a name, and the binary keeps whichever was typed.
GPL_FLAGS = re.compile(
    rb"--enable-(gpl|nonfree|postproc|"
    + b"|".join(name.replace("_", "[-_]").encode() for name in GPL_LIBRARIES)
    + rb")\b"
)
#: The API of GPL libraries, as a binary that links one carries it (exported, or in its
#: strings): eSpeak NG and the piper phonemizer built on it (GPL-3.0-or-later), the x264 and
#: x265 encoders (GPL-2.0-or-later), libimagequant (GPL-3.0-or-later). Whole names only:
#: OpenBLAS has ``scipy_ctgex264_`` and hf_xet ``peak_used_bytes``. ``phonemize_eSpeak`` may
#: follow a digit: GCC's mangled names put each name's length first (``_ZN5piper16phonemize…``);
#: MSVC's write the namespace after the name (``?phonemize_eSpeak@piper@@``).
GPL_CODE = re.compile(
    rb"(?<![A-Za-z0-9_])("
    rb"espeak_(?:ng_)?[A-Za-z]+|espeak-ng-data|piper::"
    rb"|x264_(?:encoder|param|picture)_[a-z_]+|x265_(?:encoder|param|picture|api)_[a-z_]+"
    rb"|liq_(?:attr|image|result|set|get|write|histogram)_[a-z_]+"
    rb")|(?<![A-Za-z_])phonemize_eSpeak|@piper@@"
)
BINARY_SUFFIXES = {".dll", ".pyd", ".exe"}


def problems(root: Path, runtime: dict[str, str] | None = None) -> list[str]:
    """What the freeze in ``root`` must not ship. ``runtime``: the Visual C++ runtime's
    files by name and SHA-256 (``msvc_runtime.FILES``, which tests replace)."""
    runtime = msvc_runtime.FILES if runtime is None else runtime
    found = []
    names = set()
    for path in sorted(root.rglob("*")):
        name = path.name.lower()
        names.add(name)
        # The top of a freeze's _internal, or beside an executable: not a package's own folder.
        ours = path.parent.name.lower() == "_internal" or path.parent in (root, root / "mcp")
        if path.is_file() and msvc_runtime.UCRT_NAME.match(name):
            found.append(
                f"{path.relative_to(root).as_posix()}: the Universal C runtime, which"
                " Windows 10 carries; the freeze must not"
            )
        elif path.is_file() and ours and msvc_runtime.RUNTIME_NAME.match(name):
            if runtime.get(name) != msvc_runtime.sha256(path):
                found.append(
                    f"{path.relative_to(root).as_posix()}: not Visual Studio's"
                    f" redistributable copy ({msvc_runtime.PACKAGE})"
                )
        elif any(fnmatch.fnmatch(name, pattern) for pattern in FORBIDDEN_NAMES):
            found.append(f"{path.relative_to(root).as_posix()}: a GPL component")
        elif any(fnmatch.fnmatch(name, pattern) for pattern in INTEL_NAMES):
            found.append(f"{path.relative_to(root).as_posix()}: an Intel runtime library")
        elif path.is_file() and path.suffix.lower() in BINARY_SUFFIXES:
            flag = gpl_flag(path)
            code = None if flag else gpl_code(path)
            if flag:
                found.append(f"{path.relative_to(root).as_posix()}: built with {flag}")
            elif code:
                found.append(f"{path.relative_to(root).as_posix()}: contains GPL code ({code})")
    if "ctranslate2.dll" in names and "vcomp140.dll" not in names:
        found.append("ctranslate2.dll needs Microsoft's vcomp140.dll, which the freeze lacks")
    return found


def gpl_flag(path: Path, chunk: int = 16 << 20) -> str | None:
    """The first GPL configure flag inside ``path``, read in chunks, not whole."""
    return _first(GPL_FLAGS, path, chunk)


def gpl_code(path: Path, chunk: int = 16 << 20) -> str | None:
    """The first GPL library symbol inside ``path`` (``GPL_CODE``), read in chunks."""
    return _first(GPL_CODE, path, chunk)


def _first(pattern: re.Pattern[bytes], path: Path, chunk: int) -> str | None:
    overlap = 64  # longer than any flag or symbol, so one split across two chunks is seen
    chunk = max(chunk, overlap + 1)  # a smaller one would leave the first bytes unsearched
    tail = b""
    with path.open("rb") as handle:
        block = handle.read(chunk)
        while block:
            following = handle.read(chunk)
            data = tail + block
            # The tail keeps one byte more than the overlap, searched already, so that a
            # lookbehind at the overlap's start sees the byte before it.
            match = pattern.search(data, 1 if tail else 0)
            # A match that starts in the last ``overlap`` bytes may go on in the next block:
            # the next pass sees it whole, from the overlap.
            if match and (match.start() < len(data) - overlap or not following):
                return match.group(0).decode(errors="replace")
            tail = data[-(overlap + 1) :]
            block = following
    return None


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or not Path(args[0]).is_dir():
        print("usage: check_dist.py <dist folder>", file=sys.stderr)
        return 2
    found = problems(Path(args[0]))
    for line in found:
        print(f"[FAIL] {line}")
    if not found:
        print(
            f"[ok  ] {args[0]}: no GPL or Intel component; Microsoft's runtime is"
            f" {msvc_runtime.PACKAGE}'s"
        )
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
