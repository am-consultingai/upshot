"""File jobs in the running app: the tray, /api/status and retention (D86)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from app import retention
from app.clock import iso
from app.transcription.types import Options
from app.tray import TrayApp
from tests.fixtures.api import build_harness


def add(services, state: str | None = None):  # type: ignore[no-untyped-def]
    store = services.transcriptions
    job = store.create(
        source_name="clip.wav", source_kind="path", source_path="/x/clip.wav",
        options=Options(), client="api",
    )  # fmt: skip
    if state is not None:
        store.conn.execute("UPDATE transcriptions SET state=? WHERE id=?", (state, job.id))
    return store.require(job.id)


def test_a_waiting_file_job_makes_the_app_busy(tmp_path: Path, app_home: Path) -> None:
    h = build_harness(tmp_path)
    add(h.services)
    state = TrayApp(h.services).observe()
    assert state.processing and state.queue_depth == 1
    assert h.client().get("/api/status").json()["queue_depth"] == 1


def test_a_failed_file_job_is_not_a_meeting_error(tmp_path: Path, app_home: Path) -> None:
    """The tray's error state would otherwise stay on for the 30 days until retention."""
    h = build_harness(tmp_path)
    add(h.services, state="failed")
    state = TrayApp(h.services).observe()
    assert not state.error and state.queue_depth == 0


def test_old_file_jobs_are_swept_with_meeting_retention_off(tmp_path: Path, app_home: Path) -> None:
    h = build_harness(
        tmp_path,
        retention__audio_days=None,
        retention__transcript_days=None,
        transcription__keep_days=30,
    )
    svc = h.services
    store = svc.transcriptions
    old, recent, waiting = add(svc, "done"), add(svc, "done"), add(svc)
    for job in (old, recent, waiting):
        (store.folder(job.id)).mkdir(parents=True)
        (store.folder(job.id) / "result.json").write_text("{}", encoding="utf-8")
    long_ago = iso(svc.clock.now() - timedelta(days=31))
    store.conn.execute("UPDATE transcriptions SET finished_at=? WHERE id=?", (long_ago, old.id))
    store.conn.execute(
        "UPDATE transcriptions SET finished_at=? WHERE id=?", (iso(svc.clock.now()), recent.id)
    )

    shown = h.client().get("/api/retention").json()["transcriptions"]
    assert shown["keep_days"] == 30
    assert [item["id"] for item in shown["doomed"]] == [old.id]

    result = retention.sweep_services(svc)
    assert result.transcriptions_removed == 1
    assert store.get(old.id) is None and not store.folder(old.id).exists()
    assert store.get(recent.id) is not None and store.get(waiting.id) is not None


def test_keep_days_off_keeps_them(tmp_path: Path, app_home: Path) -> None:
    h = build_harness(tmp_path, transcription__keep_days=None)
    job = add(h.services, "done")
    long_ago = iso(h.services.clock.now() - timedelta(days=400))
    h.services.transcriptions.conn.execute(
        "UPDATE transcriptions SET finished_at=? WHERE id=?", (long_ago, job.id)
    )
    assert retention.sweep_services(h.services).transcriptions_removed == 0


def test_an_update_waits_for_a_file_job(app_home: Path) -> None:
    """The PR #1 review: the installer counted meeting jobs only, so after ten quiet
    minutes an update quit Upshot under a running file job (and the bridge Claude was
    waiting on). The real service graph, as the app builds it."""
    from app.services import build

    services = build(with_worker=False, with_recorder=False)
    try:
        installer = services.installer
        assert installer.jobs_busy() is False
        job = add(services)
        assert installer.jobs_busy() is True, "a file job due now holds the update"
        services.transcriptions.claim(job.id)
        assert installer.jobs_busy() is True, "and so does one running"
        assert installer.why_not_now() == "jobs"  # what Settings says is in the way
        services.transcriptions.mark_cancelled(job.id)
        assert installer.jobs_busy() is False
    finally:
        services.conn.close()
