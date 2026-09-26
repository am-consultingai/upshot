"""cuBLAS and cuDNN for GPU transcription, fetched once onto a machine that can use them.

The freeze bundles no CUDA libraries, so without these a machine with a good NVIDIA card
transcribes on the CPU, several times slower (known-issues #2). The installer fetches them
through ``upshot.exe --prepare`` when the card could run the model (``wanted``):

- the two NVIDIA wheels from PyPI, pinned by version and SHA-256. They are the exact DLLs
  (same bytes) that transcribe on the author's GTX 1080 with this ctranslate2, so a
  Pascal card is covered;
- resumable: a ``.part`` file continues where it stopped;
- only the DLLs are kept, all in one folder: ``cuda_library_dirs`` registers a folder only
  when cuBLAS is in it, so cuDNN in a folder of its own would never be found;
- the wheels are deleted once unpacked.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import threading
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app import paths
from app.asr.model_manager import SPARE_BYTES, DownloadCancelled, NotEnoughSpace, free_bytes
from app.config import Config
from app.log import get

log = get(__name__)


@dataclass(frozen=True)
class Wheel:
    name: str
    version: str
    url: str
    sha256: str
    size: int


WHEELS: tuple[Wheel, ...] = (
    Wheel(
        "nvidia-cublas-cu12",
        "12.9.1.4",
        "https://files.pythonhosted.org/packages/45/a1/"
        "a17fade6567c57452cfc8f967a40d1035bb9301db52f27808167fbb2be2f/"
        "nvidia_cublas_cu12-12.9.1.4-py3-none-win_amd64.whl",
        "1e5fee10662e6e52bd71dec533fbbd4971bb70a5f24f3bc3793e5c2e9dc640bf",
        553_153_899,
    ),
    Wheel(
        "nvidia-cudnn-cu12",
        "9.1.1.17",
        "https://files.pythonhosted.org/packages/a5/0a/"
        "4a3852f359aa6043369217155c4b905226634025d1b9c605286fdb823847/"
        "nvidia_cudnn_cu12-9.1.1.17-py3-none-win_amd64.whl",
        "d7c4b96b1c5ca8e4dc0bdbf2ce386903ba03faa94d2997b72769fa5048cb5f1f",
        679_928_156,
    ),
)

#: The DLLs unpacked from ``WHEELS``, about 1.8 GB.
EXTRACTED_BYTES = 1_810_000_000
#: The libraries a GPU load cannot do without; their presence is what ``ready`` checks.
REQUIRED = ("cublas64_12.dll", "cublasLt64_12.dll", "cudnn64_9.dll")
#: Written into the folder once every wheel is unpacked: {wheel name: version}.
MARKER = ".upshot-cuda"

#: (url, destination .part file, bytes received callback, cancel event) -> None
Fetcher = Callable[[str, Path, Callable[[int], None], threading.Event], None]
#: GPU memory in MB, or None when there is no NVIDIA GPU or it cannot be read.
VramQuery = Callable[[], "int | None"]


def target_dir(home: Path | None = None) -> Path:
    """Where ``cuda_library_dirs`` looks: ``<home>/cuda/nvidia/<package>/bin``."""
    return (home or paths.app_home()) / "cuda" / "nvidia" / "cu12" / "bin"


def _versions(wheels: tuple[Wheel, ...]) -> dict[str, str]:
    return {wheel.name: wheel.version for wheel in wheels}


def ready(home: Path | None = None, wheels: tuple[Wheel, ...] = WHEELS) -> bool:
    folder = target_dir(home)
    marker = folder / MARKER
    if not marker.is_file() or not all((folder / name).is_file() for name in REQUIRED):
        return False
    try:
        return bool(json.loads(marker.read_text(encoding="utf-8")) == _versions(wheels))
    except (OSError, ValueError):
        return False


def wanted(config: Config, vram: VramQuery | None = None) -> tuple[bool, str]:
    """Would this machine transcribe on its GPU once it had the libraries? And why not."""
    from app.asr.local import MIN_VRAM_MB, gpu_memory_mb

    if str(config.get("asr.device", "auto")) == "cpu":
        return False, "transcription is set to run on the CPU"
    memory = (vram or gpu_memory_mb)()
    if memory is None:
        return False, "no NVIDIA GPU found"
    if memory < MIN_VRAM_MB:
        return False, f"the GPU has {memory} MB, under the {MIN_VRAM_MB} MB the model needs"
    return True, f"NVIDIA GPU with {memory} MB"


def http_fetch(
    url: str, dest: Path, on_bytes: Callable[[int], None], cancel: threading.Event
) -> None:  # pragma: no cover - network
    """Download ``url`` into ``dest``, continuing a partial file. Proxies come from the
    environment and, on Windows, from the system's Internet settings (urllib reads both)."""
    start = dest.stat().st_size if dest.exists() else 0
    request = urllib.request.Request(url, headers={"User-Agent": "Upshot"})
    if start:
        request.add_header("Range", f"bytes={start}-")
    with urllib.request.urlopen(request, timeout=60) as response:
        # A server that ignores Range sends the whole file again: start over.
        mode = "ab" if start and response.status == 206 else "wb"
        with dest.open(mode) as out:
            while block := response.read(1 << 20):
                if cancel.is_set():
                    raise DownloadCancelled()
                out.write(block)
                on_bytes(len(block))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1 << 22):
            digest.update(block)
    return digest.hexdigest()


@dataclass
class CudaStatus:
    #: missing | downloading | unpacking | ready | failed | cancelled
    state: str
    done_bytes: int = 0
    total_bytes: int = 0
    error: str = ""
    #: "no_space" when the drive is too full; "" otherwise.
    code: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class CudaInstaller:
    """The same shape as ``ModelManager``: start in the background, poll, cancel, wait."""

    def __init__(
        self,
        home: Path | None = None,
        *,
        wheels: tuple[Wheel, ...] = WHEELS,
        fetch: Fetcher = http_fetch,
    ) -> None:
        self.home = home or paths.app_home()
        self.wheels = wheels
        self.fetch = fetch
        self.target = target_dir(self.home)
        self.downloads = self.home / "cuda" / ".download"
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        total = sum(wheel.size for wheel in wheels)
        self._status = CudaStatus("ready" if self.ready() else "missing", total_bytes=total)

    def ready(self) -> bool:
        return ready(self.home, self.wheels)

    def status(self) -> CudaStatus:
        with self._lock:
            return CudaStatus(**asdict(self._status))

    def start(self) -> CudaStatus:
        with self._lock:
            running = self._thread is not None and self._thread.is_alive()
            if not running and not self.ready():
                self._cancel.clear()
                self._status.state = "downloading"
                self._status.error = self._status.code = ""
                self._thread = threading.Thread(target=self._run, name="cuda-download", daemon=True)
                self._thread.start()
        return self.status()

    def cancel(self) -> CudaStatus:
        self._cancel.set()
        return self.status()

    def wait(self, timeout: float | None = None) -> CudaStatus:
        if self._thread is not None:
            self._thread.join(timeout)
        return self.status()

    def _set(self, **fields: Any) -> None:
        with self._lock:
            for key, value in fields.items():
                setattr(self._status, key, value)

    def _run(self) -> None:
        try:
            self.downloads.mkdir(parents=True, exist_ok=True)
            parts = {wheel: self.downloads / wheel.url.rsplit("/", 1)[-1] for wheel in self.wheels}
            for wheel, part in parts.items():
                # Longer than the wheel can only be wrong, and a resume would ask past its end.
                if part.exists() and part.stat().st_size > wheel.size:
                    part.unlink()
            present = sum(min(p.stat().st_size, w.size) for w, p in parts.items() if p.exists())
            needed = sum(w.size for w in self.wheels) - present + EXTRACTED_BYTES + SPARE_BYTES
            free = free_bytes(self.home)
            self._set(done_bytes=present)
            if free < needed:
                raise NotEnoughSpace(
                    f"the GPU libraries need {needed / 1e9:.1f} GB free on the drive of "
                    f"{self.home}, and {free / 1e9:.1f} GB is free"
                )
            done = present
            for wheel, part in parts.items():
                if part.exists() and part.stat().st_size == wheel.size:
                    continue

                def received(n: int) -> None:
                    nonlocal done
                    done += n
                    self._set(done_bytes=done)

                log.info("downloading %s %s", wheel.name, wheel.version)
                self.fetch(wheel.url, part, received, self._cancel)
            for wheel, part in parts.items():
                size = part.stat().st_size
                if size != wheel.size or _sha256(part) != wheel.sha256:
                    part.unlink()
                    raise RuntimeError(f"{part.name} did not download intact; fetch it again")
            self._set(state="unpacking", done_bytes=sum(w.size for w in self.wheels))
            self._unpack(list(parts.values()))
            for part in parts.values():
                part.unlink(missing_ok=True)
            self._set(state="ready")
            log.info("GPU libraries ready in %s", self.target)
        except DownloadCancelled:
            self._set(state="cancelled")
            log.info("GPU library download cancelled; the partial files resume next time")
        except NotEnoughSpace as exc:
            self._set(state="failed", error=str(exc), code="no_space")
            log.warning("GPU library download refused: %s", exc)
        except Exception as exc:
            self._set(state="failed", error=str(exc) or type(exc).__name__)
            log.warning("GPU library download failed: %s", exc)

    def _unpack(self, wheels: list[Path]) -> None:
        """Every ``nvidia/*/bin/*.dll`` into one folder, swapped in only once complete."""
        staging = self.target.with_name("bin.new")
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        for wheel in wheels:
            with zipfile.ZipFile(wheel) as archive:
                for member in archive.infolist():
                    parts = member.filename.split("/")
                    if len(parts) == 4 and parts[0] == "nvidia" and parts[2] == "bin" and (
                        parts[3].lower().endswith(".dll")
                    ):
                        if self._cancel.is_set():
                            raise DownloadCancelled()
                        with archive.open(member) as source, (staging / parts[3]).open("wb") as out:
                            shutil.copyfileobj(source, out, 1 << 22)
        missing = [name for name in REQUIRED if not (staging / name).is_file()]
        if missing:
            raise RuntimeError(f"the GPU libraries are missing {', '.join(missing)}")
        (staging / MARKER).write_text(json.dumps(_versions(self.wheels)), encoding="utf-8")
        shutil.rmtree(self.target, ignore_errors=True)
        staging.rename(self.target)
