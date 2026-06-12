import pytest
from fake_superpos import FakeSuperpos


@pytest.fixture()
def fake():
    with FakeSuperpos() as server:
        yield server


@pytest.fixture(autouse=True)
def isolated_credentials(tmp_path, monkeypatch):
    """Keep every test away from the real ~/.config/superpos and env vars."""
    monkeypatch.setenv("SUPERPOS_CREDENTIALS_FILE", str(tmp_path / "credentials.json"))
    for var in (
        "SUPERPOS_BASE_URL", "APIARY_BASE_URL",
        "SUPERPOS_TOKEN", "APIARY_API_TOKEN", "APIARY_TOKEN",
        "SUPERPOS_AGENT_REFRESH_TOKEN", "APIARY_REFRESH_TOKEN",
        "SUPERPOS_HIVE_ID", "APIARY_HIVE_ID",
        "SUPERPOS_AGENT_ID", "APIARY_AGENT_ID",
        "SUPERPOS_AGENT_SECRET", "APIARY_AGENT_SECRET",
    ):
        monkeypatch.delenv(var, raising=False)
    yield
