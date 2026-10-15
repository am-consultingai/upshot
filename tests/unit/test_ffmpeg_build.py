"""Upshot's own ffmpeg.exe: audio decoders only, every input pinned (licence epic, S3).

The build (packaging/ffmpeg/build.sh) runs in CI by hand; these check what it is built
from. Measured before it replaced BtbN's build: the same 165 of 175 real files convert,
every sample within 1 LSB.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FFMPEG = ROOT / "packaging" / "ffmpeg"


def test_the_demuxers_are_exactly_ingest_s_whitelist() -> None:
    from app.audio.ingest import FORMATS

    out = subprocess.run([sys.executable, "-I", str(FFMPEG / "demuxers.py"),
                          str(ROOT / "app" / "audio" / "ingest.py")],
                         capture_output=True, text=True, check=True).stdout.strip()  # fmt: skip
    names = out.split(",")
    assert len(names) == len(FORMATS)
    assert [("mpeg" if n == "mpegps" else n) for n in names] == list(FORMATS)


def test_the_decoders_are_ffmpeg_s_own_audio_decoders() -> None:
    names = (FFMPEG / "decoders.txt").read_text(encoding="utf-8").split()
    assert len(names) == len(set(names))
    assert not [n for n in names if n.startswith("lib")], "no external codec library"
    needed = ["aac", "mp3", "opus", "vorbis", "flac", "alac", "amrnb", "amrwb", "wmav2",
              "ac3", "eac3", "pcm_s16le", "pcm_f32le", "pcm_alaw", "speex", "gsm_ms"]  # fmt: skip
    assert not [n for n in needed if n not in names]
    for video in ("h264", "hevc", "vp9", "av1", "mpeg4", "prores", "wmv2"):
        assert video not in names, video


def test_every_input_is_pinned_and_ffmpeg_s_signature_checked() -> None:
    script = (FFMPEG / "build.sh").read_text(encoding="utf-8")
    for name in ("FFMPEG_SHA256", "ZLIB_SHA256", "TOOLCHAIN_SHA256"):
        assert re.search(rf"^{name}=[0-9a-f]{{64}}$", script, re.M), name
    assert re.search(r"^FFMPEG_KEY=[0-9A-F]{40}$", script, re.M)
    assert "VALIDSIG $FFMPEG_KEY" in script
    assert "--disable-everything" in script and "--disable-autodetect" in script
    assert "--enable-gpl" not in script and "--enable-nonfree" not in script


def test_the_release_workflow_runs_by_hand_and_checks_its_tag() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ffmpeg.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in workflow and "push:" not in workflow
    assert "RELEASE: ${{ inputs.release }}" in workflow, "the input reaches the shell via env"
    assert '${{ inputs.release }}"' not in workflow
