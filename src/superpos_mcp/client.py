"""Thin HTTP client for the Superpos agent API.

Self-contained (httpx only) so the MCP server installs with no dependency on
the superpos-sdk package. Speaks the standard Superpos envelope
``{data, meta, errors}`` and transparently refreshes expired tokens.
"""

from __future__ import annotations

from typing import Any

import httpx

from .config import Config


class SuperposError(Exception):
    def __init__(self, message: str, status_code: int | None = None, errors: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.errors = errors


class SuperposApi:
    def __init__(self, config: Config):
        self.config = config
        self._http = httpx.Client(
            base_url=config.base_url,
            timeout=config.timeout,
            headers={"Accept": "application/json", "User-Agent": "superpos-mcp/0.1"},
        )

    # ------------------------------------------------------------------
    # Core request handling
    # ------------------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        _retry: bool = True,
    ) -> Any:
        if not self.config.token:
            self._acquire_token()

        headers = {"Authorization": f"Bearer {self.config.token}"} if self.config.token else {}
        response = self._http.request(method, path, json=json, params=params, headers=headers)

        if response.status_code == 401 and _retry:
            self._acquire_token(force=True)
            return self.request(method, path, json=json, params=params, _retry=False)

        if response.status_code == 204:
            return None

        try:
            body = response.json()
        except Exception:
            raise SuperposError(
                f"HTTP {response.status_code}: {response.text[:200]}",
                status_code=response.status_code,
            )

        if response.status_code >= 400:
            errors = body.get("errors") if isinstance(body, dict) else None
            message = body.get("message") if isinstance(body, dict) else None
            if not message and errors:
                first = errors[0] if isinstance(errors, list) and errors else errors
                message = first.get("message") if isinstance(first, dict) else str(first)
            raise SuperposError(
                message or f"HTTP {response.status_code}",
                status_code=response.status_code,
                errors=errors,
            )

        if isinstance(body, dict) and "data" in body:
            return body["data"]
        return body

    def _acquire_token(self, force: bool = False) -> None:
        """Get a usable access token: refresh first, then secret login."""
        cfg = self.config
        if cfg.refresh_token and cfg.agent_id:
            try:
                data = self._unauthenticated(
                    "POST",
                    "/api/v1/agents/token/refresh",
                    json={"agent_id": cfg.agent_id, "refresh_token": cfg.refresh_token},
                )
                cfg.save_tokens(data["token"], data.get("refresh_token"))
                return
            except SuperposError:
                pass  # fall through to secret login
        if cfg.agent_id and cfg.secret:
            data = self._unauthenticated(
                "POST",
                "/api/v1/agents/login",
                json={"agent_id": cfg.agent_id, "secret": cfg.secret},
            )
            cfg.save_tokens(data["token"], data.get("refresh_token"))
            return
        if force or not cfg.token:
            raise SuperposError(
                "Not authenticated and no way to re-authenticate. "
                "Run `superpos-mcp setup` or set SUPERPOS_TOKEN / "
                "SUPERPOS_AGENT_ID + SUPERPOS_AGENT_SECRET.",
                status_code=401,
            )

    def _unauthenticated(self, method: str, path: str, *, json: dict[str, Any]) -> Any:
        response = self._http.request(method, path, json=json)
        try:
            body = response.json()
        except Exception:
            raise SuperposError(
                f"HTTP {response.status_code}: {response.text[:200]}",
                status_code=response.status_code,
            )
        if response.status_code >= 400:
            raise SuperposError(
                (body.get("message") if isinstance(body, dict) else None)
                or f"HTTP {response.status_code}",
                status_code=response.status_code,
                errors=body.get("errors") if isinstance(body, dict) else None,
            )
        return body.get("data") if isinstance(body, dict) and "data" in body else body

    # ------------------------------------------------------------------
    # Auth / identity
    # ------------------------------------------------------------------

    def login(self, agent_id: str, secret: str) -> dict[str, Any]:
        data = self._unauthenticated(
            "POST", "/api/v1/agents/login", json={"agent_id": agent_id, "secret": secret}
        )
        self.config.agent_id = agent_id
        self.config.save_tokens(data["token"], data.get("refresh_token"))
        return data

    def register(
        self,
        name: str,
        hive_id: str,
        secret: str,
        *,
        agent_type: str = "custom",
        capabilities: list[str] | None = None,
        registration_token: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": name, "hive_id": hive_id, "secret": secret}
        if agent_type:
            payload["type"] = agent_type
        if capabilities:
            payload["capabilities"] = capabilities
        if registration_token:
            payload["registration_token"] = registration_token
        data = self._unauthenticated("POST", "/api/v1/agents/register", json=payload)
        self.config.agent_id = data["agent"]["id"]
        self.config.hive_id = hive_id
        self.config.save_tokens(data["token"], data.get("refresh_token"))
        return data

    def me(self) -> dict[str, Any]:
        return self.request("GET", "/api/v1/agents/me")

    def heartbeat(self, metadata: dict[str, Any] | None = None) -> Any:
        body = {"metadata": metadata} if metadata else None
        return self.request("POST", "/api/v1/agents/heartbeat", json=body)

    def update_status(self, status: str) -> Any:
        return self.request("PATCH", "/api/v1/agents/status", json={"status": status})

    # ------------------------------------------------------------------
    # Hive helpers
    # ------------------------------------------------------------------

    def hive(self, hive_id: str | None = None) -> str:
        resolved = hive_id or self.config.hive_id
        if not resolved:
            raise SuperposError(
                "No hive_id configured. Pass hive_id explicitly, run `superpos-mcp setup`, "
                "or set SUPERPOS_HIVE_ID."
            )
        return resolved
