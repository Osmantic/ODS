# OpenObserve: local observability

Install **OpenObserve (Local Observability)** from Extensions, set a unique
`OPENOBSERVE_PASSWORD`, then enable the service. Open
`http://localhost:5080/web/` and log in as `ods@localhost.invalid`. The optional
`OPENOBSERVE_PORT` changes both the loopback host port and allowed local browser
origins. `BIND_ADDRESS` selects the published interface; loopback remains the default.

The pinned OpenObserve v1.0.0 image provides its native UI, log/metric/trace
HTTP ingestion and SQL search. This recipe uses local mode, SQLite metadata and
local disk storage. It does not configure an agent, collect ODS logs, change
existing services or send their telemetry anywhere. Configure clients explicitly
when you want to ingest data. From the host use `http://127.0.0.1:5080`; an ODS
container on `ods-network` uses `http://openobserve:5080`. Both require native
credentials. The internal gRPC listener is bound to container loopback and is
not published.

## Credentials and browser access

Generate a password with `printf 'Aa1_'; openssl rand -hex 24`. The ODS schema
allows 8–128 URL-safe characters, including a lowercase letter, uppercase
letter, digit and `_` or `-`; the native server enforces its own password
strength policy when bootstrapping. Missing/empty credentials block Compose
activation. Keep the ODS environment private. The original distroless image
runs directly without a custom password wrapper.

`OPENOBSERVE_PASSWORD` creates the root account for a **new database only**.
Changing the environment and recreating an existing service does not reset its
password. Change it through native profile settings with the current password,
verify a fresh login and then update the bootstrap value for future clean
installations. The HTTP regression test confirms that the previous Basic
credential and login cookie stop authenticating after native password rotation.
Other native credentials, such as ingestion tokens, have their own lifecycle;
this test does not prove their revocation. Use OpenObserve's account/token
management before distributing credentials to clients.

The login cookie is HTTP-only, SameSite=Lax and expires after 30 minutes. Native
v1.0.0 encodes the Basic credential in that cookie; it is sensitive. This recipe
uses local HTTP, not TLS. Native CORS permits only `localhost` and `127.0.0.1`
with the configured port; a foreign browser origin is not granted credentialed
CORS access. CORS is not authentication. Keep root credentials restricted to
trusted local operators. This recipe does not claim multi-tenant role isolation.

## Data and resource limits

All application state, including SQLite databases, WAL, indexes and stored
telemetry, lives in `data/openobserve`, prepared by ODS for UID/GID 10001. The
service uses a read-only container filesystem, a 128-MiB temporary filesystem,
dropped capabilities, two CPUs and 2 GiB RAM. DataFusion's memory limit is
512 MiB; optional memory/disk data caches are disabled. Adjust sizing for real
workloads only after measuring them.

Retention is configured to seven days. Native compaction applies retention
asynchronously; this is not an immediate disk quota. Stream-specific settings
and native ingest timestamp limits still apply. No automatic source collection,
backup or live retention-duration qualification is included.

Native product telemetry, browser RUM instrumentation and self-trace export are
explicitly disabled. This does not disable **receiving** traces from your own
clients. These settings were checked against the shipped tag; the test validates
local API operation, not a comprehensive network-egress audit. Operators can
configure native integrations that make external requests.

## Health, backup and removal

ODS probes `/healthz`. The distroless image contains no shell, curl or wget, so
this recipe does not declare an unusable Docker healthcheck. Container running
status alone is not API readiness; the opt-in test waits for an HTTP response.

Stop the service, cold-copy the entire `data/openobserve` directory to a separate
backup destination and preserve ownership and permissions. To restore, stop the
service, restore the complete directory and start the same pinned image. Verify
root login and queries against each stored signal type. Disabling/removing the
extension retains this directory. Keep a compatible cold backup before changing
images; downgrading an image alone is not a database rollback strategy.

## Qualification

```bash
python -m pytest tests/test_openobserve.py -q
ODS_TEST_OPENOBSERVE=1 python -m pytest tests/test_openobserve.py -q
cd extensions/services/dashboard-api
pytest tests/test_openobserve_install.py tests/test_extensions.py tests/test_host_agent.py -q
```

The real-image fixture exercises HTTP login/cookies, unauthorized read/write,
local/foreign CORS, Unicode/newline log ingestion and SQL aggregation, native
JSON gauge ingestion, OTLP JSON trace ingestion, recreation with a changed
bootstrap seed, native password rotation and cold restore of all three signal
types. The install test exercises authenticated catalog installation and host
ownership preparation. Static tests verify port/origin overrides and required
credentials.

Interactive browser rendering, role isolation, ingestion-token rotation, load,
long-running retention, clustered/object storage, upgrades and native macOS or
Linux hosts remain unqualified. Linux/amd64 Docker Desktop on WSL Ubuntu is the
recorded runtime environment.

Native contracts: [v1.0.0 configuration](https://github.com/openobserve/openobserve/blob/v1.0.0/src/config/src/config.rs),
[HTTP routes and CORS](https://github.com/openobserve/openobserve/blob/v1.0.0/src/api/http/src/handler/http/router/mod.rs),
[account/login handlers](https://github.com/openobserve/openobserve/blob/v1.0.0/src/api/management/src/request/users/mod.rs).

## Host publication

The published ports honor ODS `BIND_ADDRESS`: unset keeps `127.0.0.1`; the
explicit LAN opt-in can select `0.0.0.0` or a specific host interface. This
controls Docker publication and preserves native authentication and application
policy. Disable/recreate after changes, and keep operator edits to the installed
definition backed up before updating or reinstalling the extension.

For a remote browser origin, set `ZO_WEB_URL` and the explicit
`ZO_CORS_ALLOWED_ORIGINS` in the installed `compose.yaml` to the intended URLs.
Port publication alone does not update native browser-origin policy or configure TLS.

Compose regression tests cover unset, loopback, wildcard and a specific interface
while preserving target ports, credentials and storage. Actual lifecycle fixtures
remain bound to isolated loopback ports. Remote browser/TLS deployments are not
qualified by those local tests.
