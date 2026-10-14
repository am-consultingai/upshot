"""/mcp/ext/: the meeting tools for the user's own AI tools, on the access key (D98)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.assistant import mcp
from app.db.dao import Turn
from app.pipeline.states import MeetingState
from tests.fixtures.api import ApiHarness, build_harness, serve

ACCEPT = {"Accept": "application/json, text/event-stream"}
INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "t", "version": "0"},
    },
}


@pytest.fixture
def api(tmp_path: Path, app_home: Path) -> ApiHarness:
    harness = build_harness(tmp_path)
    dao = harness.services.dao
    folder = harness.services.config.data_root / "m-budget"
    folder.mkdir(parents=True)
    dao.insert_meeting(
        meeting_id="m-budget",
        folder=folder,
        source="manual",
        state=MeetingState.RECORDING,
        profile="cpu-deferred",
        title="Q4 budget review",
        started_at="2026-09-20T09:30:00Z",
    )
    dao.index_turns(
        "m-budget", [Turn(0, "THEM", 4000, "The marketing budget is cut by ten percent.")]
    )
    return harness


def key(api: ApiHarness) -> dict[str, str]:
    return {**ACCEPT, "Authorization": f"Bearer {api.services.auth.access_key}"}


def rpc(
    client: httpx.Client, method: str, params: dict[str, Any], headers: dict[str, str]
) -> httpx.Response:
    return client.post(
        "/mcp/ext/",
        json={"jsonrpc": "2.0", "id": 2, "method": method, "params": params},
        headers=headers,
    )


def call(client: httpx.Client, headers: dict[str, str], tool: str, **arguments: Any) -> str:
    reply = rpc(client, "tools/call", {"name": tool, "arguments": arguments}, headers).json()
    return str(reply["result"]["content"][0]["text"])


def data(text: str) -> Any:
    """The JSON inside the data fence."""
    return json.loads(text.split("\n", 1)[1].rsplit("\n", 1)[0])


def test_the_access_key_opens_it_and_nothing_else_does(api: ApiHarness) -> None:
    with serve(api) as client:
        assert client.post("/mcp/ext/", json=INIT, headers=ACCEPT).status_code == 401
        wrong = {**ACCEPT, "Authorization": f"Bearer {api.services.auth.mcp_token}"}
        assert client.post("/mcp/ext/", json=INIT, headers=wrong).status_code == 401, (
            "the assistant's token is the assistant's"
        )
        refused = client.post("/mcp/", json=INIT, headers=key(api))
        assert refused.status_code == 401, "the access key does not open the assistant's door"
        opened = client.post("/mcp/ext/", json=INIT, headers=key(api))
        assert opened.status_code == 200
        result = opened.json()["result"]
    assert result["serverInfo"]["name"] == "upshot"
    assert "upshot-data-" in result["instructions"]


def test_a_reset_key_takes_effect_at_once(api: ApiHarness) -> None:
    from app.api.security import reset_access_key

    with serve(api) as client:
        old = key(api)
        reset_access_key(api.services.auth, Path(api.services.config.data_root).parent / "k")
        assert client.post("/mcp/ext/", json=INIT, headers=old).status_code == 401
        assert client.post("/mcp/ext/", json=INIT, headers=key(api)).status_code == 200


def test_the_tools_are_listed_read_only_and_without_internals(api: ApiHarness) -> None:
    with serve(api) as client:
        listed = rpc(client, "tools/list", {}, key(api)).json()["result"]["tools"]
    names = sorted(tool["name"] for tool in listed)
    assert names == [
        "calendar_range",
        "get_meeting",
        "get_summary",
        "get_transcript",
        "list_action_items",
        "list_meetings",
        "related_meetings",
        "search",
    ]
    for tool in listed:
        assert tool["annotations"]["readOnlyHint"] is True
        assert "ctx" not in tool["inputSchema"].get("properties", {}), tool["name"]


def test_a_transcript_and_a_summary_are_read(api: ApiHarness) -> None:
    folder = api.services.config.data_root / "m-budget"
    (folder / "notes.json").write_text(
        json.dumps({"chapters": [{"title": "Budget", "start_ms": 4000}]}), encoding="utf-8"
    )
    api.services.dao.index_summary("m-budget", "The budget is cut. " * 3000)
    with serve(api) as client:
        lines = data(call(client, key(api), "get_transcript", meeting_id="m-budget"))
        first = data(call(client, key(api), "get_summary", meeting_id="m-budget"))
        last = data(call(client, key(api), "get_summary", meeting_id="m-budget", part=9))
    assert lines["lines"][0]["text"] == "The marketing budget is cut by ten percent."
    assert first["parts"] == 3 and first["part"] == 1 and "part=2" in first["next"]
    assert first["chapters"] == [{"title": "Budget", "start_ms": 4000, "time": "0:04"}]
    assert last["part"] == 3 and "next" not in last and "chapters" not in last
    whole = "The budget is cut. " * 3000
    assert len(first["summary"]) + len(last["summary"]) < len(whole)


def test_the_switch_turns_the_tools_off(api: ApiHarness) -> None:
    api.services.config.set(mcp.SWITCH, False)
    with serve(api) as client:
        text = call(client, key(api), "search", query="budget")
        assert rpc(client, "tools/list", {}, key(api)).status_code == 200
    assert text == mcp.OFF_TEXT


def test_docker_desktop_may_name_the_host_here_only(api: ApiHarness) -> None:
    port = api.services.config.server_port
    with serve(api) as client:
        for host in ("host.docker.internal", f"host.docker.internal:{port}"):
            docker = {**key(api), "Host": host}
            assert client.post("/mcp/ext/", json=INIT, headers=docker).status_code == 200
            assert client.get("/api/meetings", headers={"Host": host}).status_code == 421
            assert client.post("/mcp/", json=INIT, headers={"Host": host}).status_code == 421
            assert client.get("/api/v1/info", headers={"Host": host}).status_code == 421
        evil = {**key(api), "Host": "evil.example"}
        assert client.post("/mcp/ext/", json=INIT, headers=evil).status_code == 421


def test_websites_are_refused(api: ApiHarness) -> None:
    with serve(api) as client:
        for extra in ({"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"}):
            refused = client.post("/mcp/ext/", json=INIT, headers={**key(api), **extra})
            assert refused.status_code == 403, extra


def test_the_address_without_its_slash_reaches_the_same_server(api: ApiHarness) -> None:
    """A pasted URL loses its slash; it must not land on the assistant's door."""
    with serve(api) as client:
        opened = client.post("/mcp/ext", json=INIT, headers=key(api))
        assert opened.status_code == 200, opened.text
        assert opened.json()["result"]["serverInfo"]["name"] == "upshot"
        assert client.post("/mcp/ext", json=INIT, headers=ACCEPT).status_code == 401


def test_a_key_with_other_than_ascii_is_refused_not_a_crash(api: ApiHarness) -> None:
    odd = {**ACCEPT, "Authorization": "Bearer ключ".encode()}  # type: ignore[dict-item]
    with serve(api) as client:
        assert client.post("/mcp/ext/", json=INIT, headers=odd).status_code == 401
    program = api.client(authorized=False)  # no cookie: not the page
    assert program.get("/api/v1/transcriptions", headers=odd).status_code == 401
