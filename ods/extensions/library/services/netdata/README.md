# Netdata (Monitoring)

Real-time, per-second monitoring of the host and every ODS container — CPU, RAM, disk I/O, network, Docker metrics — with zero config.

## Setup

No secrets required. Enable **Netdata** from Extensions and visit `http://localhost:19999`.

## Settings

| Variable | Default | Behavior |
| --- | --- | --- |
| `NETDATA_PORT` | `19999` | Published HTTP port |
| `BIND_ADDRESS` | `127.0.0.1` | Shared ODS bind address; set `0.0.0.0` for LAN access |

## Notes

- Host mounts are read-only (`/proc`, `/sys`, root filesystem, Docker socket).
- Netdata Cloud is not configured; everything stays local.
