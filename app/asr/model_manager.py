"""The speech model on a stranger's machine: fetched once, in the open (Windows testing 2).

Before this, a machine with no model downloaded 1.6-3 GB inside the first meeting's
transcription, through faster-whisper, into the Hugging Face user cache: minutes of what
looked like a hang, no progress, no cancel, no space check, and files the uninstaller
never saw (job 007 on machine B). Here the download is its own step, which first-run
setup starts and shows, and which the transcribe stage falls back to:

- into ``<home>/models/asr/<repo>``, where ``models.resolve`` looks and the app owns it;
- resumable: the hub keeps partial files under ``<target>/.cache`` and continues them;
- verified: each file against the SHA-256 the repo lists for it, once it has landed;
- a free-space check on the target's drive before a byte is fetched;
- progress and cancel through a progress class the hub calls for every block;
- proxies from ``HTTPS_PROXY``/``HTTP_PROXY``, as every Python HTTP client reads them.
"""

from __future__ import annotations

import shutil
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app import paths
from app.asr.models import REPO, looks_like_model_dir
from app.log import get

log = get(__name__)

#: Room left on the drive after the model, for the meetings that follow it.
SPARE_BYTES = 1 << 30

#: Written into the model folder once every file has matched the repo's listing.
VERIFIED = ".upshot-verified"

@dataclass(frozen=True)
class RemoteFile:
    size: int
    #: The LFS object's hash; the hub lists none for small plain-git files.
    sha256: str | None = None


#: (repo_id) -> {file name: RemoteFile}
Lister = Callable[[str], dict[str, RemoteFile]]
#: (repo_id, local_dir, progress class) -> None; the hub's ``snapshot_download``.
Downloader = Callable[[str, Path, type], Any]


class DownloadCancelled(Exception):
    pass


class NotEnoughSpace(OSError):
    pass


@dataclass
class ModelStatus:
    repo: str
    path: str
    #: missing | downloading | ready | failed | cancelled
    state: str
    done_bytes: int = 0
    total_bytes: int = 0
    free_bytes: int = 0
    error: str = ""
    #: "no_space" when the drive is too full, so the screen can say that in the reader's
    #: language instead of relaying this English sentence; "" for anything else.
    code: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def models_root(home: Path | None = None) -> Path:
    return (home or paths.app_home()) / "models" / "asr"


def target_for(repo: str, home: Path | None = None) -> Path:
    return models_root(home) / repo.replace("/", "__")


def free_bytes(path: Path) -> int:
    """Free space on ``path``'s drive, from its nearest folder that exists."""
    probe = path
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        return shutil.disk_usage(str(probe)).free
    except OSError:
        return 0


def hub_lister(repo: str) -> dict[str, RemoteFile]:  # pragma: no cover - network
    from huggingface_hub import HfApi
    from huggingface_hub.hf_api import RepoFile

    return {
        entry.path: RemoteFile(int(entry.size or 0), entry.lfs.sha256 if entry.lfs else None)
        for entry in HfApi().list_repo_tree(repo, recursive=True)
        if isinstance(entry, RepoFile)
    }


def sha256_of(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1 << 22):
            digest.update(block)
    return digest.hexdigest()


def hub_downloader(repo: str, target: Path, progress: type) -> Any:  # pragma: no cover
    from huggingface_hub import constants, snapshot_download

    # Plain HTTP, not Xet: Xet reports progress from its own thread and swallows what the
    # progress class raises, so a cancel went unheard and the download ran on (measured
    # with hub 1.29). Over HTTP the report comes from the download loop itself.
    constants.HF_HUB_DISABLE_XET = True
    return snapshot_download(repo_id=repo, local_dir=str(target), tqdm_class=progress)


class ModelManager:
    """One download at a time, whoever asks: the setup screen or a meeting's transcription."""

    def __init__(
        self,
        repo: str = REPO,
        *,
        home: Path | None = None,
        lister: Lister = hub_lister,
        downloader: Downloader = hub_downloader,
    ) -> None:
        self.repo = repo
        self.target = target_for(repo, home)
        self.lister = lister
        self.downloader = downloader
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._status = ModelStatus(repo, str(self.target), "missing")
        if self.ready():
            self._status.state = "ready"

    def ready(self) -> bool:
        """Only once verified: files can be in place from a download that was stopped."""
        return looks_like_model_dir(self.target) and (self.target / VERIFIED).is_file()

    def status(self) -> ModelStatus:
        with self._lock:
            if self._status.state != "downloading" and self.ready():
                self._status.state = "ready"
            self._status.free_bytes = free_bytes(self.target)
            return ModelStatus(**asdict(self._status))

    def start(self) -> ModelStatus:
        """Download in the background; a second call while one runs joins it."""
        with self._lock:
            running = self._thread is not None and self._thread.is_alive()
            if not running and not self.ready():
                self._cancel.clear()
                self._status = ModelStatus(self.repo, str(self.target), "downloading")
                self._thread = threading.Thread(
                    target=self._run, name="model-download", daemon=True
                )
                self._thread.start()
        return self.status()

    def cancel(self) -> ModelStatus:
        self._cancel.set()
        return self.status()

    def wait(self, timeout: float | None = None) -> ModelStatus:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        return self.status()

    def ensure(self) -> Path:
        """The model folder, downloading it first if needed (the transcribe stage's path)."""
        if self.ready():
            return self.target
        self.start()
        status = self.wait()
        if status.state != "ready":
            reason = status.error or status.state
            raise RuntimeError(f"the speech model could not be downloaded: {reason}")
        return self.target

    # -- the work ------------------------------------------------------------

    def _run(self) -> None:
        try:
            files = self.lister(self.repo)
            total = sum(remote.size for remote in files.values())
            present = sum(
                (self.target / name).stat().st_size
                for name in files
                if (self.target / name).is_file()
            )
            needed = max(0, total - present) + SPARE_BYTES
            free = free_bytes(self.target)
            with self._lock:
                self._status.total_bytes = total
                self._status.done_bytes = present
            if free < needed:
                raise NotEnoughSpace(
                    f"the speech model needs {needed / 1e9:.1f} GB free on the drive of "
                    f"{self.target}, and {free / 1e9:.1f} GB is free"
                )
            self.target.mkdir(parents=True, exist_ok=True)
            log.info("downloading %s (%.2f GB) into %s", self.repo, total / 1e9, self.target)
            self.downloader(self.repo, self.target, self._progress_class(present))
            self._verify(files)
            if not looks_like_model_dir(self.target):
                raise RuntimeError(f"{self.target} has no model.bin/config.json after the download")
            (self.target / VERIFIED).write_text(self.repo, encoding="utf-8")
            with self._lock:
                self._status.state = "ready"
                self._status.done_bytes = total
            log.info("speech model ready at %s", self.target)
        except DownloadCancelled:
            with self._lock:
                self._status.state = "cancelled"
            log.info("model download cancelled; the partial files stay and resume next time")
        except NotEnoughSpace as exc:
            with self._lock:
                self._status.state = "failed"
                self._status.error = str(exc)
                self._status.code = "no_space"
            log.warning("model download refused: %s", exc)
        except Exception as exc:
            with self._lock:
                self._status.state = "failed"
                self._status.error = str(exc) or type(exc).__name__
            log.warning("model download failed: %s", exc)

    def _verify(self, files: dict[str, RemoteFile]) -> None:
        """A file that does not match is removed, so the next attempt fetches it again."""
        for name, remote in files.items():
            local = self.target / name
            if not local.is_file():
                raise RuntimeError(f"{name} is missing after the download")
            size = local.stat().st_size
            if size != remote.size:
                local.unlink()
                raise RuntimeError(f"{name} is {size} bytes, not {remote.size}; fetch it again")
            if remote.sha256 and sha256_of(local) != remote.sha256:
                local.unlink()
                raise RuntimeError(f"{name} does not match its checksum; fetch it again")

    def _progress_class(self, present: int) -> type:
        """What the hub calls for every block written. It builds a transfer bar and a
        bytes-written bar; the furthest of them is the progress. Raising here is how a
        cancel reaches the download threads."""
        manager = self
        bars: list[Any] = []

        class Progress:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                self.n = int(kwargs.get("initial") or 0)
                self.total = kwargs.get("total")
                # Read by the hub's Xet reporter for its rate text (hub 1.29).
                self.format_dict: dict[str, Any] = {}
                bars.append(self)

            def update(self, n: float | None = 1) -> None:
                if manager._cancel.is_set():
                    raise DownloadCancelled()
                self.n += int(n or 0)
                with manager._lock:
                    manager._status.done_bytes = max(present, *(bar.n for bar in bars))

            def refresh(self, *args: Any, **kwargs: Any) -> None:
                pass

            def close(self) -> None:
                pass

            def set_postfix_str(self, *args: Any, **kwargs: Any) -> None:
                pass

            def set_description(self, *args: Any, **kwargs: Any) -> None:
                pass

            def __getattr__(self, name: str) -> Any:
                # Any other tqdm method a later hub calls is display-only: a no-op here.
                return lambda *args, **kwargs: None

            def __enter__(self) -> Progress:
                return self

            def __exit__(self, *exc: object) -> None:
                pass

        return Progress


_managers: dict[Path, ModelManager] = {}
_managers_lock = threading.Lock()


def manager_for(repo: str, home: Path | None = None) -> ModelManager:
    """The one manager for this repo and home, shared by the API and the pipeline."""
    target = target_for(repo, home)
    with _managers_lock:
        if target not in _managers:
            _managers[target] = ModelManager(repo, home=home)
        return _managers[target]
