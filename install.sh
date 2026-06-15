#!/usr/bin/env bash
# superpos-mcp installer — connects any MCP-capable coding agent to Superpos cloud.
#
#   curl -fsSL https://raw.githubusercontent.com/Superpos-AI/superpos-mcp/main/install.sh | bash
#
# Installs from the GitHub repo by default so a fresh install works before the
# package is on PyPI. Override the source with a PyPI name, git URL, or local path:
#   SUPERPOS_MCP_SOURCE=superpos-mcp bash install.sh                 # once published to PyPI
#   SUPERPOS_MCP_SOURCE=/path/to/superpos-mcp bash install.sh        # local checkout
set -euo pipefail

SOURCE="${SUPERPOS_MCP_SOURCE:-git+https://github.com/Superpos-AI/superpos-mcp.git}"

say() { printf '\033[1;36m[superpos-mcp]\033[0m %s\n' "$*"; }

# --- 1. Install the package with whatever Python tooling is available -------
if command -v uv >/dev/null 2>&1; then
    say "installing via uv tool install"
    uv tool install --force "$SOURCE"
elif command -v pipx >/dev/null 2>&1; then
    say "installing via pipx"
    pipx install --force "$SOURCE"
elif command -v pip3 >/dev/null 2>&1; then
    say "installing via pip3 --user"
    pip3 install --user --upgrade "$SOURCE"
else
    say "error: need uv, pipx, or pip3 to install. Get uv: https://docs.astral.sh/uv/"
    exit 1
fi

if ! command -v superpos-mcp >/dev/null 2>&1; then
    say "warning: superpos-mcp is installed but not on PATH yet."
    say "         Open a new shell, or add your Python bin directory to PATH."
fi

# --- 2. Register with detected coding agents --------------------------------
say "registering MCP server with detected coding agents"
superpos-mcp install auto || true

# --- 3. Point at the cloud workspace -----------------------------------------
if [ -t 0 ]; then
    say "connecting to your Superpos workspace (Ctrl-C to skip)"
    superpos-mcp setup || true
else
    say "next step: run 'superpos-mcp setup' to connect to your Superpos workspace"
fi

say "done. Verify from any agent by asking it to call superpos_whoami."
