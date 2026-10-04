"""Which Terms apply, whether this user accepted them, and new versions from the website.

Three sources of a terms document, newest version wins once it is in effect:

1. **Bundled**: ``app/legal/terms.md``, shipped in the build. Always there, so the app
   never depends on the network to show what the user agreed to.
2. **Fetched**: a newer version published on the website. ``check_now`` reads the
   manifest at ``legal.manifest_url`` (``site/legal/terms.json``), downloads the Markdown
   it names, checks its SHA-256 and keeps it in ``<app home>/legal/``. That is how an
   update to the Terms reaches copies that are already installed, without a new build.
3. **The installer's record**: the licence page in the installer writes the version
   accepted there to the registry, and ``adopt_installer_acceptance`` turns it into the
   app's own record on the next start, so nobody is asked twice.

What the interface does with it (``state``):

- No version accepted yet, or a **material** newer version in effect: ``gate`` is true and
  the app shows the Terms instead of its other screens until they are accepted. The
  recorder and the detector are not touched: a meeting being recorded finishes (D83).
- A non-material newer version in effect: a ``notice`` the user can acknowledge.
- A version published but not yet in effect: a ``notice`` of what changes and when.

Every acceptance is written to the config (``legal.accepted_*``) and appended to
``<app home>/legal/consent.jsonl`` with the document's checksum, which is the record of
what exactly was agreed to and how.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx

from app import paths
from app.clock import Clock, SystemClock
from app.config import Config
from app.legal.document import Terms, TermsError, parse
from app.log import get

log = get(__name__)

TERMS_PAGE = "https://upshot.amconsultingai.com/terms.html"
#: Where the official app looks for a newer version (``legal.manifest_url``).
MANIFEST_URL = "https://upshot.amconsultingai.com/legal/terms.json"
#: The registry value the installer's licence page writes (packaging/installer.iss).
INSTALLER_KEY = r"Software\AM Consulting\Upshot"
INSTALLER_VALUE = "TermsAccepted"
HTTP_TIMEOUT_S = 15.0
#: The first check waits this long after start, so it never competes with start-up.
FIRST_CHECK_DELAY_S = 60.0
_CACHED = re.compile(r"^terms-(\d{4}-\d{2}-\d{2})\.md$")
VIA = ("installer", "app")


def bundled() -> Terms:
    return parse(paths.resource("app", "legal", "terms.md").read_text(encoding="utf-8"))


def installer_record() -> str | None:
    """The version accepted on the installer's licence page, on Windows; None elsewhere."""
    if sys.platform != "win32":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, INSTALLER_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, INSTALLER_VALUE)
        return str(value) or None
    except OSError:
        return None


class TermsService:
    def __init__(
        self,
        config: Config,
        *,
        home: Path | None = None,
        http: httpx.Client | None = None,
        clock: Clock | None = None,
        installer: Callable[[], str | None] = installer_record,
        publish: Callable[..., Any] | None = None,
        bundled_terms: Terms | None = None,
    ) -> None:
        self.config = config
        self.home = (home or paths.app_home()) / "legal"
        self.clock = clock or SystemClock()
        self.installer = installer
        self.publish = publish
        self.bundled = bundled_terms or bundled()
        self._http = http
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_checked_at: str | None = None
        self.last_error: str | None = None

    # ------------------------------------------------------------------ documents

    def known(self) -> list[Terms]:
        """Every version this copy has: the bundled one and any fetched since, oldest first."""
        found = {self.bundled.version: self.bundled}
        if self.home.is_dir():
            for path in self.home.iterdir():
                if not _CACHED.match(path.name):
                    continue
                try:
                    terms = parse(path.read_text(encoding="utf-8"))
                except (OSError, TermsError) as exc:
                    log.warning("ignoring %s: %s", path.name, exc)
                    continue
                found.setdefault(terms.version, terms)
        return sorted(found.values(), key=lambda t: t.version)

    def document(self, version: str | None = None) -> Terms:
        """A known version, or the one in effect today."""
        if version is None:
            return self.current()
        for terms in self.known():
            if terms.version == version:
                return terms
        raise KeyError(f"no terms version {version}")

    def current(self, today: date | None = None) -> Terms:
        """The newest version in effect. The bundled one always is: it shipped accepted-ready."""
        today = today or self._today()
        in_effect = [t for t in self.known() if t.effective_on(today) or t is self.bundled]
        return in_effect[-1]

    def upcoming(self, today: date | None = None) -> Terms | None:
        """A published version not yet in effect, so the user can read it before it applies."""
        today = today or self._today()
        later = [t for t in self.known() if not t.effective_on(today) and t is not self.bundled]
        return later[-1] if later else None

    # ------------------------------------------------------------------ acceptance

    @property
    def accepted_version(self) -> str:
        return str(self.config.get("legal.accepted_version") or "")

    def state(self, today: date | None = None) -> dict[str, Any]:
        today = today or self._today()
        current = self.current(today)
        accepted = self.accepted_version
        behind = not accepted or current.version > accepted
        gate = behind and (not accepted or current.material)
        notice: dict[str, Any] | None = None
        if behind and not gate:
            notice = {"kind": "changed", **_meta(current)}
        else:
            upcoming = self.upcoming(today)
            if upcoming is not None and (not accepted or upcoming.version > accepted):
                notice = {"kind": "upcoming", **_meta(upcoming)}
        return {
            "accepted_version": accepted or None,
            "accepted_at": self.config.get("legal.accepted_at") or None,
            "accepted_via": self.config.get("legal.accepted_via") or None,
            "current": _meta(current),
            "gate": gate,
            "notice": notice,
            "page": TERMS_PAGE,
            "last_checked_at": self.last_checked_at,
        }

    def accept(self, version: str, *, via: str = "app") -> dict[str, Any]:
        """Record that the user agreed to ``version``. Never moves the record backwards."""
        if via not in VIA:
            raise ValueError(f"unknown acceptance route {via!r}")
        terms = self.document(version)
        with self._lock:
            accepted = self.accepted_version
            if accepted and terms.version < accepted:
                raise ValueError(f"terms {terms.version} are older than the accepted {accepted}")
            at = self.clock.now().isoformat(timespec="seconds")
            self.config.set("legal.accepted_version", terms.version)
            self.config.set("legal.accepted_at", at)
            self.config.set("legal.accepted_via", via)
            self.config.save()
            self._log_consent(terms, via=via, at=at)
        log.info("terms %s accepted (%s)", terms.version, via)
        if self.publish is not None:
            self.publish(state="accepted", version=terms.version)
        return self.state()

    def adopt_installer_acceptance(self) -> bool:
        """The installer's licence page counts as acceptance; the app records it once."""
        version = self.installer()
        if not version:
            return False
        accepted = self.accepted_version
        if accepted and version <= accepted:
            return False
        try:
            self.accept(version, via="installer")
        except (KeyError, ValueError) as exc:
            log.warning(
                "the installer recorded terms %s, which this build cannot use: %s", version, exc
            )
            return False
        return True

    def _log_consent(self, terms: Terms, *, via: str, at: str) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        record = {"version": terms.version, "sha256": terms.sha256, "via": via, "at": at}
        with (self.home / "consent.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")

    # ------------------------------------------------------------------ the website

    def check_now(self) -> bool:
        """Fetch a newer version from the website if there is one. True when one arrived.

        A failure is logged and remembered, never raised: no network is the normal case
        for a laptop on a train, and the bundled Terms still apply.
        """
        url = str(self.config.get("legal.manifest_url") or MANIFEST_URL)
        self.last_checked_at = self.clock.now().isoformat(timespec="seconds")
        try:
            client = self._client()
            manifest = client.get(url).raise_for_status().json()
            version = str(manifest["version"])
            newest = self.known()[-1].version
            if version <= newest:
                self.last_error = None
                return False
            source_url = httpx.URL(url).join(str(manifest["url"]))
            source = client.get(source_url).raise_for_status().text.replace("\r\n", "\n")
            terms = parse(source)
            if terms.version != version or terms.sha256 != str(manifest["sha256"]):
                raise TermsError("the downloaded terms do not match the manifest")
            self.home.mkdir(parents=True, exist_ok=True)
            target = self.home / f"terms-{terms.version}.md"
            partial = target.with_suffix(".part")
            partial.write_text(source, encoding="utf-8")
            os.replace(partial, target)
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.info("terms check failed: %s", self.last_error)
            return False
        self.last_error = None
        log.info("terms %s (effective %s) fetched from the website", terms.version, terms.effective)
        if self.publish is not None:
            self.publish(state="updated", version=terms.version)
        return True

    def _client(self) -> httpx.Client:
        if self._http is None:
            from app.version import build_info

            self._http = httpx.Client(
                timeout=HTTP_TIMEOUT_S,
                follow_redirects=True,
                headers={"User-Agent": f"Upshot/{build_info().version}"},
            )
        return self._http

    # ------------------------------------------------------------------ the loop

    def start(self) -> None:
        """Check after start-up and then every ``legal.check_hours``, while enabled."""
        if self._thread is not None or not self.config.get("legal.check", True):
            return
        self._thread = threading.Thread(target=self._run, name="terms", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        wait = FIRST_CHECK_DELAY_S
        while not self._stop.wait(wait):
            self.check_now()
            hours = float(self.config.get("legal.check_hours", 24) or 24)
            wait = max(hours, 1.0) * 3600

    def _today(self) -> date:
        now = self.clock.now()
        return now.date() if isinstance(now, datetime) else date.today()


def _meta(terms: Terms) -> dict[str, Any]:
    return {
        "version": terms.version,
        "effective": terms.effective,
        "material": terms.material,
        "summary": terms.summary,
    }
