# Mumble voice server

BSD-3-Clause Mumble 1.5.915, official digest-pinned Docker image for amd64/arm64.
Provides native low-latency voice rooms and channel/account permissions. It is
not a browser conferencing UI; no HTTP launch link or client install is claimed.

## Connect and administer

Set independent random 64-hex `MUMBLE_SERVER_PASSWORD` and
`MUMBLE_SUPERUSER_PASSWORD`. Connect a native Mumble client to `127.0.0.1:11131`
from this computer and use the shared server password. For administration, log in
as `SuperUser` with its separate password, then create/register ordinary users and
configure channel ACLs. Do not share the administrative credential with guests.

The native container entrypoint reapplies the SuperUser password on startup.
Change the extension configuration when rotating it; a database-only password
change will be superseded at restart. User accounts, channels and certificates
remain in the database. The wrapper validates both secrets before native startup.

Both TCP and UDP are loopback-bound. Remote teammates need explicit host-interface
bindings and firewall rules for **both** protocols; their localhost is not this
server. Other ODS containers use `mumble:64738`. The native server creates its own
certificate when none is configured. Verify its fingerprint before trusting it,
or configure a suitable certificate deliberately. No ACME, public directory
registration, router change or microphone access is configured by ODS.

## Persistence and limits

`mumble-data` retains `/data`, including SQLite, configuration and certificate
state. Back it up with the server stopped and preserve it on reinstall. Losing
it loses registered accounts/ACLs and may change the trusted certificate identity.
Native environment-derived configuration is regenerated on start; manage server
options through the recipe/environment, not edits to its generated INI file.

The recipe uses 25-user and 72000-bit/s per-user bandwidth settings, a 1 GiB/two-CPU
container limit, and disables the ICE management listener. Application permissions
still need owner configuration. It retains the native entrypoint and runs as
UID/GID 10000 with private data ownership and read-only container root.

Docker health checks a bounded TCP connection only. It does not authenticate a
client, inspect UDP delivery or test voice quality. No conversations, fake users,
audio recordings or test calls are created. On Windows/macOS use Docker Desktop
Linux containers; on Linux use Docker Engine. No GPU, host paths or model changes.

Image build, login, TCP/UDP audio, channel permissions and restore/platform runtime
remain validation pending. No server, model or audio client was started.

Sources: [Mumble](https://github.com/mumble-voip/mumble/tree/v1.5.915),
[official Docker configuration](https://github.com/mumble-voip/mumble-docker).
