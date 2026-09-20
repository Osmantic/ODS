# Dockge (Compose Manager)

A web UI for managing extra Docker Compose stacks alongside ODS — edit YAML, pull images, and tail stack logs.

## Setup

No secrets required at install (Dockge creates its admin account on first visit — open `http://localhost:5001` promptly after enabling).

## Settings

| Variable | Default | Behavior |
| --- | --- | --- |
| `DOCKGE_PORT` | `5001` | Published HTTP port |
| `DOCKGE_STACKS_DIR` | `./data/dockge/stacks` | Host directory Dockge manages stacks from |
| `BIND_ADDRESS` | `127.0.0.1` | Shared ODS bind address; set `0.0.0.0` for LAN access |

## Notes

- Dockge's socket mount is read-write by design — it manages stacks. It manages only `data/dockge/stacks`, not the ODS compose project.
- Do not point Dockge at the ODS install directory; it is meant for side stacks.
