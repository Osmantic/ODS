# HedgeDoc for ODS

Collaborative Markdown editing with note permissions, revision history and image
attachments. This recipe uses the actual notes server and a dedicated PostgreSQL
database; it is not a static Markdown preview.

## Configure and create accounts

Set `HEDGEDOC_DATABASE_PASSWORD` and a persistent random
`HEDGEDOC_SESSION_SECRET` before enabling. The latter should contain at least
32 random characters. Database credentials use separate upstream fields, avoiding
password escaping problems in a database URL.

Open `http://localhost:11043`. Registration and anonymous editing are disabled by
default. Create your account with the upstream interactive tool:

```text
docker exec -it ods-hedgedoc bin/manage_users --add you@example.com
```

Use your intended account identifier and enter the password at the prompt, not
as a command-line argument. This creates a local account; no email is sent.
Alternatively, explicitly set `HEDGEDOC_ALLOW_REGISTER=true` for the local
registration form, then close registration again through ODS after provisioning.
There is no seeded account or default password.

New notes default to private. To collaborate, create the other local account and
choose the appropriate note permissions. Opening the UI does not automatically
publish existing notes or send invitations.

`HEDGEDOC_DOMAIN` defaults to `localhost:11043` and includes the **browser port**,
without a scheme. If you change `HEDGEDOC_PORT`, update this value too. Internal
HTTP stays on port 3000. A remote HTTPS deployment needs its own URL/protocol and
WebSocket proxy configuration; this recipe publishes loopback only.

## Storage and platform support

- Official AGPL-3.0 HedgeDoc **1.12.0** image pinned by digest for amd64/arm64;
  PostgreSQL 16 companion is separately pinned. Docker Linux containers provide
  the same named-volume setup on Windows, Linux and macOS. No GPU/model required.
- `hedgedoc-db-data` preserves accounts, notes and history. `hedgedoc-uploads`
  preserves attachments at `/hedgedoc/public/uploads`. Back up both together and
  retain the secrets. Disabling the extension must retain both volumes.
- A small image layer prepares upload ownership for UID/GID 10000. The upstream
  entrypoint then runs nonroot without requiring recursive root chown at startup.
  PostgreSQL keeps its own supported initialization/user switching behavior.
- The database has no published host port; the main service waits for its health
  check. Main memory is limited to 2 GiB and database memory to 1 GiB. Limits do
  not reserve that amount, and workload capacity remains host-dependent.
- `/_health` is checked by upstream's bundled Node health script, including its
  `ready` result. It does not verify successful collaborative editing or backup
  restoration. No host folders, Docker socket or Portal project is auto-mounted.

No mail service, OAuth provider, remote avatar service or AI provider is enabled
by this recipe. Export/import Markdown for explicit exchange with Portal projects;
automatic association remains separate work.

## Verification status

Image provenance, startup ownership, DB fields, URL composition and health source
were inspected. Schema, Compose resolution and staging are independent checks.
**Build and application runtime remain pending.** Local acceptance should create
two accounts, edit a deliberately shared note from separate sessions, upload an
attachment, restart, and confirm history and attachment persistence. No accounts,
messages, containers or model workloads were started during preparation.

- [Pinned server source](https://github.com/hedgedoc/hedgedoc/tree/1.12.0)
- [Official container source](https://github.com/hedgedoc/container)
- [Configuration reference](https://docs.hedgedoc.org/configuration/)
