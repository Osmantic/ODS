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
| `BRAVE_SEARCH_SEARXNG_COMPAT` | `0` | Set to `1` to enable the searxng-compatible `/search` route (see below) |
| `BRAVE_SEARCH_TIMEOUT_MS` | `8000` | Upstream request timeout (advanced) |
| `BRAVE_SEARCH_UPSTREAM_URL` | Brave API URL | Override the upstream endpoint (testing/advanced) |

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

### `GET /search?format=json&q=<query>` (searxng compatibility mode, opt-in)

Disabled by default (`404`). Enable it in `.env` and restart the service:

```
BRAVE_SEARCH_SEARXNG_COMPAT=1
```

The route returns a searxng-shaped JSON document, so consumers that speak
searxng's JSON API (Perplexica, Open WebUI web search, scripts) can point
their searxng URL at this service:

```json
{
  "query": "your query",
  "number_of_results": 0,
  "results": [
    {
      "url": "https://...",
      "title": "...",
      "content": "...",
      "engine": "brave",
      "engines": ["brave"],
      "positions": [1],
      "score": 1.0,
      "category": "general",
      "template": "default.html",
      "parsed_url": ["https", "example.com", "/path", "", "", ""],
      "publishedDate": null,
      "thumbnail": "https://..."
    }
  ],
  "answers": [],
  "corrections": [],
  "infoboxes": [],
  "suggestions": [],
  "unresponsive_engines": []
}
```

Parameters:

| Param | Behavior |
|---|---|
| `format` | Required. Only `json` is supported; anything else returns `400 unsupported_format` |
| `q` | Required. Missing returns `400 missing_query_param_q` |
| `pageno` | 1-based page (searxng semantics), mapped to Brave's 0-based offset, capped at page 10 |
| `categories` | Only `general` is servable. Other categories (`images`, `videos`, `news`, …) return empty results with an honest `unresponsive_engines` entry |
| `engines` | If specified and `brave` is not among them, returns empty results with `unresponsive_engines` entries — the requested engines don't exist here |
| `language`, `time_range`, `safesearch` | Accepted but ignored |

Fidelity limits (degraded but honest — fields Brave cannot supply are empty,
never fabricated):

- `suggestions`, `answers`, `corrections`, `infoboxes` are always empty arrays.
- `number_of_results` is always `0` (searxng's "no engine estimate" convention).
- `score` is synthesized from result order using searxng's single-engine formula (`1/position`).
- `publishedDate` is set from Brave's `page_age` when present, else `null`.
- Only web (`general`) results are served. Perplexica's image/video/academic
  focus modes will honestly return no results rather than mislabeled web hits.

Upstream failures follow searxng's engine-failure contract — HTTP `200` with
empty `results` and a reason in `unresponsive_engines` (e.g.
`[["brave", "too many requests"]]`, `[["brave", "timeout"]]`) — because that
is what searxng consumers already handle when an engine is throttled.
Requests with missing/invalid `q` or `format` still fail with `400`.

### Pointing Perplexica at Brave

With this service enabled and `BRAVE_SEARCH_SEARXNG_COMPAT=1`, set in `.env`:

```
PERPLEXICA_SEARXNG_API_URL=http://brave-search:8585
```

and restart Perplexica. Web-focus research then uses Brave's index. Note that
Perplexica's image, video, and specialty focus modes (academic, Reddit, …)
request engines Brave cannot serve and will return empty results; keep the
default searxng URL if you rely on those.

### `GET /health`

Returns `{ "ok": true }`. Used by the dashboard healthcheck.

## What this is *not*

The default `/v1/search` surface is intentionally not searxng's API. The
opt-in compatibility mode above covers searxng consumers, but it serves a
single engine and only the `general` category — it is a pragmatic bridge, not
a full searxng replacement. This service exists for users and scripts that
want a small, stable search interface backed by an index that doesn't fall
over under load.

## Files

- `manifest.yaml` — service metadata
- `compose.yaml` — Docker Compose fragment (builds the local image)
- `Dockerfile` — `node:20-alpine` image with the proxy
- `proxy.mjs` — the proxy itself (~100 lines)
