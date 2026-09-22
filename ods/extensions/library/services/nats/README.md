# NATS with JetStream

Apache-2.0 NATS Server 2.15.0, official image pinned by digest for Linux amd64/arm64.
Provides native subject-based pub/sub, request/reply and JetStream persistence.
This is a protocol service, not a web dashboard; no browser launch is advertised.

## Connect a project

Set a random 64-hex `NATS_PASSWORD`. The account is `ods`; configure your NATS
client's username/password options, keeping credentials outside source and chat.
Host clients connect to `nats://127.0.0.1:11128`. Containers on `ods-network` use
`nats://nats:4222`. Use native NATS client libraries; MQTT/AMQP clients are not
interchangeable. Nothing is connected to Portal automatically.

Create streams with the project's actual subjects, retention, size/age limits and
consumers. Core NATS pub/sub is transient; enabling JetStream does not persist all
messages automatically. Use JetStream acknowledgements and durable consumers for
the delivery behavior your application requires. No example streams, consumers,
messages or subscriptions are created during installation.

This recipe has one shared authenticated account with full subject access.
It does not isolate mutually untrusted projects. Add deliberate per-project NATS
accounts/permissions before sharing it that way. No anonymous client, cluster,
leaf-node, WebSocket or MQTT listener is configured. It is a single server, with
no replication/high availability. No public route or TLS certificate is created;
remote access requires explicitly configured TLS and ingress.

## Storage and operation

The `nats-data` volume stores `/data/jetstream`. File storage is capped at 2 GB;
memory storage at 128 MB. Memory-backed streams are not durable across restart.
Per-stream limits can be lower. The app is limited to 1 GiB/two CPUs, 256 client
connections and 1 MB messages. Capacity exhaustion is an application condition,
not a reason to remove limits silently.

Preserve the volume on reinstall. Use native stream snapshots/restores or stop
the service before a consistent volume backup. Changing the password requires
updating clients and restarting the service; it does not remove stored messages.
The private runtime config is generated from a validated hex password, which is
not put in process arguments or logs. Docker administrators can still inspect
the configured environment.

NATS monitoring is unauthenticated upstream, so port 8222 is bound only to the
container's loopback and is not published. The native `/healthz` probe requires
JetStream enabled; it does not prove client authentication, stream delivery or
consumer processing. UI readiness uses Docker health, not HTTP on the NATS port.

Runs as UID/GID 1000 with read-only root and a private persistent directory.
Windows/macOS use Linux containers through Docker Desktop; Linux uses Docker
Engine. No host paths, GPU or model configuration. Image build, authenticated
pub/sub, JetStream delivery and restore/platform behavior remain runtime validation
pending. No service, client traffic or model was started.

Sources: [release source](https://github.com/nats-io/nats-server/tree/v2.15.0),
[configuration](https://docs.nats.io/reference/config/),
[monitoring](https://docs.nats.io/learn/monitoring/monitoring-endpoints).
