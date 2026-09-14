# Kopia encrypted snapshots

Optional Kopia 0.23.1 UI for deliberately selected local exports. The pinned
image runs on the CPU. Nothing is backed up until an operator creates a
repository and selects a source. The extension has no Docker socket, host-root
mount, live ODS data mount, FUSE device or privileged capability.

## First use

1. Set two distinct strong secrets in the Extensions configuration:
   `KOPIA_PASSWORD` for repository encryption and `KOPIA_SERVER_PASSWORD` for
   the `ods` UI administrator. Keep the encryption password in an offline
   recovery location. Losing it means losing access to the snapshots.
2. Enable Kopia and open its service link (default `http://localhost:51515`).
   Sign in as `ods`. Create a **Local Directory or NAS** repository at
   `/repository`, using the exact `KOPIA_PASSWORD` value for its encryption
   password. For an existing repository, connect instead of creating another.
3. Place selected exports in `ods/data/kopia/source/`; select `/source` in
   Kopia and create a snapshot. Verify it, then restore a sample into `/restore`
   and compare its contents before relying on the backup. The host receives
   restored files under `ods/data/kopia/restore/`.
4. Configure a snapshot schedule and retention policy deliberately in Kopia.
   There is no default ODS-wide backup or retention promise.

`/source` is read-only to the container. Put database exports or a consistent
copy from stopped applications there; copying active database files is not a
consistent database backup. The default repository is on the same host and
does not protect against disk/host loss. Copy a quiesced repository to another
device, or explicitly configure a supported independent backend in Kopia.
This profile does not provide cloud credentials or an off-site destination.

## Persistence and recovery

All writable persistent paths are inside `ods/data/kopia/`: `config`, `cache`,
`logs`, `repository` and `restore`; source exports live in `source`. Configuration
retains the repository connection, while the repository contains the encrypted
snapshots. The stable container hostname preserves source identity on recreation.

Changing `KOPIA_SERVER_PASSWORD` and recreating the container changes UI login
credentials. Changing `KOPIA_PASSWORD` alone does **not** re-key a repository:
it prevents the server from opening that repository. Use Kopia's repository
password workflow and update the deployment secret together. Keep the original
key until an independent restore has succeeded.

Stop Kopia before copying the complete repository and configuration for an
upgrade. Preserve the encryption password separately. Roll back using the
previous image and a compatible pre-upgrade repository/configuration copy;
do not assume older releases can read a migrated repository. Uninstalling the
extension must not be treated as a backup cleanup procedure.

## Access and operating limits

HTTP binds to loopback by default. `--insecure` permits HTTP inside this local
profile; authentication and CSRF checks remain enabled. Use HTTPS with correctly
configured proxy/origin handling before deliberately exposing it remotely.
The UI account controls all mounted data and is not a tenant boundary. Remote
gRPC clients and the separate control API are disabled in this profile.

The public `/metrics` endpoint is used for process health and can expose
operational metadata; it does not prove a repository is connected, snapshots
are fresh, or restoration works. Monitor the authenticated repository status
and perform restore drills. Resource limits are 2 CPUs and 2 GiB RAM; source
size, repository, cache, logs and retention still require disk planning.

## Validation

`pytest ods/tests/test_kopia.py -q` renders the installable profile and checks
credential, persistence, catalog and source-mount contracts. With Docker running,
`ODS_TEST_KOPIA=1 pytest ods/tests/test_kopia.py -q -s` additionally exercises
authenticated UI/API repository creation with CSRF, a real snapshot and restore,
and retained data after container recreation using synthetic files and isolated
volumes. It does not access existing ODS files.

Sources: [Docker installation](https://kopia.io/docs/installation/#docker-images),
[repositories](https://kopia.io/docs/repositories/),
[0.23.1 server](https://github.com/kopia/kopia/blob/v0.23.1/cli/command_server_start.go).
