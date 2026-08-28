"""Layered configuration (TECHNICAL-DESIGN.md §14).

Three layers, later overriding earlier, per key:

1. ``DEFAULTS`` — in code
2. ``app_config.json`` in the app home — the only file the settings screen writes
3. the environment — ``MA_<DOTTED__PATH>`` plus the documented non-secret aliases

**Secrets never appear in any of them.** They live in the OS credential store behind
``SecretStore``; which store is used is itself a config key, so tests select the fake
through configuration rather than by patching.
"""

from __future__ import annotations

import contextlib
import copy
import json
import os
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from app import paths
from app.errors import ConfigError
from app.log import get

log = get(__name__)

SERVICE_NAME = "meeting-agent"

#: Secret names in the credential store.
SECRET_NAMES = ("anthropic", "smtp", "google_refresh_token")

#: Dotted config keys that must never be persisted to ``app_config.json`` and always
#: render as ``"***"`` in :meth:`Config.redacted_dump`.
SECRET_KEYS = ("llm.api_key", "delivery.smtp.password", "delivery.smtp.app_password")

SYNCED_FOLDER_MARKERS = ("onedrive", "dropbox", "google drive", "googledrive", "icloud")

DEFAULTS: dict[str, Any] = {
    "data_root": None,  # null → <app home>/meetings
    "ffmpeg_path": None,
    "glossary_path": None,  # null → <app home>/glossary.yaml
    "profile": "auto",  # auto|gpu-live|cpu-deferred|remote-worker
    "worker_url": None,
    "job_policy": "auto",  # auto|asap|after_meeting|when_idle|scheduled
    "ui": {"language": "en"},
    "summary": {"language": "en"},  # en|he|auto
    "asr": {
        "backend": "local",  # local|remote|fake
        "language_mode": "detect",  # detect|fixed
        "default_language": "he",
        "detect_min_confidence": 0.6,
        "model_path": None,
        "model_repo": None,
        "compute_type": "auto",
        "device": "auto",
        "beam_size": 5,
        "remote_url": None,
        "initial_prompt_max_tokens": 200,
        "fake_language": "he",
        "fake_confidence": 0.95,
        "fake_repetitions": 1,
    },
    "audio": {
        "capture": "wasapi",  # wasapi|synthetic
        "chunk_s": 60,
        "preroll_s": 60,
        "max_meeting_h": 4,
        "min_meeting_s": 120,
        "sample_rate": 16000,
        "silence_search_s": 10,
        "hard_cut_s": 70,
        "queue_seconds": 10,
        "vad": "two_stage",
    },
    "detection": {
        "mode": "shadow",  # shadow|on|off
        "sources": "windows",  # windows|fake
        "threshold": 5,
        "sustain_s": 10,
        "near_miss_watermark": 3,
        "give_up_s": 90,
        "release_grace_s": 60,
        "dual_silence_s": 300,
        "weights": {
            "mic.known_app": 3,
            "mic.unknown_app": 1,
            "vad.loopback": 2,
            "vad.mic": 2,
            "window.title": 2,
            "session.render": 1,
            "camera": 1,
            "calendar": 3,
            "ignored": -5,
        },
        "known_apps": [
            "Zoom.exe",
            "ms-teams.exe",
            "Teams.exe",
            "chrome.exe",
            "msedge.exe",
            "slack.exe",
            "Discord.exe",
            "Webex.exe",
        ],
        "ignore": ["VoiceAccess.exe", "NVIDIA Broadcast.exe"],
        "title_patterns": ["zoom meeting", "microsoft teams", "meet -", "meet –", "webex"],
    },
    "llm": {
        "provider": "anthropic",  # anthropic|ollama|fake
        "model": "claude-opus-5",
        "effort": "high",
        "local_model": "dictalm3-nemotron-12b",
        "ollama_url": "http://127.0.0.1:11434",
        "max_tokens": 16000,
        "window_tokens": 6000,
        "window_overlap_tokens": 300,
    },
    "delivery": {
        "mode": "draft",  # draft|auto_send
        "attach_transcript": False,
        "recipients": [],
        "notifier": "windows",  # windows|fake
        "smtp": {
            "host": "smtp.gmail.com",
            "port": 587,
            "user": "",
            "from_addr": "",
            "starttls": True,
        },
    },
    "retention": {"audio_days": 30, "transcript_days": None},
    "enrichment": {"source": "null", "timeout_s": 2.0},  # null|fake
    "db": {"fts": "auto"},  # auto|off
    "secrets": {"backend": "keyring"},  # keyring|memory
    "server": {"host": "127.0.0.1", "port": 8000},
}

_ENUMS: dict[str, tuple[str, ...]] = {
    "profile": ("auto", "gpu-live", "cpu-deferred", "remote-worker"),
    "job_policy": ("auto", "asap", "after_meeting", "when_idle", "scheduled"),
    "ui.language": ("en", "he"),
    "summary.language": ("en", "he", "auto"),
    "asr.backend": ("local", "remote", "fake"),
    "asr.language_mode": ("detect", "fixed"),
    "audio.capture": ("wasapi", "synthetic"),
    "audio.vad": ("two_stage", "energy"),
    "detection.mode": ("shadow", "on", "off"),
    "detection.sources": ("windows", "fake"),
    "llm.provider": ("anthropic", "ollama", "fake"),
    "delivery.mode": ("draft", "auto_send"),
    "delivery.notifier": ("windows", "fake"),
    "enrichment.source": ("null", "fake"),
    "db.fts": ("auto", "off"),
    "secrets.backend": ("keyring", "memory"),
}

#: Non-secret environment aliases documented in DESIGN.md §15.
ENV_ALIASES: dict[str, str] = {
    "WHISPER_MODEL_PATH": "asr.model_path",
    "FFMPEG_PATH": "ffmpeg_path",
    "OLLAMA_MODEL": "llm.local_model",
    "WORKER_URL": "worker_url",
}


# --------------------------------------------------------------------------- helpers


def _walk(node: Mapping[str, Any], prefix: str = "") -> Iterator[tuple[str, Any]]:
    for key, value in node.items():
        dotted = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict) and value and all(isinstance(k, str) for k in value):
            # dicts that are values (weights) are leaves; dicts of config are branches
            if dotted in ("detection.weights",):
                yield dotted, value
            else:
                yield from _walk(value, dotted)
        else:
            yield dotted, value


def leaf_keys() -> list[str]:
    return [k for k, _ in _walk(DEFAULTS)]


def _get(node: Mapping[str, Any], dotted: str) -> Any:
    cur: Any = node
    for part in dotted.split("."):
        if not isinstance(cur, Mapping) or part not in cur:
            raise KeyError(dotted)
        cur = cur[part]
    return cur


def _set(node: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur = node
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def _merge(base: dict[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, Mapping) and isinstance(out.get(key), dict) and key != "weights":
            out[key] = _merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def env_var_for(dotted: str) -> str:
    return "MA_" + dotted.upper().replace(".", "__")


def _coerce(raw: str) -> Any:
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def env_layer(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    env = os.environ if environ is None else environ
    layer: dict[str, Any] = {}
    for dotted in leaf_keys():
        name = env_var_for(dotted)
        if name in env:
            _set(layer, dotted, _coerce(env[name]))
    for alias, dotted in ENV_ALIASES.items():
        if alias in env:
            _set(layer, dotted, _coerce(env[alias]))
    return layer


# --------------------------------------------------------------------------- secrets


class SecretStore:
    """A place secrets live. Never the config file, never the repo."""

    name = "abstract"

    def get(self, key: str) -> str | None:
        raise NotImplementedError

    def set(self, key: str, value: str) -> None:
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError


class KeyringSecrets(SecretStore):
    """Windows Credential Manager (or the platform equivalent) via ``keyring``."""

    name = "keyring"

    def get(self, key: str) -> str | None:
        """A credential store that is missing or locked must not crash the app."""
        import keyring

        try:
            value = keyring.get_password(SERVICE_NAME, key)
        except Exception as exc:
            log.warning("credential store unavailable (%s); falling back to the environment", exc)
            return None
        return str(value) if value is not None else None

    def set(self, key: str, value: str) -> None:
        import keyring

        keyring.set_password(SERVICE_NAME, key, value)

    def delete(self, key: str) -> None:
        import keyring

        with contextlib.suppress(Exception):
            keyring.delete_password(SERVICE_NAME, key)


class FakeKeyring(SecretStore):
    """In-memory store, selected with ``secrets.backend = "memory"``."""

    name = "memory"

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._data.get(key)

    def set(self, key: str, value: str) -> None:
        self._data[key] = value

    def delete(self, key: str) -> None:
        self._data.pop(key, None)


def make_secret_store(backend: str) -> SecretStore:
    if backend == "memory":
        return FakeKeyring()
    if backend == "keyring":
        return KeyringSecrets()
    raise ConfigError(f"unknown secrets backend {backend!r}")


# --------------------------------------------------------------------------- config


class Config:
    """Merged configuration with typed accessors."""

    def __init__(self, data: dict[str, Any], *, source_file: Path | None = None) -> None:
        self._data = data
        self.source_file = source_file
        self._secrets: SecretStore | None = None

    # -- construction

    @classmethod
    def load(
        cls,
        *,
        file: Path | None = None,
        environ: Mapping[str, str] | None = None,
        overrides: Mapping[str, Any] | None = None,
    ) -> Config:
        path = file if file is not None else paths.config_path()
        data = copy.deepcopy(DEFAULTS)
        if path.exists():
            try:
                file_layer = json.loads(path.read_text(encoding="utf-8"))
            except ValueError as exc:
                raise ConfigError(f"{path} is not valid JSON: {exc}") from exc
            if not isinstance(file_layer, dict):
                raise ConfigError(f"{path} must contain a JSON object")
            data = _merge(data, file_layer)
        data = _merge(data, env_layer(environ))
        if overrides:
            for dotted, value in overrides.items():
                _set(data, dotted, value)
        cfg = cls(data, source_file=path)
        cfg.validate()
        return cfg

    # -- access

    def get(self, dotted: str, default: Any = None) -> Any:
        try:
            return _get(self._data, dotted)
        except KeyError:
            return default

    def set(self, dotted: str, value: Any) -> None:
        _set(self._data, dotted, value)

    def as_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self._data)

    def redacted_dump(self) -> dict[str, Any]:
        """Config with every secret key replaced by ``"***"``. The only shape that
        reaches the settings API, the diagnostics bundle or a log line."""
        out = copy.deepcopy(self._data)
        for dotted in SECRET_KEYS:
            try:
                _get(out, dotted)
            except KeyError:
                continue
            _set(out, dotted, "***")
        return out

    def save(self, path: Path | None = None) -> Path:
        """Persist to ``app_config.json`` — with every secret key stripped out."""
        target = path or self.source_file or paths.config_path()
        payload = copy.deepcopy(self._data)
        for dotted in SECRET_KEYS:
            parts = dotted.split(".")
            cur: Any = payload
            for part in parts[:-1]:
                if not isinstance(cur, dict) or part not in cur:
                    cur = None
                    break
                cur = cur[part]
            if isinstance(cur, dict):
                cur.pop(parts[-1], None)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
        return target

    # -- secrets

    @property
    def secrets(self) -> SecretStore:
        if self._secrets is None:
            self._secrets = make_secret_store(str(self.get("secrets.backend", "keyring")))
        return self._secrets

    def secret(self, name: str, *, env: str | None = None) -> str | None:
        """A secret from the credential store, falling back to an environment variable."""
        value = self.secrets.get(name)
        if value:
            return value
        if env and os.environ.get(env):
            return os.environ[env]
        return None

    # -- typed accessors

    @property
    def data_root(self) -> Path:
        raw = self.get("data_root")
        if raw:
            return Path(str(raw)).expanduser()
        return paths.default_data_root()

    @property
    def glossary_path(self) -> Path:
        raw = self.get("glossary_path")
        if raw:
            return Path(str(raw)).expanduser()
        return paths.app_home() / "glossary.yaml"

    @property
    def ui_language(self) -> str:
        return str(self.get("ui.language", "en"))

    @property
    def summary_language(self) -> str:
        return str(self.get("summary.language", "en"))

    @property
    def profile(self) -> str:
        return str(self.get("profile", "auto"))

    @property
    def job_policy(self) -> str:
        return str(self.get("job_policy", "auto"))

    @property
    def chunk_s(self) -> int:
        return int(self.get("audio.chunk_s", 60))

    @property
    def preroll_s(self) -> int:
        return int(self.get("audio.preroll_s", 60))

    @property
    def sample_rate(self) -> int:
        return int(self.get("audio.sample_rate", 16000))

    @property
    def min_meeting_s(self) -> int:
        return int(self.get("audio.min_meeting_s", 120))

    @property
    def default_language(self) -> str:
        return str(self.get("asr.default_language", "he"))

    @property
    def language_mode(self) -> str:
        return str(self.get("asr.language_mode", "detect"))

    @property
    def detect_min_confidence(self) -> float:
        return float(self.get("asr.detect_min_confidence", 0.6))

    @property
    def detection_weights(self) -> dict[str, int]:
        weights = self.get("detection.weights", {})
        return {str(k): int(v) for k, v in dict(weights).items()}

    @property
    def server_host(self) -> str:
        return str(self.get("server.host", "127.0.0.1"))

    @property
    def server_port(self) -> int:
        return int(self.get("server.port", 8000))

    # -- validation

    def validate(self) -> None:
        for dotted, allowed in _ENUMS.items():
            value = self.get(dotted)
            if value is not None and value not in allowed:
                raise ConfigError(f"{dotted}: {value!r} is not one of {allowed}")
        for dotted, default in _walk(DEFAULTS):
            value = self.get(dotted)
            if default is None or value is None:
                continue
            if isinstance(default, bool) and not isinstance(value, bool):
                raise ConfigError(f"{dotted}: expected a boolean, got {value!r}")
            if (
                isinstance(default, int)
                and not isinstance(default, bool)
                and (isinstance(value, bool) or not isinstance(value, int | float))
            ):
                raise ConfigError(f"{dotted}: expected a number, got {value!r}")
            if isinstance(default, str) and not isinstance(value, str):
                raise ConfigError(f"{dotted}: expected a string, got {value!r}")
            if isinstance(default, list) and not isinstance(value, list):
                raise ConfigError(f"{dotted}: expected a list, got {value!r}")
        port = self.server_port
        if not (1 <= port <= 65535):
            raise ConfigError(f"server.port out of range: {port}")

    def warnings(self) -> list[str]:
        """Non-fatal configuration problems. It is the user's machine — warn, never block."""
        out: list[str] = []
        root = str(self.data_root).replace("\\", "/").lower()
        for marker in SYNCED_FOLDER_MARKERS:
            if marker in root:
                out.append(
                    f"data_root is inside a synced folder ({marker}): recordings will be "
                    "uploaded to that service as they are written, and a sync client can "
                    "lock a WAV file mid-meeting."
                )
                break
        return out


def default_config(**overrides: Any) -> Config:
    """A Config built from defaults only — used by tests and by first run."""
    cfg = Config(copy.deepcopy(DEFAULTS))
    for dotted, value in overrides.items():
        cfg.set(dotted.replace("__", "."), value)
    cfg.validate()
    return cfg
