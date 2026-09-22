# Trilium

Keep a local hierarchy of rich research notes, relationships and attachments.
Trilium's ETAPI lets local workflows create and retrieve notes with a
revocable API token. This recipe pins upstream **v0.105.0** at an immutable digest
and requires no GPU.

## First run

Before installing, keep the ODS `BIND_ADDRESS` at `127.0.0.1`. Install from
Extensions, then open `http://localhost:7832`. Choose a new knowledge base and
set a strong, unique password. Keep the listener local until setup is
complete: a fresh, empty instance has no existing owner to authenticate. This is
Trilium's single-user knowledge base, not a multi-user collaboration server.

The default `TRILIUM_PORT=7832` host binding is loopback. Access from another
computer requires an operator-managed TLS proxy or encrypted tunnel; configure
any trusted proxy addresses narrowly. The recipe requires authentication and
disables backend JavaScript execution and the raw SQL console through upstream
configuration. Ordinary rich notes and ETAPI remain available. Frontend scripts,
explicit note sharing and external links remain application features; avoid
importing untrusted executable note bundles.

## Local automation

Create a token in Trilium's ETAPI settings and keep it in your workflow's secret
store. For example, a local client can send an authenticated request to:

```text
GET http://localhost:7832/etapi/notes/root
Authorization: <your ETAPI token>
```

ETAPI supports note content, hierarchy, attributes and attachments. Revoke tokens
when a workflow no longer needs access. A token grants access to the knowledge
base; it is not a per-folder permission boundary. Desktop synchronization needs
explicit configuration and compatible Trilium versions; this recipe does not
connect to a sync server automatically.

## Data and maintenance

ODS prepares `data/trilium` for UID/GID 1000. It stores the SQLite database,
configuration and application backups. The container runs Node directly as that
user with a read-only root; it does not invoke the image's root ownership wrapper.
Only this data directory and bounded temporary scratch are writable.

Before upgrading, stop the service and back up the entire directory. Read
upstream schema/sync compatibility notes before changing the pinned version.
Rollback may require restoring a matching stopped backup as well as the old
image. Disabling the extension keeps the directory. Do not synchronize the live
SQLite database with file-sync software or treat an image downgrade as a database
downgrade. The default resource limit is 2 CPU and 1 GiB; large attachment sets
and imports need separate capacity assessment.

The public `/api/health-check` endpoint reports process availability; it does not
prove that first-run password setup is complete. Native ARM/macOS/Windows,
browser rendering, desktop synchronization, large imports and existing database
upgrades require separate validation.

Upstream: [documentation](https://docs.triliumnotes.org/),
[pinned source](https://github.com/TriliumNext/Trilium/tree/v0.105.0).
