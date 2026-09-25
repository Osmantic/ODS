# SilverBullet for ODS

SilverBullet stores knowledge spaces as editable Markdown files with queries,
links and scripting. Unlike a database-backed notebook, its space content remains
available as files. Version 2.11.0 supports multiple spaces and accounts in one
server; this recipe uses that current setup flow instead of legacy `SB_USER`
single-space authentication.

## Start and create the workspace

1. Enable the extension and open `http://localhost:11040`.
2. Complete the built-in setup wizard to create the first administrator account.
3. Create a space inside `/data`, using the browser hostname/port and path shown
   in the wizard. Keep anonymous access at `none` for private notes.
4. Create a Markdown page, then verify that it is retained after restarting.

The wizard is available before the first administrator exists: complete it before
sharing access. The published port is loopback-only. Additional accounts and
per-space access are managed in SilverBullet, independently of ODS accounts.
Readiness does not mean administrator provisioning has been completed.

## Persistence and host compatibility

- MIT upstream, official full **2.11.0** image pinned by digest, with amd64,
  arm64 and ARM image manifests. Linux runs directly through Docker; Windows and
  macOS use Docker's Linux engine. No GPU or LLM is required or configured.
- `silverbullet-data:/data` contains server configuration, accounts, spaces and
  their content. `silverbullet-home:/home/silverbullet` retains user-level state
  for optional Git/SSH setup. Back up both volumes; disabling must retain them.
- A small build layer supplies a real UID/GID 1000 account and owned directories.
  The official entrypoint runs as that account under no-new-privileges. Named
  volumes avoid OS-specific host paths and ownership assumptions.
- The full upstream image includes Chromium for SilverBullet's runtime APIs,
  rather than the slim image that returns 503 for them. This recipe keeps the
  upstream browser launcher behavior. Browser/runtime execution is not yet
  validated on the host; resource limits are two CPUs and 2 GiB RAM.
- The native `/.instance` check tests the listener. It does not verify login,
  permissions, Markdown editing, browser runtime or Git synchronization.

Space `write` permission includes powerful runtime/shell features inside this
container. Assign it intentionally. The official `CONTAINER_BOOT.md` mechanism
is retained and executes as UID 1000 here; package installation requiring root
must be done in a reviewed image layer, not assumed to work from a note.
No host shell, host home directory, Docker socket, remote Git account or model
credentials are shared automatically.

SilverBullet's `/data` is **not the Portal Playground**. To exchange files now,
export/import the specific Markdown files you want. Automatic project association
belongs to the unfinished Portal extension lifecycle work and is not implied by
this recipe. Choose an actual configured Git remote only if you want remote sync.

## Verification status and references

The image manifest/configuration and versioned upstream setup, entrypoint,
runtime-browser and authentication sources were reviewed. Schema, Compose and ODS
staging are checked independently. **Image build and application runtime remain
pending**; no administrator, note, browser session or synchronization job was
created during preparation.

- [Versioned source](https://github.com/silverbulletmd/silverbullet/tree/2.11.0)
- [Docker installation](https://silverbullet.md/Install/Docker)
- [Account and space setup](https://github.com/silverbulletmd/silverbullet/blob/2.11.0/docs/Dashboard.md)
- [Authentication](https://github.com/silverbulletmd/silverbullet/blob/2.11.0/docs/Authentication.md)
