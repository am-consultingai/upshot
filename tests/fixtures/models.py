"""Stand-in speech models, installed where the installer would have put them.

Only the files ``models.resolve`` checks for, with a verified marker for the pinned
revision: enough for the app to consider the installation whole. Never loadable.
"""

from __future__ import annotations

from pathlib import Path

from app.asr.model_manager import VERIFIED, target_for
from app.asr.models import MODELS, ROLES


def install_models(home: Path, roles: tuple[str, ...] = ROLES) -> dict[str, Path]:
    placed = {}
    for role in roles:
        model = MODELS[role]
        target = target_for(model.repo, home)
        target.mkdir(parents=True, exist_ok=True)
        (target / "model.bin").write_bytes(b"m")
        (target / "config.json").write_text("{}", encoding="utf-8")
        (target / VERIFIED).write_text(model.marker, encoding="utf-8")
        placed[role] = target
    return placed
