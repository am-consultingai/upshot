"""The agent's primary feedback loop.

    python -m app.selftest all --report out.json

Every subcommand writes the same machine-readable report shape and exits non-zero on
any failure::

    {"suite": str, "ok": bool, "checks": [{"name": str, "ok": bool,
                                           "detail": str, "metrics": {}}]}

A check that cannot run in this environment is reported ``ok`` with a detail starting
``skipped:`` and ``metrics["skipped"] = 1`` — never silently dropped.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail, "metrics": self.metrics}


def skipped(name: str, why: str, **metrics: Any) -> Check:
    return Check(name, True, f"skipped: {why}", {"skipped": 1, **metrics})


SuiteFn = Callable[[argparse.Namespace], list[Check]]
REGISTRY: dict[str, SuiteFn] = {}
IN_ALL: list[str] = []


def suite(name: str, *, in_all: bool = True) -> Callable[[SuiteFn], SuiteFn]:
    def deco(fn: SuiteFn) -> SuiteFn:
        REGISTRY[name] = fn
        if in_all:
            IN_ALL.append(name)
        return fn

    return deco


# --------------------------------------------------------------------------- suites


@suite("imports")
def _imports(args: argparse.Namespace) -> list[Check]:
    from app.introspect import import_all_modules

    names, failures = import_all_modules()
    checks = [
        Check(
            "import_all_modules",
            not failures,
            f"{len(names)} modules imported" if not failures else "; ".join(failures),
            {"modules": len(names)},
        )
    ]
    return checks


@suite("clock")
def _clock(args: argparse.Namespace) -> list[Check]:
    from app.clock import FakeClock, SystemClock, iso

    fake = FakeClock()
    before = fake.now()
    fake.advance(5)
    delta = (fake.now() - before).total_seconds()
    real = SystemClock().now()
    return [
        Check("fake_clock_advances", delta == 5.0, f"advance(5) moved now() by {delta}s"),
        Check("system_clock_is_aware", real.tzinfo is not None, iso(real)),
    ]


# Importing these modules registers their suites. Kept at the bottom so a suite may
# import from anything above it.
def _load_suites() -> None:
    from app import selftest_suites  # noqa: F401


# --------------------------------------------------------------------------- runner


def run_suite(name: str, args: argparse.Namespace) -> list[Check]:
    fn = REGISTRY.get(name)
    if fn is None:
        return [Check(name, False, f"unknown suite {name!r}")]
    try:
        return list(fn(args))
    except Exception as exc:  # a suite must never take the runner down
        return [Check(name, False, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")]


def run(name: str, args: argparse.Namespace) -> dict[str, Any]:
    _load_suites()
    names = list(IN_ALL) if name == "all" else [name]
    checks: list[Check] = []
    for suite_name in names:
        checks.extend(run_suite(suite_name, args))
    return {
        "suite": name,
        "ok": all(c.ok for c in checks),
        "checks": [c.as_dict() for c in checks],
    }


def main(argv: list[str] | None = None) -> int:
    _load_suites()
    parser = argparse.ArgumentParser(prog="python -m app.selftest")
    parser.add_argument("suite", choices=["all", *sorted(REGISTRY)])
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--input", type=Path, default=None)
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--budget-s", type=float, default=120.0, dest="budget_s")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    report = run(args.suite, args)
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text, encoding="utf-8")
    if not args.quiet:
        for check in report["checks"]:
            mark = "ok  " if check["ok"] else "FAIL"
            print(f"[{mark}] {check['name']}: {check['detail']}")
        print(f"{report['suite']}: {'OK' if report['ok'] else 'FAILED'}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":  # pragma: no cover - process entry point
    # Re-enter through the package so suites registering against ``app.selftest``
    # populate the same registry this process dispatches from.
    from app.selftest import main as _main

    sys.exit(_main())
