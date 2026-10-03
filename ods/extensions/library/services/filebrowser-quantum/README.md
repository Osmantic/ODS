# FileBrowser Quantum for ODS

Browse, upload, organize and preview files in a local repository, with user
permissions and explicitly created shares. This integrates the actively maintained
Quantum project, not a second catalog entry for the original FileBrowser.

## Installation and accounts

- Official **1.5.6-stable** image, Apache-2.0, pinned by digest. The full image
  includes FFmpeg and document preview support for amd64/arm64. It requires Docker
  Linux containers on Windows/macOS or Docker on Linux; no GPU/model is involved.
- Set `FILEBROWSER_QUANTUM_ADMIN_PASSWORD` and
  `FILEBROWSER_QUANTUM_JWT_SECRET` before enabling. Use a strong initial password
  and a persistent random signing secret (at least 32 characters recommended).
- Open `http://localhost:11041` and sign in as `admin` with the initial password.
  Password authentication is enabled, anonymous mode and self-registration are
  disabled. Change an existing account password in the application's controls;
  do not assume changing the initial-password environment resets existing users.
- Additional users default to downloading without modification, upload, delete,
  sharing, API or admin permissions. Assign the actual account permissions you
  want in the UI. The bootstrap administrator is distinct from those defaults.

## Files, configuration and recovery

`filebrowser-quantum-files` is mounted as `/srv`, the only configured file source.
Upload files through the UI or explicitly copy selected files into this volume.
The database, settings and preview/index cache are in `filebrowser-quantum-data`
at `/home/filebrowser/data`, outside the browsable source. Back up both volumes
and preserve UID/GID 1000 on restore. Keep the signing secret separately; rotation
invalidates login tokens. Disabling the extension must retain the volumes.

A small derived image supplies owned directories and the initial `config.yaml`.
The real upstream process runs as UID/GID 1000 with no-new-privileges, on internal
port 8080, so it needs no privileged-port capability. Limits are two CPUs and
2 GiB RAM. First-use volume population copies the configuration; subsequent
image upgrades do not overwrite the owner's persisted configuration.

To edit that configuration independently of the host OS:

```text
docker cp ods-filebrowser-quantum:/home/filebrowser/data/config.yaml ./filebrowser-config.yaml
```

After editing, copy it back and restart through ODS. Keep the internal port and
`/health` probe aligned. Do not add `/`, the Docker socket, or the application's
credential/database directory as browsable file sources.

The recipe does not mount Windows/Linux/macOS home directories or Portal's
Playground. Automatic Portal project association remains separate work. Office
document previews do not imply a running OnlyOffice editing server; that would
require its own actual integration. No public shares are created on installation.

The stable release uses its legacy database format. Do not replace the pinned
image with `beta`/v2 without the upstream migration procedure and backups.

## Verification status

Upstream image/configuration, password environment handling and storage layout
were reviewed. ODS schema, Compose resolution, staging and uniqueness checks are
separate from runtime. **Image build, login, upload, previews and persistence are
not yet runtime-validated.** `/health` reports service readiness, not successful
account setup or access to uploaded files. No app container was started here.

- [Pinned source](https://github.com/gtsteffaniak/filebrowser/tree/v1.5.6-stable)
- [Stable Docker instructions](https://filebrowserquantum.com/en/docs/getting-started/docker-v1.5.x/)
- [Stable configuration](https://filebrowserquantum.com/en/docs/getting-started/config-v1.5.x/)
