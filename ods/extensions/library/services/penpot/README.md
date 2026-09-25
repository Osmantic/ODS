# Penpot — local product design

Create vector layouts, reusable component libraries, interactive prototypes and exported assets. This recipe installs the upstream community application with its backend, exporter, MCP companion, PostgreSQL and Valkey. These are one extension, not six catalog entries.

## Configure and open

1. Install from ODS Extensions. Set `PENPOT_DATABASE_PASSWORD` to a random persistent password and `PENPOT_SECRET_KEY` to a separate 512-bit random secret. For example, Python's `secrets.token_urlsafe(64)` generates a suitable signing secret; save the result in extension configuration, never in project source.
2. Enable the extension. The main frontend image has a small local build layer; all upstream images are pinned by digest. Initial database migration can take time. Open the ODS launch action at `http://localhost:11026` and create your account.
3. After creating the required accounts, replace `enable-registration` with `disable-registration` in `PENPOT_FLAGS`, retaining the other flags, and recreate the frontend/backend through the extension lifecycle. Do not overwrite existing database credentials when restarting.
4. Create a team/project, add a design file, upload an image, create two boards, connect a prototype interaction and export an asset. Reopen after a stop/start to verify persistence on your machine.

`PENPOT_PORT` changes the loopback host port. Also update `PENPOT_PUBLIC_URI` to exactly the browser-facing origin. The exporter uses Docker DNS (`http://penpot:8080`) internally, avoiding host-OS-specific addresses.

## Local behavior and boundaries

- No GPU, selected chat model, cloud API key or hosted Penpot account is required. All six pinned images support Linux amd64 and arm64. Windows/macOS use Linux containers in Docker Desktop; Linux uses Docker Engine. Neither CUDA nor Metal is assumed. Browser rendering and large design files still depend on the client machine.
- Allow roughly 6 GB of available container memory for the stack and additional disk space for images, designs and exports. The configured limits bound each component; they are not a performance guarantee for large workspaces.
- Defaults disable telemetry, external font/template retrieval, SMTP and email verification for local use. Local font uploads remain available. Password-reset emails and email invitations require actual SMTP configuration; no fake mail server is installed. Keep account credentials available.
- Only the UI is published, bound to `127.0.0.1`. Database, cache, exporter and MCP ports remain private. Local HTTP uses non-secure session cookies. Before remote exposure, configure HTTPS, secure cookies, verification and real SMTP; the shipped local flags are not a remote deployment configuration.
- The upstream MCP companion is included for Penpot's own feature path. This recipe does not automatically connect it to Portal, grant it access to projects or claim AI editing is configured.
- The separate Nitrate administrative console is not part of the community Compose stack. Upstream 2.17.2 Nginx nevertheless references its missing hostname. The supplied Dockerfile replaces only that location with a 404 response, preventing Nginx startup DNS failure without routing administration requests to an unrelated service. Normal Penpot team/project administration remains in the application.

## Storage, health and lifecycle

`penpot-db` retains PostgreSQL 15 data; `penpot-assets` retains uploaded assets and generated files. Backend and frontend use the upstream UID 1001, and the image prepares the asset directory ownership; the frontend mount is read-only. Named volumes avoid Windows/Linux/macOS host-path and ownership assumptions. Valkey holds notification/cache state and is disposable.

Back up both PostgreSQL (a consistent database dump) and assets, together with the signing key and configuration. Stop writes during a coordinated backup. Disabling the extension preserves volumes. Do not delete volumes or change the PostgreSQL major version without an explicit backup/migration procedure.

The frontend health check requests `/readyz`, which proxies to the backend's actual readiness handler; backend startup waits for PostgreSQL and Valkey. This is stronger than checking the static login HTML, but it does not prove that browser editing, export, MCP or recovery works. All private service names start with `penpot-` so the ODS stop operation includes them without stopping unrelated shared extensions.

## Provenance and verification

- [Upstream source and MPL-2.0 license](https://github.com/penpot/penpot/tree/2.17.2)
- [Versioned deployment configuration](https://github.com/penpot/penpot/blob/2.17.2/docker/images/docker-compose.yaml)
- [Versioned Nginx template](https://github.com/penpot/penpot/blob/2.17.2/docker/images/files/nginx.conf.template)
- [Configuration reference](https://help.penpot.app/technical-guide/configuration/)

Registry manifests, image configuration, upstream source and ODS installation definitions were inspected. Application runtime, browser flows, persistence recovery and Docker image build remain pending; configuration/staging checks do not substitute for these checks.
