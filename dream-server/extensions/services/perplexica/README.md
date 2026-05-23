# Perplexica

AI-powered deep research and answer engine for Dream Server

## Overview

Perplexica is an open-source alternative to Perplexity AI. It combines SearXNG web search with your local LLM to answer questions with cited, up-to-date information. Instead of retrieving a static knowledge cutoff, Perplexica searches the web in real time and synthesizes results into a comprehensive answer.

## Features

- **Real-time web research**: Queries SearXNG to fetch live search results before answering
- **Citation-backed answers**: Every answer includes source links for verification
- **Conversational follow-up**: Ask follow-up questions within a research session
- **Multiple focus modes**: General, academic, writing, YouTube, Reddit, and news search modes
- **Fully local**: Routes through your local LLM (llama-server) — no data sent to external AI services
- **File uploads**: Upload documents to include in research context

## Dependencies

Perplexica requires two services to be running and healthy before it starts:

| Service | Role |
|---------|------|
| `searxng` | Provides web search results |
| `llama-server` | LLM inference for synthesizing answers |

## Configuration

Environment variables (set in `.env`):

| Variable | Default | Description |
|----------|---------|-------------|
| `PERPLEXICA_PORT` | 3004 | External port for the Perplexica web UI |
| `LLM_API_URL` | `http://llama-server:8080` | Base URL of the LLM backend (OpenAI-compatible) |
| `PERPLEXICA_SCRAPE_URL_MAX_CHARS` | 30000 | Per-URL cap applied to Perplexica's internal `scrape_url` tool output before synthesis |

> **LLM API key:** Perplexica uses `LITELLM_KEY` automatically when LiteLLM auth is enabled, then falls back to `OPENAI_API_KEY`, then `no-key` for direct llama-server installs that do not require authentication. No changes needed for local use.

> **SearXNG URL:** Perplexica connects to SearXNG internally at `http://searxng:8080`. This is fixed in `compose.yaml` and does not need to be changed.

> **Model name:** Perplexica stores its own `defaultChatModel` in its app
> settings volume. The installer seeds it on first boot, and the bootstrap
> hot-swap updates it after the full model is ready. After a manual GGUF or
> tier switch, verify Perplexica Settings or run
> `scripts/repair/repair-perplexica.sh <perplexica-url> <model-name>` from the
> installed `dream-server` directory.

## Architecture

```
┌──────────┐   Questions    ┌──────────────┐
│ Browser  │───────────────▶│  Perplexica  │
│          │◀───────────────│  (Research)  │
└──────────┘  Cited answers └──────┬───────┘
                                   │
                    ┌──────────────┴──────────────┐
                    ▼                             ▼
             ┌────────────┐               ┌──────────────┐
             │  SearXNG   │               │ llama-server │
             │ (Web Search│               │    (LLM)     │
             └────────────┘               └──────────────┘
```

**Research flow:**
1. User submits a question
2. Perplexica generates search queries and sends them to SearXNG
3. SearXNG returns ranked web results
4. Perplexica sends the results + question to llama-server
5. LLM synthesizes a cited answer and streams it back to the browser

## Resource Limits

| Limit | Value |
|-------|-------|
| CPU limit | 2 cores |
| Memory limit | 2 GB |
| CPU reservation | 0.25 cores |
| Memory reservation | 256 MB |

## Volumes

| Volume | Purpose |
|--------|---------|
| `perplexica-data` | Conversation history, settings |
| `perplexica-uploads` | Uploaded files for document research |

## Files

- `manifest.yaml` — Service metadata (port, health endpoint, dependencies)
- `compose.yaml` — Container definition (image, environment, volumes, resource limits)

## Troubleshooting

**Perplexica not starting:**

Perplexica waits for SearXNG to be healthy before starting. Check SearXNG first:
```bash
docker compose ps dream-searxng
docker compose logs dream-searxng
```

Then check Perplexica:
```bash
docker compose ps dream-perplexica
docker compose logs dream-perplexica
```

**No search results / "Search failed" errors:**
- Verify SearXNG is reachable from within the Docker network
- Test: `docker compose exec perplexica wget -qO- http://searxng:8080/healthz`

**LLM not responding:**
- Confirm llama-server is running: `docker compose ps dream-llama-server`
- Verify the `LLM_API_URL` in `.env` points to the correct host

**Slow or incomplete answers:**
- Perplexica performance is limited by LLM inference speed. Ensure llama-server has GPU access.
- Reduce the number of search results by adjusting SearXNG settings
- DreamServer caps Perplexica's internal `scrape_url` output at startup so oversized web pages do not overflow local model context windows. If a specific page needs more context, raise `PERPLEXICA_SCRAPE_URL_MAX_CHARS` in `.env` and restart Perplexica.
