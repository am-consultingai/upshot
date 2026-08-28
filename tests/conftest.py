from __future__ import annotations

import os
import random
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.clock import FakeClock


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-goldens",
        action="store_true",
        default=False,
        help="rewrite golden files instead of comparing against them",
    )


@pytest.fixture
def update_goldens(request: pytest.FixtureRequest) -> bool:
    return bool(request.config.getoption("--update-goldens"))


GOLDEN_DIR = Path(__file__).parent / "goldens"


@pytest.fixture
def golden(update_goldens: bool):  # type: ignore[no-untyped-def]
    """Compare text against ``tests/goldens/<name>``; rewrite it with --update-goldens."""

    def _golden(name: str, actual: str) -> None:
        path = GOLDEN_DIR / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if update_goldens or not path.exists():
            path.write_text(actual, encoding="utf-8")
            if not update_goldens:
                pytest.fail(f"golden {name} did not exist; created it — review and re-run")
            return
        expected = path.read_text(encoding="utf-8")
        assert actual == expected, f"golden mismatch for {name} (regenerate with --update-goldens)"

    return _golden


@pytest.fixture
def app_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Every test that touches app state gets its own app home via MA_HOME."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("MA_HOME", str(home))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    yield home


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def seeded() -> random.Random:
    return random.Random(1234)


@pytest.fixture(autouse=True)
def _no_wall_clock_home(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    """Nothing in the suite may write to a real user profile."""
    if "MA_HOME" not in os.environ:
        monkeypatch.setenv("MA_HOME", str(tmp_path_factory.mktemp("ma-home")))
    yield
