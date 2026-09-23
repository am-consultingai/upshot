"""The Drive side of the A/B test loop: list, read and write under projects/upshot.

Both machines use it. It talks to the Drive REST API with the OAuth token at
~/.config/gcloud/drive_sheets_token.json (override with UPSHOT_DRIVE_TOKEN),
standard library only, so it runs on any python3.

Paths are relative to the shared folder, e.g. "jobs/004-run-from-source/job.md".

    python3 drive.py ls [path]
    python3 drive.py get <path> [local-file]      (stdout when no file is given)
    python3 drive.py pull <path> <local-dir>      (a folder, recursively)
    python3 drive.py put <local-file> <path>      (creates or replaces; makes folders)
    python3 drive.py push <local-dir> <path>      (a folder's files, recursively)
    python3 drive.py exists <path>                (exit code 0 or 1)
    python3 drive.py mv <path> <folder-path>
"""

from __future__ import annotations

import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = "1I5zKC-RIi1RC4ga9NI_r8mUFg4rmh_w8"  # My Drive/projects/upshot
FOLDER = "application/vnd.google-apps.folder"
API = "https://www.googleapis.com/drive/v3/files"
TOKEN_FILE = Path(
    os.environ.get("UPSHOT_DRIVE_TOKEN", "~/.config/gcloud/drive_sheets_token.json")
).expanduser()

_access: tuple[str, float] | None = None


def _token() -> str:
    global _access
    if _access and _access[1] > time.time() + 60:
        return _access[0]
    tok = json.loads(TOKEN_FILE.read_text())
    data = urllib.parse.urlencode(
        {
            "client_id": tok["client_id"],
            "client_secret": tok["client_secret"],
            "refresh_token": tok["refresh_token"],
            "grant_type": "refresh_token",
        }
    ).encode()
    got = json.load(urllib.request.urlopen("https://oauth2.googleapis.com/token", data, timeout=60))
    _access = (got["access_token"], time.time() + int(got.get("expires_in", 3000)))
    return _access[0]


def _req(url: str, method: str = "GET", data: bytes | None = None, headers: dict | None = None):
    for attempt in range(5):
        h = {"Authorization": "Bearer " + _token(), **(headers or {})}
        try:
            return urllib.request.urlopen(
                urllib.request.Request(url, method=method, data=data, headers=h), timeout=300
            )
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 4:
                time.sleep(2**attempt * 5)
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if attempt < 4:
                time.sleep(2**attempt * 5)
                continue
            raise
    raise RuntimeError("unreachable")


def children(folder_id: str) -> list[dict]:
    out: list[dict] = []
    page = None
    while True:
        q = {
            "q": f"'{folder_id}' in parents and trashed = false",
            "fields": "nextPageToken, files(id,name,mimeType,modifiedTime,size)",
            "pageSize": 1000,
            "orderBy": "name",
        }
        if page:
            q["pageToken"] = page
        got = json.load(_req(API + "?" + urllib.parse.urlencode(q)))
        out += got["files"]
        page = got.get("nextPageToken")
        if not page:
            return out


def _parts(path: str) -> list[str]:
    return [p for p in path.strip("/").split("/") if p]


def resolve(path: str) -> dict | None:
    node = {"id": ROOT, "name": "", "mimeType": FOLDER}
    for name in _parts(path):
        match = [c for c in children(node["id"]) if c["name"] == name]
        if not match:
            return None
        node = match[0]
    return node


def makedirs(path: str) -> str:
    fid = ROOT
    for name in _parts(path):
        match = [c for c in children(fid) if c["name"] == name and c["mimeType"] == FOLDER]
        if match:
            fid = match[0]["id"]
            continue
        body = json.dumps({"name": name, "parents": [fid], "mimeType": FOLDER}).encode()
        fid = json.load(_req(API, "POST", body, {"Content-Type": "application/json"}))["id"]
    return fid


def ls(path: str = "") -> list[dict]:
    node = resolve(path)
    if node is None:
        return []
    return children(node["id"])


def read_bytes(path: str) -> bytes | None:
    node = resolve(path)
    if node is None or node["mimeType"] == FOLDER:
        return None
    if node["mimeType"].startswith("application/vnd.google-apps"):
        return _req(f"{API}/{node['id']}/export?mimeType=text/plain").read()
    return _req(f"{API}/{node['id']}?alt=media").read()


def read_text(path: str) -> str | None:
    got = read_bytes(path)
    return None if got is None else got.decode("utf-8", "replace")


def write_bytes(path: str, content: bytes, mime: str | None = None) -> str:
    *dirs, name = _parts(path)
    parent = makedirs("/".join(dirs))
    mime = mime or mimetypes.guess_type(name)[0] or "application/octet-stream"
    if name.endswith((".md", ".log", ".jsonl", ".ps1", ".sh", ".wsb")):
        mime = "text/plain"
    existing = [c for c in children(parent) if c["name"] == name and c["mimeType"] != FOLDER]
    if existing:
        url = (
            f"https://www.googleapis.com/upload/drive/v3/files/{existing[0]['id']}?uploadType=media"
        )
        _req(url, "PATCH", content, {"Content-Type": mime})
        return existing[0]["id"]
    boundary = uuid.uuid4().hex
    meta = json.dumps({"name": name, "parents": [parent]})
    body = (
        (
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{meta}\r\n"
            f"--{boundary}\r\nContent-Type: {mime}\r\n\r\n"
        ).encode()
        + content
        + f"\r\n--{boundary}--".encode()
    )
    got = _req(
        "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id",
        "POST",
        body,
        {"Content-Type": f"multipart/related; boundary={boundary}"},
    )
    return json.load(got)["id"]


def write_text(path: str, text: str) -> str:
    return write_bytes(path, text.encode("utf-8"))


def write_json(path: str, value: object) -> str:
    return write_bytes(path, json.dumps(value, indent=2).encode("utf-8"), "application/json")


def pull(path: str, local: Path) -> None:
    local.mkdir(parents=True, exist_ok=True)
    for c in ls(path):
        sub = f"{path.strip('/')}/{c['name']}"
        if c["mimeType"] == FOLDER:
            pull(sub, local / c["name"])
        else:
            (local / c["name"]).write_bytes(read_bytes(sub) or b"")


def push(local: Path, path: str) -> None:
    for f in sorted(local.rglob("*")):
        if f.is_file():
            write_bytes(f"{path.strip('/')}/{f.relative_to(local).as_posix()}", f.read_bytes())


def move(path: str, folder: str) -> None:
    node = resolve(path)
    if node is None:
        raise SystemExit(f"not found: {path}")
    dest = makedirs(folder)
    old = resolve("/".join(_parts(path)[:-1]))
    q = urllib.parse.urlencode({"addParents": dest, "removeParents": old["id"] if old else ROOT})
    _req(f"{API}/{node['id']}?{q}", "PATCH", b"{}", {"Content-Type": "application/json"})


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cmd, args = argv[0], argv[1:]
    if cmd == "ls":
        for c in ls(args[0] if args else ""):
            kind = "/" if c["mimeType"] == FOLDER else ""
            print(f"{c['modifiedTime']}  {c.get('size', '-'):>9}  {c['name']}{kind}")
    elif cmd == "get":
        got = read_bytes(args[0])
        if got is None:
            print(f"not found: {args[0]}", file=sys.stderr)
            return 1
        if len(args) > 1:
            Path(args[1]).write_bytes(got)
        else:
            sys.stdout.buffer.write(got)
    elif cmd == "pull":
        pull(args[0], Path(args[1]))
    elif cmd == "put":
        write_bytes(args[1], Path(args[0]).read_bytes())
    elif cmd == "push":
        push(Path(args[0]), args[1])
    elif cmd == "exists":
        return 0 if resolve(args[0]) else 1
    elif cmd == "mv":
        move(args[0], args[1])
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
