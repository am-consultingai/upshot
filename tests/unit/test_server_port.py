"""The local server's port (D95): 43217 by default, never saved because it was in use, and
the old 8000 or a fallback a saved file still holds moved to it once."""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from app import server
from app.config import DEFAULT_PORT, Config


def _write(path: Path, data: dict[str, object]) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def _saved(path: Path) -> dict[str, object]:
    return dict(json.loads(path.read_text(encoding="utf-8")))


def test_the_default_is_43217_and_the_fallbacks_the_30_after_it() -> None:
    assert DEFAULT_PORT == 43217
    assert Config.load(file=Path("does-not-exist.json"), environ={}).server_port == 43217
    assert list(server.FALLBACK_PORTS) == list(range(43218, 43248))


@pytest.mark.parametrize("old", [8000, 8010, 8017, 8040])
def test_a_saved_old_port_moves_to_the_new_default(tmp_path: Path, old: int) -> None:
    """8000, or a fallback taken once and saved with everything else, was never chosen."""
    path = tmp_path / "app_config.json"
    _write(path, {"server": {"host": "127.0.0.1", "port": old}, "setup": {"done": True}})
    cfg = Config.load(file=path, environ={})
    assert cfg.server_port == DEFAULT_PORT
    cfg.save()
    saved = _saved(path)
    assert "server.port" in saved["migrations"]["forgotten_defaults"]  # type: ignore[index]
    assert Config.load(file=path, environ={}).server_port == DEFAULT_PORT


@pytest.mark.parametrize("chosen", [9123, 8009, 8041, 43300])
def test_a_port_set_by_hand_outside_the_old_range_stays(tmp_path: Path, chosen: int) -> None:
    path = tmp_path / "app_config.json"
    _write(path, {"server": {"port": chosen}})
    cfg = Config.load(file=path, environ={})
    assert cfg.server_port == chosen
    cfg.use_port(cfg.server_port)
    cfg.save()
    assert _saved(path)["server"] == {"host": "127.0.0.1", "port": chosen}
    assert Config.load(file=path, environ={}).server_port == chosen


def test_the_migration_runs_once(tmp_path: Path) -> None:
    """After it, 8000 set by hand is a choice like any other."""
    path = tmp_path / "app_config.json"
    _write(path, {"server": {"port": 8000}})
    cfg = Config.load(file=path, environ={})
    cfg.save()
    saved = _saved(path)
    saved["server"] = {"port": 8000}  # the user edits the file by hand
    _write(path, saved)
    assert Config.load(file=path, environ={}).server_port == 8000


def test_the_port_in_use_is_never_saved(tmp_path: Path) -> None:
    """A fallback taken because something else held 43217 at one start used to be saved
    at the next settings save, and became the preferred port from then on."""
    path = tmp_path / "app_config.json"
    cfg = Config.load(file=path, environ={})
    cfg.use_port(43219)
    assert cfg.server_port == 43219, "this run listens on it"
    cfg.set("ui.language", "he")  # any settings save
    cfg.save()
    saved = _saved(path)
    assert "port" not in saved["server"]  # type: ignore[operator]
    assert Config.load(file=path, environ={}).server_port == DEFAULT_PORT


def test_a_migrated_old_port_is_not_written_back_by_the_first_save(tmp_path: Path) -> None:
    """The file on disk still holds 8000 until that save: the run's port is saved as the
    file was read after the migration, not as it is on disk."""
    path = tmp_path / "app_config.json"
    _write(path, {"server": {"port": 8017}})
    cfg = Config.load(file=path, environ={})
    cfg.use_port(cfg.server_port)
    cfg.save()
    assert "port" not in _saved(path)["server"]  # type: ignore[operator]
    assert Config.load(file=path, environ={}).server_port == DEFAULT_PORT


def test_a_port_set_deliberately_after_the_start_is_saved(tmp_path: Path) -> None:
    path = tmp_path / "app_config.json"
    cfg = Config.load(file=path, environ={})
    cfg.use_port(43220)
    cfg.set("server.port", 9200)
    cfg.save()
    assert Config.load(file=path, environ={}).server_port == 9200


def test_a_taken_default_falls_back_into_the_new_range() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as holder:
        try:
            holder.bind(("127.0.0.1", DEFAULT_PORT))
        except OSError:
            pytest.skip("43217 is already in use on this machine")
        holder.listen()
        chosen = server.choose_port("127.0.0.1", DEFAULT_PORT)
    assert chosen in range(43218, 43248)
