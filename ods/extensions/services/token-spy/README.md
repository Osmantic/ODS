# Token Spy

Authenticated LLM API proxy that captures per-turn token usage, cost, latency, and session health. It sits between your application and upstream providers (Anthropic, OpenAI, Moonshot, local models), logs every turn, and streams responses through without buffering.

## How It Works

```
Your agent -> Token Spy proxy -> Upstream API (Anthropic, OpenAI, etc.)
                  |
                  v
              SQLite DB <- Dashboard (charts, tables, settings)
                  ^
                  |
           Session Manager (polls every N minutes, enforces limits)
```

Point your agent's API base URL at Token Spy instead of the upstream provider. Clients authenticate to Token Spy with `TOKEN_SPY_API_KEY`. For external providers, Token Spy uses server-side `UPSTREAM_API_KEY` and never forwards its own Bearer token upstream. Local OpenAI-compatible backends such as llama-server or Ollama can still run without an upstream key.

In a ODS install, `TOKEN_SPY_API_KEY` is written to `.env` by the installer and passed to both Token Spy and dashboard-api. Older installs that already have `data/token-spy/token-spy-api-key.txt` keep that value on upgrade so existing clients do not lose access.

ODS model traffic that uses the stable `ods/current` route is reported by
model-router through the authenticated `/api/ingest/routed` endpoint. This is a
best-effort side channel, not a proxy hop: an unavailable or disabled Token Spy
never blocks inference. Routed events contain only model/backend identifiers,
request shape, token counters, duration, and stop reason. Prompt text,
generated content, tools, and credentials are not accepted by the ingest
schema.

## Features

- **Real-time dashboard** -- session health cards, cost charts, token breakdown, cumulative cost, recent turns table
- **Session health monitoring** -- detects context bloat, recommends resets, can auto-kill sessions exceeding configurable character limits
- **Multi-provider** -- Anthropic Messages API (`/v1/messages`) and OpenAI Chat Completions (`/v1/chat/completions`)
- **Dual database backends** -- SQLite (zero-config default) and PostgreSQL/TimescaleDB for production
- **Per-agent settings** -- configurable session limits and poll intervals, editable via dashboard or REST API
- **Local model support** -- track self-hosted models (vLLM, Ollama) with $0 cost badges

## Standalone Usage

```bash
cd token-spy
pip install -r requirements.txt
cp .env.example .env
# Edit .env -- at minimum set AGENT_NAME and TOKEN_SPY_API_KEY
TOKEN_SPY_API_KEY=dev-token \
UPSTREAM_API_KEY=provider-secret \
AGENT_NAME=my-agent python -m uvicorn main:app --host 0.0.0.0 --port 9110
```

Open `http://localhost:9110/dashboard` to see the monitoring UI.

All `/api/*`, `/token_events`, and `/v1/*` endpoints require:

```bash
Authorization: Bearer <TOKEN_SPY_API_KEY>
```

Use `UPSTREAM_API_KEY` for external Anthropic/OpenAI/Moonshot providers. For local no-auth OpenAI-compatible upstreams, `UPSTREAM_API_KEY` is optional.

## Configuration

See [TOKEN-SPY-GUIDE.md](TOKEN-SPY-GUIDE.md) for all available settings.

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Health check |
| `/dashboard` | GET | Web dashboard |
| `/api/settings` | GET/POST | Read/update settings |
| `/api/usage` | GET | Raw usage data |
| `/api/summary` | GET | Aggregated metrics by agent |
| `/api/session-status` | GET | Current session health |
| `/api/reset-session` | POST | Kill active session |
| `/token_events` | GET | SSE stream of token events |
| `/v1/messages` | POST | Anthropic proxy |
| `/v1/chat/completions` | POST | OpenAI-compatible proxy |

See [TOKEN-SPY-GUIDE.md](TOKEN-SPY-GUIDE.md) for full API documentation.

## Provider System

Pluggable cost calculation via provider classes:

```
providers/
  base.py       -- Abstract base class (LLMProvider)
  registry.py   -- @register_provider decorator + lookup
  anthropic.py  -- Claude models with cache-aware pricing
  openai.py     -- OpenAI-compatible (GPT, Kimi, local models)
```

Add new providers by subclassing `LLMProvider` and decorating with `@register_provider("name")`.
