"""The Superpos MCP server.

Exposes the Superpos cloud API as MCP tools so any MCP-capable coding agent
(Claude Code, Codex, Cursor, Gemini CLI, ...) can join a hive: poll and run
tasks, share knowledge, publish events, and manage schedules.

All hive-scoped tools default to the configured hive; pass ``hive_id`` to
target another hive the agent can access.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP

from .client import SuperposApi
from .config import Config

_TTL_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}

# The API wants Eloquent model class names for assignees; agents shouldn't.
_ASSIGNEE_TYPES = {"agent": "App\\Models\\Agent", "user": "App\\Models\\User"}


def assignee_model(value: str) -> str:
    return _ASSIGNEE_TYPES.get(value.strip().lower(), value)


def ttl_to_timestamp(ttl: str) -> str:
    """Convert duration shorthand ('30m', '1h', '7d') to the ISO8601 expiry
    timestamp the API expects; ISO dates pass through unchanged."""
    match = re.fullmatch(r"(\d+)([smhdw])", ttl.strip())
    if not match:
        return ttl
    delta = timedelta(**{_TTL_UNITS[match.group(2)]: int(match.group(1))})
    return (datetime.now(timezone.utc) + delta).isoformat()


def create_server(config: Config | None = None) -> FastMCP:
    cfg = config or Config.load()
    api = SuperposApi(cfg)

    mcp = FastMCP(
        "superpos",
        instructions=(
            "Tools for working with a Superpos cloud workspace (agent orchestration "
            "platform). Use superpos_whoami first to confirm connectivity. Tasks are "
            "the unit of work: poll → claim → progress → complete/fail. The knowledge "
            "store shares context between agents; events broadcast notifications. "
            "Issues and tracks are the planning surface (issues group into tracks); "
            "superpos_hive_map shows the hive topology."
        ),
    )

    def resolve_issue_type(value: str, hive_id: str | None) -> str:
        """Accept an issue-type key ('task', 'bug') or id; return the id."""
        types = api.request("GET", f"/api/v1/hives/{api.hive(hive_id)}/issue-types") or []
        for issue_type in types:
            if value in (issue_type.get("id"), issue_type.get("key")):
                return issue_type["id"]
        return value  # assume it's an id; the server validates

    # ------------------------------------------------------------------
    # Identity / connectivity
    # ------------------------------------------------------------------

    @mcp.tool()
    def superpos_whoami() -> dict[str, Any]:
        """Check Superpos connectivity and show the authenticated agent profile,
        configured hive, and base URL. Call this first to verify setup."""
        missing = cfg.missing()
        if missing:
            return {
                "connected": False,
                "base_url": cfg.base_url,
                "missing_configuration": missing,
                "fix": "Run `superpos-mcp setup` in a terminal, or set SUPERPOS_* env vars.",
            }
        agent = api.me()
        return {
            "connected": True,
            "base_url": cfg.base_url,
            "hive_id": cfg.hive_id,
            "agent": agent,
        }

    @mcp.tool()
    def superpos_heartbeat(status: str | None = None) -> dict[str, Any]:
        """Send a heartbeat so this agent shows as online in the Superpos dashboard.
        Optionally also set status: online, busy, idle, offline, or error."""
        result = api.heartbeat()
        if status:
            result = api.update_status(status)
        return {"ok": True, "agent": result}

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------

    @mcp.tool()
    def superpos_create_task(
        task_type: str,
        payload: dict[str, Any] | None = None,
        instructions: str | None = None,
        priority: int | None = None,
        target_capability: str | None = None,
        target_agent_id: str | None = None,
        parent_task_id: str | None = None,
        timeout_seconds: int | None = None,
        hive_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a task in the hive for another agent (or any capable agent) to pick
        up. Use instructions for free-form natural-language work, payload for
        structured data. Route with target_capability or target_agent_id."""
        body: dict[str, Any] = {"type": task_type}
        if payload is not None:
            body["payload"] = payload
        if instructions:
            body["invoke"] = {"instructions": instructions}
        if priority is not None:
            body["priority"] = priority
        if target_capability:
            body["target_capability"] = target_capability
        if target_agent_id:
            body["target_agent_id"] = target_agent_id
        if parent_task_id:
            body["parent_task_id"] = parent_task_id
        if timeout_seconds is not None:
            body["timeout_seconds"] = timeout_seconds
        return api.request("POST", f"/api/v1/hives/{api.hive(hive_id)}/tasks", json=body)

    @mcp.tool()
    def superpos_poll_tasks(
        capability: str | None = None,
        limit: int = 10,
        hive_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List pending tasks available for this agent to claim. Filter by capability
        to only see matching work. Returns [] when the queue is empty."""
        params: dict[str, Any] = {"limit": limit}
        if capability:
            params["capability"] = capability
        return api.request(
            "GET", f"/api/v1/hives/{api.hive(hive_id)}/tasks/poll", params=params
        ) or []

    @mcp.tool()
    def superpos_claim_task(task_id: str, hive_id: str | None = None) -> dict[str, Any]:
        """Atomically claim a pending task so no other agent processes it. Claim
        before starting work; fails with a conflict if already claimed."""
        return api.request(
            "PATCH", f"/api/v1/hives/{api.hive(hive_id)}/tasks/{task_id}/claim"
        )

    @mcp.tool()
    def superpos_get_task(task_id: str, hive_id: str | None = None) -> dict[str, Any]:
        """Get a single task with its current status, payload, result, and error."""
        return api.request("GET", f"/api/v1/hives/{api.hive(hive_id)}/tasks/{task_id}")

    @mcp.tool()
    def superpos_task_progress(
        task_id: str,
        progress: int,
        status_message: str | None = None,
        hive_id: str | None = None,
    ) -> dict[str, Any]:
        """Report progress (0-100) on a claimed task. Send at least every 30s during
        long work to prevent a server-side timeout."""
        body: dict[str, Any] = {"progress": progress}
        if status_message:
            body["status_message"] = status_message
        return api.request(
            "PATCH", f"/api/v1/hives/{api.hive(hive_id)}/tasks/{task_id}/progress", json=body
        )

    @mcp.tool()
    def superpos_complete_task(
        task_id: str,
        result: dict[str, Any] | None = None,
        status_message: str | None = None,
        hive_id: str | None = None,
    ) -> dict[str, Any]:
        """Mark a claimed task as completed, attaching a JSON result for the creator."""
        body: dict[str, Any] = {}
        if result is not None:
            body["result"] = result
        if status_message:
            body["status_message"] = status_message
        return api.request(
            "PATCH",
            f"/api/v1/hives/{api.hive(hive_id)}/tasks/{task_id}/complete",
            json=body or None,
        )

    @mcp.tool()
    def superpos_fail_task(
        task_id: str,
        error_message: str,
        error_code: str = "agent_error",
        hive_id: str | None = None,
    ) -> dict[str, Any]:
        """Mark a claimed task as failed with an error code and message. The server
        may retry it depending on the task's retry policy."""
        body = {"error": {"code": error_code, "message": error_message}}
        return api.request(
            "PATCH", f"/api/v1/hives/{api.hive(hive_id)}/tasks/{task_id}/fail", json=body
        )

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    @mcp.tool()
    def superpos_publish_event(
        event_type: str,
        payload: dict[str, Any] | None = None,
        hive_id: str | None = None,
    ) -> dict[str, Any]:
        """Broadcast an event to the hive (e.g. 'deploy.finished'). Other agents
        subscribed to this event type will receive it."""
        body: dict[str, Any] = {"type": event_type}
        if payload is not None:
            body["payload"] = payload
        return api.request("POST", f"/api/v1/hives/{api.hive(hive_id)}/events", json=body)

    @mcp.tool()
    def superpos_poll_events(
        cursor: int | None = None,
        limit: int = 50,
        hive_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch events published to the hive since the given cursor (the seq of the
        last event you saw). Returns events with seq numbers for the next cursor."""
        params: dict[str, Any] = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        return api.request(
            "GET", f"/api/v1/hives/{api.hive(hive_id)}/events/poll", params=params
        ) or []

    # ------------------------------------------------------------------
    # Knowledge store
    # ------------------------------------------------------------------

    @mcp.tool()
    def superpos_search_knowledge(
        query: str,
        mode: str = "hybrid",
        limit: int = 10,
        hive_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search the hive's shared knowledge store. mode: hybrid (default), fts
        (keyword), or semantic. Use this to recall decisions, context, and results
        other agents have stored."""
        params = {"q": query, "mode": mode, "limit": limit}
        return api.request(
            "GET", f"/api/v1/hives/{api.hive(hive_id)}/knowledge/search", params=params
        ) or []

    @mcp.tool()
    def superpos_list_knowledge(
        key_prefix: str | None = None,
        limit: int = 20,
        hive_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List knowledge entries in the hive, optionally filtered by key prefix."""
        params: dict[str, Any] = {"limit": limit}
        if key_prefix:
            params["key"] = key_prefix
        return api.request(
            "GET", f"/api/v1/hives/{api.hive(hive_id)}/knowledge", params=params
        ) or []

    @mcp.tool()
    def superpos_get_knowledge(entry_id: str, hive_id: str | None = None) -> dict[str, Any]:
        """Get a single knowledge entry by id, including its full value."""
        return api.request(
            "GET", f"/api/v1/hives/{api.hive(hive_id)}/knowledge/{entry_id}"
        )

    @mcp.tool()
    def superpos_create_knowledge(
        key: str,
        value: dict[str, Any],
        scope: str = "hive",
        ttl: str | None = None,
        hive_id: str | None = None,
    ) -> dict[str, Any]:
        """Store a knowledge entry shared with other agents. key is a dotted,
        domain-scoped name (e.g. 'deploy.frontend.notes'); value is any JSON object.
        scope: hive (default), organization, or agent:{agent_id}. ttl is a duration
        like '1h' or '7d' (or an ISO8601 expiry timestamp)."""
        body: dict[str, Any] = {"key": key, "value": value, "scope": scope}
        if ttl:
            body["ttl"] = ttl_to_timestamp(ttl)
        return api.request("POST", f"/api/v1/hives/{api.hive(hive_id)}/knowledge", json=body)

    @mcp.tool()
    def superpos_update_knowledge(
        entry_id: str,
        value: dict[str, Any],
        hive_id: str | None = None,
    ) -> dict[str, Any]:
        """Replace the value of an existing knowledge entry (bumps its version)."""
        return api.request(
            "PUT",
            f"/api/v1/hives/{api.hive(hive_id)}/knowledge/{entry_id}",
            json={"value": value},
        )

    # ------------------------------------------------------------------
    # Schedules
    # ------------------------------------------------------------------

    @mcp.tool()
    def superpos_list_schedules(hive_id: str | None = None) -> list[dict[str, Any]]:
        """List recurring/one-time schedules in the hive that create tasks
        automatically."""
        return api.request("GET", f"/api/v1/hives/{api.hive(hive_id)}/schedules") or []

    @mcp.tool()
    def superpos_create_schedule(
        name: str,
        trigger_type: str,
        task_type: str,
        cron_expression: str | None = None,
        interval_seconds: int | None = None,
        run_at: str | None = None,
        task_payload: dict[str, Any] | None = None,
        hive_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a schedule that produces tasks automatically. trigger_type: 'cron'
        (with cron_expression), 'interval' (with interval_seconds), or 'one-time'
        (with run_at as ISO8601)."""
        body: dict[str, Any] = {
            "name": name,
            "trigger_type": trigger_type,
            "task_type": task_type,
        }
        if cron_expression:
            body["cron_expression"] = cron_expression
        if interval_seconds is not None:
            body["interval_seconds"] = interval_seconds
        if run_at:
            body["run_at"] = run_at
        if task_payload is not None:
            body["task_payload"] = task_payload
        return api.request("POST", f"/api/v1/hives/{api.hive(hive_id)}/schedules", json=body)

    @mcp.tool()
    def superpos_delete_schedule(schedule_id: str, hive_id: str | None = None) -> dict[str, Any]:
        """Delete a schedule so it stops creating tasks."""
        api.request("DELETE", f"/api/v1/hives/{api.hive(hive_id)}/schedules/{schedule_id}")
        return {"deleted": True, "schedule_id": schedule_id}

    # ------------------------------------------------------------------
    # Issues (planning surface)
    # ------------------------------------------------------------------

    @mcp.tool()
    def superpos_list_issues(
        state: str | None = None,
        issue_type_id: str | None = None,
        assignee_id: str | None = None,
        query: str | None = None,
        per_page: int = 15,
        hive_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List issues in the hive's planning surface, newest-updated first.
        state: open, in_progress, blocked, awaiting_review, done, or cancelled.
        query filters by title substring."""
        params: dict[str, Any] = {"per_page": per_page}
        if state:
            params["state"] = state
        if issue_type_id:
            params["issue_type_id"] = issue_type_id
        if assignee_id:
            params["assignee_id"] = assignee_id
        if query:
            params["q"] = query
        return api.request(
            "GET", f"/api/v1/hives/{api.hive(hive_id)}/issues", params=params
        ) or []

    @mcp.tool()
    def superpos_get_issue(issue_id: str, hive_id: str | None = None) -> dict[str, Any]:
        """Get a single issue with its type, linked tasks, dependencies, approvals,
        and allowed_transitions (the states it can move to next)."""
        return api.request("GET", f"/api/v1/hives/{api.hive(hive_id)}/issues/{issue_id}")

    @mcp.tool()
    def superpos_create_issue(
        title: str,
        issue_type: str = "task",
        description: str | None = None,
        assignee_type: str | None = None,
        assignee_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        hive_id: str | None = None,
    ) -> dict[str, Any]:
        """Create an issue. issue_type is an issue-type key (e.g. 'task', 'bug') or
        id. To assign, pass assignee_type ('agent' or 'user') with assignee_id."""
        body: dict[str, Any] = {
            "title": title,
            "issue_type_id": resolve_issue_type(issue_type, hive_id),
        }
        if description is not None:
            body["description"] = description
        if assignee_type and assignee_id:
            body["assignee_type"] = assignee_model(assignee_type)
            body["assignee_id"] = assignee_id
        if metadata is not None:
            body["metadata"] = metadata
        return api.request("POST", f"/api/v1/hives/{api.hive(hive_id)}/issues", json=body)

    @mcp.tool()
    def superpos_update_issue(
        issue_id: str,
        title: str | None = None,
        description: str | None = None,
        issue_type: str | None = None,
        assignee_type: str | None = None,
        assignee_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        hive_id: str | None = None,
    ) -> dict[str, Any]:
        """Update an issue's fields (only the ones provided). Use
        superpos_transition_issue to change state."""
        body: dict[str, Any] = {}
        if title is not None:
            body["title"] = title
        if description is not None:
            body["description"] = description
        if issue_type is not None:
            body["issue_type_id"] = resolve_issue_type(issue_type, hive_id)
        if assignee_type and assignee_id:
            body["assignee_type"] = assignee_model(assignee_type)
            body["assignee_id"] = assignee_id
        if metadata is not None:
            body["metadata"] = metadata
        return api.request(
            "PATCH", f"/api/v1/hives/{api.hive(hive_id)}/issues/{issue_id}", json=body
        )

    @mcp.tool()
    def superpos_transition_issue(
        issue_id: str,
        to: str,
        reason: str | None = None,
        hive_id: str | None = None,
    ) -> dict[str, Any]:
        """Move an issue to a new state: open, in_progress, blocked,
        awaiting_review, done, or cancelled. Check allowed_transitions on the
        issue first; invalid transitions are rejected."""
        body: dict[str, Any] = {"to": to}
        if reason:
            body["reason"] = reason
        return api.request(
            "POST",
            f"/api/v1/hives/{api.hive(hive_id)}/issues/{issue_id}/transition",
            json=body,
        )

    @mcp.tool()
    def superpos_close_issue(
        issue_id: str,
        reason: str | None = None,
        hive_id: str | None = None,
    ) -> dict[str, Any]:
        """Close an issue as done, optionally recording a closure reason. Subject to
        the issue type's closure policy."""
        body: dict[str, Any] = {}
        if reason:
            body["reason"] = reason
        return api.request(
            "POST",
            f"/api/v1/hives/{api.hive(hive_id)}/issues/{issue_id}/close",
            json=body or None,
        )

    @mcp.tool()
    def superpos_list_issue_types(hive_id: str | None = None) -> list[dict[str, Any]]:
        """List the hive's issue-type catalogue (id, key, label, closure_policy).
        Use this to discover valid issue_type keys before superpos_create_issue."""
        return api.request(
            "GET", f"/api/v1/hives/{api.hive(hive_id)}/issue-types"
        ) or []

    @mcp.tool()
    def superpos_link_task_to_issue(
        issue_id: str,
        task_id: str,
        hive_id: str | None = None,
    ) -> dict[str, Any]:
        """Attach an existing task to an issue so the work stays traceable — e.g.
        when you spawn a subtask (superpos_create_task) to resolve the issue."""
        return api.request(
            "POST",
            f"/api/v1/hives/{api.hive(hive_id)}/issues/{issue_id}/link-task",
            json={"task_id": task_id},
        )

    @mcp.tool()
    def superpos_request_issue_approval(
        issue_id: str,
        summary: str,
        recommended_action: str | None = None,
        risks: list[str] | None = None,
        hive_id: str | None = None,
    ) -> dict[str, Any]:
        """Escalate an issue for human approval (e.g. before closing risky work).
        Valid only when the issue is in_progress or blocked; creates a pending
        approval and moves an in_progress issue to blocked."""
        body: dict[str, Any] = {"summary": summary}
        if recommended_action:
            body["recommended_action"] = recommended_action
        if risks:
            body["risks"] = risks
        return api.request(
            "POST",
            f"/api/v1/hives/{api.hive(hive_id)}/issues/{issue_id}/request-approval",
            json=body,
        )

    @mcp.tool()
    def superpos_add_issue_dependency(
        issue_id: str,
        depends_on_issue_id: str,
        kind: str = "blocks",
        hive_id: str | None = None,
    ) -> dict[str, Any]:
        """Declare that this issue depends on another. kind is the relationship
        (e.g. 'blocks', 'related_to'). Returns the created dependency row."""
        return api.request(
            "POST",
            f"/api/v1/hives/{api.hive(hive_id)}/issues/{issue_id}/dependencies",
            json={"depends_on_issue_id": depends_on_issue_id, "kind": kind},
        )

    @mcp.tool()
    def superpos_remove_issue_dependency(
        issue_id: str,
        dependency_id: str,
        hive_id: str | None = None,
    ) -> dict[str, Any]:
        """Drop a dependency row by its id (visible under dependencies[] on the
        issue from superpos_get_issue)."""
        api.request(
            "DELETE",
            f"/api/v1/hives/{api.hive(hive_id)}/issues/{issue_id}/dependencies/{dependency_id}",
        )
        return {"removed": True, "issue_id": issue_id, "dependency_id": dependency_id}

    # ------------------------------------------------------------------
    # Tracks (multi-issue containers)
    # ------------------------------------------------------------------

    @mcp.tool()
    def superpos_list_tracks(hive_id: str | None = None) -> list[dict[str, Any]]:
        """List tracks — first-class containers that group related issues into a
        body of work (like an epic). States: planning, active, paused, done,
        archived."""
        return api.request("GET", f"/api/v1/hives/{api.hive(hive_id)}/tracks") or []

    @mcp.tool()
    def superpos_get_track(
        slug: str,
        include_issues: bool = True,
        hive_id: str | None = None,
    ) -> dict[str, Any]:
        """Get a track by slug, including its full spec document and (by default)
        the issues linked to it."""
        track = api.request("GET", f"/api/v1/hives/{api.hive(hive_id)}/tracks/{slug}")
        if include_issues:
            track["issues"] = api.request(
                "GET",
                f"/api/v1/hives/{api.hive(hive_id)}/tracks/{slug}/issues",
                params={"per_page": 100},
            ) or []
        return track

    @mcp.tool()
    def superpos_create_track(
        slug: str,
        name: str,
        description: str | None = None,
        spec: str | None = None,
        state: str | None = None,
        hive_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a track. slug is a lowercase kebab-case identifier (e.g.
        'agent-hive-awareness'); spec is an optional long-form plan document
        (markdown)."""
        body: dict[str, Any] = {"slug": slug, "name": name}
        if description is not None:
            body["description"] = description
        if spec is not None:
            body["spec"] = spec
        if state:
            body["state"] = state
        return api.request("POST", f"/api/v1/hives/{api.hive(hive_id)}/tracks", json=body)

    @mcp.tool()
    def superpos_update_track(
        slug: str,
        name: str | None = None,
        description: str | None = None,
        spec: str | None = None,
        hive_id: str | None = None,
    ) -> dict[str, Any]:
        """Update a track's name, description, or spec document (only the fields
        provided). Use superpos_transition_track to change state."""
        body: dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        if description is not None:
            body["description"] = description
        if spec is not None:
            body["spec"] = spec
        return api.request(
            "PATCH", f"/api/v1/hives/{api.hive(hive_id)}/tracks/{slug}", json=body
        )

    @mcp.tool()
    def superpos_transition_track(
        slug: str,
        to: str,
        reason: str | None = None,
        hive_id: str | None = None,
    ) -> dict[str, Any]:
        """Move a track to a new state: planning, active, paused, done, or
        archived."""
        body: dict[str, Any] = {"to": to}
        if reason:
            body["reason"] = reason
        return api.request(
            "POST", f"/api/v1/hives/{api.hive(hive_id)}/tracks/{slug}/transition", json=body
        )

    @mcp.tool()
    def superpos_link_issue(
        track_slug: str,
        issue_id: str,
        hive_id: str | None = None,
    ) -> dict[str, Any]:
        """Link an existing issue to a track so it shows up in the track's body of
        work."""
        return api.request(
            "POST",
            f"/api/v1/hives/{api.hive(hive_id)}/tracks/{track_slug}/issues",
            json={"issue_id": issue_id},
        )

    @mcp.tool()
    def superpos_unlink_issue(
        track_slug: str,
        issue_id: str,
        hive_id: str | None = None,
    ) -> dict[str, Any]:
        """Remove an issue from a track (the issue itself is not deleted)."""
        api.request(
            "DELETE",
            f"/api/v1/hives/{api.hive(hive_id)}/tracks/{track_slug}/issues/{issue_id}",
        )
        return {"unlinked": True, "track_slug": track_slug, "issue_id": issue_id}

    # ------------------------------------------------------------------
    # Topology
    # ------------------------------------------------------------------

    @mcp.tool()
    def superpos_hive_map(
        timeframe: str = "24h",
        hive_id: str | None = None,
    ) -> dict[str, Any]:
        """Point-in-time map of the hive graph for drill-down: nodes (agents,
        services, inboxes, schedules) and edges (task flow, proxy access, inbox and
        schedule triggers, cross-hive links). timeframe bounds activity-derived
        edges, e.g. '1h', '24h', '7d'."""
        return api.request(
            "GET",
            f"/api/v1/hives/{api.hive(hive_id)}/topology",
            params={"timeframe": timeframe},
        )

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    @mcp.tool()
    def superpos_list_hives() -> list[dict[str, Any]]:
        """List hives (workspaces) this agent's organization has access to."""
        return api.request("GET", "/api/v1/hives") or []

    @mcp.tool()
    def superpos_hive_agents(hive_id: str | None = None) -> list[dict[str, Any]]:
        """List sibling agents in the hive with their status and capabilities —
        useful before routing a task with target_capability or target_agent_id."""
        return api.request("GET", f"/api/v1/hives/{api.hive(hive_id)}/agents") or []

    # ------------------------------------------------------------------
    # Persona / memory
    # ------------------------------------------------------------------

    @mcp.tool()
    def superpos_get_persona() -> dict[str, Any]:
        """Fetch this agent's assembled persona/system prompt from Superpos (SOUL,
        RULES, STYLE, MEMORY, ... pre-assembled server-side)."""
        return api.request("GET", "/api/v1/persona/assembled")

    @mcp.tool()
    def superpos_update_memory(content: str, mode: str = "append") -> dict[str, Any]:
        """Write to this agent's persistent MEMORY document on Superpos. mode:
        append (default), prepend, or replace."""
        return api.request(
            "PATCH", "/api/v1/persona/memory", json={"content": content, "mode": mode}
        )

    return mcp


def run() -> None:
    """Run the stdio MCP server (entry point used by coding agents)."""
    create_server().run()
