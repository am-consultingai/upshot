"""First-run setup (ClickUp z8tj1had06, z8tj1haczh, z8tj1had07).

What the /welcome screen stands on: the persisted ``setup.done`` flag and who is spared
the screen, the one speech model whatever the language or the device (D60), the VRAM
gate on the automatic GPU choice, and a GPU that fails to load falling back to the same
model on the CPU.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from app.asr import model_manager
from app.asr.local import (
    MIN_VRAM_MB,
    LocalAsr,
    gpu_memory_mb,
    plan_device,
    probe_device,
)
from app.asr.model_manager import VERIFIED, target_for
from app.asr.models import REPO, resolve
from app.config import Config, default_config

# ------------------------------------------------------------------ the setup flag


def place_model(app_home: Path, repo: str = REPO) -> Path:
    """A verified model where the manager would have put it."""
    target = target_for(repo, app_home)
    target.mkdir(parents=True)
    (target / "model.bin").write_bytes(b"m")
    (target / "config.json").write_text("{}", encoding="utf-8")
    (target / VERIFIED).write_text(repo, encoding="utf-8")
    return target


def old_config_file(tmp_path: Path) -> Path:
    """A config saved by a build that had no setup screen: every key but ``setup``."""
    data = default_config().as_dict()
    data.pop("setup")
    path = tmp_path / "app_config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_a_fresh_install_is_not_set_up(tmp_path: Path, app_home: Path) -> None:
    cfg = Config.load(file=tmp_path / "absent.json", environ={})
    assert cfg.get("setup.done") is False


def test_an_existing_install_with_a_model_is_not_sent_through_setup(
    tmp_path: Path, app_home: Path
) -> None:
    place_model(app_home)
    cfg = Config.load(file=old_config_file(tmp_path), environ={})
    assert cfg.get("setup.done") is True
    # And the next save keeps it, so the answer does not depend on the model staying.
    cfg.save()
    assert json.loads((tmp_path / "app_config.json").read_text())["setup"]["done"] is True


def test_an_existing_install_without_a_model_still_gets_setup(
    tmp_path: Path, app_home: Path
) -> None:
    cfg = Config.load(file=old_config_file(tmp_path), environ={})
    assert cfg.get("setup.done") is False


def test_a_model_path_counts_as_a_model(tmp_path: Path, app_home: Path) -> None:
    model = tmp_path / "ct2"
    model.mkdir()
    (model / "model.bin").write_bytes(b"m")
    (model / "config.json").write_text("{}", encoding="utf-8")
    path = old_config_file(tmp_path)
    data = json.loads(path.read_text())
    data["asr"]["model_path"] = str(model)
    path.write_text(json.dumps(data), encoding="utf-8")
    assert Config.load(file=path, environ={}).get("setup.done") is True


def test_a_config_saved_since_is_taken_at_its_word(tmp_path: Path, app_home: Path) -> None:
    """``setup.done: false`` written by this build means setup is still to do, model or not."""
    place_model(app_home)
    path = tmp_path / "app_config.json"
    default_config().save(path)
    assert json.loads(path.read_text())["setup"]["done"] is False
    assert Config.load(file=path, environ={}).get("setup.done") is False


def test_the_environment_still_decides_setup(tmp_path: Path, app_home: Path) -> None:
    cfg = Config.load(file=old_config_file(tmp_path), environ={"UP_SETUP__DONE": "true"})
    assert cfg.get("setup.done") is True


def test_setup_is_marked_done_through_the_settings_api(tmp_path: Path, app_home: Path) -> None:
    from tests.fixtures.api import build_harness

    client = build_harness(tmp_path).client()
    assert client.get("/api/settings").json()["config"]["setup"]["done"] is False
    response = client.put("/api/settings", json={"values": {"setup.done": True}})
    assert response.status_code == 200
    assert response.json()["config"]["setup"]["done"] is True
    saved = json.loads((app_home / "app_config.json").read_text(encoding="utf-8"))
    assert saved["setup"]["done"] is True


def test_the_seed_reset_leaves_setup_done_unless_asked(
    tmp_path: Path, app_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The e2e specs share one server: a welcome spec that dies must not strand the rest."""
    from tests.fixtures.api import build_harness

    monkeypatch.setenv("UP_TEST_MODE", "1")
    client = build_harness(tmp_path).client()
    client.post("/api/test/seed", json={"reset": True, "setup_done": False})
    assert client.get("/api/settings").json()["config"]["setup"]["done"] is False
    client.post("/api/test/seed", json={"reset": True})
    config = client.get("/api/settings").json()["config"]
    assert config["setup"]["done"] is True


def test_the_device_setting_is_checked() -> None:
    from app.errors import ConfigError

    with pytest.raises(ConfigError):
        default_config(asr__device="gpu")


# ------------------------------------------------------------------ language → model


@pytest.mark.parametrize("device", ["auto", "cpu", "cuda"])
def test_one_model_whatever_the_device(device: str) -> None:
    """ivrit-ai large-v3 on the GPU and the CPU alike: its turbo sibling is what turned
    English into Hebrew on machine B (D60)."""
    assert resolve(default_config(asr__device=device)).reference == REPO
    assert REPO == "ivrit-ai/whisper-large-v3-ct2"


def test_no_other_speech_model_is_named_anywhere_in_the_app() -> None:
    """D60 in one line: the turbo fine-tune and stock Whisper each broke on English or
    Hebrew, and a second model id is how either would come back."""
    import re

    pattern = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]*whisper[A-Za-z0-9_.-]*", re.I)
    named = {
        (str(path), match)
        for path in Path("app").rglob("*.py")
        for match in pattern.findall(path.read_text(encoding="utf-8"))
    }
    assert {match for _path, match in named} == {REPO}, sorted(named)


def test_an_old_config_cannot_bring_another_model_back(tmp_path: Path, app_home: Path) -> None:
    """A config saved by an earlier build may still say English, or name a repo."""
    path = tmp_path / "app_config.json"
    data = default_config().as_dict()
    data["asr"].update(
        language_mode="fixed", default_language="en", model_repo="mobiuslabsgmbh/turbo"
    )
    path.write_text(json.dumps(data), encoding="utf-8")
    cfg = Config.load(file=path, environ={})
    assert resolve(cfg).reference == REPO


def test_the_model_api_names_the_one_model_and_says_where_it_runs(
    tmp_path: Path, app_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.fixtures.api import build_harness

    monkeypatch.setattr(model_manager, "_managers", {})
    api = build_harness(tmp_path)
    api.services.config.set("asr.device", "cpu")
    client = api.client()

    model = client.get("/api/model").json()
    assert model["repo"] == REPO
    assert model["state"] == "missing"
    # The size is known before the download is started, for the screen to show.
    assert model["expected_bytes"] > 3_000_000_000
    assert model["free_bytes"] > 0
    assert (model["device"], model["device_reason"]) == ("cpu", "configured")
    assert model["min_vram_mb"] == MIN_VRAM_MB

    client.put("/api/settings", json={"values": {"asr.device": "cuda"}})
    assert client.get("/api/model").json()["repo"] == REPO, "the device never changes the model"


# ------------------------------------------------------------------ the VRAM gate


class FakeRunner:
    """``subprocess.run`` as nvidia-smi would answer it, recording how it was asked."""

    def __init__(self, stdout: str = "", returncode: int = 0, raises: Exception | None = None):
        self.stdout, self.returncode, self.raises = stdout, returncode, raises
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> Any:
        self.calls.append((argv, kwargs))
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, "")


def test_vram_is_read_from_nvidia_smi_hidden_and_with_a_timeout() -> None:
    runner = FakeRunner("8192\n")
    assert gpu_memory_mb(runner) == 8192
    argv, kwargs = runner.calls[0]
    assert argv[0] == "nvidia-smi"
    assert "--query-gpu=memory.total" in argv
    assert "--format=csv,noheader,nounits" in argv
    assert kwargs["timeout"] <= 10
    # No console flashing up from the windowed build on every probe.
    assert kwargs["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)


def test_the_first_gpu_is_the_one_that_counts() -> None:
    assert gpu_memory_mb(FakeRunner("2048\n24576\n")) == 2048


@pytest.mark.parametrize(
    "runner",
    [
        FakeRunner(raises=FileNotFoundError("nvidia-smi")),
        FakeRunner(raises=subprocess.TimeoutExpired("nvidia-smi", 5)),
        FakeRunner(raises=PermissionError("denied")),
        FakeRunner("", returncode=9),
        FakeRunner("NVIDIA-SMI has failed because it couldn't communicate with the driver"),
        FakeRunner(""),
    ],
    ids=["missing", "hung", "refused", "exit-code", "not-a-number", "no-output"],
)
def test_any_failure_to_read_vram_is_none(runner: FakeRunner) -> None:
    assert gpu_memory_mb(runner) is None


def test_the_automatic_choice_needs_enough_vram(tmp_path: Path) -> None:
    dirs = [tmp_path / "cuda"]
    auto = default_config()
    assert plan_device(auto, dirs, vram=lambda: 8192).device == "cuda"
    assert plan_device(auto, dirs, vram=lambda: MIN_VRAM_MB).device == "cuda"
    small = plan_device(auto, dirs, vram=lambda: MIN_VRAM_MB - 1)
    assert (small.device, small.reason, small.vram_mb) == ("cpu", "low_vram", MIN_VRAM_MB - 1)
    unknown = plan_device(auto, dirs, vram=lambda: None)
    assert (unknown.device, unknown.reason) == ("cpu", "vram_unknown")


def test_no_cuda_is_cpu_without_asking_the_gpu(tmp_path: Path) -> None:
    def never() -> int:
        raise AssertionError("nvidia-smi must not run when there is no CUDA to use")

    plan = plan_device(default_config(), [], vram=never)
    assert (plan.device, plan.reason) == ("cpu", "no_cuda")


def test_a_configured_device_is_taken_at_its_word(tmp_path: Path) -> None:
    dirs = [tmp_path / "cuda"]
    assert plan_device(default_config(asr__device="cpu"), dirs, vram=lambda: 24576).device == "cpu"
    insisted = plan_device(default_config(asr__device="cuda"), dirs, vram=lambda: 2048)
    assert (insisted.device, insisted.reason) == ("cuda", "configured")


def test_a_small_gpu_runs_on_the_cpu_at_probe_time(tmp_path: Path) -> None:
    cuda = tmp_path / "Scripts"
    cuda.mkdir()
    (cuda / "cublas64_12.dll").touch()
    config = default_config(asr__cuda_dir=str(cuda))
    env: dict[str, str] = {}
    device, compute, registered = probe_device(
        config,
        app_home=tmp_path / "home",
        search_path=[],
        system_dirs=(),
        environ=env,
        vram=lambda: 2048,
    )
    assert (device, compute, registered) == ("cpu", "int8", [])
    assert env["CUDA_VISIBLE_DEVICES"] == ""


# ------------------------------------------------------------------ CPU fallback


class Model:
    def transcribe(self, *args: Any, **kwargs: Any) -> tuple[list[Any], Any]:
        return [], None


def test_a_gpu_that_fails_to_load_falls_back_to_the_same_model_on_the_cpu(
    app_home: Path,
) -> None:
    built: list[tuple[str, str]] = []

    def factory(**kwargs: Any) -> Model:
        built.append((str(kwargs["device"]), str(kwargs["model_size_or_path"])))
        if kwargs["device"] == "cuda":
            raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
        return Model()

    config = default_config(asr__device="cuda", asr__compute_type="int8")
    backend = LocalAsr(config, model_factory=factory)
    backend.load()
    assert built == [("cuda", REPO), ("cpu", REPO)]
    assert backend.fell_back and backend.choice is not None
    assert backend.choice.reference == REPO
    assert backend.describe() == {"name": REPO, "compute": "int8", "device": "cpu"}


def test_the_fallback_does_not_fetch_again(app_home: Path) -> None:
    fetched: list[str] = []

    def fetch(repo: str) -> Path:
        fetched.append(repo)
        return app_home / repo.replace("/", "__")

    def factory(**kwargs: Any) -> Model:
        if kwargs["device"] == "cuda":
            raise RuntimeError("CUDA driver version is insufficient")
        return Model()

    config = default_config(asr__device="cuda", asr__compute_type="int8")
    LocalAsr(config, model_factory=factory, fetch=fetch).load()
    assert fetched == [REPO], "the CPU loads the files the GPU attempt already fetched"


def test_a_model_the_caller_chose_survives_the_fallback(tmp_path: Path) -> None:
    from app.asr.models import ModelChoice

    built: list[str] = []

    def factory(**kwargs: Any) -> Model:
        built.append(str(kwargs["model_size_or_path"]))
        if kwargs["device"] == "cuda":
            raise RuntimeError("cuDNN failed to initialize")
        return Model()

    mine = ModelChoice(str(tmp_path / "mine"), local=True)
    config = default_config(asr__device="cuda", asr__compute_type="int8")
    LocalAsr(config, model_factory=factory, choice=mine).load()
    assert built == [mine.reference, mine.reference]
