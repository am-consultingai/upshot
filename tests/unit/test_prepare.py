"""``--prepare``: the installer's download step, and the GPU libraries it may fetch."""

from __future__ import annotations

import hashlib
import io
import json
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


# -- the GPU libraries ---------------------------------------------------------------


def wheel_bytes(package: str, dlls: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in dlls.items():
            archive.writestr(f"nvidia/{package}/bin/{name}", content)
        archive.writestr(f"nvidia/{package}/include/header.h", b"not a dll")
        archive.writestr(f"nvidia_{package}-1.dist-info/METADATA", b"meta")
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
    assert json.loads((folder / cuda_libs.MARKER).read_text()) == {"cublas": "1", "cudnn": "2"}
    assert ready(app_home, WHEELS)
    # cuDNN sits beside cuBLAS, so the one folder the app registers carries both.
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
    [("auto", 8192, True), ("auto", 2048, False), ("auto", None, False), ("cpu", 8192, False)],
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
    """cuBLAS 12.9.1.4 and cuDNN 9.1.1.17: byte for byte the DLLs that transcribe on the
    author's GTX 1080 (Pascal) with this ctranslate2. Changing them needs that check again."""
    assert {w.name: w.version for w in cuda_libs.WHEELS} == {
        "nvidia-cublas-cu12": "12.9.1.4",
        "nvidia-cudnn-cu12": "9.1.1.17",
    }
    for wheel in cuda_libs.WHEELS:
        assert wheel.url.startswith("https://files.pythonhosted.org/")
        assert wheel.url.endswith("win_amd64.whl") and len(wheel.sha256) == 64


def test_libraries_already_on_the_machine_are_used_not_downloaded_again(tmp_path: Path) -> None:
    """A CUDA Toolkit, or a folder named in asr.cuda_dir, with cuBLAS and cuDNN: no 1.2 GB."""
    toolkit = tmp_path / "toolkit" / "bin"
    toolkit.mkdir(parents=True)
    (toolkit / "cublas64_12.dll").write_bytes(b"x")
    config = default_config()
    config.set("asr.cuda_dir", str(toolkit))
    assert cuda_libs.usable_elsewhere(config, search_path=[], system_dirs=()) is None, (
        "cuBLAS without cuDNN is not enough"
    )
    (toolkit / "cudnn64_9.dll").write_bytes(b"x")
    assert cuda_libs.usable_elsewhere(config, search_path=[], system_dirs=()) == toolkit


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
    assert [path.name for path in speaker_models] == ["segmentation.onnx", "embedding.onnx"]
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
    assert raw.startswith(b"\xef\xbb\xbfencoding=utf-8\n")
    # A reader that keeps the mark still finds every real key at the start of its line.
    lines = raw.decode("utf-8").splitlines()
    assert lines[0] == "﻿encoding=utf-8"
    found = dict(line.split("=", 1) for line in lines[1:])
    assert found["stage"] == "model"
    assert found["text"] == "מודלי דיבור: בהורדה"
    assert found["tick"] == "1"
