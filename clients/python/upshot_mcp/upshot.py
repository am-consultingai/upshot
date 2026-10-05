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
PORTS = (8000, *range(8010, 8041))
CLIENT = "mcp"
#: How long a port may take to answer the probe. A closed port is refused at once
#: whatever this says, so it only bounds a port that is open: the one Upshot recorded
#: gets long enough for an Upshot busy transcribing on the CPU (machine B, 2026-10-05,
#: where 0.5 s read as "not running"), the fallback range less.
RECORDED_TIMEOUT_S = 10.0
PROBE_TIMEOUT_S = 2.0
CHUNK = 1 << 20
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


def candidates(url: str | None = None) -> Iterator[tuple[str, float]]:
    """Where to look, in order, and how long each may take: what was given, the port the
    app recorded, then the usual ports."""
    given = url or os.environ.get("UPSHOT_URL")
    if given:
        yield given.rstrip("/"), RECORDED_TIMEOUT_S
        return
    try:
        port = int((app_home() / "server.port").read_text(encoding="utf-8").strip())
        yield f"http://127.0.0.1:{port}", RECORDED_TIMEOUT_S
    except (OSError, ValueError):
        pass
    for port in PORTS:
        yield f"http://127.0.0.1:{port}", PROBE_TIMEOUT_S


def discover(url: str | None = None) -> Upshot:
    """The running Upshot, or :class:`NotRunning`. A stale ``server.port`` or another
    program on the port is skipped: only an answer naming ``app: "upshot"`` counts."""
    seen: set[str] = set()
    for base, timeout in candidates(url):
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
        url = f"{self.base}{path}"
        if query:
            url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
        request = urllib.request.Request(url, data=body, method=method)  # type: ignore[arg-type]
        request.add_header("X-Upshot-Client", CLIENT)
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
