# Linkwarden (Bookmarks)

A bookmark manager that preserves what you save — every link gets a screenshot, PDF, and HTML snapshot stored locally.

## Setup

Set `LINKWARDEN_DB_PASSWORD` and `LINKWARDEN_SECRET` in `.env`, enable **Linkwarden**, then visit `http://localhost:3200` and create the first account.

## Settings

| Variable | Default | Behavior |
| --- | --- | --- |
| `LINKWARDEN_PORT` | `3200` | Published HTTP port |
| `LINKWARDEN_DB_PASSWORD` | `—` | Password for the private PostgreSQL database |
| `LINKWARDEN_SECRET` | `—` | NextAuth secret for session signing (openssl rand -hex 32) |
| `BIND_ADDRESS` | `127.0.0.1` | Shared ODS bind address; set `0.0.0.0` for LAN access |

## Notes

- PostgreSQL runs in a private internal network with no published port.
- Archives persist in `data/linkwarden`.
