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


def test_a_hosted_model_is_not_split_into_windows(tmp_path: Path) -> None:
    """A 25 kB transcript went out as two windows and a merge: three calls where one
    would do, each 1.5 to 2 minutes through Claude Code. Null sizes it to the provider."""

    class ClaudeCode(FakeLlm):
        name = "claude-subscription"

    h, meeting = prepared(tmp_path / "auto")
    ctx = h.context(meeting, JobStage.SUMMARIZE, services=Services())
    assert summarize.window_tokens(ctx, ClaudeCode()) == 150_000
    assert summarize.window_tokens(ctx, FakeLlm()) == summarize.DEFAULT_WINDOW_TOKENS

    llm = ClaudeCode(chars_per_token=2.0)
    ctx = h.context(meeting, JobStage.SUMMARIZE, services=Services(llm=llm))
    ctx.meeting.path.joinpath("transcript.md").write_text("שלום עולם " * 2500, encoding="utf-8")
    summarize.run(ctx)
    assert len(llm.calls) == 1, "one call, no merge"

    h, meeting = prepared(tmp_path / "explicit", llm__window_tokens=8000)
    ctx = h.context(meeting, JobStage.SUMMARIZE, services=Services())
    assert summarize.window_tokens(ctx, ClaudeCode()) == 8000, "an explicit value wins"


def test_a_forced_summary_leaves_the_transcript_alone(tmp_path: Path) -> None:
    """Re-summarizing rewrites the notes and touches nothing upstream of them."""
    h, meeting = prepared(tmp_path)
    transcript = meeting.path / "transcript.md"
    before = (transcript.read_bytes(), transcript.stat().st_mtime_ns)

    summarize.run(h.context(meeting, JobStage.SUMMARIZE, services=Services()))
    first = summarize.load_notes(meeting.path)

    llm = FakeLlm()
    ctx = h.context(meeting, JobStage.SUMMARIZE, services=Services(llm=llm))
    ctx.force = True
    summarize.run(ctx)

    assert llm.calls, "the forced run never called the model"
    assert summarize.load_notes(meeting.path)["summary_html"]
    assert (transcript.read_bytes(), transcript.stat().st_mtime_ns) == before, (
        "the transcript was rewritten by a summary run"
    )
    assert first["summary_html"], "the first summary should still have been written"


def test_action_items_reach_the_database(tmp_path: Path) -> None:
    """The one piece of structure free-form still hands back (D47).

    The document stays the model's. What is added is that the commitments inside it
    are also returned as data, so the inbox can read across meetings — which
    `known-issues.md` #10 recorded as impossible against opaque HTML.
    """
    h, meeting = prepared(tmp_path)

    class WithItems(FakeLlm):
        def complete_json(self, **kwargs):  # type: ignore[no-untyped-def]
            from app.llm.client import LlmResult

            assert "action_items" in kwargs["schema"]["properties"], "the envelope must ask"
            return LlmResult(
                data={
                    "summary_html": "<h2>Decisions</h2><p>Roll it back.</p>",
                    "action_items": [
                        {"who": "ME", "what": "take it to Monday review", "at_ms": 31000},
                        {"who": "Dana", "what": "write the spec", "due": "Thursday"},
                    ],
                },
                model="fake",
                attempts=1,
            )

    summarize.run(h.context(meeting, JobStage.SUMMARIZE, services=Services(llm=WithItems())))

    stored = h.dao.action_items(meeting_id=meeting.id)
    assert [(item.who, item.what, item.due) for item in stored] == [
        ("ME", "take it to Monday review", None),
        ("Dana", "write the spec", "Thursday"),
    ]
    assert stored[0].mine is True and stored[0].at_ms == 31000
    # And in notes.json, which is this meeting's own record of itself.
    assert len(summarize.load_notes(meeting.path)["action_items"]) == 2


def test_a_model_that_ignores_the_request_still_produces_a_summary(tmp_path: Path) -> None:
    """The summary is the product; the list is a bonus on top of it."""
    h, meeting = prepared(tmp_path)

    class Silent(FakeLlm):
        def complete_json(self, **kwargs):  # type: ignore[no-untyped-def]
            from app.llm.client import LlmResult

            return LlmResult(data={"summary_html": "<p>A document.</p>"}, model="fake", attempts=1)

    summarize.run(h.context(meeting, JobStage.SUMMARIZE, services=Services(llm=Silent())))
    assert summarize.load_notes(meeting.path)["summary_html"] == "<p>A document.</p>"
    assert h.dao.action_items(meeting_id=meeting.id) == []


def test_resummarizing_keeps_the_ticks(tmp_path: Path) -> None:
    """Editing the prompt is routine; losing what you ticked off must not be."""
    h, meeting = prepared(tmp_path)
    summarize.run(h.context(meeting, JobStage.SUMMARIZE, services=Services()))
    first = h.dao.action_items(meeting_id=meeting.id)
    assert first, "the fake provider should offer something to tick"
    h.dao.set_action_done(first[0].id, done=True)

    ctx = h.context(meeting, JobStage.SUMMARIZE, services=Services())
    ctx.force = True
    summarize.run(ctx)

    again = h.dao.action_items(meeting_id=meeting.id)
    assert [item.what for item in again] == [item.what for item in first]
    assert again[0].done is True


def test_the_meeting_date_always_reaches_the_model(tmp_path: Path) -> None:
    """No calendar invitation, and still a date: "by Thursday" is unresolvable without
    knowing which week it was said in (D50)."""
    from app.due import anchor_date

    h, meeting = prepared(tmp_path)
    llm = FakeLlm()
    summarize.run(h.context(meeting, JobStage.SUMMARIZE, services=Services(llm=llm)))
    anchor = anchor_date(meeting.started_at)
    assert anchor is not None
    for call in llm.calls:
        assert call["user"].startswith(f"Date: {anchor.isoformat()} ({anchor:%A})")
        assert "Transcript:" in call["user"]


def test_dates_details_and_chapters_are_stored(tmp_path: Path) -> None:
    """What the fake stands in for: `detail` and `due_at` reach the row, and `chapters`
    reach notes.json in time order."""
    from app.due import anchor_date, resolve_due

    h, meeting = prepared(tmp_path)
    summarize.run(h.context(meeting, JobStage.SUMMARIZE, services=Services()))
    stored = h.dao.action_items(meeting_id=meeting.id)
    first = next(item for item in stored if item.who == "ME")
    assert first.detail == "so the numbers are in before the review"
    anchor = anchor_date(meeting.started_at)
    assert anchor is not None
    expected = resolve_due("this week", anchor)
    assert expected is not None and first.due_at == expected.isoformat()
    notes = summarize.load_notes(meeting.path)
    validate(notes)
    chapters = notes["chapters"]
    assert chapters and chapters[0]["start_ms"] == 0
    assert [c["start_ms"] for c in chapters] == sorted(c["start_ms"] for c in chapters)
    assert notes["summary_html"].startswith("<p>"), "a lead sentence, not a heading"


def test_a_spoken_due_without_a_date_is_resolved_at_store_time(tmp_path: Path) -> None:
    h, meeting = prepared(tmp_path)
    h.dao.update_meeting(meeting.id, started_at="2026-09-23T14:00:00")
    meeting = h.dao.require_meeting(meeting.id)

    class WordsOnly(FakeLlm):
        def complete_json(self, **kwargs):  # type: ignore[no-untyped-def]
            from app.llm.client import LlmResult

            return LlmResult(
                data={
                    "summary_html": "<p>Done.</p>",
                    "action_items": [
                        {"who": "Dana", "what": "write the spec", "due": "עד יום חמישי"},
                        {"who": "ME", "what": "chase legal", "due": "asap"},
                        {"who": "ME", "what": "book it", "due": "Friday", "due_at": "2026-10-09"},
                    ],
                },
                model="fake",
                attempts=1,
            )

    summarize.run(h.context(meeting, JobStage.SUMMARIZE, services=Services(llm=WordsOnly())))
    rows = h.dao.conn.execute(
        "SELECT what, due_at FROM action_items WHERE meeting_id = ? ORDER BY seq", (meeting.id,)
    ).fetchall()
    assert [(row["what"], row["due_at"]) for row in rows] == [
        ("write the spec", "2026-09-24"),
        ("chase legal", None),
        ("book it", "2026-10-09"),  # the model's own date is trusted over ours
    ]


def test_windowed_chapters_are_concatenated_when_the_merge_has_none(tmp_path: Path) -> None:
    h, meeting = prepared(tmp_path, llm__window_tokens=200)

    class Windowed(FakeLlm):
        def complete_json(self, **kwargs):  # type: ignore[no-untyped-def]
            from app.llm.client import LlmResult

            merging = kwargs["user"].startswith("Merge these")
            index = len(self.calls)
            self.calls.append(kwargs)
            data: dict[str, object] = {"summary_html": f"<p>part {index}</p>"}
            if not merging:
                data["chapters"] = [{"title": f"Part {index}", "start_ms": index * 60000}]
            return LlmResult(data=data, model="fake", attempts=1)

    llm = Windowed()
    summarize.run(h.context(meeting, JobStage.SUMMARIZE, services=Services(llm=llm)))
    assert len(llm.calls) > 2, "the transcript should have been split"
    chapters = summarize.load_notes(meeting.path)["chapters"]
    windows = len(llm.calls) - 1
    assert [c["title"] for c in chapters] == [f"Part {i}" for i in range(windows)]
    from itertools import pairwise

    assert all(c["end_ms"] == n["start_ms"] for c, n in pairwise(chapters))


def test_the_shipped_prompt_asks_for_the_lead_and_the_next_step() -> None:
    text = " ".join(load_prompt("system").text.split())
    assert '<p class="next">' in text
    assert "no heading before it" in text
    assert "`chapters`" in text and "`due_at`" in text and "`detail`" in text
