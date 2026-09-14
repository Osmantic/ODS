# FreshRSS Extension

Self-hosted RSS and Atom feed aggregator and reader with responsive UI and Google Reader API compatibility.

## Quick Start

```bash
docker compose -f compose.yaml up -d
```

Visit `http://localhost:7858` in your browser to complete the initial setup wizard.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `FRESHRSS_PORT` | `7858` | Published host port for the web reader UI |
| `FRESHRSS_BASE_URL` | `http://localhost:7858` | Client-facing base URL |

## Persistence

Feed subscriptions, articles, user preferences, and custom extensions are persisted under `./data/freshrss/`:
- `./data/freshrss/data`: SQLite database, user configurations, and feed cache
- `./data/freshrss/extensions`: Installed plugins and extensions
