"""Render static HTML mocks through a real Windows browser and save PNGs.

    python3 scripts/windows/mockshot.py <mock-dir> [--out artifacts/mocks] [--width 1360]

Design mocks are HTML so they can use the real tokens and the real bundled fonts,
rather than being drawn in a tool that does not share either. They are served over
loopback rather than opened as file:// URLs, because a file:// page cannot load the
font files without tripping the browser's local-file rules — and a mock that
silently falls back to Arial is a mock of the wrong design.

Nothing here touches the application: its own port, its own browser profile, and
it serves a directory of flat files.
"""

from __future__ import annotations

import argparse
import http.server
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "windows"))

from e2e import free_port, kill_windows, to_wsl, wait_for, windows_paths  # noqa: E402


def serve(directory: Path, port: int) -> socketserver.TCPServer:
    handler = type(
        "Quiet",
        (http.server.SimpleHTTPRequestHandler,),
        {
            "log_message": lambda *_: None,
            "directory": str(directory),
            "__init__": lambda self, *a, **k: http.server.SimpleHTTPRequestHandler.__init__(
                self, *a, directory=str(directory), **k
            ),
        },
    )
    server = socketserver.TCPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mocks", help="directory of .html mocks")
    parser.add_argument("--out", default="artifacts/mocks")
    parser.add_argument("--width", type=int, default=1360)
    parser.add_argument("--height", type=int, default=1000)
    args = parser.parse_args(argv)

    mocks = Path(args.mocks).resolve()
    pages = sorted(mocks.glob("*.html"))
    if not pages:
        print(f"no .html files in {mocks}")
        return 2

    out = (ROOT / args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    where = windows_paths()
    if not where.get("chrome"):
        print("no Chromium browser found on the Windows side")
        return 2
    browser_exe = to_wsl(where["chrome"])

    port = free_port(8320)
    cdp = free_port(9540)
    profile = f"{where['temp']}\\ma-mock-{cdp}"
    server = serve(mocks, port)

    browser = subprocess.Popen(
        [str(browser_exe), "--headless=new", f"--remote-debugging-port={cdp}",
         f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
         "--force-device-scale-factor=2"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        if not wait_for(f"http://127.0.0.1:{cdp}/json/version", 60):
            print("no CDP endpoint")
            return 1
        script = ROOT / "frontend" / "__mockshot.cjs"
        script.write_text(
            """
const { chromium } = require("playwright");
(async () => {
  const [cdp, base, out, width, height, ...names] = process.argv.slice(2);
  const browser = await chromium.connectOverCDP(cdp);
  const context = browser.contexts()[0] ?? (await browser.newContext());
  const page = await context.newPage();
  await page.setViewportSize({ width: Number(width), height: Number(height) });
  for (const name of names) {
    await page.goto(`${base}/${name}`, { waitUntil: "networkidle" });
    await page.evaluate(async () => { await document.fonts.ready; });
    await page.waitForTimeout(400);
    await page.screenshot({ path: `${out}/${name.replace(/\\.html$/, "")}.png`, fullPage: true });
    console.log("  " + name);
  }
  await page.close();
  await browser.close();
})().catch((e) => { console.error("ERR", e.message); process.exit(1); });
""",
            encoding="utf-8",
        )
        run = subprocess.run(
            ["node", str(script.name), f"http://127.0.0.1:{cdp}",
             f"http://127.0.0.1:{port}", str(out), str(args.width), str(args.height),
             *[p.name for p in pages]],
            cwd=ROOT / "frontend", check=False,
        )
        script.unlink(missing_ok=True)
        if run.returncode != 0:
            return run.returncode
    finally:
        server.shutdown()
        browser.terminate()
        kill_windows(f"ma-mock-{cdp}")
        time.sleep(0.5)
    print(f"written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
