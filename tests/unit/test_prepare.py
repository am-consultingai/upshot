"""``--prepare``: the installer's download step, and the GPU libraries it may fetch."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import sys
import threading
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

from app import prepare
from app.asr import cuda_libs
from app.asr.cuda_libs import CudaInstaller, Wheel, ready, target_dir, wanted
from app.asr.model_manager import ModelManager, ModelSet, RemoteFile
from app.asr.models import MODELS, ROLES, SpeechModel
from app.config import default_config
from app.prepare import ProgressFile, run


@pytest.fixture(autouse=True)
def speaker_models(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """The diarization download, without the network: writes the two files, records each."""
    from app.asr import models

    fetched: list[Path] = []

    def download(config: object) -> object:
        found = models.resolve_diarization(config)  # type: ignore[arg-type]
        for target in (found.segmentation, found.embedding):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"onnx")
            fetched.append(target)
        return found

    monkeypatch.setattr(models, "download_diarization", download)
    return fetched


def read_progress(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    return dict(line.split("=", 1) for line in lines)


# -- the speech models stage ---------------------------------------------------------

MODEL = b"m" * 8192
CONFIG = b'{"model": "tiny"}'
FILES = {
    "model.bin": RemoteFile(len(MODEL), hashlib.sha256(MODEL).hexdigest()),
    "config.json": RemoteFile(len(CONFIG)),
}


def model_manager(
    home: Path,
    gate: threading.Event | None = None,
    order: list[str] | None = None,
    fail: str = "",
) -> ModelSet:
    """The three models as ``--prepare`` fetches them, with a stand-in for the hub."""

    def download(model: SpeechModel, target: Path, progress: type) -> None:
        if order is not None:
            order.append(model.role)
        if model.role == fail:
            (target / "config.json").write_bytes(CONFIG)
            return
        bar = progress(total=len(MODEL) + len(CONFIG), initial=0)
        with (target / "model.bin").open("wb") as out:
            for at in range(0, len(MODEL), 1024):
                if gate is not None:
                    gate.wait(5)
                out.write(MODEL[at : at + 1024])
                bar.update(1024)
        (target / "config.json").write_bytes(CONFIG)
        bar.update(len(CONFIG))

    return ModelSet({
        role: ModelManager(MODELS[role], home=home, lister=lambda repo, rev: dict(FILES),
                           downloader=download)
        for role in ROLES
    })  # fmt: skip


def test_the_model_is_fetched_and_the_file_ends_ready(app_home: Path, tmp_path: Path) -> None:
    progress_path = tmp_path / "prepare.txt"
    code = run(
        default_config(), ProgressFile(progress_path),
        model=model_manager(app_home), gpu_wanted=(False, "no NVIDIA GPU found"), poll=0.01,
    )  # fmt: skip
    assert code == prepare.EXIT_OK
    final = read_progress(progress_path)
    assert final["stage"] == "done" and final["state"] == "ready"
    assert int(final["tick"]) >= 3


def test_progress_is_written_while_the_model_downloads(app_home: Path, tmp_path: Path) -> None:
    """The installer's page must have something to show before the download ends."""
    gate = threading.Event()
    progress = ProgressFile(tmp_path / "prepare.txt")
    seen: list[dict[str, str]] = []
    original = progress.write

    def spy(**fields: object) -> None:
        original(**fields)
        seen.append(dict(progress.last))
        gate.set()  # let the download go on once the first report is out

    progress.write = spy  # type: ignore[method-assign]
    run(default_config(), progress, model=model_manager(app_home, gate),
        gpu_wanted=(False, "x"), poll=0.01)  # fmt: skip
    working = [s for s in seen if s.get("state") == "working" and s.get("stage") == "model"]
    assert working, seen
    assert working[0]["text"].startswith("Speech models (language detection, 1 of 3):")
    assert working[0]["model"] == "classifier"


def test_a_cancel_file_stops_the_download_and_says_it_continues_later(
    app_home: Path, tmp_path: Path
) -> None:
    gate = threading.Event()  # holds the download until the cancel has been asked for
    cancel = tmp_path / "cancel"
    progress_path = tmp_path / "prepare.txt"
    manager = model_manager(app_home, gate)
    ask = manager.cancel

    def cancel_then_release() -> object:
        result = ask()
        gate.set()
        return result

    manager.cancel = cancel_then_release  # type: ignore[method-assign]

    def cancelled() -> bool:
        cancel.touch()  # what the installer's "Download later" does
        return cancel.exists()

    code = run(default_config(), ProgressFile(progress_path), cancelled, model=manager,
               gpu_wanted=(False, "x"), poll=0.01)  # fmt: skip
    assert code == prepare.EXIT_CANCELLED
    assert read_progress(progress_path)["state"] == "cancelled"


def test_a_full_drive_is_its_own_exit_code(
    app_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The installer tells "not enough space" apart from a network failure."""
    from app.asr import model_manager as mm

    monkeypatch.setattr(mm, "free_bytes", lambda path: 10)
    progress_path = tmp_path / "prepare.txt"
    code = run(default_config(), ProgressFile(progress_path), model=model_manager(app_home),
               gpu_wanted=(False, "x"), poll=0.01)  # fmt: skip
    assert code == prepare.EXIT_NO_SPACE
    final = read_progress(progress_path)
    assert final["state"] == "failed" and final["code"] == "no_space"


def test_a_model_already_on_disk_is_skipped(app_home: Path, tmp_path: Path) -> None:
    manager = model_manager(app_home)
    manager.start()
    assert manager.wait(5).state == "ready"
    progress_path = tmp_path / "prepare.txt"
    seen: list[str] = []
    progress = ProgressFile(progress_path)
    original = progress.write
    progress.write = lambda **f: (original(**f), seen.append(str(f.get("text"))))  # type: ignore[method-assign,func-returns-value]
    assert run(default_config(), progress, gpu_wanted=(False, "x"), poll=0.01) == prepare.EXIT_OK
    assert "Speech models: already on this computer" in seen


def test_prepare_fetches_all_three_models_in_order(app_home: Path, tmp_path: Path) -> None:
    order: list[str] = []
    progress_path = tmp_path / "prepare.txt"
    code = run(default_config(), ProgressFile(progress_path),
               model=model_manager(app_home, order=order),
               gpu_wanted=(False, "x"), poll=0.01)  # fmt: skip
    assert code == prepare.EXIT_OK
    assert order == ["classifier", "hebrew", "other"]
    # A second run downloads nothing: the default set finds all three verified.
    seen: list[str] = []
    progress = ProgressFile(progress_path)
    original = progress.write
    progress.write = lambda **f: (original(**f), seen.append(str(f.get("text"))))  # type: ignore[method-assign,func-returns-value]
    assert run(default_config(), progress, gpu_wanted=(False, "x"), poll=0.01) == prepare.EXIT_OK
    assert "Speech models: already on this computer" in seen


def test_prepare_fails_when_any_one_model_fails(app_home: Path, tmp_path: Path) -> None:
    order: list[str] = []
    progress_path = tmp_path / "prepare.txt"
    code = run(default_config(), ProgressFile(progress_path),
               model=model_manager(app_home, order=order, fail="other"),
               gpu_wanted=(False, "x"), poll=0.01)  # fmt: skip
    assert code == prepare.EXIT_FAILED
    final = read_progress(progress_path)
    assert final["stage"] == "model" and final["state"] == "failed"
    assert order == ["classifier", "hebrew", "other"]


def test_prepare_resumes_a_partial_download(app_home: Path, tmp_path: Path) -> None:
    order: list[str] = []
    progress_path = tmp_path / "prepare.txt"
    assert run(default_config(), ProgressFile(progress_path),
               model=model_manager(app_home, order=order, fail="hebrew"),
               gpu_wanted=(False, "x"), poll=0.01) == prepare.EXIT_FAILED  # fmt: skip
    order.clear()
    assert run(default_config(), ProgressFile(progress_path),
               model=model_manager(app_home, order=order),
               gpu_wanted=(False, "x"), poll=0.01) == prepare.EXIT_OK  # fmt: skip
    assert order == ["hebrew", "other"], "the verified classifier is not fetched again"


def test_an_upgrade_removes_the_model_it_no_longer_uses(app_home: Path, tmp_path: Path) -> None:
    """ivrit-ai large-v3 gave way to its turbo (D93): 3 GB an upgrade would leave behind."""
    from app.asr.model_manager import target_for
    from app.asr.models import RETIRED_REPOS

    retired = target_for(RETIRED_REPOS[0])
    retired.mkdir(parents=True)
    (retired / "model.bin").write_bytes(MODEL)
    kept = tmp_path / "my-copy"  # a developer's own copy, outside models/asr
    kept.mkdir()
    (kept / "model.bin").write_bytes(MODEL)
    code = run(default_config(asr__model_path=str(kept)), ProgressFile(tmp_path / "p.txt"),
               model=model_manager(app_home), gpu_wanted=(False, "x"), poll=0.01)  # fmt: skip
    assert code == prepare.EXIT_OK
    assert not retired.exists()
    assert (kept / "model.bin").exists()


def test_the_retired_model_goes_before_the_download(app_home: Path, tmp_path: Path) -> None:
    """Its 3 GB may be what the turbo needs to fit, and nothing loads it any more."""
    from app.asr.model_manager import target_for
    from app.asr.models import RETIRED_REPOS

    retired = target_for(RETIRED_REPOS[0])
    retired.mkdir(parents=True)
    code = run(default_config(), ProgressFile(tmp_path / "p.txt"),
               model=model_manager(app_home, fail="hebrew"),
               gpu_wanted=(False, "x"), poll=0.01)  # fmt: skip
    assert code == prepare.EXIT_FAILED
    assert not retired.exists()


def test_a_retired_folder_that_is_the_developers_model_path_is_kept(
    app_home: Path, tmp_path: Path
) -> None:
    from app.asr.model_manager import target_for
    from app.asr.models import RETIRED_REPOS

    retired = target_for(RETIRED_REPOS[0])
    retired.mkdir(parents=True)
    code = run(default_config(asr__model_path=str(retired)), ProgressFile(tmp_path / "p.txt"),
               model=model_manager(app_home), gpu_wanted=(False, "x"), poll=0.01)  # fmt: skip
    assert code == prepare.EXIT_OK
    assert retired.exists()


def test_an_installed_build_removes_a_retired_model_path_it_no_longer_uses(
    app_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """z8tj1hfr6w: the installed build replaces it, so its 3 GB is freed for the turbo."""
    from app import paths
    from app.asr.model_manager import target_for
    from app.asr.models import RETIRED_REPOS

    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    retired = target_for(RETIRED_REPOS[0])
    retired.mkdir(parents=True)
    code = run(default_config(asr__model_path=str(retired)), ProgressFile(tmp_path / "p.txt"),
               model=model_manager(app_home), gpu_wanted=(False, "x"), poll=0.01)  # fmt: skip
    assert code == prepare.EXIT_OK
    assert not retired.exists()


def test_an_installed_build_keeps_its_retired_stand_in_when_the_download_fails(
    app_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The old model transcribes until the turbo is in place: a failed download must not
    have removed it first (review of #16)."""
    from app import paths
    from app.asr.model_manager import target_for
    from app.asr.models import RETIRED_REPOS

    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    retired = target_for(RETIRED_REPOS[0])
    retired.mkdir(parents=True)
    code = run(default_config(asr__model_path=str(retired)), ProgressFile(tmp_path / "p.txt"),
               model=model_manager(app_home, fail="hebrew"),
               gpu_wanted=(False, "x"), poll=0.01)  # fmt: skip
    assert code == prepare.EXIT_FAILED
    assert retired.exists()


# -- the GPU libraries ---------------------------------------------------------------


def wheel_bytes(package: str, dlls: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in dlls.items():
            archive.writestr(f"nvidia/{package}/bin/{name}", content)
        archive.writestr(f"nvidia/{package}/include/header.h", b"not a dll")
        archive.writestr(f"nvidia_{package}-1.dist-info/METADATA", b"meta")
        archive.writestr(
            f"nvidia_{package}-1.dist-info/licenses/License.txt", b"EULA " + package.encode()
        )
    return buffer.getvalue()


CUBLAS = wheel_bytes("cublas", {"cublas64_12.dll": b"a" * 3000, "cublasLt64_12.dll": b"b" * 2000})
CUDNN = wheel_bytes("cudnn", {"cudnn64_9.dll": b"c" * 1000, "cudnn_ops64_9.dll": b"d" * 500})
BLOBS = {"https://x/cublas.whl": CUBLAS, "https://x/cudnn.whl": CUDNN}
WHEELS = (
    Wheel("cublas", "1", "https://x/cublas.whl", hashlib.sha256(CUBLAS).hexdigest(), len(CUBLAS)),
    Wheel("cudnn", "2", "https://x/cudnn.whl", hashlib.sha256(CUDNN).hexdigest(), len(CUDNN)),
)


def fetcher(blobs: dict[str, bytes] = BLOBS, stop_after: int | None = None) -> cuda_libs.Fetcher:
    """Serves ``blobs`` in blocks, continuing a partial file as the real one does."""

    def fetch(
        url: str, dest: Path, on_bytes: Callable[[int], None], cancel: threading.Event
    ) -> None:
        data = blobs[url]
        start = dest.stat().st_size if dest.exists() else 0
        with dest.open("ab") as out:
            for at in range(start, len(data), 700):
                if stop_after is not None and at - start >= stop_after:
                    raise ConnectionError("the connection dropped")
                out.write(data[at : at + 700])
                on_bytes(len(data[at : at + 700]))

    return fetch


@pytest.fixture
def small_libs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cuda_libs, "EXTRACTED_BYTES", 10_000)


def test_the_gpu_libraries_land_in_one_folder_the_app_registers(
    app_home: Path, small_libs: None
) -> None:
    from app.asr.local import cuda_library_dirs

    installer = CudaInstaller(app_home, wheels=WHEELS, fetch=fetcher())
    installer.start()
    status = installer.wait(5)
    assert status.state == "ready", status.error
    folder = target_dir(app_home)
    assert sorted(p.name for p in folder.glob("*.dll")) == [
        "cublas64_12.dll", "cublasLt64_12.dll", "cudnn64_9.dll", "cudnn_ops64_9.dll",
    ]  # fmt: skip
    assert not list(folder.glob("*.h"))
    # NVIDIA's terms travel with the DLLs.
    assert (folder / "nvidia_cublas-1-License.txt").read_bytes() == b"EULA cublas"
    assert json.loads((folder / cuda_libs.MARKER).read_text()) == {"cublas": "1", "cudnn": "2"}
    assert ready(app_home, WHEELS)
    # Every wheel's DLLs land in one folder, which the app registers as a whole.
    # Off Windows cuda_library_dirs looks in lib/, not bin/.
    if sys.platform == "win32":
        assert folder in cuda_library_dirs(app_home=app_home, search_path=[], system_dirs=())
    assert not list((app_home / "cuda" / ".download").glob("*.whl")), "wheels are deleted"


def test_a_dropped_download_resumes_where_it_stopped(app_home: Path, small_libs: None) -> None:
    first = CudaInstaller(app_home, wheels=WHEELS, fetch=fetcher(stop_after=1400))
    first.start()
    assert first.wait(5).state == "failed"
    part = app_home / "cuda" / ".download" / "cublas.whl"
    kept = part.stat().st_size
    assert 0 < kept < len(CUBLAS)
    received: list[int] = []
    base = fetcher()

    def counting(
        url: str, dest: Path, on_bytes: Callable[[int], None], cancel: threading.Event
    ) -> None:
        def seen(n: int) -> None:
            received.append(n)
            on_bytes(n)

        base(url, dest, seen, cancel)

    second = CudaInstaller(app_home, wheels=WHEELS, fetch=counting)
    second.start()
    assert second.wait(5).state == "ready"
    assert sum(received) == len(CUBLAS) - kept + len(CUDNN)


def test_a_corrupt_wheel_is_refused_and_removed(app_home: Path, small_libs: None) -> None:
    bad = dict(BLOBS)
    bad["https://x/cudnn.whl"] = CUDNN[:-1] + b"X"
    installer = CudaInstaller(app_home, wheels=WHEELS, fetch=fetcher(bad))
    installer.start()
    status = installer.wait(5)
    assert status.state == "failed" and "intact" in status.error
    assert not (app_home / "cuda" / ".download" / "cudnn.whl").exists()
    assert not ready(app_home, WHEELS)


def test_the_gpu_libraries_are_refused_on_a_full_drive(
    app_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cuda_libs, "free_bytes", lambda path: 100)
    installer = CudaInstaller(app_home, wheels=WHEELS, fetch=fetcher())
    installer.start()
    status = installer.wait(5)
    assert status.state == "failed" and status.code == "no_space"
    assert not (app_home / "cuda" / ".download" / "cublas.whl").exists()


@pytest.mark.parametrize(
    ("device", "memory", "want"),
    [
        ("auto", 8192, True),
        # A 2 GB card runs Hebrew on the GPU (D99); 2048 MiB is what machine B reports.
        ("auto", 2048, True),
        ("auto", 2047, False),
        ("auto", None, False),
        ("cpu", 8192, False),
    ],
)
def test_the_libraries_are_wanted_only_where_the_gpu_would_be_used(
    device: str, memory: int | None, want: bool
) -> None:
    config = default_config()
    config.set("asr.device", device)
    assert wanted(config, vram=lambda: memory)[0] is want


def test_prepare_fetches_the_gpu_libraries_after_the_model(
    app_home: Path, tmp_path: Path, small_libs: None
) -> None:
    stages: list[str] = []
    progress = ProgressFile(tmp_path / "prepare.txt")
    original = progress.write
    progress.write = lambda **f: (original(**f), stages.append(str(f["stage"])))  # type: ignore[method-assign,func-returns-value]
    code = run(
        default_config(), progress, model=model_manager(app_home),
        cuda=CudaInstaller(app_home, wheels=WHEELS, fetch=fetcher()),
        gpu_wanted=(True, "NVIDIA GPU with 8192 MB"), poll=0.01,
    )  # fmt: skip
    assert code == prepare.EXIT_OK
    assert stages.index("gpu") > stages.index("model")
    assert stages[-1] == "done"


def test_no_gpu_libraries_where_the_gpu_is_too_small(app_home: Path, tmp_path: Path) -> None:
    progress_path = tmp_path / "prepare.txt"
    texts: list[str] = []
    progress = ProgressFile(progress_path)
    original = progress.write
    progress.write = lambda **f: (original(**f), texts.append(str(f.get("text"))))  # type: ignore[method-assign,func-returns-value]
    run(default_config(), progress, model=model_manager(app_home),
        gpu_wanted=(False, "the GPU has 2048 MB"), poll=0.01)  # fmt: skip
    assert "GPU libraries: not needed (the GPU has 2048 MB)" in texts
    assert not target_dir(app_home).exists()


def test_the_pinned_wheels_are_the_ones_proven_on_a_gpu() -> None:
    """cuBLAS 12.9.1.4 alone: byte for byte the DLLs that transcribe on a GTX 1080 (Pascal)
    and an MX450 (Turing) with this ctranslate2, which needs no cuDNN (licence epic, P4).
    Changing them needs that check again."""
    assert {w.name: w.version for w in cuda_libs.WHEELS} == {"nvidia-cublas-cu12": "12.9.1.4"}
    assert not any("cudnn" in name for name in cuda_libs.REQUIRED)
    for wheel in cuda_libs.WHEELS:
        assert wheel.url.startswith("https://files.pythonhosted.org/")
        assert wheel.url.endswith("win_amd64.whl") and len(wheel.sha256) == 64


def test_libraries_already_on_the_machine_are_used_not_downloaded_again(tmp_path: Path) -> None:
    """A CUDA Toolkit, or a folder named in asr.cuda_dir, with cuBLAS: no 550 MB."""
    toolkit = tmp_path / "toolkit" / "bin"
    toolkit.mkdir(parents=True)
    (toolkit / "cublas64_12.dll").write_bytes(b"x")
    config = default_config()
    config.set("asr.cuda_dir", str(toolkit))
    assert cuda_libs.usable_elsewhere(config, search_path=[], system_dirs=()) is None, (
        "cuBLAS without cuBLASLt is not enough"
    )
    (toolkit / "cublasLt64_12.dll").write_bytes(b"x")
    assert cuda_libs.usable_elsewhere(config, search_path=[], system_dirs=()) == toolkit


def test_an_earlier_builds_cudnn_is_removed_and_its_cublas_kept(app_home: Path) -> None:
    """Builds before the licence epic fetched cuDNN too (about 1 GB unpacked, loaded by
    nothing). Such a folder counts as ready, and --prepare clears the cuDNN out of it."""
    folder = target_dir(app_home)
    folder.mkdir(parents=True)
    for name in ("cublas64_12.dll", "cublasLt64_12.dll", "cudnn64_9.dll", "cudnn_ops64_9.dll"):
        (folder / name).write_bytes(b"x" * 10)
    (folder / cuda_libs.MARKER).write_text(
        json.dumps({"nvidia-cublas-cu12": "12.9.1.4", "nvidia-cudnn-cu12": "9.1.1.17"})
    )
    assert ready(app_home), "the cuBLAS it holds is the one wanted"
    assert cuda_libs.remove_retired(app_home) == 20
    assert sorted(p.name for p in folder.glob("*.dll")) == ["cublas64_12.dll", "cublasLt64_12.dll"]
    assert json.loads((folder / cuda_libs.MARKER).read_text()) == {"nvidia-cublas-cu12": "12.9.1.4"}
    assert ready(app_home)
    assert cuda_libs.remove_retired(app_home) == 0


def test_another_cublas_version_is_not_ready(app_home: Path) -> None:
    folder = target_dir(app_home)
    folder.mkdir(parents=True)
    for name in cuda_libs.REQUIRED:
        (folder / name).write_bytes(b"x")
    (folder / "cudnn64_9.dll").write_bytes(b"x")
    marker = json.dumps({"nvidia-cublas-cu12": "12.4.0"})
    (folder / cuda_libs.MARKER).write_text(marker)
    assert not ready(app_home)
    assert cuda_libs.remove_retired(app_home) == 0, "only a ready folder is touched"
    assert (folder / "cudnn64_9.dll").exists()
    assert (folder / cuda_libs.MARKER).read_text() == marker
    (folder / cuda_libs.MARKER).write_text("[]")
    assert not ready(app_home)


def test_prepare_skips_the_download_when_the_libraries_are_already_there(
    app_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cuda_libs, "usable_elsewhere", lambda config: tmp_path)
    texts: list[str] = []
    progress = ProgressFile(tmp_path / "prepare.txt")
    original = progress.write
    progress.write = lambda **f: (original(**f), texts.append(str(f.get("text"))))  # type: ignore[method-assign,func-returns-value]
    code = run(default_config(), progress, model=model_manager(app_home),
               gpu_wanted=(True, "NVIDIA GPU with 8192 MB"), poll=0.01)  # fmt: skip
    assert code == prepare.EXIT_OK
    assert "GPU libraries: already on this computer" in texts
    assert not target_dir(app_home).exists()


def test_prepare_on_an_upgrade_clears_the_retired_cudnn(
    app_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An install of an earlier build holds cuBLAS and cuDNN; the upgrade's --prepare keeps
    the cuBLAS, downloads nothing, and frees the cuDNN."""
    monkeypatch.setattr(cuda_libs, "usable_elsewhere", lambda config: None)
    folder = target_dir(app_home)
    folder.mkdir(parents=True)
    for name in ("cublas64_12.dll", "cublasLt64_12.dll", "cudnn64_9.dll"):
        (folder / name).write_bytes(b"x")
    (folder / cuda_libs.MARKER).write_text(
        json.dumps({"nvidia-cublas-cu12": "12.9.1.4", "nvidia-cudnn-cu12": "9.1.1.17"})
    )
    progress = ProgressFile(tmp_path / "prepare.txt")
    code = run(default_config(), progress, model=model_manager(app_home),
               gpu_wanted=(True, "NVIDIA GPU with 8192 MB"), poll=0.01)  # fmt: skip
    assert code == prepare.EXIT_OK
    assert sorted(p.name for p in folder.glob("*.dll")) == ["cublas64_12.dll", "cublasLt64_12.dll"]
    assert not (app_home / "cuda" / ".download").exists(), "nothing was downloaded"


def old_build_folder(app_home: Path) -> Path:
    """What an install of an earlier build left: cuBLAS and cuDNN, both in the marker."""
    folder = target_dir(app_home)
    folder.mkdir(parents=True)
    for name in ("cublas64_12.dll", "cublasLt64_12.dll", "cudnn64_9.dll", "cudnn_ops64_9.dll"):
        (folder / name).write_bytes(b"x")
    (folder / cuda_libs.MARKER).write_text(
        json.dumps({"nvidia-cublas-cu12": "12.9.1.4", "nvidia-cudnn-cu12": "9.1.1.17"})
    )
    return folder


@pytest.mark.parametrize(
    ("gpu", "wanted_now"),
    [(True, (False, "transcription is set to run on the CPU")), (False, None)],
)
def test_the_retired_cudnn_goes_even_when_no_gpu_libraries_are_wanted_now(
    app_home: Path, tmp_path: Path, gpu: bool, wanted_now: tuple[bool, str] | None
) -> None:
    """Set to the CPU since, or --prepare --no-gpu: the 1 GB still goes."""
    folder = old_build_folder(app_home)
    progress = ProgressFile(tmp_path / "prepare.txt")
    code = run(default_config(), progress, model=model_manager(app_home), gpu=gpu,
               gpu_wanted=wanted_now, poll=0.01)  # fmt: skip
    assert code == prepare.EXIT_OK
    assert sorted(p.name for p in folder.glob("*.dll")) == ["cublas64_12.dll", "cublasLt64_12.dll"]


def test_the_cudnn_goes_first_even_when_the_speech_models_then_fail(
    app_home: Path, tmp_path: Path
) -> None:
    """The cleanup comes before the downloads, so its gigabyte can be what lets the speech
    models fit, as the retired model's folder does."""
    folder = old_build_folder(app_home)
    progress = ProgressFile(tmp_path / "prepare.txt")
    failing = model_manager(app_home, fail="classifier")
    code = run(default_config(), progress, model=failing, gpu=False, poll=0.01)  # fmt: skip
    assert code != prepare.EXIT_OK
    assert not list(folder.glob("cudnn*.dll"))


def test_an_unreadable_folder_never_fails_the_install(
    app_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_build_folder(app_home)

    def denied(home: Path | None = None, wheels: object = None) -> bool:
        raise PermissionError(13, "denied")

    monkeypatch.setattr(cuda_libs, "ready", denied)
    assert cuda_libs.remove_retired(app_home) == 0
    progress = ProgressFile(tmp_path / "prepare.txt")
    code = run(default_config(), progress, model=model_manager(app_home), gpu=False, poll=0.01)
    assert code == prepare.EXIT_OK


def test_a_cudnn_that_will_not_go_never_fails_the_install(
    app_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scanner holding the file: the install succeeds, the file stays and is globbed
    again by the next install; the marker already names only cuBLAS."""
    folder = old_build_folder(app_home)
    marker = (folder / cuda_libs.MARKER).read_text()
    real_unlink = Path.unlink

    def held(self: Path, missing_ok: bool = False) -> None:
        if self.name.startswith("cudnn"):
            raise PermissionError(13, "in use", str(self))
        real_unlink(self, missing_ok)

    monkeypatch.setattr(Path, "unlink", held)
    monkeypatch.setattr(cuda_libs, "usable_elsewhere", lambda config: None)
    code = run(default_config(), ProgressFile(tmp_path / "prepare.txt"),
               model=model_manager(app_home), gpu_wanted=(True, "NVIDIA GPU with 8192 MB"),
               poll=0.01)  # fmt: skip
    assert code == prepare.EXIT_OK
    assert (folder / "cudnn64_9.dll").exists(), "tried again by the next install"
    assert json.loads((folder / cuda_libs.MARKER).read_text()) == {"nvidia-cublas-cu12": "12.9.1.4"}
    assert marker != (folder / cuda_libs.MARKER).read_text()
    assert ready(app_home)
    assert cuda_libs.remove_retired(app_home) == 0, "still held: nothing freed, nothing raised"


def test_an_earlier_builds_folder_gets_nvidias_licence_without_a_download(
    app_home: Path, tmp_path: Path
) -> None:
    """Earlier builds kept the DLLs only. The app ships the pinned wheel's License.txt,
    byte for byte, and --prepare puts it beside them."""
    folder = old_build_folder(app_home)
    progress = ProgressFile(tmp_path / "prepare.txt")
    code = run(default_config(), progress, model=model_manager(app_home), gpu=False, poll=0.01)
    assert code == prepare.EXIT_OK
    (wheel,) = cuda_libs.WHEELS
    placed = folder / cuda_libs.licence_name(wheel)
    assert placed.name == "nvidia_cublas_cu12-12.9.1.4-License.txt"
    assert placed.read_bytes().startswith(b"End User License Agreement")
    assert cuda_libs.add_missing_licences(app_home) == 0, "only when missing"


#: From nvidia_cublas_cu12-12.9.1.4.dist-info/RECORD, the line for licenses/License.txt.
RECORD_SHA256_OF_LICENSE = "rW9YU_ugyg0VnQ9Y1JrkmDDC-Mk_epJki5zpCttMbM0"


def test_the_shipped_licence_is_the_wheels_own() -> None:
    """The SHA-256 the cuBLAS 12.9.1.4 wheel's RECORD lists for its License.txt."""
    from app import paths

    (wheel,) = cuda_libs.WHEELS
    text = paths.resource("app", "asr", "licenses", cuda_libs.licence_name(wheel)).read_bytes()
    digest = base64.urlsafe_b64encode(hashlib.sha256(text).digest()).rstrip(b"=").decode()
    assert digest == RECORD_SHA256_OF_LICENSE


def test_a_missing_bundled_licence_is_logged_not_raised(
    app_home: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    old_build_folder(app_home)
    from app import paths

    monkeypatch.setattr(paths, "resource", lambda *parts: app_home / "nowhere" / parts[-1])
    assert cuda_libs.add_missing_licences(app_home) == 0
    assert "carries no nvidia_cublas_cu12-12.9.1.4-License.txt" in caplog.text


def test_the_marker_is_replaced_whole(app_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Written beside and moved over: when the move fails, the old marker is still whole
    (and still ready), never half-written. On success no .tmp is left."""
    folder = old_build_folder(app_home)
    before = (folder / cuda_libs.MARKER).read_text()

    def refused(source: object, target: object) -> None:
        raise PermissionError(13, "in use")

    with monkeypatch.context() as patched:  # only this patch; the fixtures' stay
        patched.setattr(cuda_libs.os, "replace", refused)
        assert cuda_libs.remove_retired(app_home) == 0
    assert (folder / cuda_libs.MARKER).read_text() == before
    assert ready(app_home)
    cuda_libs.remove_retired(app_home)
    assert json.loads((folder / cuda_libs.MARKER).read_text()) == {"nvidia-cublas-cu12": "12.9.1.4"}
    assert not list(folder.glob("*.tmp"))


def test_a_licence_is_added_only_to_a_ready_folder(app_home: Path) -> None:
    folder = target_dir(app_home)
    folder.mkdir(parents=True)
    assert cuda_libs.add_missing_licences(app_home) == 0
    assert not list(folder.iterdir())


@pytest.mark.parametrize("name", ["", ".", "..", "..\\evil.dll", "C:evil.dll", "a/b.dll"])
def test_a_member_name_that_could_leave_the_folder_is_not_unpacked(name: str) -> None:
    assert not cuda_libs._safe_name(name)
    assert cuda_libs._safe_name("cublas64_12.dll")


@pytest.mark.parametrize(
    "member",
    [
        "..\\..\\evil.dist-info/licenses/License.txt",
        "C:\\evil.dist-info/License.txt",
        "nvidia/cublas/bin/..\\evil.dll",
    ],
)
def test_an_unsafe_member_writes_nothing_outside_the_folder(app_home: Path, member: str) -> None:
    """Through _unpack itself: a licence or DLL member whose name could climb out."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in cuda_libs.REQUIRED:
            archive.writestr(f"nvidia/cublas/bin/{name}", b"x")
        archive.writestr(member, b"evil")
    blob = buffer.getvalue()
    wheel = Wheel("cublas", "1", "https://x/w.whl", hashlib.sha256(blob).hexdigest(), len(blob))
    installer = CudaInstaller(app_home, wheels=(wheel,), fetch=fetcher({"https://x/w.whl": blob}))
    installer.start()
    assert installer.wait(5).state == "ready"
    assert sorted(p.name for p in target_dir(app_home).iterdir()) == [
        ".upshot-cuda", "cublas64_12.dll", "cublasLt64_12.dll",
    ]  # fmt: skip
    assert not [p for p in app_home.rglob("*") if "evil" in p.name]


# -- the speaker models (D85) ---------------------------------------------------------


def test_the_speaker_models_come_after_the_speech_models(
    app_home: Path, tmp_path: Path, speaker_models: list[Path]
) -> None:
    progress = ProgressFile(tmp_path / "prepare.txt")
    texts: list[str] = []
    original = progress.write

    def spy(**fields: object) -> None:
        original(**fields)
        texts.append(progress.last.get("text", ""))

    progress.write = spy  # type: ignore[method-assign]
    code = run(default_config(), progress, model=model_manager(app_home),
               gpu_wanted=(False, "x"), poll=0.01)  # fmt: skip
    assert code == prepare.EXIT_OK
    assert [path.name for path in speaker_models] == [
        "segmentation.onnx",
        "embedding-3dspeaker-campplus-zh-en.onnx",
    ]
    speech = max(i for i, text in enumerate(texts) if text.startswith("Speech models"))
    assert texts.index("Speaker models: downloading") > speech


def test_an_upgrade_with_its_speech_models_still_gets_the_speaker_models(
    app_home: Path, tmp_path: Path, speaker_models: list[Path]
) -> None:
    from tests.fixtures.models import install_models

    install_models(app_home)
    code = run(default_config(), ProgressFile(tmp_path / "p.txt"), gpu_wanted=(False, "x"))
    assert code == prepare.EXIT_OK
    assert len(speaker_models) == 2


def test_speaker_models_already_here_are_not_fetched_again(
    app_home: Path, tmp_path: Path, speaker_models: list[Path]
) -> None:
    from app.asr.models import resolve_diarization

    found = resolve_diarization(default_config())
    for target in (found.segmentation, found.embedding):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"onnx")
    code = prepare.fetch_speaker_models(default_config(), ProgressFile(None), lambda: False)
    assert code == prepare.EXIT_OK
    assert speaker_models == []


def test_a_failed_speaker_model_download_fails_the_install(app_home: Path, tmp_path: Path) -> None:
    def broken(config: object) -> object:
        raise OSError("connection reset")

    progress_path = tmp_path / "prepare.txt"
    code = prepare.fetch_speaker_models(
        default_config(), ProgressFile(progress_path), lambda: False, download=broken
    )
    assert code == prepare.EXIT_FAILED
    final = read_progress(progress_path)
    assert final["stage"] == "model" and final["state"] == "failed"
    assert "connection reset" in final["error"]


# -- the installer's language ---------------------------------------------------------


def test_the_progress_text_is_in_the_installer_s_language(app_home: Path, tmp_path: Path) -> None:
    progress = ProgressFile(tmp_path / "prepare.txt")
    texts: list[str] = []
    original = progress.write

    def spy(**fields: object) -> None:
        original(**fields)
        texts.append(progress.last.get("text", ""))

    progress.write = spy  # type: ignore[method-assign]
    code = run(default_config(), progress, model=model_manager(app_home),
               gpu_wanted=(False, "no NVIDIA GPU found"), poll=0.01, language="es")  # fmt: skip
    assert code == prepare.EXIT_OK
    assert any(t.startswith("Modelos de voz (detección de idioma, 1 de 3): ") for t in texts), texts
    assert "Modelos de voz: descarga completa" in texts
    assert "Bibliotecas de GPU: no son necesarias (no se encontró una GPU NVIDIA)" in texts
    assert texts[-1] == "Listo para transcribir"
    # Only the text is translated: the keys and values the installer acts on are not.
    final = read_progress(tmp_path / "prepare.txt")
    assert final["stage"] == "done" and final["state"] == "ready" and final["percent"] == "100"


def test_a_small_gpus_reason_is_one_the_installer_translates() -> None:
    want, why = wanted(default_config(), vram=lambda: 1024)
    assert not want
    assert why == "the GPU has 1024 MB, under the 2048 MB the model needs"
    assert prepare.reason(why, "he") != why


def test_a_reason_for_no_gpu_libraries_is_translated() -> None:
    small = "the GPU has 2048 MB, under the 4096 MB the model needs"
    assert prepare.reason(small, "de") == (
        "die Grafikkarte hat 2048 MB, weniger als die 4096 MB, die das Modell braucht"
    )
    assert prepare.reason(small, "en") == small
    assert prepare.reason("something new", "fr") == "something new"


def test_the_progress_file_is_utf8_with_a_mark_and_a_first_line_nobody_reads(
    tmp_path: Path,
) -> None:
    """Inno reads UTF-8 only after a byte-order mark, and finds a key with
    ``Pos(Key + '=', Line) = 1``: the mark must not land on a key it looks up."""
    path = tmp_path / "prepare.txt"
    ProgressFile(path).write(stage="model", state="working", text="מודלי דיבור: בהורדה")
    raw = path.read_bytes()
    # The platform's line ending: CRLF on Windows, which Inno has always read.
    assert raw.startswith(b"\xef\xbb\xbfencoding=utf-8" + os.linesep.encode())
    # A reader that keeps the mark still finds every real key at the start of its line.
    lines = raw.decode("utf-8").splitlines()
    assert lines[0] == "﻿encoding=utf-8"
    found = dict(line.split("=", 1) for line in lines[1:])
    assert found["stage"] == "model"
    assert found["text"] == "מודלי דיבור: בהורדה"
    assert found["tick"] == "1"


def test_an_installed_build_does_not_count_a_configured_cuda_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """z8tj1hfr6w: a developer's ``cuda_dir`` outlived an uninstall, so ``--prepare`` never
    fetched the installed copy's own libraries and its GPU depended on that folder."""
    from app import paths

    folder = tmp_path / "Scripts"
    folder.mkdir()
    for name in ("cublas64_12.dll", "cublasLt64_12.dll"):
        (folder / name).write_bytes(b"x")
    config = default_config()
    config.set("asr.cuda_dir", str(folder))
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    assert cuda_libs.usable_elsewhere(config, search_path=[], system_dirs=()) is None
    monkeypatch.setattr(paths, "is_frozen", lambda: False)
    assert cuda_libs.usable_elsewhere(config, search_path=[], system_dirs=()) == folder


def test_the_gpu_step_runs_only_when_accepted_or_already_in_this_users_folder(
    tmp_path: Path,
) -> None:
    from app.prepare import gpu_allowed

    assert gpu_allowed(no_gpu=False, if_present=False, home=tmp_path)  # accepted
    assert not gpu_allowed(no_gpu=True, if_present=False, home=tmp_path)  # declined
    assert not gpu_allowed(no_gpu=False, if_present=True, home=tmp_path)  # not asked, none here
    (tmp_path / "cuda").mkdir()
    assert gpu_allowed(no_gpu=False, if_present=True, home=tmp_path)  # kept and completed
    assert not gpu_allowed(no_gpu=True, if_present=True, home=tmp_path)


def test_the_entry_point_passes_gpu_if_present_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import prepare

    monkeypatch.setenv("UP_HOME", str(tmp_path))
    seen: list[bool] = []
    monkeypatch.setattr(prepare, "run", lambda *a, gpu, **k: seen.append(gpu) or prepare.EXIT_OK)
    argv = ["--progress-file", str(tmp_path / "progress.txt"), "--gpu-if-present"]
    prepare.main(argv)
    (tmp_path / "cuda").mkdir()
    prepare.main(argv)
    prepare.main([*argv, "--no-gpu"])
    assert seen == [False, True, False]
