# Dufs for ODS

An authenticated file server with browser uploads/downloads, search, folder
archives and WebDAV. Its dedicated file volume is not the Portal workspace.

Install `dufs`, configure `DUFS_PASSWORD`, and open `http://localhost:11069`.
Sign in as `ods`. The password must be at least 12 bytes and cannot contain
`:`, `@`, `|`, line breaks or a `$6$` prefix, which have special meaning in
upstream authentication rules. The launcher validates these boundaries before
creating a single read/write account; it does not allow injected anonymous rules.
Changing this setting changes the access password at the next restart.

`DUFS_HTTP_PORT` changes the loopback host port. WebDAV clients use the same URL
and credentials; clients requiring HTTPS need a separately configured secure
proxy. Other ODS containers can use `http://dufs:5000` after explicit connection.
No client, share, Portal project or operating-system drive mapping is created.

Files live in `dufs-data` at `/data`, owned by UID 65532. Upload, deletion, search
and archive download are enabled. Outside-root symbolic links remain disabled;
there are no host mounts or shell execution features configured. Back up the
volume when writes are stopped. Deleting through WebDAV/browser deletes actual
stored files; this service is not a versioned backup or recycle-bin system.

The official v0.46.0 scratch image is pinned by digest and supports amd64/ARM.
A static Go helper provides HTTP health checks and forwards shutdown signals.
Use Docker Engine on Linux or Docker Desktop Linux containers on Windows/macOS.
No GPU, model, external identity service or database is required. Upstream offers
MIT or Apache-2.0 licensing.

Native `/__dufs__/health` reports server readiness, not successful authenticated
storage operations. Build, login, upload/download/delete, WebDAV interoperability
and restore/platform runtime remain pending. No app containers were started.

Source: https://github.com/sigoden/dufs/tree/v0.46.0
