"""Model resolution and download (DESIGN.md §11, §20.2).

Order: configured ``model_path`` → the app home's ``model/`` directory → the repo id for
the profile. With ``model_path`` pointed at an existing CTranslate2 directory, first run
downloads nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app import paths
from app.config import Config
from app.log import get

log = get(__name__)

GPU_REPO = "ivrit-ai/whisper-large-v3-ct2"
CPU_REPO = "ivrit-ai/whisper-large-v3-turbo-ct2"

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


def repo_for_profile(profile: str) -> str:
    return GPU_REPO if profile in ("gpu-live", "remote-worker") else CPU_REPO


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
        "gpu-live" if device == "cuda" else "cpu-deferred"
    )
    return ModelChoice(repo, local=False, repo_id=repo)


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
