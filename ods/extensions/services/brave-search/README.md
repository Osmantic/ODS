# Brave Search

Optional search service that wraps the Brave Search API behind a small, stable JSON HTTP endpoint.

## Why this exists

The default `searxng` extension is excellent and free, but its results come from upstream public engines (Google, Bing, DuckDuckGo, etc.) that aggressively bot-block at small scale. If you self-host for more than a single user — or run automated agents that issue many queries — you will eventually hit captchas or rate limits that searxng cannot route around.

Brave Search runs its own independent crawler index. It is not a Google reseller and has no captcha layer. The trade-off: it is a paid API. The free Data tier is sufficient for individual use; heavier usage requires a subscription.

This extension does **not** replace `searxng`. It runs alongside it. Use whichever fits your workload.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `BRAVE_SEARCH_API_KEY` | — (required) | Subscription token from Brave Search API |
| `BRAVE_SEARCH_PORT` | `8585` | External port on the host |

Set the key in `.env`:

```
BRAVE_SEARCH_API_KEY=<your-token>
```

The container will refuse to start without it.

## Enable

```bash
ods enable brave-search
ods start brave-search
```

## API

### `GET /v1/search?q=<query>&count=<n>`

| Param | Default | Notes |
|---|---|---|
| `q` | — (required) | Search query |
| `count` | `5` | Max results, clamped to 1–20 |

Success response (`200`):

```json
{
  "query": "your query",
  "results": [
    { "title": "...", "url": "https://...", "snippet": "..." }
  ]
}
```

Error responses:

| Status | Body | Cause |
|---|---|---|
| `400` | `{"error":"missing_query_param_q"}` | `q` not supplied |
| `502` | `{"error":"upstream_error","status":N}` | Brave returned non-2xx |
| `502` | `{"error":"upstream_unavailable"}` | Network, DNS, or TLS failure while contacting Brave |
| `502` | `{"error":"invalid_upstream_json"}` | Brave returned a 2xx response that was not valid JSON |
| `504` | `{"error":"upstream_timeout"}` | Brave did not respond within 8s |

### `GET /health`

Returns `{ "ok": true }`. Used by the dashboard healthcheck.

## What this is *not*

This is not a drop-in replacement for `searxng`'s API surface. Perplexica (and any other consumer that speaks searxng's specific JSON format) cannot point `SEARXNG_API_URL` at this service and have it work — the response shapes differ. A future, separately scoped effort could add a searxng-API-compatible mode for that use case. For now, this service exists for users and scripts that want a small, stable search interface backed by an index that doesn't fall over under load.

## Files

- `manifest.yaml` — service metadata
- `compose.yaml` — Docker Compose fragment (builds the local image)
- `Dockerfile` — `node:20-alpine` image with the proxy
- `proxy.mjs` — the proxy itself (~100 lines)
