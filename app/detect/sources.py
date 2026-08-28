"""Signal sources sit behind protocols, so the state machine is driven by injection."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class MicHolder:
    process: str
    since_ms: int = 0
    packaged: bool = False


class MicSource(Protocol):
    """Who holds the microphone right now (the ConsentStore)."""

    def current_holders(self) -> list[MicHolder]: ...


class SessionSource(Protocol):
    """Which processes are producing audio (pycaw render sessions)."""

    def render_processes(self) -> list[str]: ...


class TitleSource(Protocol):
    def titles(self) -> list[str]: ...


class CameraSource(Protocol):
    def in_use(self) -> bool: ...


class VadSource(Protocol):
    """Sustained speech per track: >= 3 s voiced inside a rolling 10 s window."""

    def sustained(self, track: str) -> bool: ...


@dataclass
class FakeMicSource:
    holders: list[MicHolder] = field(default_factory=list)

    def hold(self, process: str) -> None:
        self.holders = [MicHolder(process=process)]

    def release(self) -> None:
        self.holders = []

    def current_holders(self) -> list[MicHolder]:
        return list(self.holders)


@dataclass
class FakeSessionSource:
    processes: list[str] = field(default_factory=list)

    def render_processes(self) -> list[str]:
        return list(self.processes)


@dataclass
class FakeTitleSource:
    window_titles: list[str] = field(default_factory=list)

    def titles(self) -> list[str]:
        return list(self.window_titles)


@dataclass
class FakeCameraSource:
    on: bool = False

    def in_use(self) -> bool:
        return self.on


@dataclass
class FakeVadSource:
    tracks: dict[str, bool] = field(default_factory=lambda: {"me": False, "them": False})

    def sustained(self, track: str) -> bool:
        return bool(self.tracks.get(track, False))

    def set(self, *, me: bool | None = None, them: bool | None = None) -> None:
        if me is not None:
            self.tracks["me"] = me
        if them is not None:
            self.tracks["them"] = them


@dataclass
class Sources:
    mic: MicSource
    sessions: SessionSource
    titles: TitleSource
    camera: CameraSource
    vad: VadSource


def fake_sources() -> Sources:
    return Sources(
        mic=FakeMicSource(),
        sessions=FakeSessionSource(),
        titles=FakeTitleSource(),
        camera=FakeCameraSource(),
        vad=FakeVadSource(),
    )
