"""Fail when Upshot would ship a component under a licence it may not ship.

    uv run python scripts/licence_gate.py [--python] [--javascript] [--binaries]

Run by every CI job (Linux and Windows: the Python packages and their binaries; frontend:
the interface bundle) and by the build. No option means all three.

- ``--python`` / ``--javascript``: every component ``scripts/third_party_notices.py``
  lists, Python and the interface bundle respectively (plus the components no package
  manager installs), checked against ``packaging/licenses/policy.toml``: an allowed
  licence passes; a conditional one passes only for the components it names; anything
  else (GPL, AGPL, a proprietary licence, a licence no metadata declares) fails unless an
  exception approves that component by name.
- ``--binaries``: every library and program the run-time Python packages install, scanned
  as ``scripts/check_dist.py`` scans the freeze, for GPL encoders and a GPL FFmpeg. PyAV
  brought x264 and x265 in through a dependency of a dependency.

Each failure names the package, its licence, and how to see what pulls it in.
"""

from __future__ import annotations

import fnmatch
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_dist  # noqa: E402
import third_party_notices as notices  # noqa: E402

POLICY = ROOT / "packaging" / "licenses" / "policy.toml"


def load_policy(path: Path = POLICY) -> dict:  # type: ignore[type-arg]
    policy = tomllib.loads(path.read_text(encoding="utf-8"))
    for item in policy.get("exception", []):
        missing = {"name", "licence", "why", "approved"} - set(item)
        if missing:
            raise SystemExit(f"{path.name}: exception {item.get('name')!r} lacks {sorted(missing)}")
    return policy


def allowed(licence: str, name: str, policy: dict) -> bool:  # type: ignore[type-arg]
    """Does ``name`` under ``licence`` pass? OR passes when one side does, AND when all do."""
    for item in policy.get("exception", []):
        if item["name"] == name and item["licence"] == licence:
            return True
    expression = licence.strip()
    if expression.startswith("(") and expression.endswith(")"):
        expression = expression[1:-1].strip()
    if " OR " in expression:
        return any(allowed(part, name, policy) for part in expression.split(" OR "))
    if " AND " in expression:
        return all(allowed(part, name, policy) for part in expression.split(" AND "))
    if expression in policy.get("allowed", []):
        return True
    return name in policy.get("conditional", {}).get(expression, [])


def why_here(name: str) -> str:
    return f"see what needs it: uv tree --invert --package {name}" if name else ""


def licence_problems(kinds: set[str], policy: dict) -> list[str]:  # type: ignore[type-arg]
    items = notices.extra_entries() if "other" in kinds else []
    if "python" in kinds:
        items += notices.python_entries()
    if "javascript" in kinds and (ROOT / "frontend" / "node_modules").is_dir():
        items += [notices.js_entry(folder) for folder in notices.js_packages()]
    problems = []
    for item in items:
        if item.licence == "NOASSERTION":  # ffmpeg.exe, which only the build machine has
            print(f"[skip] {item.name}: {item.note.splitlines()[0]}")
            continue
        if not allowed(item.licence, item.name, policy):
            hint = why_here(item.name) if item.kind == "python" else ""
            problems.append(f"{item.name} {item.version}: {item.licence!r} is not allowed. {hint}")
    return problems


def binary_problems() -> list[str]:
    """GPL encoders or a GPL FFmpeg among the files the run-time packages installed."""
    problems = []
    for dist in notices.python_packages():
        for file in dist.files or ():
            path = Path(str(dist.locate_file(file)))
            name = path.name.lower()
            if any(fnmatch.fnmatch(name, pattern) for pattern in check_dist.FORBIDDEN_NAMES):
                problems.append(f"{dist.metadata['Name']}: {file} is a GPL component")
            elif path.suffix.lower() in check_dist.BINARY_SUFFIXES and path.is_file():
                flag = check_dist.gpl_flag(path)
                if flag:
                    problems.append(f"{dist.metadata['Name']}: {file} is built with {flag}")
    return problems


def main(argv: list[str] | None = None) -> int:
    args = set(sys.argv[1:] if argv is None else argv)
    every = not args & {"--python", "--javascript", "--binaries"}
    policy = load_policy()
    kinds = {"other"}
    if every or "--python" in args:
        kinds.add("python")
    if every or "--javascript" in args:
        kinds.add("javascript")
    problems = licence_problems(kinds, policy)
    if every or "--binaries" in args:
        problems += binary_problems()
    for line in problems:
        print(f"[FAIL] {line}")
    if not problems:
        print(f"[ok  ] licences: {', '.join(sorted(kinds))}; binaries: "
              f"{'scanned' if every or '--binaries' in args else 'not asked'}")  # fmt: skip
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
