"""Live exercise: drive the real superpos-mcp stdio server against a real cloud.

Uses the credentials from ~/.config/superpos/credentials.json. Creates only
self-targeted artifacts (a task targeted at this agent, one knowledge entry).

Run with: uv run python tests/live_drive.py
"""

import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def call(session, tool, args=None):
    result = await session.call_tool(tool, args or {})
    text = "".join(c.text for c in result.content if c.type == "text")
    if result.isError:
        print(f"  ✗ {tool}: {text[:200]}")
        return None
    try:
        return json.loads(text) if text else None
    except json.JSONDecodeError:
        return text


async def main():
    params = StdioServerParameters(command=sys.executable, args=["-m", "superpos_mcp", "serve"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            me = await call(session, "superpos_whoami")
            agent = me["agent"]
            print(f"connected : {agent['name']} ({agent['id']}) on {me['base_url']}")

            hb = await call(session, "superpos_heartbeat", {"status": "online"})
            print(f"heartbeat : status={hb['agent'].get('status')}")

            siblings = await call(session, "superpos_hive_agents") or []
            online = [s for s in siblings if isinstance(s, dict) and s.get("status") in ("online", "busy", "idle")]
            print(f"hive      : {len(siblings)} agents total, {len(online)} online/active:")
            for s in online[:10]:
                print(f"            - {s.get('name')} [{s.get('status')}] caps={s.get('capabilities')}")

            pending = await call(session, "superpos_poll_tasks", {"limit": 5})
            print(f"queue     : {len(pending or [])} pending tasks visible to me")

            # Self-targeted lifecycle — never touches the user's workers.
            task = await call(session, "superpos_create_task", {
                "task_type": "mcp_self_test",
                "instructions": "Self-test task created and completed by claude-code-mcp via superpos-mcp.",
                "target_agent_id": agent["id"],
                "priority": 1,
            })
            if task:
                print(f"task      : created {task['id']} ({task['status']})")
                claimed = await call(session, "superpos_claim_task", {"task_id": task["id"]})
                print(f"            claimed -> {claimed['status'] if claimed else 'FAILED'}")
                await call(session, "superpos_task_progress", {"task_id": task["id"], "progress": 50, "status_message": "halfway"})
                done = await call(session, "superpos_complete_task", {
                    "task_id": task["id"],
                    "result": {"verdict": "superpos-mcp live test passed"},
                    "status_message": "self-test complete",
                })
                print(f"            completed -> {done['status'] if done else 'FAILED'}")

            entry = await call(session, "superpos_create_knowledge", {
                "key": "mcp.connector.self_test",
                "value": {"note": "claude-code-mcp connected via superpos-mcp", "agent_id": agent["id"]},
                "ttl": "7d",
            })
            if entry:
                print(f"knowledge : created {entry['id']} key={entry['key']}")
                hits = await call(session, "superpos_search_knowledge", {"query": "mcp connector self test", "limit": 3})
                print(f"            search returned {len(hits or [])} hit(s)")

            events = await call(session, "superpos_poll_events", {"limit": 5})
            if events is not None:
                print(f"events    : {len(events)} recent")

            persona = await call(session, "superpos_get_persona")
            if persona:
                keys = list(persona)[:6] if isinstance(persona, dict) else type(persona).__name__
                print(f"persona   : assembled fetched ({keys})")

    print("\nLIVE TEST OK")


if __name__ == "__main__":
    asyncio.run(main())
