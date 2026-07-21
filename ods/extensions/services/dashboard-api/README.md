# dashboard-api

FastAPI backend providing system status, metrics, and management for ODS

## Overview

The Dashboard API is a Python FastAPI service that powers the ODS Dashboard UI. It exposes endpoints for GPU metrics, service health monitoring, LLM inference stats, workflow management, agent monitoring, setup wizard, version checking, and Privacy Shield control.

It runs at `http://localhost:3002` and is the single backend used by the React dashboard frontend.

## Features

- **GPU monitoring**: Real-time VRAM usage, temperature, utilization, and power draw (NVIDIA + AMD)
- **Service health**: Health checks for all ODS services via Docker network
- **LLM metrics**: Tokens/second, lifetime tokens, loaded model, context size
- **System metrics**: CPU usage, RAM usage, uptime, disk space
- **Workflow management**: n8n workflow catalog — install, enable, disable, track executions
- **Feature discovery**: Hardware-aware feature recommendations with VRAM tier detection
- **Setup wizard**: First-run setup, persona selection, diagnostic tests
- **Agent monitoring**: Session counts, throughput, cluster status, per-model token usage
- **Privacy Shield control**: Enable/disable container, fetch PII scrubbing statistics
- **Version checking**: GitHub releases integration for update notifications
- **Storage reporting**: Breakdown of disk usage by models, vector DB, and total data

## Configuration

Environment variables (set in `.env`):

| Variable | Default | Description |
|----------|---------|-------------|
| `DASHBOARD_API_PORT` | `3002` | External + internal port |
| `DASHBOARD_API_KEY` | *(auto-generated)* | API key for all protected endpoints. If unset, a random key is generated and written to `/data/dashboard-api-key.txt` |
| `GPU_BACKEND` | `nvidia` | GPU backend: `nvidia` or `amd` |
| `OLLAMA_URL` | `http://llama-server:8080` | LLM backend URL |
| `LLM_MODEL` | `qwen3:30b-a3b` | Active model name shown in dashboard |
| `KOKORO_URL` | `http://tts:8880` | Kokoro TTS URL |
| `N8N_URL` | `http://n8n:5678` | n8n workflow URL |
| `OPENCLAW_TOKEN` | *(empty)* | OpenClaw agent auth token |

## API Endpoints

### Core

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/health` | No | Health check |
| `GET` | `/gpu` | Yes | GPU metrics (VRAM, temp, utilization) |
| `GET` | `/services` | Yes | All service health statuses |
| `GET` | `/disk` | Yes | Disk usage |
| `GET` | `/model` | Yes | Current model info |
| `GET` | `/bootstrap` | Yes | Model bootstrap/download status |
| `GET` | `/status` | Yes | Full system status (all above combined) |
| `GET` | `/api/status` | Yes | Dashboard-formatted status with inference metrics |
| `GET` | `/api/host-agent/diagnostics` | Yes | Host-agent URL, gateway, auth, and live probe diagnostics |

### Preflight

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/preflight/docker` | Yes | Check Docker availability |
| `GET` | `/api/preflight/gpu` | Yes | Check GPU availability |
| `GET` | `/api/preflight/required-ports` | Yes | List service ports |
| `POST` | `/api/preflight/ports` | Yes | Check port availability conflicts |
| `GET` | `/api/preflight/disk` | Yes | Check available disk space |

### Settings & Storage

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/service-tokens` | Yes | Service auth tokens (e.g. OpenClaw) |
| `GET` | `/api/external-links` | Yes | Sidebar links from service manifests |
| `GET` | `/api/storage` | Yes | Storage breakdown (models, vector DB, total) |

### Workflows (n8n integration)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/workflows` | Yes | Workflow catalog with install status |
| `POST` | `/api/workflows/{id}/enable` | Yes | Import and activate a workflow in n8n |
| `DELETE` | `/api/workflows/{id}` | Yes | Remove a workflow from n8n |
| `GET` | `/api/workflows/{id}/executions` | Yes | Recent execution history |

### Features

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/features` | Yes | Feature status with hardware recommendations |
| `GET` | `/api/features/{id}/enable` | Yes | Enable instructions for a feature |

### Setup Wizard

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/setup/status` | Yes | First-run check and current step |
| `GET` | `/api/setup/personas` | Yes | List available personas |
| `GET` | `/api/setup/persona/{id}` | Yes | Get persona details |
| `POST` | `/api/setup/persona` | Yes | Select a persona |
| `POST` | `/api/setup/complete` | Yes | Mark setup complete |
| `POST` | `/api/setup/test` | Yes | Run diagnostic tests (streaming) |
| `POST` | `/api/chat` | Yes | Quick chat for setup wizard |

### Agent Monitoring

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/agents/metrics` | Yes | Full agent metrics (sessions, tokens, cost) |
| `GET` | `/api/agents/metrics.html` | Yes | Agent metrics as HTML fragment (htmx) |
| `GET` | `/api/agents/cluster` | Yes | Cluster health and GPU node status |
| `GET` | `/api/agents/throughput` | Yes | Throughput stats (tokens/sec) |

### Privacy Shield

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/privacy-shield/status` | Yes | Privacy Shield container status |
| `POST` | `/api/privacy-shield/toggle` | Yes | Start or stop Privacy Shield |
| `GET` | `/api/privacy-shield/stats` | Yes | PII scrubbing statistics |

### Updates

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/version` | Yes | Current version + GitHub update check |
| `GET` | `/api/releases/manifest` | Yes | Recent release history from GitHub |
| `GET` | `/api/update/status` | Yes | Host-agent managed update status |
| `POST` | `/api/update` | Yes | Trigger update actions (`check`, `backup`, `update`) |

### Extensions

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/extensions/catalog` | Yes | Browse extension catalog with status, filterable by `category` and `gpu_compatible` query params |
| `GET` | `/api/extensions/{service_id}` | Yes | Detailed info for a single extension (manifest, features, env vars, setup instructions) |
| `POST` | `/api/extensions/{service_id}/install` | Yes | Install an extension from the extensions library into user-extensions |
| `POST` | `/api/extensions/{service_id}/enable` | Yes | Enable a disabled extension (renames `compose.yaml.disabled` to `compose.yaml`, starts container via host agent) |
| `POST` | `/api/extensions/{service_id}/disable` | Yes | Disable an enabled extension (stops container via host agent, renames `compose.yaml` to `compose.yaml.disabled`) |
| `DELETE` | `/api/extensions/{service_id}` | Yes | Uninstall a disabled extension (removes its directory from user-extensions) |
| `POST` | `/api/extensions/{service_id}/logs` | Yes | Fetch container logs via the host agent (last 100 lines) |

Core services cannot be installed, enabled, disabled, or uninstalled via these endpoints (returns 403). The catalog endpoint also reports whether the [host agent](../../../docs/HOST-AGENT-API.md) is available (`agent_available` field).

## Authentication

Protected endpoints always require Bearer authentication. When `DASHBOARD_API_KEY` is set in `.env`, use that key:

```bash
curl http://localhost:3002/api/status \
  -H "Authorization: Bearer YOUR_KEY"
```

When `DASHBOARD_API_KEY` is empty, dashboard-api generates a random key at startup, writes it to `/data/dashboard-api-key.txt` with mode `0600`, and still requires Bearer authentication for protected endpoints.

## Architecture

```
Dashboard UI (:3001)
       │
       ▼
Dashboard API (:3002)
  ├── gpu.py ──────────────── nvidia-smi / sysfs AMD
  ├── helpers.py ──────────── Docker-network health checks
  ├── agent_monitor.py ─────── Background metrics collection
  └── routers/
       ├── workflows.py ────── n8n API integration
       ├── features.py ─────── Hardware-aware feature discovery
       ├── setup.py ─────────── Setup wizard + persona system
       ├── updates.py ──────── GitHub releases + ods-update.sh
       ├── agents.py ───────── Agent session + throughput metrics
       ├── privacy.py ──────── Privacy Shield container control
       └── extensions.py ───── Extension catalog, install, enable/disable, logs
```

## Files

- `main.py` — FastAPI application, core endpoints, startup
- `config.py` — Shared configuration and manifest loading
- `models.py` — Pydantic response schemas
- `security.py` — API key authentication
- `gpu.py` — GPU detection for NVIDIA and AMD
- `helpers.py` — Service health checks, LLM metrics, system metrics
- `agent_monitor.py` — Background agent metrics collection
- `routers/` — Endpoint modules (workflows, features, setup, updates, agents, privacy, extensions)
- `Dockerfile` — Container definition
- `requirements.txt` — Python dependencies

## Troubleshooting

**API not responding:**
```bash
docker compose ps dashboard-api
docker compose logs dashboard-api
```

**GPU metrics missing:**
- NVIDIA: confirm `nvidia-smi` works on the host
- AMD: the AMD overlay mounts `/sys/class/drm` — confirm `GPU_BACKEND=amd` in `.env`

**Workflow operations failing:**
- Verify n8n is running: `curl http://localhost:5678/healthz`
- Check `N8N_URL` environment variable

**Storage endpoint returning zeros:**
- The container mounts `./data` at `/data` — verify the path exists

## License

Part of ODS — Local AI Infrastructure
