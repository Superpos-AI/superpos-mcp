import pytest

from superpos_mcp.client import SuperposApi, SuperposError
from superpos_mcp.config import Config, read_credentials


def make_config(fake, **overrides):
    defaults = dict(base_url=fake.base_url, token="good-token", hive_id="hive-1")
    defaults.update(overrides)
    return Config(**defaults)


def test_envelope_unwrap(fake):
    api = SuperposApi(make_config(fake))
    agent = api.me()
    assert agent["id"] == "agent-1"


def test_login_persists_tokens(fake):
    api = SuperposApi(Config(base_url=fake.base_url))
    result = api.login("agent-1", "s3cret-s3cret-s3cret")
    assert result["token"].startswith("tok-")
    stored = read_credentials()
    assert stored["token"] == result["token"]
    assert stored["refresh_token"] == "refresh-1"


def test_bad_login_raises(fake):
    api = SuperposApi(Config(base_url=fake.base_url))
    with pytest.raises(SuperposError) as exc:
        api.login("agent-1", "wrong")
    assert exc.value.status_code == 401


def test_auto_refresh_on_expired_token(fake):
    cfg = make_config(fake, token="expired-token", refresh_token="refresh-1", agent_id="agent-1")
    api = SuperposApi(cfg)
    agent = api.me()  # 401 → refresh → retry
    assert agent["id"] == "agent-1"
    assert cfg.token.startswith("tok-")


def test_secret_login_fallback_when_refresh_invalid(fake):
    cfg = make_config(
        fake,
        token="expired-token",
        refresh_token="stale-refresh",
        agent_id="agent-1",
        secret="s3cret-s3cret-s3cret",
    )
    api = SuperposApi(cfg)
    assert api.me()["id"] == "agent-1"


def test_unauthenticated_with_no_recovery_raises(fake):
    api = SuperposApi(make_config(fake, token="expired-token"))
    with pytest.raises(SuperposError) as exc:
        api.me()
    assert exc.value.status_code == 401
    assert "superpos-mcp setup" in str(exc.value)


def test_hive_resolution_error():
    api = SuperposApi(Config(base_url="http://unused", token="t"))
    with pytest.raises(SuperposError, match="hive_id"):
        api.hive(None)
    assert api.hive("explicit") == "explicit"


def test_error_message_from_envelope(fake):
    api = SuperposApi(make_config(fake))
    with pytest.raises(SuperposError, match="Task not found"):
        api.request("GET", "/api/v1/hives/hive-1/tasks/nope")
