"""Layered configuration (TECHNICAL-DESIGN.md §14).

Three layers, later overriding earlier, per key:

1. ``DEFAULTS`` — in code
2. ``app_config.json`` in the app home — the only file the settings screen writes
3. the environment — ``UP_<DOTTED__PATH>`` plus the documented non-secret aliases

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

SERVICE_NAME = "upshot"

#: Secret names in the credential store.
SECRET_NAMES = ("anthropic", "openai", "gemini", "smtp", "google_refresh_token")

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
    "ui": {
        "language": "en",
        "view": "list",  # list|calendar — the main screen's layout
        "calendar_span": "week",  # day|week|month
        # light|dark|system. Defaults to light rather than system on purpose: this is
        # read in daylight, and every comparable product ships light only. "system"
        # is offered, not assumed.
        "theme": "light",
    },
    # Follow the meeting. This app transcribes Hebrew by default (asr.default_language),
    # so defaulting summaries to English meant an English write-up of a Hebrew meeting
    # unless the user found the setting first.
    "summary": {"language": "auto"},  # en|he|auto
    "asr": {
        "backend": "local",  # local|remote|fake
        "language_mode": "detect",  # detect|fixed
        "default_language": "he",
        "detect_min_confidence": 0.6,
        "model_path": None,
        # An existing CUDA library folder (DESIGN.md §2). Without this the probe only
        # looks in the app home, the standard toolkit paths and the nvidia-* wheels, so a
        # hand-placed cuBLAS/cuDNN is invisible and transcription silently runs on CPU.
        "cuda_dir": None,
        "model_repo": None,
        "compute_type": "auto",
        "device": "auto",
        "beam_size": 5,
        "remote_url": None,
        "initial_prompt_max_tokens": 200,
        "diarization": "off",  # off|onnx|fake — splits THEM into THEM_1/2/3
        "diarization_dir": None,
        "diarization_segmentation_path": None,
        "diarization_embedding_path": None,
        "diarization_speakers": -1,  # -1 lets the clusterer decide
        "diarization_threshold": 0.6,  # measured: DECISIONS D29
        "diarization_min_duration_on": 0.3,
        "diarization_min_duration_off": 0.5,
        "diarization_fake_speakers": 2,
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
        # The microphone endpoint, by PortAudio device index. null = the Windows default,
        # which is what most people want and what every earlier version did.
        "input_device": None,
        # The playback endpoint whose loopback becomes the "them" track. null = whatever
        # Windows currently calls the default output.
        "output_device": None,
        "synthetic_realtime": True,  # synthetic capture paces itself at 1x
        # Subtracting the far side from the near track (DECISIONS D37). "auto" does it
        # when the two tracks are measurably the same signal, which is what a microphone
        # bus carrying playback produces; "on" does it whenever a model fits at all, for
        # a partial leak the threshold deliberately ignores; "off" never looks.
        "echo_cancel": "auto",  # auto|on|off
        "echo_min_correlation": 0.85,
        "echo_scan_s": 600,  # how much of each track the estimate may read
        "echo_window_s": 60,  # the stretch it is fitted on, chosen where THEM is loudest
    },
    "detection": {
        # shadow|on|off. "shadow" watches and scores but never starts a recording, so
        # out of the box nothing is captured unless the red button is pressed — and
        # nobody joining a call three minutes late remembers the red button. The
        # conservative default is a deliberate privacy choice and it stays, but it is
        # no longer allowed to be a *silent* one: `decided` below is false until the
        # user has been asked, and the library offers the choice until they answer.
        "mode": "shadow",
        # Whether the user has ever been asked how capture should work. An install that
        # reaches its second day still recording nothing by accident is the failure
        # this exists to prevent.
        "decided": False,
        "sources": "windows",  # windows|fake
        "threshold": 5,
        # How long the score must hold before a wake counts. It guards against a
        # transient grab — an app testing the microphone, a notification sound — and a
        # conferencing app holds the microphone for the whole call, so five seconds
        # discriminates as well as ten. Ten made a detection take eleven seconds to
        # appear, which reads as broken when you are watching for it.
        "sustain_s": 5,
        "near_miss_watermark": 3,
        "give_up_s": 90,
        # First look only: a microphone taken longer ago than this was already part of
        # the scenery when the app started, not a meeting beginning (D40).
        "fresh_hold_s": 120,
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
        # Deliberately short. A machine's furniture — virtual audio devices, noise
        # suppressors, voice assistants — is told apart from a meeting by *when* it took
        # the microphone, not by name (D40). Naming vendors here would only ever cover
        # the machines we happen to have seen.
        "ignore": ["VoiceAccess.exe", "NVIDIA Broadcast.exe"],
        "title_patterns": ["zoom meeting", "microsoft teams", "meet -", "meet –", "webex"],
    },
    "llm": {
        # anthropic|openai|gemini|claude-subscription|ollama|fake
        # `claude-subscription` runs through the Claude Code CLI on this machine, using
        # the signed-in user's own plan. It is never the default: Anthropic does not
        # permit third-party products to offer claude.ai login (DECISIONS D30).
        "provider": "anthropic",
        "model": "claude-opus-5",
        "effort": "high",
        "local_model": "dictalm3-nemotron-12b",
        "ollama_url": "http://127.0.0.1:11434",
        "max_tokens": 16000,
        "window_tokens": None,  # null → sized to the provider (summarize.WINDOW_TOKENS_BY_PROVIDER)
        "window_overlap_tokens": 300,
        "openai_model": "gpt-5",
        "openai_base_url": None,  # any OpenAI-compatible endpoint
        "gemini_model": "gemini-2.5-pro",
        "gemini_base_url": "https://generativelanguage.googleapis.com/v1beta",
        # Null means the prompt shipped in app/llm/prompts/system.md. A string replaces
        # it outright: the Settings box shows the text that will be sent, so it has to be
        # the text that is sent.
        "summary_prompt": None,
        "claude_cli_path": "claude",
        "claude_cli_timeout_s": 600,
        "claude_cli_args": [],
        "claude_cli_disallowed_tools": (
            "Bash,Read,Write,Edit,NotebookEdit,Glob,Grep,WebSearch,WebFetch,Task,TodoWrite"
        ),
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
    "retention": {
        # Days before the raw WAVs are deleted; the transcript and summary stay. null or
        # 0 means never. `transcript_days` deletes the meeting outright and is off by
        # default (DECISIONS D38).
        "audio_days": 30,
        "transcript_days": None,
        "sweep_hours": 6,  # how often the worker looks; 0 disables the sweep entirely
    },
    "enrichment": {"source": "google", "timeout_s": 2.0},  # google|null|fake
    "calendar": {
        # Whether the calendar invitation goes to the summary model along with the
        # transcript: what the meeting was called, when it ran, who was invited, what the
        # organizer wrote and what they attached. Email addresses never go, whatever this
        # says — an attendee is a name by the time anything here sees them.
        "prompt_invite": True,
    },
    "db": {"fts": "auto"},  # auto|off
    "secrets": {"backend": "keyring"},  # keyring|memory
    "server": {"host": "127.0.0.1", "port": 8000},
    "schedule": {"hour": 2, "hours": 4},  # the window the `scheduled` job policy runs in
}

_ENUMS: dict[str, tuple[str, ...]] = {
    "profile": ("auto", "gpu-live", "cpu-deferred", "remote-worker"),
    "job_policy": ("auto", "asap", "after_meeting", "when_idle", "scheduled"),
    "ui.language": ("en", "he"),
    "summary.language": ("en", "he", "auto"),
    "asr.backend": ("local", "remote", "fake"),
    "asr.language_mode": ("detect", "fixed"),
    "asr.diarization": ("off", "onnx", "fake"),
    "audio.capture": ("wasapi", "synthetic"),
    "ui.view": ("list", "calendar"),
    "ui.calendar_span": ("day", "week", "month"),
    "audio.vad": ("two_stage", "energy"),
    "audio.echo_cancel": ("auto", "on", "off"),
    "detection.mode": ("shadow", "on", "off"),
    "detection.sources": ("windows", "fake"),
    "llm.provider": ("anthropic", "openai", "gemini", "claude-subscription", "ollama", "fake"),
    "delivery.mode": ("draft", "auto_send"),
    "delivery.notifier": ("windows", "fake"),
    "enrichment.source": ("google", "null", "fake"),
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


def _unset(node: dict[str, Any], dotted: str) -> None:
    """Remove a dotted key, and any branch left empty by its going."""
    parts = dotted.split(".")
    cur: Any = node
    trail: list[tuple[dict[str, Any], str]] = []
    for part in parts[:-1]:
        if not isinstance(cur, dict) or part not in cur:
            return
        trail.append((cur, part))
        cur = cur[part]
    if isinstance(cur, dict):
        cur.pop(parts[-1], None)
    for parent, key in reversed(trail):
        if isinstance(parent.get(key), dict) and not parent[key]:
            parent.pop(key)


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


#: Defaults that have since changed, as they were. ``save`` writes the whole merged
#: config, so a file saved under an old default holds that default as if it were chosen —
#: and would pin it forever. None of these keys is exposed in Settings, so a file value
#: equal to the old default is taken to be the old default, and dropped on load.
_OLD_DEFAULTS: dict[str, Any] = {
    "llm.window_tokens": 6000,
    "detection.sustain_s": 10,
    # V1 shipped without a calendar, so every config saved before Calendar 2 holds
    # "null" here. Left alone, an installation that has run once would never read a
    # calendar however plainly the user connected one.
    "enrichment.source": "null",
}


def _forget_old_defaults(file_layer: dict[str, Any]) -> None:
    for dotted, old in _OLD_DEFAULTS.items():
        try:
            value = _get(file_layer, dotted)
        except KeyError:
            continue
        if value == old and not isinstance(value, bool):
            _unset(file_layer, dotted)


def _merge(base: dict[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, Mapping) and isinstance(out.get(key), dict) and key != "weights":
            out[key] = _merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def env_var_for(dotted: str) -> str:
    return "UP_" + dotted.upper().replace(".", "__")


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

    def __init__(
        self,
        data: dict[str, Any],
        *,
        source_file: Path | None = None,
        from_env: Mapping[str, Any] | None = None,
    ) -> None:
        self._data = data
        self.source_file = source_file
        self._secrets: SecretStore | None = None
        #: Values that came from ``UP_*`` rather than from the file. They must not be
        #: written back: an environment override is meant to last for one run, and a
        #: launcher that exports one on every start would otherwise make it permanent
        #: the first time the user saves anything at all.
        self._from_env: dict[str, Any] = dict(from_env or {})
        #: Anything the running application set deliberately. This outranks the note
        #: above — a provider chosen in Settings is a choice, whatever the environment
        #: happened to say at startup.
        self._explicit: set[str] = set()

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
            _forget_old_defaults(file_layer)
            data = _merge(data, file_layer)
        env = env_layer(environ)
        data = _merge(data, env)
        cfg_from_env = dict(_walk(env))
        if overrides:
            for dotted, value in overrides.items():
                _set(data, dotted, value)
                cfg_from_env.pop(dotted, None)  # an explicit override is not the env
        cfg = cls(data, source_file=path, from_env=cfg_from_env)
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
        self._explicit.add(dotted)

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
        # Undo the environment layer, so a value exported by a launcher is not silently
        # promoted to permanent state by an unrelated save. What was on disk wins; if
        # nothing was, the key goes and the default applies again.
        on_disk: dict[str, Any] = {}
        if target.exists():
            try:
                loaded = json.loads(target.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    on_disk = loaded
            except (OSError, ValueError):
                on_disk = {}
        for dotted in self._from_env:
            if dotted in self._explicit:
                continue
            try:
                previous = _get(on_disk, dotted)
            except KeyError:
                _unset(payload, dotted)  # nothing on disk: let the default apply again
            else:
                _set(payload, dotted, previous)
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
        return str(self.get("summary.language", "auto"))

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
