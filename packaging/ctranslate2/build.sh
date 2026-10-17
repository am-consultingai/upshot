#!/usr/bin/env bash
# Build Upshot's ctranslate2 wheel for Windows without any Intel component (licence epic, S12).
#
#   packaging/ctranslate2/build.sh <work folder>
#
# Run by .github/workflows/ctranslate2.yml in Git Bash on a windows-2022 runner (MSVC 2022).
# It is upstream's own recipe (python/tools/prepare_build_environment_windows.sh at v4.8.1)
# with three changes, so the wheel carries nothing under Intel's licences:
#   - WITH_MKL=OFF: no Intel oneMKL (upstream refuses MKL without Intel's OpenMP);
#   - OPENMP_RUNTIME=COMP: Microsoft's vcomp140.dll instead of Intel's libiomp5md.dll;
#   - no cudnn64_9.dll copied in (built with WITH_CUDNN=OFF, as upstream, and never loaded).
# oneDNN 3.1.1, built from source with MSVC and linked statically as upstream does, then does
# the CPU's float32 and int8 GEMM, as it already does on AMD CPUs in the official wheel.
# Measured against the official wheel (internal research, 2026-10-16): GPU transcripts word for
# word identical; CPU transcripts those of the official wheel with CT2_USE_MKL=0; CPU speed the
# same at equal thread counts.
#
# Every input is pinned: ctranslate2 by its release commit (its submodules by the commits that
# commit records), oneDNN, the CUDA toolkit installer and NVIDIA's licence archive by SHA-256,
# the Python build tools by version. The result goes to <work>/dist: the wheel; the licence texts
# it needs (ctranslate2; oneDNN and the third-party code in it; the code compiled in from
# ctranslate2's third_party folder and pybind11; NVIDIA's CUDA licence for the CUDA runtime
# linked into ctranslate2.dll); BUILD.txt; imports.txt; the sources it was built from
# (ctranslate2-sources.zip); and SHA256SUMS over all of them.
set -euo pipefail

# Windows-style paths (D:/...): cmake and MSVC are native programs.
WORK="$(cygpath -m "$(cd "${1:?usage: build.sh <work folder>}" && pwd)")"
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${PYTHON:-python}"

CT2_VERSION=4.8.1
CT2_COMMIT=0d8bcd362ac75ef860ef161d6f0efad0ae439ff0
ONEDNN_VERSION=3.1.1
ONEDNN_SHA256=d1cd58f57b2308d362a9bc474acbc21524cb879cf5d8779b460c718f0fac82d6
CUDA_URL=https://developer.download.nvidia.com/compute/cuda/12.8.1/local_installers/cuda_12.8.1_572.61_windows.exe
CUDA_SHA256=19392bbffd0ad4ee7cb295a181e87f682187f17653679c1c548c263b7e1cd9a6
CUDA_ROOT="C:/Program Files/NVIDIA GPU Computing Toolkit/CUDA/v12.8"
# NVIDIA's CUDA licence, from the CUDA runtime's redistributable archive of this toolkit (the
# selective install above does not necessarily write EULA.txt).
CUDART_URL=https://developer.download.nvidia.com/compute/cuda/redist/cuda_cudart/windows-x86_64/cuda_cudart-windows-x86_64-12.8.90-archive.zip
CUDART_SHA256=4a39058fd8519444a81cfc7ae055d136f48d1a31ffa41ae255b35b2edd61e13b
PYBIND11_VERSION=2.11.1
SETUPTOOLS_VERSION=84.0.0
WHEEL_VERSION=0.48.0
PEFILE_VERSION=2024.8.26
NPROC=$(nproc)

rm -rf "$WORK/dist"
mkdir -p "$WORK/src" "$WORK/dist/sources"
cd "$WORK/src"
DIST="$WORK/dist"

# The CUDA toolkit: only the parts the build needs (as upstream).
if [[ ! -d "$CUDA_ROOT/bin" ]]; then
  curl -fsSL -o cuda.exe "$CUDA_URL"
  echo "$CUDA_SHA256  cuda.exe" | sha256sum -c - >/dev/null || { echo "cuda.exe: SHA-256 mismatch" >&2; exit 1; }
  ./cuda.exe -s nvcc_12.8 cudart_12.8 cublas_dev_12.8 curand_dev_12.8
  rm cuda.exe
fi

# oneDNN, static, MSVC (its OpenMP runtime is then Microsoft's too), trimmed as upstream does.
ONEDNN_PREFIX="$WORK/onednn"
curl -fsSL -o "onednn-$ONEDNN_VERSION.tar.gz" \
  "https://github.com/oneapi-src/oneDNN/archive/refs/tags/v$ONEDNN_VERSION.tar.gz"
echo "$ONEDNN_SHA256  onednn-$ONEDNN_VERSION.tar.gz" | sha256sum -c - >/dev/null \
  || { echo "oneDNN: SHA-256 mismatch" >&2; exit 1; }
rm -rf "oneDNN-$ONEDNN_VERSION" onednn-build && tar xzf "onednn-$ONEDNN_VERSION.tar.gz"
cmake -S "oneDNN-$ONEDNN_VERSION" -B onednn-build \
  -DCMAKE_INSTALL_PREFIX="$ONEDNN_PREFIX" -DONEDNN_LIBRARY_TYPE=STATIC \
  -DONEDNN_BUILD_EXAMPLES=OFF -DONEDNN_BUILD_TESTS=OFF -DONEDNN_ENABLE_WORKLOAD=INFERENCE \
  -DONEDNN_ENABLE_PRIMITIVE="CONVOLUTION;REORDER" -DONEDNN_BUILD_GRAPH=OFF -DONEDNN_CPU_RUNTIME=OMP
cmake --build onednn-build --config Release --target install --parallel "$NPROC"

# ctranslate2 at its release commit, with its submodules.
rm -rf CTranslate2
git clone -q --branch "v$CT2_VERSION" --recurse-submodules --shallow-submodules \
  https://github.com/OpenNMT/CTranslate2.git
[[ "$(git -C CTranslate2 rev-parse HEAD)" == "$CT2_COMMIT" ]] \
  || { echo "ctranslate2 v$CT2_VERSION is not commit $CT2_COMMIT" >&2; exit 1; }
# The sources as cloned, before anything builds in the tree. Relative paths only: GNU tar reads
# "D:/..." as a remote host.
git -C CTranslate2 submodule status --recursive > "$DIST/sources/submodules.txt"
tar czf "../dist/sources/ctranslate2-$CT2_VERSION-with-submodules.tar.gz" --exclude=.git CTranslate2

CT2_ROOT="$WORK/install"
CMAKE_ARGS=(
  -DCMAKE_POLICY_VERSION_MINIMUM=3.5 -DCMAKE_INSTALL_PREFIX="$CT2_ROOT"
  -DCMAKE_PREFIX_PATH="$ONEDNN_PREFIX" -DBUILD_CLI=OFF
  -DWITH_MKL=OFF -DWITH_DNNL=ON -DOPENMP_RUNTIME=COMP
  -DWITH_CUDA=ON -DWITH_CUDNN=OFF -DCUDA_TOOLKIT_ROOT_DIR="$CUDA_ROOT" -DCUDA_DYNAMIC_LOADING=ON
  -DCUDA_NVCC_FLAGS="-Xfatbin=-compress-all" -DCUDA_ARCH_LIST="Common"
)
cmake -S CTranslate2 -B ct2-build "${CMAKE_ARGS[@]}"
cmake --build ct2-build --config Release --target install --parallel "$NPROC"

# The wheel.
(
  cd CTranslate2
  cp README.md python/
  cp "$CT2_ROOT/bin/ctranslate2.dll" python/ctranslate2/
  "$PY" -m pip install -q "pybind11==$PYBIND11_VERSION" "setuptools==$SETUPTOOLS_VERSION" \
    "wheel==$WHEEL_VERSION"
  CTRANSLATE2_ROOT="$CT2_ROOT" "$PY" -m pip wheel -q --no-deps --no-build-isolation -w "$WORK/dist" ./python
)
WHEEL="$(ls "$WORK"/dist/ctranslate2-*.whl)"

# Nothing of Intel's, and no cuDNN: what the DLL imports, what it contains, what the wheel holds.
"$PY" -m pip install -q "pefile==$PEFILE_VERSION"
"$PY" -c 'import pefile, sys; [print(e.dll.decode()) for e in pefile.PE(sys.argv[1]).DIRECTORY_ENTRY_IMPORT]' \
  "$CT2_ROOT/bin/ctranslate2.dll" > "$WORK/dist/imports.txt"
grep -qx "VCOMP140.DLL" "$WORK/dist/imports.txt" || { echo "does not use Microsoft's OpenMP" >&2; exit 1; }
! grep -qiE "libiomp5|^mkl_" "$WORK/dist/imports.txt" || { echo "imports Intel's OpenMP or MKL" >&2; exit 1; }
! grep -aqi "mkl_blas" "$CT2_ROOT/bin/ctranslate2.dll" || { echo "contains Intel oneMKL" >&2; exit 1; }
"$PY" -c '
import sys, zipfile
names = zipfile.ZipFile(sys.argv[1]).namelist()
bad = [n for n in names if n.lower().endswith(".dll") and n.lower().split("/")[-1] != "ctranslate2.dll"]
sys.exit(f"unexpected DLLs in the wheel: {bad}" if bad else 0)' "$WHEEL"

# Licence texts, the sources, and what was built.
cp CTranslate2/LICENSE "$DIST/LICENSE-ctranslate2.txt"
cat "oneDNN-$ONEDNN_VERSION/LICENSE" <(echo) "oneDNN-$ONEDNN_VERSION/THIRD-PARTY-PROGRAMS" > "$DIST/LICENSE-oneDNN.txt"
curl -fsSL -o cudart.zip "$CUDART_URL"
echo "$CUDART_SHA256  cudart.zip" | sha256sum -c - >/dev/null || { echo "cudart.zip: SHA-256 mismatch" >&2; exit 1; }
"$PY" -c 'import sys, zipfile; sys.stdout.buffer.write(zipfile.ZipFile(sys.argv[1]).read(sys.argv[2]))' \
  cudart.zip cuda_cudart-windows-x86_64-12.8.90-archive/LICENSE > "$DIST/LICENSE-NVIDIA-CUDA.txt"
# What ctranslate2 compiles in from its third_party folder in this configuration (spdlog,
# cpu_features; with CUDA, cutlass and thrust/cub/libcudacxx; the AVX maths and thread-pool
# headers), and pybind11's headers in _ext.pyd. cxxopts, googletest and ruy are not built.
header() { echo "== $1 (licence notice from the file's header)"; awk '{print} /\*\//{exit}' "$2"; echo; }
{
  for dir in spdlog cpu_features cutlass thrust; do
    file="$(ls CTranslate2/third_party/$dir/LICENSE* | head -1)"
    echo "== $dir ($(basename "$file"))"; cat "$file"; echo
  done
  header avx_mathfun.h CTranslate2/third_party/avx_mathfun.h
  header avx512_mathfun.h CTranslate2/third_party/avx512_mathfun.h
  header BS_thread_pool.hpp CTranslate2/third_party/BS_thread_pool.hpp
  echo "== pybind11 $PYBIND11_VERSION (LICENSE)"
  "$PY" -c 'from importlib.metadata import distribution as d; f = [p for p in d("pybind11").files if p.name.startswith("LICENSE")]; print(f[0].read_text())'
} > "$DIST/LICENSE-third-party.txt"
cp "onednn-$ONEDNN_VERSION.tar.gz" "$DIST/sources/"
cp "$HERE/build.sh" "$DIST/sources/"
printf '%s\n' "${CMAKE_ARGS[@]}" > "$DIST/sources/cmake-args.txt"
"$PY" -m pip freeze > "$DIST/sources/python-build-tools.txt"
(cd "$DIST" && "$PY" -c 'import shutil; shutil.make_archive("ctranslate2-sources", "zip", "sources")')
{
  echo "ctranslate2 $CT2_VERSION (commit $CT2_COMMIT), Windows x64 wheel, for Upshot (built by packaging/ctranslate2/build.sh)."
  echo "No Intel component: Microsoft's OpenMP (VCOMP140.DLL) instead of Intel's; oneDNN $ONEDNN_VERSION (static) instead of oneMKL; no cuDNN."
  echo "CUDA 12.8 (cuBLAS loaded at run time; the CUDA runtime is linked in statically)."
  echo "Licences: MIT (ctranslate2); Apache-2.0 with its third-party programs (oneDNN); the code compiled in from"
  echo "ctranslate2's third_party folder and pybind11 (LICENSE-third-party.txt); NVIDIA's CUDA licence (CUDA runtime)."
  echo "Upshot commit: ${GITHUB_SHA:-unknown}."
  echo "ctranslate2-sources.zip holds everything it was built from: ctranslate2 with its submodules"
  echo "(submodules.txt has their commits), oneDNN, build.sh, the exact CMake arguments and the Python build tools."
} > "$DIST/BUILD.txt"
(cd "$DIST" && find . -maxdepth 1 -type f ! -name SHA256SUMS -printf '%P\n' | sort | xargs sha256sum > SHA256SUMS)
cat "$DIST/SHA256SUMS"
echo "built: $WHEEL"
