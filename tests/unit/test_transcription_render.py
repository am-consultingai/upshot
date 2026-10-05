"""Every format a file transcription is offered in, from one result.json (D86, §4.3)."""

from __future__ import annotations

import json

import pytest

from app.asr.backend import Segment, Word
from app.transcription.render import (
    FORMATS,
    build_cues,
    filename,
    render,
    srt_time,
    vtt_time,
)
from app.transcription.types import FileTranscript


def words(text: str, start: float, step: float = 0.3, gap: float = 0.0) -> tuple[Word, ...]:
    out, t = [], start
    for part in text.split():
        out.append(Word(part, round(t, 3), round(t + step, 3), 0.9))
        t += step + gap
    return tuple(out)


def seg(i: int, speaker: str, start: float, text: str, **kw: float) -> Segment:
    ws = words(text, start, **kw)
    return Segment(i, "them", speaker, start, ws[-1].e if ws else start + 1, text, ws)


def result(*segments: Segment, language: str | None = "he") -> dict:  # type: ignore[type-arg]
    transcript = FileTranscript(
        duration_s=max((s.end for s in segments), default=0.0),
        segments=segments,
        language=language,
        language_conf=0.9 if language else None,
        language_source="classifier" if language else "none",
        diarized=len({s.speaker for s in segments}) > 1,
    )
    return transcript.as_result(id="tr_test", source_name="ראיון.mp4")


HEBREW = result(
    seg(0, "S1", 0.0, "שלום לכולם ותודה שבאתם"),
    seg(1, "S2", 3.0, "תודה שהזמנתם אותי"),
)
ENGLISH = result(
    seg(0, "S1", 0.0, "hello everyone and thanks for coming"),
    seg(1, "S1", 3.0, "let's get started"),
    language="en",
)
EMPTY = result(language=None)


# ----------------------------------------------------------------------- cue rules


def test_a_cue_ends_after_max_words() -> None:
    cues = build_cues(
        [seg(0, "S1", 0, "one two three four five six seven eight nine")], max_words=4
    )
    assert [c.text for c in cues] == ["one two three four", "five six seven eight", "nine"]


def test_a_cue_ends_at_42_characters() -> None:
    text = "internationalization accessibility documentation"
    cues = build_cues([seg(0, "S1", 0, text)], max_words=20)
    assert all(len(c.text) <= 42 for c in cues)
    assert " ".join(c.text for c in cues) == text


def test_a_word_longer_than_a_line_is_a_cue_on_its_own() -> None:
    long = "x" * 50
    cues = build_cues([seg(0, "S1", 0, f"a {long} b")], max_words=20)
    assert [c.text for c in cues] == ["a", long, "b"]


def test_a_cue_ends_after_six_seconds() -> None:
    cues = build_cues([seg(0, "S1", 0, "slow words that go on and on", step=1.5)], max_words=20)
    assert all(c.end - c.start <= 6.0 for c in cues)
    assert len(cues) > 1


def test_a_cue_ends_at_a_pause() -> None:
    cues = build_cues([seg(0, "S1", 0, "before the pause", gap=0.7)], max_words=20)
    assert [c.text for c in cues] == ["before", "the", "pause"]
    tight = build_cues([seg(0, "S1", 0, "no pause here", gap=0.5)], max_words=20)
    assert [c.text for c in tight] == ["no pause here"]


def test_a_cue_never_spans_a_speaker_change() -> None:
    cues = build_cues([seg(0, "S1", 0, "hi there"), seg(1, "S2", 0.7, "hello")], max_words=20)
    assert [(c.speaker, c.text) for c in cues] == [("S1", "hi there"), ("S2", "hello")]


def test_a_segment_without_word_timings_is_one_cue() -> None:
    bare = Segment(0, "them", "S1", 1.0, 9.5, "a whole sentence with no word timings at all")
    cues = build_cues([bare])
    assert [(c.start, c.end, c.text) for c in cues] == [(1.0, 9.5, bare.text)]


def test_words_per_cue_is_clamped_to_its_range() -> None:
    payload = result(seg(0, "S1", 0, "a b c d e f g h i j k l m n o p q r s t u v w"))
    srt = render(payload, "srt", max_words_per_cue=0)
    assert srt.count(" --> ") == 23, "0 becomes 1"
    srt = render(payload, "srt", max_words_per_cue=99)
    assert "a b c d e f g h i j k l m n o p q r s t" in srt, "99 becomes 20"


# ----------------------------------------------------------------------- formats


def test_srt() -> None:
    srt = render(HEBREW, "srt")
    assert srt.startswith("1\n00:00:00,000 --> 00:00:01,200\nשלום לכולם ותודה שבאתם\n\n2\n")
    assert "<v" not in srt and "S1" not in srt, "SRT stays plain text"


def test_vtt_names_voices_only_when_there_are_several() -> None:
    vtt = render(HEBREW, "vtt")
    assert vtt.startswith("WEBVTT\n\n00:00:00.000 --> 00:00:01.200\n<v S1>שלום")
    assert "<v S2>תודה" in vtt
    assert "<v " not in render(ENGLISH, "vtt")


def test_vtt_escapes_markup() -> None:
    payload = result(seg(0, "S1", 0, "a <b> & c"), language="en")
    assert "a &lt;b&gt; &amp; c" in render(payload, "vtt")


def test_hebrew_is_written_as_it_is() -> None:
    for fmt in ("txt", "md", "srt", "vtt"):
        text = render(HEBREW, fmt)
        assert "שלום לכולם" in text
        assert not any(mark in text for mark in "‎‏‪‫‬⁦⁧⁩")


def test_txt_paragraphs_with_and_without_timestamps() -> None:
    assert render(ENGLISH, "txt") == (
        "S1: hello everyone and thanks for coming let's get started\n"
    ), "one speaker's adjacent segments are one paragraph"
    assert render(HEBREW, "txt", timestamps=True) == (
        "[00:00] S1: שלום לכולם ותודה שבאתם\n\n[00:03] S2: תודה שהזמנתם אותי\n"
    )


def test_md_is_the_meeting_transcript_format() -> None:
    assert render(HEBREW, "md") == (
        "**[00:00] S1:** שלום לכולם ותודה שבאתם\n**[00:03] S2:** תודה שהזמנתם אותי\n"
    )


def test_json_is_the_stored_result() -> None:
    assert json.loads(render(HEBREW, "json")) == HEBREW


def test_timestamps_past_an_hour() -> None:
    assert srt_time(3725.5) == "01:02:05,500"
    assert vtt_time(3725.5) == "01:02:05.500"
    assert srt_time(36000.0004) == "10:00:00,000"
    late = result(seg(0, "S1", 3725.0, "late"), language="en")
    assert "01:02:05,000 --> 01:02:05,300" in render(late, "srt")


def test_the_empty_result_is_valid_in_every_format() -> None:
    assert render(EMPTY, "srt") == ""
    assert render(EMPTY, "vtt") == "WEBVTT\n"
    assert render(EMPTY, "txt") == "" and render(EMPTY, "md") == ""
    assert json.loads(render(EMPTY, "json"))["segments"] == []


def test_an_unknown_format_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown format"):
        render(HEBREW, "docx")


def test_every_format_has_a_file_name() -> None:
    assert {filename("ראיון.mp4", fmt) for fmt in FORMATS} == {
        "ראיון.json", "ראיון.txt", "ראיון.md", "ראיון.srt", "ראיון.vtt",
    }  # fmt: skip
    assert filename("no-extension", "srt") == "no-extension.srt"
    assert filename(".mp4", "srt") == "transcript.srt"
