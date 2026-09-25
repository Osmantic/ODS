# Metabase — local analytics

Explore data sources and save queries, charts and dashboards. This recipe uses the official **open-source** `metabase/metabase` image, not `metabase/metabase-enterprise`. The versioned upstream license explicitly identifies that binary distribution as AGPL, even though the source repository also contains separately licensed Enterprise code. Paid features are not represented as available here.

## Setup

Configure `METABASE_DATABASE_PASSWORD` for the private application database and a separate persistent `METABASE_ENCRYPTION_KEY` (generate 32 random bytes). Install and enable, open `http://localhost:11033`, and complete the initial owner-account wizard. There is no default application password. Retain both configured secrets with your backup.

The included PostgreSQL database stores Metabase accounts, saved questions, dashboards and connection configuration. **It is not your analytics dataset.** Add an actual data source deliberately through the application. Prefer a read-only database account for queries. Do not reuse ODS service-administration credentials or automatically attach private service databases merely because they share a Docker network.

For container data sources, use their real Docker service hostname and internal port. `localhost` inside Metabase refers to its own container. Host-native databases require a reachable host address appropriate to that installation; this recipe does not assume a Windows-specific address on Linux or macOS. Importing CSV requires an explicitly configured writable destination supported by Metabase; it does not automatically write into the private application database.

## Runtime contract

- The pinned official main image supports Linux amd64 and arm64. Windows/macOS use Docker Desktop's Linux engine; Linux uses Docker Engine. No inference model, GPU or cloud subscription is required.
- The application starts after the private `metabase-db` health check. Database access remains inside `ods-network`, with no host database port published. The main UI binds to loopback on 11033.
- The upstream startup script prepares its service user and runs Java as a nonroot user. The recipe preserves that initialization rather than forcing a UID that may break upstream setup. No privileged mode, host bind mounts or Docker socket are granted.
- JVM heap is bounded relative to the 2 GB container memory limit; PostgreSQL has a separate 1 GB limit. Complex queries, concurrent users and driver requirements may need deliberate tuning. These limits do not control memory used by remote data sources.
- Anonymous tracking and automatic update checks are disabled. Updates are made by reviewing a new pinned image, not by mutating the running application. Model/AI functionality is not configured or claimed by this recipe.
- `METABASE_PORT` changes the host port. Also update `METABASE_SITE_URL` when changing the public origin. Remote access requires an intentional TLS/authentication setup; do not simply publish the local configuration to the internet.

## Data and recovery

`metabase-db-data` retains PostgreSQL 16 application data. Disabling preserves it. Use a consistent PostgreSQL dump and retain the encryption key when moving or restoring. Changing the key without the supported rotation procedure can make stored data-source credentials unreadable. Do not initialize an unrelated key against a restored database.

The application database does not contain backups of your connected analytics databases. Back those up separately. Custom database drivers are not mounted automatically; if needed, build and review a compatible derived image with their license and version tracked. Do not treat arbitrary downloaded JARs as trusted plug-ins.

## Checks and provenance

`/api/health` verifies application initialization, not a saved query or source connection. A useful runtime check is to finish owner setup, attach a test dataset with minimal permissions, save a question/dashboard, restart, and confirm that both configuration and query still work.

Official registry architecture data, startup script and binary license were inspected. ODS schema/Compose/staging checks do not prove browser setup, migration, query execution or recovery; those runtime checks remain pending.

- [Binary license distinction](https://github.com/metabase/metabase/blob/v0.63.18/LICENSE.txt)
- [Official Docker installation](https://www.metabase.com/docs/latest/installation-and-operation/running-metabase-on-docker)
- [Versioned startup script](https://github.com/metabase/metabase/blob/v0.63.18/bin/docker/run_metabase.sh)
