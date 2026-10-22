#!/usr/bin/env bash
# Build Upshot's sherpa-onnx wheel for Windows without text-to-speech (licence epic, ClickUp
# z8tj1hpjbu).
#
#   packaging/sherpa-onnx/build.sh <work folder>
#
# Run by .github/workflows/sherpa-onnx.yml in Git Bash on a windows-2022 runner (MSVC 2022).
# PyPI's sherpa-onnx is built with text-to-speech, which links eSpeak NG (GPL-3.0-or-later) and
# piper-phonemize into _sherpa_onnx.pyd, the module Upshot imports for speaker diarization.
# Upshot uses only diarization. This is upstream's own recipe for that wheel
# (.github/workflows/build-wheels-win-x64.yaml at v1.13.6, job build_wheels_win64) with
# SHERPA_ONNX_ENABLE_TTS=OFF, so neither is fetched or built, and four more changes:
#   - tts-off-names.patch: the module defines two more text-to-speech names as None, as it
#     already does the others without text-to-speech, since sherpa_onnx/__init__.py imports
#     them (at v1.13.6 the import fails without);
#   - no portaudio and no pre-installed onnxruntime: neither is used by the module, and the
#     build then depends only on what it downloads itself;
#   - onnxruntime.dll goes into the wheel beside the module, and the wheel no longer requires
#     sherpa-onnx-core. That package only carries onnxruntime.dll (byte for byte the same file)
#     and the C and C++ API libraries, which are built with text-to-speech and which the module
#     never loads;
#   - the module may import only the Python runtime, onnxruntime.dll and Windows itself.
#
# Every input is pinned: sherpa-onnx by its release commit; what its CMake downloads
# (onnxruntime, kaldi-native-fbank, kaldi-decoder, OpenFst, ...) by the SHA-256 that commit
# records; the Python build tools by version. The result goes to <work>/dist: the wheel; the
# licence texts it needs (sherpa-onnx; onnxruntime and its third-party notices; the code compiled
# in from what the build downloads); BUILD.txt; imports.txt; the sources it was built from
# (sherpa-onnx-sources.zip); and SHA256SUMS over all of them.
set -euo pipefail

# Windows-style paths (D:/...): cmake and MSVC are native programs.
WORK="$(cygpath -m "$(cd "${1:?usage: build.sh <work folder>}" && pwd)")"
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${PYTHON:-python}"

SHERPA_VERSION=1.13.6
SHERPA_COMMIT=1cb484af5e69d3c7803c1eb0b3b5ab8041e0e911
# onnxruntime.dll as sherpa-onnx 1.13.6 downloads it (onnxruntime 1.27.1, static CRT), the file
# PyPI's sherpa-onnx-core 1.13.6 carries too.
ONNXRUNTIME_DLL_SHA256=b9f6713c3602a4742680a7e6a77e3f9ac4a676ad9447bce609e53efcda795d7e
# PyPI's wheel, only to prove the eSpeak check below finds what it is meant to find.
PYPI_WHEEL_URL=https://files.pythonhosted.org/packages/cd/04/a5cd9dd2a6e5073ca5fb8df28a95ecb49df71a8b3ff822aa57f294dec985/sherpa_onnx-1.13.6-cp313-cp313-win_amd64.whl
PYPI_WHEEL_SHA256=3387c754668578612aaf7795f894b631faefb71fd7d7df20c2aa41aa2dbc59a2
CMAKE_VERSION=3.31.6
SETUPTOOLS_VERSION=84.0.0
WHEEL_VERSION=0.48.0
PEFILE_VERSION=2024.8.26

rm -rf "$WORK/dist"
mkdir -p "$WORK/src" "$WORK/dist/sources/downloads"
cd "$WORK/src"
DIST="$WORK/dist"

fetch() {  # url sha256 file
  curl -fsSL -o "$3" "$1"
  echo "$2  $3" | sha256sum -c - >/dev/null || { echo "$3: SHA-256 mismatch" >&2; exit 1; }
}
fail() { echo "$1" >&2; exit 1; }

"$PY" -m pip install -q "cmake==$CMAKE_VERSION" "setuptools==$SETUPTOOLS_VERSION" \
  "wheel==$WHEEL_VERSION" "pefile==$PEFILE_VERSION"
# The pinned CMake, not the runner's.
CMAKE_BIN="$(dirname "$(cygpath -u "$("$PY" -c 'import cmake, os; print(os.path.join(cmake.CMAKE_BIN_DIR, "cmake.exe"))')")")"
export PATH="$CMAKE_BIN:$PATH"
[[ "$(cmake --version | head -1)" == "cmake version $CMAKE_VERSION" ]] || fail "not CMake $CMAKE_VERSION"

# sherpa-onnx at its release commit. The sources as cloned, before anything builds in the tree.
# Relative paths only: GNU tar reads "D:/..." as a remote host.
rm -rf sherpa-onnx
git clone -q --depth 1 --branch "v$SHERPA_VERSION" https://github.com/k2-fsa/sherpa-onnx.git
[[ "$(git -C sherpa-onnx rev-parse HEAD)" == "$SHERPA_COMMIT" ]] \
  || fail "sherpa-onnx v$SHERPA_VERSION is not commit $SHERPA_COMMIT"
tar czf "../dist/sources/sherpa-onnx-$SHERPA_VERSION.tar.gz" --exclude=.git sherpa-onnx
git -C sherpa-onnx apply "$HERE/tts-off-names.patch"

# The wheel, as upstream builds it: only the Python module, linked against onnxruntime.dll.
CMAKE_ARGS=(
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5
  -DSHERPA_ONNX_ENABLE_TTS=OFF
  -DSHERPA_ONNX_ENABLE_BINARY=OFF -DSHERPA_ONNX_BUILD_C_API_EXAMPLES=OFF
  -DSHERPA_ONNX_ENABLE_C_API=OFF -DSHERPA_ONNX_ENABLE_WEBSOCKET=OFF
  -DSHERPA_ONNX_ENABLE_PORTAUDIO=OFF -DSHERPA_ONNX_USE_PRE_INSTALLED_ONNXRUNTIME_IF_AVAILABLE=OFF
)
(
  cd sherpa-onnx
  export SHERPA_ONNX_SPLIT_PYTHON_PACKAGE=ON SHERPA_ONNX_CMAKE_ARGS="${CMAKE_ARGS[*]}"
  "$PY" setup.py -q bdist_wheel
)
# setup.py's CMake tree (build/temp.win-amd64-cpython-313/Release).
BUILD="$(find sherpa-onnx/build -maxdepth 3 -name CMakeCache.txt -printf '%h\n')"
[[ -n "$BUILD" && "$(echo "$BUILD" | wc -l)" -eq 1 ]] || fail "no single CMake tree: $BUILD"
grep -qx "SHERPA_ONNX_ENABLE_TTS:BOOL=OFF" "$BUILD/CMakeCache.txt" || fail "TTS is not off"

# What the build downloaded: none of it eSpeak or piper; the archives go into the sources.
DOWNLOADS="$(find "$BUILD/_deps" -maxdepth 4 -path '*-populate-prefix/src/*' -type f \
  \( -name '*.tar.gz' -o -name '*.tar.bz2' -o -name '*.tgz' -o -name '*.zip' \))"
[[ -n "$DOWNLOADS" ]] || fail "found none of the build's downloads"
! ls -d "$BUILD"/_deps/* | grep -qiE "espeak|piper" || fail "the build fetched eSpeak or piper"
echo "$DOWNLOADS" | xargs -I{} cp {} "$DIST/sources/downloads/"
ls "$BUILD/_deps" | grep -- '-src$' | sed 's/-src$//' > "$DIST/sources/downloads.txt"

# onnxruntime.dll beside the module, and no sherpa-onnx-core.
ORT_DLL="$BUILD/_deps/onnxruntime-src/lib/onnxruntime.dll"
echo "$ONNXRUNTIME_DLL_SHA256  $ORT_DLL" | sha256sum -c - >/dev/null \
  || fail "onnxruntime.dll is not the expected file"
UPSTREAM_WHEEL="$(ls sherpa-onnx/dist/sherpa_onnx-*.whl)"
rm -rf unpacked && "$PY" -m wheel unpack -d unpacked "$UPSTREAM_WHEEL"
PKG="$(ls -d unpacked/*)"
cp "$ORT_DLL" "$PKG/sherpa_onnx/lib/"
"$PY" -c '
import sys
path = sys.argv[1]
lines = open(path, encoding="utf-8").read().splitlines(keepends=True)
kept = [line for line in lines if not line.lower().startswith("requires-dist: sherpa-onnx-core")]
if len(kept) != len(lines) - 1:
    sys.exit("METADATA does not require sherpa-onnx-core exactly once")
open(path, "w", encoding="utf-8", newline="").write("".join(kept))' \
  "$(ls "$PKG"/*.dist-info/METADATA)"
"$PY" -m wheel pack -d "$DIST" "$PKG"
WHEEL="$(ls "$DIST"/sherpa_onnx-*.whl)"

# No text-to-speech: what the module imports, what it contains, what the wheel holds.
rm -rf pypi && mkdir pypi && fetch "$PYPI_WHEEL_URL" "$PYPI_WHEEL_SHA256" pypi/pypi.whl
rm -rf ours && mkdir ours && (cd ours && "$PY" -m zipfile -e "$WHEEL" .)
(cd pypi && "$PY" -m zipfile -e pypi.whl .)
PYD="$(ls ours/sherpa_onnx/lib/_sherpa_onnx.*.pyd)"
# LF line endings, written by Python itself: a redirected print() on Windows writes CRLF, which
# grep -x would not match.
"$PY" -c '
import pefile, sys
names = [entry.dll.decode() for entry in pefile.PE(sys.argv[1]).DIRECTORY_ENTRY_IMPORT]
open(sys.argv[2], "w", newline="\n").write("".join(name + "\n" for name in names))' \
  "$PYD" "$DIST/imports.txt"
bad_imports="$(grep -viE '^(python3[0-9]*\.dll|onnxruntime\.dll|kernel32\.dll|advapi32\.dll|api-ms-win-.*\.dll)$' "$DIST/imports.txt" || true)"
[[ -z "$bad_imports" ]] || fail "the module imports more than it should: $bad_imports"
# eSpeak NG's and piper's names, whole (a plain "espeak" is also in "OfflineSpeaker"). The same
# test on PyPI's module must find them, or it proves nothing.
tts_code() {  # file: prints the eSpeak and piper names in it
  "$PY" -c '
import re, sys
names = re.findall(
    rb"(?<![A-Za-z0-9_])(espeak_(?:ng_)?[A-Za-z]+|espeak-ng-data|phonemize_eSpeak|piper::)",
    open(sys.argv[1], "rb").read())
print(" ".join(sorted({n.decode() for n in names})))' "$1"
}
[[ -n "$(tts_code pypi/sherpa_onnx/lib/_sherpa_onnx.*.pyd)" ]] || fail "the eSpeak test is blind"
for file in ours/sherpa_onnx/lib/*; do
  found="$(tts_code "$file")"
  [[ -z "$found" ]] || fail "$file contains eSpeak or piper code: $found"
done
"$PY" -c '
import sys, zipfile
names = [n.lower() for n in zipfile.ZipFile(sys.argv[1]).namelist()]
binaries = sorted(n.split("/")[-1] for n in names if n.endswith((".dll", ".pyd", ".exe", ".lib")))
expected = ["_sherpa_onnx.cp313-win_amd64.pyd", "onnxruntime.dll"]
sys.exit(f"unexpected binaries in the wheel: {binaries}" if binaries != expected else 0)' "$WHEEL"

# It imports, and does what Upshot asks of it, in a clean environment.
rm -rf venv && "$PY" -m venv venv
VENV_PY="venv/Scripts/python.exe"
"$VENV_PY" -m pip install -q --no-deps "$WHEEL" "numpy==2.5.2"
"$VENV_PY" -c '
import numpy as np, sherpa_onnx
assert sherpa_onnx.OfflineTts is None, "text-to-speech is built in"
clustering = sherpa_onnx.FastClustering(sherpa_onnx.FastClusteringConfig(num_clusters=2))
# Cosine distance, as for speaker embeddings: two directions, two clusters.
points = np.array([[1.0, 0.0], [0.99, 0.1], [0.0, 1.0], [0.1, 0.99]], dtype=np.float32)
labels = clustering(points)
assert labels[0] == labels[1] != labels[2] == labels[3], labels
print("sherpa_onnx", sherpa_onnx.__file__, "clusters", labels)'

# Licence texts, the sources, and what was built.
cp sherpa-onnx/LICENSE "$DIST/LICENSE-sherpa-onnx.txt"
ORT_SRC="$BUILD/_deps/onnxruntime-src"
cat "$ORT_SRC/LICENSE" <(echo) "$ORT_SRC/ThirdPartyNotices.txt" > "$DIST/LICENSE-onnxruntime.txt"
# What the build downloads and compiles into the module (onnxruntime aside, linked as a DLL):
# each project's own licence files, as downloaded.
{
  for src in "$BUILD"/_deps/*-src; do
    name="$(basename "$src" -src)"
    [[ "$name" == onnxruntime ]] && continue
    for file in $(ls "$src" | grep -iE '^(licen[cs]e|copying|notice)' | sort); do
      [[ -f "$src/$file" ]] || continue
      echo "== $name ($file)"; cat "$src/$file"; echo
    done
  done
  # Two headers simple-sentencepiece takes from other projects, whose notice is the file's own
  # leading comment: darts-clone (BSD-2-Clause) and ThreadPool (Zlib).
  for file in darts.h threadpool.h; do
    echo "== simple-sentencepiece: $file (notice from the file's header)"
    awk '{print} /\*\//{exit}' "$BUILD/_deps/simple-sentencepiece-src/ssentencepiece/csrc/$file"
    echo
  done
} > "$DIST/LICENSE-third-party.txt"
cp "$HERE/build.sh" "$HERE/tts-off-names.patch" "$DIST/sources/"
printf '%s\n' "${CMAKE_ARGS[@]}" > "$DIST/sources/cmake-args.txt"
"$PY" -m pip freeze > "$DIST/sources/python-build-tools.txt"
(cd "$DIST" \
  && "$PY" -c 'import shutil; shutil.make_archive("sherpa-onnx-sources", "zip", "sources")')
cat > "$DIST/BUILD.txt" <<EOF
sherpa-onnx $SHERPA_VERSION (commit $SHERPA_COMMIT), Windows x64 wheel for Python 3.13, for Upshot
(built by packaging/sherpa-onnx/build.sh).
Without text-to-speech (SHERPA_ONNX_ENABLE_TTS=OFF): no eSpeak NG and no piper-phonemize are
fetched, built or linked; tts-off-names.patch lets the module import without them.
onnxruntime.dll (onnxruntime 1.27.1, as sherpa-onnx downloads it) is in the wheel beside the
module, which then needs no sherpa-onnx-core.
Licences: Apache-2.0 (sherpa-onnx); MIT with its third-party notices (onnxruntime); the code compiled
in from what the build downloads (LICENSE-third-party.txt; downloads.txt names each project).
Upshot commit: ${GITHUB_SHA:-unknown}.
sherpa-onnx-sources.zip holds everything it was built from: sherpa-onnx, every archive its CMake
downloaded (downloads/), build.sh, the patch, the exact CMake arguments and the Python build
tools.
EOF
cp "$DIST/sources/downloads.txt" "$DIST/downloads.txt"
(cd "$DIST" && find . -maxdepth 1 -type f ! -name SHA256SUMS -printf '%P\n' | sort \
  | xargs sha256sum > SHA256SUMS)
cat "$DIST/SHA256SUMS"
echo "built: $WHEEL"
