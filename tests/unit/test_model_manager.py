"""The speech models are fetched in the open: progress, space check, cancel, checksum,
pinned revisions, all three in order."""

from __future__ import annotations

import hashlib
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from app.asr import model_manager
from app.asr.model_manager import ModelManager, ModelSet, RemoteFile, target_for
from app.asr.models import CLASSIFIER, HEBREW, MODELS, OTHER, ROLES, SpeechModel, resolve
from app.config import default_config

MODEL = b"m" * 4096
CONFIG = b'{"model": "tiny"}'
FILES = {
    "model.bin": RemoteFile(len(MODEL), hashlib.sha256(MODEL).hexdigest()),
    "config.json": RemoteFile(len(CONFIG)),
}


HEB = MODELS[HEBREW]
REPO = HEB.repo


def lister(repo: str, revision: str) -> dict[str, RemoteFile]:
    return dict(FILES)


def writing(model: bytes = MODEL, gate: threading.Event | None = None) -> Any:
    """A stand-in for the hub: writes the files in blocks, reporting each one."""

    def download(model_: SpeechModel, target: Path, progress: type) -> None:
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
    manager = ModelManager(HEB, home=app_home, lister=lister, downloader=writing())
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
        HEB, home=app_home, lister=lister, downloader=lambda *a: fetched.append("x")
    )
    status = manager.start()
    status = manager.wait(5)
    assert status.state == "failed"
    assert "GB free" in status.error
    assert status.code == "no_space"  # what the setup screen words for itself
    assert fetched == []


def test_cancel_stops_the_download_and_a_restart_resumes(app_home: Path) -> None:
    gate = threading.Event()
    manager = ModelManager(HEB, home=app_home, lister=lister, downloader=writing(gate=gate))
    manager.start()
    manager.cancel()
    gate.set()
    assert manager.wait(5).state == "cancelled"

    manager.downloader = writing()
    manager.start()
    assert manager.wait(5).state == "ready"


def test_a_file_that_does_not_match_its_checksum_is_removed(app_home: Path) -> None:
    manager = ModelManager(
        HEB, home=app_home, lister=lister, downloader=writing(model=b"x" * len(MODEL))
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
    manager = ModelManager(HEB, home=app_home, lister=lister, downloader=writing())
    assert manager.status().state == "missing"
    assert not resolve(default_config()).local


def test_ensure_downloads_then_returns_the_folder(app_home: Path) -> None:
    manager = ModelManager(HEB, home=app_home, lister=lister, downloader=writing())
    assert manager.ensure() == target_for(REPO, app_home)
    failing = ModelManager(
        SpeechModel("x", "other/repo", "r", 1),
        home=app_home,
        lister=lister,
        downloader=lambda *a: None,
    )
    with pytest.raises(RuntimeError, match=r"could not be downloaded: model\.bin is missing"):
        failing.ensure()


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("damage", ["folder", "marker"])
def test_a_missing_model_is_a_broken_installation_and_nothing_is_downloaded(
    app_home: Path, monkeypatch: pytest.MonkeyPatch, role: str, damage: str
) -> None:
    """R12: models come from installation only. A model deleted after install is
    reported by name; no download function is called, and no other model stands in."""
    import shutil

    from app.asr.local import LocalAsr
    from app.asr.models import ModelNotInstalled, check_installed, missing_roles
    from tests.fixtures.models import install_models

    placed = install_models(app_home)
    if damage == "folder":
        shutil.rmtree(placed[role])
    else:
        (placed[role] / model_manager.VERIFIED).unlink()

    def no_downloads(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a download was attempted")

    monkeypatch.setattr(model_manager, "http_fetch", no_downloads)
    monkeypatch.setattr(model_manager, "hub_lister", no_downloads)
    monkeypatch.setattr(model_manager.ModelManager, "start", no_downloads)
    monkeypatch.setattr(model_manager.ModelManager, "ensure", no_downloads)
    built: list[Any] = []

    config = default_config(asr__device="cpu")
    assert missing_roles(config) == [role]
    with pytest.raises(ModelNotInstalled, match=MODELS[role].repo) as caught:
        check_installed(config)
    assert "installer again" in str(caught.value)
    if role != CLASSIFIER:
        with pytest.raises(ModelNotInstalled):
            LocalAsr(config, role=role, model_factory=lambda **kw: built.append(kw)).load()
        assert built == [], "no model is loaded in its place"


def test_the_startup_check_names_the_missing_role(
    tmp_path: Path, app_home: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import shutil

    from app.main import _check_speech_models
    from tests.fixtures.api import build_harness
    from tests.fixtures.models import install_models

    placed = install_models(app_home)
    shutil.rmtree(placed[OTHER])
    api = build_harness(tmp_path)
    api.services.config.set("asr.backend", "local")
    with caplog.at_level("ERROR"):
        assert _check_speech_models(api.services) == [OTHER]
    assert any(MODELS[OTHER].repo in record.getMessage() for record in caplog.records)


def stub_all(home: Path | None = None, **kwargs: Any) -> dict[str, ModelManager]:
    """The shared managers of all three models, with stand-ins for the hub."""
    managers = {role: model_manager.manager_for(MODELS[role], home) for role in ROLES}
    for manager in managers.values():
        manager.lister = kwargs.get("lister", lister)
        manager.downloader = kwargs.get("downloader", writing())
    return managers


def test_the_model_api_reports_and_starts_the_download(
    tmp_path: Path, app_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.fixtures.api import build_harness

    monkeypatch.setattr(model_manager, "_managers", {})
    monkeypatch.setattr(model_manager, "_sets", {})
    api = build_harness(tmp_path)
    api.services.config.set("asr.device", "cpu")
    client = api.client()

    # The API finds these same managers; give them stand-ins for the hub.
    stub_all()

    before = client.get("/api/model").json()
    assert before["state"] == "missing"
    assert [row["role"] for row in before["models"]] == list(ROLES)
    assert all(m.repo in before["repo"] for m in MODELS.values())
    started = client.post("/api/model/download").json()
    assert started["state"] in ("downloading", "ready")
    model_manager.model_set(api.services.config).wait(5)
    after = client.get("/api/model").json()
    assert after["state"] == "ready" and after["done_bytes"] == after["total_bytes"]
    assert {row["state"] for row in after["models"]} == {"ready"}


def test_a_configured_hebrew_model_needs_no_download_but_the_others_do(
    tmp_path: Path, app_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.fixtures.api import build_harness

    monkeypatch.setattr(model_manager, "_managers", {})
    monkeypatch.setattr(model_manager, "_sets", {})
    model = tmp_path / "my-model"
    model.mkdir()
    (model / "model.bin").write_bytes(b"x")
    (model / "config.json").write_bytes(b"{}")
    api = build_harness(tmp_path)
    api.services.config.set("asr.model_path", str(model))
    body = api.client().get("/api/model").json()
    rows = {row["role"]: row["state"] for row in body["models"]}
    assert rows == {CLASSIFIER: "missing", HEBREW: "ready", OTHER: "missing"}


def test_status_survives_a_data_folder_on_a_missing_drive(tmp_path: Path, app_home: Path) -> None:
    from tests.fixtures.api import build_harness

    api = build_harness(tmp_path)
    api.services.config.set("data_root", str(tmp_path / "unplugged" / "drive" / "meetings"))
    response = api.client().get("/api/status")
    assert response.status_code == 200


# -- the real downloader, with the network faked ----------------------------------------


def test_a_stopped_download_keeps_its_part_file_and_the_next_one_fetches_the_rest(
    app_home: Path,
) -> None:
    """huggingface_hub 1.29 deletes a partial file on any failure, so a stopped 3 GB
    download started again from zero. Ours continues from the .part file."""
    from collections.abc import Callable

    from app.asr.model_manager import DownloadCancelled, http_downloader

    blobs = {"model.bin": MODEL, "config.json": CONFIG}
    asked: list[tuple[str, int]] = []
    stop_at: list[int | None] = [2048]

    def fetch(url: str, dest: Path, on_bytes: Callable[[int], None], cancel: object) -> None:
        name = url.rsplit("/", 1)[-1]
        data = blobs[name]
        start = dest.stat().st_size if dest.exists() else 0
        asked.append((name, start))
        with dest.open("ab") as out:
            for at in range(start, len(data), 512):
                if stop_at[0] is not None and at >= stop_at[0]:
                    raise DownloadCancelled()
                out.write(data[at : at + 512])
                on_bytes(len(data[at : at + 512]))

    first = ModelManager(HEB, home=app_home, lister=lister,
                         downloader=http_downloader(lister, fetch))  # fmt: skip
    first.start()
    assert first.wait(5).state == "cancelled"
    part = target_for(REPO, app_home) / "model.bin.part"
    assert part.stat().st_size == 2048, "the partial file stays"

    stop_at[0] = None
    asked.clear()
    second = ModelManager(HEB, home=app_home, lister=lister,
                          downloader=http_downloader(lister, fetch))  # fmt: skip
    status = second.start()
    status = second.wait(5)
    assert status.state == "ready", status.error
    assert ("model.bin", 2048) in asked, "the second attempt asked only for the rest"
    assert not part.exists()
    assert (target_for(REPO, app_home) / "model.bin").read_bytes() == MODEL
    assert status.done_bytes == status.total_bytes


def test_a_part_file_longer_than_the_file_is_started_again(app_home: Path) -> None:
    from collections.abc import Callable

    from app.asr.model_manager import http_downloader

    target = target_for(REPO, app_home)
    target.mkdir(parents=True)
    (target / "model.bin.part").write_bytes(b"x" * (len(MODEL) + 10))
    blobs = {"model.bin": MODEL, "config.json": CONFIG}

    def fetch(url: str, dest: Path, on_bytes: Callable[[int], None], cancel: object) -> None:
        data = blobs[url.rsplit("/", 1)[-1]]
        start = dest.stat().st_size if dest.exists() else 0
        with dest.open("ab") as out:
            out.write(data[start:])
        on_bytes(len(data) - start)

    manager = ModelManager(HEB, home=app_home, lister=lister,
                           downloader=http_downloader(lister, fetch))  # fmt: skip
    manager.start()
    assert manager.wait(5).state == "ready"
    assert (target / "model.bin").read_bytes() == MODEL


def test_downloads_trust_certifi_as_well_as_the_system() -> None:
    """A fresh Windows fills its root store only on demand, so the system store alone
    failed the first download there (job 022). certifi's roots are loaded too."""
    import certifi

    from app.asr.model_manager import tls_context

    context = tls_context()
    loaded = context.cert_store_stats()["x509_ca"]
    with open(certifi.where(), encoding="ascii") as bundle:
        in_certifi = bundle.read().count("BEGIN CERTIFICATE")
    assert loaded >= in_certifi > 100
    assert context.verify_mode.name == "CERT_REQUIRED" and context.check_hostname


# -- three pinned models (the multilingual epic, story A) --------------------------------


def test_the_registry_holds_exactly_three_roles_each_pinned_to_a_revision() -> None:
    from app.asr.models import TOTAL_BYTES

    assert ROLES == (CLASSIFIER, HEBREW, OTHER), "installation order: small one first"
    assert {m.repo for m in MODELS.values()} == {
        "Systran/faster-whisper-small",
        "ivrit-ai/whisper-large-v3-turbo-ct2",
        "Systran/faster-whisper-large-v3",
    }
    for model in MODELS.values():
        assert len(model.revision) == 40 and int(model.revision, 16) >= 0, model
    assert 5_100_000_000 < TOTAL_BYTES < 5_300_000_000


def test_files_are_fetched_at_the_pinned_revision_never_main(app_home: Path) -> None:
    from collections.abc import Callable

    from app.asr.model_manager import http_downloader

    listed: list[tuple[str, str]] = []
    urls: list[str] = []
    blobs = {"model.bin": MODEL, "config.json": CONFIG}

    def listing(repo: str, revision: str) -> dict[str, RemoteFile]:
        listed.append((repo, revision))
        return dict(FILES)

    def fetch(url: str, dest: Path, on_bytes: Callable[[int], None], cancel: object) -> None:
        urls.append(url)
        dest.write_bytes(blobs[url.rsplit("/", 1)[-1]])

    manager = ModelManager(HEB, home=app_home, lister=listing,
                           downloader=http_downloader(listing, fetch))  # fmt: skip
    manager.start()
    assert manager.wait(5).state == "ready"
    assert set(listed) == {(HEB.repo, HEB.revision)}
    assert urls and all(f"/resolve/{HEB.revision}/" in url for url in urls)
    assert not any("/main/" in url for url in urls)
    marker = (target_for(REPO, app_home) / model_manager.VERIFIED).read_text(encoding="utf-8")
    assert marker == f"{HEB.repo}@{HEB.revision}"


def test_a_marker_for_another_revision_is_not_ready(app_home: Path) -> None:
    target = target_for(REPO, app_home)
    target.mkdir(parents=True)
    (target / "model.bin").write_bytes(MODEL)
    (target / "config.json").write_bytes(CONFIG)
    (target / model_manager.VERIFIED).write_text(f"{REPO}@{'0' * 40}", encoding="utf-8")
    assert not ModelManager(HEB, home=app_home, lister=lister).ready()
    assert not resolve(default_config(), HEBREW).local
    # A marker from before revisions were pinned held the repo alone.
    (target / model_manager.VERIFIED).write_text(REPO, encoding="utf-8")
    assert not resolve(default_config(), HEBREW).local
    (target / model_manager.VERIFIED).write_text(HEB.marker, encoding="utf-8")
    assert resolve(default_config(), HEBREW).local


def test_resolve_finds_each_role_in_its_own_folder(app_home: Path) -> None:
    for role in ROLES:
        manager = ModelManager(MODELS[role], home=app_home, lister=lister, downloader=writing())
        manager.start()
        assert manager.wait(5).state == "ready"
    for role in ROLES:
        choice = resolve(default_config(), role)
        assert choice.local and Path(choice.reference) == target_for(MODELS[role].repo, app_home)


def test_the_model_path_override_is_for_the_hebrew_model_only(
    tmp_path: Path, app_home: Path
) -> None:
    mine = tmp_path / "mine"
    mine.mkdir()
    (mine / "model.bin").write_bytes(b"x")
    (mine / "config.json").write_bytes(b"{}")
    config = default_config()
    config.set("asr.model_path", str(mine))
    assert resolve(config, HEBREW).reference == str(mine)
    assert not resolve(config, OTHER).local and resolve(config, OTHER).reference != str(mine)
    assert not resolve(config, CLASSIFIER).local
    assert model_manager.overridden_roles(config) == frozenset({HEBREW})


def test_a_model_path_that_is_not_the_pinned_model_is_said_once(
    tmp_path: Path,
    app_home: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A developer's large-v3 copy still loads after D93, but the log says what it is."""
    # Sizes scaled down: a real 3 GB file is not sparse on Windows, and writing it held up
    # the next test's downloads past their timeout.
    monkeypatch.setitem(MODELS, HEBREW, replace(MODELS[HEBREW], size_bytes=1_620))
    mine = tmp_path / "large-v3"
    mine.mkdir()
    (mine / "model.bin").write_bytes(b"x" * 3_087)  # large-v3 against the turbo, in kB
    (mine / "config.json").write_bytes(b"{}")
    config = default_config()
    config.set("asr.model_path", str(mine))
    with caplog.at_level("WARNING"):
        assert resolve(config, HEBREW).reference == str(mine)
        resolve(config, HEBREW)
    said = [r for r in caplog.records if MODELS[HEBREW].repo in r.getMessage()]
    assert len(said) == 1


def recording_downloader(order: list[str], fail: str = "") -> Any:
    base = writing()

    def download(model_: SpeechModel, target: Path, progress: type) -> None:
        order.append(model_.role)
        if model_.role == fail:
            (target / "config.json").write_bytes(CONFIG)  # model.bin never arrives
            return
        base(model_, target, progress)

    return download


def test_all_three_are_downloaded_in_order(app_home: Path) -> None:
    order: list[str] = []
    managers = {
        role: ModelManager(MODELS[role], home=app_home, lister=lister,
                           downloader=recording_downloader(order))
        for role in ROLES
    }  # fmt: skip
    models = ModelSet(managers)
    assert models.status().state == "missing"
    models.start()
    status = models.wait(10)
    assert status.state == "ready", status.error
    assert order == [CLASSIFIER, HEBREW, OTHER]
    assert status.done_bytes == status.total_bytes == 3 * (len(MODEL) + len(CONFIG))
    for role in ROLES:
        assert resolve(default_config(), role).local
    # A second run fetches nothing.
    order.clear()
    ModelSet(managers).start()
    assert order == []


def test_one_model_failing_verification_fails_the_set_and_stops_there(app_home: Path) -> None:
    order: list[str] = []
    managers = {
        role: ModelManager(MODELS[role], home=app_home, lister=lister,
                           downloader=recording_downloader(order, fail=HEBREW))
        for role in ROLES
    }  # fmt: skip
    models = ModelSet(managers)
    models.start()
    status = models.wait(10)
    assert status.state == "failed" and "model.bin" in status.error
    assert order == [CLASSIFIER, HEBREW], "the third is not started after a failure"
    rows = {row["role"]: row["state"] for row in status.models}
    assert rows == {CLASSIFIER: "ready", HEBREW: "failed", OTHER: "missing"}


def test_the_free_space_check_covers_all_three_together(
    app_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Room for any one model, not for the three: refused before the first byte."""
    order: list[str] = []
    one = len(MODEL) + len(CONFIG)
    monkeypatch.setattr(
        model_manager, "free_bytes", lambda path: model_manager.SPARE_BYTES + 2 * one
    )
    managers = {
        role: ModelManager(MODELS[role], home=app_home, lister=lister,
                           downloader=recording_downloader(order))
        for role in ROLES
    }  # fmt: skip
    models = ModelSet(managers)
    models.start()
    status = models.wait(10)
    assert status.state == "failed" and status.code == "no_space"
    assert order == []


def test_a_stopped_set_resumes_where_it_stopped(app_home: Path) -> None:
    order: list[str] = []
    gate = threading.Event()
    slow = writing(gate=gate)

    def download(model_: SpeechModel, target: Path, progress: type) -> None:
        order.append(model_.role)
        (slow if model_.role == HEBREW else writing())(model_, target, progress)

    managers = {
        role: ModelManager(MODELS[role], home=app_home, lister=lister, downloader=download)
        for role in ROLES
    }
    models = ModelSet(managers)
    models.start()
    while models.status().current != HEBREW:
        threading.Event().wait(0.01)
    models.cancel()
    gate.set()
    assert models.wait(10).state == "cancelled"
    order.clear()
    models.start()
    assert models.wait(10).state == "ready"
    assert order == [HEBREW, OTHER], "the classifier is not fetched again"


def _large_v3_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A developer's ivrit large-v3 next to the pinned turbo, sizes scaled down to kB."""
    monkeypatch.setitem(MODELS, HEBREW, replace(MODELS[HEBREW], size_bytes=1_620))
    mine = tmp_path / "large-v3"
    mine.mkdir()
    (mine / "model.bin").write_bytes(b"x" * 3_087)
    (mine / "config.json").write_bytes(b"{}")
    return mine


def test_an_installed_build_ignores_a_model_path_that_is_not_the_pinned_model(
    tmp_path: Path,
    app_home: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """z8tj1hfr6w: a developer session's large-v3 outlived an uninstall, and the installed
    build ran on it and never fetched the turbo. It now resolves to its own copy."""
    from app import paths

    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    mine = _large_v3_copy(tmp_path, monkeypatch)
    config = default_config()
    config.set("asr.model_path", str(mine))
    with caplog.at_level("WARNING"):
        choice = resolve(config, HEBREW)
        resolve(config, HEBREW)
    assert not choice.local and choice.reference == MODELS[HEBREW].repo
    assert model_manager.overridden_roles(config) == frozenset()
    assert not model_manager.model_set(config).skip, "--prepare must fetch the turbo"
    said = [r for r in caplog.records if "installed build" in r.getMessage()]
    assert len(said) == 1 and str(mine) in said[0].getMessage()
    assert config.get("asr.model_path") == str(mine), "the key stays for running from source"


def test_an_installed_build_keeps_a_model_path_that_holds_the_pinned_model(
    tmp_path: Path, app_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A copy of the turbo kept elsewhere (a Hugging Face snapshot) is still taken."""
    from app import paths

    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    monkeypatch.setitem(MODELS, HEBREW, replace(MODELS[HEBREW], size_bytes=1_620))
    turbo = tmp_path / "turbo"
    turbo.mkdir()
    (turbo / "model.bin").write_bytes(b"x" * 1_620)
    (turbo / "config.json").write_bytes(b"{}")
    config = default_config()
    config.set("asr.model_path", str(turbo))
    assert resolve(config, HEBREW).reference == str(turbo)
    assert model_manager.overridden_roles(config) == frozenset({HEBREW})


def test_running_from_source_still_honours_any_model_path(
    tmp_path: Path, app_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import paths

    monkeypatch.setattr(paths, "is_frozen", lambda: False)
    mine = _large_v3_copy(tmp_path, monkeypatch)
    config = default_config()
    config.set("asr.model_path", str(mine))
    assert resolve(config, HEBREW).reference == str(mine)
