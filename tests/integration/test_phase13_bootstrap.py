from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app import bootstrap, paths
from app.config import default_config


def test_bootstrap_idempotent(tmp_path: Path, app_home: Path) -> None:
    config = default_config()
    config.set("data_root", str(tmp_path / "meetings"))

    first = bootstrap.run(config, register_task=False)
    assert first.ok and first.changed
    assert paths.db_path().exists()
    assert paths.config_path().exists()
    assert (app_home / "logs").is_dir()
    assert config.data_root.is_dir()

    second = bootstrap.run(config, register_task=False)
    assert second.ok
    assert second.changed is False, "a second run creates nothing"
    assert {step.name for step in second.steps} == {step.name for step in first.steps}
    assert second.profile == first.profile and second.model == first.model

    from app.db import migrate as migrations
    from app.db.dao import connect

    conn = connect()
    assert migrations.current_version(conn) == max(m.version for m in migrations.discover())
    conn.close()


def test_bootstrap_offline(tmp_path: Path, app_home: Path) -> None:
    """No model and no downloads: a clear, actionable error — and never a hang."""
    config = default_config()
    config.set("data_root", str(tmp_path / "meetings"))
    with pytest.raises(bootstrap.BootstrapError) as info:
        bootstrap.run(config, allow_download=False, register_task=False)
    message = str(info.value)
    assert "asr.model_path" in message
    assert "download" in message


def test_bootstrap_adopts_an_existing_model(tmp_path: Path, app_home: Path) -> None:
    model_dir = tmp_path / "ct2"
    model_dir.mkdir()
    (model_dir / "model.bin").write_bytes(b"x")
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    config = default_config()
    config.set("data_root", str(tmp_path / "meetings"))
    config.set("asr.model_path", str(model_dir))

    report = bootstrap.run(config, allow_download=False, register_task=False)
    assert report.ok
    assert report.model == str(model_dir)
    model_step = next(step for step in report.steps if step.name == "model")
    assert model_step.detail.startswith("local: ")


def test_bootstrap_report_is_json(tmp_path: Path, app_home: Path) -> None:
    config = default_config()
    config.set("data_root", str(tmp_path / "meetings"))
    payload = json.loads(json.dumps(bootstrap.run(config, register_task=False).as_dict()))
    assert payload["ok"] is True
    assert {"profile", "model", "home", "steps"} <= set(payload)


def test_logon_task_command_shape() -> None:
    command = bootstrap.task_scheduler_command(Path(r"C:\Apps\upshot.exe"))
    assert command[:2] == ["schtasks", "/Create"]
    assert "/SC" in command and command[command.index("/SC") + 1] == "ONLOGON"
    assert command[command.index("/TN") + 1] == "Upshot"
    assert "upshot.exe" in command[command.index("/TR") + 1]


def test_logon_task_skipped_off_windows(tmp_path: Path, app_home: Path) -> None:
    step = bootstrap.register_logon_task(Path("x"))
    if sys.platform == "win32":  # pragma: no cover - Windows only
        pytest.skip("this asserts the non-Windows path")
    assert step.ok and step.detail.startswith("skipped:")
    assert bootstrap.task_exists() is False


def test_preflight_reports_problems(tmp_path: Path, app_home: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    config = default_config()
    config.set("data_root", str(tmp_path / "OneDrive" / "meetings"))
    config.set("audio.capture", "synthetic")
    problems = bootstrap.preflight(config)
    assert any("synced folder" in problem for problem in problems)

    clean = default_config()
    clean.set("data_root", str(tmp_path / "meetings"))
    clean.set("audio.capture", "synthetic")
    assert bootstrap.preflight(clean) == []


def test_entry_point_runs_selftest(tmp_path: Path) -> None:
    """`--selftest imports` is the tripwire the frozen build runs; it works unfrozen too."""
    import os

    env = {**os.environ, "UP_HOME": str(tmp_path / "home")}
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.tray import main; raise SystemExit(main())",
            "--selftest",
            "imports",
        ],
        capture_output=True,
        env=env,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "import_all_modules" in result.stdout


def test_the_frozen_entry_script_calls_main(tmp_path: Path) -> None:
    """PyInstaller runs app/tray.py as __main__, not `from app.tray import main`.

    The first freeze on machine A defined main() and exited 0 without calling it: no
    tray, no server, and every --selftest in the build "passed" with no report written.
    """
    import os

    root = Path(__file__).resolve().parents[2]
    report = tmp_path / "clock.json"
    env = {**os.environ, "UP_HOME": str(tmp_path / "home"), "PYTHONPATH": str(root)}
    result = subprocess.run(
        [
            sys.executable,
            str(root / "app" / "tray.py"),
            "--selftest",
            "clock",
            "--report",
            str(report),
        ],
        capture_output=True,
        env=env,
        text=True,
        timeout=120,
        cwd=root,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(report.read_text(encoding="utf-8"))["suite"] == "clock"


def test_entry_point_runs_bootstrap(tmp_path: Path) -> None:
    import os

    env = {**os.environ, "UP_HOME": str(tmp_path / "home")}
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.tray import main; raise SystemExit(main())",
            "--bootstrap",
        ],
        capture_output=True,
        env=env,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert (tmp_path / "home" / "index.db").exists()
    # The installer's startup shortcut is the autostart; first run registers no task,
    # which on Windows would also be a real one this test left behind.
    steps = {step["name"]: step for step in payload["steps"]}
    assert steps["logon_task"]["detail"] == "skipped: not requested"


def test_packaging_files_exist() -> None:
    spec = Path("packaging/upshot.spec").read_text(encoding="utf-8")
    for hidden in (
        "ctranslate2",
        "onnxruntime.capi",
        "keyring.backends.Windows",
        "pystray._win32",
        "comtypes",
    ):
        assert hidden in spec, f"{hidden} must be declared as a hidden import"
    # No templates any more: the summary is the document the prompt wrote, so there is
    # no Jinja layout left to bundle.
    assert "app/llm/prompts" in spec

    installer = Path("packaging/installer.iss").read_text(encoding="utf-8")
    assert "PrivilegesRequired=lowest" in installer, "the installer must not need admin"
    assert "{localappdata}" in installer
    # No logon task since --bootstrap stopped creating one (autostart is the installer's
    # {userstartup} shortcut), so there is nothing for the uninstaller to remove.
    assert "schtasks" not in installer
    assert "AppId=" in installer, "an upgrade finds the installed copy by its AppId"

    build = Path("packaging/build.ps1").read_text(encoding="utf-8")
    assert '"--selftest", $suite' in build and '"imports"' in build, (
        "the build must run the hidden-import tripwire"
    )
    assert "npm run build" in build and "pyinstaller" in build
    # Windows PowerShell 5.1 reads a BOM-less script as ANSI: one em dash broke the build.
    for script in ("build.ps1", "sign.ps1", "installer.iss"):
        assert Path("packaging", script).read_bytes().isascii(), f"{script} must stay ASCII"
