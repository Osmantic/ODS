# Gotify for ODS

A local notification server with application tokens, message history and web
clients. The server code is MIT; the upstream logo is CC-BY-4.0. This integration
does not connect any sender, recipient or external messaging account automatically.

## Setup

Install `gotify` and set a strong `GOTIFY_ADMIN_PASSWORD`. Open
`http://localhost:11073` and sign in as `admin`. `GOTIFY_PORT` changes the loopback
host port. Public registration is disabled. Create the users and application
tokens you actually need in the UI, then explicitly configure the chosen sender.
Changing the initial password setting does not reset an existing account.

The API host is `http://gotify:8080` for a deliberately connected ODS container
on `ods-network`. Application tokens send messages; client tokens read messages.
Do not treat an application token as a read-only credential. Use separate tokens
for separate consumers and revoke them through Gotify when no longer needed.
No message is sent as part of this installation recipe or its configuration checks.

The default host endpoint is local HTTP. Phone/remote clients require an explicit
reachable HTTPS endpoint and appropriate WebSocket forwarding. They cannot reach
this server by using their own localhost. Remote routing, push-client setup,
OIDC providers and browser notification permissions are not configured here.

## Storage and runtime

`gotify-data` retains the SQLite database, users, tokens, messages and uploaded
application icons at `/data`. Back it up while the app is stopped and protect the
backup because it contains credentials and message content. Database upgrades
should follow upstream release notes; preserve a compatible backup before changing
versions. This recipe does not enable a custom retention/deletion policy.

The pinned 3.1.1 official image keeps its native `serve` command and runs under
UID 1000 with prepared data ownership. `GOTIFY_PLUGINSDIR` is explicitly empty,
which disables the native plugin-directory scan. Use Docker Engine
on Linux or Docker Desktop Linux containers on Windows/macOS. Published image
architectures are recorded in upstream.json; no GPU or model is involved.

Native `/health` reports server readiness, not message delivery. Build, initial
login, token scopes, message flow, client reconnects and restore/platform execution
remain runtime-pending. No application containers were started.

Source: https://github.com/gotify/server/tree/v3.1.1
