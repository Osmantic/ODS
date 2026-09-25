# Actual Budget for ODS

Local budgeting with transaction-file import, reports and a self-hosted sync
server. This integration runs the real web client and sync server; it does not
provide financial advice or connect to a bank during installation.

## Initial setup

Open `http://localhost:11046` and create the server password in Actual's first-run
screen. Complete this before sharing access. Password login is explicitly enabled
and is the only allowed login method; no proxy header or external identity
provider is configured. Create a budget or import a file using Actual's own UI.
There is no seeded password, bank credential or example transaction dataset.

The browser requires a secure context for its storage features. Localhost works
for this local recipe. Access from another computer requires a deliberate HTTPS
deployment and a separate exposure decision, not just changing the hostname.

## Storage and recovery

`actual-budget-data:/data` preserves the server configuration and both
`server-files` and `user-files`. The former includes account/session metadata;
the latter contains synchronized budget files. The client also keeps local data
in its browser storage. Verify a budget has actually synchronized before treating
the server copy as current. Back up the volume and export budget files through
Actual; clearing a browser profile is not a backup procedure.

Retain UID/GID 1001 when restoring the volume. Disabling the extension must retain
it. Budget schema upgrades may not be reversible by simply downgrading the image,
so keep a pre-upgrade backup. Server password recovery is available through the
upstream interactive tool:

```text
docker exec -it ods-actual-budget node /app/scripts/reset-password.js
```

Do not confuse that server password with an optional budget encryption password.
Encryption is configured in Actual and is not silently enabled by this recipe.
Bank synchronization providers, if desired later, require their own explicit
configuration and credentials; no bank or third-party service is authorized here.

## Runtime and verification

Official MIT **26.9.0** image pinned by digest for amd64/arm64. It runs as the
upstream-created UID/GID 1001 with no-new-privileges and its existing tini process
supervisor. The upstream image already prepares `/data` with correct ownership,
so no custom image or root initialization is needed. Windows/macOS use Docker's
Linux engine; Linux uses the same deployment. No model, GPU or context adjustment.

The published port binds loopback only; limits are one CPU and 1 GiB RAM for the
server. The bundled Node health checker reads `/health` and requires status `UP`.
It does not prove login, successful budget synchronization or data recovery.
There is no automatic association with Portal projects or host directories.

Image provenance, server configuration and storage ownership were inspected.
Schema, Compose resolution and ODS staging checks are separate from application
use. **Login, import, synchronization and restart/restore runtime remain pending.**
No budgets, bank connections, transactions or account operations were created
during preparation.

- [Versioned source](https://github.com/actualbudget/actual/tree/v26.9.0)
- [Docker setup](https://actualbudget.org/docs/install/docker/)
- [Server configuration](https://actualbudget.org/docs/config/)
- [Password recovery](https://actualbudget.org/docs/troubleshooting/reset_password/)
