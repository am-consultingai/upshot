"""Importing a transcript produced elsewhere, without re-transcribing it."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.pipeline.states import JobStage, MeetingState
from app.transcript_import import import_transcript, lines_of, markdown, spread


@pytest.fixture
def services(app_home):  # type: ignore[no-untyped-def]
    """A real service graph on a throwaway app home, with nothing running behind it."""
    from app.services import build

    built = build(with_worker=False, with_recorder=False)
    yield built
    built.conn.close()


TEXT = "first line, quite short\n\na second line that is a good deal longer than the first\n"


def test_spread_follows_length_not_line_count() -> None:
    """Equal spacing would put a one-word line and a paragraph at the same distance."""
    lines = ["x", "y" * 99]
    starts = spread(lines, 100.0)
    assert starts[0] == 0
    assert starts[1] == 1000, "the long line starts after the short one's share, not halfway"


def test_markdown_matches_what_the_summarizer_reads() -> None:
    text = markdown(["hello"], [65_000], "THEM")
    assert text.startswith("**[01:05] THEM:** hello")


def test_import_lands_ready_to_summarize(services) -> None:  # type: ignore[no-untyped-def]
    """TRANSCRIBED with no transcribe job: the text was handed to us, not derived."""
    result = import_transcript(
        services,
        text=TEXT,
        title="AppsFlyer sync",
        started_at=datetime(2026, 9, 2, 13, 0, tzinfo=UTC),
        duration_s=1800,
    )
    meeting = services.dao.require_meeting(result.meeting_id)
    assert meeting.state == MeetingState.TRANSCRIBED
    assert result.turns == 2
    assert (meeting.path / "transcript.md").exists()
    assert (meeting.path / "transcript.json").exists()
    assert services.queue.get_by_stage(meeting.id, str(JobStage.TRANSCRIBE)) is None, (
        "nothing may be queued to re-derive a transcript we were given"
    )


def test_import_refuses_an_empty_transcript(services) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError, match="empty"):
        import_transcript(
            services,
            text="   \n\n  ",
            title="nothing",
            started_at=datetime(2026, 9, 2, 13, 0, tzinfo=UTC),
            duration_s=60,
        )


def test_lines_of_drops_blanks() -> None:
    assert lines_of("a\n\n\n b \n") == ["a", "b"]
