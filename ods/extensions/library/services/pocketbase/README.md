# PocketBase for ODS

An MIT application backend with collection APIs, authentication and file storage
backed by SQLite. This recipe packages the official v0.40.4 release binary; it
does not assume an official PocketBase Docker image exists or use a third-party
application wrapper.

## First setup and use

Install `pocketbase` and open `http://localhost:11068/_/`. Follow upstream's initial
superuser setup flow. If it requires the one-time installer link, retrieve that
link from this container's startup log (`docker logs ods-pocketbase`) and open it
using the local published port 11068. Treat the installer token as a credential.
No admin/password, demo collection or application user is created by ODS.

Create collections and define access rules before connecting a frontend. Use
`http://localhost:11068` from the host browser or `http://pocketbase:8090` from an
explicitly configured ODS consumer on `ods-network`. `POCKETBASE_PORT` changes the
host port. The service does not connect itself to Portal, change model settings,
or copy project data. Its admin interface and API are only published on loopback.

## Data, migrations and hooks

`pocketbase-workspace` contains `pb_data`, `pb_migrations`, `pb_hooks` and
`pb_public`. The native flags explicitly point to these persistent paths, so
schema migration files created by the dashboard survive container replacement.
The app filesystem is otherwise read-only; updating the executable in place is
not supported. Update the pinned recipe and recreate the container instead.

Back up the stopped volume before upgrades. Review upstream release/migration
notes: restoring a database from a newer schema into an older binary is not a
supported rollback plan. Hooks are executable application code; none are seeded
or downloaded here. Hook file auto-restart is disabled; restart the extension
after intentionally updating its hooks. Public files are served from pb_public
only when you explicitly place them there. External SMTP/S3/OAuth are not set up.

## Build and compatibility

Linux amd64 and arm64 release archives have explicit SHA256 values in fetch.sh.
The multi-stage build rejects other architectures and verifies the archive before
extracting it. Runtime uses pinned Alpine, CA certificates and UID 1000. Use Docker
Engine on Linux or Docker Desktop Linux containers on Windows/macOS. Native host
binaries are not installed. No GPU or model is required.

The native `/api/health` reports server readiness; it does not prove collection
rules, superuser setup or application behavior. Build/download verification,
setup/login, CRUD/files, hooks, migration and backup/restore runtime remain pending.
No application containers were started during this integration.

Source and assets: https://github.com/pocketbase/pocketbase/releases/tag/v0.40.4
