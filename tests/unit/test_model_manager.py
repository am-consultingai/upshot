"""The speech model is fetched in the open: progress, space check, cancel, checksum."""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from typing import Any

import pytest

from app.asr import model_manager
from app.asr.model_manager import ModelManager, RemoteFile, target_for
from app.asr.models import REPO, resolve
from app.config import default_config

MODEL = b"m" * 4096
CONFIG = b'{"model": "tiny"}'
FILES = {
    "model.bin": RemoteFile(len(MODEL), hashlib.sha256(MODEL).hexdigest()),
    "config.json": RemoteFile(len(CONFIG)),
}


def lister(repo: str) -> dict[str, RemoteFile]:
    return dict(FILES)


def writing(model: bytes = MODEL, gate: threading.Event | None = None) -> Any:
    """A stand-in for the hub: writes the files in blocks, reporting each one."""

    def download(repo: str, target: Path, progress: type) -> None:
        bar = progress(total=len(model) + len(CONFIG), initial=0, unit="B")
        with (target / "model.bin").open("wb") as out:
            for at in range(0, len(model), 1024):
                if gate is not None:
                    gate.wait(5)
                out.write(model[at : at + 1024])
                bar.update(len(model[at : at + 1024]))
        (target / "config.json").write_bytes(CONFIG)
        bar.update(len(CONFIG))

    return download


def test_the_model_lands_in_the_app_home_and_resolve_finds_it(app_home: Path) -> None:
    manager = ModelManager(REPO, home=app_home, lister=lister, downloader=writing())
    assert manager.status().state == "missing"
    status = manager.start()
    assert status.state in ("downloading", "ready")
    status = manager.wait(5)
    assert status.state == "ready", status.error
    assert status.done_bytes == status.total_bytes == len(MODEL) + len(CONFIG)
    assert Path(status.path) == target_for(REPO, app_home)
    choice = resolve(default_config())
    assert choice.local and Path(choice.reference) == target_for(REPO, app_home)


def test_too_little_space_fails_before_fetching_anything(
    app_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetched: list[str] = []
    monkeypatch.setattr(model_manager, "free_bytes", lambda path: 10)
    manager = ModelManager(
        REPO, home=app_home, lister=lister, downloader=lambda *a: fetched.append("x")
    )
    status = manager.start()
    status = manager.wait(5)
    assert status.state == "failed"
    assert "GB free" in status.error
    assert status.code == "no_space"  # what the setup screen words for itself
    assert fetched == []


def test_cancel_stops_the_download_and_a_restart_resumes(app_home: Path) -> None:
    gate = threading.Event()
    manager = ModelManager(REPO, home=app_home, lister=lister, downloader=writing(gate=gate))
    manager.start()
    manager.cancel()
    gate.set()
    assert manager.wait(5).state == "cancelled"

    manager.downloader = writing()
    manager.start()
    assert manager.wait(5).state == "ready"


def test_a_file_that_does_not_match_its_checksum_is_removed(app_home: Path) -> None:
    manager = ModelManager(
        REPO, home=app_home, lister=lister, downloader=writing(model=b"x" * len(MODEL))
    )
    manager.start()
    status = manager.wait(5)
    assert status.state == "failed"
    assert "checksum" in status.error
    assert not (target_for(REPO, app_home) / "model.bin").exists()


def test_files_from_a_stopped_download_are_not_a_model(app_home: Path) -> None:
    target = target_for(REPO, app_home)
    target.mkdir(parents=True)
    (target / "model.bin").write_bytes(MODEL[:100])
    (target / "config.json").write_bytes(CONFIG)
    manager = ModelManager(REPO, home=app_home, lister=lister, downloader=writing())
    assert manager.status().state == "missing"
    assert not resolve(default_config()).local


def test_ensure_downloads_then_returns_the_folder(app_home: Path) -> None:
    manager = ModelManager(REPO, home=app_home, lister=lister, downloader=writing())
    assert manager.ensure() == target_for(REPO, app_home)
    failing = ModelManager("other/repo", home=app_home, lister=lister, downloader=lambda *a: None)
    with pytest.raises(RuntimeError, match=r"could not be downloaded: model\.bin is missing"):
        failing.ensure()


def test_a_missing_model_is_fetched_through_the_manager_not_faster_whisper(app_home: Path) -> None:
    from app.asr.local import LocalAsr

    built: list[dict[str, Any]] = []
    fetched: list[str] = []

    def fetch(repo: str) -> Path:
        fetched.append(repo)
        return app_home / "fetched"

    class Model:
        def transcribe(self, *args: Any, **kwargs: Any) -> tuple[list[Any], Any]:
            return [], None

    def factory(**kwargs: Any) -> Model:
        built.append(kwargs)
        return Model()

    config = default_config()
    config.set("asr.device", "cpu")
    backend = LocalAsr(config, model_factory=factory, fetch=fetch)
    backend.load()
    assert fetched == [REPO]
    assert built[0]["model_size_or_path"] == str(app_home / "fetched")


def test_the_model_api_reports_and_starts_the_download(
    tmp_path: Path, app_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.fixtures.api import build_harness

    monkeypatch.setattr(model_manager, "_managers", {})
    api = build_harness(tmp_path)
    api.services.config.set("asr.device", "cpu")
    client = api.client()

    # The API finds this same manager; give it stand-ins for the hub.
    manager = model_manager.manager_for(REPO)
    manager.lister, manager.downloader = lister, writing()

    before = client.get("/api/model").json()
    assert before["state"] == "missing" and before["repo"] == REPO
    started = client.post("/api/model/download").json()
    assert started["state"] in ("downloading", "ready")
    manager.wait(5)
    after = client.get("/api/model").json()
    assert after["state"] == "ready" and after["done_bytes"] == after["total_bytes"]


def test_a_configured_model_needs_no_download(
    tmp_path: Path, app_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.fixtures.api import build_harness

    monkeypatch.setattr(model_manager, "_managers", {})
    model = tmp_path / "my-model"
    model.mkdir()
    (model / "model.bin").write_bytes(b"x")
    (model / "config.json").write_bytes(b"{}")
    api = build_harness(tmp_path)
    api.services.config.set("asr.model_path", str(model))
    body = api.client().get("/api/model").json()
    assert body["state"] == "ready" and body["path"] == str(model)


def test_status_survives_a_data_folder_on_a_missing_drive(tmp_path: Path, app_home: Path) -> None:
    from tests.fixtures.api import build_harness

    api = build_harness(tmp_path)
    api.services.config.set("data_root", str(tmp_path / "unplugged" / "drive" / "meetings"))
    response = api.client().get("/api/status")
    assert response.status_code == 200
