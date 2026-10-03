# Eclipse Mosquitto

MQTT broker for explicit event exchange between project applications and automation clients, for example an application publishing processing status that a configured workflow subscribes to. This is a protocol service, not a dashboard or an inference model.

## Authentication and topics

Set `MOSQUITTO_PASSWORD` to exactly 64 random hexadecimal characters. The local MQTT username is `ods`. Startup validates the credential, hashes the broker password file and prepares a private temporary client configuration for the health probe; secrets are not embedded in the image or probe arguments.

The account can read and write `ods/#`, and read only `$SYS/broker/version` for health checks. No anonymous access, bridge, device registration or topic publication is performed. Use separate names such as `ods/my-project/jobs/status` for application routing. These names are conventions, not per-project isolation: clients sharing this account have access to all `ods/#` topics. Multi-user isolation requires a deliberate account/ACL setup.

## Connecting projects

Connect a chosen MQTT client to `localhost:11087` on the host, or `mosquitto:1883` from ODS containers, using the configured credentials. Add the client library and topic handling to the intended project explicitly. Installing this recipe does not edit Playground projects, create n8n/Kestra flows or allow Portal to publish messages automatically.

Host publishing is loopback-only. MQTT here is authenticated but not TLS-encrypted; remote devices need a separately configured TLS listener and network policy. No WebSocket listener or HTTP UI is provided. The ODS definition uses native Docker health evidence, not an HTTP probe against the MQTT port. The healthcheck authenticates and reads broker version without writing application topics.

## Persistence and resource limits

`mosquitto-data` preserves retained messages and persistent client sessions under `/mosquitto/data`. Normal shutdown saves state; periodic saves run every 60 seconds. This is not a complete historical event log. Back up the volume with the broker stopped and retain the client password separately. Changing the password requires updating clients and restarting the broker.

Defaults limit connections to 100, message payloads to 1 MiB and queued messages to 1,000 per client, with 256 MiB RAM and one CPU. No GPU or model changes are involved. Logs go to stdout; the inherited image log directory is temporary rather than an unused anonymous volume.

## Provenance and verification

Official Eclipse Mosquitto 2.0.22 image, pinned by digest, licensed EPL-2.0 OR BSD-3-Clause. The upstream v2.1.2 source tag was found, but its matching official image was unavailable; this recipe does not claim to package it. The image has amd64/arm64 and additional Linux variants for Docker hosts, including Windows and macOS with Docker.

Image build, authentication, topic ACLs, MQTT delivery and persistence/restore remain runtime-pending. A broker healthcheck does not establish delivery to an application subscriber.

Upstream: https://github.com/eclipse-mosquitto/mosquitto/tree/v2.0.22
