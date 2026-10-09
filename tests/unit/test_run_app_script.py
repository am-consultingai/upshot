"""scripts/windows/run-app.ps1 runs from source on the installed app's own models and GPU
libraries. It names the model folders and their marker itself (it checks them before
Python is set up), so these must stay what the app writes."""

from __future__ import annotations

import re
from pathlib import Path

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


def test_the_app_itself_decides_revisions_and_gpu_libraries() -> None:
    """The script checks only that the folders are there; the pinned revision and the GPU
    libraries are the app's own decision, the one --prepare makes (review of #17)."""
    text = script()
    for call in ("is_verified(target_for(m.repo), m)", "cuda_libs.wanted(c)",
                 "cuda_libs.ready()", "cuda_libs.usable_elsewhere(c)"):  # fmt: skip
        assert call in text, call
    check = text[text.index('python -c @"') :]
    check = check[: check.index('"@')]
    assert '"' not in check.split("\n", 1)[1], "Windows PowerShell mangles double quotes"


def test_the_launcher_has_no_model_or_cuda_folder_of_its_own() -> None:
    """No fallback: a missing model is an installation to repair (z8tj1hfr6w)."""
    text = script()
    assert "Import-PowerShellDataFile" not in text, "run-app.local.psd1 is never read"
    assert not re.search(r"\$ModelPath\b|\$CudaDir\b|UP_ASR__COMPUTE_TYPE", text, re.I)
