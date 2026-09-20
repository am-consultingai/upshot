"""The one structure the free-form summariser still hands back (D47).

The envelope parser is deliberately forgiving and the store deliberately careful:
a provider that half-honours the request should cost its own item and nothing else,
and re-summarizing — which is a routine act, since the prompt is editable and
Summarize has no staleness check — must never cost the user a tick they have made.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.clock import FakeClock
from app.db.dao import Dao, connect, normalize_action
from app.llm.schema import action_items, is_valid
from app.pipeline.states import MeetingState


@pytest.fixture
def dao(tmp_path: Path):  # type: ignore[no-untyped-def]
    conn = connect(tmp_path / "index.db")
    d = Dao(conn, FakeClock())
    d.seed_ids(11)
    yield d
    conn.close()


def _meeting(dao: Dao, tmp_path: Path, meeting_id: str = "m1") -> str:
    meeting = dao.insert_meeting(
        meeting_id=meeting_id,
        folder=tmp_path / meeting_id,
        source="manual",
        state=MeetingState.RECORDING,
        profile="cpu-deferred",
        title=f"Meeting {meeting_id}",
    )
    return meeting.id


# ------------------------------------------------------------------ the envelope


def test_envelope_accepts_action_items() -> None:
    assert is_valid(
        {
            "summary_html": "<p>hi</p>",
            "action_items": [{"who": "Dana", "what": "write the spec", "due": "Thursday"}],
        }
    )


def test_envelope_without_action_items_is_still_valid() -> None:
    """Additive on purpose: a summary written before this existed must keep opening."""
    assert is_valid({"summary_html": "<p>hi</p>"})


def test_envelope_rejects_an_unknown_key() -> None:
    assert not is_valid({"summary_html": "<p>hi</p>", "conclusions": []})


def test_action_items_survives_a_half_honoured_reply() -> None:
    """One bad entry costs itself. The summary is the product; this list is a bonus."""
    parsed = action_items(
        {
            "summary_html": "<p>x</p>",
            "action_items": [
                {"who": "Dana", "what": "  write the spec  ", "due": " Thursday "},
                "not an object",
                {"who": "Yoni"},  # no `what`: nothing to do
                {"who": "", "what": "pull the numbers"},
                {"who": "ME", "what": "take it to review", "at_ms": 31000.0},
            ],
        }
    )
    assert parsed == [
        {"who": "Dana", "what": "write the spec", "due": "Thursday", "at_ms": None},
        {"who": "?", "what": "pull the numbers", "due": None, "at_ms": None},
        {"who": "ME", "what": "take it to review", "due": None, "at_ms": 31000},
    ]


def test_action_items_of_a_reply_without_the_key() -> None:
    assert action_items({"summary_html": "<p>x</p>"}) == []
    assert action_items({"summary_html": "<p>x</p>", "action_items": "later"}) == []
    assert action_items("not a dict") == []


# ------------------------------------------------------------------ the store


def test_replace_and_read_back(dao: Dao, tmp_path: Path) -> None:
    meeting_id = _meeting(dao, tmp_path)
    dao.replace_action_items(
        meeting_id,
        [
            ("ME", "take the rollback to Monday review", None, 31000),
            ("Dana", "write the instrumentation spec", "Thursday", None),
        ],
    )
    items = dao.action_items(meeting_id=meeting_id)
    assert [item.what for item in items] == [
        "take the rollback to Monday review",
        "write the instrumentation spec",
    ]
    assert items[0].mine is True
    assert items[1].mine is False
    assert items[1].due == "Thursday"
    assert items[0].at_ms == 31000
    assert all(item.done is False for item in items)
    # The join is what makes the inbox readable without opening anything.
    assert items[0].meeting_title == "Meeting m1"


def test_replace_drops_blank_items_and_renumbers(dao: Dao, tmp_path: Path) -> None:
    meeting_id = _meeting(dao, tmp_path)
    dao.replace_action_items(
        meeting_id, [("ME", "   ", None, None), ("Dana", "do the thing", None, None)]
    )
    items = dao.action_items(meeting_id=meeting_id)
    assert [(item.seq, item.what) for item in items] == [(0, "do the thing")]


def test_resummarizing_keeps_what_the_user_ticked(dao: Dao, tmp_path: Path) -> None:
    """The point of the norm column, and the reason the checkbox can be trusted."""
    meeting_id = _meeting(dao, tmp_path)
    dao.replace_action_items(
        meeting_id,
        [
            ("Dana", "write the instrumentation spec", None, None),
            ("Yoni", "pull drop-off", None, None),
        ],
    )
    spec = next(i for i in dao.action_items(meeting_id=meeting_id) if i.who == "Dana")
    dao.set_action_done(spec.id, done=True)

    # A second run: the model reorders, repunctuates and adds one.
    dao.replace_action_items(
        meeting_id,
        [
            ("Yoni", "pull drop-off", None, None),
            ("Dana", "Write   the instrumentation spec", "Thursday", None),
            ("ME", "book the review", None, None),
        ],
    )
    after = {item.what: item for item in dao.action_items(meeting_id=meeting_id)}
    assert after["Write   the instrumentation spec"].done is True, "the tick must survive"
    assert after["pull drop-off"].done is False
    assert after["book the review"].done is False


def test_normalize_action_is_the_match_key() -> None:
    assert normalize_action("  Write   the SPEC ") == normalize_action("write the spec")


def test_unticking(dao: Dao, tmp_path: Path) -> None:
    meeting_id = _meeting(dao, tmp_path)
    dao.replace_action_items(meeting_id, [("ME", "do it", None, None)])
    item = dao.action_items(meeting_id=meeting_id)[0]
    assert dao.set_action_done(item.id, done=True).done is True  # type: ignore[union-attr]
    assert dao.set_action_done(item.id, done=False).done is False  # type: ignore[union-attr]
    assert dao.set_action_done(999_999, done=True) is None


def test_open_only_and_cross_meeting_order(dao: Dao, tmp_path: Path) -> None:
    """Mine first, then newest meeting first: the order the inbox reads in."""
    first = dao.insert_meeting(
        meeting_id="m-old",
        folder=tmp_path / "m-old",
        source="manual",
        state=MeetingState.RECORDING,
        profile="cpu-deferred",
        started_at="2026-09-01T09:00:00Z",
    ).id
    second = dao.insert_meeting(
        meeting_id="m-new",
        folder=tmp_path / "m-new",
        source="manual",
        state=MeetingState.RECORDING,
        profile="cpu-deferred",
        started_at="2026-09-19T09:00:00Z",
    ).id
    dao.replace_action_items(first, [("ME", "mine but old", None, None)])
    dao.replace_action_items(second, [("Dana", "hers and new", None, None)])

    every = dao.action_items()
    assert [item.what for item in every] == ["mine but old", "hers and new"]

    dao.set_action_done(every[0].id, done=True)
    assert [item.what for item in dao.action_items(open_only=True)] == ["hers and new"]


def test_deleting_a_meeting_takes_its_action_items(dao: Dao, tmp_path: Path) -> None:
    meeting_id = _meeting(dao, tmp_path)
    dao.replace_action_items(meeting_id, [("ME", "do it", None, None)])
    dao.delete_meeting(meeting_id)
    assert dao.action_items() == []
