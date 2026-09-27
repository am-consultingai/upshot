"""``upshot.exe --prepare``: fetch what transcription needs, for the installer to show.

The installer runs this right after copying the app (packaging/installer.iss) and draws
its own progress page from the file this writes, so the 3 GB speech model arrives during
the install, in the open, instead of silently inside the first meeting's transcription.
It fetches, in order:

1. the speech model, through the same ``ModelManager`` the app uses: resumable, checked
   file by file, refused up front when the drive is too full;
2. the CUDA libraries (``cuda_libs``), only on a machine whose NVIDIA GPU could run the
   model.

    upshot.exe --prepare --progress-file P [--cancel-file C] [--no-gpu]

``P`` is rewritten every half second as ``key=value`` lines — plain text, because Inno's
Pascal script reads it: ``stage`` (model|gpu|done), ``state`` (working|ready|skipped|
failed|cancelled), ``done_mb``, ``total_mb``, ``percent``, ``text``, ``error``, ``code``,
and ``tick``, which grows on every write so the reader can tell a live process from a
dead one. Creating ``C`` cancels; partial files stay and resume next time.

A failure here never undoes the install: the app fetches a missing model before the
first transcription, and runs on the CPU without the CUDA libraries.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from app.config import Config
from app.log import get

log = get(__name__)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CANCELLED = 2
EXIT_NO_SPACE = 3


class Download(Protocol):
    """What ``ModelManager`` and ``CudaInstaller`` share."""

    def start(self) -> Any: ...
    def status(self) -> Any: ...
    def cancel(self) -> Any: ...
    def wait(self, timeout: float | None = None) -> Any: ...


class ProgressFile:
    """``key=value`` lines, replaced whole each time so a reader never sees half a write."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.tick = 0
        self.last: dict[str, str] = {}

    def write(self, **fields: object) -> None:
        self.tick += 1
        self.last = {key: str(value).replace("\n", " ") for key, value in fields.items()}
        if self.path is None:
            return
        lines = [f"{key}={value}" for key, value in self.last.items()]
        lines.append(f"tick={self.tick}")
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
        # On Windows the replace is refused while the installer has the file open to read
        # it. Try again briefly; a missed report is followed by the next one anyway.
        for attempt in range(10):
            try:
                os.replace(temporary, self.path)
                return
            except PermissionError:
                time.sleep(0.02 * (attempt + 1))
        log.debug("prepare: progress file busy, skipped one report")


def _mb(n: int) -> int:
    return int(n // 1_000_000)


def follow(
    stage: str,
    label: str,
    download: Download,
    progress: ProgressFile,
    cancelled: Callable[[], bool],
    *,
    poll: float = 0.5,
    busy: tuple[str, ...] = ("downloading", "unpacking"),
) -> int:
    """Start ``download`` and report it until it ends. Returns an exit code."""
    download.start()
    asked_to_cancel = False
    while True:
        status = download.status()
        if not asked_to_cancel and cancelled():
            asked_to_cancel = True
            log.info("prepare: %s cancelled by the installer", stage)
            download.cancel()
        done, total = int(status.done_bytes), int(status.total_bytes)
        percent = min(100, done * 100 // total) if total else 0
        if status.state == "unpacking":
            text = f"{label}: unpacking"
        elif total:
            text = f"{label}: {_mb(done):,} MB of {_mb(total):,} MB"
        else:
            text = f"{label}: starting"
        if status.state not in busy:
            break
        progress.write(
            stage=stage, state="working", done_mb=_mb(done), total_mb=_mb(total),
            percent=percent, text=text,
        )  # fmt: skip
        time.sleep(poll)
    download.wait(5)
    if status.state == "ready":
        progress.write(
            stage=stage, state="ready", done_mb=_mb(total), total_mb=_mb(total), percent=100,
            text=f"{label}: ready",
        )  # fmt: skip
        return EXIT_OK
    if status.state == "cancelled":
        progress.write(stage=stage, state="cancelled", text=f"{label}: stopped; it continues later")
        return EXIT_CANCELLED
    code = str(getattr(status, "code", "") or "")
    progress.write(
        stage=stage, state="failed", text=f"{label}: not downloaded", error=status.error, code=code,
    )  # fmt: skip
    return EXIT_NO_SPACE if code == "no_space" else EXIT_FAILED


def run(
    config: Config,
    progress: ProgressFile,
    cancelled: Callable[[], bool] = lambda: False,
    *,
    gpu: bool = True,
    model: Download | None = None,
    cuda: Download | None = None,
    gpu_wanted: tuple[bool, str] | None = None,
    poll: float = 0.5,
) -> int:
    """Both stages, in order. Stops at the first that does not finish."""
    from app.asr.models import resolve

    choice = resolve(config)
    if model is None and choice.local:
        progress.write(stage="model", state="skipped", percent=100,
                       text="Speech model: already on this computer")  # fmt: skip
        log.info("prepare: the speech model is already at %s", choice.reference)
    else:
        if model is None:
            from app.asr.model_manager import manager_for

            model = manager_for(choice.repo_id or choice.reference)
        code = follow("model", "Speech model", model, progress, cancelled, poll=poll)
        if code != EXIT_OK:
            return code

    if gpu:
        from app.asr import cuda_libs

        want, why = gpu_wanted if gpu_wanted is not None else cuda_libs.wanted(config)
        elsewhere = cuda_libs.usable_elsewhere(config) if cuda is None and want else None
        if elsewhere is not None:
            progress.write(stage="gpu", state="skipped", percent=100,
                           text="GPU libraries: already on this computer")  # fmt: skip
            log.info("prepare: CUDA libraries already usable in %s; nothing to download", elsewhere)
        elif cuda is None and want and cuda_libs.ready():
            progress.write(stage="gpu", state="skipped", percent=100,
                           text="GPU libraries: already on this computer")  # fmt: skip
        elif not want:
            progress.write(stage="gpu", state="skipped", percent=100,
                           text=f"GPU libraries: not needed ({why})")  # fmt: skip
            log.info("prepare: no GPU libraries: %s", why)
        else:
            log.info("prepare: fetching the GPU libraries (%s)", why)
            cuda = cuda or cuda_libs.CudaInstaller()
            code = follow("gpu", "GPU libraries", cuda, progress, cancelled, poll=poll)
            if code != EXIT_OK:
                return code

    progress.write(stage="done", state="ready", percent=100, text="Ready to transcribe")
    return EXIT_OK


def main(argv: list[str]) -> int:
    """The ``--prepare`` entry point (app/tray.py)."""
    import argparse

    parser = argparse.ArgumentParser(prog="upshot.exe --prepare")
    parser.add_argument("--progress-file", type=Path)
    parser.add_argument("--cancel-file", type=Path)
    parser.add_argument("--no-gpu", action="store_true")
    args = parser.parse_args(argv)
    cancel_file: Path | None = args.cancel_file

    def cancelled() -> bool:
        return cancel_file is not None and cancel_file.exists()

    progress = ProgressFile(args.progress_file)
    try:
        code = run(Config.load(), progress, cancelled, gpu=not args.no_gpu)
    except Exception as exc:  # the installer must always get a last word
        log.exception("prepare failed")
        progress.write(stage="done", state="failed", text="Could not prepare", error=str(exc))
        return EXIT_FAILED
    log.info("prepare finished: exit %d (%s)", code, progress.last.get("text", ""))
    return code
