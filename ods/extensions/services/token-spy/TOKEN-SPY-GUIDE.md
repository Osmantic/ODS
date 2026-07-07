# Token Spy — System Guide & Feature Roadmap

**For: AI agents and operators using Token Spy**

---

## What Is Token Spy?

Token Spy is an **authenticated API proxy** that sits between your AI agents and upstream LLM providers. Every API call passes through Token Spy, which logs token usage, cost, latency, and session health before forwarding the request upstream.

You do need one proxy-specific change: point your base URL at Token Spy and send `Authorization: Bearer <TOKEN_SPY_API_KEY>` on protected routes. For external providers, Token Spy uses `UPSTREAM_API_KEY` on the server side and does not forward its own Bearer token upstream.

### Architecture

```
You (agent) -> Token Spy proxy -> Upstream API (Anthropic, OpenAI, etc.)
                  |
                  v
              SQLite DB <- Dashboard (charts, tables, settings)
                  ^
                  |
           Session Manager (polls every N minutes, enforces limits)
```

### Your Proxy Ports

| Agent      | Proxy Port | Dashboard                          |
|------------|------------|------------------------------------|
| my-agent   | `:9110`    | `http://localhost:9110/dashboard`   |

Each agent instance shares the same database, so any dashboard shows data for all agents.

### Authentication Model

- Clients authenticate to Token Spy with `Authorization: Bearer <TOKEN_SPY_API_KEY>`
- Dashboard/API routes and proxy routes both use the same Token Spy API key
- External Anthropic/OpenAI/Moonshot upstreams should be configured with `UPSTREAM_API_KEY`
- Local OpenAI-compatible upstreams can run without `UPSTREAM_API_KEY`
- Token Spy strips its own Bearer token before forwarding requests upstream

---

## How Session Control Works

Token Spy manages your context size through a **character-based session limit**. Here's the flow:

1. **Every API call**: Token Spy logs `conversation_history_chars` — the total size of all messages in your request.
2. **After logging**: It checks if your history exceeds the configured `session_char_limit`.
3. **If exceeded**: Token Spy kills your largest active session file, forcing a fresh session on your next turn.
4. **Session Manager**: A separate timer (systemd/cron) polls every `poll_interval_minutes` and runs additional cleanup (removes inactive sessions, enforces session count limits).

### Why Characters Instead of Tokens?

One token is roughly 4 characters. We use characters because:
- Character counts are available *before* sending to the API (tokens are counted by the provider *after*)
- It's provider-agnostic — works the same whether you're hitting Anthropic, OpenAI, or a local model
- The dashboard shows both: `51K / 100K (~25K tokens)`

### Default Settings

```json
{
  "session_char_limit": 200000,
  "poll_interval_minutes": 5,
  "agents": {}
}
```

Per-agent overrides use `null` to inherit the global default.

---

## API Reference

All endpoints are available on the proxy port. Multiple instances share the same database and settings file.

### Settings

**Read current settings:**
```bash
curl http://localhost:9110/api/settings \
  -H "Authorization: Bearer $TOKEN_SPY_API_KEY"
```

**Update global session limit (takes effect immediately):**
```bash
curl -X POST http://localhost:9110/api/settings \
  -H "Authorization: Bearer $TOKEN_SPY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"session_char_limit": 150000}'
```

**Set a per-agent override:**
```bash
curl -X POST http://localhost:9110/api/settings \
  -H "Authorization: Bearer $TOKEN_SPY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"agents": {"my-agent": {"session_char_limit": 80000}}}'
```

**Clear a per-agent override (back to inheriting global):**
```bash
curl -X POST http://localhost:9110/api/settings \
  -H "Authorization: Bearer $TOKEN_SPY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"agents": {"my-agent": {"session_char_limit": null}}}'
```

**Change poll frequency (also updates the systemd timer if configured):**
```bash
curl -X POST http://localhost:9110/api/settings \
  -H "Authorization: Bearer $TOKEN_SPY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"poll_interval_minutes": 1}'
```

### Monitoring

**Health check:**
```bash
curl http://localhost:9110/health
# -> {"status":"ok","agent":"my-agent","uptime_seconds":373,"session_char_limit":200000}
```

**Session status (current session health):**
```bash
curl "http://localhost:9110/api/session-status?agent=my-agent" \
  -H "Authorization: Bearer $TOKEN_SPY_API_KEY"
# -> {
#     "current_session_turns": 27,
#     "current_history_chars": 170829,
#     "recommendation": "healthy",
#     "session_char_limit": 200000,
#     "cost_since_last_reset": 0.357,
#     ...
#   }
```

Recommendation levels scale with your configured limit:
- **healthy**: history < limit
- **monitor**: history > limit (compaction expected)
- **compact_soon**: history > 2x limit
- **reset_recommended**: history > 2.5x limit (auto-reset fires at limit)

**Usage data (raw turns):**
```bash
curl "http://localhost:9110/api/usage?hours=24&limit=100" \
  -H "Authorization: Bearer $TOKEN_SPY_API_KEY"
```

**Summary (aggregated by agent):**
```bash
curl "http://localhost:9110/api/summary?hours=24" \
  -H "Authorization: Bearer $TOKEN_SPY_API_KEY"
```

**Manual session reset (emergency):**
```bash
curl -X POST "http://localhost:9110/api/reset-session?agent=my-agent" \
  -H "Authorization: Bearer $TOKEN_SPY_API_KEY"
```

### Proxy Requests

**Anthropic Messages API via Token Spy:**
```bash
curl -X POST http://localhost:9110/v1/messages \
  -H "Authorization: Bearer $TOKEN_SPY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-sonnet-4","max_tokens":128,"messages":[{"role":"user","content":"Hello"}]}'
```

**OpenAI-compatible Chat Completions via Token Spy:**
```bash
curl -X POST http://localhost:9110/v1/chat/completions \
  -H "Authorization: Bearer $TOKEN_SPY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3-coder-next","messages":[{"role":"user","content":"Hello"}]}'
```

### Dashboard

Open `http://localhost:9110/dashboard` in a browser. Features:
- Session health cards with live status badges
- Cost per turn timeline
- History growth chart with auto-reset threshold lines
- Token usage bar chart (input, output, cache read, cache write)
- Cost breakdown doughnut (cache efficiency visualization)
- Cumulative cost timeline
- Recent turns table
- **Settings panel** (click the gear icon) — edit session limits and poll frequency with live token estimates

---

## Rules for Safe Experimentation

**DO:**
- Use the `/api/settings` endpoint to adjust limits
- Monitor the dashboard to see the effects
- Set per-agent overrides to test different limits independently
- Use the `/api/session-status` endpoint to check your current health before and after changes

**DO NOT:**
- Edit source code (`main.py`, `db.py`, `session-manager.sh`) on a running service — changes cause undefined behavior
- Modify systemd unit files directly — use the settings API which updates them safely

---

## Feature Roadmap

### Feature 1: Model Comparison View
Side-by-side performance and cost comparison across all models. Bar charts for cost per turn, average latency, and input tokens by model. Data already exists in the database — no schema changes needed.

### Feature 2: Latency / Response Time Chart
Timeline chart showing API response times with per-model and per-agent breakdown. `duration_ms` is already logged on every turn. Highlights outliers and can correlate context size with latency.

### Feature 3: Cost Alerts / Budget Cap
Configurable spending thresholds with dashboard warnings. Daily and hourly budget indicators. Informational only — no traffic blocking.

### Feature 4: Session Timeline / Session History
Visual history of past sessions showing lifecycle from start to reset. Session boundary detection already exists. Shows patterns in session length, cost, and reset frequency.

### Feature 5: Stop Reason Analytics
Breakdown of why each API call ended — natural stop, tool call, max tokens, etc. Surfaces issues like context truncation. `stop_reason` is already logged.

### Feature 6: Tool Usage Tracking
Track which tools are registered and how frequently they appear in requests. Identify dead-weight tool definitions consuming tokens. Requires minor schema addition.
