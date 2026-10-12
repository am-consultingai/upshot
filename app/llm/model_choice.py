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
  before, except Antigravity: its own default is unnamed and slow (75 s for one line), so
  automatic names the newest Gemini Flash, Medium for summaries and Low for the
  conversation. Any other Gemini model on the plan can be chosen instead.
* **A choice that is no longer offered falls back to automatic**, logged, never an error:
  a CLI update that drops a model must not stop the summaries.

Choices are stored as ``llm.summary_models`` and ``llm.assistant_models``, provider to
model id; a provider missing or empty is automatic.
"""

from __future__ import annotations

import contextlib
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
#: How long a failure is kept: a CLI signed in, or a key fixed, a minute later must not
#: wait six hours to be seen (second review of #24).
RETRY_S = 60

#: Why a list is empty, as a code the page translates. Never the provider's own words:
#: they could name an account or a path, and they would reach the log.
NOT_INSTALLED = "not-installed"
SIGNED_OUT = "signed-out"
NO_KEY = "no-key"
NO_ANSWER = "no-answer"
REFUSED = "refused"
NO_MODELS = "no-models"
FAILED = "failed"


class ListError(Exception):
    def __init__(self, code: str, status: int | None = None) -> None:
        super().__init__(code if status is None else f"{code} {status}")
        self.code = code
        self.status = status


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
    #: One of the codes above, or ``""``.
    error: str = ""
    #: The HTTP status a vendor refused with.
    status: int | None = None
    #: ``live`` asked the provider; ``fixed`` is a list of our own (Claude's aliases).
    source: str = "live"

    def ids(self) -> set[str]:
        return {model.id for model in self.models}

    def fresh(self, now: float) -> bool:
        return now - self.fetched_at < (FRESH_S if self.models else RETRY_S)


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
        with contextlib.suppress(Exception):
            process.communicate(timeout=5)  # close the pipes
        raise ListError(NO_ANSWER) from None
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
        raise ListError(NOT_INSTALLED)
    if provider == "antigravity-subscription":
        code, out, err = run([path, "models"], 30.0)
        models = parse_agy_models(out)
        if not models:
            said = f"{out}\n{err}".lower()
            raise ListError(SIGNED_OUT if "sign in" in said else NO_MODELS)
        return models
    if provider == "codex-subscription":
        code, out, err = run([path, "debug", "models"], 30.0)
        if code != 0:
            raise ListError(FAILED)
        return parse_codex_models(out)
    raise ListError(FAILED)


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
        raise ListError(REFUSED, status)
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
    key = config.secret(provider, env=KEY_ENV.get(provider))
    if not key:
        raise ListError(NO_KEY)
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
    raise ListError(FAILED)


CLI_PROVIDERS = ("claude-subscription", "codex-subscription", "antigravity-subscription")
API_PROVIDERS = ("anthropic", "gemini", "openai", "ollama")
PROVIDERS = (*CLI_PROVIDERS, *API_PROVIDERS)
#: The providers the assistant panel talks to (``app.assistant.api.route_for``): only
#: they have a chat box to choose the conversation's model at.
CHAT_PROVIDERS = frozenset(CLI_PROVIDERS)


def conversation_use(provider: str) -> str:
    """The use whose model answers someone waiting (Ask, the provider Test). For a
    provider with no chat box, whose conversation model cannot be chosen anywhere, that is
    the summaries' model, which Settings does choose (third review of #24)."""
    return "assistant" if provider in CHAT_PROVIDERS else "summary"


#: The environment variable each key may come from, as each client reads it.
KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY"}


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
        #: Bumped by ``forget``: a fetch begun before it is not kept after it.
        self._generation: dict[str, int] = {}

    def listing(self, provider: str, *, refresh: bool = False) -> Listing:
        """The provider's list, asking for it if it has none, it is stale, or ``refresh``."""
        with self._lock:
            held = self._lists.get(provider)
            if held is not None and not refresh and held.fresh(self.now()):
                return held
            running = self._fetching.get(provider)
            generation = self._generation.get(provider, 0)
            if running is None:
                done = threading.Event()
                self._fetching[provider] = done
        if running is not None:
            running.wait(60)
            with self._lock:
                return self._lists.get(provider) or Listing(error=NO_ANSWER)
        try:
            listing = self._fetch(provider)
            with self._lock:
                if self._generation.get(provider, 0) == generation:
                    self._lists[provider] = listing
            return listing
        finally:
            with self._lock:
                # Only our own entry: after a forget(), a newer fetch may hold the slot.
                if self._fetching.get(provider) is done:
                    del self._fetching[provider]
            done.set()

    def cached(self, provider: str) -> Listing | None:
        """The list already held, without asking: for a summary that must not wait."""
        with self._lock:
            return self._lists.get(provider)

    def forget(self, provider: str) -> None:
        """Drop ``provider``'s list: its key, sign-in or install changed, so the next ask
        is a real one."""
        with self._lock:
            self._lists.pop(provider, None)
            self._generation[provider] = self._generation.get(provider, 0) + 1
            # A fetch still out answers what was asked before the change: the next caller
            # starts its own instead of waiting on it (fourth review of #24).
            self._fetching.pop(provider, None)

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
                return Listing(fetched_at=started, error=FAILED)
        except ListError as exc:
            log.info("model list for %s unavailable: %s", provider, exc)
            return Listing(fetched_at=started, error=exc.code, status=exc.status)
        except Exception as exc:
            # Only the kind of failure: its text could carry a path or an account.
            log.info("model list for %s failed: %s", provider, type(exc).__name__)
            return Listing(fetched_at=started, error=FAILED)
        log.info("model list for %s: %d model(s)", provider, len(models))
        return Listing(models=models, fetched_at=started, source=source)


def providers_to_list(config: Config) -> list[str]:
    """Whose lists to ask for at start: the providers in use (chosen and fallback). The
    rest are asked when Settings shows them."""
    wanted = []
    for key in ("llm.provider", "llm.fallback_provider"):
        provider = str(config.get(key) or "")
        if provider in PROVIDERS and provider not in wanted:
            wanted.append(provider)
    return wanted


# ---------------------------------------------------------------------- choosing

#: ``gemini-3.8-flash-low`` → version (3, 8) and level ``low``.
_AGY_FLASH = re.compile(r"^gemini-(?P<version>\d+(?:\.\d+)*)-flash-(?P<level>low|medium|high)$")

#: The providers whose automatic choice is read from their list.
NEEDS_LIST = frozenset({"antigravity-subscription"})


def automatic(
    provider: str, use: str, listing: Listing | None, config: Config | None = None
) -> str:
    """Our own choice for ``provider`` and ``use``; ``""`` means the provider's default.

    Only Antigravity gets one: its own default is unnamed and thinks at length (75 s for
    a one-line answer on machine A). The newest Gemini Flash it lists: Medium for
    summaries, Low for the conversation. An ``llm.antigravity_model`` set by hand still
    wins for both.
    """
    if provider != "antigravity-subscription":
        return ""
    configured = str(config.get("llm.antigravity_model") or "") if config is not None else ""
    if configured:
        return configured if valid_id(configured) else ""
    if listing is None:
        return ""
    want = "medium" if use == "summary" else "low"
    flashes = []
    for model in listing.models:
        match = _AGY_FLASH.match(model.id)
        if match and match.group("level") == want:
            version = tuple(int(part) for part in match.group("version").split("."))
            flashes.append((version, model.id))
    return max(flashes)[1] if flashes else ""


def chosen(config: Config, provider: str, use: str) -> str:
    """What the user chose, ``""`` for automatic, and for anything that is not a model id."""
    choices = config.get(CONFIG_KEY[use]) or {}
    value = str(choices.get(provider, "") or "") if isinstance(choices, dict) else ""
    if value and not valid_id(value):
        log.warning("ignoring a %s model for %s: not a model id", use, provider)
        return ""
    return value


def resolve(
    config: Config, provider: str, use: str, catalog: Catalog | None, *, fetch: bool = False
) -> str:
    """The model to run: the user's choice while the provider still offers it, else ours.

    Uses the list already held. ``fetch`` lets a caller that may wait (a summary on the
    worker, a conversation turn off the event loop) ask for it when Antigravity's
    automatic choice needs one and none usable is held: about two seconds. Never on the
    event loop: ``agy models`` can take thirty (second review of #24). With no list, a
    choice is trusted as it stands, except a cloud model for Ollama.
    """
    listing = catalog.cached(provider) if catalog is not None else None
    choice = chosen(config, provider, use)
    if (
        fetch
        and catalog is not None
        and not choice
        and provider in NEEDS_LIST
        and (listing is None or not listing.models)
    ):
        listing = catalog.listing(provider)
    if choice and provider == "ollama" and not is_local_ollama({"name": choice}):
        log.warning("ignoring a cloud model for Ollama: it would leave the machine")
        choice = ""
    if choice:
        if listing is None or not listing.models or choice in listing.ids():
            return choice
        log.warning(
            "%s no longer offers %s for %s; using the automatic choice", provider, choice, use
        )
    return automatic(provider, use, listing, config)


#: The clients whose model is a CLI flag: without one, the CLI's own default runs.
_CLI_CLIENTS = frozenset(CLI_PROVIDERS)


def guard(provider: str, model: str) -> str:
    """The model as it will run, checked where it is used, whatever set it (a hand-edited
    ``llm.antigravity_model`` or ``llm.local_model`` included).

    A CLI's model that is not a model id is dropped (the CLI's default runs). An API
    model that is not one, and an Ollama model that runs in the cloud, refuse: a
    sensitive meeting must never leave the machine.
    """
    from app.errors import PermanentError

    if not model:
        return model
    if not valid_id(model):
        if provider in _CLI_CLIENTS:
            log.warning("ignoring the %s model: not a model id", provider)
            return ""
        raise PermanentError(
            f"The model set for {provider} is not a model id. Choose one in Settings.",
            category="setup",
        )
    if provider == "ollama" and not is_local_ollama({"name": model}):
        raise PermanentError(
            "The Ollama model set is a cloud model, which would send the meeting off this "
            "machine. Choose a local one in Settings.",
            category="setup",
        )
    return model


# ---------------------------------------------------------------------- the one in use

_catalog: Catalog | None = None


def set_catalog(catalog: Catalog | None) -> None:
    global _catalog
    _catalog = catalog


def catalog() -> Catalog | None:
    return _catalog
