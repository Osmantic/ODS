# Shlink

Authenticated short-link creation and visit statistics for projects. MIT Shlink
5.1.6 official image pinned by digest, with private PostgreSQL 17 persistence.

## Usage

Set distinct random 64-hex `SHLINK_DB_PASSWORD` and `SHLINK_API_KEY` before installing.
The management API is `http://localhost:11121/rest/v3`, authenticated with the
`X-Api-Key` header. Use native endpoints such as `GET /short-urls` and
`POST /short-urls` with a JSON `longUrl` to manage project links. Treat the key as a
secret in project configuration; do not put it in published client-side code.

Generated links use `http://localhost:11121`, suitable only for the same computer.
For a public deployment, explicitly configure the actual domain, HTTPS reverse proxy
and ingress, then review existing links. Changing the default domain does not rewrite
previously created domains. Within the ODS network, management clients can connect
to `http://shlink:8080`; returned localhost links still refer to the browser's host.

Shlink is an API/CLI server. No graphical client is bundled and the root URL is not
presented as a working dashboard. A selected client can be connected explicitly.
Portal project association and secret delivery remain pending.

## Behavior and storage

Native startup performs schema initialization/migrations and initial API-key setup.
Changing the environment is not a substitute for revoking old keys with native
management commands; rotate PostgreSQL credentials in the database as well.
`shlink-db-data` preserves links, visits and credentials. `shlink-data` preserves
application data/cache. Back up consistently before upgrades; migrations may not
support downgrading the application against the new schema.

Automatic title fetching, GeoLite downloads/geolocation, orphan-visit tracking and
IP/referrer/user-agent collection are disabled. Basic visits can still be counted.
No external analytics provider or notification channel is connected. No links are
created by installation. Upstream startup remains intact and uses a writable root
filesystem for generated configuration/cache; the native process runs as UID 1001.
Two web workers and one background worker are explicitly set, with 256 MB PHP limits
and a 2 GB container limit. The database has a 1 GB limit and no published port.

Host port 11121 is loopback-only; other ODS-network containers can access redirects
and must authenticate for management. Public redirects are an intentional feature.

## Compatibility and validation

Official amd64/arm64 images target compatible Docker runtimes on Linux, Windows and
macOS without GPU/model requirements. Native `/rest/health` checks availability;
image build, API authentication, migrations, redirects, analytics and restore remain
pending runtime verification. No service/model was started during preparation.

Sources: https://shlink.io/documentation/install-docker-image/,
https://shlink.io/documentation/environment-variables/ and
https://github.com/shlinkio/shlink/tree/v5.1.6.
