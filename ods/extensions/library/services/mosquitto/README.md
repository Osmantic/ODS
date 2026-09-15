# Mosquitto MQTT events

This optional extension supplies Eclipse Mosquitto 2.1.2 for MQTT 5 sensor and
workflow events. MQTT retained messages let a newly connected consumer obtain the
last value of a topic. This is a single local broker with native MQTT TCP and
WebSocket clients; it has no browser dashboard.

## Install and connect

Install **Mosquitto (MQTT Events)** from the Extensions library. Set two independent
secrets, `MOSQUITTO_PUBLISH_PASSWORD` and `MOSQUITTO_READ_PASSWORD`, before enabling.
The catalog and Compose both require them. Storage is owned by UID/GID 1883.

| Client | Endpoint | Account and permission |
| --- | --- | --- |
| Local MQTT TCP | `127.0.0.1:1883` | `publisher` writes `ods/#`; `reader` reads `ods/#` |
| Local MQTT WebSocket | `ws://127.0.0.1:9001/mqtt` | Same two accounts and ACL |
| ODS Docker workflows | `mosquitto:1883` | Same two accounts and ACL |

`MOSQUITTO_PORT` and `MOSQUITTO_WEBSOCKET_PORT` change the published ports. A reader
cannot publish, and a publisher cannot write outside `ods/#`. Anonymous MQTT
connections are rejected. Supply usernames and passwords in your client's
credential settings, not URLs or workflow exports.

For example, publish UTF-8 JSON to `ods/sensors/room/temperature` with QoS 1 and
retain enabled, then subscribe with the reader account. Publishing a zero-byte
retained message clears that topic's retained value. Retained data is visible to
every reader permitted by this shared namespace; this is not a per-user tenant
boundary. Use separate brokers or reviewed ACL changes for independent tenants.

## Authentication, health, and limits

The native password-file and ACL-file plugins enforce both MQTT listeners. A
startup script copies the ACL and generates a password file as UID 1883 under
private `/tmp`, both mode 0600, using native `mosquitto_passwd` for password hashes.
The native broker runs as UID 1883 with a read-only root
filesystem, no capabilities, and a 256 MiB container memory limit. Docker
administrators can read the configured environment and process arguments.

The private HTTP API listener on port 8081 exposes only anonymous
`GET /api/v1/version` through the ACL. The other information endpoints are denied,
and no HTTP API port is published to the host. This endpoint proves broker HTTP
availability; the native regression separately proves MQTT login, authorization,
and delivery. The manifest uses `startup_check: true` with port zero so ODS checks
availability without inventing a browser link.

Each MQTT listener allows 128 connections. Packets are limited to 1 MiB, message
payloads to 512 KiB, queued messages to 1,000 / 16 MiB per client, and broker memory
to 192 MiB. These are bounds, not throughput or capacity guarantees.

Host bindings default to loopback. ODS Docker peers can reach the listeners.
This configuration does not enable TLS; keep those peers trusted and provision
native TLS before exposing MQTT beyond the local trusted host/network. Changing
`BIND_ADDRESS` alone does not establish a secure remote service.

## Persistence, rotation, and recovery

Mosquitto stores retained messages and durable MQTT sessions in
`data/mosquitto/mosquitto.db`. It saves every 30 seconds and during graceful
shutdown. A QoS 1 PUBACK acknowledges broker acceptance, not a synchronous disk
flush; abrupt host loss may lose changes since the last save. This is neither HA
nor an exactly-once workflow execution guarantee.

To rotate a role's password, update the corresponding ODS setting and recreate
the service through the existing extension lifecycle. The startup script
rebuilds both password entries from the configured values. Existing connections
are terminated by recreation and the old password is rejected at reconnect.
Retained data remains intact. Configure clients with the new secret before
resuming producers and consumers. Rotation is intentionally different from a
one-time database bootstrap password.

For a cold backup, stop the service gracefully, copy the whole `data/mosquitto`
directory, and keep the ODS secret settings separately. Restore it while stopped,
preserve UID/GID 1883 ownership, and start the same pinned image with the intended
current passwords. Disable the extension to stop it; retain the data until
recovery has been verified. Roll back by restoring a known-good cold copy and its
matching configuration. Do not downgrade a live persistence file blindly.

## Validation

```sh
python -m pip install pytest 'paho-mqtt==2.1.0'
python -m pytest tests/test_mosquitto.py -q
sudo --preserve-env=PATH env ODS_TEST_MOSQUITTO=1 python -m pytest tests/test_mosquitto.py -q -s
```

The opt-in test uses isolated containers, networks, random loopback ports, and
temporary storage. It verifies real MQTT 5 over TCP and WebSockets, invalid and
anonymous credentials, write/namespace denials, retained Unicode payloads,
private HTTP endpoint ACLs, graceful recreation, rotation, cold restore, and
retained-message deletion. Local qualification covers Linux amd64 Docker on
WSL2. ARM, macOS, sustained load, native TLS, persistent client-session recovery,
and abrupt crash loss have not been qualified.

Version-matched references: [2.1.2 release](https://mosquitto.org/blog/2026/02/version-2-1-2-released/),
[password plugin](https://github.com/eclipse-mosquitto/mosquitto/blob/v2.1.2/www/pages/documentation/plugins/password-file.md),
[ACL plugin](https://github.com/eclipse-mosquitto/mosquitto/blob/v2.1.2/www/pages/documentation/plugins/acl-file.md),
and [native HTTP API authentication](https://github.com/eclipse-mosquitto/mosquitto/blob/v2.1.2/src/http_api.c).
