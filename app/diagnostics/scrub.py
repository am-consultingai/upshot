"""What is removed from anything a crash report says, and how its stack is made (D87).

A report never carries meeting content: no transcript, summary, title, attendee, calendar
detail, file name or path. The stack is module and function names with line numbers;
the exception message is the only free text, and it passes through ``scrub_text``:

- the user's home folder and the app's data folder become ``~`` (a Windows user name
  elsewhere in a path becomes ``<user>``);
- a meeting slug (``2026-10-05_1944_0bac74_<title>``) becomes ``<meeting>``;
- an email address becomes ``<email>``;
- a URL keeps its scheme and host, never its path or query;
- a long run of token-like characters becomes ``<redacted>``;
- quoted text longer than a few words becomes ``<text>``: a title, a sentence of a
  transcript or a prompt does not belong in a crash report;
- any run of words in a script other than Latin (Hebrew, Arabic, Cyrillic, …) becomes
  ``<text>``: Upshot's own messages are English, so such words are always the user's —
  a Hebrew title or transcript line written into a message without quotes;
- what is left is cut to ``MAX_TEXT`` characters.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable
from pathlib import Path
from types import TracebackType
from typing import Any

MAX_TEXT = 300
MAX_FRAMES = 40

_MEETING = re.compile(r"\d{4}-\d{2}-\d{2}_\d{4}_[0-9a-f]{6}(?:_[^\s/\\'\"<>|:]*)?")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_URL = re.compile(r"\b(https?)://([^/\s'\"<>?#]+)[^\s'\"<>]*")
_TOKEN = re.compile(r"[A-Za-z0-9+/=_\-]{32,}")
_WINDOWS_USER = re.compile(r"(?i)([A-Z]:\\Users\\)[^\\/\s'\"]+")
_POSIX_USER = re.compile(r"(/(?:home|Users)/)[^/\s'\"]+")
_QUOTED = re.compile(r"(['\"“”‘’])([^'\"“”‘’]{0,400}?)\1")
#: Quoted text with more words than this is someone's words, not an identifier.
QUOTED_WORDS = 3
#: A run of letters outside Latin and the common punctuation between them.
_FOREIGN = re.compile(
    r"[^\x00-\u024f\s\d<>()\[\]{}'\"“”‘’….,:;!?/\\_\-+=*&%$#@~|]+(?:[\s\-_]+[^\x00-\u024f\s\d<>()\[\]{}'\"“”‘’….,:;!?/\\_\-+=*&%$#@~|]+)*"
)


def scrub_text(text: str, *, homes: Iterable[str] = ()) -> str:
    out = str(text)
    for home in sorted({h for h in homes if h and len(h) > 3}, key=len, reverse=True):
        for variant in {home, home.replace("\\", "/"), home.replace("/", "\\")}:
            out = re.sub(re.escape(variant), "~", out, flags=re.IGNORECASE)
    out = _MEETING.sub("<meeting>", out)
    out = _EMAIL.sub("<email>", out)
    out = _URL.sub(lambda m: f"{m.group(1)}://{m.group(2)}/…", out)
    out = _WINDOWS_USER.sub(r"\1<user>", out)
    out = _POSIX_USER.sub(r"\1<user>", out)
    out = _TOKEN.sub("<redacted>", out)
    out = _FOREIGN.sub("<text>", out)
    out = _QUOTED.sub(
        lambda m: m.group(0) if len(m.group(2).split()) <= QUOTED_WORDS else "<text>", out
    )
    return out if len(out) <= MAX_TEXT else out[: MAX_TEXT - 1] + "…"


def homes() -> list[str]:
    """The folders whose paths are personal: the user's, and the app's data folder."""
    found = [str(Path.home())]
    try:
        from app import paths

        found.append(str(paths.app_home()))
    except Exception:  # never let scrubbing fail on a path lookup
        pass
    return found


def frames_from_traceback(tb: TracebackType | None) -> list[dict[str, Any]]:
    """Sentry frames, oldest first: module, function, line, and whether it is Upshot's.

    The module comes from the frame's globals, so no file path is ever read, let alone
    sent. No local variables, no source lines.
    """
    frames: list[dict[str, Any]] = []
    while tb is not None:
        code = tb.tb_frame.f_code
        module = str(tb.tb_frame.f_globals.get("__name__") or "?")
        frames.append(
            {
                "module": module,
                "function": code.co_name,
                "lineno": tb.tb_lineno,
                "in_app": module == "app" or module.startswith("app."),
            }
        )
        tb = tb.tb_next
    return frames[-MAX_FRAMES:]


def exception_values(exc: BaseException) -> list[dict[str, Any]]:
    """The exception and those it was raised from, innermost last, as Sentry wants them."""
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen and len(chain) < 5:
        chain.append(current)
        seen.add(id(current))
        current = current.__cause__ or (
            None if current.__suppress_context__ else current.__context__
        )
    values = []
    for item in reversed(chain):
        kind = type(item)
        frames = frames_from_traceback(item.__traceback__)
        values.append(
            {
                "type": kind.__name__,
                "module": kind.__module__,
                "value": _message(str(item), frames),
                "stacktrace": {"frames": frames},
            }
        )
    return values


#: Code whose errors quote what a model or an AI CLI said, which can be a meeting's
#: content in plain words no pattern can recognise: only what comes before the first
#: colon is kept ("Claude Code exited 1"), the rest is withheld.
WITHHELD_FROM = ("app.llm", "app.ask", "app.assistant")


def _message(text: str, frames: list[dict[str, Any]]) -> str:
    raised_in = next((f["module"] for f in reversed(frames) if f.get("in_app")), "")
    if any(raised_in == m or raised_in.startswith(m + ".") for m in WITHHELD_FROM):
        head = text.split(":", 1)[0]
        return scrub_text(head, homes=homes()) + (": <withheld>" if ":" in text else "")
    return scrub_text(text, homes=homes())


#: ``File "C:\…\app\pipeline\worker.py", line 120 in _run`` from faulthandler.
_FAULT_FRAME = re.compile(r'File "(?P<path>[^"]+)", line (?P<line>\d+) in (?P<func>\S+)')


def frames_from_fault_dump(text: str) -> list[dict[str, Any]]:
    """Frames from a faulthandler dump: the thread that crashed, oldest first.

    faulthandler writes file paths; only a module name derived from the part under the
    ``app`` package survives (``app.pipeline.worker``), anything else keeps its file stem.
    """
    blocks = re.split(r"\n\s*\n", text.strip())
    crashed = next(
        (b for b in blocks if "most recent call first" in b), blocks[0] if blocks else ""
    )
    frames: list[dict[str, Any]] = []
    for match in _FAULT_FRAME.finditer(crashed):
        frames.append(
            {
                "module": _module_of(match.group("path")),
                "function": match.group("func"),
                "lineno": int(match.group("line")),
                "in_app": _module_of(match.group("path")).startswith("app"),
            }
        )
    frames.reverse()  # faulthandler lists the most recent call first
    return frames[-MAX_FRAMES:]


#: ``    at name (http://127.0.0.1:8000/assets/index-AbC.js:12:345)`` or without the name.
_JS_FRAME = re.compile(
    r"^\s*at (?:(?P<func>.+?) \()?(?P<url>[^\s()]+):(?P<line>\d+):(?P<col>\d+)\)?\s*$"
)
_JS_TYPE = re.compile(r"^(?P<type>[A-Za-z_$][\w$]*(?:Error|Exception))(?::\s?(?P<rest>.*))?$")


def client_exception(message: str, stack: str) -> dict[str, Any]:
    """An error from the page, in Sentry's shape: its type, the scrubbed message, frames.

    A frame keeps the function name, the bundle's file name and the line and column, so
    Sentry can map it back through the source maps uploaded for the release. The page's
    address is reduced to ``app:///assets/<bundle>``: never a query, a route or a host.
    """
    lines = (stack or "").splitlines()
    first = lines[0].strip() if lines else ""
    kind = "Error"
    head = _JS_TYPE.match(first)
    if head:
        kind = head.group("type")
    frames: list[dict[str, Any]] = []
    for line in lines[1:]:
        match = _JS_FRAME.match(line)
        if not match:
            continue
        url = match.group("url").split("?")[0].split("#")[0]
        ours = "/assets/" in url
        # Only a bundle keeps its name: anything else is a page, whose address can be a
        # meeting's, title and all.
        name = url.rsplit("/", 1)[-1] if ours else "page"
        frames.append(
            {
                "function": (match.group("func") or "?").strip()[:100],
                "abs_path": f"app:///assets/{name}" if ours else "app:///page",
                "filename": f"assets/{name}" if ours else "page",
                "lineno": int(match.group("line")),
                "colno": int(match.group("col")),
                "in_app": ours,
            }
        )
    frames.reverse()  # Chrome lists the most recent call first; Sentry wants it last
    return {
        "type": kind,
        "value": scrub_text(message or first, homes=homes()),
        "stacktrace": {"frames": frames[-MAX_FRAMES:]},
    }


def _module_of(path: str) -> str:
    parts = re.split(r"[\\/]", path)
    stem = parts[-1].removesuffix(".py") if parts else "?"
    if "app" in parts:
        index = len(parts) - 1 - parts[::-1].index("app")
        dotted = [*parts[index:-1], stem]
        return ".".join(p for p in dotted if p and p != "__init__")
    return stem


def platform_tags() -> dict[str, str]:
    """What kind of machine, said coarsely enough not to identify one."""
    import platform

    tags = {
        "os": platform.system(),
        "os_release": platform.release(),
        "python": sys.version.split()[0],
    }
    try:
        import psutil

        gigabytes = psutil.virtual_memory().total / 2**30
        tags["ram_gb"] = str(
            next(b for b in (8, 12, 16, 24, 32, 48, 64, 128, 10**6) if gigabytes <= b)
        )
    except Exception:  # psutil is a dependency, but a report must never fail on it
        pass
    return tags
