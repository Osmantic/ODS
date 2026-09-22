# Homebox

Inventory for equipment and other physical items: locations, tags, purchase information, maintenance, warranties, photos and documents. This is the maintained `sysadminsmedia/homebox` project; the original archived repository is not a second extension.

## Initial setup

Set `HOMEBOX_API_KEY_PEPPER` to a random secret of at least 32 bytes. Upstream refuses to start without it. Keep it stable: rotating it invalidates issued API keys. This value is not a user password and does not automatically create an account.

Open `http://localhost:11089`, register the initial owner account, then set `HOMEBOX_ALLOW_REGISTRATION=false` and restart the extension to close ordinary self-registration. The initial default is `true`, explicitly to permit setup. Upstream group-invitation tokens can still authorize registrations. Do not expose the registration screen to an untrusted network. Host publishing is loopback-only, although other containers on `ods-network` can reach `homebox:7745`.

No demo account, sample inventory, API key, mail delivery, notification destination or device-discovery job is created. Analytics and OpenTelemetry export are disabled. Item information is supplied by the owner; installing this extension does not scan the host or change ODS hardware/model configuration.

## Persistence and projects

The named volume `homebox-data` retains the SQLite database and uploaded files under `/data`. Back up the stopped volume together with the API-key pepper. A database-only copy omits attachments. The application runs as UID/GID 1000 with a read-only base, temporary `/tmp`, 512 MiB RAM and one CPU; uploaded files are limited to 20 MiB each. Storage capacity still depends on the Docker host.

For a project that needs inventory data, create a deliberately scoped upstream API key and configure that project's client to use the documented Homebox API. This recipe does not grant Portal access, create a project association, import files or send inventory data automatically. The native UI remains the initial account/setup surface until chat configuration orchestration is completed.

## Provenance and verification

Official AGPL-3.0 Homebox 0.26.2 image, digest pinned for linux/amd64 and linux/arm64. Linux Docker and Linux-container runtimes on Windows/macOS use the same named-volume layout. The health endpoint `/api/v1/status` establishes server responsiveness, not correctness of inventory records or backups.

Image build, account creation/login, attachment upload, API-key authentication, persistence/restore and actual platform execution remain unverified. Packaging checks are not runtime validation; no containers or inference were started.

Sources: [release source](https://github.com/sysadminsmedia/homebox/tree/v0.26.2), [configuration](https://github.com/sysadminsmedia/homebox/blob/v0.26.2/docs/src/content/docs/en/quick-start/configure/index.mdx).
