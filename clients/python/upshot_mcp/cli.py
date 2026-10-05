"""``upshot-mcp``: an MCP server over stdio, or ``upshot-mcp transcribe FILE`` for scripts."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from upshot_mcp import __version__, paths
from upshot_mcp.protocol import Call, Server, ToolError
from upshot_mcp.tools import EXTENSIONS, INSTRUCTIONS, Bridge, tools
from upshot_mcp.upshot import NotRunning, UpshotError, discover


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="upshot-mcp", description=__doc__)
    parser.add_argument("--url", help="Upshot's address, if it cannot be found by itself")
    parser.add_argument("--wsl-distro", help="set when launched from WSL: the distribution's name")
    parser.add_argument("--version", action="version", version=f"upshot-mcp {__version__}")
    sub = parser.add_subparsers(dest="command")
    cli = sub.add_parser("transcribe", help="transcribe one file and print or save the result")
    cli.add_argument("file")
    cli.add_argument("--format", default="text", choices=["text", "txt", "srt", "vtt", "json"])
    cli.add_argument("-o", "--output", help="file or folder to write the result to")
    cli.add_argument("--language", default="auto")
    cli.add_argument("--prompt", default="")
    cli.add_argument("--no-diarize", action="store_true")
    cli.add_argument("--json", action="store_true", help="print the finished job as JSON")
    args = parser.parse_args(argv)

    if args.command == "transcribe":
        return _transcribe(args)
    # Text mode with Windows newlines is what a client reads line by line just as well, but
    # a raw UTF-8 stream is what the protocol says.
    sys.stdin.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")  # type: ignore[attr-defined]
    bridge = Bridge(url=args.url, wsl_distro=args.wsl_distro)
    Server("upshot-transcribe", __version__, INSTRUCTIONS, tools(bridge)).serve()
    return 0


def _transcribe(args: argparse.Namespace) -> int:
    """Waits as long as it takes, printing progress to stderr; Ctrl+C cancels the job."""
    import threading

    bridge = Bridge(url=args.url, wsl_distro=args.wsl_distro)
    fmt = "text" if args.format == "txt" else args.format
    options = {"language": args.language, "diarize": not args.no_diarize, "prompt": args.prompt}
    try:
        app = bridge.upshot()
        job = bridge._submit(app, args.file, options)
        call = Call({}, lambda *_: None, threading.Event())
        try:
            while job["state"] in ("pending", "running"):
                job = bridge._wait(app, job, 30, call)
                percent = round(float(job.get("progress") or 0) * 100)
                print(f"{job['source_name']}: {job['state']} {percent}%", file=sys.stderr)
        except KeyboardInterrupt:
            app.cancel(job["id"])
            print("cancelled", file=sys.stderr)
            return 130
        if args.output:
            message = bridge._save(app, job, fmt, args.output)
            saved: Any = message
        else:
            saved = None
            if not args.json:
                text = (
                    bridge._answer(app, job, fmt, "", 0, 0)
                    if fmt != "text"
                    else app.result(job["id"], "txt", timestamps="true")
                )
                sys.stdout.write(text if text.endswith("\n") else text + "\n")
        if args.json:
            print(json.dumps({"job": job, "saved": saved,
                              "extension": EXTENSIONS[fmt]}, ensure_ascii=False))  # fmt: skip
        elif saved:
            print(saved, file=sys.stderr)
        return 0 if job["state"] == "done" else 1
    except (ToolError, NotRunning, UpshotError, paths.PathError) as exc:
        print(f"upshot-mcp: {exc}", file=sys.stderr)
        return 2


__all__ = ["discover", "main"]
