# Perplexica

AI-powered deep research and answer engine for ODS

## Overview

Perplexica is an open-source alternative to Perplexity AI. It combines SearXNG web search with your local LLM to answer questions with cited, up-to-date information. Instead of retrieving a static knowledge cutoff, Perplexica searches the web in real time and synthesizes results into a comprehensive answer.

Upstream renamed the project to **Vane** in March 2026
([ItzCrazyKns/Vane](https://github.com/ItzCrazyKns/Vane)); the UI now says
Vane. ODS keeps the `perplexica` service id, `ods-perplexica` container name,
port and volume names, so settings and chat history carry over.

## Image pin

ODS pins the upstream **slim** image for release `v1.12.2`:
`itzcrazykns1337/vane:slim-v1.12.2@sha256:d2878cf9…` (full identity, per-platform
digests and provenance in `config/perplexica-release.json`).

- **slim** contains only the Next.js app and uses ODS's own `searxng` service.
  The **full** image also bundles a SearXNG instance, which ODS does not need.
- Vane 1.12.2 added a Chromium (Playwright) page scraper. The `slim-v1.12.2`
  release image ships the Playwright package but not the browser (upstream
  added it to `Dockerfile.slim` only after the release). Speed and Balanced
  modes use SearXNG results and do not scrape, so they are unaffected. Quality
  mode and the `scrape_url` tool cannot read pages with this image.
- The app root moved from `/home/perplexica` to `/home/vane`. ODS mounts the
  existing `perplexica-data` and `perplexica-uploads` volumes at the new paths.
- Speed and Balanced rank SearXNG results with the configured embedding model.
  The built-in `Xenova/all-MiniLM-L6-v2` is downloaded from Hugging Face into the
  container on first use (again after each recreate); without Internet access
  ranking is skipped and results are used unranked.

To bump: pick a versioned `slim-vX.Y.Z` tag on Docker Hub, verify the manifest
list with `docker buildx imagetools inspect`, review the upstream compare for
Dockerfile, data-path, `/api/config`, `/api/search` and `/api/chat` changes, then
update `compose.yaml`, `config/dependency-lock.json`,
`installers/phases/08-images.sh` and `config/perplexica-release.json` together;
`tests/test-perplexica-entrypoint.py` checks that they agree.

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
| `PERPLEXICA_SEARXNG_API_URL` | empty | Explicit SearXNG-compatible endpoint; empty preserves the current setting (`http://searxng:8080` on fresh installs) |

> **LLM API key:** Perplexica uses `LITELLM_KEY` automatically when LiteLLM auth is enabled, then falls back to `OPENAI_API_KEY`, then `no-key` for direct llama-server installs that do not require authentication. No changes needed for local use.

> **SearXNG URL:** Perplexica connects to SearXNG internally at `http://searxng:8080` by default. Set `PERPLEXICA_SEARXNG_API_URL` in `.env` to point it at another SearXNG-compatible service, such as `http://brave-search:8585` when the `brave-search` extension runs with `BRAVE_SEARCH_SEARXNG_COMPAT=1`. ODS validates and applies an explicit override to Perplexica's persisted setting on each start. Empty preserves the current setting; explicitly set `http://searxng:8080` once to switch an existing install back.

> **Model name:** Perplexica stores its own `defaultChatModel` in its app
> settings volume. The installer seeds it on first boot, and the bootstrap
> hot-swap updates it after the full model is ready. After a manual GGUF or
> tier switch, verify Perplexica Settings or run
> `scripts/repair/repair-perplexica.sh <perplexica-url> <model-name>` from the
> installed `ods` directory.

> **Embedding model:** When both embedding defaults are unselected, ODS selects
> Perplexica's existing Transformers `Xenova/all-MiniLM-L6-v2` model if the app
> advertises it. Existing or partly configured owner selections are preserved.
> If the built-in model is unavailable or a selection is incomplete, choose a
> provider and embedding model in Perplexica Settings before delegating research.

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
| `perplexica-data` → `/home/vane/data` | Conversation history, settings (`config.json`, `db.sqlite`) and uploaded files (`data/uploads`) |
| `perplexica-uploads` → `/home/vane/uploads` | Legacy mount kept for compatibility; the app stores uploads under `data/uploads` |

## Files

- `manifest.yaml` — Service metadata (port, health endpoint, dependencies)
- `compose.yaml` — Container definition (image, environment, volumes, resource limits)

## Troubleshooting

**Perplexica not starting:**

Perplexica waits for SearXNG to be healthy before starting. Check SearXNG first:
```bash
docker compose ps ods-searxng
docker compose logs ods-searxng
```

Then check Perplexica:
```bash
docker compose ps ods-perplexica
docker compose logs ods-perplexica
```

**No search results / "Search failed" errors:**
- Verify SearXNG is reachable from within the Docker network
- Test: `docker compose exec perplexica wget -qO- http://searxng:8080/healthz`

**LLM not responding:**
- Confirm llama-server is running: `docker compose ps ods-llama-server`
- Verify the `LLM_API_URL` in `.env` points to the correct host

**Slow or incomplete answers:**
- Perplexica performance is limited by LLM inference speed. Ensure llama-server has GPU access.
- Reduce the number of search results by adjusting SearXNG settings
- ODS caps Perplexica's internal `scrape_url` output at startup so oversized web pages do not overflow local model context windows. If a specific page needs more context, raise `PERPLEXICA_SCRAPE_URL_MAX_CHARS` in `.env` and restart Perplexica.
