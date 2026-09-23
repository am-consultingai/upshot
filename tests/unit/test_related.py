"""Related meetings: each signal on its own, then how they rank together (D53)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.clock import FakeClock
from app.db.dao import Dao, Turn, connect
from app.pipeline.states import MeetingState
from app.related import jaccard, related, same_people, shared_actions


@pytest.fixture
def dao(tmp_path: Path):  # type: ignore[no-untyped-def]
    conn = connect(tmp_path / "index.db")
    d = Dao(conn, FakeClock())
    yield d
    conn.close()


def _meeting(
    dao: Dao,
    tmp_path: Path,
    meeting_id: str,
    *,
    title: str | None = None,
    started_at: str = "2026-09-20T10:00:00",
    state: str = MeetingState.DELIVERED,
    people: list[str] | None = None,
    series: str | None = None,
    said: list[str] | None = None,
    actions: list[str] | None = None,
) -> str:
    calendar: dict[str, Any] | None = None
    if people is not None or series is not None:
        calendar = {
            "title": series,
            "participants": people or [],
            "match": {"state": "matched", "source": "auto"},
        }
    dao.insert_meeting(
        meeting_id=meeting_id,
        folder=tmp_path / meeting_id,
        source="manual",
        state=state,
        profile="cpu-deferred",
        title=title or meeting_id,
        started_at=started_at,
        calendar_json=json.dumps(calendar) if calendar else None,
    )
    if said:
        dao.index_turns(meeting_id, [Turn(i, "THEM", i * 1000, t) for i, t in enumerate(said)])
    if actions:
        dao.replace_action_items(meeting_id, [("ME", what, None, None) for what in actions])
    return meeting_id


def _reasons(found: list[Any], meeting_id: str) -> list[dict[str, Any]]:
    return next(r.reasons for r in found if r.meeting.id == meeting_id)


def test_shared_action_items(dao: Dao, tmp_path: Path) -> None:
    _meeting(dao, tmp_path, "a", actions=["send the revised pricing deck to Dana", "book a room"])
    _meeting(dao, tmp_path, "b", actions=["Send revised pricing deck to Dana"])
    _meeting(dao, tmp_path, "c", actions=["water the plants"])
    found = related(dao, "a")
    assert [r.meeting.id for r in found] == ["b"]
    assert _reasons(found, "b") == [{"code": "shared_actions", "count": 1}]


def test_jaccard_and_identical_text() -> None:
    assert jaccard({"a", "b"}, {"a", "b"}) == 1.0
    assert jaccard(set(), {"a"}) == 0.0
    from app.db.dao import ActionItem

    def item(what: str) -> ActionItem:
        return ActionItem(0, "m", 0, "ME", what, None, None, True, None)

    assert shared_actions([item("Do it")], [item("do  it")]) == 1, "identical once normalised"
    assert shared_actions([item("write the spec")], [item("review the budget")]) == 0


def test_same_people(dao: Dao, tmp_path: Path) -> None:
    _meeting(dao, tmp_path, "a", people=["Dana Levi", "Yoni", "Maya"])
    _meeting(dao, tmp_path, "two-shared", people=["dana levi", "Yoni", "Someone Else"])
    _meeting(dao, tmp_path, "one-shared", people=["Dana Levi", "Other", "Another"])
    found = related(dao, "a")
    assert [r.meeting.id for r in found] == ["two-shared"]
    assert _reasons(found, "two-shared") == [
        {"code": "same_people", "names": ["Dana Levi", "Yoni"]}
    ]


def test_a_one_on_one_shares_its_only_person() -> None:
    assert same_people({"dana"}, {"dana", "yoni", "maya"}) == {"dana"}
    assert same_people({"dana", "x"}, {"dana", "y"}) == set()
    assert same_people(set(), {"dana"}) == set()


def test_same_series(dao: Dao, tmp_path: Path) -> None:
    _meeting(dao, tmp_path, "a", series="Weekly Pricing Sync")
    _meeting(dao, tmp_path, "b", series="weekly  pricing sync")
    _meeting(dao, tmp_path, "c", series="Board meeting")
    found = related(dao, "a")
    assert [r.meeting.id for r in found] == ["b"]
    assert {"code": "same_series"} in _reasons(found, "b")


def test_mentions_a_distinctive_word(dao: Dao, tmp_path: Path) -> None:
    _meeting(dao, tmp_path, "a", said=["activation dropped after the interstitial shipped"])
    _meeting(dao, tmp_path, "b", said=["what happened to activation in August?"])
    _meeting(dao, tmp_path, "c", said=["the office move is on Sunday"])
    found = related(dao, "a")
    assert [r.meeting.id for r in found] == ["b"]
    assert _reasons(found, "b") == [{"code": "mentions", "term": "activation"}]


def test_common_and_short_words_are_not_mentions(dao: Dao, tmp_path: Path) -> None:
    _meeting(dao, tmp_path, "a", said=["actually we should really think about this"])
    _meeting(dao, tmp_path, "b", said=["actually I really think we should"])
    assert related(dao, "a") == []


def test_a_word_everyone_says_is_not_distinctive(dao: Dao, tmp_path: Path) -> None:
    """Rare across the library, or it is not a topic: "roadmap" said in every meeting
    connects all of them, which is the same as connecting none."""
    for index in range(14):
        _meeting(dao, tmp_path, f"m{index}", said=["the roadmap review"])
    _meeting(dao, tmp_path, "x", said=["roadmap and the kubernetes migration"])
    _meeting(dao, tmp_path, "y", said=["kubernetes costs doubled"])
    found = related(dao, "x")
    assert _reasons(found, "y") == [{"code": "mentions", "term": "kubernetes"}]
    assert all(r.meeting.id == "y" for r in found)


def test_hebrew_mentions(dao: Dao, tmp_path: Path) -> None:
    _meeting(dao, tmp_path, "a", said=["אנחנו צריכים לסגור את הקונטיינר עד סוף החודש"])
    _meeting(dao, tmp_path, "b", said=["מה קורה עם הקונטיינר"])
    found = related(dao, "a")
    assert _reasons(found, "b") == [{"code": "mentions", "term": "הקונטיינר"}]


def test_excludes_itself_and_recordings_in_progress(dao: Dao, tmp_path: Path) -> None:
    _meeting(dao, tmp_path, "a", series="Standup")
    _meeting(dao, tmp_path, "live", series="Standup", state=MeetingState.RECORDING)
    _meeting(dao, tmp_path, "thrown", series="Standup", state=MeetingState.DISCARDED)
    assert related(dao, "a") == []


def test_ranking_and_limit(dao: Dao, tmp_path: Path) -> None:
    """A carried commitment outranks a shared series, which outranks a shared word; and
    among equals the nearer meeting comes first. At most five."""
    _meeting(
        dao,
        tmp_path,
        "a",
        series="Pricing",
        said=["the kubernetes bill"],
        actions=["send the pricing deck"],
        started_at="2026-09-20T10:00:00",
    )
    _meeting(dao, tmp_path, "action", actions=["send the pricing deck"])
    _meeting(dao, tmp_path, "series-near", series="Pricing", started_at="2026-09-19T10:00:00")
    _meeting(dao, tmp_path, "series-far", series="Pricing", started_at="2026-08-01T10:00:00")
    _meeting(dao, tmp_path, "word", said=["kubernetes again"])
    for index in range(4):
        _meeting(dao, tmp_path, f"more{index}", series="Pricing", started_at="2026-07-01T10:00:00")
    found = related(dao, "a")
    assert len(found) == 5
    assert [r.meeting.id for r in found[:3]] == ["action", "series-near", "series-far"]
    assert "word" not in [r.meeting.id for r in found], "the weakest falls off the end"
    assert set(found[0].as_api()) == {"id", "title", "started_at", "duration_s", "reasons"}
