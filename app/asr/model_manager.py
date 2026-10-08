"""The speech model on a stranger's machine: fetched once, in the open (Windows testing 2).

Before this, a machine with no model downloaded 3 GB inside the first meeting's
transcription, through faster-whisper, into the Hugging Face user cache: minutes of what
looked like a hang, no progress, no cancel, no space check, and files the uninstaller
never saw (job 007 on machine B). Here the download is its own step, which the installer
(``--prepare``) and first-run setup start and show, and which transcription never does:

- into ``<home>/models/asr/<repo>``, where ``models.resolve`` looks and the app owns it;
- at the revision ``models.MODELS`` pins, never ``main``; the verified marker records the
  revision, so a marker left by another revision counts as not downloaded;
- resumable across runs: each file downloads into ``<name>.part`` and a later attempt asks
  for the rest with an HTTP range. Not through the hub's own download: from 1.29 it writes
  to a temporary name unique to the run and deletes it on any failure, so a stopped
  download started again from zero (found 2026-09-26);
- verified: each file against the SHA-256 the repo lists for it, once it has landed;
- a free-space check on the target's drive before a byte is fetched;
- progress and cancel through a tqdm-shaped progress class called for every block;
- proxies from ``HTTPS_PROXY``/``HTTP_PROXY``, and on Windows from the system's Internet
  settings, as urllib reads both.
"""

from __future__ import annotations

import functools
import os
import shutil
import ssl
import threading
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app import paths
from app.asr.models import MODELS, RETIRED_REPOS, ROLES, SpeechModel, looks_like_model_dir
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


#: (repo_id, revision) -> {file name: RemoteFile}
Lister = Callable[[str, str], dict[str, RemoteFile]]
#: (model, local_dir, progress class) -> None; ``http_downloader`` by default.
Downloader = Callable[[SpeechModel, Path, type], Any]


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
    #: A set of models (``ModelSet``): the role being fetched now, and each one's status.
    current: str = ""
    models: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def models_root(home: Path | None = None) -> Path:
    return (home or paths.app_home()) / "models" / "asr"


def target_for(repo: str, home: Path | None = None) -> Path:
    return models_root(home) / repo.replace("/", "__")


def remove_retired(home: Path | None = None) -> list[Path]:
    """Delete the folders of models no build loads any more (``models.RETIRED_REPOS``).

    Only ever a folder this manager made under ``models/asr``: a copy a developer keeps
    elsewhere (``asr.model_path``) is not touched. Never raises: a folder that will not go
    now is tried again by the next install.
    """
    removed: list[Path] = []
    for repo in RETIRED_REPOS:
        folder = target_for(repo, home)
        if not folder.exists():
            continue
        try:
            shutil.rmtree(folder)
        except OSError as exc:
            log.warning("could not remove the retired model %s: %s", folder, exc)
            continue
        log.info("removed the retired model %s", folder)
        removed.append(folder)
    return removed


def is_verified(folder: Path, model: SpeechModel) -> bool:
    """A model folder whose files all matched the listing of the pinned revision."""
    marker = folder / VERIFIED
    if not (looks_like_model_dir(folder) and marker.is_file()):
        return False
    try:
        return marker.read_text(encoding="utf-8").strip() == model.marker
    except OSError:
        return False


def free_bytes(path: Path) -> int:
    """Free space on ``path``'s drive, from its nearest folder that exists."""
    probe = path
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        return shutil.disk_usage(str(probe)).free
    except OSError:
        return 0


def hub_lister(repo: str, revision: str) -> dict[str, RemoteFile]:  # pragma: no cover - network
    from huggingface_hub import HfApi
    from huggingface_hub.hf_api import RepoFile

    return {
        entry.path: RemoteFile(int(entry.size or 0), entry.lfs.sha256 if entry.lfs else None)
        for entry in HfApi().list_repo_tree(repo, revision=revision, recursive=True)
        if isinstance(entry, RepoFile)
    }


def sha256_of(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1 << 22):
            digest.update(block)
    return digest.hexdigest()


@functools.cache
def tls_context() -> ssl.SSLContext:
    """Trust certifi's roots and the system's, together.

    The system store alone failed on a fresh Windows (job 022, CERTIFICATE_VERIFY_FAILED):
    Windows adds most roots only when a CryptoAPI client first needs them, and Python's
    ``ssl`` is not one, so the first HTTPS download from a new machine had nothing to trust.
    certifi alone would refuse a company proxy that re-signs traffic with its own root,
    which lives only in the system store. The hub's listing worked in the same run because
    httpx carries certifi.
    """
    context = ssl.create_default_context()
    try:
        import certifi

        context.load_verify_locations(cafile=certifi.where())
    except Exception as exc:  # the system store is still there
        log.warning("certifi's roots could not be loaded: %s", exc)
    return context


def http_fetch(
    url: str, dest: Path, on_bytes: Callable[[int], None], cancel: threading.Event
) -> None:
    """Download ``url`` into ``dest``, continuing a partial file with an HTTP range.

    ``on_bytes`` is told every block; raising ``DownloadCancelled`` from it, or setting
    ``cancel``, stops the download and leaves ``dest`` where it got to. Proxies come from
    the environment and, on Windows, from the system's Internet settings (urllib reads both).
    """
    start = dest.stat().st_size if dest.exists() else 0
    request = urllib.request.Request(url, headers={"User-Agent": "Upshot"})
    if start:
        request.add_header("Range", f"bytes={start}-")
    with urllib.request.urlopen(request, timeout=60, context=tls_context()) as response:
        # A server that ignores Range sends the whole file again: start over.
        mode = "ab" if start and response.status == 206 else "wb"
        with dest.open(mode) as out:
            while block := response.read(1 << 20):
                if cancel.is_set():
                    raise DownloadCancelled()
                out.write(block)
                on_bytes(len(block))


#: (url, destination .part file, bytes received callback, cancel event) -> None
Fetcher = Callable[[str, Path, Callable[[int], None], threading.Event], None]

HUB_URL = "https://huggingface.co/{repo}/resolve/{revision}/{name}"


def http_downloader(lister: Lister = hub_lister, fetch: Fetcher = http_fetch) -> Downloader:
    """Each file of the repo over plain HTTPS, into ``<name>.part`` until it is whole."""

    def download(model: SpeechModel, target: Path, progress: type) -> None:
        files = lister(model.repo, model.revision)
        parts = {name: target / (name + ".part") for name in files}
        done = 0
        for name, remote in files.items():
            whole, part = target / name, parts[name]
            if whole.is_file() and whole.stat().st_size == remote.size:
                done += remote.size
            elif part.is_file():
                if part.stat().st_size > remote.size:  # can only be wrong; start it again
                    part.unlink()
                else:
                    done += part.stat().st_size
        bar = progress(total=sum(r.size for r in files.values()), initial=done)
        never = threading.Event()  # cancelling is the progress class raising
        for name, remote in files.items():
            whole, part = target / name, parts[name]
            if whole.is_file() and whole.stat().st_size == remote.size:
                continue
            part.parent.mkdir(parents=True, exist_ok=True)
            if not (part.is_file() and part.stat().st_size == remote.size):
                url = HUB_URL.format(
                    repo=model.repo, revision=model.revision, name=urllib.parse.quote(name)
                )
                fetch(url, part, bar.update, never)
            os.replace(part, whole)

    return download


class ModelManager:
    """One model's download, one at a time, whoever asks: the installer or the setup screen."""

    def __init__(
        self,
        model: SpeechModel,
        *,
        home: Path | None = None,
        lister: Lister = hub_lister,
        downloader: Downloader | None = None,
    ) -> None:
        self.model = model
        self.repo = model.repo
        self.target = target_for(model.repo, home)
        self.lister = lister
        self.downloader = downloader or http_downloader(lister)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._status = ModelStatus(self.repo, str(self.target), "missing")
        if self.ready():
            self._status.state = "ready"

    def ready(self) -> bool:
        """Only once verified: files can be in place from a download that was stopped."""
        return is_verified(self.target, self.model)

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
        """The model folder, downloading it first if needed."""
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
            files = self.lister(self.model.repo, self.model.revision)
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
            log.info(
                "downloading %s@%s (%.2f GB) into %s",
                self.repo,
                self.model.revision[:12],
                total / 1e9,
                self.target,
            )
            self.downloader(self.model, self.target, self._progress_class(present))
            self._verify(files)
            if not looks_like_model_dir(self.target):
                raise RuntimeError(f"{self.target} has no model.bin/config.json after the download")
            (self.target / VERIFIED).write_text(self.model.marker, encoding="utf-8")
            with self._lock:
                self._status.state = "ready"
                self._status.done_bytes = total
            log.info("speech model %s ready at %s", self.model.role, self.target)
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


def manager_for(model: SpeechModel, home: Path | None = None) -> ModelManager:
    """The one manager for this model and home, shared by the API and the installer."""
    target = target_for(model.repo, home)
    with _managers_lock:
        if target not in _managers or _managers[target].model != model:
            _managers[target] = ModelManager(model, home=home)
        return _managers[target]


def overridden_roles(config: Any) -> frozenset[str]:
    """Roles whose model a developer keeps outside the managed folder (``asr.model_path``):
    nothing to fetch for them."""
    from app.asr.models import resolve

    roles = set()
    for role in ROLES:
        choice = resolve(config, role)
        if choice.local and Path(choice.reference) != target_for(MODELS[role].repo):
            roles.add(role)
    return frozenset(roles)


class ModelSet:
    """All three models, fetched one after another: what ``--prepare`` and the setup
    screen start. Its status sums the three, and names the one being fetched now.

    Roles in ``skip`` are already somewhere else (``asr.model_path``) and count as ready.
    The free-space check covers everything still to fetch, plus the spare, before the
    first byte: a drive that fits the small model but not the large ones fails at once
    rather than an hour in.
    """

    def __init__(
        self,
        managers: dict[str, ModelManager] | None = None,
        *,
        home: Path | None = None,
        skip: frozenset[str] = frozenset(),
    ) -> None:
        self.managers = managers or {role: manager_for(MODELS[role], home) for role in ROLES}
        self.skip = skip
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._current = ""
        self._failure: ModelStatus | None = None
        self._cancelled = False

    def _pending(self) -> dict[str, ModelManager]:
        return {
            role: manager
            for role, manager in self.managers.items()
            if role not in self.skip and not manager.ready()
        }

    def ready(self) -> bool:
        return not self._pending()

    def repo_text(self) -> str:
        return " + ".join(manager.repo for manager in self.managers.values())

    def status(self) -> ModelStatus:
        parts = {role: manager.status() for role, manager in self.managers.items()}
        done = total = 0
        rows: list[dict[str, Any]] = []
        for role, part in parts.items():
            expected = part.total_bytes or self.managers[role].model.size_bytes
            state = "ready" if role in self.skip else part.state
            done += expected if state == "ready" else part.done_bytes
            total += expected
            rows.append(
                {"role": role, "repo": part.repo, "state": state,
                 "done_bytes": expected if state == "ready" else part.done_bytes,
                 "total_bytes": expected}
            )  # fmt: skip
        with self._lock:
            running = self._thread is not None and self._thread.is_alive()
            failure, cancelled, current = self._failure, self._cancelled, self._current
        first = next(iter(parts.values()))
        status = ModelStatus(
            self.repo_text(),
            str(Path(first.path).parent),
            "missing",
            done_bytes=done,
            total_bytes=total,
            free_bytes=first.free_bytes,
            current=current if running else "",
            models=rows,
        )
        if running:
            status.state = "downloading"
        elif all(row["state"] == "ready" for row in rows):
            status.state = "ready"
        elif failure is not None:
            status.state, status.error, status.code = "failed", failure.error, failure.code
        elif cancelled:
            status.state = "cancelled"
        return status

    def start(self) -> ModelStatus:
        with self._lock:
            running = self._thread is not None and self._thread.is_alive()
            if not running and not self.ready():
                self._cancel.clear()
                self._failure, self._cancelled = None, False
                # Named before the thread starts, so the first report already says which.
                self._current = next(iter(self._pending()), "")
                self._thread = threading.Thread(target=self._run, name="models", daemon=True)
                self._thread.start()
        return self.status()

    def cancel(self) -> ModelStatus:
        self._cancel.set()
        for manager in self.managers.values():
            manager.cancel()
        return self.status()

    def wait(self, timeout: float | None = None) -> ModelStatus:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        return self.status()

    def _run(self) -> None:
        pending = self._pending()
        try:
            self._check_space(pending)
        except NotEnoughSpace as exc:
            with self._lock:
                self._failure = ModelStatus("", "", "failed", error=str(exc), code="no_space")
            log.warning("model download refused: %s", exc)
            return
        except Exception as exc:
            with self._lock:
                self._failure = ModelStatus("", "", "failed", error=str(exc) or type(exc).__name__)
            log.warning("could not list the speech models: %s", exc)
            return
        for role, manager in pending.items():
            if self._cancel.is_set():
                with self._lock:
                    self._cancelled = True
                return
            with self._lock:
                self._current = role
            manager.start()
            status = manager.wait()
            if status.state == "cancelled" or self._cancel.is_set():
                with self._lock:
                    self._cancelled = True
                return
            if status.state != "ready":
                with self._lock:
                    self._failure = status
                return

    def _check_space(self, pending: dict[str, ModelManager]) -> None:
        if not pending:
            return
        needed = SPARE_BYTES
        target = next(iter(pending.values())).target
        for manager in pending.values():
            files = manager.lister(manager.model.repo, manager.model.revision)
            total = sum(remote.size for remote in files.values())
            present = sum(
                (manager.target / name).stat().st_size
                for name in files
                if (manager.target / name).is_file()
            )
            needed += max(0, total - present)
        free = free_bytes(target)
        if free < needed:
            raise NotEnoughSpace(
                f"the speech models need {needed / 1e9:.1f} GB free on the drive of "
                f"{target}, and {free / 1e9:.1f} GB is free"
            )


_sets: dict[tuple[Path, frozenset[str]], ModelSet] = {}


def model_set(config: Any, home: Path | None = None) -> ModelSet:
    """The one ``ModelSet`` for this home, so a download the setup screen started is the
    one its next poll reports on."""
    skip = overridden_roles(config)
    key = (models_root(home), skip)
    with _managers_lock:
        existing = _sets.get(key)
    if existing is None:
        existing = ModelSet(home=home, skip=skip)
        with _managers_lock:
            existing = _sets.setdefault(key, existing)
    return existing
