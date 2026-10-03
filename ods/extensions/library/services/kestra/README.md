# Kestra for ODS

Kestra OSS provides a visual workflow editor, schedules, execution history and task artifacts. This recipe pins version 2.0.2 with PostgreSQL 18. It includes a small storage-ownership image layer so fresh Docker volumes are writable by the upstream `kestra` user; the running application is not root.

## Configure and use

Provide `KESTRA_ADMIN_EMAIL`, `KESTRA_ADMIN_PASSWORD`, `KESTRA_DATABASE_PASSWORD` and a separate `KESTRA_MANAGEMENT_PASSWORD` in Extensions. The administrator name must be an email, and its password must meet the upstream requirement of at least eight characters including an uppercase letter and number. Use stronger random credentials for real use. Keep database and management passwords separate.

Enable Kestra and open `http://localhost:11025/`. `KESTRA_URL` must match your browser-facing address when changing `KESTRA_PORT`. Credentials are passed as environment values and referenced by the application's configuration resolver, rather than interpolated into YAML strings containing arbitrary password characters.

Import `document-report.yaml` in the flow editor. Run it with an uploaded UTF-8 document, then download `report.json` from the execution artifacts. The report uses the supplied file's contents and checksum. No flow is automatically scheduled or executed during installation; tutorial auto-import is disabled.

## Execution boundary

The example uses `io.kestra.plugin.core.runner.Process`, which executes inside the Kestra container using its bundled Python. There is no Docker socket, host working-directory mount or nested container authority. Flows requiring Docker task runners or tools absent from the image need a separately reviewed integration; selecting such a runner does not make host Docker access available.

Kestra itself does not load a model. AI tasks require explicit provider configuration; select the ODS gateway/current-model route when connecting a compatible task, and verify the plugin's requirements. Neither this recipe nor its sample changes the Portal model or context size.

## Data and shutdown

Named volumes `kestra-storage`, `kestra-work` and `kestra-db-data` hold artifacts, working files and PostgreSQL data. PostgreSQL 18 uses `/var/lib/postgresql` for its volume, distinct from older major-version layouts. Take a database-consistent backup plus artifact/configuration backups before updates; changing the image tag is not a database migration.

The server gets 45 seconds of graceful termination and Docker allows 60 seconds, fitting the current ODS host stop timeout. Disable stops both application and private database. Inspect interrupted executions before retrying externally visible tasks; do not assume every side effect is automatically reversible.

## Access and compatibility

Only port 8080 is mapped to the loopback host address. Management port 8081 is not published and uses its own credentials. The environment endpoint is disabled. `/ping` is a listener check and does not prove that a particular workflow completed.

Both base images support amd64 and arm64 Linux. Windows/macOS use Linux-container Docker. The application and database caps total 4 GB, separate from ODS and any inference backend. First enable builds the storage layer locally from the pinned upstream image.

Runtime validation remains pending: authentication, importing/running the document flow, saved artifacts after restart, schedule behavior and interrupted-execution recovery. Schema, staging and Compose checks alone do not prove these user flows.

Upstream: [Kestra](https://github.com/kestra-io/kestra), Apache-2.0. See its [security configuration](https://kestra.io/docs/configuration/security-and-secrets) and [Python task documentation](https://kestra.io/plugins/plugin-script-python/io.kestra.plugin.scripts.python.script). Provenance is recorded in `upstream.json`.
