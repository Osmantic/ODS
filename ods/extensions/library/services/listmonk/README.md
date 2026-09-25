# Listmonk

Manage subscriber lists, segments, campaign drafts and templates locally. Official
AGPLv3 Listmonk 6.2.0 image, digest-pinned, with a private PostgreSQL 17 companion.

## Setup

Set distinct random 64-hex `LISTMONK_DB_PASSWORD` and `LISTMONK_ADMIN_PASSWORD`
secrets before installation. Open `http://localhost:11120` and log in as `ods` with
the initial administrator password. Native installation creates this account only
on the first database setup. Changing the environment does not reset existing
administrator or PostgreSQL passwords; use native account/database rotation.

The startup uses upstream's idempotent installation and upgrade sequence, then
launches the server. Database setup errors stop startup. Back up PostgreSQL before
changing the pinned image: migrations can make older application versions unusable.

In Settings -> Media, set the local upload directory to `/listmonk/uploads` before
uploading assets. The named volume persists those files. Configure the public site
URL deliberately if images, subscription pages or unsubscribe links must work from
other machines; a localhost URL is only useful on this computer.

ODS does not configure SMTP, import recipients or schedule/send a campaign. Native
starter content may be present after initialization; it is not user data or evidence
of a sent message. Select your actual provider and opt-in recipients explicitly.
API integration should use a role with the required permissions rather than sharing
the administrator password. Portal project association is still pending.

## Storage and access

`listmonk-db-data` stores subscribers, templates, settings and campaign state;
`listmonk-uploads` stores media. Back up both consistently and retain database
credentials separately. There is no automatic mailbox/provider connection or model
selection. The application runs as UID/GID 1000 with a read-only root and 1 GB memory
limit; PostgreSQL also has a 1 GB limit and no published port.

Host UI port 11120 binds to loopback. Other containers on `ods-network` can reach
the UI but need its login for management actions. Public subscription functionality
belongs to native Listmonk; inspect its settings before exposing the service remotely.
Remote use requires explicit TLS/network and sender configuration.

## Compatibility and verification

The official image supports amd64/arm64 Docker environments on compatible Windows,
Linux and macOS hosts, without a GPU/model. The health probe checks HTTP availability,
not SMTP delivery. Build, initial login, migrations, media, restore and delivery remain
unverified at runtime. No services/models were started and no messages were sent.

Sources: https://listmonk.app/docs/installation/ and
https://github.com/knadh/listmonk/tree/v6.2.0.
