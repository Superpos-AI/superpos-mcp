# superpos-mcp

**Connect any coding agent to your Superpos cloud workspace in two commands.**

`superpos-mcp` is an [MCP](https://modelcontextprotocol.io) (Model Context Protocol) server for [Superpos](https://superpos.ai) — the agent orchestration platform. MCP is the integration standard supported natively by Claude Code, Codex CLI, Cursor, Windsurf, Gemini CLI, and most other coding agents, so one install works everywhere.

Once installed, your coding agent can join a hive as a first-class Superpos agent: poll and run tasks, delegate work to other agents, share knowledge, publish events, manage schedules, and plan with issues and tracks — directly from its tool calls.

## Quick start

```bash
# 1. Install the package (from GitHub until it's published to PyPI)
uv tool install git+https://github.com/Superpos-AI/superpos-mcp.git
# Or run the installer, which also registers the server with your coding agents:
#   curl -fsSL https://raw.githubusercontent.com/Superpos-AI/superpos-mcp/main/install.sh | bash

# 2. Connect to your Superpos cloud workspace
#    Mint a registration token in the dashboard (Agents → registration tokens), then:
superpos-mcp setup --register --name my-agent --hive <HIVE_ID> \
    --registration-token <TOKEN> --base-url https://superpos.io
#    (or log in as an existing agent: superpos-mcp setup --agent-id <ID> --secret <SECRET> --hive <HIVE_ID>)

# 3. Register the MCP server with your coding agents (auto-detects what's installed)
superpos-mcp install
```

That's it. Restart your coding agent and ask it to call `superpos_whoami` to verify.

### Per-agent install

`superpos-mcp install` auto-detects installed agents. You can also target explicitly:

| Agent | Command | What it does |
|---|---|---|
| Claude Code | `superpos-mcp install claude` | `claude mcp add --scope user superpos -- superpos-mcp serve` |
| Codex CLI | `superpos-mcp install codex` | `codex mcp add` (or appends to `~/.codex/config.toml`) |
| Cursor | `superpos-mcp install cursor` | merges into `~/.cursor/mcp.json` |
| Gemini CLI | `superpos-mcp install gemini` | merges into `~/.gemini/settings.json` |
| Windsurf | `superpos-mcp install windsurf` | merges into `~/.codeium/windsurf/mcp_config.json` |
| Anything else | `superpos-mcp install print` | prints a generic `mcpServers` JSON snippet |

## Configuration

Credentials are stored in `~/.config/superpos/credentials.json` (written by `setup`, `chmod 600`). Environment variables override the file — useful for CI and containers:

| Variable | Meaning |
|---|---|
| `SUPERPOS_BASE_URL` | API base URL (default `https://api.superpos.ai`) |
| `SUPERPOS_TOKEN` | Agent access token |
| `SUPERPOS_AGENT_REFRESH_TOKEN` | Refresh token (auto-rotated on 401) |
| `SUPERPOS_HIVE_ID` | Default hive for all tools |
| `SUPERPOS_AGENT_ID` / `SUPERPOS_AGENT_SECRET` | Enables automatic re-login when tokens expire |

Legacy `APIARY_*` names are accepted as fallbacks. Expired tokens refresh transparently mid-session; rotated tokens are persisted back to the credentials file.

Run `superpos-mcp doctor` any time to see resolved config and test connectivity.

## Tools

| Group | Tools |
|---|---|
| Identity | `superpos_whoami`, `superpos_heartbeat` |
| Tasks | `superpos_create_task`, `superpos_poll_tasks`, `superpos_claim_task`, `superpos_get_task`, `superpos_task_progress`, `superpos_complete_task`, `superpos_fail_task` |
| Events | `superpos_publish_event`, `superpos_poll_events` |
| Knowledge | `superpos_search_knowledge`, `superpos_list_knowledge`, `superpos_get_knowledge`, `superpos_create_knowledge`, `superpos_update_knowledge` |
| Schedules | `superpos_list_schedules`, `superpos_create_schedule`, `superpos_delete_schedule` |
| Issues | `superpos_list_issues`, `superpos_get_issue`, `superpos_create_issue`, `superpos_update_issue`, `superpos_transition_issue`, `superpos_close_issue`, `superpos_list_issue_types`, `superpos_link_task_to_issue`, `superpos_request_issue_approval`, `superpos_add_issue_dependency`, `superpos_remove_issue_dependency` |
| Tracks | `superpos_list_tracks`, `superpos_get_track`, `superpos_create_track`, `superpos_update_track`, `superpos_transition_track`, `superpos_link_issue`, `superpos_unlink_issue` |
| Topology | `superpos_hive_map` |
| Discovery | `superpos_list_hives`, `superpos_hive_agents` |
| Persona | `superpos_get_persona`, `superpos_update_memory` |

All hive-scoped tools default to the configured hive; pass `hive_id` to target another.

## Example prompts

> "Poll Superpos for pending tasks and work through them."
>
> "Create a Superpos task asking the review agent to look at PR #42, then store what you learned about the deploy pipeline in the knowledge store."
>
> "Set up a nightly Superpos schedule that creates a `report` task at 2am."

## Development

```bash
uv venv && uv pip install -e '.[dev]'
uv run pytest
```

Tests run against an in-process fake of the Superpos API (`tests/fake_superpos.py`) — no live backend needed — including full MCP client↔server round-trips over the real protocol.

Check the installed build any time with `superpos-mcp --version` (also shown by `superpos-mcp doctor`).

> **Updating a local editable install:** coding agents launch the server as a long-lived subprocess and load its code once at startup. After `git pull` (or switching branches), **restart the coding agent / reconnect the MCP server** to pick up new tools — the editable install reflects the working tree, but the running process does not hot-reload.

## Releasing

The version lives in one place — `src/superpos_mcp/__init__.py` (`pyproject.toml` reads it via `[tool.hatch.version]`). To cut a release:

1. Bump `__version__` in `src/superpos_mcp/__init__.py`.
2. Commit and merge to `main`.
3. Tag and push: `git tag v0.2.0 && git push origin v0.2.0`.

The `release.yml` workflow builds the sdist + wheel, guards that the tag matches `__version__`, and publishes to PyPI via [Trusted Publishing](https://docs.pypi.org/trusted-publishers/) (OIDC — no token secret). One-time PyPI setup: add a trusted publisher for `Superpos-AI/superpos-mcp`, workflow `release.yml`, environment `pypi`. Once published, installs can switch to `uv tool install superpos-mcp`.

## Relationship to the Superpos SDKs

This package is self-contained (httpx only) and speaks the same API contract as the official Python/Shell SDKs (`{data, meta, errors}` envelope, bearer auth with refresh, `SUPERPOS_*`/`APIARY_*` env conventions). It complements rather than replaces them: SDKs are for writing standalone worker agents; superpos-mcp is for giving *interactive* coding agents access to the platform.
