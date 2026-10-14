"""The running Upshot app, over its REST API on this computer's loopback (``/api/v1``)."""

from __future__ import annotations

import json
import os
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

#: The app's preferred port, then the range it falls back to (``app/server.py``).
PORTS = (43217, *range(43218, 43248))
CLIENT = "mcp"
#: How long a port may take to answer the probe. A closed port is refused at once
#: whatever this says, so it only bounds a port that is open: the one Upshot recorded
#: gets long enough for an Upshot busy transcribing on the CPU (the test machine, 2026-10-05,
#: where 0.5 s read as "not running"), the fallback range less.
RECORDED_TIMEOUT_S = 10.0
PROBE_TIMEOUT_S = 2.0
#: For a quick look (``candidates(quick=True)``): long enough for a busy Upshot to answer
#: its info, short enough that a client listing tools is not kept waiting.
QUICK_TIMEOUT_S = 3.0
CHUNK = 1 << 20
#: The access key's file in the app home (D98), and what overrides it.
KEY_FILE = "access.key"
KEY_ENV = "UPSHOT_KEY"
NO_KEY = (
    "Upshot refused this program's access key. Make sure Upshot is up to date, then "
    "restart this AI app. If the key was reset in Upshot's Settings, restarting is enough."
)
BUSY = (
    "Upshot is busy and did not answer in time; anything it is transcribing carries on. "
    "Ask again in a moment."
)


class NotRunning(Exception):
    """No Upshot answered on this computer."""


class Busy(Exception):
    """Upshot is there but did not answer in time: it is working, not gone."""


class UpshotError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


# Loopback only, and never through a proxy the system or the environment names.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _get_json(url: str, timeout: float) -> Any:
    with _OPENER.open(urllib.request.Request(url), timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def app_home() -> Path:
    """Where the app keeps ``server.port``: ``UP_HOME``, else its folder under Local AppData.

    On Windows the Local AppData folder comes from the known-folder API, not from
    ``%LOCALAPPDATA%``, which a process started some other way may not carry.
    """
    env = os.environ.get("UP_HOME")
    if env:
        return Path(env).expanduser()
    if sys.platform == "win32":
        return Path(_known_local_appdata() or os.environ.get("LOCALAPPDATA", "")) / "Upshot"
    return Path.home() / ".local" / "share" / "upshot"


def _known_local_appdata() -> str | None:  # pragma: no cover - Windows only
    import ctypes
    import uuid
    from ctypes import wintypes

    guid = (ctypes.c_byte * 16).from_buffer_copy(
        uuid.UUID("F1B32785-6FBA-4FCF-9D55-7B8E7F157091").bytes_le
    )
    out = wintypes.LPWSTR()
    if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out)):
        return None
    try:
        return out.value
    finally:
        ctypes.windll.ole32.CoTaskMemFree(out)


def access_key() -> str:
    """The access key: ``UPSHOT_KEY``, else the file the app keeps in its home. Read on
    every start and again after a refusal, so a key reset in Settings needs nothing here."""
    given = os.environ.get(KEY_ENV, "").strip()
    if given:
        return given
    try:
        return (app_home() / KEY_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def candidates(url: str | None = None, *, quick: bool = False) -> Iterator[tuple[str, float]]:
    """Where to look, in order, and how long each may take: what was given, the port the
    app recorded, then the usual ports. ``quick``: the first two only, briefly, for a
    question the client waits on (D98: tools/list, before Upshot has started)."""
    wait = QUICK_TIMEOUT_S if quick else RECORDED_TIMEOUT_S
    given = url or os.environ.get("UPSHOT_URL")
    if given:
        yield given.rstrip("/"), wait
        return
    try:
        port = int((app_home() / "server.port").read_text(encoding="utf-8").strip())
        yield f"http://127.0.0.1:{port}", wait
    except (OSError, ValueError):
        pass
    if quick:
        return
    for port in PORTS:
        yield f"http://127.0.0.1:{port}", PROBE_TIMEOUT_S


def discover(url: str | None = None, *, quick: bool = False) -> Upshot:
    """The running Upshot, or :class:`NotRunning`. A stale ``server.port`` or another
    program on the port is skipped: only an answer naming ``app: "upshot"`` counts."""
    seen: set[str] = set()
    for base, timeout in candidates(url, quick=quick):
        if base in seen:
            continue
        seen.add(base)
        try:
            info = _get_json(f"{base}/api/v1/info", timeout)
        except (OSError, ValueError):
            continue
        if isinstance(info, dict) and info.get("app") == "upshot":
            return Upshot(base, info)
    raise NotRunning("Upshot isn't running on this computer. Start Upshot, then ask again.")


class Upshot:
    def __init__(self, base: str, info: dict[str, Any] | None = None, timeout: float = 30) -> None:
        self.base = base.rstrip("/")
        self.info = info or {}
        self.timeout = timeout
        self.key = access_key()
        #: What the AI app calls itself (``clientInfo.name``), for Upshot's activity log.
        self.agent = ""

    # -- plumbing

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        body: bytes | Iterator[bytes] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        raw: bool = False,
    ) -> Any:
        try:
            return self._send(method, path, query=query, body=body, headers=headers,
                              timeout=timeout, raw=raw)  # fmt: skip
        except UpshotError as exc:
            # The key was reset in Settings since this started: read it again, once. A
            # streamed upload cannot be sent twice, so it is told to start over instead.
            if exc.status != 401:
                raise
            fresh = access_key()
            if not fresh or fresh == self.key:
                raise UpshotError(401, NO_KEY) from None
            self.key = fresh
            if body is not None and not isinstance(body, bytes):
                raise UpshotError(401, "Upshot's access key was just reset; ask again.") from None
            try:
                return self._send(method, path, query=query, body=body, headers=headers,
                                  timeout=timeout, raw=raw)  # fmt: skip
            except UpshotError as again:
                if again.status == 401:
                    raise UpshotError(401, NO_KEY) from None
                raise

    def _send(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        body: bytes | Iterator[bytes] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        raw: bool = False,
    ) -> Any:
        url = f"{self.base}{path}"
        if query:
            url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
        request = urllib.request.Request(url, data=body, method=method)  # type: ignore[arg-type]
        request.add_header("X-Upshot-Client", CLIENT)
        if self.key:
            request.add_header("Authorization", f"Bearer {self.key}")
        if self.agent:
            request.add_header("X-Upshot-Agent", self.agent)
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with _OPENER.open(request, timeout=timeout or self.timeout) as response:
                data = response.read()
        except urllib.error.HTTPError as exc:
            raise UpshotError(exc.code, _detail(exc.read())) from None
        except TimeoutError:
            raise Busy(BUSY) from None
        except OSError as exc:
            if isinstance(getattr(exc, "reason", None), TimeoutError):
                raise Busy(BUSY) from None
            raise NotRunning(
                f"Upshot stopped answering ({exc}). Start Upshot, then ask again."
            ) from None
        return data.decode("utf-8") if raw else json.loads(data.decode("utf-8"))

    # -- the API

    def refresh(self) -> None:
        """Ask again whether this is Upshot, and whether its switch is on."""
        info = self._request("GET", "/api/v1/info", timeout=RECORDED_TIMEOUT_S)
        if not isinstance(info, dict) or info.get("app") != "upshot":
            raise NotRunning("Upshot isn't running on this computer. Start Upshot, then ask again.")
        self.info = info

    def submit_path(self, path: str, options: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps({"path": path, **options}).encode("utf-8")
        return self._request(  # type: ignore[no-any-return]
            "POST", "/api/v1/transcriptions", body=body,
            headers={"Content-Type": "application/json"},
        )  # fmt: skip

    def upload(self, source: Path, options: dict[str, Any]) -> dict[str, Any]:
        """The file's bytes, streamed: for a file the app cannot read where it is."""
        boundary = "upshot" + secrets.token_hex(12)
        head = b"".join(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{key}"\r\n\r\n{_field(value)}\r\n'.encode()
            for key, value in options.items()
        )
        name = source.name.replace('"', "")
        head += (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode()
        tail = f"\r\n--{boundary}--\r\n".encode()
        size = len(head) + source.stat().st_size + len(tail)

        def chunks() -> Iterator[bytes]:
            yield head
            with source.open("rb") as handle:
                while block := handle.read(CHUNK):
                    yield block
            yield tail

        return self._request(  # type: ignore[no-any-return]
            "POST", "/api/v1/transcriptions", body=chunks(), timeout=600,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                     "Content-Length": str(size)},
        )  # fmt: skip

    def get(self, job_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/transcriptions/{_q(job_id)}")  # type: ignore[no-any-return]

    def wait(self, job_id: str, seconds: float) -> dict[str, Any]:
        return self._request(  # type: ignore[no-any-return]
            "GET", f"/api/v1/transcriptions/{_q(job_id)}/wait",
            query={"timeout": f"{max(0.0, min(seconds, 120)):.1f}"}, timeout=seconds + 30,
        )  # fmt: skip

    def result(self, job_id: str, fmt: str, **params: Any) -> str:
        return self._request(  # type: ignore[no-any-return]
            "GET", f"/api/v1/transcriptions/{_q(job_id)}/result",
            query={"format": fmt, **params}, raw=True,
        )  # fmt: skip

    def recent(self, limit: int) -> list[dict[str, Any]]:
        listed = self._request("GET", "/api/v1/transcriptions", query={"limit": limit})
        return list(listed.get("transcriptions", []))

    def mcp(self, method: str, params: dict[str, Any], timeout: float = 45) -> dict[str, Any]:
        """One JSON-RPC request to the app's MCP server for the user's own tools,
        ``/mcp/ext/`` (D98): stateless, so no session is opened first."""
        body = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, ensure_ascii=False
        ).encode("utf-8")
        reply = self._request(
            "POST", "/mcp/ext/", body=body, timeout=timeout,
            headers={"Content-Type": "application/json",
                     "Accept": "application/json, text/event-stream"},
        )  # fmt: skip
        if not isinstance(reply, dict):
            raise UpshotError(502, "Upshot answered in a way this program does not understand")
        if "error" in reply:
            error = reply["error"] if isinstance(reply["error"], dict) else {}
            raise UpshotError(400, str(error.get("message") or "Upshot refused the request"))
        return reply.get("result") or {}

    def cancel(self, job_id: str) -> dict[str, Any]:
        return self._request("POST", f"/api/v1/transcriptions/{_q(job_id)}/cancel", body=b"")  # type: ignore[no-any-return]


def _q(job_id: str) -> str:
    return urllib.parse.quote(job_id, safe="")


def _field(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _detail(body: bytes) -> str:
    try:
        parsed = json.loads(body.decode("utf-8"))
        if isinstance(parsed, dict) and isinstance(parsed.get("detail"), str):
            return parsed["detail"]
    except ValueError:
        pass
    return body.decode("utf-8", "replace")[:500] or "Upshot refused the request"
