# Change Detection

Watches web pages for changes and keeps a local diff history — useful for tracking model releases, docs, or prices without an external service.

## Setup

No secrets required. Enable **Change Detection** from Extensions and visit `http://localhost:5555`.

## Settings

| Variable | Default | Behavior |
| --- | --- | --- |
| `CHANGEDETECTION_PORT` | `5555` | Published HTTP port |
| `BIND_ADDRESS` | `127.0.0.1` | Shared ODS bind address; set `0.0.0.0` for LAN access |

## Notes

- Add a strong admin password in Settings before exposing beyond localhost.
- Fetches run from your host IP; be considerate with watch intervals.
