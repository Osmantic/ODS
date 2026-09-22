# Wiki.js — shared project documentation

Wiki.js 2.5.314, AGPL-3.0, official image pinned for amd64/arm64. Upstream: https://github.com/requarks/wiki/tree/v2.5.314. Native database migrations and first-owner setup are preserved.

## Setup and use

Set `WIKIJS_DB_PASSWORD` to 64 random hexadecimal characters through ODS configuration. This is a database secret, not the browser login password. Open `http://localhost:11107/` and complete the native setup wizard to create your own administrator credentials and site URL. No administrator, demo article or preset browser password is inserted by this recipe.

Complete setup locally before exposing the service. Review guest permissions and disable public access when building a private knowledge base; this recipe does not invent a private-site flag or bypass native group/page permissions. Configure who can read/edit each project area in Wiki.js. There is no automatic import of Portal conversations or project files, and no inference/model connection.

Setup includes the upstream telemetry choice; leave it disabled if you do not want reporting. Wiki.js may fetch locale packages and update metadata from upstream. This is a self-hosted application, not a claim of zero network traffic. External authentication, SMTP, Git synchronization, storage providers and analytics require explicit owner configuration; none are preconfigured here. Mail-based invitations/recovery require a working mail service.

## Persistence

A dedicated PostgreSQL 16 container stores users, pages, permissions and application configuration on `wikijs-db-data`. It has no published host port and resides on the private `wikijs-internal` network. The app also stores content/cache/upload data on `wikijs-data` at `/wiki/data`. Back up both consistently, retain the database secret and preserve the app's UID 1000 ownership when restoring. Database backups must use PostgreSQL-aware tools or a stopped consistent copy.

The database password initializes only an empty PostgreSQL volume. Rotating it requires an authenticated database role password change and matching application configuration, not deleting volumes. Major database upgrades need a supported migration procedure. Extension removal does not authorize deleting stored documentation.

App resources: two CPUs, one GiB memory (Node heap 768 MiB), read-only root with bounded temporary space; database memory one GiB. Upstream nonroot application startup is preserved. Local HTTP is loopback-only; use a deliberate HTTPS/root-URL setup for remote users. Other ODS network peers can reach the application, which relies on native permissions after setup.

## Verification

Official Linux amd64/arm64 containers target Docker on Windows/Linux/macOS, without GPU/model dependency. The `/healthz` route checks the database pool after setup; during onboarding, upstream serves its setup page for unknown routes, so an HTTP 200 is only availability, not proof that setup is complete.

Image build, actual wizard/login, page editing/uploads, permissions, migrations and restore/platform runtime remain pending. Only configuration/packaging checks were run; no app, database or model was started.
