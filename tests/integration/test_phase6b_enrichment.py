from __future__ import annotations

import ast
import time
from pathlib import Path

import pytest

from app.asr.fake import FakeAsr
from app.enrich.factory import make_source
from app.enrich.fake import FakeSource
from app.enrich.null import NullSource
from app.enrich.source import Enrichment
from app.meetings import MeetingService
from app.pipeline.stages import transcribe
from app.pipeline.states import MeetingState
from tests.fixtures.meetings import harness, write_chunks


class Services:
    def __init__(self, asr: FakeAsr) -> None:
        self.asr = asr


def service(h, source=None) -> MeetingService:  # type: ignore[no-untyped-def]
    return MeetingService(h.config, h.dao, h.queue, clock=h.clock, source=source)


def test_null_source_is_default(tmp_path: Path) -> None:
    h = harness(tmp_path, asr__backend="fake", audio__vad="energy")
    assert make_source(h.config).name == "null"
    svc = service(h)
    assert isinstance(svc.enrichment_source, NullSource)
    meeting = svc.create(source="manual")
    assert meeting.calendar_json is None
    write_chunks(meeting.path, seconds=200)
    svc.finish(meeting.id, duration_s=200)
    assert h.dao.require_meeting(meeting.id).state == MeetingState.RECORDED
    assert h.queue.get_by_stage(meeting.id, "transcribe") is not None


def test_fake_source_fills_empty_fields(tmp_path: Path) -> None:
    h = harness(tmp_path)
    fake = FakeSource()
    meeting = service(h, fake).create(source="manual")
    assert meeting.title == "Weekly Sync"
    assert meeting.title_source == "calendar"
    assert meeting.calendar_json and "יוסי כהן" in meeting.calendar_json
    assert fake.calls, "the source was asked exactly once, at creation"
    assert len(fake.calls) == 1


def test_enrichment_never_overwrites(tmp_path: Path) -> None:
    h = harness(tmp_path)
    meeting = service(h, FakeSource()).create(source="manual", title="My own name")
    assert meeting.title == "My own name"
    assert meeting.title_source is None
    assert meeting.calendar_json, "the payload is still stored verbatim"


def test_enrichment_timeout_ignored(tmp_path: Path) -> None:
    h = harness(tmp_path)
    slow = FakeSource(delay_s=5.0)
    started = time.monotonic()
    svc = service(h, slow)
    meeting = svc.create(source="manual")
    elapsed = time.monotonic() - started
    assert elapsed < 2.5, f"commit waited {elapsed:.2f}s on a slow enrichment source"
    assert meeting.title is None
    assert meeting.calendar_json is None
    assert svc.warnings and "timed out" in svc.warnings[0]


def test_enrichment_exception_ignored(tmp_path: Path) -> None:
    h = harness(tmp_path, asr__backend="fake", audio__vad="energy")
    svc = service(h, FakeSource(raises=RuntimeError("calendar exploded")))
    meeting = svc.create(source="manual")
    assert meeting.calendar_json is None
    assert svc.warnings and "exploded" in svc.warnings[0]
    write_chunks(meeting.path, seconds=200)
    svc.finish(meeting.id, duration_s=200)
    assert h.dao.require_meeting(meeting.id).state == MeetingState.RECORDED


def test_participants_reach_initial_prompt(tmp_path: Path) -> None:
    h = harness(tmp_path, asr__backend="fake", audio__vad="energy")
    enriched = service(h, FakeSource()).create(source="manual")
    write_chunks(enriched.path, seconds=90)
    backend = FakeAsr()
    transcribe.run(h.context(enriched, services=Services(backend)))
    prompts = [call["initial_prompt"] for call in backend.transcribe_calls]
    assert any(p and "יוסי כהן" in p for p in prompts)

    plain = service(h).create(source="manual")
    write_chunks(plain.path, seconds=90)
    other = FakeAsr()
    transcribe.run(h.context(plain, services=Services(other)))
    assert all(
        not p or "יוסי כהן" not in p for p in [c["initial_prompt"] for c in other.transcribe_calls]
    )


def test_enrichment_with_a_source_returning_none(tmp_path: Path) -> None:
    h = harness(tmp_path)
    empty = FakeSource(enrichment=None)
    empty.enrichment = None  # type: ignore[assignment]
    meeting = service(h, empty).create(source="manual")
    assert meeting.calendar_json is None


def test_enrichment_dataclass_defaults() -> None:
    enrichment = Enrichment()
    assert enrichment.title is None
    assert enrichment.participants == () and enrichment.recipients == ()
    assert enrichment.as_raw()["participants"] == []


def test_no_google_imports() -> None:
    """V1 ships with zero Google integration — not deferred, absent."""
    offenders: list[str] = []
    for path in sorted(Path("app").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                root = name.split(".")[0]
                if root in {"google", "googleapiclient", "google_auth_oauthlib", "gspread"}:
                    offenders.append(f"{path}: {name}")
    assert offenders == [], offenders


@pytest.mark.parametrize("word", ["oauth", "client_secret", "refresh_token"])
def test_no_oauth_flow_in_this_build(word: str) -> None:
    hits = [
        str(path)
        for path in sorted(Path("app").rglob("*.py"))
        if word in path.read_text(encoding="utf-8").lower()
        and "google_refresh_token" not in path.read_text(encoding="utf-8")
    ]
    assert hits == [], hits
