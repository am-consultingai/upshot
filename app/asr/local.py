"""faster-whisper backend (EXECUTION-PLAN.md Phase 5).

The two details that decide whether this works on Windows at all:

* CTranslate2 loads cuBLAS lazily with a plain ``LoadLibrary`` at the first matrix
  multiply, which does **not** search ``add_dll_directory`` paths — so every CUDA library
  directory is both registered *and* prepended to ``PATH``.
* A warmup inference runs at load, so a broken GPU stack fails at startup rather than
  halfway through a meeting.
"""

from __future__ import annotations

import gc
import os
import sys
import wave
from collections.abc import Callable, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from app import paths
from app.asr.backend import Segment, Word
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


def probe_device(
    config: Config | None = None,
    *,
    app_home: Path | None = None,
    search_path: Sequence[str] | None = None,
    system_dirs: Sequence[str] = SYSTEM_CUDA_DIRS,
    environ: dict[str, str] | None = None,
) -> tuple[str, str, list[str]]:
    """(device, compute_type, registered dirs). No CUDA anywhere → CPU, int8."""
    env = environ if environ is not None else os.environ
    configured_device = str(config.get("asr.device", "auto")) if config else "auto"
    configured_compute = str(config.get("asr.compute_type", "auto")) if config else "auto"
    dirs = cuda_library_dirs(
        configured=config.get("asr.cuda_dir") if config else None,
        app_home=app_home,
        search_path=search_path,
        system_dirs=system_dirs,
    )
    if configured_device == "cpu" or (configured_device == "auto" and not dirs):
        env["CUDA_VISIBLE_DEVICES"] = ""
        compute = configured_compute if configured_compute != "auto" else "int8"
        return "cpu", compute, []
    registered = register_cuda_dirs(dirs)
    compute = configured_compute if configured_compute != "auto" else supported_compute_type()
    return "cuda", compute, registered


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


class LocalAsr:
    """faster-whisper, loaded lazily and unloaded before the LLM stage."""

    name = "local"

    def __init__(
        self,
        config: Config,
        *,
        model_factory: ModelFactory | None = None,
        choice: ModelChoice | None = None,
    ) -> None:
        self.config = config
        self.model_factory = model_factory or _default_factory
        self.choice = choice
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
        self.choice = self.choice or resolve(self.config, device=device)
        try:
            self.model = self._build(device, compute)
            self.device, self.compute_type = device, compute
            self._warmup()
        except Exception as exc:
            if device == "cpu" or not self._is_cuda_error(exc):
                raise
            log.warning("GPU load failed (%s); falling back to CPU/int8", exc)
            self.fell_back = True
            self.model = self._build("cpu", "int8")
            self.device, self.compute_type = "cpu", "int8"
            self._warmup()
        return self.model

    def _build(self, device: str, compute_type: str) -> Any:
        choice = self.choice or resolve(self.config, device=device)
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
        track = wav.parent.name if wav.parent.name in ("me", "them") else "them"
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
            language, probability, *_rest = detect(str(wav))
            return str(language), float(probability)
        _segments, info = model.transcribe(str(wav), language=None, vad_filter=True)
        return str(info.language), float(info.language_probability)

    def unload(self) -> None:
        """ASR and the LLM are never resident together (DESIGN.md §20.4)."""
        self.model = None
        gc.collect()
