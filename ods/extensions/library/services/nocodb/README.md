# NocoDB: local structured tables

NocoDB supplies a spreadsheet-like editor, typed tables and native authenticated
row APIs for local workflows. It owns a dedicated PostgreSQL database; it does
not discover or import ODS service databases. The image is pinned to 2026.09.0
and PostgreSQL to 17.11-alpine, including immutable image digests.

## Enable and onboard

1. Prepare `data/nocodb/files` for UID/GID 1000 and `data/nocodb/postgres` for
   UID/GID 70. On Linux, from `ods/`:

   ```bash
   sudo install -d -m 0700 -o 1000 -g 1000 data/nocodb/files
   sudo install -d -m 0700 -o 70 -g 70 data/nocodb/postgres
   ```

2. In Extensions, supply four distinct secrets: `NOCODB_ADMIN_PASSWORD`
   (at least eight characters, including an uppercase letter, digit and symbol),
   `NOCODB_DB_PASSWORD`, `NOCODB_JWT_SECRET`, and `NOCODB_CONNECTION_KEY`.
   Use long random values for the last three and retain them with private backups.
3. Enable NocoDB, open `http://localhost:8087`, and sign in as `admin@ods.local`.
   Immediately enable **Account settings → Signup settings → Invite-only signup**
   before giving other users or untrusted Docker peers access. Native signup is
   initially open; the recipe does not invent an unsupported environment switch
   to disable it. The setting persists in PostgreSQL. No SMTP server is configured.
4. Create a base and table. Generate a base-scoped API token for its workflow;
   send it as `xc-token` to the native `/api/v2/tables/<table-id>/records` API.
   Revoke unused tokens through NocoDB's token management.

`NOCODB_PORT` changes the loopback host port. `NOCODB_SITE_URL` is optional;
empty derives `http://localhost:<NOCODB_PORT>`, and an explicit URL wins. The
PostgreSQL service has no host port and lives on a private internal network.
An unprivileged NGINX 1.30.1 gateway joins `ods-network`, where trusted workflows
can reach `http://nocodb:8080`. NocoDB and PostgreSQL themselves join only the
private internal network. The gateway forwards HTTP/WebSocket traffic only to
NocoDB port 8080; it does not expose NocoDB's native data-reflection SQL proxy on
5433. Native health is `/api/v1/health`; health is not proof that
signup onboarding is complete.

## State and credential ownership

- **Managed administrator:** NocoDB applies `NC_ADMIN_PASSWORD` to the fixed
  `admin@ods.local` account on every startup. Change `NOCODB_ADMIN_PASSWORD` and
  recreate the application to rotate it. A password changed only in the UI can
  be replaced by the configured value on restart. Existing login sessions are
  invalidated by native password reconciliation; separately issued API tokens
  must be revoked separately. Do not change the fixed administrator email in
  Compose: native email reconciliation can transfer account/base ownership.
- **Database password:** PostgreSQL initializes it only on an empty data
  directory. For an existing database, rotate the native PostgreSQL role and
  the ODS setting together before recreating the application. Editing the
  environment alone does not rotate the stored PostgreSQL role.
- **JWT and connection secrets:** retain the same values across restarts and
  restores. The connection key encrypts native external-source connection
  configuration, not table data or the entire PostgreSQL volume. Changing it
  without a native migration can make existing connection definitions unreadable.
- A small Node preload module serializes the database connection as native
  `NC_DB_JSON`; password punctuation is preserved without URL or shell expansion.
  It then runs the image's `docker/index.js` under its native `dumb-init`.

## Privacy, limits and recovery

Native telemetry, error reporting and product feed are disabled. NocoDB runs
on an internal Docker network without an external route. This recipe is for
locally owned tables: outbound webhooks, external database connectors, remote
imports and SMTP require a separately reviewed networking change. Do not attach
the application directly to `ods-network` to enable them: its additional native
SQL proxy would then become reachable. The application runs as UID 1000 with a read-only root, a writable
files directory and bounded temporary storage; PostgreSQL runs as UID 70. The
application has a 2 GiB container / 1 GiB Node heap budget, PostgreSQL 512 MiB,
and the UID 101 gateway 128 MiB. The gateway accepts bodies up to 16 MiB.
These limits are a starting budget, not a measured capacity claim.

Stop all three services before copying **both** `data/nocodb/postgres` and
`data/nocodb/files`, plus the exact image pins and all four secrets, to a private
backup. Restore into separate empty directories with their original ownership,
then start the pinned version and verify login, signup restrictions, rows and
workflow token access. Disable the extension to stop it; keep its data for
recovery. Do not downgrade an upgraded database in place: restore a matching
pre-upgrade backup instead.

Linux amd64 qualification exercises real PostgreSQL-backed table creation,
Unicode/numeric row CRUD, token scoping and revocation, signup restriction,
administrator rotation, recreation and cold restore. Browser interaction,
attachments, external database connectors, SMTP, ARM/macOS/Windows runtime,
load, high availability and cross-version migration remain unqualified. Bind
mount ownership on Docker Desktop must be checked on the target host.

## Validation

```bash
python -m pytest ods/tests/test_nocodb.py -q
sudo --preserve-env=PATH env ODS_TEST_NOCODB=1 python -m pytest ods/tests/test_nocodb.py -q -s
```

The opt-in test uses isolated ephemeral loopback ports, its own PostgreSQL/files
directories and generated credentials. It creates no external notifications.

Native references: [environment variables](https://nocodb.com/docs/self-hosting/environment-variables),
[2026.09.0 administrator reconciliation](https://github.com/nocodb/nocodb/blob/2026.09.0/packages/nocodb/src/helpers/initAdminFromEnv.ts),
[native signup settings endpoint](https://github.com/nocodb/nocodb/blob/2026.09.0/packages/nocodb/src/controllers/org-users.controller.ts),
[database JSON configuration](https://github.com/nocodb/nocodb/blob/2026.09.0/packages/nocodb/src/utils/NcConfig.ts).
