from __future__ import annotations

from pathlib import Path

import pytest

from app import meta
from app.asr.fake import FakeAsr
from app.errors import RecoverableError
from app.llm.client import FakeLlm
from app.pipeline.stages import assemble, deliver, render, summarize, transcribe
from app.pipeline.states import JobStage, MeetingState
from tests.fixtures.meetings import harness, write_chunks
from tests.fixtures.smtp import FakeSmtp


class Services:
    def __init__(self, **kwargs: object) -> None:
        self.asr = kwargs.get("asr") or FakeAsr()
        self.llm = kwargs.get("llm") or FakeLlm()
        self.notifier = kwargs.get("notifier")


class ExplodingLlm(FakeLlm):
    def complete_json(self, **kwargs: object):  # type: ignore[no-untyped-def, override]
        raise AssertionError("the render stage must never construct an LLM client")


class RecordingNotifier:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def summary_ready(self, meeting_id: str, title: str) -> None:
        self.calls.append((meeting_id, title))


def pipeline_to_notes(tmp_path: Path, **cfg):  # type: ignore[no-untyped-def]
    h = harness(tmp_path, asr__backend="fake", llm__provider="fake", audio__vad="energy", **cfg)
    meeting = h.meeting()
    write_chunks(meeting.path, seconds=200)
    services = Services()
    transcribe.run(h.context(meeting, services=services))
    assemble.run(h.context(meeting, JobStage.ASSEMBLE, services=services))
    summarize.run(h.context(meeting, JobStage.SUMMARIZE, services=services))
    return h, h.dao.require_meeting(meeting.id)


def test_render_writes_both_variants(tmp_path: Path) -> None:
    h, meeting = pipeline_to_notes(tmp_path)
    render.run(h.context(meeting, JobStage.RENDER, services=Services()))
    ui, email = render.output_paths(meeting.path)
    assert ui.exists() and email.exists()
    # No stylesheet of ours in either any more. The template that carried one is gone, so
    # whatever styling the page has is the prompt's own — injecting a reset would be the
    # app deciding how the document looks, which is the thing free-form removed.
    body = ui.read_text(encoding="utf-8")
    assert "<style" not in body and "<style" not in email.read_text(encoding="utf-8")
    assert 'dir="' in body, "direction still follows the summary language"


def test_rerender_without_llm(tmp_path: Path) -> None:
    """notes.json is the source of truth: restyling costs no tokens."""
    h, meeting = pipeline_to_notes(tmp_path)
    services = Services(llm=ExplodingLlm())
    render.run(h.context(meeting, JobStage.RENDER, services=services))
    ui, _email = render.output_paths(meeting.path)
    first = ui.read_bytes()
    ui.unlink()
    render.run(h.context(meeting, JobStage.RENDER, services=services))
    assert ui.read_bytes() == first


def test_render_is_idempotent(tmp_path: Path) -> None:
    h, meeting = pipeline_to_notes(tmp_path)
    ctx = h.context(meeting, JobStage.RENDER, services=Services())
    render.run(ctx)
    ui, _ = render.output_paths(meeting.path)
    stamp = ui.stat().st_mtime_ns
    render.run(ctx)
    assert ui.stat().st_mtime_ns == stamp


def test_draft_mode_does_not_send(tmp_path: Path) -> None:
    with FakeSmtp() as server:
        h, meeting = pipeline_to_notes(tmp_path)
        h.config.set("delivery.smtp.host", server.host)
        h.config.set("delivery.smtp.port", server.port)
        h.config.set("delivery.smtp.starttls", False)
        h.config.set("delivery.smtp.from_addr", "me@example.com")
        notifier = RecordingNotifier()
        services = Services(notifier=notifier)
        render.run(h.context(meeting, JobStage.RENDER, services=services))
        deliver.run(h.context(meeting, JobStage.DELIVER, services=services))
        assert server.received == []
        record = meta.read(meeting.path)["delivery"]
        assert record["mode"] == "draft" and record["sent"] is False
        assert record["deliverable"] is True
        assert notifier.calls and notifier.calls[0][0] == meeting.id


def test_smtp_send_to_fake_server(tmp_path: Path) -> None:
    with FakeSmtp() as server:
        h, meeting = pipeline_to_notes(tmp_path, delivery__mode="auto_send")
        h.config.set("delivery.smtp.host", server.host)
        h.config.set("delivery.smtp.port", server.port)
        h.config.set("delivery.smtp.starttls", False)
        h.config.set("delivery.smtp.from_addr", "me@example.com")
        h.config.set("delivery.recipients", ["team@example.com", "boss@example.com"])
        services = Services()
        render.run(h.context(meeting, JobStage.RENDER, services=services))
        deliver.run(h.context(meeting, JobStage.DELIVER, services=services))

        assert len(server.received) == 1
        received = server.received[0]
        assert received.rcpt_tos == ["team@example.com", "boss@example.com"]
        assert received.subject
        assert received.part("plain") and "<" not in received.part("plain")  # type: ignore[operator]
        html = received.part("html")
        # Inline styles are the model's business now; all the email must guarantee is a
        # HTML part that is not carrying a stylesheet a mail client would strip.
        assert html and "<style" not in html
        assert meta.read(meeting.path)["delivery"]["sent"] is True


def test_attach_transcript_when_opted_in(tmp_path: Path) -> None:
    with FakeSmtp() as server:
        h, meeting = pipeline_to_notes(
            tmp_path, delivery__mode="auto_send", delivery__attach_transcript=True
        )
        h.config.set("delivery.smtp.host", server.host)
        h.config.set("delivery.smtp.port", server.port)
        h.config.set("delivery.smtp.starttls", False)
        h.config.set("delivery.smtp.from_addr", "me@example.com")
        services = Services()
        render.run(h.context(meeting, JobStage.RENDER, services=services))
        deliver.run(h.context(meeting, JobStage.DELIVER, services=services))
        names = [
            part.get_filename() for part in server.received[0].message.walk() if part.get_filename()
        ]
        assert names == ["transcript.md"]


def test_smtp_failure_is_retryable(tmp_path: Path) -> None:
    with FakeSmtp() as server:
        server.refuse = True
        h, meeting = pipeline_to_notes(tmp_path, delivery__mode="auto_send")
        h.config.set("delivery.smtp.host", server.host)
        h.config.set("delivery.smtp.port", server.port)
        h.config.set("delivery.smtp.starttls", False)
        h.config.set("delivery.smtp.from_addr", "me@example.com")
        services = Services()
        render.run(h.context(meeting, JobStage.RENDER, services=services))
        for step in (
            MeetingState.TRANSCRIBING,
            MeetingState.TRANSCRIBED,
            MeetingState.SUMMARIZING,
            MeetingState.SUMMARIZED,
            MeetingState.RENDERED,
        ):
            h.dao.set_state(meeting.id, step)
        with pytest.raises(RecoverableError):
            deliver.run(h.context(meeting, JobStage.DELIVER, services=services))
        assert h.dao.require_meeting(meeting.id).state == MeetingState.RENDERED


def test_deliver_requires_rendered_html(tmp_path: Path) -> None:
    h, meeting = pipeline_to_notes(tmp_path)
    with pytest.raises(FileNotFoundError):
        deliver.run(h.context(meeting, JobStage.DELIVER, services=Services()))
