# Shiori for ODS

Bookmarks with tags, readable page extraction and optional page archives.
The web application and CLI use the same persistent local library. No existing
browser bookmarks or Portal research results are imported automatically.

## First account

Provide `SHIORI_INITIAL_PASSWORD` and `SHIORI_HTTP_SECRET_KEY` before enabling.
The initial password must be 12–72 UTF-8 bytes (bcrypt's limit is measured in
bytes). Use a separate random session secret of at least 32 characters and keep
it stable across restarts. Open `http://localhost:11055` and log in as `shiori`
with the initial password you supplied, then manage accounts in Shiori.

The upstream release creates a known default password when no accounts exist.
This recipe rebuilds the exact source revision with a narrow bootstrap change:
the first account instead requires your configured password before HTTP startup.
The patch asserts the expected original code before changing it. Passwords are
not embedded in the image, substituted into build arguments or printed in errors.

Existing accounts are never overwritten by this bootstrap. Changing the initial
password environment variable does not reset an existing account; use account
settings for password changes. Importing an old database also imports its existing
credentials, so review them yourself before granting access.

## Library and storage

Add a URL in the web UI, choose whether to archive its content and organize it
with tags. Extraction depends on the actual page; authenticated pages, blocked
sites and JavaScript-heavy content may not archive faithfully. Archive retrieval
contacts the URL you selected; it is not model-generated research or a guarantee
of a complete offline copy.

`shiori-data:/shiori` keeps SQLite, thumbnails and archives together. Disabling
the extension must preserve it. Stop the server for a consistent volume backup
and retain ownership UID/GID 1000 on restore. Keep the session secret separately
and preserve pre-upgrade backups for database migrations.

To import a standard browser bookmark export deliberately, copy your exported
file and invoke the actual CLI:

```text
docker cp "./bookmarks.html" ods-shiori:/shiori/bookmarks.html
docker exec ods-shiori /usr/bin/shiori import /shiori/bookmarks.html
```

Review the imported entries in the UI. These commands are examples for your
chosen file and were not run. The integration does not mount the browser profile,
host home directory or another extension's data.

## Runtime and evidence

MIT v1.8.0, source commit `da56f82faac5bf350fc4175e5ff50726cb10d956`.
The source archive checksum, Go builder and official Alpine runtime image are
fixed. Source includes the bundled UI assets. The rebuilt binary preserves the
upstream CLI/server behavior except the first-password bootstrap. The runtime
runs as UID/GID 1000, with Docker init, one CPU and 1 GiB RAM.

The official runtime manifest supports Linux amd64, arm64 and ARM; the custom
binary is compiled for the Docker target architecture. Windows/macOS require
Linux containers. No model, GPU or context setting is changed. Only loopback
11055 is published, and trusted-header SSO is explicitly disabled.

The HTTP health probe checks the login page, not authenticated library access.
The password helper's byte bounds and error redaction are unit-tested, and the
patch was applied to the checked-out exact source for inspection. **The complete
image build, account creation, login, archive extraction, import and restart/
restore remain runtime-unverified.** Configuration checks are separate from
those outcomes. No application container or inference was started.

- [Versioned source](https://github.com/go-shiori/shiori/tree/v1.8.0)
- [Upstream bootstrap](https://github.com/go-shiori/shiori/blob/v1.8.0/internal/cmd/root.go)
- [Usage](https://github.com/go-shiori/shiori/blob/v1.8.0/docs/Usage.md)
