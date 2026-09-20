# Grafana (Dashboards)

Full dashboarding for the ODS stack — point it at Prometheus, Loki, or service metrics endpoints.

## Setup

Set `GRAFANA_ADMIN_PASSWORD` in `.env`, enable **Grafana**, then visit `http://localhost:3101` (login `admin`). Reporting/update checks are disabled.

## Settings

| Variable | Default | Behavior |
| --- | --- | --- |
| `GRAFANA_PORT` | `3101` | Published HTTP port |
| `GRAFANA_ADMIN_PASSWORD` | `—` | Initial admin password |
| `BIND_ADDRESS` | `127.0.0.1` | Shared ODS bind address; set `0.0.0.0` for LAN access |

## Notes

- Pair with the Netdata extension's Prometheus exporter or add your own data sources.
- Dashboards persist in `data/grafana`.
