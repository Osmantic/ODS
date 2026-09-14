# Gotify Extension for ODS

[Gotify](https://gotify.net/) is a simple, lightweight server for sending and receiving push notifications in real-time via WebSockets or REST API.

## Quick Start

```bash
ods enable gotify
```

Open `http://localhost:7843` in your browser. Default login is `admin` with password configured in `GOTIFY_DEFAULTUSER_PASS`.

## Configuration

| Variable | Description | Default |
|----------|------------|---------|
| `GOTIFY_PORT` | Published host port | `7843` |
| `GOTIFY_DEFAULTUSER_PASS` | Default administrator password | `admin` |

## Persistence

SQLite database files, push tokens, and client credentials are saved to `./data/gotify`.
Data is retained when stopping or upgrading the container.
