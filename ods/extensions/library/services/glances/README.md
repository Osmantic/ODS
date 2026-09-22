# Glances Extension for ODS

[Glances](https://nicolargo.github.io/glances/) is an open-source, modern cross-platform curses and web-based monitoring tool written in Python.

## Quick Start

```bash
ods enable glances
```

Open `http://localhost:7854` in your browser. Also exposes a REST API at `/api/3/` for integration with external dashboards.

## Configuration

| Variable | Description | Default |
|----------|------------|---------|
| `GLANCES_PORT` | Published host port | `7854` |

## Persistence

Glances runs in ephemeral read-only monitoring mode querying process stats and local system sensors.
Container recreation does not require persisted state.
