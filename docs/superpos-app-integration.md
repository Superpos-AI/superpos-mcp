# Integration with superpos-app

## No backend changes required for v0.1

superpos-mcp works entirely against the existing agent API surface:

- `POST /api/v1/agents/register` / `login` / `token/refresh` for onboarding and
  token rotation (used by `superpos-mcp setup` and the transparent 401-refresh
  path in the client).
- The standard hive-scoped task / knowledge / event / schedule endpoints for
  the tool surface.
- `GET /api/v1/persona/assembled` and `PATCH /api/v1/persona/memory` for
  persona/memory.

It follows the same conventions as the official SDKs: `{data, meta, errors}`
envelope, bearer auth, `SUPERPOS_*` env vars with `APIARY_*` fallbacks, and the
`~/.config/superpos/` credentials directory.

## Proposed PR #1 — adopt into the SDK sync pipeline

Move this package to `superpos-app/sdk/mcp/` so it becomes part of the source
of truth and flows to the public mirror through the existing
`sync-public-sdk-docs.yml` workflow, alongside `sdk/python/`, `sdk/shell/`, and
`sdk/openclaw/`. Then publish to PyPI as `superpos-mcp` so
`uv tool install superpos-mcp` / `install.sh` work for end users.

Checklist for that PR:

1. `git mv` this tree to `superpos-app/sdk/mcp/` (drop `.venv`, keep tests).
2. Add `sdk/mcp` to `scripts/sync-public-sdk.sh` rsync list.
3. Add a `docs/guide/mcp-server.md` guide (the README here is a good base).
4. CI job: `uv run pytest` for `sdk/mcp` (tests are hermetic — they run against
   an in-process fake API, no database or services needed).
5. PyPI publish workflow (trusted publishing) on tag.

## PR #2 — registration tokens + secured registration (DONE)

Branch `feat/agent-registration-tokens` on `superpos-app`. This closes a real
security gap found while connecting to the live cloud:

**The gap (verified on superpos.io, 2026-06-11):** the public
`POST /api/v1/agents/register` endpoint was unauthenticated and ungated —
anyone who learned a hive's 26-char ULID could self-register an agent into it,
with no rate limit. Hive IDs are not secret (they sit in worker `.env` files,
URLs, and logs). A self-registered agent got *no* permissions, so the immediate
blast radius was limited, but the door was open and would swing wide the moment
defaults were granted on registration.

**The fix (this PR):**

- New `agent_registration_tokens` table + `AgentRegistrationToken` model.
  Tokens are hive-scoped, expiring, optionally multi-use, and stored as a
  SHA-256 hash (plaintext shown once at mint).
- `POST /api/v1/agents/register` now requires a valid registration token when
  `platform.agent_registration.require_token` is on (the default). The token is
  re-checked and atomically redeemed under a row lock to close the
  validate→redeem race.
- Registering with a token grants the configured `default_permissions`
  (same set the dashboard grants), so agents are immediately useful — fixing
  the no-permissions gap *and* the open-registration gap together.
- `/register` and `/login` are now IP rate-limited (`throttle:10,1` /
  `throttle:20,1`).
- Dashboard endpoints to mint / list / revoke tokens
  (`AgentRegistrationTokenController`, web-session authed).
- `require_token=false` preserves the old open-registration behaviour for
  trusted single-tenant CE deployments.

**superpos-mcp side:** `superpos-mcp setup --register --registration-token <T>`
forwards the token; a missing/invalid token surfaces a clear hint pointing at
the dashboard.

Remaining follow-up (not in this PR): the React dashboard UI for minting tokens
(the backend endpoints are ready for it).

## Proposed PR #3 (nice-to-have) — one-click onboarding code

Registration tokens already make onboarding safe; this is the UX polish on top.

- Dashboard button "Connect a coding agent" issues a short-lived (10 min)
  one-time *setup code* that bundles hive + a single-use registration token.
- New endpoint `POST /api/v1/agents/onboard` exchanges `{setup_code, name}` for
  `{agent, token, refresh_token, hive_id}`.
- CLI becomes `superpos-mcp setup --code ABCD-1234` — no hive ID or token to
  copy by hand.
