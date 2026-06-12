"""End-to-end tests: real MCP client session ↔ superpos MCP server ↔ fake API."""

import json

from mcp.shared.memory import create_connected_server_and_client_session as client_session

from superpos_mcp.config import Config
from superpos_mcp.server import create_server


def make_server(fake, **overrides):
    defaults = dict(base_url=fake.base_url, token="good-token", hive_id="hive-1")
    defaults.update(overrides)
    return create_server(Config(**defaults))


async def call(client, tool, args=None):
    result = await client.call_tool(tool, args or {})
    payload = result.structuredContent
    if isinstance(payload, dict) and set(payload) == {"result"}:
        payload = payload["result"]  # FastMCP wraps non-dict returns
    if payload is None:
        text = "".join(c.text for c in result.content if c.type == "text")
        try:
            payload = json.loads(text) if text else None
        except json.JSONDecodeError:
            payload = text
    return result, payload


async def test_lists_all_tools(fake):
    server = make_server(fake)
    async with client_session(server._mcp_server) as client:
        tools = {t.name for t in (await client.list_tools()).tools}
    expected = {
        "superpos_whoami", "superpos_heartbeat",
        "superpos_create_task", "superpos_poll_tasks", "superpos_claim_task",
        "superpos_get_task", "superpos_task_progress", "superpos_complete_task",
        "superpos_fail_task",
        "superpos_publish_event", "superpos_poll_events",
        "superpos_search_knowledge", "superpos_list_knowledge",
        "superpos_get_knowledge", "superpos_create_knowledge", "superpos_update_knowledge",
        "superpos_list_schedules", "superpos_create_schedule", "superpos_delete_schedule",
        "superpos_list_hives", "superpos_hive_agents",
        "superpos_get_persona", "superpos_update_memory",
    }
    assert expected <= tools


async def test_whoami_connected(fake):
    server = make_server(fake)
    async with client_session(server._mcp_server) as client:
        _, payload = await call(client, "superpos_whoami")
    assert payload["connected"] is True
    assert payload["agent"]["id"] == "agent-1"
    assert payload["hive_id"] == "hive-1"


async def test_whoami_reports_missing_config(fake):
    server = create_server(Config(base_url=fake.base_url))  # no token, no hive
    async with client_session(server._mcp_server) as client:
        _, payload = await call(client, "superpos_whoami")
    assert payload["connected"] is False
    assert any("token" in m for m in payload["missing_configuration"])
    assert any("hive_id" in m for m in payload["missing_configuration"])


async def test_full_task_lifecycle(fake):
    server = make_server(fake)
    async with client_session(server._mcp_server) as client:
        _, task = await call(client, "superpos_create_task", {
            "task_type": "code_review",
            "payload": {"repo": "superpos-app"},
            "instructions": "Review the latest PR",
            "priority": 7,
        })
        assert task["status"] == "pending"

        _, pending = await call(client, "superpos_poll_tasks")
        assert [t["id"] for t in pending] == [task["id"]]

        _, claimed = await call(client, "superpos_claim_task", {"task_id": task["id"]})
        assert claimed["status"] == "in_progress"

        # Second claim must conflict and surface as a tool error.
        result, _ = await call(client, "superpos_claim_task", {"task_id": task["id"]})
        assert result.isError

        _, progressed = await call(client, "superpos_task_progress", {
            "task_id": task["id"], "progress": 50, "status_message": "halfway",
        })
        assert progressed["progress"] == 50

        _, done = await call(client, "superpos_complete_task", {
            "task_id": task["id"], "result": {"verdict": "LGTM"},
        })
        assert done["status"] == "completed"
        assert done["result"] == {"verdict": "LGTM"}

        _, empty = await call(client, "superpos_poll_tasks")
        assert empty == []


async def test_fail_task(fake):
    server = make_server(fake)
    async with client_session(server._mcp_server) as client:
        _, task = await call(client, "superpos_create_task", {"task_type": "build"})
        await call(client, "superpos_claim_task", {"task_id": task["id"]})
        _, failed = await call(client, "superpos_fail_task", {
            "task_id": task["id"], "error_message": "compiler exploded",
        })
    assert failed["status"] == "failed"
    assert failed["error"]["message"] == "compiler exploded"


async def test_knowledge_roundtrip(fake):
    server = make_server(fake)
    async with client_session(server._mcp_server) as client:
        _, entry = await call(client, "superpos_create_knowledge", {
            "key": "deploy.frontend.notes",
            "value": {"summary": "use blue-green deploys"},
        })
        _, hits = await call(client, "superpos_search_knowledge", {"query": "blue-green"})
        assert hits and hits[0]["id"] == entry["id"]

        _, updated = await call(client, "superpos_update_knowledge", {
            "entry_id": entry["id"], "value": {"summary": "canary deploys now"},
        })
        assert updated["version"] == 2

        _, fetched = await call(client, "superpos_get_knowledge", {"entry_id": entry["id"]})
        assert fetched["value"] == {"summary": "canary deploys now"}


async def test_events_pubsub(fake):
    server = make_server(fake)
    async with client_session(server._mcp_server) as client:
        await call(client, "superpos_publish_event", {
            "event_type": "deploy.finished", "payload": {"sha": "abc123"},
        })
        _, events = await call(client, "superpos_poll_events")
        assert events[0]["type"] == "deploy.finished"
        _, after = await call(client, "superpos_poll_events", {"cursor": events[0]["seq"]})
        assert after == []


async def test_schedules(fake):
    server = make_server(fake)
    async with client_session(server._mcp_server) as client:
        _, schedule = await call(client, "superpos_create_schedule", {
            "name": "nightly-report",
            "trigger_type": "cron",
            "task_type": "report",
            "cron_expression": "0 2 * * *",
        })
        _, schedules = await call(client, "superpos_list_schedules")
        assert [s["id"] for s in schedules] == [schedule["id"]]
        _, deleted = await call(client, "superpos_delete_schedule", {"schedule_id": schedule["id"]})
        assert deleted["deleted"] is True
        _, schedules = await call(client, "superpos_list_schedules")
        assert schedules == []


async def test_expired_token_recovers_transparently(fake):
    server = make_server(
        fake, token="expired-token", refresh_token="refresh-1", agent_id="agent-1"
    )
    async with client_session(server._mcp_server) as client:
        _, payload = await call(client, "superpos_whoami")
    assert payload["connected"] is True


async def test_persona_and_memory(fake):
    server = make_server(fake)
    async with client_session(server._mcp_server) as client:
        _, persona = await call(client, "superpos_get_persona")
        assert "assembled" in persona
        _, memory = await call(client, "superpos_update_memory", {"content": "learned a thing"})
        assert memory["content"] == "learned a thing"


def test_ttl_shorthand_conversion():
    from superpos_mcp.server import ttl_to_timestamp

    iso = ttl_to_timestamp("7d")
    assert iso.endswith("+00:00") and "T" in iso
    # Non-shorthand values pass through untouched.
    assert ttl_to_timestamp("2030-01-01T00:00:00Z") == "2030-01-01T00:00:00Z"
