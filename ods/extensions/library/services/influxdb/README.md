# InfluxDB OSS

Time-series storage with the v2 write/query API, Flux and embedded dashboards. This recipe deliberately uses maintained OSS 2.9.1 for these interfaces; InfluxDB 3 is a different major architecture, not an interchangeable image upgrade.

## Initialization

Provide distinct random 64-hex INFLUXDB_ADMIN_PASSWORD and INFLUXDB_ADMIN_TOKEN values. Native first initialization creates user ods, organization ods and an empty projects bucket with 30-day retention. Open http://localhost:11135 with that user/password. No synthetic measurements, collector, task, dashboard or project client is created.

The initial token has operator privileges. Keep it private and create per-project tokens with only the bucket permissions needed by each client. Environment values are visible to administrators with Docker inspection access; the native CLI also persists its configuration/token in the config volume. Treat both volumes and the installation configuration as sensitive. Native token hashing does not protect copies stored by clients.

Initialization is one-time: the native entrypoint skips setup when its database exists. Changing these environment variables does not reset a password, rotate existing tokens or alter the retention policy. Use the native administrator UI/API to make those changes. Do not delete persistent volumes to rotate credentials.

## Project use

Configure a host client with http://localhost:11135, organization ods and the chosen bucket/token. An explicitly attached ods-network container uses http://influxdb:8086 instead. Configure the project's collector or line-protocol client with its scoped token; installing this extension does not start system collection, associate Portal projects or supply model access automatically. Create separate buckets/retention policies when projects require isolation or longer history. The initial 30-day policy expires older points.

The host port is loopback-only. Authentication protects the API but other ods-network containers can reach the endpoint. Deliberately configure TLS ingress before remote use.

## Persistence and resources

influxdb-data retains the engine, metadata and write-ahead log under /var/lib/influxdb2. influxdb-config retains server and CLI configuration under /etc/influxdb2. Back up both while stopped, or use the upstream authenticated backup/restore commands for a live instance. Keep credentials separately. Copying only the engine directory is not a complete backup.

The image runs as UID/GID 1000, with a read-only root and temporary /tmp. Native setup and startup are preserved; the temporary initialization listener is not published to the host. Two CPUs and 2 GiB memory bound the service; high-cardinality or large workloads need deliberate tuning. Anonymous reporting is disabled. GPU and model selection are unchanged.

The /health probe verifies server health, not that a project writes correct measurements or that backups restore successfully.

## Provenance and verification

MIT OSS 2.9.1 and official image pinned by digest; amd64 and arm64 verified in registry metadata. Named volumes avoid OS-specific host paths for Linux Docker and Windows/macOS Linux-container runtimes. Build, initial setup/login, authenticated writes/queries, retention, restore and actual platform execution remain unverified. No containers or models were started.

Sources: [source license](https://github.com/influxdata/influxdb/blob/v2.9.1/LICENSE), [native image startup](https://github.com/influxdata/influxdata-docker/tree/129b84f0088aa9bf24bccff6a011fb6717b701aa/influxdb/2.9), [Docker setup](https://docs.influxdata.com/influxdb/v2/install/use-docker-compose/).
