# Miniflux — feed library for research workflows

An optional RSS/Atom reader with local PostgreSQL storage and an authenticated API. Use it to curate sources, read articles, and let n8n retrieve unread entries for a local summarization workflow. Feed downloads contact the publisher; this is not an offline source of current news.

## Setup

Set two distinct strong secrets in the ODS `.env` file before installing **Miniflux (Feed Reader)** from Extensions:

- `MINIFLUX_DB_PASSWORD`: retained for the private database connection.
- `MINIFLUX_ADMIN_PASSWORD`: initial password for the `admin` account.

Compose refuses missing or empty passwords. Quote `.env` values according to Compose syntax. The database password travels separately from its URL, so URL punctuation does not require percent-encoding. No database port is published.

Enable the extension and visit `http://localhost:8098`. Sign in as `admin`, add a feed or import OPML, then create a separate non-admin user and API key for automation. In n8n, use an HTTP Request node with `GET http://miniflux:8080/v1/entries?status=unread` and store its `X-Auth-Token` header in a credential, not in the URL. This extension does not automatically send articles to an LLM.

## Settings

| Variable | Default | Behavior |
| --- | --- | --- |
| `MINIFLUX_PORT` | `8098` | Published port; listener remains `8080` |
| `MINIFLUX_BASE_URL` | empty; derives `http://localhost:${MINIFLUX_PORT:-8098}` | Set explicitly for a protected external hostname or URL prefix |
| `MINIFLUX_DB_PASSWORD` | required | Must match the initialized PostgreSQL account |
| `MINIFLUX_ADMIN_PASSWORD` | required | Creates the initial admin; does not reset an existing account |

The default binding is loopback. Configure a trusted HTTPS access path before using credentials across a network. Feed fetching and outgoing integrations retain Miniflux's private-network restrictions. No authentication proxy, signup bypass, or external integration is enabled.

Changing only `MINIFLUX_PORT` also updates the localhost URLs shown for the
bookmarklet, Fever, and Google Reader integrations. An explicit nonempty
`MINIFLUX_BASE_URL` always wins, including an existing saved value. To opt into
the derived localhost URL on an older installation, clear that setting and
recreate Miniflux, then copy the new bookmarklet/integration URLs. Existing
bookmarks and external clients are not rewritten automatically. Keep an
explicit URL for remote access; a bind address does not identify a public hostname.

## Data and upgrades

Miniflux 2.3.3 and PostgreSQL 17.11 are pinned by multi-architecture digest. PostgreSQL persists under `data/miniflux/postgres`; the Miniflux container is stateless. Disabling or recreating the application does not delete subscriptions, entries, or accounts. The database is a Compose dependency and may continue running when only the application is stopped.

Before an upgrade, export a logical backup from the host:

```bash
docker exec ods-miniflux-db pg_dump -U miniflux -d miniflux > miniflux-backup.sql
```

Keep this file private. Restore into an empty compatible database with the application stopped using PostgreSQL's `psql`; keep an untouched copy of the original data until recovery is verified. Do not change PostgreSQL major versions against the existing directory or downgrade after a Miniflux migration without a compatible backup. Password rotation requires updating the database account and ODS configuration together.

CPU only. Published image variants include Linux amd64 and arm64. The supplied lifecycle test covers Linux amd64 Docker; Apple Silicon, rootless Docker, native Windows paths, remote access, and public-feed availability require environment-specific verification.

References: [Docker installation](https://miniflux.app/docs/docker.html), [configuration](https://miniflux.app/docs/configuration.html), [API](https://miniflux.app/docs/api.html), [upgrades](https://miniflux.app/docs/upgrade.html).
