import json
from pathlib import Path

from superpos_mcp import cli
from superpos_mcp.config import read_credentials


def test_setup_login_writes_credentials(fake, capsys):
    rc = cli.main([
        "setup",
        "--base-url", fake.base_url,
        "--agent-id", "agent-1",
        "--secret", "s3cret-s3cret-s3cret",
        "--hive", "hive-1",
    ])
    assert rc == 0
    stored = read_credentials()
    assert stored["base_url"] == fake.base_url
    assert stored["hive_id"] == "hive-1"
    assert stored["token"].startswith("tok-")
    assert stored["refresh_token"] == "refresh-1"
    assert "Logged in" in capsys.readouterr().out


def test_setup_register_creates_agent(fake, capsys):
    rc = cli.main([
        "setup", "--register",
        "--base-url", fake.base_url,
        "--name", "fresh-agent",
        "--hive", "hive-1",
        "--capabilities", "code,review",
    ])
    assert rc == 0
    stored = read_credentials()
    assert stored["agent_id"].startswith("agent-")
    assert len(stored["secret"]) >= 16
    assert "Registered agent 'fresh-agent'" in capsys.readouterr().out


def test_setup_register_forwards_registration_token(fake, capsys):
    rc = cli.main([
        "setup", "--register",
        "--base-url", fake.base_url,
        "--name", "token-agent",
        "--hive", "hive-1",
        "--registration-token", "srt_test-token-value",
    ])
    assert rc == 0
    assert fake.state.last_registration_token == "srt_test-token-value"


def test_setup_bad_login_fails(fake, capsys):
    rc = cli.main([
        "setup",
        "--base-url", fake.base_url,
        "--agent-id", "agent-1",
        "--secret", "wrong-secret-wrong",
        "--hive", "hive-1",
    ])
    assert rc == 1
    assert "login failed" in capsys.readouterr().err


def test_install_writes_cursor_and_gemini_configs(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    rc = cli.main(["install", "cursor", "gemini", "windsurf"])
    assert rc == 0

    cursor = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
    assert "superpos" in cursor["mcpServers"]
    assert cursor["mcpServers"]["superpos"]["args"][-1] == "serve"

    gemini = json.loads((tmp_path / ".gemini" / "settings.json").read_text())
    assert "superpos" in gemini["mcpServers"]

    windsurf = json.loads((tmp_path / ".codeium" / "windsurf" / "mcp_config.json").read_text())
    assert "superpos" in windsurf["mcpServers"]


def test_install_merges_existing_cursor_config(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    cursor_path = tmp_path / ".cursor" / "mcp.json"
    cursor_path.parent.mkdir(parents=True)
    cursor_path.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))

    assert cli.main(["install", "cursor"]) == 0
    config = json.loads(cursor_path.read_text())
    assert set(config["mcpServers"]) == {"other", "superpos"}


def test_install_codex_appends_toml(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    assert cli.main(["install", "codex"]) == 0
    content = (tmp_path / ".codex" / "config.toml").read_text()
    assert "[mcp_servers.superpos]" in content
    # Idempotent: second run doesn't duplicate.
    assert cli.main(["install", "codex"]) == 0
    assert content.count("[mcp_servers.superpos]") == 1


def test_install_print_outputs_snippet(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert cli.main(["install", "print"]) == 0
    out = capsys.readouterr().out
    snippet = json.loads(out[out.index("{"):out.rindex("}") + 1])
    assert "superpos" in snippet["mcpServers"]


def test_install_unknown_target_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert cli.main(["install", "emacs"]) == 1
    assert "unknown target" in capsys.readouterr().out


def test_doctor_ok(fake, capsys):
    cli.main([
        "setup",
        "--base-url", fake.base_url,
        "--agent-id", "agent-1",
        "--secret", "s3cret-s3cret-s3cret",
        "--hive", "hive-1",
    ])
    rc = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "✓ Connected as test-agent" in out


def test_doctor_reports_missing(capsys):
    rc = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "missing" in out
