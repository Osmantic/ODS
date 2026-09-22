# ClickHouse — project analytics database

Configure a strong `CLICKHOUSE_PASSWORD`, install and enable. The initial database and user are both named `ods`. The official entrypoint removes the default user when creating this configured user; no passwordless network account is enabled. The `ods` account can manage database users, so use it for initial setup and create restricted accounts for applications/reporting.

HTTP SQL is available on `http://localhost:11036`. ODS treats this as an API/database extension, not as a general-purpose browser application. A host-side check can use `curl --user ods --data-binary "SELECT version()" http://localhost:11036/`; curl prompts for the password rather than embedding it in the URL. In older PowerShell use `curl.exe`. No query is run by installation on behalf of the owner.

Other ODS containers use `clickhouse:8123` for HTTP or `clickhouse:9000` for the native protocol. Port 9000 is not published to the host. Metabase can be configured explicitly with the HTTP hostname/port, a dedicated read-only account and your selected database; neither extension is automatically given credentials or attached to the other.

## CPU and host prerequisites

The pinned 26.8.9.10 LTS image is available for amd64 and arm64, but the upstream binaries require more than that architecture label:

- x86-64-v3 instructions, including AVX2, are required on amd64.
- ARM requires ARMv8.2-A plus Load-Acquire RCpc support (mandatory from ARMv8.3-A). The upstream compatibility notes exclude some ARM boards even though Docker can run arm64 images.

Check the CPU/VM instruction support before enabling. Architecture discovery alone is not a sufficient preflight, and this recipe does not yet add a host instruction detector. Unsupported processors need a separately validated upstream build, not an assertion that the stock image works. Windows/macOS run Linux containers in Docker Desktop; Linux uses Docker Engine. No GPU or chat model is needed.

## Persistence and limits

`clickhouse-data` retains table data, metadata and access state. `clickhouse-logs` retains server logs. The official initialization handles directory ownership and drops to its service user. Named volumes avoid host-specific path and ownership conventions. Do not force a custom UID without migrating existing volume permissions.

The recipe publishes only loopback HTTP and does not use host networking, privileged mode, Docker sockets or optional Linux capability grants. It sets the recommended file-descriptor limit and a 4 GB memory/two-CPU container limit. These are baseline constraints, not a throughput guarantee. Large queries require deliberate server/query memory limits and storage planning.

Keep credentials and configuration with your backups. Use ClickHouse's supported backup/restore procedures for live data; copying an actively changing volume is not a consistent backup. Disabling preserves volumes. Do not change a restored instance's bootstrap password casually or treat an image upgrade as a database migration rollback.

The HTTP password is local configuration, not a secret to put in a query string, project source or shared connection URL. For remote access, configure TLS, limited users and network controls deliberately; do not simply widen the bind address. `CLICKHOUSE_PORT` changes the published HTTP port only.

## Verification

The `/ping` health probe checks the HTTP listener and intentionally does not use a database password. It does not establish that an authenticated query or disk recovery works. Verify `SELECT version()`, a temporary project table, insertion/query and restart persistence before relying on real datasets. Record those results separately from recipe validation.

The release/license, official image architectures, entrypoint account setup and health utility availability were inspected. Schema/Compose and ODS staging checks do not prove CPU compatibility, query correctness or runtime persistence. Those checks remain pending; no database workload was started during preparation.

- [Release source and license](https://github.com/ClickHouse/ClickHouse/tree/v26.8.9.10-lts)
- [Official image compatibility and configuration](https://github.com/ClickHouse/ClickHouse/blob/master/docker/server/README.md)
- [Versioned account initialization](https://github.com/ClickHouse/ClickHouse/blob/v26.8.9.10-lts/docker/server/entrypoint.sh)
