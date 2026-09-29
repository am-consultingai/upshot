"""The assistant on a Google AI plan (D79), with the fake ``agy`` on PATH.

Upshot looks things up and passes them in; ``agy`` runs as the tool-less ``upshot`` agent
(the fake refuses to run without it) and streams its answer, whose citation markers go
through the same checks as the other routes'.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from app.assistant.antigravity_route import gather, keywords
from app.db.dao import Turn as Line
from app.pipeline.states import MeetingState
from tests.fixtures.antigravity import install_fake_agy
from tests.fixtures.api import build_harness, serve
from tests.integration.test_assistant_api import ask, chunks_of, text_of

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the fake CLI is a shebang script")


@pytest.fixture
def agy_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    install_fake_agy(tmp_path / "bin")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}")
    log = tmp_path / "agy-calls.jsonl"
    monkeypatch.setenv("FAKE_AGY_LOG", str(log))
    monkeypatch.setenv("FAKE_AGY_MODE", "ok")
    return log


@pytest.fixture
def api(tmp_path: Path, app_home: Path, agy_log: Path):  # type: ignore[no-untyped-def]
    harness = build_harness(tmp_path)
    harness.services.config.set("llm.provider", "antigravity-subscription")
    harness.services.dao.insert_meeting(
        meeting_id="m-budget",
        folder=harness.services.config.data_root / "m-budget",
        source="manual",
        state=MeetingState.RECORDING,
        profile="cpu-deferred",
        title="Q4 budget review",
        started_at="2026-09-20T09:30:00Z",
    )
    harness.services.dao.index_turns(
        "m-budget",
        [
            Line(0, "THEM", 4000, "The marketing budget is cut by ten percent."),
            Line(1, "ME", 9000, "Dana will send the revised plan by Thursday."),
        ],
    )
    return harness


def runs(log: Path) -> list[dict[str, Any]]:
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    return [call for call in calls if "--input-format" in call["args"]]


def test_antigravity_answers_from_what_upshot_gathered_with_a_checked_citation(
    api, agy_log: Path
) -> None:  # type: ignore[no-untyped-def]
    with serve(api) as client:
        chunks = chunks_of(ask(client, "What was decided about the marketing budget", chat_id="a"))
    kinds = [c["type"] for c in chunks if c != "[DONE]"]
    assert not [k for k in kinds if k.startswith("tool-")], "agy calls no tools; Upshot looked up"
    citation = next(c for c in chunks if c != "[DONE]" and c["type"] == "data-citation")
    assert (citation["data"]["meeting_id"], citation["data"]["at_ms"]) == ("m-budget", 4000)
    assert text_of(chunks) == "Antigravity found it [1](#cite-1)."
    assert chunks[-2]["messageMetadata"] == {
        "provider": "antigravity-subscription",
        "model": "gemini-fake",
    }

    run = runs(agy_log)[-1]
    assert "--agent" in run["args"] and "--json-schema" not in run["args"]
    assert "The marketing budget is cut by ten percent." in run["prompt"], "the transcript went in"
    assert "you have no tools" in run["prompt"].lower()
    assert Path(run["cwd"]).name == "antigravity-cli"


def test_every_turn_carries_the_conversation_so_far(api) -> None:  # type: ignore[no-untyped-def]
    with serve(api) as client:
        first = text_of(chunks_of(ask(client, "What about the budget", chat_id="b")))
        second = text_of(chunks_of(ask(client, "And who sends the plan?", chat_id="b")))
    assert not first.startswith("Recapped.")
    assert second.startswith("Recapped.")


def test_signed_out_says_so_without_running_anything(
    api, agy_log: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("FAKE_AGY_MODE", "signed-out")
    with serve(api) as client:
        chunks = chunks_of(ask(client, "What about the budget", chat_id="c"))
    problem = next(c for c in chunks if c != "[DONE]" and c["type"] == "data-problem")
    assert problem["data"] == {"code": "signed-out", "provider": "antigravity-subscription"}
    assert runs(agy_log) == [], "a signed-out run would open Google's sign-in page unasked"


def test_the_status_says_antigravity_can_answer(api) -> None:  # type: ignore[no-untyped-def]
    with serve(api) as client:
        body = client.get("/api/assistant/status").json()
    assert body == {"provider": "antigravity-subscription", "available": True, "problem": None}


def test_keywords_are_the_words_worth_searching_for() -> None:
    assert keywords("What was decided about the marketing budget?") == ["marketing", "budget"]
    assert keywords("מה החלטנו על התקציב?") == ["התקציב"]


def test_a_question_sharing_no_word_still_reaches_the_recent_meetings(api) -> None:  # type: ignore[no-untyped-def]
    """Hebrew asked, English held: the most recent meetings are read in full anyway."""
    found = gather(api.services, "מה דנה צריכה לשלוח?", {"route": "/", "scope": "all"})
    assert "Dana will send the revised plan by Thursday." in found


def test_the_meeting_on_screen_comes_first(api) -> None:  # type: ignore[no-untyped-def]
    found = gather(api.services, "anything", {"meeting_id": "m-budget", "scope": "meeting"})
    assert found.index('"meeting_id":"m-budget"') < found.index('"count"')
