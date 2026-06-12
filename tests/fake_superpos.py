"""A minimal in-process fake of the Superpos cloud API for testing.

Implements the envelope contract ({data, meta, errors}), bearer auth with
expiring tokens + refresh, and an in-memory task/knowledge/event store that
mirrors the real lifecycle semantics (claim conflicts, status transitions).
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class FakeState:
    def __init__(self) -> None:
        self.valid_tokens = {"good-token"}
        self.refresh_tokens = {"refresh-1": "agent-1"}
        self.agent = {
            "id": "agent-1",
            "name": "test-agent",
            "status": "online",
            "capabilities": ["code"],
        }
        self.secret = "s3cret-s3cret-s3cret"
        self.tasks: dict[str, dict[str, Any]] = {}
        self.knowledge: dict[str, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []
        self.schedules: dict[str, dict[str, Any]] = {}
        self.counter = 0
        self.requests: list[tuple[str, str]] = []  # (method, path) log
        self.last_registration_token: str | None = None

    def next_id(self, prefix: str) -> str:
        self.counter += 1
        return f"{prefix}-{self.counter}"


def make_handler(state: FakeState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # silence
            pass

        # -- helpers --------------------------------------------------
        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            return json.loads(self.rfile.read(length))

        def _send(self, status: int, data: Any = None, errors: Any = None, message: str | None = None):
            payload: dict[str, Any] = {}
            if errors is not None:
                payload["errors"] = errors
            if message is not None:
                payload["message"] = message
            if errors is None and message is None:
                payload = {"data": data, "meta": {}}
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authed(self) -> bool:
            auth = self.headers.get("Authorization", "")
            return auth.removeprefix("Bearer ") in state.valid_tokens

        # -- routing --------------------------------------------------
        def _route(self, method: str):
            path, _, query = self.path.partition("?")
            params = {}
            for pair in query.split("&"):
                if "=" in pair:
                    k, _, v = pair.partition("=")
                    params[k] = v
            state.requests.append((method, path))
            body = self._body() if method in ("POST", "PATCH", "PUT") else {}

            # ---- public auth endpoints ----
            if path == "/api/v1/agents/login" and method == "POST":
                if body.get("agent_id") == state.agent["id"] and body.get("secret") == state.secret:
                    token = state.next_id("tok")
                    state.valid_tokens.add(token)
                    return self._send(200, {"agent": state.agent, "token": token, "refresh_token": "refresh-1"})
                return self._send(401, errors=[{"code": "auth_failed", "message": "Invalid credentials."}])

            if path == "/api/v1/agents/register" and method == "POST":
                state.last_registration_token = body.get("registration_token")
                agent = {
                    "id": state.next_id("agent"),
                    "name": body["name"],
                    "status": "registered",
                    "capabilities": body.get("capabilities", []),
                }
                token = state.next_id("tok")
                state.valid_tokens.add(token)
                return self._send(201, {"agent": agent, "token": token, "refresh_token": "refresh-new"})

            if path == "/api/v1/agents/token/refresh" and method == "POST":
                if state.refresh_tokens.get(body.get("refresh_token")) == body.get("agent_id"):
                    token = state.next_id("tok")
                    state.valid_tokens.add(token)
                    return self._send(200, {"token": token, "refresh_token": "refresh-1"})
                return self._send(401, errors=[{"code": "auth_failed", "message": "Invalid refresh credentials."}])

            # ---- everything below requires auth ----
            if not self._authed():
                return self._send(401, errors=[{"code": "unauthenticated", "message": "Unauthenticated."}])

            if path == "/api/v1/agents/me" and method == "GET":
                return self._send(200, state.agent)
            if path == "/api/v1/agents/heartbeat" and method == "POST":
                return self._send(200, {**state.agent, "last_heartbeat_at": "now"})
            if path == "/api/v1/agents/status" and method == "PATCH":
                state.agent["status"] = body["status"]
                return self._send(200, state.agent)
            if path == "/api/v1/hives" and method == "GET":
                return self._send(200, [{"id": "hive-1", "name": "Test Hive"}])
            if path == "/api/v1/persona/assembled" and method == "GET":
                return self._send(200, {"assembled": "You are a helpful agent.", "version": 1})
            if path == "/api/v1/persona/memory" and method == "PATCH":
                return self._send(200, {"name": "MEMORY", "content": body["content"], "mode": body.get("mode")})

            # ---- hive-scoped ----
            m = re.match(r"^/api/v1/hives/([^/]+)/(.*)$", path)
            if m:
                hive, rest = m.group(1), m.group(2)
                return self._hive_route(method, hive, rest, body, params)

            return self._send(404, errors=[{"code": "not_found", "message": f"No route {method} {path}"}])

        def _hive_route(self, method, hive, rest, body, params):
            if rest == "tasks" and method == "POST":
                task = {
                    "id": state.next_id("task"),
                    "hive_id": hive,
                    "type": body["type"],
                    "status": "pending",
                    "payload": body.get("payload"),
                    "priority": body.get("priority", 5),
                    "invoke": body.get("invoke"),
                    "target_capability": body.get("target_capability"),
                }
                state.tasks[task["id"]] = task
                return self._send(201, task)

            if rest == "tasks/poll" and method == "GET":
                pending = [t for t in state.tasks.values() if t["status"] == "pending"]
                cap = params.get("capability")
                if cap:
                    pending = [t for t in pending if t.get("target_capability") in (None, cap)]
                return self._send(200, pending[: int(params.get("limit", 10))])

            tm = re.match(r"^tasks/([^/]+)(?:/(\w+))?$", rest)
            if tm:
                task_id, action = tm.group(1), tm.group(2)
                task = state.tasks.get(task_id)
                if not task:
                    return self._send(404, errors=[{"code": "not_found", "message": "Task not found."}])
                if action is None and method == "GET":
                    return self._send(200, task)
                if action == "claim" and method == "PATCH":
                    if task["status"] != "pending":
                        return self._send(409, errors=[{"code": "conflict", "message": "Task already claimed."}])
                    task.update(status="in_progress", claimed_by=state.agent["id"])
                    return self._send(200, task)
                if action == "progress" and method == "PATCH":
                    task["progress"] = body["progress"]
                    return self._send(200, task)
                if action == "complete" and method == "PATCH":
                    task.update(status="completed", result=body.get("result"))
                    return self._send(200, task)
                if action == "fail" and method == "PATCH":
                    task.update(status="failed", error=body.get("error"))
                    return self._send(200, task)

            if rest == "events" and method == "POST":
                event = {
                    "id": state.next_id("evt"),
                    "type": body["type"],
                    "payload": body.get("payload"),
                    "seq": len(state.events) + 1,
                }
                state.events.append(event)
                return self._send(201, event)
            if rest == "events/poll" and method == "GET":
                cursor = int(params.get("cursor", 0))
                return self._send(200, [e for e in state.events if e["seq"] > cursor])

            if rest == "knowledge" and method == "POST":
                entry = {
                    "id": state.next_id("kn"),
                    "key": body["key"],
                    "value": body["value"],
                    "scope": body.get("scope", "hive"),
                    "version": 1,
                }
                state.knowledge[entry["id"]] = entry
                return self._send(201, entry)
            if rest == "knowledge" and method == "GET":
                entries = list(state.knowledge.values())
                if params.get("key"):
                    entries = [e for e in entries if e["key"].startswith(params["key"])]
                return self._send(200, entries)
            if rest == "knowledge/search" and method == "GET":
                q = params.get("q", "").lower().replace("+", " ").replace("%20", " ")
                hits = [
                    {**e, "score": 0.9}
                    for e in state.knowledge.values()
                    if q in e["key"].lower() or q in json.dumps(e["value"]).lower()
                ]
                return self._send(200, hits)
            km = re.match(r"^knowledge/([^/]+)$", rest)
            if km:
                entry = state.knowledge.get(km.group(1))
                if not entry:
                    return self._send(404, errors=[{"code": "not_found", "message": "Entry not found."}])
                if method == "GET":
                    return self._send(200, entry)
                if method == "PUT":
                    entry["value"] = body["value"]
                    entry["version"] += 1
                    return self._send(200, entry)

            if rest == "schedules" and method == "GET":
                return self._send(200, list(state.schedules.values()))
            if rest == "schedules" and method == "POST":
                schedule = {"id": state.next_id("sched"), "status": "active", **body}
                state.schedules[schedule["id"]] = schedule
                return self._send(201, schedule)
            sm = re.match(r"^schedules/([^/]+)$", rest)
            if sm and method == "DELETE":
                state.schedules.pop(sm.group(1), None)
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return None

            if rest == "agents" and method == "GET":
                return self._send(200, [state.agent])

            return self._send(404, errors=[{"code": "not_found", "message": f"No hive route {method} {rest}"}])

        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

        def do_PATCH(self):
            self._route("PATCH")

        def do_PUT(self):
            self._route("PUT")

        def do_DELETE(self):
            self._route("DELETE")

    return Handler


class FakeSuperpos:
    """Context manager that runs the fake API on an ephemeral local port."""

    def __init__(self) -> None:
        self.state = FakeState()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.state))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self) -> FakeSuperpos:
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()


if __name__ == "__main__":
    # Standalone demo mode: a fake Superpos cloud on a fixed port.
    import sys

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8089
    state = FakeState()
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(state))
    print(f"Fake Superpos cloud running at http://127.0.0.1:{port}")
    print("  hive_id : hive-1")
    print("  token   : good-token")
    print("  login   : agent-1 / s3cret-s3cret-s3cret")
    print("Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
