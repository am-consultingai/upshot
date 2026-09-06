from __future__ import annotations

from pathlib import Path

import pytest

from app import meta
from app.asr.fake import FakeAsr
from app.llm.client import FakeLlm
from app.llm.prompts import load as load_prompt
from app.llm.schema import validate
from app.pipeline.stages import assemble, summarize, transcribe
from app.pipeline.states import JobStage
from tests.fixtures.meetings import harness, write_chunks


class Services:
    def __init__(self, asr: FakeAsr | None = None, llm: FakeLlm | None = None) -> None:
        self.asr = asr or FakeAsr()
        self.llm = llm or FakeLlm()


def prepared(tmp_path: Path, *, seconds: float = 200.0, duration_s: int | None = None, **cfg):  # type: ignore[no-untyped-def]
    h = harness(tmp_path, asr__backend="fake", llm__provider="fake", audio__vad="energy", **cfg)
    meeting = h.meeting()
    write_chunks(meeting.path, seconds=seconds)
    services = Services()
    transcribe.run(h.context(meeting, services=services))
    assemble.run(h.context(meeting, JobStage.ASSEMBLE, services=services))
    if duration_s is not None:
        h.dao.update_meeting(meeting.id, duration_s=duration_s)
    return h, h.dao.require_meeting(meeting.id)


def test_summarize_writes_the_prompts_document(tmp_path: Path) -> None:
    """One field, and it holds whatever the prompt produced.

    The nine-field schema, the map/reduce merge and the sanity gates that used to be
    checked here are gone with the structured path: all three read fixed fields, so all
    three constrained the shape of a document the prompt was supposed to own.
    """
    h, meeting = prepared(tmp_path)
    llm = FakeLlm()
    summarize.run(h.context(meeting, JobStage.SUMMARIZE, services=Services(llm=llm)))
    notes = summarize.load_notes(meeting.path)
    validate(notes)
    assert notes["summary_html"], "the document itself"
    payload = meta.read(meeting.path)
    assert payload["prompt_versions"]["format"] == "free"
    assert payload["summary_language"] == "he"


def test_summary_language_resolution(tmp_path: Path) -> None:
    for configured, expected in (("en", "en"), ("he", "he"), ("auto", "he")):
        h, meeting = prepared(tmp_path / configured, summary__language=configured)
        ctx = h.context(meeting, JobStage.SUMMARIZE, services=Services())
        assert summarize.resolve_summary_language(ctx) == expected
        summarize.run(ctx)
        assert h.dao.require_meeting(meeting.id).summary_language == expected


def test_output_language_reaches_the_request(tmp_path: Path) -> None:
    h, meeting = prepared(tmp_path, summary__language="he")
    llm = FakeLlm()
    summarize.run(h.context(meeting, JobStage.SUMMARIZE, services=Services(llm=llm)))
    for call in llm.calls:
        system_text = "\n".join(block["text"] for block in call["system"])
        assert "Hebrew (he)" in system_text


def test_summarize_is_idempotent(tmp_path: Path) -> None:
    h, meeting = prepared(tmp_path)
    llm = FakeLlm()
    services = Services(llm=llm)
    summarize.run(h.context(meeting, JobStage.SUMMARIZE, services=services))
    calls = len(llm.calls)
    first = summarize.notes_path(meeting.path).read_bytes()
    summarize.run(h.context(meeting, JobStage.SUMMARIZE, services=services))
    assert len(llm.calls) == calls
    assert summarize.notes_path(meeting.path).read_bytes() == first


def test_missing_transcript_fails_loudly(tmp_path: Path) -> None:
    h = harness(tmp_path, llm__provider="fake")
    meeting = h.meeting()
    meeting.path.mkdir(parents=True, exist_ok=True)
    with pytest.raises(FileNotFoundError):
        summarize.run(h.context(meeting, JobStage.SUMMARIZE, services=Services()))


def test_editing_the_prompt_rebuilds_the_notes(tmp_path: Path) -> None:
    """Press Summarize after editing the prompt and the notes must actually be rebuilt.

    Staleness was measured against the transcript alone. The transcript had not moved, so
    the stage logged "notes.json is current; skipping" and the previous summary stood —
    the prompt editor appeared to do nothing at all.
    """
    h, meeting = prepared(tmp_path)

    def summarize_once() -> str:
        llm = FakeLlm()
        summarize.run(h.context(meeting, JobStage.SUMMARIZE, services=Services(llm=llm)))
        return str(meta.read(meeting.path)["prompt_versions"]["system"])

    # Read, not pinned: revising the shipped prompt is routine and must not fail here.
    shipped = load_prompt("system").version
    assert summarize_once() == shipped, "the shipped prompt, by its own version"
    assert summarize_once() == shipped, "unchanged: skipping here is correct"

    h.config.set("llm.summary_prompt", "Be extremely terse.")
    first_edit = summarize_once()
    assert first_edit.startswith("custom:"), "an edited prompt must be identifiable"

    # A second, different edit has to count too: a bare "custom" marker would make every
    # edit after the first look identical, and the notes would never be rebuilt again.
    h.config.set("llm.summary_prompt", "Be extremely terse, in bullets.")
    assert summarize_once() != first_edit


def test_a_forced_run_ignores_everything_on_disk(tmp_path: Path) -> None:
    """Pressing Summarize means redo it — not "redo it if you think it is stale".

    Every guard here had at some point decided, on the user's behalf, that the summary
    already on disk would do: first against the transcript, then against the prompt. A
    button press outranks all of them.
    """
    h, meeting = prepared(tmp_path)
    llm = FakeLlm()
    summarize.run(h.context(meeting, JobStage.SUMMARIZE, services=Services(llm=llm)))
    first = len(llm.calls)
    assert first >= 1

    # Nothing has changed at all: not the transcript, not the prompt.
    again = FakeLlm()
    summarize.run(h.context(meeting, JobStage.SUMMARIZE, services=Services(llm=again)))
    assert again.calls == [], "unforced and unchanged, it is right to skip"

    forced = FakeLlm()
    ctx = h.context(meeting, JobStage.SUMMARIZE, services=Services(llm=forced))
    ctx.force = True
    summarize.run(ctx)
    assert len(forced.calls) == first, "forced: the model is asked again regardless"


def test_free_form_lets_the_prompt_decide_everything(tmp_path: Path) -> None:
    """The structured schema and the template are ours, and in free mode neither applies.

    A prompt asking for a differently shaped document could previously have no visible
    effect at all: nine fixed fields with `additionalProperties: False`, then a Jinja
    template that hardcoded the headings. Free mode keeps only the JSON envelope, which
    is what makes an answer extractable across providers.
    """
    from app.pipeline.stages import render

    h, meeting = prepared(tmp_path, llm__summary_format="free")

    class Designed(FakeLlm):
        def complete_json(self, **kwargs):  # type: ignore[no-untyped-def]
            from app.llm.client import LlmResult

            return LlmResult(
                data={"summary_html": "<section class='card'><h9>Anything</h9></section>"},
                model="fake",
                attempts=1,
            )

    summarize.run(h.context(meeting, JobStage.SUMMARIZE, services=Services(llm=Designed())))
    notes = summarize.load_notes(meeting.path)
    assert "summary_html" in notes, "the model's own document, not our nine fields"

    render.run(h.context(meeting, JobStage.RENDER, services=Services()))
    html = (meeting.path / "summary.html").read_text(encoding="utf-8")
    assert "<h9>Anything</h9>" in html, "passed through, not relaid out by the template"
    assert "labels" not in html
