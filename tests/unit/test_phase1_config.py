from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import paths
from app.config import SECRET_KEYS, Config, default_config
from app.errors import ConfigError


def test_config_layer_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    file = tmp_path / "app_config.json"
    file.write_text(
        json.dumps(
            {
                "profile": "cpu-deferred",  # contested by env → env wins
                "job_policy": "when_idle",  # file only → file wins
                "ui": {"language": "he"},
            }
        ),
        encoding="utf-8",
    )
    env = {"UP_PROFILE": "gpu-live"}
    cfg = Config.load(file=file, environ=env)
    assert cfg.profile == "gpu-live"  # env beats file
    assert cfg.job_policy == "when_idle"  # file beats default
    assert cfg.ui_language == "he"  # file beats default
    assert cfg.summary_language == "auto"  # default survives: follow the meeting
    assert cfg.get("audio.chunk_s") == 60


def test_config_env_parses_json_scalars(tmp_path: Path) -> None:
    cfg = Config.load(
        file=tmp_path / "missing.json",
        environ={"UP_AUDIO__CHUNK_S": "45", "UP_DELIVERY__ATTACH_TRANSCRIPT": "true"},
    )
    assert cfg.get("audio.chunk_s") == 45
    assert cfg.get("delivery.attach_transcript") is True


def test_config_env_aliases(tmp_path: Path) -> None:
    cfg = Config.load(file=tmp_path / "x.json", environ={"WHISPER_MODEL_PATH": "/models/ct2"})
    assert cfg.get("asr.model_path") == "/models/ct2"


def test_config_rejects_bad_enum(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        Config.load(file=tmp_path / "x.json", environ={"UP_PROFILE": "banana"})


def test_config_rejects_wrong_type(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        Config.load(file=tmp_path / "x.json", environ={"UP_AUDIO__CHUNK_S": '"sixty"'})


def test_secrets_never_in_config_file(tmp_path: Path) -> None:
    cfg = default_config(secrets__backend="memory")
    cfg.set("llm.api_key", "sk-ant-SUPERSECRET")
    cfg.secrets.set("anthropic", "sk-ant-SUPERSECRET")
    path = cfg.save(tmp_path / "app_config.json")
    raw = path.read_bytes()
    assert b"SUPERSECRET" not in raw
    assert cfg.secrets.get("anthropic") == "sk-ant-SUPERSECRET"


def test_redacted_dump() -> None:
    cfg = default_config()
    for key in SECRET_KEYS:
        cfg.set(key, "actual-secret-value")
    dump = cfg.redacted_dump()
    text = json.dumps(dump)
    assert "actual-secret-value" not in text
    for key in SECRET_KEYS:
        node = dump
        for part in key.split("."):
            node = node[part]
        assert node == "***"


def test_fake_keyring_is_selected_by_config() -> None:
    assert default_config(secrets__backend="memory").secrets.name == "memory"
    assert default_config().secrets.name == "keyring"


def test_apphome_respects_env(app_home: Path) -> None:
    assert paths.app_home() == app_home
    assert paths.db_path().parent == app_home
    assert str(paths.config_path()).startswith(str(app_home))


def test_data_root_default(app_home: Path) -> None:
    cfg = default_config()
    assert cfg.data_root == app_home / "meetings"
    cfg.set("data_root", str(app_home / "elsewhere"))
    assert cfg.data_root == app_home / "elsewhere"


def test_synced_folder_warning(app_home: Path) -> None:
    for folder in ("C:/Users/am/OneDrive/meetings", "/home/am/Dropbox/x", "/x/Google Drive/y"):
        cfg = default_config()
        cfg.set("data_root", folder)
        warnings = cfg.warnings()
        assert warnings, folder
        assert "synced folder" in warnings[0]
    clean = default_config()
    clean.set("data_root", str(app_home / "meetings"))
    assert clean.warnings() == []


def test_env_override_does_not_become_permanent(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A launcher exports UP_* for one run; an unrelated save must not adopt it.

    The Windows launcher set UP_LLM__PROVIDER on every start. Saving anything at all then
    wrote that value into app_config.json, so the provider chosen on the Settings screen
    appeared to revert by itself and could never be made to stick.
    """
    path = tmp_path / "app_config.json"
    path.write_text(json.dumps({"llm": {"provider": "anthropic"}}), encoding="utf-8")

    config = Config.load(file=path, environ={"UP_LLM__PROVIDER": '"fake"'})
    assert config.get("llm.provider") == "fake", "the override still applies to this run"
    config.set("audio.min_meeting_s", 5)
    config.save()
    assert json.loads(path.read_text())["llm"]["provider"] == "anthropic"


def test_a_deliberate_choice_outranks_the_environment(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Choosing a provider in Settings is a decision, whatever the launcher exported."""
    path = tmp_path / "app_config.json"
    path.write_text(json.dumps({"llm": {"provider": "anthropic"}}), encoding="utf-8")

    config = Config.load(file=path, environ={"UP_LLM__PROVIDER": '"fake"'})
    config.set("llm.provider", "claude-subscription")
    config.save()
    assert json.loads(path.read_text())["llm"]["provider"] == "claude-subscription"


def test_an_env_key_absent_from_disk_is_dropped_not_frozen(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """With nothing on disk to restore, the key goes and the default applies again."""
    path = tmp_path / "app_config.json"
    path.write_text(json.dumps({}), encoding="utf-8")

    config = Config.load(file=path, environ={"UP_LLM__PROVIDER": '"fake"'})
    config.save()
    assert "provider" not in json.loads(path.read_text()).get("llm", {})


def test_an_old_default_saved_to_disk_does_not_pin_it(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """`save` writes the whole config, so every file saved before the default changed
    holds 6000 as if it were chosen. Loaded, that is the old default and nothing more."""
    path = tmp_path / "app_config.json"
    path.write_text(json.dumps({"llm": {"window_tokens": 6000}}), encoding="utf-8")
    assert Config.load(file=path, environ={}).get("llm.window_tokens") is None

    path.write_text(json.dumps({"llm": {"window_tokens": 8000}}), encoding="utf-8")
    assert Config.load(file=path, environ={}).get("llm.window_tokens") == 8000


def test_detection_ships_as_watch_and_log() -> None:
    """A fresh install watches and writes down what it saw; it does not record by itself.

    The user decides that, after reading a week of scores on the Detector page. Asserted
    here so flipping it has to be a deliberate edit to a failing test, not a quiet default.
    """
    assert default_config().get("detection.mode") == "shadow"


def test_a_faster_sustain_window_reaches_existing_installs(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Ten seconds was the old default and sits in every config file saved so far, so
    lowering it in code alone would have changed nothing for anyone who has run the app."""
    path = tmp_path / "app_config.json"
    path.write_text(json.dumps({"detection": {"sustain_s": 10}}), encoding="utf-8")
    assert Config.load(file=path, environ={}).get("detection.sustain_s") == 5

    path.write_text(json.dumps({"detection": {"sustain_s": 20}}), encoding="utf-8")
    assert Config.load(file=path, environ={}).get("detection.sustain_s") == 20, "a choice stands"


def test_the_launcher_does_not_force_a_detection_mode() -> None:
    """The shipped default is watch-and-log, and the launcher must not override it.

    It used to export `UP_DETECTION__MODE="off"` on every start. The environment layer
    beats the config file, so the mode chosen in Settings was silently discarded at the
    next launch while `app_config.json` went on claiming it — the setting appeared to
    revert by itself, and the detector watched nothing for a day.
    """
    launcher = Path(__file__).resolve().parents[2] / "scripts" / "windows" / "run-app.ps1"
    assert "UP_DETECTION__MODE" not in launcher.read_text(encoding="utf-8")
