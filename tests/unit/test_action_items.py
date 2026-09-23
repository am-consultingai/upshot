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
    blank = {"detail": None, "due_at": None}
    assert parsed == [
        {"who": "Dana", "what": "write the spec", "due": "Thursday", "at_ms": None, **blank},
        {"who": "?", "what": "pull the numbers", "due": None, "at_ms": None, **blank},
        {"who": "ME", "what": "take it to review", "due": None, "at_ms": 31000, **blank},
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


# ------------------------------------------------------------------ detail, dates, snooze, source


def _dated_meeting(dao: Dao, tmp_path: Path, started_at: str = "2026-09-23T14:00:00") -> str:
    return dao.insert_meeting(
        meeting_id="m-dated",
        folder=tmp_path / "m-dated",
        source="manual",
        state=MeetingState.RECORDING,
        profile="cpu-deferred",
        started_at=started_at,
    ).id


def test_items_carry_detail_and_a_date(dao: Dao, tmp_path: Path) -> None:
    meeting_id = _dated_meeting(dao, tmp_path)
    dao.replace_action_items(
        meeting_id,
        [
            {
                "who": "ME",
                "what": "send the deck",
                "due": "Thursday",
                "at_ms": 1000,
                "detail": "Dana cannot book the review without it",
                "due_at": "2026-09-24",
            }
        ],
    )
    item = dao.action_items(meeting_id=meeting_id)[0]
    assert item.detail == "Dana cannot book the review without it"
    assert item.due_at == "2026-09-24"
    assert item.source == "model"
    assert item.snoozed_until is None
    payload = item.as_dict()
    for key in ("detail", "due_at", "snoozed_until", "source", "done"):
        assert key in payload


def test_an_old_row_gets_its_date_lazily(dao: Dao, tmp_path: Path) -> None:
    """No backfill: a row with only the spoken words is resolved against the meeting's
    own day when it is read — Wednesday's "by Thursday" is the next day."""
    meeting_id = _dated_meeting(dao, tmp_path)
    dao.replace_action_items(meeting_id, [("Dana", "write the spec", "by Thursday", None)])
    item = dao.action_items(meeting_id=meeting_id)[0]
    assert item.due_at == "2026-09-24"
    stored = dao.conn.execute("SELECT due_at FROM action_items").fetchone()["due_at"]
    assert stored is None, "resolved on read, not written back"


def test_an_unparseable_due_stays_undated(dao: Dao, tmp_path: Path) -> None:
    meeting_id = _dated_meeting(dao, tmp_path)
    dao.replace_action_items(meeting_id, [("Dana", "write the spec", "asap", None)])
    assert dao.action_items(meeting_id=meeting_id)[0].due_at is None


def test_resummarizing_keeps_the_snooze(dao: Dao, tmp_path: Path) -> None:
    meeting_id = _meeting(dao, tmp_path)
    dao.replace_action_items(meeting_id, [("ME", "chase legal", None, None)])
    item = dao.action_items(meeting_id=meeting_id)[0]
    dao.update_action_item(item.id, snoozed_until="2026-10-01")
    dao.replace_action_items(meeting_id, [("ME", "Chase  legal", None, None)])
    assert dao.action_items(meeting_id=meeting_id)[0].snoozed_until == "2026-10-01"


def test_user_items_survive_a_resummarize(dao: Dao, tmp_path: Path) -> None:
    """Typed in by hand is not the model's to take away — and the seq numbers the two
    kinds of row share must not collide when the model's list changes length."""
    meeting_id = _meeting(dao, tmp_path)
    dao.replace_action_items(meeting_id, [("ME", "one", None, None), ("ME", "two", None, None)])
    mine = dao.add_action_item(meeting_id, what="call the bank", due_at="2026-09-30")
    assert mine.source == "user"
    assert mine.seq == 2
    assert mine.mine is True, "ME by default"

    # Longer this time: the model's rows would reach seq 2, where the user's row sat.
    dao.replace_action_items(
        meeting_id,
        [("ME", "one", None, None), ("ME", "two", None, None), ("ME", "three", None, None)],
    )
    after = sorted(dao.action_items(meeting_id=meeting_id), key=lambda i: i.seq)
    assert [(i.seq, i.what, i.source) for i in after] == [
        (0, "one", "model"),
        (1, "two", "model"),
        (2, "three", "model"),
        (3, "call the bank", "user"),
    ]
    assert after[3].due_at == "2026-09-30"

    # And shorter.
    dao.replace_action_items(meeting_id, [])
    assert [(i.seq, i.what) for i in dao.action_items(meeting_id=meeting_id)] == [
        (0, "call the bank")
    ]


def test_a_model_item_repeating_a_user_item_is_not_shown_twice(dao: Dao, tmp_path: Path) -> None:
    meeting_id = _meeting(dao, tmp_path)
    dao.add_action_item(meeting_id, what="Send the deck")
    dao.replace_action_items(meeting_id, [("ME", "send the  deck", None, None)])
    items = dao.action_items(meeting_id=meeting_id)
    assert [(i.what, i.source) for i in items] == [("Send the deck", "user")]


def test_update_action_item(dao: Dao, tmp_path: Path) -> None:
    meeting_id = _meeting(dao, tmp_path)
    dao.replace_action_items(meeting_id, [("Dana", "write the spec", None, None)])
    item = dao.action_items(meeting_id=meeting_id)[0]

    updated = dao.update_action_item(
        item.id, who="me", what="Write the spec v2", detail=" for the review ", due_at="2026-10-02"
    )
    assert updated is not None
    assert (updated.who, updated.mine) == ("me", True), "reassigning keeps `mine` in step"
    assert updated.what == "Write the spec v2"
    assert updated.detail == "for the review"
    assert updated.due_at == "2026-10-02"
    norm = dao.conn.execute("SELECT norm FROM action_items WHERE id = ?", (item.id,)).fetchone()
    assert norm["norm"] == "write the spec v2", "rewording keeps the match key in step"

    cleared = dao.update_action_item(item.id, due_at=None, detail=None)
    assert cleared is not None and cleared.due_at is None and cleared.detail is None
    assert dao.update_action_item(item.id) is not None, "nothing to change is not an error"
    with pytest.raises(ValueError):
        dao.update_action_item(item.id, what="   ")
    with pytest.raises(ValueError):
        dao.update_action_item(item.id, colour="red")
    assert dao.update_action_item(999_999, done=True) is None


def test_delete_action_item(dao: Dao, tmp_path: Path) -> None:
    meeting_id = _meeting(dao, tmp_path)
    item = dao.add_action_item(meeting_id, what="gone soon")
    assert dao.delete_action_item(item.id) is True
    assert dao.delete_action_item(item.id) is False
    assert dao.action_items(meeting_id=meeting_id) == []


def test_add_action_item_refuses_nothing(dao: Dao, tmp_path: Path) -> None:
    meeting_id = _meeting(dao, tmp_path)
    with pytest.raises(ValueError):
        dao.add_action_item(meeting_id, what="  ")


# ------------------------------------------------------------------ tags


def test_tags_are_normalised_and_deduplicated(dao: Dao, tmp_path: Path) -> None:
    meeting_id = _meeting(dao, tmp_path)
    stored = dao.set_tags(meeting_id, ["  Q4   planning ", "q4 planning", "Hiring", "", "לקוחות"])
    assert stored == ["Q4 planning", "Hiring", "לקוחות"], "user's casing, user's order"
    assert dao.tags(meeting_id) == stored
    assert dao.set_tags(meeting_id, ["hiring"]) == ["hiring"], "re-cased on its own meeting"


def test_a_tag_keeps_the_librarys_spelling(dao: Dao, tmp_path: Path) -> None:
    first = _meeting(dao, tmp_path, "m1")
    second = _meeting(dao, tmp_path, "m2")
    dao.set_tags(first, ["Roadmap"])
    assert dao.set_tags(second, ["roadmap", "pricing"]) == ["Roadmap", "pricing"]
    assert dao.tag_counts() == [("Roadmap", 2), ("pricing", 1)]
    assert dao.tags_by_meeting() == {first: ["Roadmap"], second: ["Roadmap", "pricing"]}


def test_tag_limits(dao: Dao, tmp_path: Path) -> None:
    meeting_id = _meeting(dao, tmp_path)
    with pytest.raises(ValueError):
        dao.set_tags(meeting_id, [f"tag {n}" for n in range(13)])
    with pytest.raises(ValueError):
        dao.set_tags(meeting_id, ["x" * 41])
    assert dao.set_tags(meeting_id, [f"tag {n}" for n in range(12)])  # exactly the limit


def test_deleting_a_meeting_takes_its_tags(dao: Dao, tmp_path: Path) -> None:
    meeting_id = _meeting(dao, tmp_path)
    dao.set_tags(meeting_id, ["gone"])
    dao.delete_meeting(meeting_id)
    assert dao.tag_counts() == []


def test_tag_counts_order(dao: Dao, tmp_path: Path) -> None:
    for name in ("m1", "m2", "m3"):
        _meeting(dao, tmp_path, name)
    dao.set_tags("m1", ["beta", "alpha"])
    dao.set_tags("m2", ["beta", "Alpha"])
    dao.set_tags("m3", ["gamma", "beta"])
    assert dao.tag_counts() == [("beta", 3), ("alpha", 2), ("gamma", 1)]


# ------------------------------------------------------------------ chapters


def test_chapters_are_sorted_and_filled() -> None:
    from app.llm.schema import chapters

    parsed = chapters(
        {
            "summary_html": "<p>x</p>",
            "chapters": [
                {"title": "Pricing", "start_ms": 60000, "end_ms": None},
                {"title": " Intro ", "start_ms": 0},
                "not a chapter",
                {"title": "", "start_ms": 5},
                {"title": "No start"},
                {"title": "Wrap-up", "start_ms": 120000, "end_ms": 150000},
            ],
        }
    )
    assert parsed == [
        {"title": "Intro", "start_ms": 0, "end_ms": 60000},
        {"title": "Pricing", "start_ms": 60000, "end_ms": 120000},
        {"title": "Wrap-up", "start_ms": 120000, "end_ms": 150000},
    ]
    assert chapters({"summary_html": "x"}) == []
    assert chapters("nope") == []


def test_envelope_accepts_chapters_detail_and_due_at() -> None:
    assert is_valid(
        {
            "summary_html": "<p>hi</p>",
            "action_items": [
                {"who": "Dana", "what": "spec", "detail": "for review", "due_at": "2026-09-24"}
            ],
            "chapters": [{"title": "Intro", "start_ms": 0, "end_ms": 1000}],
        }
    )


def test_a_malformed_due_at_costs_only_the_date() -> None:
    parsed = action_items(
        {
            "summary_html": "<p>x</p>",
            "action_items": [{"who": "Dana", "what": "spec", "due": "Thu", "due_at": "Thursday"}],
        }
    )
    assert parsed[0]["due_at"] is None and parsed[0]["due"] == "Thu"
