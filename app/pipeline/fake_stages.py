"""Test-only stages (EXECUTION-PLAN.md Phase 2).

They exist so the queue, the retry ladder, preemption and crash recovery can be proven
before a single real stage exists. Selected by handing the worker this registry.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from app.errors import PermanentError, Preempted
from app.pipeline.artifacts import up_to_date
from app.pipeline.context import StageContext


@dataclass
class FakeStages:
    """A registry of deliberately misbehaving stages plus a side-effect counter."""

    fail_times: int = 3
    yield_after: int = 1
    units: int = 5
    calls: Counter[str] = field(default_factory=Counter)
    work_done: Counter[str] = field(default_factory=Counter)

    def registry(self) -> dict[str, object]:
        return {
            "slow": self.slow,
            "flaky": self.flaky,
            "boom": self.boom,
            "preemptible": self.preemptible,
            "idempotent": self.idempotent,
        }

    # -- stages

    def slow(self, ctx: StageContext) -> None:
        self.calls["slow"] += 1
        for _ in range(self.units):
            ctx.checkpoint()
            ctx.clock.sleep(0.01)
            self.work_done["slow"] += 1

    def flaky(self, ctx: StageContext) -> None:
        self.calls["flaky"] += 1
        if self.calls["flaky"] <= self.fail_times:
            raise RuntimeError(f"flaky failure #{self.calls['flaky']}")
        self.work_done["flaky"] += 1

    def boom(self, ctx: StageContext) -> None:
        self.calls["boom"] += 1
        raise PermanentError("boom is never recoverable", category="test")

    def preemptible(self, ctx: StageContext) -> None:
        self.calls["preemptible"] += 1
        for index in range(self.units):
            if index >= self.yield_after and ctx.should_yield():
                raise Preempted("yielding between units")
            self.work_done["preemptible"] += 1

    def idempotent(self, ctx: StageContext) -> None:
        """Writes ``done.txt`` once; a second run must do no work."""
        self.calls["idempotent"] += 1
        source = ctx.folder / "input.txt"
        output = ctx.folder / "done.txt"
        if up_to_date(output, [source]):
            return
        ctx.folder.mkdir(parents=True, exist_ok=True)
        output.write_text("done", encoding="utf-8")
        self.work_done["idempotent"] += 1
