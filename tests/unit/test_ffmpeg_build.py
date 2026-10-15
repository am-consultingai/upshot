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
              "ac3", "eac3", "pcm_s16le", "pcm_f32le", "pcm_alaw", "speex", "gsm_ms",
              "adpcm_g722", "adpcm_g726", "ra_144", "ra_288"]  # fmt: skip
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


#: Components FFmpeg 8.1.3's configure builds only with --enable-gpl (``deps="lgpl_gpl"``).
GPL_ONLY = {
    "adpcm_circus", "adpcm_ima_escape", "adpcm_ima_hvqm2", "adpcm_ima_hvqm4",
    "adpcm_ima_magix", "adpcm_ima_pda", "adpcm_n64", "adpcm_psxc", "ahx",
}  # fmt: skip


def test_no_gpl_only_decoder_or_parser_is_asked_for() -> None:
    """configure would drop them silently; asking for them would be asking for GPL code."""
    for name in ("decoders.txt", "parsers.txt"):
        names = set((FFMPEG / name).read_text(encoding="utf-8").split())
        assert not names & GPL_ONLY, name


def test_the_build_job_holds_no_write_token() -> None:
    """The build runs third-party code; only the publish job, after it, may write."""
    workflow = (ROOT / ".github" / "workflows" / "ffmpeg.yml").read_text(encoding="utf-8")
    build, publish = workflow.split("\n  publish:\n", 1)
    assert "permissions: {}" in workflow
    assert "contents: read" in build and "contents: write" not in build
    assert "persist-credentials: false" in build
    assert "contents: write" in publish and "build.sh" not in publish


def test_configure_must_enable_exactly_what_was_asked() -> None:
    """configure drops a name it does not know, or a GPL-only one, without a word."""
    script = (FFMPEG / "build.sh").read_text(encoding="utf-8")
    for what in ("decoders", "demuxers", "parsers"):
        assert f'check {what} "$' in script, what


def test_the_parsers_are_audio_ones_only() -> None:
    names = set((FFMPEG / "parsers.txt").read_text(encoding="utf-8").split())
    assert {"aac", "mpegaudio", "opus", "vorbis", "flac", "ac3", "amr"} <= names
    assert not {"h264", "hevc", "vvc", "mpegvideo", "vp9", "av1"} & names
