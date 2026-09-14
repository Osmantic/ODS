# VictoriaMetrics — local metric history

An optional single-node VictoriaMetrics 1.152.0 database with its built-in VMUI. Use it for historical numeric measurements from an explicitly configured Prometheus remote-write producer or HTTP importer. It collects its own operational metrics every 30 seconds; it does not automatically scrape ODS services or replace Token Spy's dated request/cost reports.

## Setup

Set a strong `VMETRICS_PASSWORD` in ODS `.env`, then install **VictoriaMetrics (Metric History)** from Extensions. Compose refuses an unset or empty password. Open `http://localhost:8428/vmui/` and use HTTP Basic Auth username `ods` and your configured password. The same credentials protect queries, ingestion and administrative API operations. `/health` is intentionally public and contains no series data.

A producer on the ODS network can remote-write to `http://victoriametrics:8428/api/v1/write` using a protected Basic Auth credential. Host producers use the published port instead. Configure producers explicitly and avoid secrets or personal content in metric labels. The service has no Docker socket, host telemetry mount, cloud exporter or automatic model-runtime integration.

To verify ingestion from the host, let curl prompt for the password:

```bash
printf 'ods_operator_probe 1\n' | curl --fail --user ods \
  --data-binary @- http://localhost:8428/api/v1/import/prometheus
```

Search `ods_operator_probe` in VMUI. Ingestion and query visibility are asynchronous; the lifecycle test explicitly flushes the isolated store before checking its receipt.

## Settings and lifecycle

| Variable | Default | Meaning |
| --- | --- | --- |
| `VMETRICS_PORT` | `8428` | Published HTTP port |
| `VMETRICS_PASSWORD` | required | Password for the `ods` account |
| `VMETRICS_RETENTION` | `30d` | Retention duration; shortening it can remove old samples |

The default binding is loopback. Use a trusted HTTPS access path before sending credentials across a network. This is a single administrative account, not a multi-tenant authorization layer. Rotate the password in ODS and all configured producers together, then recreate the container. Existing samples remain intact.

Data persists in `data/victoriametrics`. The process is limited to 1 GB RAM and 2 CPUs; its cache budget is 512 MiB and four queries can execute concurrently. These limits do not replace disk capacity monitoring or producer/cardinality limits. No GPU is needed.

Before upgrading, stop `ods-victoriametrics` and copy the entire data directory to a private backup, then start it again. Restore a verified backup with its original pinned image for rollback; retain the original data until validation finishes. Disable/recreate preserves samples. Retention cleanup is normal data deletion, so export history you need beyond the configured window. Larger deployments and online backups should follow the upstream snapshot/vmbackup procedure.

The checked-in opt-in test exercises authentication, actual import/query results and recreation using isolated data. Full ODS installation, cross-platform filesystem behavior, real exporters, sustained ingestion load and long-duration retention remain environment-specific checks.

References: [single-node deployment and environment flags](https://docs.victoriametrics.com/victoriametrics/single-server-victoriametrics/), [HTTP APIs](https://docs.victoriametrics.com/victoriametrics/url-examples/), [v1.152.0 authentication implementation](https://github.com/VictoriaMetrics/VictoriaMetrics/blob/v1.152.0/lib/httpserver/httpserver.go).
