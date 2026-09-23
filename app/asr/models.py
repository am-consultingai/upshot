"""Model resolution and download (DESIGN.md §11, §20.2).

Order: configured ``model_path`` → the app home's ``model/`` directory → the repo for the
device and the meeting language, downloaded by ``app.asr.model_manager`` into
``models/asr/`` → the bare repo id.
With ``model_path`` pointed at an existing CTranslate2 directory, first run downloads
nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app import paths
from app.config import Config
from app.log import get

log = get(__name__)

SEGMENTATION_URL = (
    "https://huggingface.co/csukuangfj/sherpa-onnx-pyannote-segmentation-3-0/"
    "resolve/main/model.onnx"
)
#: A GitHub release asset, so the diarization path needs no Hugging Face account at all.
EMBEDDING_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-recongition-models/wespeaker_en_voxceleb_CAM%2B%2B.onnx"
)

GPU_REPO = "ivrit-ai/whisper-large-v3-ct2"
CPU_REPO = "ivrit-ai/whisper-large-v3-turbo-ct2"

#: Stock Whisper, for meetings the user has said are not in Hebrew. The ivrit-ai models
#: are fine-tuned on Hebrew and their language detection leans to it: an English meeting
#: came out pinned as Hebrew on a stranger's machine (ClickUp z8tj1haczh). These are the
#: same two architectures, converted by the people faster-whisper itself maps
#: "large-v3" and "turbo" to, so sizes and speed are unchanged.
GPU_REPO_MULTILINGUAL = "Systran/faster-whisper-large-v3"
CPU_REPO_MULTILINGUAL = "mobiuslabsgmbh/faster-whisper-large-v3-turbo"

#: What each download weighs (DESIGN.md §20.2), so the setup screen can say so before it
#: starts. The manager learns the exact figure from the hub once the download begins.
REPO_BYTES: dict[str, int] = {
    GPU_REPO: 3_090_000_000,
    CPU_REPO: 1_620_000_000,
    GPU_REPO_MULTILINGUAL: 3_090_000_000,
    CPU_REPO_MULTILINGUAL: 1_620_000_000,
}

MODEL_FILES = ("model.bin", "config.json")


@dataclass(frozen=True)
class ModelChoice:
    """What the backend will load, and whether it is already on disk."""

    reference: str
    local: bool
    repo_id: str | None = None

    @property
    def path(self) -> Path | None:
        return Path(self.reference) if self.local else None


def looks_like_model_dir(path: Path) -> bool:
    return path.is_dir() and all((path / name).exists() for name in MODEL_FILES)


def repo_for_profile(profile: str, *, hebrew: bool = True) -> str:
    gpu = profile in ("gpu-live", "remote-worker")
    if hebrew:
        return GPU_REPO if gpu else CPU_REPO
    return GPU_REPO_MULTILINGUAL if gpu else CPU_REPO_MULTILINGUAL


def wants_hebrew_model(config: Config) -> bool:
    """The Hebrew fine-tune, unless every meeting is pinned to another language.

    "Detect" keeps it on purpose: that is what every install before the setup screen
    ran, so an existing user's model does not change under them, and most meetings here
    are Hebrew. Only a fixed non-Hebrew language — the setup screen's "English" — moves
    to stock Whisper, which transcribes English as English.
    """
    return not (config.language_mode == "fixed" and config.default_language != "he")


def resolve(config: Config, *, device: str = "cpu") -> ModelChoice:
    configured = config.get("asr.model_path")
    if configured:
        path = Path(str(configured)).expanduser()
        if looks_like_model_dir(path):
            return ModelChoice(str(path), local=True)
        log.warning("asr.model_path %s is not a CTranslate2 model directory", path)
    home_model = paths.app_home() / "model"
    if looks_like_model_dir(home_model):
        return ModelChoice(str(home_model), local=True)
    repo = str(config.get("asr.model_repo") or "") or repo_for_profile(
        "gpu-live" if device == "cuda" else "cpu-deferred", hebrew=wants_hebrew_model(config)
    )
    from app.asr.model_manager import VERIFIED, target_for

    managed = target_for(repo)
    if looks_like_model_dir(managed) and (managed / VERIFIED).is_file():
        return ModelChoice(str(managed), local=True, repo_id=repo)
    return ModelChoice(repo, local=False, repo_id=repo)


def any_model_on_disk(config: Config) -> bool:
    """Whether a speech model this config would load is already here, on either device.

    Never raises: it decides whether an existing install skips first-run setup, and a
    question like that must not be able to stop the application from starting.
    """
    try:
        return any(resolve(config, device=device).local for device in ("cpu", "cuda"))
    except Exception as exc:  # pragma: no cover - a filesystem that refuses to be read
        log.warning("could not tell whether a speech model is on disk: %s", exc)
        return False


def ensure(config: Config, *, device: str = "cpu", allow_download: bool = True) -> ModelChoice:
    """Resolve, downloading only when nothing local is available."""
    choice = resolve(config, device=device)
    if choice.local or not allow_download:
        if not choice.local and not allow_download:
            raise FileNotFoundError(
                f"no local ASR model and downloads are disabled (would fetch {choice.reference})"
            )
        return choice
    log.info("no local model; faster-whisper will fetch %s on first use", choice.reference)
    return choice


@dataclass(frozen=True)
class DiarizationModels:
    segmentation: Path
    embedding: Path

    @property
    def present(self) -> bool:
        return self.segmentation.exists() and self.embedding.exists()


def diarization_dir(config: Config) -> Path:
    configured = config.get("asr.diarization_dir")
    if configured:
        return Path(str(configured)).expanduser()
    return paths.app_home() / "models" / "diarization"


def resolve_diarization(config: Config) -> DiarizationModels:
    """Configured paths win; otherwise the app home's diarization directory."""
    directory = diarization_dir(config)
    segmentation = config.get("asr.diarization_segmentation_path") or (
        directory / "segmentation.onnx"
    )
    embedding = config.get("asr.diarization_embedding_path") or (directory / "embedding.onnx")
    return DiarizationModels(Path(str(segmentation)), Path(str(embedding)))


def download_diarization(config: Config, *, timeout: float = 300.0) -> DiarizationModels:
    """Fetch the two ONNX models. ~37 MB, no account, no token (DECISIONS.md D28)."""
    import urllib.request

    models = resolve_diarization(config)
    for target, url in ((models.segmentation, SEGMENTATION_URL), (models.embedding, EMBEDDING_URL)):
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(target.suffix + ".part")
        log.info("downloading %s → %s", url.rsplit("/", 1)[-1], target)
        with urllib.request.urlopen(url, timeout=timeout) as response, partial.open("wb") as out:
            while chunk := response.read(1 << 20):
                out.write(chunk)
        partial.replace(target)
    return models
