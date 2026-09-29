"""The speech models, and where they are on disk (DESIGN.md §11, §20.2, DECISIONS.md D80).

Three roles, each one pinned repo revision, downloaded by ``app.asr.model_manager`` into
``models/asr/<repo>`` at installation. ``asr.model_path`` overrides the Hebrew model's
folder for a developer who keeps a copy elsewhere.
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

#: The three speech models (DECISIONS.md D80, superseding D60), each pinned to a hub
#: revision so an upstream re-upload cannot silently change transcripts. All three are
#: downloaded at installation (``upshot.exe --prepare``) and only then (R12):
#:
#: - ``classifier``: Whisper small. It hears 5 × 30 s of the meeting's speech and says
#:   which language it was in, before either large model loads (``app/asr/classify.py``).
#:   As accurate as large-v3 at that on every recording measured, at a fifth of the cost.
#: - ``hebrew``: ivrit-ai large-v3, for a meeting that is mostly Hebrew. Its newest Hebrew
#:   large model (2025-10-27); its later uploads are turbo variants and format conversions.
#: - ``other``: stock Whisper large-v3, for every other language. ivrit cannot write
#:   them: on FLEURS it scored 99-103 % WER on Spanish, French and Russian.
#:
#: Large-v3 and never turbo for transcription (R6): turbo is smaller and less accurate.
CLASSIFIER = "classifier"
HEBREW = "hebrew"
OTHER = "other"


@dataclass(frozen=True)
class SpeechModel:
    """One of the three: which repo, at which revision, and what it weighs."""

    role: str
    repo: str
    revision: str
    #: What the download weighs (DESIGN.md §20.2), so the setup screen and the installer
    #: can say so before it starts. The manager learns the exact figure from the hub.
    size_bytes: int

    @property
    def marker(self) -> str:
        """What the verified marker holds: a marker for another revision is not ready."""
        return f"{self.repo}@{self.revision}"


#: In installation order: the small one first, so a failure shows up early.
MODELS: dict[str, SpeechModel] = {
    CLASSIFIER: SpeechModel(
        CLASSIFIER,
        "Systran/faster-whisper-small",
        "536b0662742c02347bc0e980a01041f333bce120",
        486_000_000,
    ),
    HEBREW: SpeechModel(
        HEBREW,
        "ivrit-ai/whisper-large-v3-ct2",
        "e9ed4a4a98d761b0f617d668303de2c514236c66",
        3_090_000_000,
    ),
    OTHER: SpeechModel(
        OTHER,
        "Systran/faster-whisper-large-v3",
        "edaa852ec7e145841d8ffdb056a99866b5f0a478",
        3_090_000_000,
    ),
}
ROLES: tuple[str, ...] = tuple(MODELS)

#: All three together, about 6.67 GB.
TOTAL_BYTES = sum(model.size_bytes for model in MODELS.values())

#: A meeting's language when nothing says otherwise: an empty meeting, a missing field.
DEFAULT_LANGUAGE = "he"

#: The language code that routes a meeting to the Hebrew model.
HEBREW_LANGUAGE = "he"


def role_for_language(language: str | None) -> str:
    """``he`` → the Hebrew model; anything else, or no language at all → stock large-v3."""
    return HEBREW if language == HEBREW_LANGUAGE else OTHER


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


def resolve(config: Config, role: str = HEBREW) -> ModelChoice:
    """Where one role's model is: the managed folder, verified at its pinned revision.

    ``asr.model_path`` is a developer's override for the Hebrew model only (a copy of it
    kept elsewhere); nothing a user sets reaches the other two. A model that is not on
    disk comes back with ``local=False``: the caller decides what that means.
    """
    model = MODELS[role]
    if role == HEBREW:
        configured = config.get("asr.model_path")
        if configured:
            path = Path(str(configured)).expanduser()
            if looks_like_model_dir(path):
                return ModelChoice(str(path), local=True, repo_id=model.repo)
            log.warning("asr.model_path %s is not a CTranslate2 model directory", path)
    from app.asr.model_manager import is_verified, target_for

    managed = target_for(model.repo)
    if is_verified(managed, model):
        return ModelChoice(str(managed), local=True, repo_id=model.repo)
    return ModelChoice(model.repo, local=False, repo_id=model.repo)


def resolve_all(config: Config) -> dict[str, ModelChoice]:
    return {role: resolve(config, role) for role in ROLES}


def any_model_on_disk(config: Config) -> bool:
    """Whether any of the speech models is already here: an install that has been used.

    Never raises: it decides whether an existing install skips first-run setup, and a
    question like that must not be able to stop the application from starting.
    """
    try:
        return any(choice.local for choice in resolve_all(config).values())
    except Exception as exc:  # pragma: no cover - a filesystem that refuses to be read
        log.warning("could not tell whether a speech model is on disk: %s", exc)
        return False


def ensure(config: Config, *, allow_download: bool = True, role: str = HEBREW) -> ModelChoice:
    """Resolve, downloading only when nothing local is available."""
    choice = resolve(config, role)
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

    from app.asr.model_manager import tls_context

    models = resolve_diarization(config)
    for target, url in ((models.segmentation, SEGMENTATION_URL), (models.embedding, EMBEDDING_URL)):
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(target.suffix + ".part")
        log.info("downloading %s → %s", url.rsplit("/", 1)[-1], target)
        with (
            urllib.request.urlopen(url, timeout=timeout, context=tls_context()) as response,
            partial.open("wb") as out,
        ):
            while chunk := response.read(1 << 20):
                out.write(chunk)
        partial.replace(target)
    return models
