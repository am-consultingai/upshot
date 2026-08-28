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
    env = {"MA_PROFILE": "gpu-live"}
    cfg = Config.load(file=file, environ=env)
    assert cfg.profile == "gpu-live"  # env beats file
    assert cfg.job_policy == "when_idle"  # file beats default
    assert cfg.ui_language == "he"  # file beats default
    assert cfg.summary_language == "en"  # default survives
    assert cfg.get("audio.chunk_s") == 60


def test_config_env_parses_json_scalars(tmp_path: Path) -> None:
    cfg = Config.load(
        file=tmp_path / "missing.json",
        environ={"MA_AUDIO__CHUNK_S": "45", "MA_DELIVERY__ATTACH_TRANSCRIPT": "true"},
    )
    assert cfg.get("audio.chunk_s") == 45
    assert cfg.get("delivery.attach_transcript") is True


def test_config_env_aliases(tmp_path: Path) -> None:
    cfg = Config.load(file=tmp_path / "x.json", environ={"WHISPER_MODEL_PATH": "/models/ct2"})
    assert cfg.get("asr.model_path") == "/models/ct2"


def test_config_rejects_bad_enum(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        Config.load(file=tmp_path / "x.json", environ={"MA_PROFILE": "banana"})


def test_config_rejects_wrong_type(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        Config.load(file=tmp_path / "x.json", environ={"MA_AUDIO__CHUNK_S": '"sixty"'})


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
