"""The API behind the redesign: dated and hand-added action items, tags, speaker names,
chapters, related meetings, asking a meeting, storage, and recording a calendar event."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.db.dao import Turn
from app.pipeline.states import MeetingState
from tests.fixtures.api import build_harness


@pytest.fixture
def api(tmp_path: Path, app_home: Path):  # type: ignore[no-untyped-def]
    return build_harness(tmp_path)


def _meeting(api, meeting_id: str, title: str = "A meeting", **kwargs: Any):  # type: ignore[no-untyped-def]
    return api.services.dao.insert_meeting(
        meeting_id=meeting_id,
        folder=api.services.config.data_root / meeting_id,
        source="manual",
        state=kwargs.pop("state", MeetingState.DELIVERED),
        profile="cpu-deferred",
        title=title,
        started_at=kwargs.pop("started_at", "2026-09-23T14:00:00"),
        **kwargs,
    )


# ------------------------------------------------------------------ action items


def test_items_carry_the_new_fields(api) -> None:  # type: ignore[no-untyped-def]
    _meeting(api, "m-a")
    api.services.dao.replace_action_items(
        "m-a", [{"who": "ME", "what": "send the deck", "due": "Thursday", "detail": "for Dana"}]
    )
    item = api.client().get("/api/action-items").json()["items"][0]
    assert item["detail"] == "for Dana"
    assert item["due_at"] == "2026-09-24", "resolved from `due` against the meeting's day"
    assert item["snoozed_until"] is None
    assert item["source"] == "model"


def test_patch_touches_only_what_was_sent(api) -> None:  # type: ignore[no-untyped-def]
    _meeting(api, "m-a")
    api.services.dao.replace_action_items(
        "m-a", [{"who": "Dana", "what": "write the spec", "due_at": "2026-09-30"}]
    )
    client = api.client()
    item_id = client.get("/api/action-items").json()["items"][0]["id"]

    snoozed = client.patch(f"/api/action-items/{item_id}", json={"snoozed_until": "2026-09-25"})
    assert snoozed.status_code == 200
    body = snoozed.json()
    assert body["snoozed_until"] == "2026-09-25"
    assert body["due_at"] == "2026-09-30", "absent is not null"
    assert body["done"] is False

    edited = client.patch(
        f"/api/action-items/{item_id}",
        json={"who": "ME", "what": "write the spec v2", "detail": "blocks design", "done": True},
    ).json()
    assert (edited["who"], edited["mine"], edited["what"]) == ("ME", True, "write the spec v2")
    assert edited["detail"] == "blocks design" and edited["done"] is True

    cleared = client.patch(f"/api/action-items/{item_id}", json={"due_at": None}).json()
    assert cleared["due_at"] is None, "explicit null clears"
    assert cleared["snoozed_until"] == "2026-09-25"


def test_patch_refuses_a_malformed_date(api) -> None:  # type: ignore[no-untyped-def]
    _meeting(api, "m-a")
    api.services.dao.replace_action_items("m-a", [("ME", "x", None, None)])
    client = api.client()
    item_id = client.get("/api/action-items").json()["items"][0]["id"]
    for body in ({"due_at": "Thursday"}, {"due_at": "2026-02-30"}, {"snoozed_until": "24/9"}):
        assert client.patch(f"/api/action-items/{item_id}", json=body).status_code == 422
    assert client.patch(f"/api/action-items/{item_id}", json={"what": " "}).status_code == 422
    assert client.patch("/api/action-items/99999", json={"due_at": None}).status_code == 404


def test_add_and_delete_an_item(api) -> None:  # type: ignore[no-untyped-def]
    _meeting(api, "m-a")
    api.services.dao.replace_action_items("m-a", [("ME", "from the model", None, None)])
    client = api.client()
    created = client.post(
        "/api/meetings/m-a/action-items",
        json={"what": "call the bank", "due_at": "2026-09-28", "detail": "before the wire"},
    )
    assert created.status_code == 201
    item = created.json()
    assert item["source"] == "user" and item["who"] == "ME" and item["mine"] is True
    assert item["seq"] == 1 and item["due_at"] == "2026-09-28"
    assert item["meeting_title"] == "A meeting"

    assert client.post("/api/meetings/m-a/action-items", json={"what": " "}).status_code == 422
    assert client.post("/api/meetings/m-a/action-items", json={}).status_code == 422
    bad_date = {"what": "x", "due_at": "soon"}
    assert client.post("/api/meetings/m-a/action-items", json=bad_date).status_code == 422
    assert client.post("/api/meetings/nope/action-items", json={"what": "x"}).status_code == 404

    assert client.delete(f"/api/action-items/{item['id']}").json() == {"deleted": item["id"]}
    assert client.delete(f"/api/action-items/{item['id']}").status_code == 404
    assert [i["what"] for i in client.get("/api/action-items").json()["items"]] == [
        "from the model"
    ]


# ------------------------------------------------------------------ tags


def test_tags_roundtrip(api) -> None:  # type: ignore[no-untyped-def]
    _meeting(api, "m-a")
    _meeting(api, "m-b")
    client = api.client()
    assert client.put("/api/meetings/m-a/tags", json={"tags": ["Pricing", " q4 "]}).json() == {
        "tags": ["Pricing", "q4"]
    }
    assert client.put("/api/meetings/m-b/tags", json={"tags": ["pricing"]}).json() == {
        "tags": ["Pricing"]
    }
    assert client.get("/api/tags").json() == {
        "tags": [{"tag": "Pricing", "count": 2}, {"tag": "q4", "count": 1}]
    }
    rows = {row["id"]: row for row in client.get("/api/meetings").json()["meetings"]}
    assert rows["m-a"]["tags"] == ["Pricing", "q4"]
    assert rows["m-b"]["tags"] == ["Pricing"]
    assert client.get("/api/meetings/m-a").json()["tags"] == ["Pricing", "q4"]

    too_many = {"tags": [f"t{n}" for n in range(13)]}
    assert client.put("/api/meetings/m-a/tags", json=too_many).status_code == 422
    assert client.put("/api/meetings/m-a/tags", json={"tags": ["x" * 41]}).status_code == 422
    assert client.put("/api/meetings/nope/tags", json={"tags": []}).status_code == 404
    assert client.put("/api/meetings/m-a/tags", json={"tags": []}).json() == {"tags": []}


# ------------------------------------------------------------------ the meeting payload


def test_speaker_names_merge(api) -> None:  # type: ignore[no-untyped-def]
    _meeting(api, "m-a")
    client = api.client()
    assert client.get("/api/meetings/m-a").json()["speaker_names"] == {}
    body = client.patch(
        "/api/meetings/m-a", json={"speaker_names": {"THEM": "Dana", "THEM_1": "Yoni"}}
    ).json()
    assert body["speaker_names"] == {"THEM": "Dana", "THEM_1": "Yoni"}
    body = client.patch(
        "/api/meetings/m-a", json={"speaker_names": {"THEM_1": None, "ME": "Avi"}}
    ).json()
    assert body["speaker_names"] == {"THEM": "Dana", "ME": "Avi"}
    body = client.patch("/api/meetings/m-a", json={"speaker_names": {"THEM": ""}}).json()
    assert body["speaker_names"] == {"ME": "Avi"}
    listed = client.get("/api/meetings").json()["meetings"][0]
    assert listed["speaker_names"] == {"ME": "Avi"}, "an object in the list too, not a string"
    assert (
        client.patch("/api/meetings/m-a", json={"speaker_names": {"": "x"}}).status_code == 422
    )


def test_chapters_come_from_the_notes(api) -> None:  # type: ignore[no-untyped-def]
    meeting = _meeting(api, "m-a")
    client = api.client()
    assert client.get("/api/meetings/m-a").json()["chapters"] == []
    meeting.path.mkdir(parents=True, exist_ok=True)
    (meeting.path / "notes.json").write_text(
        json.dumps(
            {
                "summary_html": "<p>x</p>",
                "chapters": [
                    {"title": "Pricing", "start_ms": 60000},
                    {"title": "Intro", "start_ms": 0, "end_ms": None},
                ],
            }
        ),
        encoding="utf-8",
    )
    assert client.get("/api/meetings/m-a").json()["chapters"] == [
        {"title": "Intro", "start_ms": 0, "end_ms": 60000},
        {"title": "Pricing", "start_ms": 60000, "end_ms": None},
    ]


# ------------------------------------------------------------------ related and ask


def test_related_endpoint(api) -> None:  # type: ignore[no-untyped-def]
    _meeting(api, "m-a", "Pricing review")
    _meeting(api, "m-b", "Pricing follow-up", started_at="2026-09-16T14:00:00")
    _meeting(api, "m-live", "Live", state=MeetingState.RECORDING)
    dao = api.services.dao
    dao.replace_action_items("m-a", [("ME", "send the pricing deck", None, None)])
    dao.replace_action_items("m-b", [("ME", "send the pricing deck", None, None)])
    dao.replace_action_items("m-live", [("ME", "send the pricing deck", None, None)])
    body = api.client().get("/api/meetings/m-a/related").json()
    assert body["related"] == [
        {
            "id": "m-b",
            "title": "Pricing follow-up",
            "started_at": "2026-09-16T14:00:00",
            "duration_s": None,
            "reasons": [
                {"code": "shared_actions", "count": 1},
                {"code": "mentions", "term": "pricing"},
            ],
        }
    ]
    assert api.client().get("/api/meetings/nope/related").status_code == 404


def _transcribed(api, meeting_id: str, title: str, lines: list[tuple[int, str, str]]) -> None:  # type: ignore[no-untyped-def]
    from app.asr.backend import Segment, TranscriptFile

    meeting = _meeting(api, meeting_id, title)
    TranscriptFile(
        language="en",
        segments=[
            Segment(id=i, track="them", speaker=who, start=at / 1000, end=at / 1000 + 3, text=text)
            for i, (at, who, text) in enumerate(lines)
        ],
    ).write(meeting.path / "transcript.json")
    api.services.dao.index_turns(
        meeting_id, [Turn(i, who, at, text) for i, (at, who, text) in enumerate(lines)]
    )


def test_ask_answers_from_the_transcript(api) -> None:  # type: ignore[no-untyped-def]
    _transcribed(
        api,
        "m-a",
        "Churn review",
        [
            (0, "ME", "Let's start with the numbers."),
            (65000, "THEM", "Churn went up to four percent in August."),
            (90000, "ME", "Then we move the renewal campaign earlier."),
        ],
    )
    client = api.client()
    body = client.post("/api/meetings/m-a/ask", json={"question": "What was churn in August?"})
    assert body.status_code == 200, body.text
    answer = body.json()
    assert answer["scope"] == "meeting"
    assert answer["answer"] == "Churn went up to four percent in August."
    assert answer["citations"] == [
        {"meeting_id": "m-a", "at_ms": 65000, "text": "Churn went up to four percent in August."}
    ]


def test_ask_is_given_the_names_and_the_related_meetings(api) -> None:  # type: ignore[no-untyped-def]
    from app.llm.client import FakeLlm

    api.services.llm = FakeLlm()
    _transcribed(api, "m-a", "Kubernetes costs", [(0, "THEM", "The kubernetes bill doubled.")])
    _transcribed(
        api, "m-b", "Kubernetes plan", [(5000, "THEM", "The migration finishes Friday.")]
    )
    api.services.dao.update_meeting("m-a", speaker_names=json.dumps({"THEM": "Dana"}))
    client = api.client()
    body = client.post(
        "/api/meetings/m-a/ask",
        json={"question": "When does the migration finish?", "scope": "related"},
    ).json()
    assert body["scope"] == "related"
    assert body["citations"] == [
        {"meeting_id": "m-b", "at_ms": 5000, "text": "The migration finishes Friday."}
    ]
    sent = api.services.llm.calls[-1]["user"]
    assert "=== Meeting m-a" in sent and "=== Meeting m-b" in sent
    assert "[00:00] Dana: The kubernetes bill doubled." in sent


def test_ask_refusals(api) -> None:  # type: ignore[no-untyped-def]
    from app.llm.client import FakeLlm

    _meeting(api, "m-a")
    client = api.client()
    assert client.post("/api/meetings/nope/ask", json={"question": "x"}).status_code == 404
    assert client.post("/api/meetings/m-a/ask", json={"question": "  "}).status_code == 422
    assert (
        client.post("/api/meetings/m-a/ask", json={"question": "x", "scope": "all"}).status_code
        == 422
    )

    class Broken(FakeLlm):
        def complete_json(self, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("the provider is down")

    api.services.llm = Broken()
    response = client.post("/api/meetings/m-a/ask", json={"question": "anything?"})
    assert response.status_code == 502
    assert "the provider is down" in response.json()["detail"]


def test_ask_drops_citations_to_meetings_it_was_not_shown(api) -> None:  # type: ignore[no-untyped-def]
    from app.llm.client import FakeLlm, LlmResult

    _transcribed(api, "m-a", "A", [(0, "ME", "hello"), (10000, "THEM", "the answer is 42")])

    class Wandering(FakeLlm):
        def complete_json(self, **kwargs):  # type: ignore[no-untyped-def]
            return LlmResult(
                data={
                    "answer": "42",
                    "citations": [
                        {"meeting_id": "elsewhere", "at_ms": 1},
                        {"at_ms": 12500},  # between turns, and no meeting named
                        {"meeting_id": "m-a", "at_ms": 10000},  # the same turn again
                    ],
                },
                model="fake",
            )

    api.services.llm = Wandering()
    body = api.client().post("/api/meetings/m-a/ask", json={"question": "what?"}).json()
    assert body["citations"] == [{"meeting_id": "m-a", "at_ms": 10000, "text": "the answer is 42"}]


# ------------------------------------------------------------------ status and recording


def test_status_reports_storage_and_caches_it(api) -> None:  # type: ignore[no-untyped-def]
    root = Path(api.services.config.data_root)
    client = api.client()
    before = client.get("/api/status").json()["storage_bytes"]
    assert isinstance(before, int)
    (root / "m-x").mkdir(parents=True, exist_ok=True)
    (root / "m-x" / "audio.bin").write_bytes(b"\0" * 5000)
    assert client.get("/api/status").json()["storage_bytes"] == before, "cached for a minute"
    api.clock.advance(61)
    assert client.get("/api/status").json()["storage_bytes"] >= before + 5000


def test_storage_of_a_missing_root_is_zero(api) -> None:  # type: ignore[no-untyped-def]
    from app.api.routes import storage_bytes

    api.services.config.set("data_root", str(Path(api.services.config.data_root) / "absent"))
    assert storage_bytes(api.services) == 0


def test_record_this_calendar_event(api) -> None:  # type: ignore[no-untyped-def]
    from datetime import datetime, timedelta

    from app.gcal.events import Attendee, CalendarEvent, EventStore

    start = api.clock.now()
    EventStore(api.services.conn).replace_window(
        "primary",
        start - timedelta(hours=1),
        start + timedelta(hours=2),
        [
            CalendarEvent(
                calendar_id="primary",
                event_id="ev-1",
                title="Pricing sync",
                start=start,
                end=start + timedelta(minutes=30),
                attendees=(Attendee(name="Dana Levi"),),
                attendee_count=1,
            )
        ],
        synced_at=datetime.now().astimezone(),
    )
    client = api.client()
    missing = {"calendar_id": "primary", "event_id": "nope"}
    assert client.post("/api/recording/start", json=missing).status_code == 404
    half = {"event_id": "ev-1"}
    assert client.post("/api/recording/start", json=half).status_code == 400

    started = client.post(
        "/api/recording/start", json={"calendar_id": "primary", "event_id": "ev-1"}
    )
    assert started.status_code == 200, started.text
    meeting_id = started.json()["meeting_id"]
    try:
        page = client.get(f"/api/meetings/{meeting_id}").json()
        assert page["title"] == "Pricing sync"
        assert page["title_source"] == "calendar"
        calendar = page["calendar"]
        assert calendar["event"]["event_id"] == "ev-1"
        assert calendar["match"]["state"] == "matched"
        assert calendar["match"]["source"] == "user", "chosen, so nothing re-matches it"
        assert calendar["participants"] == ["Dana Levi"]
    finally:
        client.post("/api/recording/stop")


# ------------------------------------------------------------------ the seed


def test_the_seed_takes_the_new_fields(
    tmp_path: Path, app_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UP_TEST_MODE", "1")
    harness = build_harness(tmp_path)
    client = harness.client()
    response = client.post(
        "/api/test/seed",
        json={
            "reset": True,
            "meetings": [
                {
                    "id": "seeded",
                    "title": "Seeded",
                    "state": "DELIVERED",
                    "started_at": "2026-09-23T10:00:00",
                    "ended_at": "2026-09-23T10:45:00",
                    "duration_s": 2700,
                    "sensitive": True,
                    "tags": ["Pricing", "pricing", "Q4"],
                    "speaker_names": {"THEM": "Dana"},
                    "chapters": [{"title": "Intro", "start_ms": 0, "end_ms": 60000}],
                    "notes": {"summary_html": "<p>x</p>"},
                    "turns": [
                        {"speaker": "ME", "at_ms": 0, "end_ms": 1500, "text": "hi"},
                        {"speaker": "THEM", "at_ms": 2000, "text": "hello"},
                    ],
                    "action_items": [
                        {
                            "who": "Dana",
                            "what": "write the spec",
                            "due": "Thursday",
                            "detail": "for the review",
                        },
                        {"who": "ME", "what": "send the deck", "done": True},
                        {"who": "ME", "what": "chase legal", "snoozed_until": "2026-09-28"},
                        {
                            "who": "ME",
                            "what": "call the bank",
                            "source": "user",
                            "due_at": "2026-09-30",
                            "done": True,
                        },
                    ],
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    page = client.get("/api/meetings/seeded").json()
    assert page["ended_at"] == "2026-09-23T10:45:00" and page["duration_s"] == 2700
    assert page["sensitive"] == 1
    assert page["tags"] == ["Pricing", "Q4"]
    assert page["speaker_names"] == {"THEM": "Dana"}
    assert page["chapters"] == [{"title": "Intro", "start_ms": 0, "end_ms": 60000}]
    items = {item["what"]: item for item in page["action_items"]}
    assert items["write the spec"]["detail"] == "for the review"
    assert items["write the spec"]["due_at"] == "2026-09-24"
    assert items["send the deck"]["done"] is True
    assert items["chase legal"]["snoozed_until"] == "2026-09-28"
    assert items["call the bank"]["source"] == "user" and items["call the bank"]["done"] is True
    transcript = client.get("/api/meetings/seeded/transcript").json()
    assert transcript["segments"][0]["end"] == 1.5
    assert transcript["segments"][1]["end"] == 6.0

    # A reset takes the tags with the meetings.
    client.post("/api/test/seed", json={"reset": True})
    assert client.get("/api/tags").json() == {"tags": []}


def test_failed_stage_on_the_list_and_the_page(api) -> None:  # type: ignore[no-untyped-def]
    _meeting(api, "m-bad")
    _meeting(api, "m-ok")
    queue = api.services.queue
    for stage in ("summarize", "transcribe"):
        job = queue.enqueue("m-bad", stage)
        api.services.conn.execute("UPDATE jobs SET state = 'failed' WHERE id = ?", (job.id,))
    queue.complete(queue.enqueue("m-ok", "transcribe"))
    client = api.client()
    rows = {row["id"]: row for row in client.get("/api/meetings").json()["meetings"]}
    assert rows["m-bad"]["failed_stage"] == "transcribe", "the earliest in pipeline order"
    assert rows["m-ok"]["failed_stage"] is None
    assert client.get("/api/meetings/m-bad").json()["failed_stage"] == "transcribe"


def test_search_hits_carry_the_speakers_name(api) -> None:  # type: ignore[no-untyped-def]
    _meeting(api, "m-a", "Churn review")
    api.services.dao.index_turns(
        "m-a",
        [
            Turn(0, "THEM", 0, "churn is up"),
            Turn(1, "ME", 1000, "churn again"),
            Turn(2, "THEM_1", 2000, "churn forever"),
        ],
    )
    api.services.dao.update_meeting("m-a", speaker_names=json.dumps({"THEM": "Dana", "ME": "Avi"}))
    hits = api.client().get("/api/search", params={"q": "churn"}).json()["hits"]
    names = {hit["speaker"]: hit["speaker_name"] for hit in hits if hit["kind"] == "transcript"}
    assert names == {"THEM": "Dana", "ME": None, "THEM_1": None}
    assert all(hit["speaker_name"] is None for hit in hits if hit["kind"] == "title")
