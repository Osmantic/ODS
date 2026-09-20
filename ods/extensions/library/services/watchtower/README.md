# Watchtower (Image Updater)

Polls registries daily and recreates containers **labelled** `com.centurylinklabs.watchtower.enable=true` — nothing is updated unless you opt the container in.

## Setup

No port or secrets. Enable **Watchtower** from Extensions; label any *side* container you want auto-updated. ODS-managed images are pinned by tag/digest and are NOT labelled — they stay put.

## Settings

| Variable | Default | Behavior |
| --- | --- | --- |
| `BIND_ADDRESS` | `127.0.0.1` | Shared ODS bind address; set `0.0.0.0` for LAN access |

## Notes

- Label-gated (`WATCHTOWER_LABEL_ENABLE=true`): safe to run alongside the pinned ODS stack.
- Docker socket mount is read-write by necessity — the service recreates containers.
- Poll interval defaults to 24h.
