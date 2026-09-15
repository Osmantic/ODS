# SilverBullet local Markdown workspace

Optional single-space SilverBullet 2.10.0, pinned to the upstream multi-platform image digest. The normal Extensions install prepares `data/silverbullet` for UID 1000, discovers the manifest and starts the Compose recipe. No GPU, cloud account or separate database is required.

Set distinct `SILVERBULLET_PASSWORD` and `SILVERBULLET_API_TOKEN` before enabling. Each accepts 16-128 ASCII letters, digits, underscores and hyphens; `openssl rand -hex 24` generates a suitable value. Keep them in the protected ODS `.env`. Sign in as `ods` at `http://localhost:3034` (or `SILVERBULLET_PORT`). The token grants full workspace read/write access through `Authorization: Bearer ...`; it is not a read-only share token. Missing or malformed credentials stop activation before the server starts. Docker and .env administrators can read them.

The recipe forces single-space mode even on empty storage, preventing the multi-space setup wizard from taking over. Markdown pages, attachments and native authentication state persist under `data/silverbullet`. Browser sign-in and API clients use the same space. For programmatic file operations use `/.fs/<path>` and `X-Sync-Mode: true`, as documented in the exact-version upstream HTTP API. Browser sync keeps a local copy of notes; sign out and clear site data when retiring a browser. This recipe does not promise encrypted server storage or erase browser copies when removing the service.

HTTP defaults to loopback and honors `BIND_ADDRESS` for explicit LAN publication. Use localhost, or an operator-managed HTTPS tunnel/proxy for remote browsers; service-worker APIs require a secure browser context. The shared ODS network is a trusted boundary. Shell execution is disabled (`SB_SHELL_BACKEND=off`), the headless runtime API is disabled, no Docker socket is mounted, and the container runs as UID 1000 with a read-only root and dropped capabilities. Authenticated upstream HTTP proxy operations and browser-side scripts remain available; only give workspace credentials to trusted users and review imported scripts. These controls do not turn programmable notes into a sandbox for hostile content.

`/.ping` is public availability only and contains no note content. Both the Docker healthcheck and ODS manifest probe use it. The image's default `/.instance` probe is overridden because this recipe deliberately runs one authenticated space.

To rotate access, change the password and token in `.env` and recreate the service. The native security hash invalidates existing login sessions when credentials change. To back up, stop SilverBullet and copy the entire data directory, including hidden native metadata, together with the credentials. Restore that cold copy with the same pinned version and UID 1000 ownership before restarting. Disable/uninstall can remove the container while retaining data; deleting data is a separate explicit operation. Version downgrade compatibility is not assumed: restore the matching cold backup to roll back an upgrade.

Local qualification uses Linux amd64 Docker with an isolated directory: anonymous/wrong-token rejection, browser session login, page and binary attachment CRUD/listing, shell denial, recreation, password/token rotation, old-session rejection and cold restoration. Browser rendering/offline conflict resolution, native macOS/Windows installation, external HTTPS, multi-space accounts, headless runtime, Git sync and version upgrades are not qualified by this recipe's tests.

Upstream: [2.10.0 Dockerfile](https://github.com/silverbulletmd/silverbullet/blob/2.10.0/Dockerfile), [HTTP API](https://github.com/silverbulletmd/silverbullet/blob/2.10.0/docs/HTTP%20API.md), [configuration](https://github.com/silverbulletmd/silverbullet/blob/2.10.0/docs/Install/Configuration.md).

## Host publication

The published ports honor ODS `BIND_ADDRESS`: unset keeps `127.0.0.1`; the
explicit LAN opt-in can select `0.0.0.0` or a specific host interface. This
controls Docker publication and preserves native authentication and application
policy. Disable/recreate after changes, and keep operator edits to the installed
definition backed up before updating or reinstalling the extension.

Remote browsers need an HTTPS origin for service-worker functionality.
Publishing an HTTP port on the LAN does not provide that secure browser context.

Compose regression tests cover unset, loopback, wildcard and a specific interface
while preserving target ports, credentials and storage. Actual lifecycle fixtures
remain bound to isolated loopback ports. Remote browser/TLS deployments are not
qualified by those local tests.
