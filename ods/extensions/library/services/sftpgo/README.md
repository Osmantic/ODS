# SFTPGo

Use a dedicated local transfer workspace for datasets, documents and generated
artifacts. SFTPGo provides persistent per-user storage, SFTP clients and a browser
file manager. This recipe pins the upstream community **v2.7.5** image by digest.

## Install and create a transfer account

1. Set a strong `SFTPGO_ADMIN_PASSWORD` in the installation's `.env` before
   installing **SFTPGo** from Extensions. `SFTPGO_ADMIN_USER` defaults to `admin`.
2. Open `http://localhost:7828/web/admin` and sign in. Create a separate enabled
   file-transfer user with a password and/or SSH public key, a local filesystem
   home under `/srv/sftpgo/data/<username>`, and only the permissions needed.
   Administrator accounts manage users; they are not SFTP file-transfer accounts.
3. Use `sftp -P 2022 username@localhost`, or open
   `http://localhost:7828/web/client` with the transfer user's credentials.
   Adjust these URLs/ports if `SFTPGO_PORT` or `SFTPGO_SFTP_PORT` is overridden.

Both published ports bind to loopback by default. A remote operator must configure
an explicit protected route: use a trusted VPN/SSH tunnel for SFTP and protected
HTTPS for browser access. An HTTP reverse proxy does not proxy the SSH protocol.
Verify the SSH host-key fingerprint before accepting it on another computer.
FTP and WebDAV listeners are disabled; public file shares are not created by this
recipe. Do not enable anonymous access or grant a user access to ODS configuration.

## Storage, accounts and recovery

`data/sftpgo/state` contains the SQLite account/configuration database and generated
SSH host keys. `data/sftpgo/files` contains user files and the configured backup
directory. The existing extension installer prepares both mounts for UID/GID 1000.
For a manual Compose installation, create those directories with that ownership
before starting the service. No Docker socket or other ODS data directory is mounted.

The initial administrator environment values are used only when the database has
no administrator. Changing `.env` does not reset an existing password; change it
in WebAdmin. Back up both directories together while the service is stopped,
along with the installed recipe and `.env`. Retaining only user files loses account
permissions and server identity. Container replacement retains all three.

Keep the previous image and a pre-upgrade stopped backup. A downgrade can require
restoring that matching database snapshot; do not start an older binary against a
newer database without checking upstream migration guidance. Disabling/removing
the extension leaves its host data for an explicit later cleanup.

## Verification

`ODS_TEST_SFTPGO_DOCKER=1 python -m pytest -q ods/tests/test_sftpgo_extension.py`
tests the real pinned image: initial admin authentication, two distinct users,
SFTP upload/download and account isolation, persisted files/accounts/SSH identity
after recreation. Browser interaction, remote TLS/VPN routing, cloud storage,
public shares and native Windows/macOS/ARM execution need separate validation.

Upstream contracts: [v2.7.5 image](https://github.com/drakkan/sftpgo/blob/v2.7.5/Dockerfile),
[configuration](https://github.com/drakkan/sftpgo/blob/v2.7.5/sftpgo.json),
and [HTTP API](https://github.com/drakkan/sftpgo/blob/v2.7.5/openapi/openapi.yaml).
