"""The file transcription service over HTTP: ``/api/v1`` (D86, plan §4.4).

Any program on this computer may use it, with no key (R2). Websites are kept out by the
Origin guard (``app/api/security.py``), and ``/api/v1`` is exempt from CSRF, which a
program has no cookie for. The Settings switch ``transcription.service_enabled`` turns
it off for programs; the page, recognised by its ``up_csrf`` cookie or header, keeps
working. That switch is a courtesy, not an access control: any local program can get the
cookie (D57).

Uploads are parsed as they stream in, straight into the data root: Starlette's own form
parsing would spool the whole file to ``%TEMP%`` first, a second copy on C:.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import secrets
import shutil
import time
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import quote

import anyio
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from starlette.requests import ClientDisconnect

from app.api.security import CSRF_COOKIE, CSRF_HEADER
from app.asr import languages
from app.audio.ingest import UnsupportedAudio, probe
from app.log import get
from app.services import Services
from app.transcription.render import FORMATS, filename, render
from app.transcription.store import (
    DONE,
    FINISHED,
    INPUT_DIR,
    PENDING,
    RESULT_NAME,
    Transcription,
    TranscriptionStore,
    new_id,
)
from app.transcription.types import MAX_PROMPT_CHARS, MAX_WORDS_PER_CUE, Options

log = get(__name__)

router = APIRouter(prefix="/api/v1")

CLIENTS = frozenset({"ui", "api", "mcp"})
CLIENT_HEADER = "x-upshot-client"
#: Bytes of decoded 16 kHz mono WAV per second of audio, plus a tenth for headroom.
WAV_BYTES_PER_S = 32_000 * 1.1
MAX_WAIT_S = 120
#: Writes to disk are batched to this before each hop to a worker thread.
WRITE_BATCH = 1 << 20
#: The largest a non-file form field may be (the prompt is 1000 characters).
MAX_FIELD_BYTES = 16 * 1024
OFF_DETAIL = "Transcription for other apps is off in Upshot's Settings"


def services_of(request: Request) -> Services:
    return request.app.state.services  # type: ignore[no-any-return]


def store_of(request: Request) -> TranscriptionStore:
    store = services_of(request).transcriptions
    assert store is not None
    return store  # type: ignore[no-any-return]


# --------------------------------------------------------------------------- the switch


def is_page(request: Request, svc: Services) -> bool:
    """The app's own page: it holds the ``up_csrf`` cookie, or sends it as a header."""
    return svc.auth.valid_csrf(request.cookies.get(CSRF_COOKIE)) or svc.auth.valid_csrf(
        request.headers.get(CSRF_HEADER)
    )


def require_enabled(request: Request) -> Services:
    svc = services_of(request)
    if not bool(svc.config.get("transcription.service_enabled", True)) and not is_page(
        request, svc
    ):
        raise HTTPException(403, OFF_DETAIL)
    return svc


# --------------------------------------------------------------------------- limits


def max_hours(svc: Services) -> float:
    """``transcription.max_hours``, or 6 on a GPU and 2 on the CPU (D86): large-v3 runs at
    about 4× the audio's length on the CPU, and the one worker is shared with meetings."""
    configured = svc.config.get("transcription.max_hours")
    if configured:
        return float(configured)
    if str(svc.config.get("asr.backend", "local")) == "fake":
        return 6.0
    device = svc.extras.get("transcription_device")
    if device is None:
        from app.asr.local import planned_device

        device = planned_device(svc.config)
        svc.extras["transcription_device"] = device
    return 6.0 if device == "cuda" else 2.0


def max_upload_bytes(svc: Services) -> int:
    return int(float(svc.config.get("transcription.max_upload_mb", 4096)) * 1024 * 1024)


def free_bytes(svc: Services) -> int:
    root = Path(svc.config.data_root)
    root.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(str(root)).free


def options_from(fields: dict[str, str]) -> Options:
    """The caller's options, validated: 422 on anything that is not one."""
    language = (fields.get("language") or "auto").strip().lower()
    if language != "auto" and not languages.is_supported(language):
        raise HTTPException(422, f"unknown language {language!r}: 'auto' or a Whisper code")
    diarize_raw = (fields.get("diarize") or "true").strip().lower()
    if diarize_raw not in ("true", "false", "1", "0", "yes", "no"):
        raise HTTPException(422, "diarize: true or false")
    prompt = fields.get("prompt") or ""
    if len(prompt) > MAX_PROMPT_CHARS:
        raise HTTPException(422, f"prompt: at most {MAX_PROMPT_CHARS} characters")
    words_raw = fields.get("max_words_per_cue") or "7"
    try:
        words = int(words_raw)
    except ValueError:
        raise HTTPException(422, "max_words_per_cue: a whole number") from None
    if not 1 <= words <= MAX_WORDS_PER_CUE:
        raise HTTPException(422, f"max_words_per_cue: 1 to {MAX_WORDS_PER_CUE}")
    return Options(
        language=language,
        diarize=diarize_raw in ("true", "1", "yes"),
        prompt=prompt,
        max_words_per_cue=words,
    )


def client_of(request: Request) -> str:
    client = (request.headers.get(CLIENT_HEADER) or "api").strip().lower()
    return client if client in CLIENTS else "api"


def safe_suffix(name: str) -> str:
    """The extension of an uploaded file, made safe for a file name; never the name."""
    suffix = PureWindowsPath(PurePosixPath(name).name).suffix.lower().lstrip(".")
    return suffix if re.fullmatch(r"[a-z0-9]{1,8}", suffix) else "bin"


def display_name(name: str) -> str:
    """The original name, for display only: its last path component, at most 255 chars."""
    base = PureWindowsPath(PurePosixPath(name or "").name).name.strip()
    return (base or "upload")[:255]


# --------------------------------------------------------------------------- paths


def check_path(raw: str) -> Path:
    """A ``path`` input, or 400 (§4.4). Drive paths and the WSL shares only: any other UNC
    path would make Upshot authenticate to that host over SMB, and a POSIX path means the
    caller is in WSL and should use the bridge, which translates it."""
    text = (raw or "").strip()
    if not text:
        raise HTTPException(400, "path: give the full path of a local file")
    if text.startswith(("\\\\?\\", "\\\\.\\", "//?/", "//./")):
        raise HTTPException(400, "path: device paths are not accepted")
    if text.startswith(("\\\\", "//")):
        host = re.split(r"[\\/]", text.lstrip("\\/"), maxsplit=1)[0].lower()
        if host not in ("wsl.localhost", "wsl$"):
            raise HTTPException(
                400, "path: network paths are not accepted, only files on this computer"
            )
    elif text.startswith("/"):
        if not (Path(text).is_absolute() and _posix_host()):
            raise HTTPException(
                400,
                "path: this is a WSL or Linux path. Use the upshot-mcp bridge, which "
                "translates it, or upload the file instead",
            )
    elif not re.match(r"^[A-Za-z]:[\\/]", text):
        raise HTTPException(400, "path: give the full path of a local file, e.g. C:\\...")
    path = Path(text)
    if not path.exists():
        raise HTTPException(400, f"path: no such file: {text}")
    if not path.is_file():
        raise HTTPException(400, f"path: not a file: {text}")
    return path


def _posix_host() -> bool:
    """On Linux and macOS (a source checkout, the tests) a POSIX path is a local path."""
    import os

    return os.name != "nt"


# --------------------------------------------------------------------------- answers


def links(job_id: str) -> dict[str, str]:
    base = f"/api/v1/transcriptions/{job_id}"
    return {
        "self": base,
        "wait": f"{base}/wait",
        "result": f"{base}/result",
        "cancel": f"{base}/cancel",
    }


def waiting_reason(svc: Services, job: Transcription) -> str | None:
    if job.state != PENDING:
        return None
    if job.not_before is not None:
        from app.clock import parse_iso

        if parse_iso(job.not_before) > svc.clock.now():
            return "retry"
    worker = svc.worker
    reason = worker.waiting_reason() if worker is not None else "queue"
    return str(reason)


def describe(svc: Services, job: Transcription) -> dict[str, Any]:
    payload = job.as_dict()
    payload["position"] = svc.scheduler.position(job) if svc.scheduler else None
    payload["waiting_reason"] = waiting_reason(svc, job)
    # Which way the transcript runs: the RTL list lives in app/asr/languages.py only.
    payload["direction"] = languages.direction_for(job.language)
    payload["links"] = links(job.id)
    return payload


def find(svc: Services, job_id: str) -> Transcription:
    job = svc.transcriptions.get(job_id) if svc.transcriptions else None
    if job is None:
        raise HTTPException(404, f"no such transcription: {job_id}")
    return job  # type: ignore[no-any-return]


# --------------------------------------------------------------------------- creating


@router.post("/transcriptions", status_code=202)
async def create(request: Request) -> JSONResponse:
    """A file, as a multipart upload (``file`` plus options) or as JSON ``{"path": …}``."""
    svc = require_enabled(request)
    kind = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if kind == "multipart/form-data":
        job = await _from_upload(request, svc)
    elif kind == "application/json":
        job = await _from_path(request, svc)
    else:
        raise HTTPException(415, "send multipart/form-data with a file, or JSON with a path")
    return JSONResponse(describe(svc, job), status_code=202)


async def _from_path(request: Request, svc: Services) -> Transcription:
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(422, "the body is not JSON") from None
    if not isinstance(body, dict):
        raise HTTPException(422, "the body must be a JSON object")
    fields = {key: _field_text(value) for key, value in body.items() if key != "path"}
    options = options_from(fields)
    source = check_path(str(body.get("path") or ""))
    size = source.stat().st_size
    duration = await _check_media(svc, source, label=source.name)
    store = svc.transcriptions
    return store.create(  # type: ignore[no-any-return]
        source_name=display_name(source.name),
        source_kind="path",
        source_path=str(source),
        options=options,
        client=client_of(request),
        size_bytes=size,
        duration_s=duration,
    )


def _field_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)


async def _from_upload(request: Request, svc: Services) -> Transcription:
    store: TranscriptionStore = svc.transcriptions
    assert store is not None
    declared = request.headers.get("content-length")
    if declared is None:
        raise HTTPException(411, "send a Content-Length")
    limit = max_upload_bytes(svc)
    if int(declared) > limit:
        raise HTTPException(413, f"the file is larger than {limit // (1024 * 1024)} MB")
    if int(declared) > free_bytes(svc):
        raise HTTPException(507, "not enough free disk space for this file")

    incoming = store.incoming()
    incoming.mkdir(parents=True, exist_ok=True)
    partial = incoming / secrets.token_hex(8)
    upload = _Upload(request.headers.get("content-type", ""), partial)
    try:
        await upload.receive(request, limit)
        if upload.file_name is None:
            raise HTTPException(422, "no file: send it as the form field 'file'")
        options = options_from(upload.fields)
        duration = await _check_media(svc, partial, label=display_name(upload.file_name))
        job_id = new_id()
        folder = store.folder(job_id)
        target = folder / INPUT_DIR / f"source.{safe_suffix(upload.file_name)}"
        target.parent.mkdir(parents=True, exist_ok=True)
        partial.replace(target)
        try:
            return store.create(
                source_name=display_name(upload.file_name),
                source_kind="upload",
                source_path=str(target),
                options=options,
                client=client_of(request),
                size_bytes=upload.size,
                duration_s=duration,
                transcription_id=job_id,
            )
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise
    finally:
        with contextlib.suppress(OSError):
            partial.unlink(missing_ok=True)


async def _check_media(svc: Services, source: Path, *, label: str) -> float | None:
    """415 for what is not audio, 413 past ``max_hours``, 507 without room for the WAV."""
    try:
        described = await anyio.to_thread.run_sync(lambda: probe(source, config=svc.config))
    except UnsupportedAudio as exc:
        raise HTTPException(415, str(exc).replace(source.name, label)) from None
    if not described.has_audio:
        raise HTTPException(415, f"{label} has no audio")
    duration = described.duration_s
    if duration is not None:
        hours = max_hours(svc)
        if duration > hours * 3600:
            raise HTTPException(
                413,
                f"the recording is {duration / 3600:.1f} hours; this computer takes files of "
                f"up to {hours:g} hours",
            )
        if duration * WAV_BYTES_PER_S > free_bytes(svc):
            raise HTTPException(507, "not enough free disk space to transcribe this file")
    return duration


class _Upload:
    """A multipart body parsed as it arrives: the ``file`` part straight to disk, the other
    parts (the options) into ``fields``."""

    def __init__(self, content_type: str, target: Path) -> None:
        from python_multipart.multipart import MultipartParser, parse_options_header

        _, params = parse_options_header(content_type)
        boundary = params.get(b"boundary")
        if not boundary:
            raise HTTPException(422, "multipart body without a boundary")
        self.target = target
        self.fields: dict[str, str] = {}
        self.file_name: str | None = None
        self.size = 0
        self._handle: Any = None
        self._buffer = bytearray()
        self._pending: list[bytes] = []
        self._header_field = b""
        self._header_value = b""
        self._headers: dict[bytes, bytes] = {}
        self._part_name: str | None = None
        self._part_is_file = False
        self._field_value = bytearray()
        self.parser = MultipartParser(
            boundary,
            {
                "on_part_begin": self._part_begin,
                "on_header_field": self._on_header_field,
                "on_header_value": self._on_header_value,
                "on_header_end": self._header_end,
                "on_headers_finished": self._headers_finished,
                "on_part_data": self._part_data,
                "on_part_end": self._part_end,
            },
        )

    async def receive(self, request: Request, limit: int) -> None:
        received = 0
        self._handle = await anyio.to_thread.run_sync(self.target.open, "wb")
        try:
            async for chunk in request.stream():
                received += len(chunk)
                if received > limit:
                    raise HTTPException(413, f"the file is larger than {limit // (1024 * 1024)} MB")
                self.parser.write(chunk)
                if len(self._buffer) >= WRITE_BATCH:
                    await self._flush()
            self.parser.finalize()
            await self._flush()
        except ClientDisconnect:
            log.info("an upload was abandoned after %d bytes", received)
            raise HTTPException(400, "the upload was interrupted") from None
        finally:
            handle, self._handle = self._handle, None
            await anyio.to_thread.run_sync(handle.close)

    async def _flush(self) -> None:
        if self._buffer:
            data = bytes(self._buffer)
            self._buffer.clear()
            await anyio.to_thread.run_sync(self._handle.write, data)

    # -- parser callbacks (synchronous, inside parser.write)

    def _part_begin(self) -> None:
        self._headers = {}
        self._part_name = None
        self._part_is_file = False
        self._field_value = bytearray()

    def _on_header_field(self, data: bytes, start: int, end: int) -> None:
        self._header_field += data[start:end]

    def _on_header_value(self, data: bytes, start: int, end: int) -> None:
        self._header_value += data[start:end]

    def _header_end(self) -> None:
        self._headers[self._header_field.lower()] = self._header_value
        self._header_field = b""
        self._header_value = b""

    def _headers_finished(self) -> None:
        from python_multipart.multipart import parse_options_header

        _, params = parse_options_header(self._headers.get(b"content-disposition", b""))
        name = params.get(b"name", b"").decode("utf-8", "replace")
        self._part_name = name
        if name == "file" and b"filename" in params:
            if self.file_name is not None:
                raise HTTPException(422, "one file per request")
            raw = params[b"filename"]
            self.file_name = raw.decode("utf-8", "replace")
            self._part_is_file = True

    def _part_data(self, data: bytes, start: int, end: int) -> None:
        if self._part_is_file:
            self._buffer += data[start:end]
            self.size += end - start
            return
        self._field_value += data[start:end]
        if len(self._field_value) > MAX_FIELD_BYTES:
            raise HTTPException(422, f"the field {self._part_name!r} is too long")

    def _part_end(self) -> None:
        if not self._part_is_file and self._part_name:
            self.fields[self._part_name] = self._field_value.decode("utf-8", "replace")


# --------------------------------------------------------------------------- reading


@router.get("/transcriptions")
def list_transcriptions(
    request: Request,
    state: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    before: str | None = Query(None),
) -> dict[str, Any]:
    svc = require_enabled(request)
    store = store_of(request)
    jobs = store.recent(state=state, limit=limit, before=before)
    return {"transcriptions": [describe(svc, job) for job in jobs]}


@router.get("/transcriptions/{job_id}")
def get_transcription(request: Request, job_id: str) -> dict[str, Any]:
    svc = require_enabled(request)
    return describe(svc, find(svc, job_id))


@router.get("/transcriptions/{job_id}/wait")
async def wait(
    request: Request, job_id: str, timeout: float = Query(60, ge=0, le=MAX_WAIT_S)
) -> dict[str, Any]:
    """Until the job is finished or ``timeout`` seconds pass, whichever is first. Async on
    the event bus: a waiting client holds no thread."""
    svc = require_enabled(request)
    events = svc.events
    queue = events.subscribe()
    try:
        job = find(svc, job_id)
        deadline = time.monotonic() + timeout
        while job.state not in FINISHED:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                event = await asyncio.wait_for(queue.get(), timeout=remaining)
            except TimeoutError:
                break
            if event.type != "transcription" or event.payload.get("id") != job_id:
                continue
            if event.payload.get("state") == "deleted":
                raise HTTPException(404, f"{job_id} was deleted")
            job = find(svc, job_id)
    finally:
        events.unsubscribe(queue)
    return describe(svc, job)


@router.get("/transcriptions/{job_id}/result")
def result(
    request: Request,
    job_id: str,
    format: str = Query("json"),
    timestamps: bool = Query(False),
    max_words_per_cue: int | None = Query(None, ge=1, le=MAX_WORDS_PER_CUE),
) -> Response:
    svc = require_enabled(request)
    job = find(svc, job_id)
    if format not in FORMATS:
        raise HTTPException(422, f"format: one of {', '.join(FORMATS)}")
    if job.state != DONE:
        raise HTTPException(409, f"{job_id} is {job.state}, not done")
    path = store_of(request).folder(job_id) / RESULT_NAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    words = max_words_per_cue or job.parsed_options.max_words_per_cue
    body = render(payload, format, max_words_per_cue=words, timestamps=timestamps)
    name = filename(job.source_name, format)
    return Response(
        content=body.encode("utf-8"),
        media_type=FORMATS[format][0],
        headers={"Content-Disposition": content_disposition(name)},
    )


def content_disposition(name: str) -> str:
    """RFC 6266 with RFC 5987: an ASCII fallback, and the real name (Hebrew) in UTF-8."""
    fallback = re.sub(r"[^A-Za-z0-9._-]", "_", name) or "transcript"
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"


# --------------------------------------------------------------------------- changing


@router.post("/transcriptions/{job_id}/cancel")
def cancel(request: Request, job_id: str) -> dict[str, Any]:
    svc = require_enabled(request)
    find(svc, job_id)
    return describe(svc, store_of(request).cancel(job_id))


@router.post("/transcriptions/{job_id}/retry")
def retry(request: Request, job_id: str) -> JSONResponse:
    svc = require_enabled(request)
    job = find(svc, job_id)
    if job.source_kind == "upload" and not Path(job.source_path).exists():
        raise HTTPException(409, "the uploaded file is no longer kept; add it again")
    try:
        retried = store_of(request).retry(job_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    return JSONResponse(describe(svc, retried), status_code=202)


@router.delete("/transcriptions/{job_id}")
def delete(request: Request, job_id: str) -> dict[str, Any]:
    """Cancels a running job and removes it once it has stopped (a hand-off)."""
    svc = require_enabled(request)
    find(svc, job_id)
    gone = store_of(request).delete(job_id)
    return {"id": job_id, "deleted": gone, "pending_delete": not gone}


# --------------------------------------------------------------------------- info


@router.get("/info")
def info(request: Request) -> dict[str, Any]:
    """Who answers, and what it will take. Open with the switch off, so a client can say
    the switch is off rather than guess; the bridge finds the app by ``app: "upshot"``."""
    svc = services_of(request)
    from app.version import build_info

    missing: list[str] = []
    if str(svc.config.get("asr.backend", "local")) != "fake":
        from app.asr.models import missing_roles

        missing = list(missing_roles(svc.config))
    worker = svc.worker
    return {
        "app": "upshot",
        "api": 1,
        "version": build_info().as_dict().get("version"),
        "enabled": bool(svc.config.get("transcription.service_enabled", True)),
        "models_missing": missing,
        "device": svc.extras.get("transcription_device"),
        "queue_depth": svc.scheduler.depth() if svc.scheduler else svc.queue.depth(),
        "waiting_reason": worker.waiting_reason() if worker is not None else None,
        "languages": ["auto", *sorted(languages.LANGUAGES)],
        "formats": list(FORMATS),
        "max_upload_mb": int(float(svc.config.get("transcription.max_upload_mb", 4096))),
        "max_hours": max_hours(svc),
    }


# --------------------------------------------------------------------------- the page's own

#: For Settings → "Transcription for other apps": how to connect Claude. Under ``/api``,
#: not ``/api/v1``, so CSRF protects it like every other route of the page's.
ui_router = APIRouter(prefix="/api/transcription")

BRIDGE_DIR = "mcp"
BRIDGE_EXE = "upshot-mcp.exe"
BUNDLE_NAME = "upshot-transcribe.mcpb"
SERVER_NAME = "upshot-transcribe"
#: A made-up install folder for the e2e server, so the copy buttons have a real shape.
TEST_INSTALL_DIR = "C:\\Users\\someone\\AppData\\Local\\Programs\\Upshot"


def install_dir() -> str | None:
    """Where the installed app lives (the frozen exe's folder); None in a source checkout."""
    import sys

    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).parent)
    from app.main import test_mode

    return TEST_INSTALL_DIR if test_mode() else None


def wsl_path(windows_path: str) -> str:
    r"""``C:\Users\x`` → ``/mnt/c/Users/x``: the default automount root."""
    drive, _, rest = windows_path.partition(":")
    return f"/mnt/{drive.lower()}{rest.replace(chr(92), '/')}"


def desktop_config_paths() -> list[str]:
    """Claude Desktop keeps its config in one of two places: under ``%APPDATA%`` for the
    regular installer, under the package's folder for the Microsoft Store build (MSIX)."""
    import os

    found: list[str] = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        packages = Path(local) / "Packages"
        if packages.is_dir():
            for package in sorted(packages.glob("Claude_*")):
                found.append(
                    str(
                        package / "LocalCache" / "Roaming" / "Claude" / "claude_desktop_config.json"
                    )
                )
    roaming = os.environ.get("APPDATA")
    if roaming:
        found.append(str(Path(roaming) / "Claude" / "claude_desktop_config.json"))
    return found


@ui_router.get("/connect")
def connect(request: Request) -> dict[str, Any]:
    """The commands and paths Settings shows, with this install's real path and port."""
    svc = services_of(request)
    port = svc.config.server_port
    folder = install_dir()
    bridge = f"{folder}\\{BRIDGE_DIR}\\{BRIDGE_EXE}" if folder else None
    bundle = Path(folder) / BRIDGE_DIR / BUNDLE_NAME if folder else None
    api_url = f"http://127.0.0.1:{port}/api/v1"
    desktop_config = (
        {"mcpServers": {SERVER_NAME: {"command": bridge, "args": []}}} if bridge else None
    )
    return {
        "enabled": bool(svc.config.get("transcription.service_enabled", True)),
        "api_url": api_url,
        "install_dir": folder,
        "bridge": bridge,
        "bundle_available": bool(bundle and bundle.exists())
        or (bundle is not None and folder == TEST_INSTALL_DIR),
        "wsl_command": (
            f'claude mcp add {SERVER_NAME} -- "{wsl_path(bridge)}" --wsl-distro "$WSL_DISTRO_NAME"'
            if bridge
            else None
        ),
        "windows_command": f'claude mcp add {SERVER_NAME} -- "{bridge}"' if bridge else None,
        "desktop_config": json.dumps(desktop_config, indent=2) if desktop_config else None,
        "desktop_config_paths": desktop_config_paths(),
        "curl_example": f"curl -F file=@clip.mp4 {api_url}/transcriptions",
        "keep_days": svc.config.get("transcription.keep_days"),
    }


@ui_router.post("/add-to-claude-desktop")
def add_to_claude_desktop(request: Request) -> dict[str, Any]:
    """Open the bundled ``.mcpb`` with the OS: Claude Desktop shows its Install dialog."""
    folder = install_dir()
    bundle = Path(folder) / BRIDGE_DIR / BUNDLE_NAME if folder else None
    if folder == TEST_INSTALL_DIR:
        services_of(request).extras["opened_bundle"] = str(bundle)
        return {"opened": True}
    if bundle is None or not bundle.exists():
        raise HTTPException(404, "the Claude Desktop bundle comes with the installed app")
    import os

    startfile = getattr(os, "startfile", None)
    if startfile is None:  # pragma: no cover - Windows only
        raise HTTPException(501, "opening the bundle needs Windows")
    startfile(str(bundle))  # pragma: no cover - Windows only
    return {"opened": True}  # pragma: no cover
