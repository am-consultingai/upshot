"""A Spanish, an Arabic, a Russian and a Chinese meeting, end to end (story D).

Fake ASR in the meeting's language and the fake LLM: transcribe → assemble → summarize →
render, then the API and search. The notes are asked for in the meeting's language; the
page runs the right way; the interface stays in the interface language (the rendered
page carries no chrome of its own); the words are findable in their own script.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.asr.fake import FakeAsr
from app.llm.client import FakeLlm
from app.pipeline.stages import assemble, render, summarize, transcribe
from app.pipeline.states import JobStage
from tests.fixtures.meetings import harness, write_chunks


class Services:
    def __init__(self, language: str) -> None:
        self.asr = FakeAsr(language=language)
        self.llm = FakeLlm()


def through_render(tmp_path: Path, language: str, **cfg: object):  # type: ignore[no-untyped-def]
    h = harness(tmp_path, asr__backend="fake", llm__provider="fake", audio__vad="energy", **cfg)
    meeting = h.meeting()
    write_chunks(meeting.path, seconds=60)
    services = Services(language)
    transcribe.run(h.context(meeting, services=services))
    meeting = h.dao.require_meeting(meeting.id)
    assemble.run(h.context(meeting, JobStage.ASSEMBLE, services=services))
    summarize.run(h.context(meeting, JobStage.SUMMARIZE, services=services))
    meeting = h.dao.require_meeting(meeting.id)
    render.run(h.context(meeting, JobStage.RENDER, services=services))
    return h, h.dao.require_meeting(meeting.id), services


@pytest.mark.parametrize(
    ("language", "name", "direction"),
    [
        ("es", "Spanish", "ltr"),
        ("ar", "Arabic", "rtl"),
        ("ru", "Russian", "ltr"),
        ("zh", "Chinese", "ltr"),
    ],
)
def test_a_meeting_goes_end_to_end_in_its_language(
    tmp_path: Path, language: str, name: str, direction: str
) -> None:
    _h, meeting, services = through_render(tmp_path, language, ui__language="he")
    assert meeting.language == language
    assert services.asr.roles == ["other"], "stock large-v3, not ivrit"
    system = "\n".join(b["text"] for call in services.llm.calls for b in call["system"])
    assert f"Write the notes in {name} ({language})" in system
    assert meeting.summary_language == language
    page = (meeting.path / render.UI_NAME).read_text(encoding="utf-8")
    assert page.startswith(f'<div dir="{direction}" lang="{language}" class="ma-free">')
    # No headings of its own: the chrome around it is the interface's (he here).
    for label in ("TL;DR", "תקציר", "Action items", "משימות"):
        assert label not in page.split(">", 1)[0]


def test_a_hebrew_meeting_is_unchanged(tmp_path: Path) -> None:
    _h, meeting, services = through_render(tmp_path, "he")
    assert (meeting.language, services.asr.roles) == ("he", ["hebrew"])
    page = (meeting.path / render.UI_NAME).read_text(encoding="utf-8")
    assert page.startswith('<div dir="rtl" lang="he"')


def test_the_api_says_which_way_each_meeting_runs(tmp_path: Path) -> None:
    from tests.fixtures.api import build_harness

    api = build_harness(tmp_path)
    client = api.client()
    ids = {}
    for language in ("ar", "es", "he"):
        meeting = api.services.meetings.create(title=f"meeting {language}")
        api.services.dao.update_meeting(meeting.id, language=language)
        ids[language] = meeting.id
    directions = {
        lang: client.get(f"/api/meetings/{mid}").json()["direction"] for lang, mid in ids.items()
    }
    assert directions == {"ar": "rtl", "es": "ltr", "he": "rtl"}
    # A Hebrew meeting summarized in English: the notes run left to right.
    api.services.dao.update_meeting(ids["he"], summary_language="en")
    body = client.get(f"/api/meetings/{ids['he']}").json()
    assert (body["direction"], body["summary_direction"]) == ("rtl", "ltr")


def test_the_language_list_is_every_whisper_language(tmp_path: Path) -> None:
    from tests.fixtures.api import build_harness

    body = build_harness(tmp_path).client().get("/api/languages").json()
    codes = {item["code"] for item in body["languages"]}
    assert len(codes) == 100 and {"he", "en", "ar", "yue"} <= codes
    assert {"code": "es", "name": "Spanish", "native": "Español"} in body["languages"]


@pytest.mark.parametrize(
    ("language", "query"),
    [("ru", "развёртыванием"), ("ar", "المشروع"), ("zh", "项目的"), ("es", "despliegue")],
)
def test_search_finds_words_in_their_own_script(tmp_path: Path, language: str, query: str) -> None:
    h, meeting, _services = through_render(tmp_path, language)
    hits = h.dao.search(query)
    assert any(hit.meeting_id == meeting.id for hit in hits), (query, hits)
