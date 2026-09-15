# RabbitMQ local work queues

An optional AMQP 0-9-1 broker for background jobs submitted by local scripts,
workers and explicitly configured workflows. It does not reroute ODS chat or
install a worker. RabbitMQ is pinned to the official 4.3.5 management image and
its multi-platform digest. No GPU is required; allow 2 GB RAM and free disk.

## Install and connect

1. Set a unique, strong `RABBITMQ_PASSWORD` in the installation `.env` or the
   Extensions configuration form. Install RabbitMQ from Extensions.
2. Open `http://localhost:15672` and sign in as `ods`. The initial virtual host
   is `ods`. `RABBITMQ_PORT` changes the console port; `RABBITMQ_AMQP_PORT`
   changes the host AMQP port (default `5672`).
3. Connect an AMQP client to `127.0.0.1:5672` with virtual host `ods`, username
   `ods` and the password. Containers on `ods-network` use `rabbitmq:5672`.
   Create a separate user and virtual-host permissions for each worker; the
   initial `ods` account is an administrator.

Use durable queues, persistent messages, publisher confirms and explicit
consumer acknowledgements for jobs that must survive a broker restart. A
consumer should acknowledge only after committing its work, and handle
redelivery idempotently. A durable queue alone does not make transient messages
persistent. Set queue/message TTLs and size limits appropriate for your workload.

## State, operations and recovery

`data/rabbitmq` holds the broker database, users, permissions, Erlang cookie and
durable messages. The stable `rabbit@ods-rabbitmq` node identity must be retained
when restoring that directory. The installer prepares UID 999 bind ownership.
The container has a read-only root and a writable persistent data directory.
Erlang discovery/distribution listens only on container loopback; only the AMQP
and management client ports are published.

The console login page is the dashboard HTTP reachability probe. Docker's
healthcheck separately verifies that the broker is running without local disk
or memory alarms. A reachable login page does not prove a queue can accept a
publish. Memory/disk alarms deliberately apply publisher backpressure.

The two client ports bind to `127.0.0.1` by default. An explicit `BIND_ADDRESS`
also applies to both. These listeners use plain AMQP/HTTP: configure native TLS
or an appropriate secured tunnel before exposing them beyond a trusted host.
Changing the bind does not enable TLS or weaken RabbitMQ authentication.

The environment password seeds an **empty** database. Changing `.env` and
restarting does not rotate an existing account: change its password through
RabbitMQ, update clients and then update `.env` for future empty installs.
Disabling/re-enabling and recreating the container keep the database.

For a consistent filesystem backup, stop RabbitMQ, copy `data/rabbitmq` with
ownership and permissions intact, and restart it. Restore to the same pinned
version and node identity. Definition export alone does not include messages.
Before upgrading, retain that cold backup and follow the supported RabbitMQ
upgrade path. Rolling back a recipe does not downgrade or restore its database.
This is a single node, not a replicated/high-availability broker.

## Verification

`python3 -m pytest ods/tests/test_rabbitmq.py -q` checks generated Compose plans,
catalog wiring, port overrides and required credentials. With Docker and
`pika==1.3.2` installed, `ODS_TEST_RABBITMQ=1` additionally exercises authenticated
AMQP publish confirms, redelivery, durable messages across recreation, password
rotation, native health and cold restore using isolated temporary storage.
It creates only task-owned containers/networks and removes them afterward.

References: [official image](https://hub.docker.com/_/rabbitmq/),
[reliability](https://www.rabbitmq.com/docs/reliability),
[backup](https://www.rabbitmq.com/docs/backup),
[access control](https://www.rabbitmq.com/docs/access-control).
