"""Manual smoke test: real stdio transport against the fake Superpos API.

Run with: uv run python tests/smoke_stdio.py
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from fake_superpos import FakeSuperpos
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    with FakeSuperpos() as fake:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "superpos_mcp", "serve"],
            env={
                **os.environ,
                "SUPERPOS_BASE_URL": fake.base_url,
                "SUPERPOS_TOKEN": "good-token",
                "SUPERPOS_HIVE_ID": "hive-1",
                "SUPERPOS_CREDENTIALS_FILE": "/tmp/superpos-smoke-nonexistent.json",
            },
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                print(f"tools over stdio: {len(tools.tools)}")

                result = await session.call_tool("superpos_whoami", {})
                payload = json.loads(result.content[0].text)
                assert payload["connected"] is True, payload
                print(f"whoami: connected as {payload['agent']['name']}")

                result = await session.call_tool(
                    "superpos_create_task",
                    {"task_type": "demo", "instructions": "say hello"},
                )
                task = json.loads(result.content[0].text)
                result = await session.call_tool("superpos_claim_task", {"task_id": task["id"]})
                result = await session.call_tool(
                    "superpos_complete_task",
                    {"task_id": task["id"], "result": {"hello": "world"}},
                )
                done = json.loads(result.content[0].text)
                assert done["status"] == "completed", done
                print(f"task lifecycle over stdio: {task['id']} -> {done['status']}")

    print("SMOKE OK")


if __name__ == "__main__":
    asyncio.run(main())
