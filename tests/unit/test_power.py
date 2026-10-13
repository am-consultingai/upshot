"""Keep awake: held while a meeting records or a job runs, released after, never flapping."""

from __future__ import annotations

from types import SimpleNamespace

from app.power import KeepAwake, NoPowerRequest, busy_reason


class Request:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.calls: list[str] = []

    def hold(self, reason: str) -> bool:
        self.calls.append(f"hold:{reason}")
        return self.ok

    def release(self) -> None:
        self.calls.append("release")


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def keeper(
    reason: list[str | None], request: Request, clock: Clock, enabled: list[bool] | None = None
) -> KeepAwake:
    switch = enabled if enabled is not None else [True]
    return KeepAwake(
        lambda: reason[0],
        request=request,
        enabled=lambda: switch[0],
        linger_s=120.0,
        monotonic=clock,
    )


def test_held_while_busy_and_released_after_the_linger() -> None:
    reason: list[str | None] = [None]
    request, clock = Request(), Clock()
    keep = keeper(reason, request, clock)
    keep.tick()
    assert request.calls == [], "nothing running: nothing asked"
    reason[0] = "Upshot is transcribing and summarizing"
    keep.tick()
    keep.tick()
    assert request.calls == ["hold:Upshot is transcribing and summarizing"], "asked once"
    reason[0] = None
    clock.now = 60.0
    keep.tick()
    assert keep.held is not None, "a gap between two stages is not the end"
    clock.now = 120.0
    keep.tick()
    assert request.calls[-1] == "release"
    assert keep.held is None


def test_a_new_reason_is_a_new_request() -> None:
    reason: list[str | None] = ["Upshot is recording a meeting"]
    request, clock = Request(), Clock()
    keep = keeper(reason, request, clock)
    keep.tick()
    reason[0] = "Upshot is transcribing and summarizing"
    keep.tick()
    assert request.calls == [
        "hold:Upshot is recording a meeting",
        "hold:Upshot is transcribing and summarizing",
    ]


def test_turned_off_releases_at_once() -> None:
    reason: list[str | None] = ["Upshot is recording a meeting"]
    enabled = [True]
    request, clock = Request(), Clock()
    keep = keeper(reason, request, clock, enabled)
    keep.tick()
    enabled[0] = False
    keep.tick()
    assert request.calls == ["hold:Upshot is recording a meeting", "release"]


def test_a_refused_request_is_not_asked_again_every_tick() -> None:
    reason: list[str | None] = ["Upshot is recording a meeting"]
    request, clock = Request(ok=False), Clock()
    keep = keeper(reason, request, clock)
    for _ in range(3):
        keep.tick()
    assert request.calls == ["hold:Upshot is recording a meeting"]
    assert keep.held is None


def test_stop_releases() -> None:
    request, clock = Request(), Clock()
    keep = keeper(["Upshot is recording a meeting"], request, clock)
    keep.tick()
    keep.stop()
    assert request.calls[-1] == "release"


def test_the_reasons() -> None:
    idle = SimpleNamespace(
        recorder=SimpleNamespace(committed=False), worker=SimpleNamespace(busy=False)
    )
    assert busy_reason(idle) is None
    recording = SimpleNamespace(
        recorder=SimpleNamespace(committed=True), worker=SimpleNamespace(busy=False)
    )
    assert busy_reason(recording) == "Upshot is recording a meeting"
    working = SimpleNamespace(
        recorder=SimpleNamespace(committed=False), worker=SimpleNamespace(busy=True)
    )
    assert busy_reason(working) == "Upshot is transcribing and summarizing"
    assert busy_reason(SimpleNamespace()) is None, "headless or in tests: nothing to hold"


def test_off_windows_nothing_is_asked() -> None:
    assert NoPowerRequest().hold("x") is False
