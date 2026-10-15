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
import re
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


class BadExpression(ValueError):
    """A licence string that is not a well-formed SPDX expression."""


def parse(licence: str) -> object:
    """An SPDX expression as a tree: a str (an identifier, "X WITH Y" kept whole) or
    ("OR"|"AND", [children]). OR binds loosest, then AND; parentheses group. Operators are
    case-insensitive, as the SPDX spec says; anything malformed raises BadExpression."""
    tokens = [t for t in re.split(r"(\(|\)|\s+)", licence) if t and not t.isspace()]
    position = 0

    def peek() -> str | None:
        return tokens[position] if position < len(tokens) else None

    def take() -> str:
        nonlocal position
        if position >= len(tokens):
            raise BadExpression(f"{licence!r} ends early")
        position += 1
        return tokens[position - 1]

    def expression(operator: str = "OR") -> object:
        inner = "AND" if operator == "OR" else None
        parts = [expression(inner) if inner else term()]
        while (peek() or "").upper() == operator:
            take()
            parts.append(expression(inner) if inner else term())
        return parts[0] if len(parts) == 1 else (operator, parts)

    def term() -> object:
        token = take()
        if token == "(":
            inside = expression()
            if take() != ")":
                raise BadExpression(f"{licence!r}: unbalanced parenthesis")
            return inside
        if token == ")" or token.upper() in ("AND", "OR", "WITH"):
            raise BadExpression(f"{licence!r}: unexpected {token!r}")
        if (peek() or "").upper() == "WITH":
            take()
            exception = take()
            if exception in ("(", ")") or exception.upper() in ("AND", "OR", "WITH"):
                raise BadExpression(f"{licence!r}: WITH needs an exception")
            return f"{token} WITH {exception}"
        return token

    tree = expression()
    if position != len(tokens):
        raise BadExpression(f"{licence!r}: unexpected {tokens[position]!r}")
    return tree


def allowed(licence: str, name: str, policy: dict) -> bool:  # type: ignore[type-arg]
    """Does ``name`` under ``licence`` pass? OR passes when one side does, AND when all do;
    a string that is not an SPDX expression (or names no licence) does not pass."""
    for item in policy.get("exception", []):
        if item["name"] == name and item["licence"] == licence:
            return True
    try:
        tree = parse(licence)
    except BadExpression:
        return False
    permitted = {i.casefold() for i in policy.get("allowed", [])}
    conditional = {k.casefold(): v for k, v in policy.get("conditional", {}).items()}

    def passes(node: object) -> bool:
        if isinstance(node, tuple):
            operator, children = node
            return (any if operator == "OR" else all)(passes(c) for c in children)
        key = str(node).casefold()
        return key in permitted or name in conditional.get(key, [])

    return passes(tree)


def why_here(name: str) -> str:
    return f"see what needs it: uv tree --invert --package {name}" if name else ""


#: The one component whose licence is unknowable without its binary: ffmpeg.exe, which only
#: the build machine has (the build's --strict notices refuse it there).
NEEDS_ITS_BINARY = "FFmpeg (BtbN FFmpeg-Builds, LGPL Windows build)"


def licence_problems(kinds: set[str], policy: dict) -> list[str]:  # type: ignore[type-arg]
    items = notices.extra_entries() if "other" in kinds else []
    problems = []
    if "python" in kinds:
        items += notices.python_entries()
    if "javascript" in kinds:
        if not (ROOT / "frontend" / "node_modules").is_dir():
            problems.append("frontend/node_modules is missing: run npm ci (and npm run build)")
        else:
            notices.MISSING.clear()
            items += [notices.js_entry(folder) for folder in notices.js_packages()]
            problems += [f"{missing}: not checked" for missing in notices.MISSING]
    for item in items:
        if item.name == NEEDS_ITS_BINARY and item.licence == "NOASSERTION":
            print(f"[skip] {item.name}: {(item.note.splitlines() or ['no binary'])[0]}")
            continue
        if not allowed(item.licence, item.name, policy):
            hint = why_here(item.name) if item.kind == "python" else ""
            problems.append(f"{item.name} {item.version}: {item.licence!r} is not allowed. {hint}")
    return problems


#: Contents are read from these, on every platform the gate runs on.
SCANNED_SUFFIXES = check_dist.BINARY_SUFFIXES | {".so", ".dylib"}


def binary_problems() -> list[str]:
    """GPL encoders or a GPL FFmpeg among the files the run-time packages installed, by
    file or folder name and by content."""
    problems = []
    for dist in notices.python_packages():
        if dist.files is None:
            problems.append(f"{dist.metadata['Name']}: no RECORD, so its files cannot be scanned")
            continue
        for file in dist.files:
            path = Path(str(dist.locate_file(file)))
            names = [part.lower() for part in Path(str(file)).parts]
            if any(fnmatch.fnmatch(n, p) for n in names for p in check_dist.FORBIDDEN_NAMES):
                problems.append(f"{dist.metadata['Name']}: {file} is a GPL component")
            elif (
                path.suffix.lower() in SCANNED_SUFFIXES or ".so." in path.name
            ) and path.is_file():
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
