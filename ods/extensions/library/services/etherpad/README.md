# Etherpad for ODS

Real-time collaborative text pads with revision history, authorship colors and
synchronized editing. This Apache-2.0 application stores its own documents rather
than editing Portal project files or replacing the chat composer.

## Setup

Install `etherpad` with separate `ETHERPAD_ADMIN_PASSWORD`, `ETHERPAD_USER_PASSWORD`,
`ETHERPAD_DATABASE_PASSWORD`, `ETHERPAD_ADMIN_SECRET` and `ETHERPAD_USER_SECRET`.
Use strong passwords and independent random OAuth client secrets. Open
`http://localhost:11070`, authenticate as `user` for editing or `admin` for
administration, then create a pad. A new pad starts with whitespace, not upstream
promotional text. `ETHERPAD_PORT` changes the local port and built-in login URLs.

Authentication and authorization are enabled. The initial editor account is shared
if you give its password to multiple people; author colors/names are not proof of
individual identity. Configure distinct accounts or an authentication plugin if
you need that separation. No LDAP consumer, external identity provider or API
client is connected automatically.

The built-in issuer/redirect URLs use localhost. Access from other computers
requires an explicitly configured HTTPS endpoint, matching issuer/redirect
settings and WebSocket-capable reverse proxy. Proxy trust is disabled by default.
Do not assume changing only the exposed port grants remote collaboration.

## Persistence and updates

The dedicated `etherpad-db` PostgreSQL stores pads and history and has no published
host port. `etherpad-var` preserves runtime state and `ods-settings.json`, copied
from the versioned image template only on first run. `etherpad-plugins` preserves
installed plugin packages. Back up all three volumes with the app stopped, and
retain the saved credentials. Changing the database password environment variable
does not rotate the password inside an existing PostgreSQL volume.

Settings retain their upstream environment placeholders, so ODS credentials and
port overrides continue to apply. The admin settings editor is hidden; deliberate
advanced changes go in the persisted settings file. Compare that file with the
new upstream template before upgrading because it is intentionally not overwritten.
No plugins are preinstalled by this integration; compatibility and licenses of
user-installed plugins require separate review.

The updater is notification-only: it can query upstream for release information
but does not apply updates automatically. IP logging and metrics are disabled.
SMTP and LibreOffice conversion are not configured; available import/export
formats depend on the native image. No claim of DOCX/PDF conversion is made.

## Runtime boundary

Pinned official 3.3.5 amd64/arm64 image with its native unprivileged user and
Node/tsx startup. Use Docker Engine on Linux or Docker Desktop Linux containers
on Windows/macOS. No GPU, model or host Docker socket is needed.

The HTTP health check verifies server readiness. Image startup, authenticated
editing in multiple browsers, persistence, plugin behavior and restore/platform
execution remain runtime-pending. No application containers were started.

Source: https://github.com/ether/etherpad/tree/v3.3.5
