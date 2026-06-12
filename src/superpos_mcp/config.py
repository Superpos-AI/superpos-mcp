"""Configuration resolution for superpos-mcp.

Resolution order (highest wins):
1. Environment variables — ``SUPERPOS_*`` with legacy ``APIARY_*`` fallbacks,
   matching the conventions of the official Superpos SDKs.
2. Credentials file written by ``superpos-mcp setup``
   (``~/.config/superpos/credentials.json`` by default).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ENV_ALIASES: dict[str, list[str]] = {
    "base_url": ["SUPERPOS_BASE_URL", "APIARY_BASE_URL"],
    "token": ["SUPERPOS_TOKEN", "APIARY_API_TOKEN", "APIARY_TOKEN"],
    "refresh_token": ["SUPERPOS_AGENT_REFRESH_TOKEN", "APIARY_REFRESH_TOKEN"],
    "hive_id": ["SUPERPOS_HIVE_ID", "APIARY_HIVE_ID"],
    "agent_id": ["SUPERPOS_AGENT_ID", "APIARY_AGENT_ID"],
    "secret": ["SUPERPOS_AGENT_SECRET", "APIARY_AGENT_SECRET"],
    "timeout": ["SUPERPOS_TIMEOUT", "APIARY_TIMEOUT"],
}

DEFAULT_CLOUD_BASE_URL = "https://api.superpos.ai"


def credentials_path() -> Path:
    override = os.environ.get("SUPERPOS_CREDENTIALS_FILE")
    if override:
        return Path(override)
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "superpos" / "credentials.json"


def _env(key: str) -> str | None:
    for name in ENV_ALIASES.get(key, []):
        value = os.environ.get(name)
        if value:
            return value
    return None


@dataclass
class Config:
    base_url: str = DEFAULT_CLOUD_BASE_URL
    token: str | None = None
    refresh_token: str | None = None
    hive_id: str | None = None
    agent_id: str | None = None
    secret: str | None = None
    timeout: float = 30.0
    # Which fields came from env vars (those are never persisted back).
    _from_env: set[str] = field(default_factory=set, repr=False)

    @classmethod
    def load(cls) -> Config:
        cfg = cls()
        stored = read_credentials()
        for key in ("base_url", "token", "refresh_token", "hive_id", "agent_id", "secret"):
            if stored.get(key):
                setattr(cfg, key, stored[key])
        if stored.get("timeout"):
            cfg.timeout = float(stored["timeout"])

        for key in ENV_ALIASES:
            value = _env(key)
            if value is None:
                continue
            cfg._from_env.add(key)
            if key == "timeout":
                cfg.timeout = float(value)
            else:
                setattr(cfg, key, value)
        cfg.base_url = cfg.base_url.rstrip("/")
        return cfg

    def save_tokens(self, token: str, refresh_token: str | None) -> None:
        """Persist rotated tokens unless the token was injected via env."""
        self.token = token
        if refresh_token:
            self.refresh_token = refresh_token
        if "token" in self._from_env:
            return
        stored = read_credentials()
        stored["token"] = token
        if refresh_token:
            stored["refresh_token"] = refresh_token
        write_credentials(stored)

    def missing(self) -> list[str]:
        """Names of settings still required before API calls can succeed."""
        gaps = []
        if not self.token and not (self.agent_id and self.secret):
            gaps.append("token (or agent_id + secret)")
        if not self.hive_id:
            gaps.append("hive_id")
        return gaps


def read_credentials() -> dict[str, Any]:
    path = credentials_path()
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def write_credentials(data: dict[str, Any]) -> None:
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    path.chmod(0o600)
