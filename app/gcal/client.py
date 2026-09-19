"""Which Google OAuth client this build uses.

The client is a *Desktop app* client, and it ships inside the application: ID and secret
both. For an installed app that is what Google expects — the secret "is obviously not
treated as a secret" — and it is PKCE plus the loopback redirect that protect the flow.

It is still kept out of git, because the repository is public: a client posted there is
picked up by leak scanners and can be disabled, which would break every installation at
once. So the JSON Google hands out lives beside this module as an ignored file, and the
PyInstaller spec copies it into the bundle when it exists. A fallback client for the day
this one is blocked is backlog task z8tj1h900g.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from app import paths

#: The file name Google's console download is renamed to, wherever it is looked for.
CLIENT_FILE = "google_oauth_client.json"

#: Points at a client JSON anywhere on disk. Wins over every other location.
CLIENT_ENV = "UP_GOOGLE_CLIENT"


@dataclass(frozen=True)
class OAuthClient:
    client_id: str
    client_secret: str
    source: Path


def candidates() -> list[Path]:
    """Where the client is looked for, in order."""
    found: list[Path] = []
    env = os.environ.get(CLIENT_ENV)
    if env:
        found.append(Path(env).expanduser())
    found.append(paths.resource("app", "gcal", CLIENT_FILE))  # baked into this build
    found.append(paths.app_home() / CLIENT_FILE)
    found.append(Path.home() / ".config" / "upshot" / CLIENT_FILE)
    return found


def parse(path: Path) -> OAuthClient:
    """Read the JSON exactly as Google's console downloads it."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    # A Desktop client downloads under "installed". A Web client ("web") would need a
    # registered redirect URI for every port, which a loopback flow cannot give it.
    body = payload.get("installed")
    if not isinstance(body, dict):
        kinds = ", ".join(sorted(payload)) or "nothing"
        raise ValueError(f"{path.name} is not a Desktop app client (it holds {kinds})")
    client_id = str(body.get("client_id") or "")
    client_secret = str(body.get("client_secret") or "")
    if not client_id or not client_secret:
        raise ValueError(f"{path.name} has no client_id or client_secret")
    return OAuthClient(client_id=client_id, client_secret=client_secret, source=path)


def load() -> OAuthClient | None:
    """The first usable client, or None when this build has none."""
    for path in candidates():
        if path.is_file():
            return parse(path)
    return None
