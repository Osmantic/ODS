# File Browser: local file workspace

A CPU-only file manager for the dedicated `data/filebrowser/files` directory.
Install **File Browser (Local Files)** from Extensions, set a unique
`FILEBROWSER_PASSWORD` in ODS Settings, then enable it. Open
`http://localhost:8095` and sign in as `ods`. `FILEBROWSER_PORT` changes the published host port. It defaults to loopback
and honors `BIND_ADDRESS` for explicit LAN publication.

The pinned image is File Browser v2.63.23, including its native web UI, JSON
login, scoped users and file API. Generate the bootstrap password with
`openssl rand -hex 24`; the recipe accepts 16–72 ASCII letters, digits, `_` or
`-`. The 72-byte upper limit comes from bcrypt. Missing/invalid credentials
stop activation without printing their value. Startup hashes the password
with the native `filebrowser hash` command and passes the hash to the original
image initializer. The plaintext briefly appears in that hash process's
arguments; administrators who can inspect Docker or the ODS environment can
already read the bootstrap secret. Keep the ODS environment private.

## Files, users and permissions

Upload/download files through the UI, or use `/api/login` and send the returned
JWT in `X-Auth` for the native API. Files stay under `/srv`; ODS configuration,
models and home directories are not mounted. The first `ods` account is an
administrator. Use Settings → User Management to create other accounts with a
relative scope such as `shared` and only the permissions they need. Read-only
users need download permission and should have create, modify, rename, delete,
share, execute and administrator permissions disabled.

The recipe disables command execution and external symlink following at the
server boundary. It runs as UID/GID 1000 with a read-only container filesystem,
64 MiB temporary storage, dropped capabilities, and a one-CPU/512-MiB limit.
Only the designated file, database and configuration directories are writable.
An administrator can still change users, permissions, settings and files;
this is a trusted local service, not a sandbox for hostile administrators.
Public sharing is a native operator action and can intentionally grant access
to selected files without login. Do not create public shares for private files.

## Passwords and existing sessions

`FILEBROWSER_PASSWORD` seeds a **new database only**. Changing it in ODS and
recreating a populated service does not replace an existing account password.
Use File Browser's native account settings to change that password with the
current password, then verify a fresh login and update the stored bootstrap
value for a future clean installation.

The session lifetime is set to 30 minutes, but native password changes do
**not** revoke existing JWTs. A token can also renew its session, so the timeout
alone is not a revocation guarantee. To retire a compromised account, create
and verify a separate administrator first when needed, then delete the old
account using native user management. Deletion blocks both file access and
renewal for its old token. Exact v2.63.23 reports HTTP 500 for that deleted-user
token; a fresh login with the deleted credentials returns 403. Do not describe
a password change as immediately signing out every device.

## Persistence, backup and removal

Keep all three directories together:

- `data/filebrowser/files`: uploaded files and folders.
- `data/filebrowser/database`: account hashes, permissions, signing key and app state.
- `data/filebrowser/config`: the native image's persistent JSON configuration.

ODS prepares all three for UID/GID 1000 before activation. Stop File Browser,
copy all three directories to a separate backup destination, preserve ownership
and permissions, then restart. For recovery, stop the service, restore the three
directories from one cold backup, and start the same pinned image. Verify the
administrator login, scoped-user permissions and file bytes. The database is
sensitive; backing it up also preserves existing sessions and account state.
Disabling/removing the extension retains these data directories. Back them up
before an upgrade; an image downgrade alone is not a database rollback plan.

## Qualification

```bash
python -m pytest tests/test_filebrowser.py -q
# Isolated Docker fixture with prepared UID 1000 host directories:
ODS_TEST_FILEBROWSER=1 python -m pytest tests/test_filebrowser.py -q
cd extensions/services/dashboard-api
pytest tests/test_filebrowser_install.py tests/test_extensions.py tests/test_host_agent.py -q
```

The opt-in test runs the exact image and real HTTP/WebSocket handlers: login,
Unicode and binary upload/download, duplicate-upload conflict preservation,
scoped read-only access, disabled shell, recreation, native password rotation,
continued JWT renewal, cold restore, account retirement and file deletion. The
API install test exercises the authenticated install boundary and host storage
preparation. Static recipe checks cover port overrides and credential rejection.
Interactive browser rendering, large/chunked transfers, sharing, external
symlink attacks, native Linux/macOS hosts and upgrades from older databases
remain unqualified by this fixture.

Native contracts: [v2.63.23 root settings](https://github.com/filebrowser/filebrowser/blob/v2.63.23/cmd/root.go),
[account mutation](https://github.com/filebrowser/filebrowser/blob/v2.63.23/http/users.go),
[JWT authentication and renewal](https://github.com/filebrowser/filebrowser/blob/v2.63.23/http/auth.go).

## Host publication

The published ports honor ODS `BIND_ADDRESS`: unset keeps `127.0.0.1`; the
explicit LAN opt-in can select `0.0.0.0` or a specific host interface. This
controls Docker publication and preserves native authentication and application
policy. Disable/recreate after changes, and keep operator edits to the installed
definition backed up before updating or reinstalling the extension.

Native authentication, workspace permissions, shell restrictions and session
behavior remain in force. Configure TLS/access controls for remote credentials.

Compose regression tests cover unset, loopback, wildcard and a specific interface
while preserving target ports, credentials and storage. Actual lifecycle fixtures
remain bound to isolated loopback ports. Remote browser/TLS deployments are not
qualified by those local tests.
