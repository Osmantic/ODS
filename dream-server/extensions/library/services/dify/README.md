# Dify

AI workflow and agent platform for building LLM-powered applications. Build and deploy AI agents, chatbots, and automation workflows with a visual editor.

## Requirements

- **GPU:** NVIDIA or AMD (min 4 GB VRAM)
- **Dependencies:** None

## Enable / Disable

```bash
dream enable dify
dream disable dify
```

Your data is preserved when disabling. To re-enable later: `dream enable dify`

## Access

- **URL:** `http://localhost:8002`

## First-Time Setup

1. Enable the service: `dream enable dify`
2. Open `http://localhost:8002`
3. Create an admin account on first launch
4. Connect to Dream Server's LLM via the OpenAI-compatible endpoint

## Configuration

| Variable | Description | Default |
|----------|------------|---------|
| `DIFY_SECRET_KEY` | Secret key for API access (auto-generated) | _(required)_ |
| `DIFY_EXTERNAL_URL` | External URL for API responses and redirects | `http://localhost:8002` |
| `DIFY_OPENAI_API_BASE` | OpenAI-compatible API endpoint for LLM backend | `http://llama-server:8080/v1` |
| `DIFY_OPENAI_API_KEY` | API key for OpenAI-compatible endpoint | `dummy-key` |
| `DIFY_INIT_PASSWORD` | Initial admin password (optional) | _(empty)_ |

## Known Issues

Dify uses separate container images for API and web frontend (`langgenius/dify-api` + `langgenius/dify-web`), making it a complex multi-container setup.
