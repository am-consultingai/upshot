"""scripts/windows/run-app.ps1 runs from source on the installed app's own models and GPU
libraries. It names their folders and markers itself (it checks them before Python is
set up), so these must stay what the app writes."""

from __future__ import annotations

import re
from pathlib import Path

from app.asr import cuda_libs
from app.asr.model_manager import VERIFIED, target_for
from app.asr.models import MODELS

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "windows" / "run-app.ps1"


def script() -> str:
    return SCRIPT.read_text(encoding="utf-8-sig")


def test_the_launcher_names_the_three_model_folders_the_app_installs(tmp_path: Path) -> None:
    block = re.search(r"\$speechModels = \[ordered\]@\{(.*?)\}", script(), re.S)
    assert block, "run-app.ps1 lost its $speechModels table"
    named = dict(re.findall(r'(\w+)\s*=\s*"([^"]+)"', block.group(1)))
    assert named == {role: target_for(model.repo, tmp_path).name for role, model in MODELS.items()}
    assert f'"{VERIFIED}"' in script()


def test_the_launcher_names_the_gpu_folder_the_app_installs(tmp_path: Path) -> None:
    relative = "\\".join(cuda_libs.target_dir(tmp_path).relative_to(tmp_path).parts)
    assert f'"{relative}"' in script()
    assert f'"{cuda_libs.MARKER}"' in script()


def test_the_launcher_has_no_model_or_cuda_folder_of_its_own() -> None:
    """No fallback: a missing model is an installation to repair (z8tj1hfr6w)."""
    text = script()
    assert "run-app.local.psd1" not in text
    assert not re.search(r"\$ModelPath|\$CudaDir\b", text)
