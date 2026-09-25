# SFTPGo

Native SFTP and browser file transfer for project clients, with independent user
directories, permissions and quotas. AGPL-3.0-only, pinned official 2.7.6 image.

## Setup and use

Set `SFTPGO_SETUP_CODE` to a random 64-character hexadecimal secret before installing.
Open `http://localhost:11113/web/admin`, supply that installation code and create
the first administrator in SFTPGo's native wizard. Create a separate transfer user
with its own credentials or SSH public key and only the permissions it needs.
The installation code is not an account password and does not reset existing users.

Connect an SFTP client to `127.0.0.1:11114`, or use `/web/client` on the web port.
Verify the server's SSH host-key fingerprint when connecting for the first time.
FTP and WebDAV listeners are disabled; no unencrypted FTP endpoint is published.
Set user storage/transfer quotas in the application as appropriate; the memory limit
does not impose a disk quota.

## Storage and integration

`sftpgo-files` persists `/srv/sftpgo`, including user files and application backups.
`sftpgo-state` persists `/var/lib/sftpgo`, including SQLite and SSH host keys.
Back up both volumes with the service stopped; preserve host keys on restore so
clients can recognize the same server. Do not delete the state volume to upgrade.
The native nonroot UID/GID is 1000. Root filesystem is read-only; temporary files
use a bounded `/tmp`. Graceful shutdown allows transfers 32 seconds to finish.

This recipe does not expose the host filesystem, Docker socket or another project's
workspace. A Portal project is not automatically synchronized with the transfer
volume. Project integration must explicitly select its directory and permissions.
No S3 backend, external identity provider, event hook or transfer user is preseeded.

Both host ports bind to loopback. Other containers on `ods-network` can reach the
service; the installation code protects first-admin creation there too. Remote use
requires deliberate network/TLS configuration; the local web UI uses HTTP.

## Compatibility and verification

The pinned image offers Linux amd64/arm64, usable through supported Docker runtimes
on Linux, Windows and macOS. No GPU or model is required. Native `sftpgo ping` checks
HTTP health; this does not verify account setup, permissions or successful transfers.
Schema/Compose/source checks are separate from runtime validation: image build,
first-admin setup, SFTP upload/download and restoration remain untested.

Sources: https://github.com/drakkan/sftpgo/tree/v2.7.6 and
https://docs.sftpgo.com/latest/docker/.
