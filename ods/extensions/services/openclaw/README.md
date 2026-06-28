# OpenClaw

> ⚠️ **DEPRECATED as of 2026-05-12.** Replaced by **Hermes Agent** (see `extensions/services/hermes/` and [docs/HERMES.md](../../../docs/HERMES.md)). New ODS installs no longer enable OpenClaw by default. This extension stays installable for one release cycle, then is removed. Migration notes: [docs/MIGRATION-OPENCLAW-TO-HERMES.md](../../../docs/MIGRATION-OPENCLAW-TO-HERMES.md).

Autonomous agent framework for ODS

## Overview

OpenClaw is an autonomous AI agent gateway that orchestrates multi-step reasoning, tool use, and task execution using your local LLM. Agents can search the web via SearXNG, execute code, manage files, and chain complex workflows — all running locally without sending data to external services.

## Features

- **Autonomous agents**: LLM-driven agents that plan, act, and iterate toward a goal
- **Web search integration**: Connects to local SearXNG for real-time web research
- **Tool use**: Agents can use a configurable set of tools across multiple steps
- **Multi-model support**: Primary inference model and a lighter bootstrap model for planning
- **Gateway API**: Exposes a REST API for launching and monitoring agent tasks
- **LAN binding**: Accessible from other devices on your local network

## Dependencies

OpenClaw requires these services to be healthy before it starts:

| Service | Role |
|---------|------|
| `searxng` | Web search tool for agents |
| `llama-server` | LLM inference backend |

## Configuration

Environment variables (set in `.env`):

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENCLAW_TOKEN` | *(required)* | Gateway authentication token — **must be set in `.env`** |
| `OPENCLAW_PORT` | 7860 | External port for the OpenClaw web UI and API |
| `LLM_MODEL` | `qwen3:30b-a3b` | Primary LLM model for agent reasoning |
| `BOOTSTRAP_MODEL` | `qwen3:8b-q4_K_M` | Lighter model used for planning and bootstrapping |
| `LLM_API_URL` | `http://llama-server:8080` | Base URL of the LLM backend |

> **`OPENCLAW_TOKEN` is required.** The installer sets a random value in `.env`. If it is missing, the container will refuse to start. Verify it is set: `grep OPENCLAW_TOKEN ods/.env`

## Volume Mounts

| Host Path | Container Path | Purpose |
|-----------|---------------|---------|
| `./config/openclaw` | `/config` (read-only) | Gateway config files and token injection script |
| `./data/openclaw` | `/data` | Agent task state and persistent data |
| `./data/openclaw/home/agents` | `/home/node/.openclaw/agents` | Agent definitions and sessions |
| `./data/openclaw/home/canvas` | `/home/node/.openclaw/canvas` | OpenClaw canvas state |
| `./data/openclaw/home/cron` | `/home/node/.openclaw/cron` | Scheduled agent jobs |
| `./config/openclaw/workspace` | `/home/node/.openclaw/workspace` | Agent workspace directory |

## Architecture

```
┌──────────┐  HTTP :7860     ┌──────────────┐
│ Browser  │────────────────▶│   OpenClaw   │
│  Client  │◀────────────────│   (Gateway)  │
└──────────┘  Agent results  └──────┬───────┘
                                    │ Plans tasks
                    ┌───────────────┴───────────────┐
                    ▼                               ▼
             ┌────────────┐                 ┌──────────────┐
             │  SearXNG   │                 │ llama-server │
             │ (Web Search│                 │    (LLM)     │
             └────────────┘                 └──────────────┘
```

**Agent execution flow:**
1. User submits a task via the web UI or API
2. OpenClaw uses the bootstrap model to decompose the task into steps
3. The primary LLM model executes each step, calling tools (search, code, etc.) as needed
4. Results are aggregated and returned to the user

## Resource Limits

| Limit | Value |
|-------|-------|
| CPU limit | 2 cores |
| Memory limit | 4 GB |
| CPU reservation | 0.5 cores |
| Memory reservation | 1 GB |

## Files

- `manifest.yaml` — Service metadata (port, health endpoint, required env vars)
- `compose.yaml` — Container definition (image, environment, volumes, resource limits)

## Troubleshooting

**OpenClaw refuses to start (`OPENCLAW_TOKEN` error):**
```bash
# Check token is set
grep OPENCLAW_TOKEN ods/.env

# Set a token value if missing
echo "OPENCLAW_TOKEN=$(openssl rand -hex 24)" >> ods/.env
docker compose up -d openclaw
```

**OpenClaw not starting (SearXNG dependency):**

OpenClaw waits for SearXNG to be healthy. Check SearXNG first:
```bash
docker compose ps ods-searxng
docker compose logs ods-searxng
```

**Cannot access web UI on port 7860:**
```bash
docker compose ps ods-openclaw
docker compose logs ods-openclaw
```

**Agents not using the right model:**
- Update `LLM_MODEL` in `.env` to match a model loaded in llama-server
- Restart OpenClaw: `docker compose restart openclaw`

**Token injection errors:**
```bash
# Check config directory
ls ods/config/openclaw/
docker compose logs ods-openclaw | grep -i token
```
