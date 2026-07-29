# Omnigent (Agent Meta-Harness)

Optional extension that runs the [Omnigent](https://github.com/omnigent-ai/omnigent)
**server** — a coordinator for sandboxed sessions of CLI coding agents
(Claude Code, Codex, OpenCode, Cursor, Pi) with a uniform API, stateful
policies (cost budgets, permissions), and live session sharing.

## What this extension does — and does not — give you

- **Enabled by the extension:** the Omnigent server + web UI on
  `http://localhost:6767` (SQLite lite tier, localhost-bound, auth on).
- **Not enabled by the extension:** agent execution. Omnigent runners
  install **on the host** and register against the server — the server
  image deliberately ships no harness SDKs, no tmux, and no LLM API
  keys. Full walkthrough: [docs/OMNIGENT.md](../../../docs/OMNIGENT.md).

## Quickstart

```bash
ods enable omnigent
# generate a stable session-cookie secret (logins survive restarts):
echo "OMNIGENT_ACCOUNTS_COOKIE_SECRET=$(openssl rand -hex 32)" >> .env
ods restart omnigent
# then open http://localhost:6767 — first account created becomes admin
```

Then install a runner on the host and point its harness at the local
LLM gateway (LiteLLM `http://localhost:4000/v1` or llama-server
`/v1` with a dummy API key) — see [docs/OMNIGENT.md](../../../docs/OMNIGENT.md).

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `OMNIGENT_PORT` | `6767` | Host port for server + web UI (internal 8000) |
| `OMNIGENT_IMAGE_TAG` | `v0.5.1` | Pinned upstream image tag — bump deliberately |
| `OMNIGENT_AUTH_ENABLED` | `1` | Built-in accounts auth; `0` = single-user local mode (localhost only) |
| `OMNIGENT_ACCOUNTS_COOKIE_SECRET` | _(empty)_ | Set via `openssl rand -hex 32` for stable sessions |

## Governance caveat

Agents launched through Omnigent are governed by Omnigent's **session**
policies (budgets, permissions) but do **not** flow through APE's
tool-call audit log yet. If APE auditing is part of your threat model,
treat Omnigent-launched agents accordingly. Tracked in Osmantic/ODS#1867.

## State

All state lives in `data/omnigent/` (SQLite DB, artifacts, account
data). Remove the directory for a factory reset.
