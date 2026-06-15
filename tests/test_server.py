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
        "superpos_list_issues", "superpos_get_issue", "superpos_create_issue",
        "superpos_update_issue", "superpos_transition_issue", "superpos_close_issue",
        "superpos_list_issue_types", "superpos_link_task_to_issue",
        "superpos_request_issue_approval", "superpos_add_issue_dependency",
        "superpos_remove_issue_dependency",
        "superpos_list_tracks", "superpos_get_track", "superpos_create_track",
        "superpos_update_track", "superpos_transition_track",
        "superpos_link_issue", "superpos_unlink_issue",
        "superpos_hive_map",
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


async def test_issue_lifecycle(fake):
    server = make_server(fake)
    async with client_session(server._mcp_server) as client:
        # issue_type accepts a key, not just an id
        _, issue = await call(client, "superpos_create_issue", {
            "title": "Login button explodes",
            "issue_type": "bug",
            "description": "Repro: click it.",
            "metadata": {"severity": "high"},
        })
        assert issue["state"] == "open"
        assert issue["issue_type_id"] == "type-bug"

        _, open_issues = await call(client, "superpos_list_issues", {"state": "open"})
        assert [i["id"] for i in open_issues] == [issue["id"]]

        _, by_title = await call(client, "superpos_list_issues", {"query": "explodes"})
        assert [i["id"] for i in by_title] == [issue["id"]]

        _, updated = await call(client, "superpos_update_issue", {
            "issue_id": issue["id"], "title": "Login button crashes",
        })
        assert updated["title"] == "Login button crashes"
        assert updated["description"] == "Repro: click it."  # untouched

        _, moved = await call(client, "superpos_transition_issue", {
            "issue_id": issue["id"], "to": "in_progress",
        })
        assert moved["state"] == "in_progress"

        _, closed = await call(client, "superpos_close_issue", {
            "issue_id": issue["id"], "reason": "fixed in #42",
        })
        assert closed["state"] == "done"
        assert closed["closure_reason"] == "fixed in #42"

        # Terminal issues reject further transitions.
        result, _ = await call(client, "superpos_transition_issue", {
            "issue_id": issue["id"], "to": "open",
        })
        assert result.isError

        _, fetched = await call(client, "superpos_get_issue", {"issue_id": issue["id"]})
        assert fetched["state"] == "done"


async def test_create_issue_assignee_shorthand(fake):
    server = make_server(fake)
    async with client_session(server._mcp_server) as client:
        _, issue = await call(client, "superpos_create_issue", {
            "title": "Assigned work",
            "assignee_type": "agent",
            "assignee_id": "agent-1",
        })
    assert issue["assignee_type"] == "App\\Models\\Agent"
    assert issue["assignee_id"] == "agent-1"


async def test_issue_types_link_approval_and_dependencies(fake):
    server = make_server(fake)
    async with client_session(server._mcp_server) as client:
        _, types = await call(client, "superpos_list_issue_types")
        assert {t["key"] for t in types} >= {"task", "bug"}

        _, issue = await call(client, "superpos_create_issue", {"title": "Ship it"})

        # Link a task for traceability.
        _, task = await call(client, "superpos_create_task", {"task_type": "build"})
        _, linked = await call(client, "superpos_link_task_to_issue", {
            "issue_id": issue["id"], "task_id": task["id"],
        })
        assert [t["id"] for t in linked["tasks"]] == [task["id"]]

        # A dependency on another issue, then remove it.
        _, other = await call(client, "superpos_create_issue", {"title": "Prereq"})
        _, dep = await call(client, "superpos_add_issue_dependency", {
            "issue_id": issue["id"], "depends_on_issue_id": other["id"],
        })
        assert dep["kind"] == "blocks"
        _, removed = await call(client, "superpos_remove_issue_dependency", {
            "issue_id": issue["id"], "dependency_id": dep["id"],
        })
        assert removed["removed"] is True
        _, after = await call(client, "superpos_get_issue", {"issue_id": issue["id"]})
        assert after["dependencies"] == []

        # Approval is only valid from in_progress/blocked; it moves the issue to blocked.
        await call(client, "superpos_transition_issue", {
            "issue_id": issue["id"], "to": "in_progress",
        })
        _, escalated = await call(client, "superpos_request_issue_approval", {
            "issue_id": issue["id"], "summary": "Needs human sign-off",
            "recommended_action": "approve_closure",
        })
        assert escalated["state"] == "blocked"
        assert escalated["approvals"][0]["status"] == "pending"


async def test_track_lifecycle(fake):
    server = make_server(fake)
    async with client_session(server._mcp_server) as client:
        _, track = await call(client, "superpos_create_track", {
            "slug": "agent-hive-awareness",
            "name": "Agent hive awareness",
            "spec": "# Plan\nGive agents eyes.",
        })
        assert track["state"] == "planning"

        _, tracks = await call(client, "superpos_list_tracks")
        assert [t["slug"] for t in tracks] == ["agent-hive-awareness"]
        assert "spec" not in tracks[0]  # list omits the heavy spec document

        _, issue = await call(client, "superpos_create_issue", {"title": "Phase 1"})
        _, link = await call(client, "superpos_link_issue", {
            "track_slug": "agent-hive-awareness", "issue_id": issue["id"],
        })
        assert link["issue_id"] == issue["id"]

        _, full = await call(client, "superpos_get_track", {"slug": "agent-hive-awareness"})
        assert full["spec"] == "# Plan\nGive agents eyes."
        assert [i["id"] for i in full["issues"]] == [issue["id"]]

        _, renamed = await call(client, "superpos_update_track", {
            "slug": "agent-hive-awareness", "name": "Hive awareness",
        })
        assert renamed["name"] == "Hive awareness"

        _, active = await call(client, "superpos_transition_track", {
            "slug": "agent-hive-awareness", "to": "active",
        })
        assert active["state"] == "active"

        _, unlinked = await call(client, "superpos_unlink_issue", {
            "track_slug": "agent-hive-awareness", "issue_id": issue["id"],
        })
        assert unlinked["unlinked"] is True

        _, empty = await call(client, "superpos_get_track", {"slug": "agent-hive-awareness"})
        assert empty["issues"] == []


async def test_hive_map(fake):
    server = make_server(fake)
    async with client_session(server._mcp_server) as client:
        _, topology = await call(client, "superpos_hive_map", {"timeframe": "7d"})
    assert topology["timeframe"] == "7d"
    assert {n["type"] for n in topology["nodes"]} == {"agent"}
    assert topology["edges"] == []


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
