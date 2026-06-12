"""superpos-mcp command line interface.

Commands:
  serve     Run the stdio MCP server (what coding agents invoke). Default.
  setup     Authenticate against a Superpos cloud instance and persist credentials.
  install   Register the MCP server with coding agents (claude, codex, cursor,
            gemini, windsurf) or print a generic config snippet.
  doctor    Show resolved configuration and test connectivity.
"""

from __future__ import annotations

import argparse
import json
import secrets as secrets_mod
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from .client import SuperposApi, SuperposError
from .config import (
    DEFAULT_CLOUD_BASE_URL,
    Config,
    credentials_path,
    read_credentials,
    write_credentials,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="superpos-mcp", description=__doc__)
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("serve", help="run the stdio MCP server (default)")

    p_setup = sub.add_parser("setup", help="authenticate and persist credentials")
    p_setup.add_argument("--base-url", help=f"Superpos API base URL (default {DEFAULT_CLOUD_BASE_URL})")
    p_setup.add_argument("--hive", dest="hive_id", help="default hive id for this agent")
    p_setup.add_argument("--agent-id", help="existing agent id (login mode)")
    p_setup.add_argument("--secret", help="agent secret (login mode, or to use when registering)")
    p_setup.add_argument("--token", help="pre-issued access token (skip login)")
    p_setup.add_argument("--register", action="store_true", help="register a new agent instead of logging in")
    p_setup.add_argument("--name", help="agent name (register mode)")
    p_setup.add_argument(
        "--registration-token",
        help="hive registration token (register mode) — minted in the Superpos dashboard",
    )
    p_setup.add_argument(
        "--capabilities", help="comma-separated capabilities for register mode (e.g. code,review)"
    )

    p_install = sub.add_parser("install", help="register this MCP server with coding agents")
    p_install.add_argument(
        "targets",
        nargs="*",
        default=["auto"],
        help="claude, codex, cursor, gemini, windsurf, print, or auto (default: detect installed agents)",
    )

    sub.add_parser("doctor", help="show resolved config and test connectivity")

    args = parser.parse_args(argv)
    command = args.command or "serve"

    if command == "serve":
        from .server import run

        run()
        return 0
    if command == "setup":
        return cmd_setup(args)
    if command == "install":
        return cmd_install(args.targets)
    if command == "doctor":
        return cmd_doctor()
    parser.error(f"unknown command {command}")
    return 2


# ----------------------------------------------------------------------
# setup
# ----------------------------------------------------------------------


def _prompt(label: str, default: str | None = None, required: bool = True) -> str | None:
    if not sys.stdin.isatty():
        return default
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"{label}{suffix}: ").strip() or (default or "")
        if value or not required:
            return value or None


def cmd_setup(args: argparse.Namespace) -> int:
    stored = read_credentials()
    base_url = (
        args.base_url
        or stored.get("base_url")
        or _prompt("Superpos base URL", DEFAULT_CLOUD_BASE_URL)
        or DEFAULT_CLOUD_BASE_URL
    ).rstrip("/")

    cfg = Config(base_url=base_url)
    api = SuperposApi(cfg)

    if args.token:
        # Pre-issued token: just persist and verify.
        cfg.token = args.token
        hive_id = args.hive_id or stored.get("hive_id") or _prompt("Hive ID")
        data: dict[str, Any] = {"base_url": base_url, "token": args.token, "hive_id": hive_id}
        try:
            agent = api.me()
            data["agent_id"] = agent.get("id")
        except SuperposError as exc:
            print(f"error: token rejected by {base_url}: {exc}", file=sys.stderr)
            return 1
        write_credentials({**stored, **data})
        print(f"✓ Authenticated as {agent.get('name', data['agent_id'])} — credentials saved to {credentials_path()}")
        return 0

    if args.register:
        name = args.name or _prompt("New agent name")
        hive_id = args.hive_id or stored.get("hive_id") or _prompt("Hive ID")
        if not name or not hive_id:
            print("error: --register needs --name and --hive", file=sys.stderr)
            return 1
        secret = args.secret or secrets_mod.token_urlsafe(24)
        capabilities = [c.strip() for c in (args.capabilities or "").split(",") if c.strip()]
        registration_token = args.registration_token or _prompt(
            "Registration token (from the Superpos dashboard)", required=False
        )
        try:
            result = api.register(
                name,
                hive_id,
                secret,
                capabilities=capabilities or None,
                registration_token=registration_token,
            )
        except SuperposError as exc:
            print(f"error: registration failed: {exc}", file=sys.stderr)
            if exc.status_code in (401, 422) and "token" in str(exc).lower():
                print(
                    "hint: this hive requires a registration token. Mint one in the "
                    "Superpos dashboard (Agents → registration tokens) and pass it via "
                    "--registration-token.",
                    file=sys.stderr,
                )
            return 1
        agent = result["agent"]
        write_credentials(
            {
                **stored,
                "base_url": base_url,
                "hive_id": hive_id,
                "agent_id": agent["id"],
                "secret": secret,
                "token": result["token"],
                "refresh_token": result.get("refresh_token"),
            }
        )
        print(f"✓ Registered agent '{name}' ({agent['id']}) in hive {hive_id}")
        print(f"  Credentials saved to {credentials_path()}")
        return 0

    # Login mode (default).
    agent_id = args.agent_id or stored.get("agent_id") or _prompt("Agent ID")
    secret = args.secret or stored.get("secret") or _prompt("Agent secret")
    hive_id = args.hive_id or stored.get("hive_id") or _prompt("Hive ID")
    if not agent_id or not secret:
        print(
            "error: need --agent-id and --secret (or --token, or --register --name --hive)",
            file=sys.stderr,
        )
        return 1
    try:
        result = api.login(agent_id, secret)
    except SuperposError as exc:
        print(f"error: login failed: {exc}", file=sys.stderr)
        return 1
    write_credentials(
        {
            **stored,
            "base_url": base_url,
            "hive_id": hive_id,
            "agent_id": agent_id,
            "secret": secret,
            "token": result["token"],
            "refresh_token": result.get("refresh_token"),
        }
    )
    name = (result.get("agent") or {}).get("name", agent_id)
    print(f"✓ Logged in as {name} — credentials saved to {credentials_path()}")
    if not hive_id:
        print("  note: no hive_id set; pass hive_id per tool call or re-run setup with --hive")
    return 0


# ----------------------------------------------------------------------
# install
# ----------------------------------------------------------------------


def _server_command() -> list[str]:
    """Command coding agents should run to start this server."""
    exe = shutil.which("superpos-mcp")
    if exe:
        return [exe, "serve"]
    # Fall back to invoking via the current interpreter.
    return [sys.executable, "-m", "superpos_mcp", "serve"]


def _merge_json_config(path: Path, command: list[str]) -> str:
    config = {}
    if path.exists():
        try:
            config = json.loads(path.read_text())
        except json.JSONDecodeError:
            return f"skipped {path} (existing file is not valid JSON)"
    servers = config.setdefault("mcpServers", {})
    servers["superpos"] = {"command": command[0], "args": command[1:]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n")
    return f"wrote {path}"


def install_claude(command: list[str]) -> str:
    claude = shutil.which("claude")
    if claude:
        result = subprocess.run(
            [claude, "mcp", "add", "--scope", "user", "superpos", "--", *command],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return "registered with Claude Code (claude mcp add, user scope)"
        return f"claude mcp add failed: {result.stderr.strip() or result.stdout.strip()}"
    return "claude CLI not found — run: claude mcp add --scope user superpos -- " + " ".join(command)


def install_codex(command: list[str]) -> str:
    codex = shutil.which("codex")
    if codex:
        result = subprocess.run(
            [codex, "mcp", "add", "superpos", "--", *command],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return "registered with Codex CLI (codex mcp add)"
    # Fall back to editing config.toml directly.
    path = Path.home() / ".codex" / "config.toml"
    existing = path.read_text() if path.exists() else ""
    if "[mcp_servers.superpos]" in existing:
        return f"already present in {path}"
    args_toml = ", ".join(json.dumps(a) for a in command[1:])
    block = (
        f"\n[mcp_servers.superpos]\n"
        f"command = {json.dumps(command[0])}\n"
        f"args = [{args_toml}]\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(existing + block)
    return f"wrote {path}"


def install_cursor(command: list[str]) -> str:
    return _merge_json_config(Path.home() / ".cursor" / "mcp.json", command)


def install_gemini(command: list[str]) -> str:
    return _merge_json_config(Path.home() / ".gemini" / "settings.json", command)


def install_windsurf(command: list[str]) -> str:
    return _merge_json_config(
        Path.home() / ".codeium" / "windsurf" / "mcp_config.json", command
    )


INSTALLERS = {
    "claude": install_claude,
    "codex": install_codex,
    "cursor": install_cursor,
    "gemini": install_gemini,
    "windsurf": install_windsurf,
}

DETECT = {
    "claude": lambda: shutil.which("claude") or (Path.home() / ".claude").exists(),
    "codex": lambda: shutil.which("codex") or (Path.home() / ".codex").exists(),
    "cursor": lambda: (Path.home() / ".cursor").exists(),
    "gemini": lambda: shutil.which("gemini") or (Path.home() / ".gemini").exists(),
    "windsurf": lambda: (Path.home() / ".codeium" / "windsurf").exists(),
}


def cmd_install(targets: list[str]) -> int:
    command = _server_command()

    if targets == ["auto"] or "auto" in targets:
        targets = [name for name, found in DETECT.items() if found()]
        if not targets:
            targets = ["print"]
        else:
            print(f"detected: {', '.join(targets)}")

    failed = False
    for target in targets:
        if target == "print":
            snippet = {"mcpServers": {"superpos": {"command": command[0], "args": command[1:]}}}
            print("Add this to your agent's MCP configuration:")
            print(json.dumps(snippet, indent=2))
            continue
        installer = INSTALLERS.get(target)
        if not installer:
            print(f"  {target}: unknown target (choose from {', '.join(INSTALLERS)}, print)")
            failed = True
            continue
        print(f"  {target}: {installer(command)}")

    if not read_credentials().get("token"):
        print("\nNext: run `superpos-mcp setup` to connect to your Superpos cloud workspace.")
    return 1 if failed else 0


# ----------------------------------------------------------------------
# doctor
# ----------------------------------------------------------------------


def _mask(value: str | None) -> str:
    if not value:
        return "(not set)"
    return value[:6] + "…" if len(value) > 8 else "***"


def cmd_doctor() -> int:
    cfg = Config.load()
    print(f"credentials file : {credentials_path()} ({'exists' if credentials_path().exists() else 'missing'})")
    print(f"base_url         : {cfg.base_url}")
    print(f"hive_id          : {cfg.hive_id or '(not set)'}")
    print(f"agent_id         : {cfg.agent_id or '(not set)'}")
    print(f"token            : {_mask(cfg.token)}")
    print(f"refresh_token    : {_mask(cfg.refresh_token)}")
    missing = cfg.missing()
    if missing:
        print(f"missing          : {', '.join(missing)}")
        print("\nRun `superpos-mcp setup` to fix.")
        return 1
    api = SuperposApi(cfg)
    try:
        agent = api.me()
    except SuperposError as exc:
        print(f"\n✗ API check failed: {exc}")
        return 1
    print(f"\n✓ Connected as {agent.get('name', cfg.agent_id)} (status: {agent.get('status', '?')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
