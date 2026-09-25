# Wakapi for ODS

Self-hosted programming activity statistics by project, language and editor.
It receives WakaTime-compatible heartbeats; installing this server alone does
not monitor your editor, run a model or collect existing Portal conversations.

## Account and editor setup

Set `WAKAPI_PASSWORD_SALT` to a persistent random secret of at least 32 characters
before enabling. This is the password-hashing pepper, not an account password.
Keep it with your backup; changing it can invalidate existing password logins.

Open `http://localhost:11050`, register your account and retrieve its API key
from the settings page. Registration is enabled for local first use. Set
`WAKAPI_ALLOW_SIGNUP=false` after creating the desired accounts and recreate
the service through ODS to apply it. No account or API key is fabricated.

Install a WakaTime-compatible plugin in an editor you choose, then edit its
WakaTime configuration (commonly `.wakatime.cfg` in your home directory):

```ini
[settings]
api_url = http://localhost:11050/api
api_key = YOUR_OWN_WAKAPI_API_KEY
```

Preserve other existing settings. This selects the local backend; it does not
configure an additional cloud destination. A remote/containerized editor has a
different `localhost` and needs an explicitly reachable endpoint instead.
Review the editor plugin's file/project privacy settings before enabling it;
activity metadata can include paths and project names. The server does not
install editor plugins or rewrite user configuration automatically.

## Storage and access

`wakapi-data:/data` retains SQLite activity, accounts and configuration data.
Back up that volume while stopped and retain ownership UID/GID 65532, together
with the password pepper. Disabling the extension must preserve the volume.
Keep pre-upgrade backups for migrations. The upstream default session key is
generated at startup, so a restart can require logging in again; the database
and activity remain persistent. No stable-session guarantee is implied.

The port is loopback-only. HTTP cookies are enabled for this localhost setup;
remote use requires HTTPS, a matching public URL and secure-cookie configuration.
Trusted proxy authentication and metrics exposure remain off. Public leaderboards,
remote history imports and mail are disabled. Password-reset emails therefore
require deliberate SMTP configuration before they can work.

## Runtime and verification

Official MIT **2.18.0** image pinned by digest for amd64, arm64 and ARM Linux.
Windows/macOS require a Linux Docker engine. Upstream prepares `/data` for its
distroless nonroot user; the recipe keeps UID/GID 65532, original startup and
adds Docker init. No custom build or root initialization is required.

The one-CPU/1 GiB limits suit initial personal use; substantial activity volume
may require adjustments. No GPU backend, selected AI model or context limit is
changed. The bundled `/app/healthcheck` requests `/api/health`, requires HTTP 200
and times out after two seconds. It checks responsiveness, not valid heartbeats
or correct aggregates.

**Account setup, editor ingestion, aggregation, login and restart/restore remain
runtime-unverified.** Schema/Compose/staging checks do not establish those
behaviors. No editor tracking, server container or model inference was started.

- [Versioned source and client instructions](https://github.com/muety/wakapi/tree/2.18.0)
- [Configuration](https://github.com/muety/wakapi/blob/2.18.0/config.default.yml)
- [Native health checker](https://github.com/muety/wakapi/blob/2.18.0/scripts/healthcheck.go)
