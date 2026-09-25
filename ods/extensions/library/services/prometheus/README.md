# Prometheus — local metrics collection and PromQL

Prometheus 3.14.0, Apache-2.0, official image pinned by digest. Upstream: https://github.com/prometheus/prometheus/tree/v3.14.0.

The native web UI/API is available at `http://localhost:11101/`. Initial configuration collects only this Prometheus instance's own metrics every 30 seconds. These are real service metrics, not simulated host/GPU/model performance. No host mounts, Docker socket, hardware exporter, remote-write destination, alert recipient or cloud account is configured.

## Configure a project

The first startup copies the bundled configuration to `/prometheus/config/prometheus.yml` in the persistent volume. Later startups preserve it and validate it with the native `promtool` before serving.

Copy it out with `docker cp ods-prometheus:/prometheus/config/prometheus.yml ./prometheus.yml`, edit locally, then copy the file to `/tmp/candidate.yml` inside this container. Validate that candidate with `docker exec ods-prometheus /bin/promtool check config /tmp/candidate.yml`. After successful validation, copy it as the default container user with `docker exec ods-prometheus cp /tmp/candidate.yml /prometheus/config/prometheus.yml` and restart the extension. Keep a backup of the prior configuration; do not overwrite the data directory. Copy referenced rule/credential files deliberately and restrict their permissions to the service user.

Add an explicit `scrape_configs` job for the project's actual Prometheus-compatible endpoint. Use the service's Docker name/internal port for a peer in `ods-network`; `localhost` refers to this collector. Configure the correct TLS/authentication rather than disabling certificate verification. Keep target/sample limits appropriate to the project and available memory. This recipe does not auto-discover or scrape unrelated services.

If Grafana is installed, explicitly create a Prometheus data source using `http://prometheus:9090`. Grafana is optional and not installed as a dependency. No dashboard is preloaded with invented project metrics.

## Storage and access

The named `prometheus-data` volume holds owner configuration and `/prometheus/tsdb`. Retention is 15 days or 2 GB of TSDB blocks, whichever expires first; this is not a hard total disk quota because WAL/head data and other files also consume disk. Resources are two CPUs and one GiB memory; query concurrency is four with a 30-second timeout. Adjust deliberately for large metric cardinality.

Use a stopped, consistent volume backup for restore. Preserve UID/GID 65534 ownership. Never point this directory at unrelated PostgreSQL or other application storage. Read-only root filesystem and bounded temporary space are used.

No application login is configured: port 11101 binds only to loopback, but other services on `ods-network` can query the API. Metrics can contain sensitive labels, so configure authentication/TLS through a reviewed web configuration or proxy before wider exposure. HTTP lifecycle reload, administrative API and remote-write receiving are not enabled.

## Compatibility and validation

Official multiarchitecture image supports the targeted Linux amd64/arm64 Docker environments on Windows/Linux/macOS, without GPU or model dependency. Native `/-/ready` checks TSDB readiness; it does not prove that configured external targets can be scraped. Schema/packaging checks are separate from image startup, actual scraping/queries, retention and restore validation, which remain pending. No container/model was started during preparation.
