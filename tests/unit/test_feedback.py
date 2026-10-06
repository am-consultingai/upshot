"""Feedback from inside the app: anonymous, previewed, and sent as previewed (D87, D1-D3)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.config import Config
from app.diagnostics.client import Attachment, envelope
from app.diagnostics.feedback import (
    DAILY_MAX,
    Feedback,
    FeedbackError,
    FeedbackSender,
    transcript_size,
)

DSN = "https://0123456789abcdef0123456789abcdef@o1.ingest.example.io/42"


class Sent:
    def __init__(self) -> None:
        self.items: list[tuple[str, dict[str, Any], list[Attachment]]] = []

    def __call__(self, kind: str, payload: dict[str, Any], files: list[Attachment]) -> bool:
        self.items.append((kind, payload, files))
        return True


def sender(tmp_path: Path, *, dsn: str = DSN) -> tuple[FeedbackSender, Sent]:
    config = Config.load(file=tmp_path / "app_config.json")
    config.set("diagnostics.install_id", "f" * 32)
    sent = Sent()
    made = FeedbackSender(
        config,
        dsn=dsn,
        version="0.2.0",
        commit="abcdef123456",
        home=tmp_path,
        send=sent,
        now=lambda: datetime(2026, 10, 5, 12, tzinfo=UTC),
    )
    return made, sent


def test_feedback_is_anonymous_and_says_what_the_user_wrote(tmp_path: Path) -> None:
    made, sent = sender(tmp_path)
    result = made.send(Feedback(kind="idea", message="  A dark tray icon, please.  "))
    assert result["sent"] is True and result["reference"].startswith("FB-")
    kind, payload, files = sent.items[0]
    assert kind == "feedback" and files == []
    assert payload["contexts"]["feedback"] == {"message": "A dark tray icon, please."}
    assert payload["tags"]["reference"] == result["reference"]
    dumped = json.dumps(payload)
    assert "user" not in payload and "f" * 32 not in dumped, "no install id with feedback"


def test_an_email_is_there_only_when_given_and_must_look_like_one(tmp_path: Path) -> None:
    made, _ = sender(tmp_path)
    payload, _ = made.build(Feedback(kind="problem", message="x", email="dana@example.com"))
    assert payload["contexts"]["feedback"]["contact_email"] == "dana@example.com"
    with pytest.raises(FeedbackError, match="email"):
        made.build(Feedback(kind="problem", message="x", email="not an email"))


def test_technical_details_are_coarse_and_optional(tmp_path: Path) -> None:
    made, _ = sender(tmp_path)
    (tmp_path / "diagnostics").mkdir()
    (tmp_path / "diagnostics" / "sent.json").write_text(
        json.dumps({"KeyError@app.asr.local:transcribe": "2026-10-05"}), encoding="utf-8"
    )
    with_details, _ = made.build(Feedback(kind="problem", message="x"))
    assert with_details["tags"]["recent_crashes"] == "KeyError@app.asr.local:transcribe"
    assert "os" in with_details["tags"]
    without, _ = made.build(Feedback(kind="problem", message="x", details=False))
    assert "os" not in without["tags"] and "recent_crashes" not in without["tags"]


def test_the_screenshot_goes_only_when_attached(tmp_path: Path) -> None:
    made, sent = sender(tmp_path)
    made.send(Feedback(kind="problem", message="x", screenshot=b"\x89PNG..."))
    files = sent.items[0][2]
    assert [(f.filename, f.content_type, f.data) for f in files] == [
        ("upshot-window.png", "image/png", b"\x89PNG...")
    ]
    preview = made.preview(Feedback(kind="problem", message="x", screenshot=b"\x89PNG..."))
    assert preview["attachments"] == [{"filename": "upshot-window.png", "bytes": 7}]


def test_a_summary_rating_says_which_provider_and_the_summary_only_if_asked(tmp_path: Path) -> None:
    made, _ = sender(tmp_path)
    meeting = {
        "provider": "anthropic",
        "prompt_version": "s7",
        "summary_language": "he",
        "transcript_size": "5k-20k",
    }
    payload, files = made.build(
        Feedback(
            kind="summary_rating",
            rating="down",
            message="missed a decision",
            meeting=meeting,
            summary_html="<p>x</p>",
        )
    )
    assert payload["tags"]["rating"] == "down" and payload["tags"]["provider"] == "anthropic"
    assert [f.filename for f in files] == ["summary.html"]
    plain, none = made.build(Feedback(kind="summary_rating", rating="up", meeting=meeting))
    assert none == [] and plain["contexts"]["feedback"]["message"] == "Summary rated up"
    with pytest.raises(FeedbackError):
        made.build(Feedback(kind="summary_rating", rating="sideways"))


@pytest.mark.parametrize(
    ("feedback", "message"),
    [
        (Feedback(kind="rant", message="x"), "unknown kind"),
        (Feedback(kind="idea", message="   "), "write something"),
        (Feedback(kind="idea", message="x" * 5001), "limited"),
    ],
)
def test_what_cannot_be_sent_is_refused_before_anything_leaves(
    tmp_path: Path, feedback: Feedback, message: str
) -> None:
    made, sent = sender(tmp_path)
    with pytest.raises(FeedbackError, match=message):
        made.send(feedback)
    assert sent.items == []


def test_a_build_from_source_cannot_send(tmp_path: Path) -> None:
    made, _ = sender(tmp_path, dsn="")
    assert made.available is False
    assert made.preview(Feedback(kind="idea", message="x"))["payload"], "it can still be seen"
    with pytest.raises(FeedbackError, match="from source"):
        made.send(Feedback(kind="idea", message="x"))


def test_there_is_a_daily_limit(tmp_path: Path) -> None:
    made, sent = sender(tmp_path)
    for _ in range(DAILY_MAX):
        made.send(Feedback(kind="idea", message="x"))
    with pytest.raises(FeedbackError, match="enough"):
        made.send(Feedback(kind="idea", message="x"))
    assert len(sent.items) == DAILY_MAX


def test_an_envelope_carries_the_screenshot_after_the_feedback() -> None:
    body = envelope(
        "feedback", {"event_id": "a" * 32}, [Attachment("s.png", "image/png", b"PNGDATA")]
    )
    lines = body.split(b"\n")
    assert json.loads(lines[1])["type"] == "feedback"
    assert json.loads(lines[3]) == {
        "type": "attachment", "length": 7, "filename": "s.png", "content_type": "image/png",
    }  # fmt: skip
    assert lines[4] == b"PNGDATA"


def test_transcript_size_is_coarse() -> None:
    assert [transcript_size(n) for n in (10, 6000, 30000, 90000)] == [
        "<5k",
        "5k-20k",
        "20k-60k",
        "60k+",
    ]
