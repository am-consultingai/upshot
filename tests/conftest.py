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
    """Every test that touches app state gets its own app home via UP_HOME."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("UP_HOME", str(home))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    yield home


@pytest.fixture
def installed_models(app_home: Path) -> dict[str, Path]:
    """All three speech models present and verified in the test's app home."""
    from tests.fixtures.models import install_models

    return install_models(app_home)


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def seeded() -> random.Random:
    return random.Random(1234)


@pytest.fixture(autouse=True)
def _no_wall_clock_home(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    """Nothing in the suite may write to a real user profile."""
    if "UP_HOME" not in os.environ:
        monkeypatch.setenv("UP_HOME", str(tmp_path_factory.mktemp("ma-home")))
    # Nor ask the real website for a newer version of the Terms (app/legal/terms.py).
    monkeypatch.setenv("UP_LEGAL__CHECK", "false")
    # Nor for a newer version of the app (app/updates/service.py).
    monkeypatch.setenv("UP_UPDATES__CHECK", "false")
    yield


@pytest.fixture(autouse=True)
def _no_real_dotenv(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    """Nor read this machine's .env (scripts/local_env.py): it names where real secrets are,
    and a failing assertion would print them. A test that wants one writes its own."""
    import sys

    scripts = str(Path(__file__).resolve().parents[1] / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import local_env

    monkeypatch.setattr(local_env, "ENV_FILE", tmp_path_factory.mktemp("dotenv") / ".env")
    yield


@pytest.fixture(autouse=True)
def _no_model_catalog_between_tests():  # type: ignore[no-untyped-def]
    """The app keeps one model catalog for the process (app.llm.model_choice); a test
    that builds services sets it, and the next test must not inherit it."""
    yield
    from app.llm import model_choice

    model_choice.set_catalog(None)
