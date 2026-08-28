from __future__ import annotations

from app.asr.backend import Segment, sort_segments
from app.pipeline.stages.assemble import (
    coalesce,
    render_markdown,
    suppress_echo,
    timestamp,
)


def seg(seg_id: int, track: str, start: float, end: float, text: str) -> Segment:
    return Segment(
        id=seg_id,
        track=track,
        speaker="ME" if track == "me" else "THEM",
        start=start,
        end=end,
        text=text,
    )


def test_merge_orders_by_start() -> None:
    segments = [
        seg(0, "them", 5.0, 6.0, "third"),
        seg(1, "me", 1.0, 2.0, "first"),
        seg(2, "them", 3.0, 4.0, "second"),
    ]
    ordered = sort_segments(segments)
    assert [s.text for s in ordered] == ["first", "second", "third"]
    # ties break deterministically by track, then id
    tie = sort_segments([seg(9, "them", 1.0, 2.0, "b"), seg(4, "me", 1.0, 2.0, "a")])
    assert [s.track for s in tie] == ["me", "them"]
    assert [s.track for s in sort_segments(list(reversed(tie)))] == ["me", "them"]


def test_echo_suppressed() -> None:
    sentence = "אני חושב שנצטרך לדחות את הרילי‏ס לשבוע הבא"
    segments = sort_segments(
        [
            seg(0, "me", 10.0, 13.0, sentence),
            seg(1, "them", 10.2, 13.2, sentence),
            seg(2, "them", 20.0, 22.0, "בסדר גמור"),
        ]
    )
    kept, dropped = suppress_echo(segments)
    assert dropped == 1
    assert [s.track for s in kept] == ["me", "them"]
    assert kept[0].speaker == "ME"
    assert kept[1].text == "בסדר גמור"


def test_echo_not_oversuppressed() -> None:
    """Genuine agreement is not an echo."""
    segments = sort_segments(
        [
            seg(0, "me", 10.0, 11.0, "כן, בדיוק"),
            seg(1, "them", 10.1, 11.5, "כן, בדיוק מה שאמרתי קודם על הארכיטקטורה"),
        ]
    )
    kept, dropped = suppress_echo(segments)
    assert dropped == 0
    assert len(kept) == 2


def test_echo_far_apart_in_time_survives() -> None:
    text = "let us move the release to next week"
    segments = sort_segments([seg(0, "me", 10.0, 12.0, text), seg(1, "them", 400.0, 402.0, text)])
    kept, dropped = suppress_echo(segments)
    assert dropped == 0 and len(kept) == 2


def test_coalesce_adjacent_turns() -> None:
    close = [seg(i, "me", i * 2.0, i * 2.0 + 1.0, f"part {i}") for i in range(3)]
    turns = coalesce(close)
    assert len(turns) == 1
    assert turns[0].text == "part 0 part 1 part 2"

    far = [seg(i, "me", i * 4.0, i * 4.0 + 1.0, f"part {i}") for i in range(3)]
    assert len(coalesce(far)) == 3


def test_overlap_preserved() -> None:
    segments = sort_segments(
        [
            seg(0, "me", 10.0, 14.0, "I think we should ship it"),
            seg(1, "them", 11.0, 13.0, "אבל יש לנו באג פתוח"),
        ]
    )
    kept, dropped = suppress_echo(segments)
    turns = coalesce(kept)
    assert dropped == 0
    assert len(turns) == 2
    assert turns[0].end > turns[1].start, "the overlap stays visible in the timestamps"
    assert {turn.speaker for turn in turns} == {"ME", "THEM"}


def test_timestamp_format() -> None:
    assert timestamp(0) == "00:00"
    assert timestamp(72.4) == "01:12"
    assert timestamp(3599) == "59:59"
    assert timestamp(3600) == "60:00"


def test_transcript_md_golden(golden) -> None:  # type: ignore[no-untyped-def]
    segments = sort_segments(
        [
            seg(0, "them", 12.3, 17.8, "בוקר טוב, נתחיל עם הסטטוס של ה-deployment"),
            seg(1, "me", 18.0, 20.0, "אני העברתי את זה ל-Kubernetes אתמול"),
            seg(2, "me", 21.0, 23.5, "ואז הרצתי את ה-migration"),
            seg(3, "them", 30.0, 34.0, "מצוין, אז אפשר לסגור את ה-ticket"),
            seg(4, "me", 61.0, 63.0, "yes, closing it now"),
        ]
    )
    kept, _ = suppress_echo(segments)
    markdown = render_markdown(coalesce(kept), title="סטטוס שבועי")
    golden("transcript_he.md", markdown)
