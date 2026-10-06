"""Find, fetch and verify the next version of Upshot (D87).

``check_now`` reads each channel's manifest from ``updates.manifest_base`` and refuses
any whose signature does not verify (``app.updates.manifest``). The newest version newer
than this one wins; the rollout decides whether this copy is offered it yet, by a bucket
(0-99) the copy draws once and keeps in its config. The bucket is never sent anywhere.

``download`` fetches the installer into ``<app home>/updates/``, resuming a partial file,
and pausing whenever ``busy`` says so (a meeting is being recorded). It counts the
installer ready only when its size and SHA-256 match the manifest and, on Windows, its
Authenticode signer is the pinned certificate. Installing it is the installer step's job
(B3); this module keeps it ready and says so.

Nothing here raises to its caller: no network is the normal case for a laptop, and a
failed check is remembered in ``last_error`` and tried again at the next one.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from app import paths
from app.clock import Clock, SystemClock
from app.config import Config
from app.log import get
from app.updates import authenticode
from app.updates.keys import PUBLIC_KEYS
from app.updates.manifest import (
    CHANNELS,
    DOWNLOAD_PREFIX,
    Manifest,
    ManifestError,
    Offer,
    choose,
    parse,
    verify,
)

log = get(__name__)

MANIFEST_BASE = "https://upshot.amconsultingai.com/updates/"
HTTP_TIMEOUT_S = 30.0
#: The first check waits this long after start, so it never competes with start-up.
FIRST_CHECK_DELAY_S = 60.0
#: How soon a download paused for a recording tries again.
RESUME_AFTER_S = 300.0
READY_FILE = "ready.json"


class _Paused(Exception):
    """The download stopped for a recording or for shutdown; the partial file is kept."""


class _Refused(Exception):
    """The server's file is not the one the manifest describes; nothing of it is kept.

    A connection that drops half-way raises ``httpx.HTTPError`` instead, and keeps the
    partial file to resume from.
    """


class UpdateService:
    def __init__(
        self,
        config: Config,
        *,
        home: Path | None = None,
        http: httpx.Client | None = None,
        clock: Clock | None = None,
        current_version: str | None = None,
        public_keys: list[str] | None = None,
        download_prefix: str = DOWNLOAD_PREFIX,
        check_signer: Callable[[Path, str], authenticode.SignerCheck] = authenticode.check,
        require_signer: bool | None = None,
        busy: Callable[[], bool] = lambda: False,
        frozen: bool | None = None,
        publish: Callable[..., Any] | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.config = config
        self.home = (home or paths.app_home()) / "updates"
        self.clock = clock or SystemClock()
        if current_version is None:
            from app.version import build_info

            current_version = build_info().version
        self.current_version = current_version
        self.public_keys = public_keys or [key for _name, key in PUBLIC_KEYS]
        self.download_prefix = download_prefix
        self.check_signer = check_signer
        # The signer can only be checked on Windows, which is the only place an installer runs.
        self.require_signer = sys.platform == "win32" if require_signer is None else require_signer
        self.busy = busy
        self.frozen = paths.is_frozen() if frozen is None else frozen
        self.publish = publish
        self.rng = rng or random.SystemRandom()
        self._http = http
        self._check_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.phase = "idle"  # idle | checking | downloading | waiting | ready | failed
        self.offer: Offer | None = None
        self.held_back: Manifest | None = None
        self.progress: tuple[int, int] | None = None
        self.last_checked_at: str | None = None
        self.last_error: str | None = None

    # ------------------------------------------------------------------ settings

    @property
    def channel(self) -> str:
        value = str(self.config.get("updates.channel") or "stable")
        return value if value in CHANNELS else "stable"

    @property
    def bucket(self) -> int:
        """This copy's place in a rollout, 0-99: drawn once, kept, never sent."""
        value = self.config.get("updates.bucket")
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 99:
            return value
        drawn = self.rng.randrange(100)
        self.config.set("updates.bucket", drawn)
        self.config.save()
        return drawn

    def channels(self) -> list[str]:
        """Beta copies read both manifests, so leaving beta later never downgrades them."""
        return ["stable", "beta"] if self.channel == "beta" else ["stable"]

    # ------------------------------------------------------------------ checking

    def fetch(self, channel: str) -> Manifest:
        base = str(self.config.get("updates.manifest_base") or MANIFEST_BASE)
        url = httpx.URL(base).join(f"{channel}.json")
        client = self._client()
        data = client.get(url).raise_for_status().content
        signature = client.get(url.copy_with(path=url.path + ".sig")).raise_for_status().content
        verify(data, signature, self.public_keys)
        # A test server (the machine B run) may stand in for GitHub Releases. The manifest
        # still has to verify against the built-in keys, so this widens nothing.
        prefix = str(self.config.get("updates.download_prefix") or self.download_prefix)
        return parse(data, channel=channel, download_prefix=prefix)

    def check_now(self, *, download: bool = True) -> dict[str, Any]:
        """Look for a newer version, and fetch it when one is offered to this copy."""
        if not self._check_lock.acquire(blocking=False):
            return self.state()  # a check is already running
        try:
            self._set_phase("checking")
            self.last_checked_at = self.clock.now().isoformat(timespec="seconds")
            manifests: list[Manifest] = []
            errors: list[str] = []
            for channel in self.channels():
                try:
                    manifests.append(self.fetch(channel))
                except (httpx.HTTPError, ManifestError) as exc:
                    errors.append(f"{channel}: {type(exc).__name__}: {exc}")
            if errors:
                log.info("update check: %s", "; ".join(errors))
            if not manifests:
                self.last_error = "; ".join(errors) or "no manifest"
                self._set_phase("failed" if errors else "idle")
                return self.state()
            self.last_error = None
            self.offer, self.held_back = choose(manifests, self.current_version, self.bucket)
            if self.offer is None:
                self.forget_ready()
                self._set_phase("idle")
            elif (ready := self.ready()) and ready["version"] == self.offer.manifest.version:
                self._set_phase("ready")
            elif download and self.frozen:
                self.download(self.offer.manifest)
            else:
                self._set_phase("idle")
            return self.state()
        finally:
            self._check_lock.release()

    # ------------------------------------------------------------------ downloading

    def download(self, manifest: Manifest) -> Path | None:
        """Fetch, verify and keep the installer. None when paused or refused."""
        self.home.mkdir(parents=True, exist_ok=True)
        final = self.home / f"Upshot-{manifest.version}-Setup.exe"
        partial = final.with_name(final.name + ".part")
        try:
            if not final.exists():
                self._fetch_to(manifest, partial)
                os.replace(partial, final)
            problem = self._verify(manifest, final)
        except _Refused as exc:
            problem = str(exc)
        except _Paused:
            self._set_phase("waiting")
            return None
        except (httpx.HTTPError, OSError) as exc:
            self.last_error = f"download: {type(exc).__name__}: {exc}"
            log.info("update %s: %s", manifest.version, self.last_error)
            self._set_phase("failed")
            return None
        if problem:
            for path in (final, partial):
                path.unlink(missing_ok=True)
            self.last_error = f"refused: {problem}"
            # Nothing of it is kept, so nothing of it is "downloaded" (seen on machine B).
            self.progress = None
            log.warning("update %s refused: %s", manifest.version, problem)
            self._set_phase("failed")
            return None
        mandatory = self.offer.mandatory if self.offer else manifest.critical
        record: dict[str, Any] = {
            "version": manifest.version,
            "file": final.name,
            "sha256": manifest.sha256,
            "mandatory": mandatory,
        }
        (self.home / READY_FILE).write_text(json.dumps(record), encoding="utf-8")
        self._remove_others(keep=final)
        self.progress = None
        log.info("update %s downloaded and verified", manifest.version)
        self._set_phase("ready")
        return final

    def _fetch_to(self, manifest: Manifest, partial: Path) -> None:
        have = partial.stat().st_size if partial.exists() else 0
        if have > manifest.size:
            partial.unlink()
            have = 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        kbps = int(self.config.get("updates.download_kbps") or 0)
        self.progress = (have, manifest.size)
        self._set_phase("downloading")
        with self._client().stream("GET", manifest.url, headers=headers) as response:
            response.raise_for_status()
            if have and response.status_code != 206:
                have = 0  # the server sent the whole file: start again
            mode = "ab" if have else "wb"
            started = time.monotonic()
            got = 0
            with partial.open(mode) as fh:
                # As the bytes arrive, not in fixed chunks: a fixed size holds back what
                # arrived until the chunk fills, and a dropped connection loses it.
                for chunk in response.iter_bytes():
                    if self._stop.is_set() or self.busy():
                        raise _Paused
                    fh.write(chunk)
                    have += len(chunk)
                    got += len(chunk)
                    if have > manifest.size:
                        raise _Refused("the download is larger than the manifest says")
                    self.progress = (have, manifest.size)
                    if kbps > 0:
                        ahead = got / (kbps * 1024) - (time.monotonic() - started)
                        if ahead > 0:
                            self._stop.wait(ahead)
        if have != manifest.size:
            # The server finished sending: its file is shorter than the manifest says.
            raise _Refused(f"the download ended at {have} of {manifest.size} bytes")

    def _verify(self, manifest: Manifest, path: Path) -> str | None:
        if path.stat().st_size != manifest.size:
            return "the size does not match the manifest"
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for block in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != manifest.sha256:
            return "the SHA-256 does not match the manifest"
        if self.require_signer:
            signer = self.check_signer(path, manifest.signer)
            if not signer.ok:
                return f"Authenticode: {signer.reason}"
        return None

    # ------------------------------------------------------------------ the ready installer

    def ready(self) -> dict[str, Any] | None:
        """The verified installer waiting to be run, if any (read by the installer step)."""
        try:
            record = json.loads((self.home / READY_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(record, dict) or not (self.home / str(record.get("file"))).is_file():
            return None
        return {**record, "path": str(self.home / str(record["file"]))}

    def forget_ready(self) -> None:
        (self.home / READY_FILE).unlink(missing_ok=True)
        self._remove_others(keep=None)

    def _remove_others(self, keep: Path | None) -> None:
        if not self.home.is_dir():
            return
        for path in self.home.glob("Upshot-*-Setup.exe*"):
            if keep is None or path != keep:
                path.unlink(missing_ok=True)

    # ------------------------------------------------------------------ state

    def state(self) -> dict[str, Any]:
        offer = self.offer
        return {
            "phase": self.phase,
            "current": self.current_version,
            "channel": self.channel,
            "auto_install": bool(self.config.get("updates.auto_install", True)),
            "enabled": self.frozen and bool(self.config.get("updates.check", True)),
            "available": {**offer.manifest.as_dict(), "mandatory": offer.mandatory}
            if offer
            else None,
            "held_back": self.held_back.version if self.held_back else None,
            "progress": {"bytes": self.progress[0], "total": self.progress[1]}
            if self.progress
            else None,
            "ready": self.ready() is not None,
            "last_checked_at": self.last_checked_at,
            "last_error": self.last_error,
        }

    def _set_phase(self, phase: str) -> None:
        self.phase = phase
        if self.publish is not None:
            self.publish(**self.state())

    def _client(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(
                timeout=HTTP_TIMEOUT_S,
                follow_redirects=True,
                headers={"User-Agent": f"Upshot/{self.current_version}"},
            )
        return self._http

    # ------------------------------------------------------------------ the loop

    def start(self) -> None:
        """Check after start-up and then every ``updates.check_hours``.

        Only an installed copy checks by itself: one run from source has no installer to
        replace, though "Check now" still says what is available.
        """
        if self._thread is not None or not self.frozen or not self.config.get("updates.check"):
            return
        self._thread = threading.Thread(target=self._run, name="updates", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        wait = FIRST_CHECK_DELAY_S
        while not self._stop.wait(wait):
            self.check_now()
            hours = float(self.config.get("updates.check_hours", 6) or 6)
            wait = RESUME_AFTER_S if self.phase == "waiting" else max(hours, 1.0) * 3600

    def check_in_background(self) -> dict[str, Any]:
        """For "Check now" in the interface: answers at once; a download may take minutes."""
        threading.Thread(target=self.check_now, name="updates-check", daemon=True).start()
        return {**self.state(), "phase": "checking"}
