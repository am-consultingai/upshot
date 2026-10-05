"""Updates: the signed manifest, the rollout, and the verified download (D87)."""

from __future__ import annotations

import base64
import hashlib
import json
import random
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.clock import FakeClock
from app.config import Config
from app.updates.authenticode import SignerCheck
from app.updates.keys import PUBLIC_KEYS
from app.updates.manifest import (
    DOWNLOAD_PREFIX,
    Manifest,
    ManifestError,
    choose,
    parse,
    verify,
)
from app.updates.service import UpdateService

KEY = Ed25519PrivateKey.generate()
SPARE = Ed25519PrivateKey.generate()
STRANGER = Ed25519PrivateKey.generate()
INSTALLER = b"MZ" + bytes(range(256)) * 40
THUMBPRINT = "AB" * 20


def public(key: Ed25519PrivateKey) -> str:
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def signed(data: bytes, key: Ed25519PrivateKey = KEY) -> bytes:
    return base64.b64encode(key.sign(data)) + b"\n"


def manifest_dict(version: str = "0.3.0", **fields: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema": 1,
        "channel": "stable",
        "version": version,
        "published": "2026-10-20T10:00:00Z",
        "url": f"{DOWNLOAD_PREFIX}v{version}/Upshot-{version}-Setup.exe",
        "size": len(INSTALLER),
        "sha256": hashlib.sha256(INSTALLER).hexdigest(),
        "signer": THUMBPRINT,
        "rollout": 100,
        "critical": False,
        "notes": {"en": "Faster summaries.", "he": "סיכומים מהירים יותר."},
    }
    body.update(fields)
    return body


def encode(body: dict[str, Any]) -> bytes:
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


def m(version: str, rollout: int = 100, **fields: Any) -> Manifest:
    channel = fields.pop("channel", "stable")
    body = manifest_dict(version, rollout=rollout, channel=channel, **fields)
    return parse(encode(body), channel=channel)


# ------------------------------------------------------------------ the signature


def test_the_built_in_keys_are_two_distinct_ed25519_keys() -> None:
    keys = [key for _name, key in PUBLIC_KEYS]
    assert len(keys) == 2 and len(set(keys)) == 2
    assert all(len(base64.b64decode(key)) == 32 for key in keys)


def test_a_manifest_signed_by_either_key_verifies() -> None:
    data = encode(manifest_dict())
    verify(data, signed(data), [public(KEY), public(SPARE)])
    verify(data, signed(data, SPARE), [public(KEY), public(SPARE)])


@pytest.mark.parametrize("case", ["stranger", "tampered", "not base64", "empty", "truncated"])
def test_anything_else_is_refused(case: str) -> None:
    data = encode(manifest_dict())
    signature = {
        "stranger": signed(data, STRANGER),
        "tampered": signed(data),
        "not base64": b"!!!not-base64!!!",
        "empty": b"",
        "truncated": signed(data)[:20],
    }[case]
    if case == "tampered":
        data = data.replace(b"0.3.0", b"9.9.9")
    with pytest.raises(ManifestError):
        verify(data, signature, [public(KEY)])


# ------------------------------------------------------------------ the format


def test_a_good_manifest_parses() -> None:
    manifest = parse(encode(manifest_dict()), channel="stable")
    assert manifest.version == "0.3.0" and manifest.rollout == 100
    assert manifest.notes["he"] == "סיכומים מהירים יותר."
    assert manifest.signer == THUMBPRINT


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"schema": 2}, "schema"),
        ({"channel": "beta"}, "published as"),
        ({"version": "1.0"}, "MAJOR.MINOR.PATCH"),
        ({"version": "1.0.0-rc1"}, "MAJOR.MINOR.PATCH"),
        ({"min_version": "x"}, "min_version"),
        ({"url": "https://evil.example/Upshot-0.3.0-Setup.exe"}, "releases"),
        ({"url": DOWNLOAD_PREFIX + "v0.3.0/notes.txt"}, "releases"),
        ({"url": "http://github.com/am-consultingai/upshot/releases/download/x.exe"}, "releases"),
        ({"size": 0}, "size"),
        ({"size": True}, "size"),
        ({"sha256": "ABC"}, "sha256"),
        ({"signer": "nope"}, "thumbprint"),
        ({"rollout": 101}, "rollout"),
        ({"rollout": -1}, "rollout"),
        ({"critical": "yes"}, "critical"),
        ({"notes": ["en"]}, "notes"),
        ({"notes_url": "http://x"}, "https"),
    ],
)
def test_a_manifest_this_copy_cannot_use_is_refused(change: dict[str, Any], message: str) -> None:
    with pytest.raises(ManifestError, match=message):
        parse(encode(manifest_dict(**change)), channel="stable")


def test_not_json_is_refused() -> None:
    with pytest.raises(ManifestError):
        parse(b"<html>", channel="stable")


# ------------------------------------------------------------------ the offer


def test_nothing_newer_offers_nothing() -> None:
    assert choose([m("0.3.0")], "0.3.0", 0) == (None, None)
    assert choose([m("0.2.9")], "0.3.0", 0) == (None, None)


def test_the_rollout_is_a_strict_bound_on_the_bucket() -> None:
    offer, held = choose([m("0.3.0", rollout=10)], "0.2.0", 9)
    assert offer and offer.manifest.version == "0.3.0" and not offer.mandatory and held is None
    offer, held = choose([m("0.3.0", rollout=10)], "0.2.0", 10)
    assert offer is None and held and held.version == "0.3.0"
    assert choose([m("0.3.0", rollout=0)], "0.2.0", 0)[0] is None, "rollout 0 halts it"


def test_critical_and_too_old_ignore_the_rollout() -> None:
    offer, _ = choose([m("0.3.0", rollout=0, critical=True)], "0.2.0", 99)
    assert offer and offer.mandatory
    offer, _ = choose([m("0.3.0", rollout=0, min_version="0.2.5")], "0.2.0", 99)
    assert offer and offer.mandatory
    offer, _ = choose([m("0.3.0", rollout=0, min_version="0.2.0")], "0.2.0", 99)
    assert offer is None, "exactly min_version is not too old"


def test_the_newest_wins_across_channels_and_beta_never_downgrades() -> None:
    stable, beta = m("0.3.0"), m("0.4.0", channel="beta")
    offer, _ = choose([stable, beta], "0.2.0", 50)
    assert offer and offer.manifest.version == "0.4.0"
    # A copy on 0.4.0 that left beta reads only stable, which is older: nothing.
    assert choose([stable], "0.4.0", 50) == (None, None)


# ------------------------------------------------------------------ the service


class Site:
    """The website and GitHub Releases, as a mock transport."""

    def __init__(self, *manifests: dict[str, Any], key: Ed25519PrivateKey = KEY) -> None:
        self.files: dict[str, bytes] = {}
        for body in manifests:
            data = encode(body)
            self.files[f"/updates/{body['channel']}.json"] = data
            self.files[f"/updates/{body['channel']}.json.sig"] = signed(data, key)
            path = httpx.URL(body["url"]).path
            self.files[path] = INSTALLER
        self.offline = False
        self.requests: list[httpx.Request] = []
        self.ignore_range = False
        #: Drop the connection after this many bytes of the installer, once.
        self.drop_after: int | None = None

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.offline:
            raise httpx.ConnectError("offline", request=request)
        body = self.files.get(request.url.path)
        if body is None:
            return httpx.Response(404)
        if self.drop_after is not None and request.url.path.endswith(".exe"):
            cut, self.drop_after = self.drop_after, None

            def dropping() -> Any:
                yield body[:cut]
                raise httpx.ReadError("connection reset", request=request)

            return httpx.Response(200, content=dropping())
        wanted = request.headers.get("range")
        if wanted and not self.ignore_range:
            start = int(wanted.removeprefix("bytes=").rstrip("-"))
            return httpx.Response(206, content=body[start:])
        return httpx.Response(200, content=body)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle))


def service(
    tmp_path: Path,
    site: Site,
    *,
    current: str = "0.2.0",
    signer_ok: bool = True,
    busy: Any = lambda: False,
    frozen: bool = True,
    channel: str = "stable",
    bucket: int | None = 5,
) -> UpdateService:
    config = Config.load(file=tmp_path / "app_config.json")
    config.set("updates.manifest_base", "https://upshot.example/updates/")
    config.set("updates.channel", channel)
    if bucket is not None:
        config.set("updates.bucket", bucket)

    def check_signer(path: Path, thumbprint: str) -> SignerCheck:
        return SignerCheck(signer_ok, "pinned" if signer_ok else "signed by another certificate")

    return UpdateService(
        config,
        home=tmp_path,
        http=site.client(),
        clock=FakeClock(datetime.fromisoformat("2026-10-20T09:00:00+03:00")),
        current_version=current,
        public_keys=[public(KEY), public(SPARE)],
        check_signer=check_signer,
        require_signer=True,
        busy=busy,
        frozen=frozen,
        rng=random.Random(7),
    )


def test_a_newer_version_is_downloaded_verified_and_kept_ready(tmp_path: Path) -> None:
    updates = service(tmp_path, Site(manifest_dict()))
    state = updates.check_now()
    assert state["phase"] == "ready" and state["ready"] is True
    assert state["available"]["version"] == "0.3.0" and state["available"]["mandatory"] is False
    ready = updates.ready()
    assert ready and Path(ready["path"]).read_bytes() == INSTALLER
    assert not list((tmp_path / "updates").glob("*.part"))
    assert updates.check_now()["phase"] == "ready", "a second check does not download again"


def test_the_request_names_the_app_and_nothing_else(tmp_path: Path) -> None:
    site = Site(manifest_dict())
    config = Config.load(file=tmp_path / "app_config.json")
    config.set("updates.manifest_base", "https://upshot.example/updates/")
    updates = UpdateService(config, home=tmp_path, current_version="0.2.0", frozen=False)
    client = updates._client()
    assert client.headers["user-agent"] == "Upshot/0.2.0"
    assert "cookie" not in client.headers and "authorization" not in client.headers
    assert site.requests == []


def test_a_manifest_signed_by_a_stranger_is_ignored(tmp_path: Path) -> None:
    updates = service(tmp_path, Site(manifest_dict(), key=STRANGER))
    state = updates.check_now()
    assert state["phase"] == "failed" and state["available"] is None
    assert "known key" in (state["last_error"] or "")
    assert not (tmp_path / "updates").exists() or not list((tmp_path / "updates").iterdir())


@pytest.mark.parametrize(
    "body",
    [
        manifest_dict(sha256="0" * 64),
        manifest_dict(size=len(INSTALLER) + 1),
    ],
    ids=["hash", "size"],
)
def test_an_installer_that_does_not_match_the_manifest_is_refused_and_deleted(
    tmp_path: Path, body: dict[str, Any]
) -> None:
    updates = service(tmp_path, Site(body))
    state = updates.check_now()
    assert state["phase"] == "failed" and state["ready"] is False
    assert "match" in (state["last_error"] or "") or "ended at" in (state["last_error"] or "")
    assert state["progress"] is None, "a refused download is not shown as downloaded"
    assert not list((tmp_path / "updates").glob("Upshot-*"))


def test_an_installer_signed_by_another_certificate_is_refused(tmp_path: Path) -> None:
    updates = service(tmp_path, Site(manifest_dict()), signer_ok=False)
    state = updates.check_now()
    assert state["phase"] == "failed" and "Authenticode" in (state["last_error"] or "")
    assert not list((tmp_path / "updates").glob("Upshot-*"))


def test_a_partial_download_resumes_where_it_stopped(tmp_path: Path) -> None:
    site = Site(manifest_dict())
    (tmp_path / "updates").mkdir()
    (tmp_path / "updates" / "Upshot-0.3.0-Setup.exe.part").write_bytes(INSTALLER[:1000])
    updates = service(tmp_path, site)
    assert updates.check_now()["phase"] == "ready"
    download = [r for r in site.requests if r.url.path.endswith(".exe")]
    assert download[-1].headers["range"] == "bytes=1000-"


def test_a_dropped_connection_keeps_what_arrived_and_the_next_check_finishes_it(
    tmp_path: Path,
) -> None:
    site = Site(manifest_dict())
    site.drop_after = 3000
    updates = service(tmp_path, site)
    state = updates.check_now()
    assert state["phase"] == "failed" and "ReadError" in (state["last_error"] or "")
    partial = tmp_path / "updates" / "Upshot-0.3.0-Setup.exe.part"
    assert partial.stat().st_size == 3000
    assert updates.check_now()["phase"] == "ready"
    download = [r for r in site.requests if r.url.path.endswith(".exe")]
    assert download[-1].headers["range"] == "bytes=3000-"


def test_a_server_that_ignores_the_range_starts_the_file_again(tmp_path: Path) -> None:
    site = Site(manifest_dict())
    site.ignore_range = True
    (tmp_path / "updates").mkdir()
    (tmp_path / "updates" / "Upshot-0.3.0-Setup.exe.part").write_bytes(b"junk" * 10)
    updates = service(tmp_path, site)
    assert updates.check_now()["phase"] == "ready"
    assert Path(updates.ready()["path"]).read_bytes() == INSTALLER  # type: ignore[index]


def test_a_recording_pauses_the_download_and_keeps_what_arrived(tmp_path: Path) -> None:
    recording = {"on": True}
    updates = service(tmp_path, Site(manifest_dict()), busy=lambda: recording["on"])
    assert updates.check_now()["phase"] == "waiting"
    assert (tmp_path / "updates" / "Upshot-0.3.0-Setup.exe.part").exists()
    recording["on"] = False
    assert updates.check_now()["phase"] == "ready"


def test_no_network_is_remembered_not_raised(tmp_path: Path) -> None:
    site = Site(manifest_dict())
    site.offline = True
    state = service(tmp_path, site).check_now()
    assert state["phase"] == "failed" and "ConnectError" in (state["last_error"] or "")


def test_held_back_by_the_rollout_downloads_nothing(tmp_path: Path) -> None:
    updates = service(tmp_path, Site(manifest_dict(rollout=10)), bucket=50)
    state = updates.check_now()
    assert state["available"] is None and state["held_back"] == "0.3.0"
    assert state["phase"] == "idle" and not (tmp_path / "updates").exists()


def test_the_bucket_is_drawn_once_and_kept(tmp_path: Path) -> None:
    updates = service(tmp_path, Site(manifest_dict()), bucket=None)
    first = updates.bucket
    assert 0 <= first <= 99
    reloaded = Config.load(file=tmp_path / "app_config.json")
    assert reloaded.get("updates.bucket") == first
    assert updates.bucket == first


def test_a_beta_copy_reads_both_channels(tmp_path: Path) -> None:
    beta = manifest_dict("0.4.0", channel="beta")
    site = Site(manifest_dict("0.3.0"), beta)
    state = service(tmp_path, site, channel="beta").check_now()
    assert state["available"]["version"] == "0.4.0" and state["phase"] == "ready"
    assert service(tmp_path / "s", site).check_now()["available"]["version"] == "0.3.0"


def test_a_copy_run_from_source_says_what_is_available_but_downloads_nothing(
    tmp_path: Path,
) -> None:
    updates = service(tmp_path, Site(manifest_dict()), frozen=False)
    state = updates.check_now()
    assert state["available"]["version"] == "0.3.0" and state["phase"] == "idle"
    assert state["enabled"] is False and not (tmp_path / "updates").exists()
    updates.start()
    assert updates._thread is None, "only an installed copy checks by itself"


def test_once_up_to_date_an_old_download_is_cleared(tmp_path: Path) -> None:
    updates = service(tmp_path, Site(manifest_dict()))
    assert updates.check_now()["ready"] is True
    later = service(tmp_path, Site(manifest_dict()), current="0.3.0")
    assert later.check_now()["ready"] is False
    assert not list((tmp_path / "updates").glob("Upshot-*"))


def test_a_test_server_may_stand_in_for_github_but_the_manifest_must_still_be_signed(
    tmp_path: Path,
) -> None:
    body = manifest_dict(url="http://127.0.0.1:8399/Upshot-0.3.0-Setup.exe")
    site = Site(body)
    site.files["/Upshot-0.3.0-Setup.exe"] = INSTALLER
    refused = service(tmp_path / "a", site)
    assert refused.check_now()["phase"] == "failed", "not this repository's releases"
    allowed = service(tmp_path / "b", site)
    allowed.config.set("updates.download_prefix", "http://127.0.0.1:8399/")
    assert allowed.check_now()["phase"] == "ready"
    stranger = Site(body, key=STRANGER)
    stranger.files["/Upshot-0.3.0-Setup.exe"] = INSTALLER
    unsigned = service(tmp_path / "c", stranger)
    unsigned.config.set("updates.download_prefix", "http://127.0.0.1:8399/")
    assert unsigned.check_now()["phase"] == "failed", "the prefix widens nothing else"
