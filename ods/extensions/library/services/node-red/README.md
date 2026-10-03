# Node-RED

Visual event-driven programming for project HTTP APIs, MQTT events, timers and transformations. This integration runs the official Apache-2.0 Node-RED 5.0.7 application, not a substitute ODS workflow screen.

## Setup

Set `NODE_RED_ADMIN_PASSWORD` to a unique password of 16–72 UTF-8 bytes and `NODE_RED_CREDENTIAL_SECRET` to 64 random hexadecimal characters. The password limit avoids bcrypt's silent truncation. Startup rejects missing/invalid values without logging them, generates a bcrypt hash, and removes the bootstrap values from the Node process environment.

Open `http://localhost:11088` and sign in as **ods**. Editor access requires authentication. HTTP-in flow endpoints live under `/api` and use HTTP Basic authentication with the same account/password. This is a single-owner instance; a project name does not isolate tenants. HTTP Basic requires TLS if you deliberately expose it outside the local machine. No endpoint or automation is created by installation.

## Project connections

For MQTT flows, add/configure the built-in MQTT nodes explicitly. If Mosquitto is installed, use `mosquitto:1883` from this container and its separately configured credentials, with topics such as `ods/my-project/status`. Mosquitto is optional: installing Node-RED does not install or publish to a broker. HTTP nodes can call a chosen service on `ods-network`; localhost inside a flow refers to Node-RED's container, not the host.

Save credentials in node credential fields so Node-RED encrypts them with the stable credential key. Do not paste keys into flow source or exported JSON. Installing this extension does not modify Playground files or synchronize Portal goals. Node-RED's separate Git Projects feature is disabled; do not confuse flow storage with an ODS project directory.

## Storage and execution

The named volume `node-red-data` stores flows, encrypted credentials, installed palette nodes and context under `/data`. File-backed context normally flushes every 30 seconds; sudden termination may lose recent updates. Back up the stopped volume and retain the credential encryption key separately. Changing that key without migrating credentials prevents decryption.

User-installed palette nodes run application code; their native dependencies may need additional image packages. The base image is read-only with a writable data volume and bounded temporary directory. Automatic external modules in Function nodes are disabled; ordinary functions have a 30-second limit. The process runs as UID/GID 1000, with 1 GiB RAM, a 768 MiB JS heap and two CPUs. These are defaults, not a throughput guarantee. No GPU, model selection or inference context settings are changed.

## Provenance and readiness

Official image pinned to its verified digest for linux/amd64 and linux/arm64. Windows/macOS require a Linux-container Docker runtime; Linux uses Docker directly. No architecture-specific host paths are mounted. The HTTP probe checks editor-server availability, not success of any user flow.

Configuration/schema checks do not prove login, flow execution, MQTT delivery or persistence. Image build and these runtime checks remain pending; no containers were started for this change.

Sources: [upstream](https://github.com/node-red/node-red/tree/5.0.7), [Docker deployment](https://nodered.org/docs/getting-started/docker), [authentication](https://nodered.org/docs/user-guide/runtime/securing-node-red).
