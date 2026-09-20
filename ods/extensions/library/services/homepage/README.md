# Homepage (Start Page)

A customizable start page that links every enabled ODS service from one place, with optional Docker widget integration via the read-only socket mount.

## Setup

No secrets required. Enable **Homepage** from Extensions, visit `http://localhost:3100`, and edit `data/homepage/*.yaml` to curate the layout.

## Settings

| Variable | Default | Behavior |
| --- | --- | --- |
| `HOMEPAGE_PORT` | `3100` | Published HTTP port |
| `BIND_ADDRESS` | `127.0.0.1` | Shared ODS bind address; set `0.0.0.0` for LAN access |

## Notes

- The Docker socket mount is read-only — used solely for container status widgets.
- Config files live in `data/homepage/`; see https://gethomepage.dev for widget documentation.
