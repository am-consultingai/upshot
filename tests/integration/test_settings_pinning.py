"""A setting the environment is holding must say so, rather than appear to forget.

The environment is the top configuration layer, so a launcher that exports
``UP_DETECTION__MODE`` beats ``app_config.json`` on every start. Saving from the Settings
screen worked, the file recorded the choice, and the next launch overrode it again — so
"Detecting meetings" came back off however many times it was set, and nothing on screen,
in the API or in the log said what was doing it. Precedence is unchanged; the silence is
not.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.api import build_harness


@pytest.fixture
def api(tmp_path: Path, app_home: Path):  # type: ignore[no-untyped-def]
    return build_harness(tmp_path)


def test_nothing_is_pinned_by_default(api) -> None:  # type: ignore[no-untyped-def]
    body = api.client().get("/api/settings").json()
    assert body["pinned"] == {}


def test_a_pinned_key_is_named_with_the_variable_holding_it(tmp_path: Path, app_home: Path) -> None:
    from app.config import Config

    harness = build_harness(tmp_path)
    path = tmp_path / "app_config.json"
    path.write_text("{}", encoding="utf-8")
    # The same configuration the harness built, loaded the way a launcher's start loads
    # it: from the file, with the environment on top.
    was = harness.services.config
    config = Config.load(file=path, environ={"UP_DETECTION__MODE": '"off"'})
    for key in ("server.port", "data_root", "secrets.backend", "llm.provider", "asr.backend"):
        config.set(key, was.get(key))
    harness.services.config = config

    body = harness.client().get("/api/settings").json()
    assert body["pinned"] == {"detection.mode": "UP_DETECTION__MODE"}
    assert body["config"]["detection"]["mode"] == "off"


def test_detection_mode_is_the_users_and_survives_a_save(api) -> None:  # type: ignore[no-untyped-def]
    """With nothing pinning it, the choice is the user's and it holds."""
    client = api.client()
    assert client.get("/api/settings").json()["config"]["detection"]["mode"] == "shadow"

    body = client.put("/api/settings", json={"values": {"detection.mode": "on"}}).json()
    assert body["config"]["detection"]["mode"] == "on"
    assert body["pinned"] == {}
    assert api.services.config.get("detection.mode") == "on"
    # And the detector reads it live, with no restart, as the hint on the screen promises.
    assert api.client().get("/api/status").json()["detector"]["mode"] == "on"
