#!/usr/bin/env bash
# Build Upshot's ffmpeg.exe: FFmpeg's own audio decoders and nothing else (licence epic, S3).
#
#   packaging/ffmpeg/build.sh <work folder>
#
# Run by .github/workflows/ffmpeg.yml on Linux; it cross-compiles for Windows. Upshot only
# ever converts an imported file to 16 kHz mono WAV (app/audio/ingest.py), so the build
# has FFmpeg's built-in audio decoders, the demuxers ingest whitelists, the WAV muxer and
# zlib (QuickTime's compressed headers), and no external codec library at all: one licence
# to honour (LGPL-2.1-or-later) instead of some sixty. Measured against the BtbN build it
# replaces on 175 real files: the same files convert, every sample within 1 LSB.
#
# Every input is pinned by SHA-256, FFmpeg's tarball also by its release signature. The
# result goes to <work>/dist: ffmpeg.exe, its licence texts, BUILD.txt, SHA256SUMS, and
# the sources it was built from (the LGPL's corresponding source).
set -euo pipefail

WORK="$(realpath "${1:?usage: build.sh <work folder>}")"
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"

FFMPEG_VERSION=8.1.3
FFMPEG_URL="https://ffmpeg.org/releases/ffmpeg-$FFMPEG_VERSION.tar.xz"
FFMPEG_SHA256=7138d28c96d9d3e3af4ee3d8cad72741f8ffb40da90c1112235dea3ecd3178a3
# FFmpeg's release signing key (https://ffmpeg.org/download.html#releases).
FFMPEG_KEY=FCF986EA15E6E293A5644F10B4322F04D67658D8
ZLIB_VERSION=1.3.1
ZLIB_URL="https://github.com/madler/zlib/releases/download/v$ZLIB_VERSION/zlib-$ZLIB_VERSION.tar.gz"
ZLIB_SHA256=9a93b2b7dfdac77ceba5a558a580e74667dd6fede4585b91eefb60f03b72df23
TOOLCHAIN_VERSION=20261006
TOOLCHAIN="llvm-mingw-$TOOLCHAIN_VERSION-ucrt-ubuntu-22.04-x86_64"
TOOLCHAIN_URL="https://github.com/mstorsjo/llvm-mingw/releases/download/$TOOLCHAIN_VERSION/$TOOLCHAIN.tar.xz"
TOOLCHAIN_SHA256=5f9c6ed95b2d4bdb2869a488c5fd5857fbdabcf288a0aa3eb1da43f6a08d8ab4

mkdir -p "$WORK/src" "$WORK/dist/sources"
cd "$WORK/src"

fetch() {  # url sha256
  local name; name="$(basename "$1")"
  [[ -f "$name" ]] || curl -fsSL -o "$name" "$1"
  echo "$2  $name" | sha256sum -c - >/dev/null || { echo "$name: SHA-256 mismatch" >&2; exit 1; }
}
fetch "$FFMPEG_URL" "$FFMPEG_SHA256"
curl -fsSL -o "ffmpeg-$FFMPEG_VERSION.tar.xz.asc" "$FFMPEG_URL.asc"
curl -fsSL -o ffmpeg-devel.asc https://ffmpeg.org/ffmpeg-devel.asc
export GNUPGHOME="$WORK/gnupg"
mkdir -p "$GNUPGHOME" && chmod 700 "$GNUPGHOME"
gpg -q --import ffmpeg-devel.asc
gpg --status-fd 1 --verify "ffmpeg-$FFMPEG_VERSION.tar.xz.asc" "ffmpeg-$FFMPEG_VERSION.tar.xz" \
  | grep -q "VALIDSIG $FFMPEG_KEY" || { echo "FFmpeg's signature does not verify" >&2; exit 1; }
fetch "$ZLIB_URL" "$ZLIB_SHA256"
fetch "$TOOLCHAIN_URL" "$TOOLCHAIN_SHA256"

rm -rf "ffmpeg-$FFMPEG_VERSION" "zlib-$ZLIB_VERSION" "$TOOLCHAIN" zlib-out
tar xJf "ffmpeg-$FFMPEG_VERSION.tar.xz"
tar xzf "zlib-$ZLIB_VERSION.tar.gz"
tar xJf "$TOOLCHAIN.tar.xz"
export PATH="$WORK/src/$TOOLCHAIN/bin:$PATH"

(cd "zlib-$ZLIB_VERSION" &&
  make -s -f win32/Makefile.gcc PREFIX=x86_64-w64-mingw32- CC=x86_64-w64-mingw32-clang libz.a)
mkdir -p zlib-out/include zlib-out/lib
cp "zlib-$ZLIB_VERSION"/{zlib.h,zconf.h} zlib-out/include/
cp "zlib-$ZLIB_VERSION/libz.a" zlib-out/lib/

# The demuxers are exactly ingest's whitelist (FFmpeg calls "mpeg" mpegps in configure).
DEMUXERS="$(python3 -I "$HERE/demuxers.py" "$REPO/app/audio/ingest.py")"
DECODERS="$(paste -sd, "$HERE/decoders.txt")"

cd "ffmpeg-$FFMPEG_VERSION"
./configure --enable-cross-compile --cross-prefix=x86_64-w64-mingw32- \
  --cc=x86_64-w64-mingw32-clang --target-os=mingw32 --arch=x86_64 \
  --disable-everything --disable-autodetect --disable-network --disable-doc --disable-debug \
  --disable-x86asm --disable-avdevice --disable-swscale --disable-ffplay --disable-ffprobe \
  --enable-static --disable-shared --enable-zlib \
  --extra-cflags="-I$WORK/src/zlib-out/include" --extra-ldflags="-L$WORK/src/zlib-out/lib" \
  --enable-protocol=file --enable-demuxer="$DEMUXERS" --enable-parsers \
  --enable-decoder="$DECODERS" --enable-muxer=wav --enable-encoder=pcm_s16le \
  --enable-filter=aresample,aformat,anull,abuffer,abuffersink,atrim \
  --extra-version=upshot-audio-only > "$WORK/configure.log"
grep -q "^License: LGPL version 2.1 or later" "$WORK/configure.log" \
  || { echo "configure did not produce an LGPL-2.1 build" >&2; exit 1; }
make -s -j"$(nproc)"

DIST="$WORK/dist"
cp ffmpeg.exe "$DIST/ffmpeg.exe"
cp COPYING.LGPLv2.1 "$DIST/LICENSE-FFmpeg.txt"
cp "../zlib-$ZLIB_VERSION/LICENSE" "$DIST/LICENSE-zlib.txt"
cp "../ffmpeg-$FFMPEG_VERSION.tar.xz" "../ffmpeg-$FFMPEG_VERSION.tar.xz.asc" \
   "../zlib-$ZLIB_VERSION.tar.gz" "$DIST/sources/"
cp "$HERE/build.sh" "$HERE/demuxers.py" "$HERE/decoders.txt" "$DIST/sources/"
{
  echo "FFmpeg $FFMPEG_VERSION, audio decoders only, for Upshot (built by packaging/ffmpeg/build.sh)."
  echo "zlib $ZLIB_VERSION; toolchain llvm-mingw $TOOLCHAIN_VERSION."
  echo "Licence: LGPL-2.1-or-later (FFmpeg), Zlib (zlib)."
  echo "The sources/ folder holds everything it was built from."
  grep "^License:" "$WORK/configure.log"
} > "$DIST/BUILD.txt"
(cd "$DIST" && find . -type f ! -name SHA256SUMS -printf '%P\n' | sort | xargs sha256sum > SHA256SUMS)
echo "built: $DIST/ffmpeg.exe"
