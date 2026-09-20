# code-server (VS Code)

VS Code served in the browser, rooted at the ODS install directory — handy for editing configs, manifests, and compose files without SSH.

## Setup

Set `CODE_SERVER_PASSWORD` in `.env`, enable **code-server** from Extensions, then visit `http://localhost:8443`.

## Settings

| Variable | Default | Behavior |
| --- | --- | --- |
| `CODE_SERVER_PORT` | `8443` | Published HTTP port |
| `CODE_SERVER_PASSWORD` | `—` | Browser login password for the editor |
| `BIND_ADDRESS` | `127.0.0.1` | Shared ODS bind address; set `0.0.0.0` for LAN access |

## Notes

- The workspace root is the ODS install dir; `.env` and secrets are visible to anyone with the editor password — keep it localhost-bound.
- Sudo inside the container is disabled (`SUDO_PASSWORD` empty).
