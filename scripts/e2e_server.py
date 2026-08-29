"""Launch the app for Playwright: fakes everywhere, a temp data root, seed route on.

MA_E2E_PORT=8123 MA_E2E_SESSION=... uv run python scripts/e2e_server.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import default_config
from app.log import setup
from app.main import create_app
from app.server import LocalServer
from app.services import build


def main() -> int:
    os.environ.setdefault("MA_TEST_MODE", "1")
    home = Path(os.environ.get("MA_HOME") or tempfile.mkdtemp(prefix="ma-e2e-"))
    home.mkdir(parents=True, exist_ok=True)
    os.environ["MA_HOME"] = str(home)
    setup(to_file=False)

    port = int(os.environ.get("MA_E2E_PORT", "8123"))
    config = default_config(
        asr__backend="fake",
        llm__provider="fake",
        audio__capture="synthetic",
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
    services.auth.session_secret = os.environ.get("MA_E2E_SESSION", services.auth.session_secret)
    services.auth.csrf_secret = os.environ.get("MA_E2E_CSRF", services.auth.csrf_secret)
    app = create_app(services)
    print(f"e2e server on http://127.0.0.1:{port} (home={home})", flush=True)
    LocalServer(app, host="127.0.0.1", port=port).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
