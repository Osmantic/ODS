# PairDrop for ODS

Send selected files or text between paired browser sessions using WebRTC.
This is a transfer application, not a file manager, sync service or Portal upload
endpoint. It does not read project files automatically or save received files
inside the ODS workspace.

## Local use

Install and enable `pairdrop`, then open `http://localhost:11061` in two browser
sessions/profiles. `PAIRDROP_PORT` changes the loopback host port. Choose the peer,
select the file/message and accept the incoming transfer. Received downloads go
where the receiving browser saves them. Transfers require both browsers to remain
open and able to establish a WebRTC connection.

The original Node server is invoked directly under UID 1000, avoiding npm's
runtime log/cache writes. The filesystem is read-only with bounded temporary
storage. There is no server file archive or account database to persist. Pairing
and browser preferences belong to the browser origin; changing the port changes
that origin. Clearing browser data may require pairing again.

## Other devices and networks

The default endpoint is loopback; another computer or phone cannot reach it by
opening its own localhost. For other devices, place a separately configured HTTPS
reverse proxy in front of the local endpoint. Forward WebSocket Upgrade/Connection
headers and overwrite X-Forwarded-For with the actual client address. Do not expose
the backend directly or pass client-supplied forwarding headers unchanged: upstream
uses client addresses for device discovery. This recipe does not configure a
public domain, TLS certificate, firewall rule or router port forwarding.

`rtc-config.json` deliberately contains no external STUN/TURN servers. Direct
connections on a reachable local network can work; cross-network/NAT traversal is
not promised. Configure your own STUN/TURN infrastructure and this JSON explicitly
if needed, then rebuild. WebSocket file fallback is disabled so file payloads are
not silently relayed through this server. There is no public signaling service.
Browser permissions/HTTPS govern clipboard, notifications and persistent pairing.

## Platforms and verification

Official release v1.11.2 publishes Linux amd64 and arm64 images. Windows/macOS
require Docker Desktop Linux containers; Linux uses Docker Engine. No GPU or
model is used. The root LICENSE and image metadata say GPL-3.0; package.json's ISC
label does not override the repository license recorded here.

The HTTP health check only establishes that the web server answers. Image build,
peer discovery, transfer, pairing persistence, proxy behavior and OS/browser
combinations remain runtime-pending. No containers were started for this recipe.

Upstream: https://github.com/schlagmichdoch/PairDrop
Deployment: https://github.com/schlagmichdoch/PairDrop/blob/v1.11.2/docs/host-your-own.md
