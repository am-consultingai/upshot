"""faster-whisper backend (EXECUTION-PLAN.md Phase 5).

The two details that decide whether this works on Windows at all:

* CTranslate2 loads cuBLAS lazily with a plain ``LoadLibrary`` at the first matrix
  multiply, which does **not** search ``add_dll_directory`` paths — so every CUDA library
  directory is both registered *and* prepended to ``PATH``.
* A warmup inference runs at load, so a broken GPU stack fails at startup rather than
  halfway through a meeting.
"""

from __future__ import annotations

import functools
import gc
import os
import subprocess
import sys
import wave
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from app import paths
from app.asr.backend import Segment, Word, track_of
from app.asr.models import ModelChoice, resolve
from app.config import Config
from app.log import get

log = get(__name__)

CUDA_ERROR_MARKERS = (
    "cuda",
    "cublas",
    "cudnn",
    "cudart",
    "nvrtc",
    "nvidia",
    "gpu",
    "no kernel image",
)

CUBLAS_GLOBS = ("cublas*", "libcublas*")
SYSTEM_CUDA_DIRS = (
    "C:/Program Files/NVIDIA GPU Computing Toolkit/CUDA",
    "/usr/local/cuda/lib64",
    "/usr/lib/x86_64-linux-gnu",
)

ModelFactory = Callable[..., Any]


def _has_cublas(directory: Path) -> bool:
    if not directory.is_dir():
        return False
    return any(next(directory.glob(pattern), None) is not None for pattern in CUBLAS_GLOBS)


def cuda_library_dirs(
    *,
    configured: Sequence[str] | str | None = None,
    app_home: Path | None = None,
    search_path: Sequence[str] | None = None,
    system_dirs: Sequence[str] = SYSTEM_CUDA_DIRS,
) -> list[Path]:
    """Every directory holding a cuBLAS the runtime could load.

    A configured ``asr.cuda_dir`` wins: it is the user saying "the libraries are here",
    which is the whole point of being able to adopt an existing install.
    """
    found: list[Path] = []
    if configured:
        entries = [configured] if isinstance(configured, str) else list(configured)
        for entry in entries:
            candidate = Path(str(entry)).expanduser()
            if _has_cublas(candidate):
                found.append(candidate)
            else:
                log.warning("asr.cuda_dir has no cuBLAS in it: %s", candidate)
    home = app_home if app_home is not None else paths.app_home()
    bin_name = "bin" if sys.platform == "win32" else "lib"
    nvidia_root = home / "cuda" / "nvidia"
    if nvidia_root.is_dir():
        for package in sorted(nvidia_root.iterdir()):
            candidate = package / bin_name
            if _has_cublas(candidate):
                found.append(candidate)
    for raw in system_dirs:
        candidate = Path(raw)
        if _has_cublas(candidate):
            found.append(candidate)
        elif candidate.is_dir():
            for child in sorted(candidate.glob("*/bin")):
                if _has_cublas(child):
                    found.append(child)
    for entry in list(search_path if search_path is not None else sys.path):
        base = Path(entry) / "nvidia"
        if not base.is_dir():
            continue
        for package in sorted(base.iterdir()):
            candidate = package / bin_name
            if _has_cublas(candidate):
                found.append(candidate)
    seen: dict[str, Path] = {}
    for path in found:
        seen.setdefault(str(path), path)
    return list(seen.values())


def register_cuda_dirs(dirs: Sequence[Path]) -> list[str]:
    """``add_dll_directory`` *and* a PATH prepend — both, or the load fails lazily."""
    registered: list[str] = []
    for directory in dirs:
        text = str(directory)
        if sys.platform == "win32":
            add = getattr(os, "add_dll_directory", None)
            if add is not None:
                try:
                    add(text)
                except OSError:  # pragma: no cover - a vanished directory
                    log.warning("could not register DLL directory %s", text)
                    continue
            os.environ["PATH"] = text + os.pathsep + os.environ.get("PATH", "")
        else:
            current = os.environ.get("LD_LIBRARY_PATH", "")
            os.environ["LD_LIBRARY_PATH"] = text + (os.pathsep + current if current else "")
        registered.append(text)
    return registered


#: The least GPU memory the automatic choice will run Whisper on (ClickUp z8tj1had07).
#: large-v3 holds ~1.6 GB of weights at int8 and ~3 GB at float16, plus activations and
#: whatever the desktop and the meeting app already keep on the card; a 2-3 GB laptop GPU
#: ran out mid-meeting instead of failing at load, where the CPU fallback would catch it.
MIN_VRAM_MB = 4096

NVIDIA_SMI = ("nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits")

#: ``subprocess.run``'s shape, so the VRAM query can be tested without a GPU.
Runner = Callable[..., Any]
#: GPU memory in MB, or None when it cannot be read.
VramQuery = Callable[[], "int | None"]


def gpu_memory_mb(runner: Runner | None = None, *, timeout: float = 5.0) -> int | None:
    """Total memory of the first GPU, in MB, as ``nvidia-smi`` reports it.

    CTranslate2 exposes a device count but not memory, and the driver's own tool is on
    ``PATH`` wherever an NVIDIA driver is. The first line is the one that counts: that
    is device 0, which is the one faster-whisper loads onto. Any failure — no tool, a
    hung driver, output that is not a number — is None, which the caller reads as "not
    enough": the CPU is slow, but a GPU that runs out of memory halfway through a
    meeting loses the meeting.
    """
    run = runner or subprocess.run
    try:
        result = run(
            list(NVIDIA_SMI),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            # The windowed build has no console, so without this every probe would
            # flash one up (the same reason ffmpeg runs hidden, see audio/ingest.py).
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        log.info("could not read the GPU's memory (%s); treating it as too small", exc)
        return None
    if getattr(result, "returncode", 1) != 0:
        log.info("nvidia-smi exited %s; treating the GPU as too small", result.returncode)
        return None
    for line in str(result.stdout or "").splitlines():
        if line.strip():
            try:
                return int(float(line.strip()))
            except ValueError:
                log.info("nvidia-smi said %r, not a memory size", line.strip())
                return None
    return None


@functools.cache
def cached_gpu_memory_mb() -> int | None:
    """Asked once per run: the setup screen polls the device plan every second while
    the model downloads, and a card does not grow memory in the meantime."""
    return gpu_memory_mb()


@dataclass(frozen=True)
class DevicePlan:
    """Where Whisper will run, and why — the setup screen says both."""

    device: str  # cpu|cuda
    #: configured: asr.device says so · no_cuda: no CUDA libraries anywhere ·
    #: low_vram: the GPU is under MIN_VRAM_MB · vram_unknown: its memory could not be
    #: read · gpu: CUDA libraries and enough memory
    reason: str
    vram_mb: int | None = None


def plan_device(
    config: Config | None, dirs: Sequence[Path], *, vram: VramQuery | None = None
) -> DevicePlan:
    """The one decision ``probe_device`` and ``planned_device`` share.

    Only the automatic choice is gated on memory. ``asr.device = "cuda"`` is the user
    insisting, and the load-time fallback still catches a card that cannot cope.
    """
    configured = str(config.get("asr.device", "auto")) if config else "auto"
    if configured == "cpu":
        return DevicePlan("cpu", "configured")
    if configured != "auto":
        return DevicePlan("cuda", "configured")
    if not dirs:
        return DevicePlan("cpu", "no_cuda")
    memory = (vram or cached_gpu_memory_mb)()
    if memory is None:
        return DevicePlan("cpu", "vram_unknown")
    if memory < MIN_VRAM_MB:
        return DevicePlan("cpu", "low_vram", memory)
    return DevicePlan("cuda", "gpu", memory)


def probe_device(
    config: Config | None = None,
    *,
    app_home: Path | None = None,
    search_path: Sequence[str] | None = None,
    system_dirs: Sequence[str] = SYSTEM_CUDA_DIRS,
    environ: dict[str, str] | None = None,
    vram: VramQuery | None = None,
) -> tuple[str, str, list[str]]:
    """(device, compute_type, registered dirs). No CUDA anywhere → CPU, int8."""
    env = environ if environ is not None else os.environ
    configured_compute = str(config.get("asr.compute_type", "auto")) if config else "auto"
    dirs = cuda_library_dirs(
        configured=config.get("asr.cuda_dir") if config else None,
        app_home=app_home,
        search_path=search_path,
        system_dirs=system_dirs,
    )
    if plan_device(config, dirs, vram=vram).device == "cpu":
        env["CUDA_VISIBLE_DEVICES"] = ""
        compute = configured_compute if configured_compute != "auto" else "int8"
        return "cpu", compute, []
    registered = register_cuda_dirs(dirs)
    compute = configured_compute if configured_compute != "auto" else supported_compute_type()
    return "cuda", compute, registered


def device_plan(config: Config, *, vram: VramQuery | None = None) -> DevicePlan:
    """What ``probe_device`` would choose, without its side effects (it registers DLL
    folders and sets CUDA_VISIBLE_DEVICES): which model to fetch depends on it."""
    dirs = cuda_library_dirs(configured=config.get("asr.cuda_dir"))
    return plan_device(config, dirs, vram=vram)


def planned_device(config: Config, *, vram: VramQuery | None = None) -> str:
    return device_plan(config, vram=vram).device


def supported_compute_type() -> str:
    """float16 → int8_float32 → int8. On Pascal (GTX 1080) int8 is the right answer."""
    try:
        import ctranslate2

        supported = set(ctranslate2.get_supported_compute_types("cuda"))
    except Exception:  # pragma: no cover - no ctranslate2 or no GPU
        return "int8"
    for candidate in ("float16", "int8_float32", "int8"):
        if candidate in supported:
            return candidate
    return "int8"


def _default_factory(**kwargs: Any) -> Any:  # pragma: no cover - needs the real package
    from faster_whisper import WhisperModel

    return WhisperModel(**kwargs)


def decode_audio(wav: Path) -> Any:
    """A WAV as the 1D float32 mono array at 16 kHz that faster-whisper's
    ``detect_language`` requires.

    Prefers the library's own decoder so resampling matches what ``transcribe`` does
    internally; falls back to ``wave`` since our chunks are already 16 kHz mono int16.
    """
    try:
        from faster_whisper.audio import decode_audio as _decode

        return _decode(str(wav), sampling_rate=16000)
    except Exception:  # pragma: no cover - only when the helper moves or av is absent
        import wave

        import numpy as np

        with wave.open(str(wav), "rb") as handle:
            frames = handle.readframes(handle.getnframes())
            channels = handle.getnchannels()
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        if channels > 1:
            usable = audio.size - (audio.size % channels)
            audio = audio[:usable].reshape(-1, channels).mean(axis=1)
        return audio


class LocalAsr:
    """faster-whisper, loaded lazily and unloaded before the LLM stage."""

    name = "local"

    def __init__(
        self,
        config: Config,
        *,
        model_factory: ModelFactory | None = None,
        choice: ModelChoice | None = None,
        fetch: Callable[[str], Path] | None = None,
    ) -> None:
        self.config = config
        self.model_factory = model_factory or _default_factory
        self.choice = choice
        #: A choice the caller made is kept whatever the device; one resolved here was
        #: resolved *for* a device, and is resolved again if the device changes.
        self._given_choice = choice
        # Downloads a repo that is not on disk and returns its folder. Without it the repo
        # id goes to faster-whisper, which fetches it unseen into the Hugging Face cache.
        self.fetch = fetch
        self.model: Any = None
        self.device = "cpu"
        self.compute_type = "int8"
        self.registered_dll_dirs: list[str] = []
        self.fell_back = False
        self.warmups = 0

    # -- loading -----------------------------------------------------------

    def describe(self) -> dict[str, Any]:
        choice = self.choice or resolve(self.config, device=self.device)
        return {"name": choice.reference, "compute": self.compute_type, "device": self.device}

    def load(self) -> Any:
        if self.model is not None:
            return self.model
        device, compute, registered = probe_device(self.config)
        self.registered_dll_dirs = registered
        self.choice = self._given_choice or resolve(self.config, device=device)
        try:
            self.model = self._build(device, compute)
            self.device, self.compute_type = device, compute
            self._warmup()
        except Exception as exc:
            if device == "cpu" or not self._is_cuda_error(exc):
                raise
            log.warning("GPU load failed (%s); falling back to CPU/int8", exc)
            self.fell_back = True
            # The GPU's model is large-v3, 3 GB and several times slower than turbo on a
            # CPU. Keeping it after the fallback made a meeting take hours instead of
            # minutes; the CPU gets the CPU's model, fetched first if it is not here.
            self.choice = self._given_choice or resolve(self.config, device="cpu")
            self.model = self._build("cpu", "int8")
            self.device, self.compute_type = "cpu", "int8"
            self._warmup()
        return self.model

    def _build(self, device: str, compute_type: str) -> Any:
        choice = self.choice or resolve(self.config, device=device)
        if not choice.local and choice.repo_id and self.fetch is not None:
            folder = self.fetch(choice.repo_id)
            choice = ModelChoice(str(folder), local=True, repo_id=choice.repo_id)
            self.choice = choice
        cpu_threads = max(1, (os.cpu_count() or 4) - 2)
        return self.model_factory(
            model_size_or_path=choice.reference,
            device=device,
            compute_type=compute_type,
            cpu_threads=cpu_threads if device == "cpu" else 0,
        )

    @staticmethod
    def _is_cuda_error(exc: BaseException) -> bool:
        text = f"{type(exc).__name__}: {exc}".lower()
        return any(marker in text for marker in CUDA_ERROR_MARKERS)

    def _warmup(self) -> None:
        """Force the lazy GPU library load now, not mid-meeting."""
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "warmup.wav"
            with wave.open(str(path), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(16000)
                handle.writeframes(b"\x00\x00" * 8000)  # 0.5 s of silence
            self._raw_transcribe(path, language="en", initial_prompt=None, word_timestamps=False)
        self.warmups += 1

    # -- protocol ----------------------------------------------------------

    def _raw_transcribe(
        self, wav: Path, *, language: str | None, initial_prompt: str | None, word_timestamps: bool
    ) -> list[Any]:
        model = self.model if self.model is not None else self.load()
        segments, _info = model.transcribe(
            str(wav),
            language=language,
            initial_prompt=initial_prompt,
            vad_filter=True,
            word_timestamps=word_timestamps,
            condition_on_previous_text=False,  # the repetition-loop guard; do not remove
            beam_size=int(self.config.get("asr.beam_size", 5)),
        )
        return list(segments)

    def transcribe(
        self,
        wav: Path,
        *,
        language: str = "he",
        initial_prompt: str | None = None,
        word_timestamps: bool = True,
    ) -> list[Segment]:
        self.load()
        track = track_of(wav)
        speaker = "ME" if track == "me" else "THEM"
        out: list[Segment] = []
        for index, raw in enumerate(
            self._raw_transcribe(
                wav,
                language=language,
                initial_prompt=initial_prompt,
                word_timestamps=word_timestamps,
            )
        ):
            words = tuple(
                Word(
                    str(getattr(word, "word", "")).strip(),
                    float(getattr(word, "start", 0.0)),
                    float(getattr(word, "end", 0.0)),
                    float(getattr(word, "probability", 1.0)),
                )
                for word in (getattr(raw, "words", None) or ())
            )
            out.append(
                Segment(
                    id=index,
                    track=track,
                    speaker=speaker,
                    start=float(raw.start),
                    end=float(raw.end),
                    text=str(raw.text).strip(),
                    words=words,
                    avg_logprob=float(getattr(raw, "avg_logprob", 0.0)),
                    no_speech_prob=float(getattr(raw, "no_speech_prob", 0.0)),
                )
            )
        return out

    def detect_language(self, wav: Path) -> tuple[str, float]:
        model = self.load()
        detect = getattr(model, "detect_language", None)
        if detect is not None:
            try:
                # `transcribe` takes a path; `detect_language` does NOT — it wants a 1D
                # float32 array at 16 kHz and calls `.dtype` on whatever it is given. A
                # path here raised "'str' object has no attribute 'dtype'" and cost a
                # whole meeting.
                language, probability, *_rest = detect(decode_audio(wav))
                return str(language), float(probability)
            except Exception as exc:
                # Never let a detection-API change take the transcript with it: the
                # transcribe path below reaches the same answer, just more slowly.
                log.warning("detect_language failed (%s); falling back to transcribe", exc)
        _segments, info = model.transcribe(str(wav), language=None, vad_filter=True)
        return str(info.language), float(info.language_probability)

    def unload(self) -> None:
        """ASR and the LLM are never resident together (DESIGN.md §20.4)."""
        self.model = None
        gc.collect()
