# PocketBase: local application backend

Install **PocketBase (Local App Backend)** from Extensions, set the three
`POCKETBASE_*` settings, then enable it. Open `http://localhost:8090/_/` and
log in as `ods@localhost.invalid`. The API root is `http://localhost:8090/api`.
`POCKETBASE_PORT` changes the loopback port and permitted local browser origins.
Other ODS containers can explicitly use `http://pocketbase:8090` with native
application credentials. `BIND_ADDRESS` selects the published interface, defaulting to loopback.

PocketBase 0.40.4 is a pre-1.0 application backend. Operators must review native
migration/release notes before upgrading; this recipe is not a qualification
for a critical production deployment or an automatic schema-migration service.

## Runtime and account contract

The install hook builds `ods-pocketbase:0.40.4-r1` from pinned Alpine 3.24.1 and
the official PocketBase 0.40.4 release archive. The Dockerfile checks the exact
upstream SHA-256 for amd64 or arm64 and rejects other architectures. It does not
claim an official upstream Docker image. Installation needs access to Docker
Hub and the GitHub release asset; the normal runtime needs neither a registry
nor a cloud backend. Keep the built image or repeat the setup hook before
re-enabling on a new host. The Compose definition is image-only with
`pull_policy: never`, so normal activation does not substitute a registry image.

Generate a unique bootstrap password with `openssl rand -hex 24`, and a separate
settings encryption key with `openssl rand -hex 16`. Passwords require 16–72
ASCII letters, digits, `_` or `-`; the encryption key requires exactly 32 such
characters. The entrypoint rejects invalid values before database access.

A native bootstrap hook creates the superuser in a transaction only on a fresh,
empty data directory. Existing accounts are preserved; changing the seed in
`.env` does not reset them. The entrypoint passes the bootstrap password through a private temporary file
and removes it from the server environment; the native hook deletes the file
after initialization. No plaintext password is
passed in normal startup arguments, but Docker/ODS administrators can read its
source environment. Keep at least one superuser. An existing database without
one fails before HTTP startup, requiring explicit native account recovery.

Use the native administrator account editor to rotate passwords, then verify a
fresh login. Do not use the superuser token in a client application: create an
auth collection and restrictive collection rules for application accounts.
Native superusers bypass collection rules and remain trusted administrators.
The recipe does not create user collections or change rules behind the operator.

PocketBase file fields are public by default. For private attachments, select
**Protected** and define a restrictive collection view rule. Use a short-lived
native file token for downloads; ordinary record authentication is not a
replacement for this token. The regression fixture creates two application
accounts and verifies owner-only reads/writes and protected-file downloads.

## Storage, settings and backups

ODS prepares `data/pocketbase` for UID/GID 1000. This bind contains `data.db`,
auxiliary data, files and generated schema migrations. The rest of the container
is read-only, with a 128-MiB temporary directory, dropped capabilities, two CPUs,
1 GiB memory and Docker's init process. Native `/api/health` is public and is
used by both Docker and ODS; it does not prove collection or file integrity.

`POCKETBASE_ENCRYPTION_KEY` encrypts the native **settings record only**. It does
not encrypt collection records, uploaded files, all SQLite pages or backups.
Keep the key separately with the complete cold backup; do not change it as a
routine password rotation. A different key cannot decrypt the saved settings.
Initial metadata identifies ODS and disables IP logging; subsequent native
settings are preserved. Email, OAuth, remote storage and external automation
are not provisioned. Administrators can configure native integrations later.

Disable before copying the entire `data/pocketbase` directory with ownership
and permissions. Restore the complete directory and its original encryption
key using the same pinned runtime, then verify fresh login, collection rules,
records and protected-file bytes. Disable/remove retains data. Keep a compatible
cold backup before upgrading; image rollback alone cannot undo migrations.
If `data.db` is missing while the directory still contains other state, startup
refuses to create a replacement database over that partial restore.

If all superusers are lost, first preserve the complete cold backup. With the
extension disabled, the native `superuser` CLI can recover an account using
`--dir=/pb/pb_data --hooksDir=/tmp/recovery-hooks
--encryptionEnv=POCKETBASE_ENCRYPTION_KEY` and the same data/key. Bypassing the
recipe hook is deliberate for this offline recovery command. Native
`superuser upsert <email> <password>` takes the password in process arguments;
use a trusted local maintenance session and avoid shell history. It also resets
an existing account, so it is not the routine password-rotation path. Re-enable
and verify native login before considering recovery complete.

## Qualification

```bash
python -m pytest tests/test_pocketbase.py -q
ODS_TEST_POCKETBASE=1 python -m pytest tests/test_pocketbase.py -q
cd extensions/services/dashboard-api
pytest tests/test_pocketbase_install.py tests/test_extensions.py tests/test_host_agent.py -q
```

The opt-in test builds through the install hook and exercises the pinned native
HTTP API on isolated owned storage. It covers superuser authentication, CORS,
auth collections, owner rules, Unicode records, protected binary files, native
password rotation, seed preservation and a complete cold restore. Static
contracts cover image-only activation, build-error propagation, port overrides
and missing/invalid credentials. The API test exercises authenticated ODS
installation and re-enabling with the copied build inputs.

Runtime evidence is Linux/amd64 Docker Desktop on WSL Ubuntu. The arm64 archive
checksum is recorded but arm64 execution, browser rendering, realtime clients,
concurrent migrations, upgrades, live email/OAuth and native macOS/Linux hosts
remain unqualified. Read the PR for actual results and any negative evidence.

Native references: [0.40.4 release](https://github.com/pocketbase/pocketbase/releases/tag/v0.40.4),
[bootstrap events](https://github.com/pocketbase/pocketbase/blob/v0.40.4/core/base.go),
[settings encryption](https://github.com/pocketbase/pocketbase/blob/v0.40.4/core/settings_query.go),
[protected files](https://github.com/pocketbase/pocketbase/blob/v0.40.4/apis/file.go).

## Host publication

The published ports honor ODS `BIND_ADDRESS`: unset keeps `127.0.0.1`; the
explicit LAN opt-in can select `0.0.0.0` or a specific host interface. This
controls Docker publication and preserves native authentication and application
policy. Disable/recreate after changes, and keep operator edits to the installed
definition backed up before updating or reinstalling the extension.

Native browser-origin policy remains explicit. Cross-origin application
clients may require adding their origin to `--origins` in the installed
`start-pocketbase.sh`, rebuilding through `setup.sh`, and recreating. This binding
change does not relax the native origin list or add TLS.

Compose regression tests cover unset, loopback, wildcard and a specific interface
while preserving target ports, credentials and storage. Actual lifecycle fixtures
remain bound to isolated loopback ports. Remote browser/TLS deployments are not
qualified by those local tests.
