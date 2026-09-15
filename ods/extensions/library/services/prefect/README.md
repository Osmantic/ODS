# Prefect local Python flow orchestration

Optional Prefect 3.8.6 server and UI using the digest-pinned Python 3.12 image. It stores flow/task state, schedules, artifacts and orchestration metadata on ODS hardware. Execution belongs to an explicitly configured Python client or worker; this recipe starts no worker, mounts no Docker socket and automatically executes no user flow code.

Set `PREFECT_PASSWORD` before enabling: 16-128 ASCII letters, digits, underscores or hyphens (`openssl rand -hex 24` generates a suitable value). Open `http://localhost:4200`, or `PREFECT_PORT`. The native UI asks for the complete authentication string `ods:<your password>`. The same-origin `/api` UI setting follows custom ports and an operator's root-mounted TLS proxy without embedding container DNS names in browser requests. UI assets and readiness are public; flow data and API operations require Basic authentication. There is one shared operator credential, with no per-user RBAC or read-only sharing.

A matching `prefect==3.8.6` Python client uses `PREFECT_API_URL=http://localhost:4200/api` and `PREFECT_API_AUTH_STRING=ods:<your password>` (adjust the port). Clear an existing `PREFECT_API_KEY`, which targets Prefect Cloud and takes precedence over self-hosted authentication. Container clients on the trusted ODS network use `http://prefect:4200/api`. Basic auth is plaintext over this local HTTP hop; remote use requires a trusted TLS/access boundary.

Native CSRF protection is enabled. The matching UI/client obtains tokens automatically. A custom HTTP client must first GET `/api/csrf-token?client=<unique-client-id>` with authentication, then send `Prefect-Csrf-Client` and `Prefect-Csrf-Token` on mutations. Cross-origin browser requests are disabled. Server analytics, cloud orchestration telemetry and promotional UI content are disabled through the exact version's native settings.

ODS prepares `data/prefect` for UID 1000. SQLite and Prefect home data persist there; the image's UI bundles are copied into a writable temporary directory so the root filesystem can remain read-only. Capabilities are dropped and port 4200 defaults to loopback and honors `BIND_ADDRESS`. The recipe keeps native tini process handling and starts the server directly, without the image's optional runtime package-install or shell-profile hooks. `GET /api/ready` probes native database readiness, not the success or timeliness of a workflow.

Flow code runs wherever the client/worker runs. Result files belong to that client's configured result storage; pointing a remote client at this server does not upload arbitrary local result files into ODS. The live test deliberately runs a matching client in the isolated server container to qualify local result persistence alongside server metadata. Production workers, code locations, credentials, schedules and result storage remain explicit operator choices. Treat deployments, variables, logs, artifacts and data backups as sensitive operator information.

To rotate the shared credential, update `PREFECT_PASSWORD` and recreate the service; old API credentials fail after restart and clients must update their auth string. Stop the service before cold-copying the entire `data/prefect` directory and configuration. Restore with the same pinned version and UID 1000, then verify completed flow/task state, artifacts and any local result files you intentionally stored there. Disable/uninstall may retain state; deleting it is separate. Before upgrading, take a matching cold backup: changing the image tag alone is not a safe database downgrade.

This is a single-server SQLite deployment with no automatic retention or capacity policy. Qualification covers Linux amd64 Docker, native API authentication/CSRF/CORS, matching Python flow/task execution returning 42, artifact storage, recreation, password rotation and cold restoration including an SDK fetch of the persisted local result. Native macOS/Windows installation, browser rendering, external workers, scheduled execution/recovery, remote result stores, multi-server PostgreSQL/Redis operation, sustained concurrency and version upgrades remain unverified.

Exact contracts: [3.8.6 API server](https://github.com/PrefectHQ/prefect/blob/3.8.6/src/prefect/server/api/server.py), [CSRF endpoint](https://github.com/PrefectHQ/prefect/blob/3.8.6/src/prefect/server/api/csrf_token.py), [native API settings](https://github.com/PrefectHQ/prefect/blob/3.8.6/src/prefect/settings/models/server/api.py), [self-hosted security](https://docs.prefect.io/v3/advanced/security-settings).

## Host publication

The published ports honor ODS `BIND_ADDRESS`: unset keeps `127.0.0.1`; the
explicit LAN opt-in can select `0.0.0.0` or a specific host interface. This
controls Docker publication and preserves native authentication and application
policy. Disable/recreate after changes, and keep operator edits to the installed
definition backed up before updating or reinstalling the extension.

The native UI uses its same-origin `/api` path. CSRF checks remain enabled
and cross-origin browser access stays disabled. Configure TLS for remote credentials.

Compose regression tests cover unset, loopback, wildcard and a specific interface
while preserving target ports, credentials and storage. Actual lifecycle fixtures
remain bound to isolated loopback ports. Remote browser/TLS deployments are not
qualified by those local tests.
