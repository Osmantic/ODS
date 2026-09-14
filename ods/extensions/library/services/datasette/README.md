# Datasette

Explore SQLite dataset snapshots in a local browser: filter tables, run read-only
SQL, and export query results as JSON or CSV. This recipe uses the upstream stable
Datasette 0.65.4 image, pinned by digest. That image is **Linux x86-64 only**;
Apple Silicon and other ARM hosts are not supported by this recipe.

## Install and sign in

1. From the ODS installation directory, add a stable random `DATASETTE_SECRET`
   to `.env` before installing Datasette from **Extensions**. For example, generate
   a value with `openssl rand -hex 32`. Keep it private and retain it on upgrades.
2. Put complete SQLite snapshots ending in `.db`, `.sqlite` or `.sqlite3` in
   `data/datasette/`. Files must be readable by container UID 1000. Use SQLite's
   backup command/API to snapshot a running database; do not copy only the main
   file of a live WAL database. Do not mount an application's live database here.
3. Inspect `docker logs ods-datasette` for the one-use `/-/auth-token?token=...`
   login path. Open that path on `http://localhost:7827` (or your configured
   `DATASETTE_PORT`). The logged internal host/port is not the browser address.
   Treat the complete login URL like a password. A new URL is generated on restart.

Anonymous visitors cannot browse the instance, tables, SQL results or exports.
The public stylesheet endpoint is used for HTTP health checks. Publishing defaults to
loopback; remote access requires an operator-managed protected HTTPS path. The
single local root session is not a multi-user account system.

## Dataset lifecycle

The data mount and root filesystem are read-only. Every database is opened with
SQLite's immutable option: stop Datasette before replacing snapshots, then start
it again. Restart after adding or removing a database. An empty directory starts
a healthy empty browser. Directory names with spaces and Unicode are supported.
Only database files are loaded; adjacent plugins, metadata or settings are ignored.

The SQL timeout is one second, results are capped at 1,000 rows and streamed CSV
exports at 10 MB. Downloading the entire SQLite file is disabled. These limits
do not redact data: a signed-in operator can query all rows in each mounted file.
No upload, write-back or automatic connection to other ODS databases is provided.

Back up your snapshots and `.env`. To roll back, stop/remove the extension and
retain `data/datasette`; this recipe performs no database migrations. Rotating
`DATASETTE_SECRET` invalidates existing browser sessions.

## Verification

`ODS_TEST_DATASETTE_DOCKER=1 python -m pytest -q ods/tests/test_datasette_extension.py`
exercises the pinned image, anonymous denial, one-use login, filtering, SQL/CSV,
write rejection and unchanged snapshots across container replacement. Browser
interaction, ARM emulation and direct native Windows/macOS execution are not
covered by that Linux Docker test.

Upstream contracts: [0.65.4 authentication](https://github.com/simonw/datasette/blob/0.65.4/docs/authentication.rst),
[settings](https://github.com/simonw/datasette/blob/0.65.4/docs/settings.rst),
and [official image build](https://github.com/simonw/datasette/blob/0.65.4/.github/workflows/push_docker_tag.yml).
