"""First run (TECHNICAL-DESIGN.md §17).

Idempotent by construction: every step checks for what it would create, so running it
twice changes nothing and running it after an upgrade fills only what is missing.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app import paths
from app.config import Config, default_config
from app.log import get

log = get(__name__)

TASK_NAME = "Upshot"
APP_USER_MODEL_ID = "Upshot.App"


class BootstrapError(RuntimeError):
    """First run cannot continue, and the message says what the user must do."""


@dataclass
class Step:
    name: str
    ok: bool
    detail: str = ""
    changed: bool = False


@dataclass
class BootstrapReport:
    steps: list[Step] = field(default_factory=list)
    profile: str = "cpu-deferred"
    model: str = ""
    home: Path = field(default_factory=paths.app_home)

    @property
    def ok(self) -> bool:
        return all(step.ok for step in self.steps)

    @property
    def changed(self) -> bool:
        return any(step.changed for step in self.steps)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "changed": self.changed,
            "profile": self.profile,
            "model": self.model,
            "home": str(self.home),
            "steps": [
                {"name": s.name, "ok": s.ok, "detail": s.detail, "changed": s.changed}
                for s in self.steps
            ],
        }


def choose_profile(config: Config) -> tuple[str, str]:
    """CUDA probe → profile → model. Pascal-class GPUs get int8 (DESIGN.md §20)."""
    from app.asr.local import planned_device
    from app.asr.models import resolve

    configured = config.profile
    # The same decision the backend makes at load, VRAM gate included, so the profile
    # and the model named here are the ones that will actually run.
    device = planned_device(config)
    profile = (
        configured if configured != "auto" else ("gpu-live" if device == "cuda" else "cpu-deferred")
    )
    choice = resolve(config, device=device)
    return profile, choice.reference


def register_app_user_model_id(app_id: str = APP_USER_MODEL_ID) -> bool:
    from app.tray import register_app_user_model_id as register

    return register(app_id)


def task_scheduler_command(exe: Path, task_name: str = TASK_NAME) -> list[str]:
    """Autostart at logon via Task Scheduler — it restarts on failure, a shortcut doesn't."""
    return [
        "schtasks",
        "/Create",
        "/F",
        "/SC",
        "ONLOGON",
        "/TN",
        task_name,
        "/TR",
        f'"{exe}"',
        "/RL",
        "LIMITED",
    ]


def task_exists(task_name: str = TASK_NAME) -> bool:
    if sys.platform != "win32":
        return False
    try:  # pragma: no cover - Windows only
        result = subprocess.run(
            ["schtasks", "/Query", "/TN", task_name],
            capture_output=True,
            timeout=20,
            check=False,
        )
        return result.returncode == 0
    except Exception:
        return False


def register_logon_task(exe: Path | None = None, *, task_name: str = TASK_NAME) -> Step:
    if sys.platform != "win32":
        return Step("logon_task", True, "skipped: Task Scheduler is Windows-only")
    if task_exists(task_name):  # pragma: no cover - Windows only
        return Step("logon_task", True, f"{task_name} already registered")
    target = exe or Path(sys.executable)
    try:  # pragma: no cover - Windows only
        result = subprocess.run(
            task_scheduler_command(target, task_name),
            capture_output=True,
            timeout=30,
            check=False,
        )
    except Exception as exc:
        return Step("logon_task", False, f"schtasks failed: {exc}")
    ok = result.returncode == 0
    # schtasks explains a refusal ("Access is denied." for a standard user) on stderr only.
    output = result.stdout if ok else result.stderr or result.stdout
    detail = output.decode(errors="replace").strip() or f"schtasks exit {result.returncode}"
    return Step("logon_task", ok, detail, changed=ok)


def unregister_logon_task(task_name: str = TASK_NAME) -> bool:  # pragma: no cover - Windows only
    if sys.platform != "win32":
        return False
    result = subprocess.run(
        ["schtasks", "/Delete", "/F", "/TN", task_name], capture_output=True, check=False
    )
    return result.returncode == 0


def run(
    config: Config | None = None,
    *,
    allow_download: bool = True,
    register_task: bool = True,
    exe: Path | None = None,
) -> BootstrapReport:
    """The whole first-run sequence. Safe to run on every launch."""
    home = paths.ensure_app_home()
    report = BootstrapReport(home=home)
    cfg = config or default_config()

    report.steps.append(Step("app_home", True, str(home), changed=not (home / "logs").exists()))
    (home / "logs").mkdir(exist_ok=True)

    data_root = cfg.data_root
    existed = data_root.exists()
    data_root.mkdir(parents=True, exist_ok=True)
    report.steps.append(Step("data_root", True, str(data_root), changed=not existed))

    from app.db import migrate as migrations
    from app.db.dao import capabilities, connect

    db_existed = paths.db_path().exists()
    conn = connect(fts=str(cfg.get("db.fts", "auto")) != "off")
    version = migrations.current_version(conn)
    caps = capabilities(conn)
    conn.close()
    report.steps.append(
        Step("database", True, f"schema_version={version}, fts5={caps.fts}", changed=not db_existed)
    )

    profile, model = choose_profile(cfg)
    report.profile, report.model = profile, model
    from app.asr.models import looks_like_model_dir

    model_local = looks_like_model_dir(Path(model)) if model else False
    if not model_local and not allow_download:
        report.steps.append(
            Step(
                "model",
                False,
                f"no local ASR model and downloads are disabled — set asr.model_path to an "
                f"existing CTranslate2 directory, or allow the {model} download",
            )
        )
    else:
        report.steps.append(
            Step("model", True, ("local: " if model_local else "will download: ") + model)
        )
    report.steps.append(Step("profile", True, profile))

    report.steps.append(
        Step(
            "app_user_model_id",
            True,
            APP_USER_MODEL_ID if register_app_user_model_id() else "skipped: not Windows",
        )
    )
    if register_task:
        report.steps.append(register_logon_task(exe))
    else:
        report.steps.append(Step("logon_task", True, "skipped: not requested"))

    config_path = paths.config_path()
    if not config_path.exists():
        cfg.save(config_path)
        report.steps.append(Step("config_file", True, str(config_path), changed=True))
    else:
        report.steps.append(Step("config_file", True, f"{config_path} already exists"))

    if not report.ok:
        failed = [step for step in report.steps if not step.ok]
        raise BootstrapError("; ".join(f"{step.name}: {step.detail}" for step in failed))
    return report


def preflight(config: Config, *, min_free_bytes: int = 2 * 1024**3) -> list[str]:
    """Before arming: devices, disk, model. Returns the problems, empty when ready."""
    import shutil

    problems: list[str] = []
    root = config.data_root
    root.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(str(root)).free
    if free < min_free_bytes:
        problems.append(f"only {free // 1024**2} MB free under {root}")
    if (
        os.environ.get("UP_SKIP_DEVICE_PREFLIGHT") != "1"
        and str(config.get("audio.capture", "wasapi")) == "wasapi"
    ):
        from app.audio.devices import NoDeviceError, default_render, loopback_for

        try:
            loopback_for(default_render())
        except (NoDeviceError, Exception) as exc:
            problems.append(f"no loopback capture endpoint: {exc}")
    problems.extend(config.warnings())
    return problems
