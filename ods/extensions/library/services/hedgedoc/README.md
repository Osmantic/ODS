# HedgeDoc

HedgeDoc 1.12.0 provides a local collaborative Markdown editor. This recipe uses
PostgreSQL 17, local email/password accounts and private notes by default. It
requires no LLM or GPU. Both images are pinned by digest.

## Configure and start

Before installing from the Extensions library, add these values to the ODS
installation `.env`. Generate each secret separately with `openssl rand -hex 32`:

```dotenv
HEDGEDOC_DB_PASSWORD=replace-with-a-random-secret
HEDGEDOC_SESSION_SECRET=replace-with-another-random-secret
```

Install HedgeDoc from the dashboard Extensions library. After it becomes healthy,
create an account with the upstream interactive password prompt:

```bash
docker exec -it ods-hedgedoc bin/manage_users --add you@example.com
```

Open `http://localhost:7824` and sign in with that account. The email identifies a
local account; no mail server is required. Repeat the command with another email
to invite a collaborator. Public registration and anonymous note creation/editing
are disabled. New notes are private: the owner must deliberately change a note's
permission before another account can collaborate. HedgeDoc's permission menu
controls who can read or edit each note; review it before sharing a link.

File uploads are disabled (`CMD_ENABLE_UPLOADS=none`) because upstream filesystem
uploads are served separately from private note permissions. The uploads mount is
retained for upstream startup compatibility. Remote avatar lookup is disabled.
Markdown can still reference external images and links, which a reader's browser
may load; this recipe does not promise that arbitrary Markdown is offline.

## Address and remote access

The service binds to loopback by default. `HEDGEDOC_PORT` changes the published
port and its default browser URL together. If users access the service through a
different hostname or a TLS reverse proxy, set `HEDGEDOC_PUBLIC_HOST` to the
browser-facing **hostname and port**, without a scheme or path. For example:

```dotenv
HEDGEDOC_PUBLIC_HOST=notes.example.test
HEDGEDOC_USE_SSL=true
```

`HEDGEDOC_USE_SSL` selects generated HTTPS URLs; it does not install a certificate
or start a TLS listener. Configure HTTPS and WebSocket forwarding on your reverse
proxy before allowing remote users to sign in. Keep `BIND_ADDRESS` at loopback
unless you have deliberately configured protected network access. A wrong public
hostname causes login redirects and collaborative connections to point elsewhere.

## Data, verification and rollback

Accounts, Markdown and revisions live in `data/hedgedoc/postgres`; the application
uses `data/hedgedoc/uploads` for its upstream uploads directory. Retain `.env`
secrets with a protected backup. Stop both application and database before copying
`data/hedgedoc` for a consistent cold backup. Recreate containers without deleting
these directories. Changing `HEDGEDOC_DB_PASSWORD` alone does not rotate an
initialized PostgreSQL account password; change the database account and client
configuration together. Changing the session secret signs users out.

To roll back an image/database upgrade, stop the extension and restore both the
matching image pins and its pre-upgrade data backup. Do not start an older
PostgreSQL major version against a newer data directory. Disabling the extension
stops it; deleting the persisted directory destroys its notes and accounts.

The opt-in `ods/tests/test_hedgedoc_extension.py` test starts this exact recipe,
creates a local account and private Markdown note, checks access boundaries and
reads the same note after container recreation. Browser collaboration, TLS proxy
deployment and native ARM/macOS/Windows activation require separate verification.

Upstream: [configuration](https://docs.hedgedoc.org/configuration/),
[Docker deployment](https://docs.hedgedoc.org/setup/docker/), and
[1.12.0 release](https://github.com/hedgedoc/hedgedoc/releases/tag/1.12.0).
