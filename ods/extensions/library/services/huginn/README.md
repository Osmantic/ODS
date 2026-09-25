# Huginn for ODS

Huginn provides event-driven agents for feeds, monitoring and automation. This recipe includes the official application and a private PostgreSQL 16 database. It does not install scripts into the host OS, expose Docker or automatically connect to Portal conversations.

## Required setup

Configure `HUGINN_DATABASE_PASSWORD`, `HUGINN_SECRET_TOKEN`, `HUGINN_ADMIN_PASSWORD` and `HUGINN_INVITATION_CODE` in Extensions before enabling. Generate different random values. The Rails secret should contain at least 64 random characters and stay stable across restarts. The owner password is used for initial database seeding; changing its environment value does not reset an existing account.

The default initial username is `admin`, configurable with `HUGINN_ADMIN_USER`. Open `http://localhost:11022`, or the configured `HUGINN_PORT`. If changing the address, also change `HUGINN_DOMAIN` to the browser-facing hostname and port. `HUGINN_TIMEZONE` defaults to UTC; choose a Rails-supported zone for scheduled agents.

The upstream first-owner seed imports its demonstration scenario. Review those agents and disable or remove the demonstrations you do not want. New user accounts do not automatically import that scenario in this recipe. Additional registrations require your private invitation code. Create your own workflow and inspect its emitted events before connecting outbound destinations such as email.

## Networking and integrations

Only the web port is published, on loopback by default. PostgreSQL has no host mapping. The upstream Smokescreen egress proxy is enabled to restrict private-address requests from agents; explicitly configure an allowlist if a workflow intentionally needs an internal destination. No paid service or model is required. Provider-specific workflows can still require their own credentials.

Huginn is an automation application, not a replacement model server. It neither changes the selected ODS model nor assumes a CUDA/Metal backend. Connecting a workflow to a model endpoint is a separate explicit configuration step.

## Storage and upgrades

The named volume `huginn-db-data` stores the database at `/var/lib/postgresql/data`. Workflows, credentials, accounts and events live there. The application image handles migrations; the database is created by PostgreSQL before application startup. Use a database-consistent PostgreSQL backup before updates; retain the application secret and configuration separately. Do not migrate this volume to another PostgreSQL major version without its supported migration procedure.

The app image also declares an unused internal MySQL volume. This recipe selects the external PostgreSQL adapter and does not use that MySQL store for application data. The updated ODS host agent stops both `huginn` and `huginn-db` on disable, retaining persistent data.

## Platforms and validation

The application and database images provide amd64 and arm64 Linux variants; Windows/macOS need Linux-container Docker. The app is nonroot, runs on CPU and has a 2 GB limit; the database has a 1 GB limit. HTTP readiness checks the sign-in route, and database readiness uses `pg_isready`. Account creation, event processing, scheduled work and persistence remain runtime validation tasks; configuration checks alone do not verify them.

Upstream: [Huginn](https://github.com/huginn/huginn), MIT, with its documented [Docker deployment](https://github.com/huginn/huginn/tree/master/docker/multi-process). Digest evidence is recorded in `upstream.json`.
