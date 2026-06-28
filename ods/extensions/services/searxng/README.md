# SearXNG

Privacy-respecting metasearch engine for ODS

## Overview

SearXNG aggregates results from 70+ search engines — Google, Bing, DuckDuckGo, Wikipedia, and more — without tracking you or building a profile. It is the web search backbone for Perplexica and can be used directly as a private search interface or queried via API by other services and agents.

## Features

- **Multi-engine aggregation**: Queries dozens of search engines simultaneously and deduplicates results
- **Zero tracking**: No user profiling, no ad targeting, no query logging to third parties
- **API access**: JSON API for programmatic search queries (used by Perplexica and OpenClaw)
- **Configurable engines**: Enable, disable, or weight individual search engines in `config/searxng/`
- **Multiple categories**: Web, images, news, science, files, social media, and more
- **Lightweight**: Runs in under 512 MB of memory

## Configuration

Environment variables (set in `.env`):

| Variable | Default | Description |
|----------|---------|-------------|
| `SEARXNG_PORT` | 8888 | External port for the SearXNG web UI and API |

Additional configuration is managed via files in `./config/searxng/`:
- `settings.yml` — Engine list, UI preferences, result limits, secret key
- `limiter.toml` — Rate limiting rules

> **Settings file:** The config directory is mounted read-write so changes to `settings.yml` take effect after a container restart without rebuilding the image.

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /healthz` | GET | Health check (returns 200 when ready) |
| `GET /` | GET | Web search UI |
| `GET /search` | GET | Search query (see parameters below) |

### Search API Parameters

| Parameter | Description | Example |
|-----------|-------------|---------|
| `q` | Search query | `q=local+AI` |
| `format` | Response format (`json`, `rss`, `html`) | `format=json` |
| `categories` | Comma-separated categories | `categories=general,news` |
| `engines` | Comma-separated engines to use | `engines=google,bing` |
| `language` | Language code | `language=en` |
| `pageno` | Page number (default: 1) | `pageno=2` |

### Example API Usage

```bash
# JSON search results
curl "http://localhost:8888/search?q=local+AI&format=json"

# Search with specific engines
curl "http://localhost:8888/search?q=machine+learning&format=json&engines=google,wikipedia"

# Health check
curl http://localhost:8888/healthz
```

## Architecture

```
┌────────────┐   GET /search?q=...   ┌──────────────┐
│ Perplexica │──────────────────────▶│   SearXNG    │
│  OpenClaw  │                       │  (Metasearch)│
│  Browser   │◀──────────────────────│              │
└────────────┘    JSON results       └──────┬───────┘
                                            │
                          ┌─────────────────┼─────────────┐
                          ▼                 ▼             ▼
                       Google            Bing        Wikipedia
                       (+ 67 more engines)
```

## Resource Limits

| Limit | Value |
|-------|-------|
| CPU limit | 1 core |
| Memory limit | 512 MB |
| CPU reservation | 0.1 cores |
| Memory reservation | 64 MB |

## Files

- `manifest.yaml` — Service metadata (port, health endpoint, category)
- `compose.yaml` — Container definition (image, environment, volumes, resource limits)

## Troubleshooting

**SearXNG not starting:**
```bash
docker compose ps ods-searxng
docker compose logs ods-searxng
```

**Search returns no results:**
- Some search engines may be rate-limiting your IP. Check `config/searxng/settings.yml` and disable heavy-use engines.
- Verify outbound internet access from the container: `docker compose exec searxng wget -qO- https://example.com`

**Perplexica cannot reach SearXNG:**
- Perplexica connects to SearXNG internally at `http://searxng:8080`. Ensure both containers are on the same Docker network.
- Check: `docker compose ps ods-searxng` — the container must be healthy.

**Editing search engine settings:**
```bash
# Edit config (changes apply after restart)
nano ods/config/searxng/settings.yml
docker compose restart searxng
```
