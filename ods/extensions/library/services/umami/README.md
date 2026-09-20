# Umami (Analytics)

Self-hosted, privacy-friendly web analytics — page views, referrers, and events without sending data to a third party.

## Setup

Set `UMAMI_DB_PASSWORD` and `UMAMI_APP_SECRET` in `.env`, enable **Umami**, visit `http://localhost:3030` (default login `admin`/`umami` — change it immediately).

## Settings

| Variable | Default | Behavior |
| --- | --- | --- |
| `UMAMI_PORT` | `3030` | Published HTTP port |
| `UMAMI_DB_PASSWORD` | `—` | Password for the private PostgreSQL database |
| `UMAMI_APP_SECRET` | `—` | Random salt for auth tokens (openssl rand -hex 32) |
| `BIND_ADDRESS` | `127.0.0.1` | Shared ODS bind address; set `0.0.0.0` for LAN access |

## Notes

- PostgreSQL runs in a private internal network with no published port.
- Telemetry is disabled (`DISABLE_TELEMETRY=1`).
