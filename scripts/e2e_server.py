"""Launch the app for Playwright: fakes everywhere, a temp data root, seed route on.

UP_E2E_PORT=8123 UP_E2E_SESSION=... uv run python scripts/e2e_server.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import default_config
from app.gcal.client import CLIENT_ENV
from app.log import setup
from app.main import create_app
from app.server import LocalServer
from app.services import build


def main() -> int:
    os.environ.setdefault("UP_TEST_MODE", "1")
    home = Path(os.environ.get("UP_HOME") or tempfile.mkdtemp(prefix="ma-e2e-"))
    home.mkdir(parents=True, exist_ok=True)
    os.environ["UP_HOME"] = str(home)
    # The specs assume a build that can connect a calendar. The real client file is
    # gitignored, so a fresh clone has none (job 007 on machine B): give every run the
    # same stand-in, which no spec ever sends to Google.
    if not os.environ.get(CLIENT_ENV):
        stand_in = home / "e2e_google_oauth_client.json"
        stand_in.write_text(
            json.dumps({"installed": {"client_id": "e2e-client", "client_secret": "e2e-secret"}}),
            encoding="utf-8",
        )
        os.environ[CLIENT_ENV] = str(stand_in)
    setup(to_file=False)

    # argv wins over the environment, and the harness always passes it. Not for
    # configuration — the env var did that fine — but so the port appears on the
    # *command line*, which is the only handle WSL has for killing this process on
    # the Windows side. See scripts/windows/e2e.py.
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("UP_E2E_PORT", "8123"))
    config = default_config(
        asr__backend="fake",
        llm__provider="fake",
        audio__capture="synthetic",
        # A tone rather than the default silence, so the Settings meter has something to
        # show — and so a meter that stays dead in e2e is a real failure.
        audio__synthetic_pattern="tone",
        audio__vad="energy",
        delivery__notifier="fake",
        secrets__backend="memory",
        job_policy="asap",
    )
    config.set("data_root", str(home / "meetings"))
    config.set("server.port", port)
    # Belt and braces: an accidental provider call in an e2e run must fail locally
    # rather than leave the machine. Discovered when the Test button reached Google.
    config.set("llm.gemini_base_url", "http://127.0.0.1:9")
    config.set("llm.openai_base_url", "http://127.0.0.1:9")
    config.set("llm.ollama_url", "http://127.0.0.1:9")

    services = build(config, with_worker=True, with_recorder=True)
    services.auth.session_secret = os.environ.get("UP_E2E_SESSION", services.auth.session_secret)
    services.auth.csrf_secret = os.environ.get("UP_E2E_CSRF", services.auth.csrf_secret)
    app = create_app(services)
    print(f"e2e server on http://127.0.0.1:{port} (home={home})", flush=True)
    LocalServer(app, host="127.0.0.1", port=port).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
