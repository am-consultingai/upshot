"""Toast buttons, carried to the running app as ``upshot:`` links (D70).

A button on a Windows notification can launch a URL. The installer registers the
``upshot:`` scheme for the signed-in user, so pressing "Start recording" runs
``upshot.exe "upshot:recording.start?calendar=...&event=..."``. That short-lived process
hands the action to the running instance over its local API, with the launcher key it left
in the app home, and exits. It opens no window, so the user's focus stays where it was;
only "open" actions (clicking the notification itself, "Open") show Upshot's window.

A link is used rather than the toast's own activation callback because the toast process
exits as soon as the toast is shown, and a notification can be pressed much later, from
the Action Center.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from app.log import get

log = get(__name__)

SCHEME = "upshot"
#: Actions the running instance performs (``POST /api/launcher/action``).
REMOTE = frozenset({"recording.start", "recording.stop", "meeting.discard"})
#: Actions that show a page of the app; the launched process does these itself, since
#: Windows lets it, and not the tray process, bring a window to the front.
PAGES = {"open": "/", "meeting.open": "/m/{meeting}", "meeting.email": "/m/{meeting}"}
#: Query keys a link may carry, and the request fields they become.
FIELDS = {"meeting": "meeting_id", "calendar": "calendar_id", "event": "event_id"}


@dataclass(frozen=True)
class Link:
    action: str
    params: dict[str, str] = field(default_factory=dict)


def url(action: str, **params: str | None) -> str:
    """``upshot:recording.start?calendar=primary&event=abc``; empty values are left out."""
    query = urllib.parse.urlencode({key: value for key, value in params.items() if value})
    return f"{SCHEME}:{action}" + (f"?{query}" if query else "")


def parse(link: str) -> Link | None:
    """The action a link names, or None when it is not one of ours."""
    scheme, _, rest = link.strip().partition(":")
    if scheme.lower() != SCHEME or not rest:
        return None
    action, _, query = rest.lstrip("/").partition("?")
    action = action.rstrip("/")
    if action not in REMOTE and action not in PAGES:
        return None
    params = {
        key: values[0]
        for key, values in urllib.parse.parse_qs(query).items()
        if key in FIELDS and values
    }
    return Link(action, params)


def link_argument(arguments: list[str]) -> str | None:
    """The ``upshot:`` link Windows passed on the command line, if any."""
    for argument in arguments:
        if argument.lower().startswith(f"{SCHEME}:"):
            return argument
    return None


def page(link: Link) -> str:
    template = PAGES.get(link.action, "/")
    meeting = link.params.get("meeting", "")
    return template.format(meeting=urllib.parse.quote(meeting)) if meeting else "/"


def forward(link: Link, port: int, home: Path) -> bool:
    """Hand a remote action to the running instance. False when it refused or is gone."""
    from app.api.security import ACTION_PATH, LAUNCHER_HEADER, LAUNCHER_KEY_FILE

    body = {"action": link.action} | {FIELDS[key]: value for key, value in link.params.items()}
    try:
        key = (home / LAUNCHER_KEY_FILE).read_text(encoding="utf-8").strip()
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}{ACTION_PATH}",
            data=json.dumps(body).encode(),
            method="POST",
            headers={LAUNCHER_HEADER: key, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            log.info("toast action %s: %s", link.action, response.status)
            return True
    except Exception as exc:
        log.warning("toast action %s was not carried out: %s", link.action, exc)
        return False
