# MicroBin for ODS

Text snippets, attachments, expiring links and per-item access options. This
integration runs MicroBin 2.1.0 with site-wide HTTP Basic authentication. It is
one catalog application; its access bridge and backend are companion containers.

## Setup and sharing

Set two secrets before enabling:

- `MICROBIN_ACCESS_PASSWORD`: site-wide login for username `ods`.
- `MICROBIN_ADMIN_PASSWORD`: administrator login for username `admin`.

Open `http://localhost:11051`, complete the browser's HTTP authentication prompt,
then create a snippet or upload a file. The admin page at `/admin` uses the
separate administrator password after site authentication. No upstream default
administrator password is retained. This is a shared site login, not a multiuser
identity system or an automatic Portal account integration.

People following a link also need the site credentials and network access.
Only loopback is published by default; remote sharing requires explicit HTTPS
and exposure configuration. Never embed passwords in shared URLs. The default
recipe does not set a remote public URL or automatically publish a paste from
a Portal conversation.

## Expiration and storage

`microbin-data:/data` stores the database and attachments. The derived backend
image prepares this directory for UID/GID 65532 before switching to nonroot.
The form defaults to 24-hour expiration; inspect the expiration you select for
each item. Upstream garbage collection also removes items after 90 days without
access, even when configured not to expire. Burn-after-reading and per-item
read-only/private controls are available. An unlisted private item is not the
same as encryption; knowing its link can still grant access within the site.

Limits are 64 MiB for unencrypted files and 32 MiB for encrypted files if those
features are explicitly enabled later. Client/server encryption switches are
not silently enabled by this recipe. The persistent volume is not encrypted by
this integration. Disabling must retain the volume; expiration still applies
after service resumes. Stop the backend for a consistent database/attachment
backup and preserve UID/GID 65532 on restore. Keep pre-upgrade backups.

## Authentication and ODS health

The upstream Basic middleware protects every application route. The ODS bridge
forwards normal requests without adding credentials, so unauthenticated readers
and uploads remain rejected by MicroBin. It exposes only `/_ods/health` without
authentication. That endpoint makes an authenticated request to the fixed
backend root, rejects redirects/non-200 responses and returns readiness only;
it never forwards page content or credentials to the caller. The backend has
no host port. Its authentication is still active on the Docker network.

The bridge is built as a static Go binary with a pinned builder. It and the
backend run without root and with no-new-privileges. The backend gets one CPU
and 1 GiB RAM; the bridge gets half a CPU and 128 MiB. Telemetry and update checks
are disabled. No model or GPU is required or reconfigured.

## Evidence and remaining validation

Official BSD-3-Clause image pinned by digest for Linux amd64/arm64; Windows and
macOS use their Docker Linux engine. Source was checked at the actual 2.1.0
release, whose image differs from the current development Dockerfile.

Bridge tests cover rejection of missing credentials, forwarding caller
authentication, health failure on wrong credentials, redirect/failure rejection
and exclusion of private page content from the health response. Schema, Compose
and ODS staging checks are separate from **runtime build, browser login,
upload/download, expiry and restore, which remain unverified**. No containers,
real uploads or model inference were started during preparation.

- [Versioned source](https://github.com/szabodanika/microbin/tree/v2.1.0)
- [Versioned authentication middleware](https://github.com/szabodanika/microbin/blob/v2.1.0/src/main.rs)
- [Configuration guide](https://microbin.eu/docs/installation-and-configuration/configuration/)
