"""The four tools, over Upshot's REST API (D86, plan §4.6)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from upshot_mcp import paths
from upshot_mcp.protocol import Call, Tool, ToolError
from upshot_mcp.upshot import NotRunning, Upshot, UpshotError, discover

#: A tool call is held open for at most this long; past it, the reply carries an id to
#: come back with. Claude Desktop gave up on a 55-second call ("MCP error -32001: Request
#: timed out") while 25-second ones went through, so the wait stays well inside that.
MAX_WAIT_S = 30
DEFAULT_WAIT_S = 25
#: Characters of transcript per reply. Claude Code's default limit for one tool result is
#: 25 000 tokens, and Hebrew takes far more tokens per character than English.
PART_CHARS = 20_000
#: How long each long-poll asks for, so progress and cancellation are seen in between.
POLL_S = 5
FORMATS = ("text", "srt", "vtt", "json")
EXTENSIONS = {"text": "txt", "srt": "srt", "vtt": "vtt", "json": "json"}

INSTRUCTIONS = (
    "Transcribes audio and video files on this computer with Upshot, the user's local "
    "meeting recorder: the file never leaves the computer. Give transcribe_file the full "
    "path of a file. A long file takes minutes; when the reply says it is still running, "
    "call get_transcription with its id until it is done."
)

STATE_WORDS = {
    "pending": "waiting in Upshot's queue",
    "running": "transcribing",
    "done": "done",
    "failed": "failed",
    "cancelled": "cancelled",
}
REASONS = {
    "recording": "Upshot is recording a meeting; it continues when the recording ends",
    "policy:when_idle": "Upshot waits until the computer is idle",
    "policy:scheduled": "Upshot transcribes in its nightly window",
    "retry": "Upshot will try again shortly",
}


class Bridge:
    def __init__(self, *, url: str | None = None, wsl_distro: str | None = None) -> None:
        self.url = url
        self.wsl_distro = wsl_distro

    def upshot(self) -> Upshot:
        try:
            app = discover(self.url)
        except NotRunning as exc:
            raise ToolError(str(exc)) from None
        if app.info.get("enabled") is False:
            raise ToolError(
                "Transcription for other apps is off in Upshot. Turn it on in Upshot's "
                "Settings, under Transcription for other apps, then ask again."
            )
        return app

    # -- tools

    def transcribe_file(self, call: Call) -> str:
        args = call.arguments
        fmt = _format(args.get("format", "text"))
        options = {
            "language": str(args.get("language") or "auto"),
            "diarize": bool(args.get("diarize", True)),
            "prompt": str(args.get("prompt") or ""),
        }
        app = self.upshot()
        job = self._submit(app, str(args.get("path") or ""), options)
        job = self._wait(app, job, _seconds(args.get("wait_seconds", DEFAULT_WAIT_S)), call)
        return self._answer(app, job, fmt, str(args.get("save_to") or ""), 0, 0)

    def get_transcription(self, call: Call) -> str:
        args = call.arguments
        job_id = str(args.get("id") or "").strip()
        if not job_id:
            raise ToolError("give the id transcribe_file returned")
        app = self.upshot()
        job = _call(app.get, job_id)
        job = self._wait(app, job, _seconds(args.get("wait_seconds", 0)), call)
        return self._answer(
            app, job, _format(args.get("format", "text")), str(args.get("save_to") or ""),
            float(args.get("from_s") or 0), float(args.get("to_s") or 0),
        )  # fmt: skip

    def list_transcriptions(self, call: Call) -> str:
        limit = max(1, min(int(call.arguments.get("limit") or 20), 100))
        jobs = _call(self.upshot().recent, limit)
        if not jobs:
            return "Upshot has no transcriptions yet."
        return "\n".join(_line(job) for job in jobs)

    def cancel_transcription(self, call: Call) -> str:
        job_id = str(call.arguments.get("id") or "").strip()
        if not job_id:
            raise ToolError("give the id of the transcription to cancel")
        job = _call(self.upshot().cancel, job_id)
        return f"{job['source_name']} ({job_id}) is {STATE_WORDS.get(job['state'], job['state'])}."

    # -- steps

    def _submit(self, app: Upshot, path: str, options: dict[str, Any]) -> dict[str, Any]:
        try:
            target = paths.to_windows(path, self.wsl_distro)
        except paths.PathError as exc:
            raise ToolError(str(exc)) from None
        if not paths.is_absolute(target):
            raise ToolError(f"give the full path of the file, not {path!r}")
        try:
            return app.submit_path(target, options)
        except UpshotError as exc:
            # The app could not read it where it is; this process may (another share, a
            # permission). Then send the bytes instead.
            local = Path(target)
            if exc.status == 400 and local.is_file():
                return _call(app.upload, local, options)
            raise ToolError(exc.detail) from None

    def _wait(self, app: Upshot, job: dict[str, Any], seconds: float, call: Call) -> dict[str, Any]:
        deadline = time.monotonic() + seconds
        while job["state"] in ("pending", "running"):
            left = deadline - time.monotonic()
            if left <= 0:
                break
            call.check()
            call.progress(round(float(job.get("progress") or 0) * 100, 1), 100.0, _status(job))
            job = _call(app.wait, job["id"], min(POLL_S, left))
        return job

    def _answer(
        self, app: Upshot, job: dict[str, Any], fmt: str, save_to: str, from_s: float, to_s: float
    ) -> str:
        if job["state"] == "failed":
            raise ToolError(f"Upshot could not transcribe {job['source_name']}: {job.get('error')}")
        if job["state"] == "cancelled":
            raise ToolError(f"The transcription of {job['source_name']} was cancelled.")
        if job["state"] != "done":
            again = f"id={job['id']}, wait_seconds={DEFAULT_WAIT_S}"
            if fmt != "text":
                again += f", format={fmt}"
            if save_to:
                again += f", save_to={save_to}"
            return json.dumps(
                {
                    "id": job["id"],
                    "state": job["state"],
                    "phase": job.get("phase"),
                    "progress": job.get("progress"),
                    "position": job.get("position"),
                    "waiting_reason": job.get("waiting_reason"),
                    "status": _status(job),
                    "next": f"Not done yet. Call get_transcription with {again}, and again "
                    "until it is done.",
                },
                ensure_ascii=False,
            )
        if save_to:
            return self._save(app, job, fmt, save_to)
        if fmt == "text":
            return _text_part(job, json.loads(_call(app.result, job["id"], "json")), from_s, to_s)
        body = _call(app.result, job["id"], {"json": "json"}.get(fmt, fmt))
        if len(body) > PART_CHARS:
            return (
                body[:PART_CHARS]
                + f"\n\n[Cut at {PART_CHARS} characters. Call get_transcription with "
                f"id={job['id']}, format={fmt} and save_to=<a folder> to write the whole file.]"
            )
        return body

    def _save(self, app: Upshot, job: dict[str, Any], fmt: str, save_to: str) -> str:
        folder = paths.to_windows(save_to, self.wsl_distro)
        target = Path(folder)
        if target.suffix.lower() == f".{EXTENSIONS[fmt]}":
            out = target
        else:
            out = target / f"{paths.stem(job['source_name'])}.{EXTENSIONS[fmt]}"
        query = {"timestamps": "true"} if fmt == "text" else {}
        body = _call(app.result, job["id"], "txt" if fmt == "text" else fmt, **query)
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(body, encoding="utf-8")
        except OSError as exc:
            raise ToolError(f"could not write {save_to}: {exc}") from None
        shown = out if not self.wsl_distro else _back_to_wsl(str(out), save_to)
        return (
            f"Saved the {fmt} transcript of {job['source_name']} to {shown} "
            f"({_duration(job)}, language {job.get('language') or 'unknown'})."
        )


# --------------------------------------------------------------------------- helpers


def _call(fn: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except NotRunning as exc:
        raise ToolError(str(exc)) from None
    except UpshotError as exc:
        raise ToolError(exc.detail) from None


def _format(value: Any) -> str:
    fmt = str(value or "text").lower()
    if fmt == "txt":
        fmt = "text"
    if fmt not in FORMATS:
        raise ToolError(f"format must be one of {', '.join(FORMATS)}")
    return fmt


def _seconds(value: Any) -> float:
    try:
        return max(0.0, min(float(value), MAX_WAIT_S))
    except (TypeError, ValueError):
        return 0.0


def _clock(seconds: float) -> str:
    whole = int(seconds)
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def _duration(job: dict[str, Any]) -> str:
    seconds = job.get("duration_s")
    return _clock(float(seconds)) if seconds else "length unknown"


def _status(job: dict[str, Any]) -> str:
    state = job["state"]
    if state == "pending":
        reason = REASONS.get(job.get("waiting_reason") or "")
        position = job.get("position")
        where = f", position {position} in the queue" if position else ""
        return f"waiting{where}" + (f"; {reason}" if reason else "")
    if state == "running":
        percent = round(float(job.get("progress") or 0) * 100)
        return f"transcribing ({job.get('phase') or 'starting'}, {percent}%)"
    return STATE_WORDS.get(state, state)


def _line(job: dict[str, Any]) -> str:
    return f"{job['id']}  {job['source_name']}  {_status(job)}  {_duration(job)}" + (
        f"  {job['language']}" if job.get("language") else ""
    )


def _text_part(job: dict[str, Any], result: dict[str, Any], from_s: float, to_s: float) -> str:
    """Timestamped speaker lines from ``from_s``, at most about PART_CHARS characters."""
    segments = [
        s for s in result.get("segments", [])
        if s["start"] >= from_s and (not to_s or s["start"] < to_s)
    ]  # fmt: skip
    if not result.get("segments"):
        return f"{job['source_name']}: no speech was found ({_duration(job)})."
    lines: list[str] = []
    size = 0
    next_from: float | None = None
    for segment in segments:
        line = f"[{_clock(segment['start'])}] {segment['speaker']}: {segment['text']}"
        if size + len(line) > PART_CHARS and lines:
            next_from = segment["start"]
            break
        lines.append(line)
        size += len(line) + 1
    header = (
        f"{job['source_name']} ({_duration(job)}, language {result.get('language') or 'unknown'}, "
        f"speakers {', '.join(result.get('speakers') or []) or 'none'}), id {job['id']}"
    )
    body = "\n".join(lines) if lines else "(nothing in that range)"
    tail = (
        f"\n\n[More follows. Call get_transcription with id={job['id']} and from_s={next_from}.]"
        if next_from is not None
        else ""
    )
    return f"{header}\n\n{body}{tail}"


def _back_to_wsl(windows: str, given: str) -> str:
    """Say where the file went in the words the caller used."""
    return (
        given
        if given.endswith(windows.rsplit("\\", 1)[-1])
        else f"{given.rstrip('/')}/{windows.rsplit(chr(92), 1)[-1]}"
    )


# --------------------------------------------------------------------------- the list


def tools(bridge: Bridge) -> list[Tool]:
    path_schema = {
        "type": "string",
        "description": "Full path of an audio or video file on this computer, e.g. "
        "C:\\Users\\me\\Videos\\interview.mp4 (or a WSL path such as /home/me/clip.wav).",
    }
    common = {
        "format": {
            "type": "string",
            "enum": list(FORMATS),
            "default": "text",
            "description": "text: timestamped speaker lines. srt or vtt: subtitles. json: "
            "segments with word timings.",
        },
        "save_to": {
            "type": "string",
            "default": "",
            "description": "Optional folder (or file path) to write the result to instead of "
            "returning it, e.g. the folder the video is in.",
        },
    }
    return [
        Tool(
            name="transcribe_file",
            title="Transcribe a file with Upshot",
            description="Transcribe an audio or video file on this computer with Upshot, "
            "locally. Returns the transcript, or, for a long file still running after "
            "wait_seconds, its id and progress for get_transcription.",
            read_only=False,
            run=bridge.transcribe_file,
            input_schema={
                "type": "object",
                "properties": {
                    "path": path_schema,
                    "language": {
                        "type": "string",
                        "default": "auto",
                        "description": "auto, or a language code such as he or en.",
                    },
                    "diarize": {
                        "type": "boolean",
                        "default": True,
                        "description": "Separate the speakers (S1, S2…).",
                    },
                    "prompt": {
                        "type": "string",
                        "default": "",
                        "description": "Optional names or terms said in the recording.",
                    },
                    "wait_seconds": {
                        "type": "number",
                        "default": DEFAULT_WAIT_S,
                        "maximum": MAX_WAIT_S,
                        "description": "How long to wait for the result in this call.",
                    },
                    **common,
                },
                "required": ["path"],
            },
        ),
        Tool(
            name="get_transcription",
            title="Get an Upshot transcription",
            description="A transcription by id: its progress, or the transcript once done. "
            "Text comes in parts of about 20 000 characters; the reply says where the next "
            "part starts (from_s).",
            run=bridge.get_transcription,
            input_schema={
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "from_s": {"type": "number", "default": 0},
                    "to_s": {"type": "number", "default": 0, "description": "0: to the end."},
                    "wait_seconds": {"type": "number", "default": 0, "maximum": MAX_WAIT_S},
                    **common,
                },
                "required": ["id"],
            },
        ),
        Tool(
            name="list_transcriptions",
            title="List Upshot transcriptions",
            description="Recent file transcriptions in Upshot, newest first, with their state.",
            run=bridge.list_transcriptions,
            input_schema={
                "type": "object",
                "properties": {"limit": {"type": "integer", "default": 20, "maximum": 100}},
            },
        ),
        Tool(
            name="cancel_transcription",
            title="Cancel an Upshot transcription",
            description="Stop a waiting or running transcription. It stays in Upshot's list as "
            "cancelled.",
            read_only=False,
            run=bridge.cancel_transcription,
            input_schema={
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
        ),
    ]
