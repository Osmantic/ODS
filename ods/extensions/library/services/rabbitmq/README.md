# RabbitMQ — project message queues

RabbitMQ 4.3.6, MPL-2.0, official management image pinned by digest. Upstream: https://github.com/rabbitmq/rabbitmq-server/tree/v4.3.6.

## Initial setup

Configure two distinct 64-hex secrets: `RABBITMQ_ADMIN_PASSWORD` for the initial `ods_admin` account, and `RABBITMQ_NODE_SECRET` for the Erlang node/CLI cookie. Keep the node secret stable and private; it is not a client password. Open `http://localhost:11105/` using `ods_admin`. This initializes an `ods` virtual host without creating project queues or exchanging messages. The normal guest account is not provisioned on a fresh database because the default account is overridden.

Create a separate application user in the authenticated management UI, without administrator tags, and grant only the required configure/write/read permissions on its project virtual host. Use distinct virtual hosts and users where project isolation is required. Do not give applications the bootstrap administrator credentials.

For a host application, AMQP is on `localhost:11106`; inside `ods-network`, use `rabbitmq:5672`. Set the exact virtual host and project credentials in the client's secret configuration. Choose durable queues, persistent messages, publisher confirms and consumer acknowledgement behavior according to the project's delivery requirements. This single-node deployment does not provide replicated availability or exactly-once application execution.

No project client, worker, queue, exchange binding, remote broker or model is connected automatically. Optional MQTT/STOMP/stream protocols are not enabled by this recipe.

## Persistent identity and credentials

The named `rabbitmq-data` volume preserves `/var/lib/rabbitmq`. Hostname `ods-rabbitmq` and node name `rabbit@ods-rabbitmq` are fixed so container recreation does not silently select a new node data directory. Retain this identity and the node secret when restoring. Never mount the same node data directory into simultaneous brokers.

Default administrator credentials apply only to an empty database. Later environment edits do not rotate an existing user's password; use authenticated RabbitMQ administration. Back up definitions and persistent node data using the documented stopped-node procedure. Definition exports alone are not backups of queued messages. Removing the extension must not implicitly erase the volume.

The original image initialization/chown and privilege drop to the rabbitmq user are retained. This recipe leaves the upstream root filesystem writable for runtime-generated files; it does not claim a read-only runtime. Console logging is bounded by Docker log rotation. Limits: two CPUs, two GiB memory, one GiB memory alarm, one GiB minimum free disk, 128 channels per connection and 16 MiB messages. Resource alarms block publishers instead of pretending delivery succeeded. These are local development defaults, not production cluster sizing.

## Reachability and checks

Both published ports bind to loopback. AMQP and management HTTP are not TLS-enabled; use deliberately configured TLS before remote exposure. Peers on the shared ODS network can reach the authenticated listener. Erlang distribution/epmd ports are not published to the host, and no cluster is joined automatically.

The native Docker health check verifies that RabbitMQ is running and has no local alarms. The dashboard's HTTP check sees the login page; neither proves a client's end-to-end publish/consume behavior.

Official image supports the targeted Linux amd64/arm64 Docker environments on Windows/Linux/macOS. No GPU/model dependency. Image build, first login, project permissions, real delivery, restart/restore and platform validation remain pending. No broker or model was started during preparation.
