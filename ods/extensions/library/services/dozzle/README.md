# Dozzle — container log viewer

A lightweight browser UI for live-tailing, searching, and filtering Docker container logs. Useful for watching the ODS stack (llama-server, open-webui, dashboard-api, extensions) without SSH access to the host.

## Setup

No secrets required. Enable **Dozzle (Log Viewer)** from Extensions and visit `http://localhost:8484`.

The container mounts the Docker socket **read-only** — Dozzle can list and tail containers but cannot start, stop, or modify them. Telemetry is disabled (`DOZZLE_NO_ANALYTICS=true`) and the UI binds to localhost by default.

## Settings

| Variable | Default | Behavior |
| --- | --- | --- |
| `DOZZLE_PORT` | `8484` | Published port; the in-container listener stays on `8080` |
| `DOZZLE_BASE` | `/` | URL path prefix for reverse-proxy subpath deployments |
| `BIND_ADDRESS` | `127.0.0.1` | Shared ODS bind address; set `0.0.0.0` for LAN access |

## Notes

- Logs come from the Docker daemon — container restarts and `docker compose` lifecycles appear automatically; no per-service configuration is needed.
- Dozzle has no built-in authentication. Keep it localhost-bound (the default) or place it behind `ods-proxy` for remote access.
