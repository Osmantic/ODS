# Linkding research bookmarks

Linkding stores bookmarks, tags and notes in your ODS installation. Use its web
interface or token API to keep sources from a research workflow. This is a
bookmark library; it does not automatically ingest pages into ODS RAG.

## Install and create an account

Copy this directory to `extensions/services/linkding` in the active installation
or install Linkding from the dashboard Extensions library, then run:

```bash
ods enable linkding
docker exec -it ods-linkding python manage.py createsuperuser --username owner --email ''
```

The interactive command asks for a password. It is not stored in compose or
`.env`. Open `http://localhost:8100` and sign in. The initial database has no
default account and no anonymous signup. `LINKDING_PORT` changes the published
port; the internal API stays at `http://linkding:9090`. A local port collision
can be resolved by setting the variable before enabling the service.

The service binds to loopback by default. Use the installation's protected
access path for remote use. With a custom reverse proxy, follow upstream's
CSRF-origin configuration; do not disable authentication or CSRF checks.

## Workflow API

Create an API token under **Settings → Integrations**. Store it in your workflow
tool's credential store and send `Authorization: Token <token>` to Linkding.
For example, an n8n HTTP Request node on the ODS network can POST to
`http://linkding:9090/api/bookmarks/?disable_scraping` with:

```json
{
  "url": "https://example.com/research",
  "title": "Reviewed source",
  "notes": "Evidence for the current report",
  "tag_names": ["research"],
  "shared": false
}
```

`disable_scraping` prevents that API write from fetching the bookmarked URL.
By default the web UI can fetch page metadata from URLs you save. ODS disables
Linkding's background task processor, including Internet Archive submissions;
this also disables other background tasks. It does not provide a network egress
firewall. Keep public bookmark sharing disabled unless you intend to publish.
Duplicate URLs update the existing bookmark according to the upstream API.

## Persistence, verification and rollback

SQLite, the signing key and assets live in `data/linkding`. The upstream startup
script owns that directory as `www-data` and uWSGI drops to that account. The
root filesystem is read-only; only the data mount and temporary directory are
writable. The API request size limit is 10 MiB, container memory 512 MiB.

For a consistent cold backup, stop Linkding, copy its **whole data directory**
(including SQLite WAL files and `secretkey.txt`) with ownership intact, then
restart it. Restore only while stopped, to the same pinned image first. Token
continuity depends on restoring the database and signing key together. Reverting
the extension removes the service; keep the data until recovery is verified.

Pinned version: **1.46.2**, with a multi-architecture index digest in compose.
The opt-in test runs the copied install fragment, provisions an account, verifies
token-authorized create/search/update and anonymous rejection, recreates the
container, and checks token/data continuity. Linux amd64 Docker is the live-tested
host. ARM, macOS Docker Desktop, native Windows mounts, rootless Docker and backup
restore remain unverified; image platform availability is not a runtime result.

```bash
ODS_TEST_LINKDING_DOCKER=1 pytest -q tests/test_linkding_extension.py
```

Contracts: [installation](https://github.com/sissbruecker/linkding/blob/v1.46.2/docs/src/content/docs/installation.md),
[configuration](https://github.com/sissbruecker/linkding/blob/v1.46.2/docs/src/content/docs/options.md),
[API](https://github.com/sissbruecker/linkding/blob/v1.46.2/docs/src/content/docs/api.md).
