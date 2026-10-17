"""Upshot's own ctranslate2 wheel: no Intel component, every input pinned (licence epic, S12).

The build (packaging/ctranslate2/build.sh) runs in CI, by hand to publish; these check what it
is built from and how it is published. Measured before it replaces the official wheel: GPU
transcripts word for word identical, CPU speed the same at equal thread counts.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (ROOT / "packaging" / "ctranslate2" / "build.sh").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github" / "workflows" / "ctranslate2.yml").read_text(encoding="utf-8")


def test_every_input_is_pinned() -> None:
    assert re.search(r"^CT2_COMMIT=[0-9a-f]{40}$", SCRIPT, re.M)
    assert '"$(git -C CTranslate2 rev-parse HEAD)" == "$CT2_COMMIT"' in SCRIPT
    for name in ("ONEDNN_SHA256", "CUDA_SHA256", "CUDART_SHA256"):
        assert re.search(rf"^{name}=[0-9a-f]{{64}}$", SCRIPT, re.M), name
        assert f'echo "${name}  ' in SCRIPT, f"{name} is checked"
    for name in ("PYBIND11_VERSION", "SETUPTOOLS_VERSION", "WHEEL_VERSION", "PEFILE_VERSION"):
        assert re.search(rf"^{name}=\d", SCRIPT, re.M), name
    assert "install_requirements.txt" not in SCRIPT, "the build tools are pinned here, not upstream"


def test_the_build_has_no_intel_component_and_no_cudnn() -> None:
    for flag in ("-DWITH_MKL=OFF", "-DOPENMP_RUNTIME=COMP", "-DWITH_CUDNN=OFF", "-DWITH_DNNL=ON"):
        assert flag in SCRIPT, flag
    assert 'libiomp5md.dll" python' not in SCRIPT and 'cudnn64_9.dll" python' not in SCRIPT
    # The built DLL is checked, not just the flags: Microsoft's OpenMP, no Intel OpenMP or MKL,
    # and no DLL in the wheel but ctranslate2.dll.
    assert 'grep -qx "VCOMP140.DLL"' in SCRIPT
    assert 'grep -qiE "libiomp5|^mkl_"' in SCRIPT and 'grep -aqi "mkl_blas"' in SCRIPT
    assert "unexpected DLLs in the wheel" in SCRIPT


def test_the_release_carries_its_licences_and_sources() -> None:
    names = ("LICENSE-ctranslate2.txt", "LICENSE-oneDNN.txt", "LICENSE-third-party.txt",
             "LICENSE-NVIDIA-CUDA.txt")  # fmt: skip
    for name in names:
        assert name in SCRIPT and name in WORKFLOW, name
    assert "THIRD-PARTY-PROGRAMS" in SCRIPT, "oneDNN's third-party notices go with it"
    # What ctranslate2 compiles in from its third_party folder, and pybind11, carry notices.
    compiled_in = (
        "spdlog cpu_features cutlass thrust", "avx_mathfun.h", "avx512_mathfun.h",
        "BS_thread_pool.hpp", 'd("pybind11").files',
    )  # fmt: skip
    for part in compiled_in:
        assert part in SCRIPT, part
    # The sources are archived as cloned, before the build writes into the tree, and hashed
    # in the build job like everything else.
    assert SCRIPT.index("tar czf") < SCRIPT.index("cmake -S CTranslate2")
    assert "ctranslate2-sources.zip" in SCRIPT and "ctranslate2-sources.zip" in WORKFLOW
    assert "zip -qr" not in WORKFLOW


def test_the_release_is_published_by_hand_and_checks_its_tag() -> None:
    assert "workflow_dispatch:" in WORKFLOW and "push:" not in WORKFLOW
    assert "github.event_name == 'workflow_dispatch'" in WORKFLOW, "a pull request never publishes"
    assert "github.ref == 'refs/heads/main'" in WORKFLOW
    assert "RELEASE: ${{ inputs.release }}" in WORKFLOW, "the input reaches the shell via env"
    assert '${{ inputs.release }}"' not in WORKFLOW
    assert "--prerelease" in WORKFLOW


def test_the_build_job_holds_no_write_token() -> None:
    """The build runs third-party code; only the publish job, after it, may write."""
    build, publish = WORKFLOW.split("\n  publish:\n", 1)
    assert "permissions: {}" in WORKFLOW
    assert "contents: read" in build and "contents: write" not in build
    assert "persist-credentials: false" in build
    assert "contents: write" in publish and "build.sh" not in publish
