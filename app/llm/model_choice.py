"""Which model each provider runs, for summaries and for the conversation (2026-10-12).

Until now the model was the app's choice alone (Settings said so), and for the CLI
subscriptions it was the CLI's own default. Antigravity's default thinks at length: one
``{"ok": true}`` took 75 s and 7,600 tokens of reasoning on machine A, against 15 s on
Flash (Low). So the user may now choose, per provider, a model for summaries and one for
the conversation, from what the provider itself lists.

* **The lists are asked for, never written down.** Each provider is asked what it offers:
  ``agy models``, ``codex debug models``, the vendors' model-list APIs, Ollama's tags.
  Claude Code has no list command, so it offers the aliases its ``--model`` takes. The
  lists are kept in memory, refreshed in the background at start and on request.
* **Automatic is a choice of ours, and the default.** It is what each provider did
  before, except Antigravity's conversation, whose automatic choice is the newest Gemini
  Flash (Low): someone waits for each answer. Its summaries keep the stronger default.
* **A choice that is no longer offered falls back to automatic**, logged, never an error:
  a CLI update that drops a model must not stop the summaries.

Choices are stored as ``llm.summary_models`` and ``llm.assistant_models``, provider to
model id; a provider missing or empty is automatic.
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from app.config import Config
from app.log import get

log = get(__name__)

#: What a model is chosen for.
USES = ("summary", "assistant")
#: Where each use's choices are kept.
CONFIG_KEY = {"summary": "llm.summary_models", "assistant": "llm.assistant_models"}

#: Claude Code takes these for ``--model`` and has no command that lists models: each
#: names the latest model of its family, so the list stays right as models change.
CLAUDE_ALIASES = (
    ("opus", "Opus (latest)"),
    ("sonnet", "Sonnet (latest)"),
    ("haiku", "Haiku (latest)"),
)

#: How long a list is trusted before Settings asks again.
FRESH_S = 6 * 3600


@dataclass(frozen=True)
class ModelInfo:
    id: str
    label: str

    def as_dict(self) -> dict[str, str]:
        return {"id": self.id, "label": self.label}


@dataclass
class Listing:
    """One provider's list, when it was asked for, and why it is empty if it is."""

    models: list[ModelInfo] = field(default_factory=list)
    fetched_at: float = 0.0
    error: str = ""
    #: ``live`` asked the provider; ``fixed`` is a list of our own (Claude's aliases).
    source: str = "live"

    def ids(self) -> set[str]:
        return {model.id for model in self.models}


# ---------------------------------------------------------------------- listing


Runner = Callable[[list[str], float], tuple[int, str, str]]

#: A model id as providers write them. Checked before one reaches a command line:
#: on Windows a CLI may resolve to an npm ``.cmd`` shim, whose arguments pass through
#: ``cmd.exe``, and a leading ``-`` would read as a flag (review of #24).
MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,119}$")


def valid_id(model: str) -> bool:
    return bool(MODEL_ID.match(model))


def _run(args: list[str], timeout: float) -> tuple[int, str, str]:
    """Run a listing command, and on a timeout end it and everything it started: an npm
    shim's ``node`` would otherwise hold the pipes and the request with them."""
    from app.llm.claude_cli import creation_flags
    from app.llm.signin import kill_tree

    process = subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creation_flags(visible=False),
    )
    try:
        out, err = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(process)
        raise LookupError(f"no answer in {timeout:g} s") from None
    return process.returncode, out, err


def parse_agy_models(out: str) -> list[ModelInfo]:
    """``agy models``: one ``id<TAB>label`` line per model, after a "Fetching…" line."""
    found = []
    for line in out.splitlines():
        if "\t" not in line:
            continue
        model_id, label = (part.strip() for part in line.split("\t", 1))
        if valid_id(model_id):
            found.append(ModelInfo(model_id, label or model_id))
    return found


def parse_codex_models(out: str) -> list[ModelInfo]:
    """``codex debug models``: a JSON catalog; only the models it lists for people. Text
    after the JSON (an update notice) is left alone."""
    start = out.find("{")
    if start < 0:
        return []
    data, _end = json.JSONDecoder().raw_decode(out, start)
    found = []
    for item in data.get("models", []):
        slug = str(item.get("slug") or "")
        if valid_id(slug) and item.get("visibility", "list") == "list":
            found.append(ModelInfo(slug, str(item.get("display_name") or slug)))
    return found


def _list_cli(provider: str, config: Config, run: Runner) -> list[ModelInfo]:
    if provider == "claude-subscription":
        return [ModelInfo(model_id, label) for model_id, label in CLAUDE_ALIASES]
    client = _cli_client(provider, config)
    path = client.resolve()
    if path is None:
        raise LookupError("not installed")
    if provider == "antigravity-subscription":
        code, out, err = run([path, "models"], 30.0)
        models = parse_agy_models(out)
        if not models:
            lines = (err or out).strip().splitlines()
            raise LookupError(lines[-1] if lines else "no models listed")
        return models
    if provider == "codex-subscription":
        code, out, err = run([path, "debug", "models"], 30.0)
        if code != 0:
            raise LookupError(f"codex debug models exited {code}")
        return parse_codex_models(out)
    raise LookupError(f"{provider} cannot list models")


def _cli_client(provider: str, config: Config) -> Any:
    from app.llm.client import make_client

    return make_client(config, provider=provider)


#: OpenAI's list holds every kind of model; these cannot write a summary.
_NOT_CHAT = re.compile(
    r"(whisper|tts|dall-e|embedding|moderation|transcribe|realtime|audio|image|search|"
    r"davinci|babbage|computer-use|sora)",
    re.IGNORECASE,
)


def is_local_ollama(model: dict[str, Any]) -> bool:
    """An Ollama model that runs on this machine. Ollama also lists cloud models, which
    run on ollama.com: a sensitive meeting must never reach one (D97, review of #24)."""
    name = str(model.get("name") or "")
    if model.get("remote_host") or model.get("remote_model"):
        return False
    return not re.search(r"[:-]cloud\b", name, re.IGNORECASE)


def _is_embedding(model: dict[str, Any]) -> bool:
    """An Ollama model that turns text into vectors and cannot write a summary."""
    family = str((model.get("details") or {}).get("family") or "").lower()
    return "embed" in str(model.get("name") or "").lower() or "bert" in family


def _get(http: Any, url: str, **kwargs: Any) -> dict[str, Any]:
    """A vendor's answer, or why not in a word: its status, never its body (which could
    say anything) and never the key."""
    response = http.get(url, **kwargs)
    status = getattr(response, "status_code", 200)
    if status >= 400:
        raise LookupError(f"the provider answered {status}")
    data = response.json()
    return data if isinstance(data, dict) else {}


def _list_api(provider: str, config: Config, http: Any) -> list[ModelInfo]:
    """The vendors' own model lists, with the key the user saved."""
    if provider == "ollama":
        url = str(config.get("llm.ollama_url", "http://127.0.0.1:11434")).rstrip("/")
        data = _get(http, f"{url}/api/tags", timeout=10)
        return [
            ModelInfo(m["name"], m["name"])
            for m in data.get("models", [])
            if valid_id(str(m.get("name") or "")) and is_local_ollama(m) and not _is_embedding(m)
        ]
    key = config.secret(provider)
    if not key:
        raise LookupError("no key saved")
    if provider == "anthropic":
        data = _get(
            http,
            "https://api.anthropic.com/v1/models?limit=100",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
            timeout=15,
        )
        return [
            ModelInfo(m["id"], str(m.get("display_name") or m["id"]))
            for m in data.get("data", [])
            if valid_id(str(m.get("id") or ""))
        ]
    if provider == "gemini":
        base = str(config.get("llm.gemini_base_url")).rstrip("/")
        data = _get(
            http, f"{base}/models?pageSize=200", headers={"x-goog-api-key": key}, timeout=15
        )
        found = []
        for m in data.get("models", []):
            model_id = str(m.get("name") or "").removeprefix("models/")
            methods = m.get("supportedGenerationMethods", [])
            if valid_id(model_id) and "generateContent" in methods:
                found.append(ModelInfo(model_id, str(m.get("displayName") or model_id)))
        return found
    if provider == "openai":
        base = str(config.get("llm.openai_base_url") or "https://api.openai.com/v1").rstrip("/")
        data = _get(http, f"{base}/models", headers={"Authorization": f"Bearer {key}"}, timeout=15)
        return sorted(
            (
                ModelInfo(m["id"], m["id"])
                for m in data.get("data", [])
                if valid_id(str(m.get("id") or "")) and not _NOT_CHAT.search(m["id"])
            ),
            key=lambda model: model.id,
        )
    raise LookupError(f"{provider} has no model list")


CLI_PROVIDERS = ("claude-subscription", "codex-subscription", "antigravity-subscription")
API_PROVIDERS = ("anthropic", "gemini", "openai", "ollama")
PROVIDERS = (*CLI_PROVIDERS, *API_PROVIDERS)


class Catalog:
    """The lists, kept in memory and asked for again when stale or on request. Callers
    asking for the same provider at once share one fetch."""

    def __init__(
        self,
        config: Config,
        *,
        run: Runner = _run,
        http: Any = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self.run = run
        self.http = http
        self.now = now
        self._lists: dict[str, Listing] = {}
        self._lock = threading.Lock()
        self._fetching: dict[str, threading.Event] = {}

    def listing(self, provider: str, *, refresh: bool = False) -> Listing:
        """The provider's list, asking for it if it has none, it is stale, or ``refresh``."""
        with self._lock:
            held = self._lists.get(provider)
            if held is not None and not refresh and self.now() - held.fetched_at < FRESH_S:
                return held
            running = self._fetching.get(provider)
            if running is None:
                done = threading.Event()
                self._fetching[provider] = done
        if running is not None:
            running.wait(60)
            with self._lock:
                return self._lists.get(provider) or Listing(error="still being asked for")
        try:
            listing = self._fetch(provider)
            with self._lock:
                self._lists[provider] = listing
            return listing
        finally:
            with self._lock:
                self._fetching.pop(provider, None)
            done.set()

    def cached(self, provider: str) -> Listing | None:
        """The list already held, without asking: for a summary that must not wait."""
        with self._lock:
            return self._lists.get(provider)

    def refresh_in_background(self, providers: Iterable[str]) -> threading.Thread:
        """Ask every given provider for its list, off the caller's thread (at start)."""

        def work() -> None:
            for provider in providers:
                try:
                    self.listing(provider, refresh=True)
                except Exception:  # a list is a convenience; never let it fail the app
                    log.warning("could not list the models of %s", provider, exc_info=True)

        thread = threading.Thread(target=work, name="model-lists", daemon=True)
        thread.start()
        return thread

    def _fetch(self, provider: str) -> Listing:
        started = self.now()
        try:
            if provider in CLI_PROVIDERS:
                models = _list_cli(provider, self.config, self.run)
                source = "fixed" if provider == "claude-subscription" else "live"
            elif provider in API_PROVIDERS:
                if self.http is not None:
                    models = _list_api(provider, self.config, self.http)
                else:
                    import httpx

                    with httpx.Client() as http:
                        models = _list_api(provider, self.config, http)
                source = "live"
            else:
                return Listing(fetched_at=started, error="this provider has no model list")
        except Exception as exc:
            reason = str(exc) or type(exc).__name__
            log.info("model list for %s unavailable: %s", provider, reason)
            return Listing(fetched_at=started, error=reason[:200])
        log.info("model list for %s: %d model(s)", provider, len(models))
        return Listing(models=models, fetched_at=started, source=source)


def providers_to_list(config: Config) -> list[str]:
    """Whose lists to ask for at start: the CLIs (each says at once if it is not
    installed), the API providers with a key saved, and Ollama when it is in use."""
    active = {str(config.get("llm.provider") or ""), str(config.get("llm.fallback_provider") or "")}
    wanted = [*CLI_PROVIDERS]
    wanted += [p for p in ("anthropic", "gemini", "openai") if config.secret(p)]
    if "ollama" in active:
        wanted.append("ollama")
    return wanted


# ---------------------------------------------------------------------- choosing

#: ``gemini-3.8-flash-low`` → version (3, 8) and level ``low``.
_AGY_FLASH = re.compile(r"^gemini-(?P<version>\d+(?:\.\d+)*)-flash-(?P<level>low|medium|high)$")


def automatic(
    provider: str, use: str, listing: Listing | None, config: Config | None = None
) -> str:
    """Our own choice for ``provider`` and ``use``; ``""`` means the provider's default.

    Only Antigravity's conversation gets one: its default thinks at length (75 s for a
    one-line answer), and someone waits for each answer. The newest Gemini Flash (Low)
    it lists. Its summaries keep the default, the stronger model, and an
    ``llm.antigravity_model`` set by hand still wins for both.
    """
    if provider != "antigravity-subscription":
        return ""
    configured = str(config.get("llm.antigravity_model") or "") if config is not None else ""
    if configured:
        return configured if valid_id(configured) else ""
    if use != "assistant" or listing is None:
        return ""
    flashes = []
    for model in listing.models:
        match = _AGY_FLASH.match(model.id)
        if match and match.group("level") == "low":
            version = tuple(int(part) for part in match.group("version").split("."))
            flashes.append((version, model.id))
    return max(flashes)[1] if flashes else ""


def chosen(config: Config, provider: str, use: str) -> str:
    """What the user chose, ``""`` for automatic, and for anything that is not a model id."""
    choices = config.get(CONFIG_KEY[use]) or {}
    value = str(choices.get(provider, "") or "") if isinstance(choices, dict) else ""
    if value and not valid_id(value):
        log.warning("ignoring %s model %r for %s: not a model id", use, value, provider)
        return ""
    return value


def resolve(config: Config, provider: str, use: str, catalog: Catalog | None) -> str:
    """The model to run: the user's choice while the provider still offers it, else ours.

    Uses the list already held, so a summary does not wait on one. Antigravity's
    conversation is the exception: its automatic choice needs the list, and without one
    it would think at length, so the list is asked for (about two seconds). With no list
    held, a choice is trusted as it stands, except a cloud model for Ollama.
    """
    listing = catalog.cached(provider) if catalog is not None else None
    if (
        listing is None
        and catalog is not None
        and provider == "antigravity-subscription"
        and use == "assistant"
        and not chosen(config, provider, use)
    ):
        listing = catalog.listing(provider)
    choice = chosen(config, provider, use)
    if choice and provider == "ollama" and not is_local_ollama({"name": choice}):
        log.warning("ignoring the cloud model %s for Ollama: it would leave the machine", choice)
        choice = ""
    if choice:
        if listing is None or not listing.models or choice in listing.ids():
            return choice
        log.warning(
            "%s no longer offers %s for %s; using the automatic choice", provider, choice, use
        )
    return automatic(provider, use, listing, config)


# ---------------------------------------------------------------------- the one in use

_catalog: Catalog | None = None


def set_catalog(catalog: Catalog | None) -> None:
    global _catalog
    _catalog = catalog


def catalog() -> Catalog | None:
    return _catalog
