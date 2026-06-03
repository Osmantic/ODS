# litellm

OpenAI-compatible LLM API gateway for Dream Server

## Overview

LiteLLM is a unified API gateway that provides a single OpenAI-compatible endpoint regardless of which LLM backend is active. Dream Server uses it to support three operating modes: **local** (llama-server only), **cloud** (external APIs only), and **hybrid** (local primary with cloud fallback).

LiteLLM runs at `http://localhost:4000` and is the recommended integration point for custom applications, scripts, and n8n workflows that need a stable, mode-independent API endpoint.

Dream Server does not expose LiteLLM as a normal dashboard quicklink. The upstream LiteLLM Admin UI lives at `/ui/`, but that surface expects a proxy-admin UI setup and database-backed management flow. Dream Server's default LiteLLM container is provisioned as an API gateway, so use the OpenAI-compatible `/v1/*` endpoints with `LITELLM_KEY` instead of treating `/ui/` as the supported login path.

## Features

- **Single endpoint for all modes**: Same `POST /v1/chat/completions` URL regardless of backend
- **Three operating modes**: Local, cloud, and hybrid with automatic fallback
- **OpenAI-compatible API**: Works with any OpenAI SDK client
- **Multi-provider routing**: Anthropic, OpenAI, Together AI, MiniMax, and local llama-server
- **Master key auth**: Secure all requests with `LITELLM_KEY`
- **Drop params**: Unsupported parameters silently ignored across backends

## Operating Modes

The active mode is controlled by `DREAM_MODE` in `.env`. The corresponding config file is loaded automatically from `config/litellm/`.

### local (default)

Routes all requests to llama-server. No cloud API keys required.

```yaml
# config/litellm/local.yaml
model_list:
  - model_name: default       # Routes to llama-server:8080
  - model_name: "*"           # Wildcard — any model name routes locally
```

### cloud

Routes to external cloud APIs. Requires at least one cloud API key.

```yaml
# config/litellm/cloud.yaml
model_list:
  - model_name: default       # → anthropic/claude-sonnet-4-5-20250514
  - model_name: gpt4o         # → openai/gpt-4o
  - model_name: fast          # → anthropic/claude-haiku-4-5-20251001
  - model_name: minimax       # → MiniMax-M2.7 via minimax API
  - model_name: minimax-fast  # → MiniMax-M2.7-highspeed
```

### hybrid

Uses llama-server as primary; falls back to Anthropic Claude on failure.

```yaml
# config/litellm/hybrid.yaml
model_list:
  - model_name: default       # → llama-server (primary)
  - model_name: default       # → anthropic/claude-sonnet-4-5-20250514 (fallback)
router_settings:
  num_retries: 2
  fallbacks:
    - default: [default]
```

## Configuration

Environment variables (set in `.env`):

| Variable | Default | Description |
|----------|---------|-------------|
| `LITELLM_KEY` | *(required)* | Master API key — generate with `echo "sk-dream-$(openssl rand -hex 16)"` |
| `LITELLM_PORT` | `4000` | External + internal port |
| `DREAM_MODE` | `local` | Operating mode: `local`, `cloud`, or `hybrid` |
| `ANTHROPIC_API_KEY` | *(empty)* | Required for `cloud` and `hybrid` modes |
| `OPENAI_API_KEY` | *(empty)* | Required for OpenAI models in `cloud` mode |
| `TOGETHER_API_KEY` | *(empty)* | Required for Together AI models in `cloud` mode |
| `MINIMAX_API_KEY` | *(empty)* | Required for MiniMax models in `cloud`/`hybrid` modes |

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health/readiness` | Readiness probe |
| `GET` | `/health/liveness` | Liveness probe |
| `GET` | `/v1/models` | List configured models |
| `POST` | `/v1/chat/completions` | Chat completions (OpenAI format) |
| `POST` | `/v1/completions` | Text completions |
| `POST` | `/v1/embeddings` | Embeddings (if backend supports it) |

### Example

```bash
curl http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer YOUR_LITELLM_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "default",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

### Python (OpenAI SDK)

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:4000/v1",
    api_key="YOUR_LITELLM_KEY"
)

response = client.chat.completions.create(
    model="default",
    messages=[{"role": "user", "content": "Hello!"}]
)
```

## Architecture

```
Your App / n8n / Scripts
         │
         ▼
    LiteLLM (:4000)
    config/litellm/${DREAM_MODE}.yaml
         │
    ┌────┴─────────────────┐
    │                       │
    ▼  (local / hybrid)     ▼  (cloud / hybrid fallback)
llama-server:8080      Anthropic / OpenAI / Together AI
(GGUF model)           (external APIs)
```

## Switching Modes

```bash
# Edit .env
DREAM_MODE=cloud

# Restart LiteLLM
docker compose up -d litellm
```

Or use the `dream` CLI:

```bash
dream mode cloud
```

## Files

- `compose.yaml` — Service definition
- `manifest.yaml` — Service metadata
- `config/litellm/local.yaml` — Local mode config
- `config/litellm/cloud.yaml` — Cloud mode config
- `config/litellm/hybrid.yaml` — Hybrid mode config

## Troubleshooting

**LiteLLM not starting:**
```bash
docker compose ps litellm
docker compose logs litellm
```

**401 Unauthorized:**
- Check `LITELLM_KEY` in `.env` matches what you're sending in the `Authorization` header

**Cloud mode failing — "API key not found":**
- Set the required key in `.env` (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, etc.) and restart

**Model not found:**
- In `local` mode, the wildcard `"*"` routes any model name to llama-server — the model field is passed through to llama.cpp
- Verify the config file is correct: `cat dream-server/config/litellm/local.yaml`

**Checking health:**
```bash
curl http://localhost:4000/health/readiness
```

## License

Part of Dream Server — Local AI Infrastructure
