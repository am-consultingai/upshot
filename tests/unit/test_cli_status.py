"""The CLI status cache: a CLI is asked once per few minutes at most, unless asked fresh."""

from __future__ import annotations

from app.llm.cli_status import CliStatusCache


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def counting() -> tuple[list[int], object]:
    asked: list[int] = []

    def probe() -> int:
        asked.append(1)
        return len(asked)

    return asked, probe


def test_an_answer_is_reused_until_it_is_old() -> None:
    clock = Clock()
    cache = CliStatusCache(ttl_s=300.0, clock=clock)
    asked, probe = counting()
    assert cache.get("claude", probe) == 1
    clock.now = 299.0
    assert cache.get("claude", probe) == 1, "a refetch storm must not run the CLI"
    clock.now = 300.0
    assert cache.get("claude", probe) == 2
    assert len(asked) == 2


def test_fresh_asks_again_and_holds_the_new_answer() -> None:
    cache = CliStatusCache(clock=Clock())
    asked, probe = counting()
    cache.get("claude", probe)
    assert cache.get("claude", probe, fresh=True) == 2
    assert cache.get("claude", probe) == 2
    assert len(asked) == 2


def test_forget_drops_one_provider_or_all() -> None:
    cache = CliStatusCache(clock=Clock())
    _asked, probe = counting()
    cache.get("claude", probe)
    cache.get("codex", probe)
    cache.forget("claude")
    assert cache.get("claude", probe) == 3, "forgotten: asked again"
    assert cache.get("codex", probe) == 2, "the other is still held"
    cache.forget()
    assert cache.get("codex", probe) == 4


def test_another_program_is_asked_not_the_answer_for_the_old_one() -> None:
    """Settings can point a provider at another CLI (``llm.codex_cli_path``)."""
    cache = CliStatusCache(clock=Clock())
    _asked, probe = counting()
    assert cache.get("codex", probe, program="codex") == 1
    assert cache.get("codex", probe, program="/opt/codex") == 2
    assert cache.get("codex", probe, program="codex") == 1, "each program its own answer"
    cache.forget("codex")
    assert cache.get("codex", probe, program="/opt/codex") == 3, "forget drops them all"
