# Perplexica

AI-powered deep research and answer engine for ODS

## Overview

Perplexica is an open-source alternative to Perplexity AI. It combines SearXNG web search with the selected ODS model route to answer questions with cited, up-to-date information. Instead of retrieving a static knowledge cutoff, Perplexica searches the web in real time and synthesizes results into a comprehensive answer.

Upstream renamed the project to **Vane** in March 2026
([ItzCrazyKns/Vane](https://github.com/ItzCrazyKns/Vane)); the UI now says
Vane. ODS keeps the `perplexica` service id, `ods-perplexica` container name,
port and volume names, so settings and chat history carry over.

## Image pin

ODS pins the upstream **full** image for release `v1.12.2`:
`itzcrazykns1337/vane:v1.12.2@sha256:61f2bbf3…` (full identity, per-platform
digests and provenance in `config/perplexica-release.json`).

- **full** bundles a SearXNG instance that ODS does not use; ODS routes
  Perplexica to its own `searxng` service via `SEARXNG_API_URL` and
  `PERPLEXICA_SEARXNG_API_URL`. The **slim** image omits the bundled SearXNG
  but also omits the Playwright Chromium browser.
- Vane 1.12.2 added a Chromium (Playwright) page scraper. The `slim-v1.12.2`
  release image ships the Playwright package but not the browser (upstream
  added it to `Dockerfile.slim` only after the release), so Quality mode could
  not read pages. The **full** image installs Playwright Chromium
  (`--only-shell --with-deps`), so Quality mode reads the pages of its own
  search results. Speed and Balanced modes use SearXNG results and do not
  scrape.
- **`scrape_url` is disabled.** Vane's researcher offers its model a
  `scrape_url` action in every mode, which opens any URL the model names, with
  no address validation, from this container on the ODS network. Anyone who
  can call the unauthenticated `/api/search`, or text in a search result,
  could steer it to an internal service. The ODS entrypoint patches the bundle
  at each start so the action is never offered and opens nothing if a model
  names it anyway; the container does not start if the patch no longer
  matches the bundle. Asking Perplexica to summarize a specific URL therefore
  answers from search results instead. Quality mode still reads the pages of
  its own search results when the image has a browser.
- ODS patches the pinned Vane client citation renderer at container startup.
  The upstream renderer otherwise changes bracketed text inside Markdown code
  fences into citations. The patch preserves code, links, and escaped brackets
  while retaining citations in prose. A restart with updated ODS renderer code
  re-patches the audited client bytes even when Compose reuses the container.
  If the client bundle no longer matches the pinned expression, startup stops
  with an explicit error; update the patch and its tests when deliberately
  changing the image.
- The app root moved from `/home/perplexica` to `/home/vane`. ODS mounts the
  existing `perplexica-data` and `perplexica-uploads` volumes at the new paths.
- Speed and Balanced rank SearXNG results with the configured embedding model.
  The built-in `Xenova/all-MiniLM-L6-v2` is downloaded from Hugging Face into the
  container on first use (again after each recreate); without Internet access
  ranking is skipped and results are used unranked.

To bump: pick a versioned `vX.Y.Z` tag on Docker Hub, verify the manifest
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
- **ODS model integration**: Uses your configured ODS model, including local inference and authenticated remote APIs
- **File uploads**: Upload documents to include in research context

## Dependencies

Perplexica needs SearXNG and a working model route:

| Service | Role |
|---------|------|
| `searxng` | Provides web search results |
| `llama-server` on managed-local installs | Local inference; the Compose local overlay waits for it to be healthy |
| Selected external model on external-route installs | Inference through the configured ODS gateway; no managed llama-server is started for Perplexica |

## Configuration

Environment variables (set in `.env`):

| Variable | Default | Description |
|----------|---------|-------------|
| `PERPLEXICA_PORT` | 3004 | External port for the Perplexica web UI |
| `LLM_API_URL` | `http://llama-server:8080` | Base URL of the LLM backend (OpenAI-compatible) |
| `PERPLEXICA_SCRAPE_URL_MAX_CHARS` | 30000 | Per-URL cap the startup patch still applies to Perplexica's internal `scrape_url` output; ODS disables that action, so the cap has no effect unless the disable is removed |
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
> Library add-back completes Vane's first-use setup after the saved chat route
> and an advertised embedding model have been verified. A missing or incomplete
> embedding choice keeps the setup screen available for the owner.

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
             │  SearXNG   │               │ Selected ODS │
             │ (Web Search│               │ model route  │
             └────────────┘               └──────────────┘
```

**Research flow:**
1. User submits a question
2. Perplexica generates search queries and sends them to SearXNG
3. SearXNG returns ranked web results
4. Perplexica sends the results and question to the selected local or external model route
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
- On managed-local installs, confirm llama-server is healthy: `docker compose ps ods-llama-server`
- On external-route installs, verify the configured upstream is reachable and the selected model works through LiteLLM
- Verify the `LLM_API_URL` in `.env` points to the intended route

**Slow or incomplete answers:**
- Perplexica performance is limited by the selected model's inference speed. On managed-local installs, check GPU access for llama-server.
- Reduce the number of search results by adjusting SearXNG settings
- ODS disables Perplexica's internal `scrape_url` action at startup (see Image pin), so a request that names a URL is answered from search results.

**Perplexica exits at startup with "could not disable it":**
- The image's bundle no longer matches the `scrape_url` patch, for example after an image override. Return to the pinned image in `compose.yaml`, or update `docker-entrypoint.sh` and `tests/test-perplexica-entrypoint.py` for the new bundle.
