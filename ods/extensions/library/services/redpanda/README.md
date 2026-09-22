# Redpanda Kafka event log

Optional Redpanda **26.2.2** provides one local Kafka-compatible broker for
retained events, consumer groups, and replay from earlier offsets. Topics and
ACLs are created explicitly through native Kafka/rpk administration. This is a
single-node deployment with replication factor one, not a highly available
cluster or an exactly-once application-processing guarantee.

## Enable and connect

Install **Redpanda (Kafka Event Log)** from the Extensions library and set:

| Setting | Meaning |
| --- | --- |
| `REDPANDA_PASSWORD` | Required initial SCRAM-SHA-256 password for administrator `ods` |
| `REDPANDA_PORT` | Host Kafka port and advertised host-client port; default `19092` |
| `REDPANDA_ADVERTISE_HOST` | Hostname/IPv4 address clients receive in broker metadata; default `127.0.0.1` |

The bootstrap password cannot contain a colon, carriage return, or newline,
because the native `RP_BOOTSTRAP_USER` format uses colons as separators. The
entrypoint rejects these values before starting the broker and does not print
them. A separate random password, such as `openssl rand -base64 32`, is suitable.

Configure Kafka clients with `SASL_PLAINTEXT`, `SCRAM-SHA-256`, and the appropriate
user/password. Host clients connect to `127.0.0.1:19092`. ODS Docker clients use
`redpanda:9092`. The two named listeners advertise the corresponding address for
subsequent client connections; changing only the initial bootstrap address in a
client cannot fix an incorrect advertised host. A custom host port is applied
to both the published port and the host listener's native metadata.

Bindings default to loopback. SASL authenticates users but this recipe does not
configure TLS. Docker peers and the local host must be trusted. Before remote
exposure, provision native Kafka TLS and review ACLs; setting `BIND_ADDRESS` or
an advertised host alone does not establish a secure remote deployment. IPv6,
proxies, and remote clients are not qualified by this recipe.

There is no browser UI. The private Admin API on port 9644 requires native HTTP
Basic authentication for protected endpoints. Its readiness endpoint remains
public by Redpanda design. Native metrics also remain on this private listener;
the port is not published by the production fragment. The RPC listener binds
only to loopback. HTTP Proxy and Schema Registry are not enabled.

## Create least-privilege workflow accounts

The `ods` bootstrap account is a superuser for initial administration. Do not
reuse it in workflow exports or ordinary producers/consumers. Use native
`rpk security user` and `rpk security acl` commands to create dedicated users and
grant only the required topic operations and consumer-group names. Native rpk
client settings accept the broker address, SASL username/password, and mechanism;
keep credentials in a protected client profile or environment rather than
embedding them in commands shared with others.

Create topics explicitly; automatic topic creation is disabled. A reader needs
READ/DESCRIBE on its topic and READ on its consumer group. A producer needs the
appropriate topic write authority. The native fixture creates a reader with
topic/group ACLs, verifies delivery and committed offsets, and confirms that it
cannot publish. It also validates host and Docker listener metadata through real
Kafka clients.

Consumer offsets are persisted independently of the retained log. Committing an
offset records progress; a consumer can resume after a broker recreation or
explicitly replay older retained offsets. Retention still deletes old segments,
so an offset cannot recover data that has already expired. Application side
effects and duplicate-event handling remain the workflow's responsibility.

## Bootstrap, resource limits, and durability

Native cluster bootstrap sets uniform SASL, the `ods` superuser, protected Admin
API, disabled usage reporting, explicit topic creation, replication factor one,
and disabled write caching. It intentionally does not mix global `enable_sasl`
with per-listener authentication properties. The stock development-mode recipe
is not used: developer mode is false, and write caching is disabled cluster-wide
so a topic override cannot re-enable it.

The container runs as UID/GID 101, with a read-only root filesystem, no
capabilities, one CPU, and a 3 GiB memory limit. Redpanda receives 2 GiB of memory;
temporary configuration uses 64 MiB tmpfs. Host tuning/preflight via rpk is
disabled for this container recipe (`--check=false`), and it uses overprovisioned
scheduling without locking memory. It does not alter host sysctls, disk tuning,
or kernel configuration. Native broker startup and its actual configuration
are qualified; these choices are not performance or production-capacity claims.

Default retention is one day / 1 GiB per partition with 128 MiB segments. Segment-based
retention and internal cluster logs mean this is not a hard 1 GiB total-directory
quota. Monitor disk usage and size topic retention to the workload. Producer
`acks=all` with write caching disabled uses the native broker's durable-write
path, but replication factor one cannot survive loss of the data disk. Abrupt
power loss, faulty storage, transactions, and cross-version recovery have not
been tested by this fixture.

## Password rotation and recovery

`RP_BOOTSTRAP_USER` and `bootstrap.yaml` seed a new cluster. They do not reset
persisted users or cluster properties on restart. Rotate an initialized user's
password through native `rpk security user update` or the authenticated Admin
API, then update the stored ODS password setting to match. An obsolete bootstrap
password must not reset a rotated user; this is covered by the native regression.
Cluster-setting changes use native cluster configuration rather than editing
the initial bootstrap file and expecting it to be reapplied.

All broker state, including topic logs, users, ACLs, consumer offsets, and cluster
configuration, resides in `data/redpanda`. For a cold backup, stop the broker
gracefully, copy the whole directory, and retain the installed recipe and matching
secret settings. Restore while stopped with UID/GID 101 ownership and the same
pinned image, then verify authenticated metadata, a known retained record, and a
consumer group's progress. Do not copy live log files or run the original and
restored identities as two nodes in the same cluster.

Disable stops the extension and retains data. Back up before upgrading. Rollback
requires the matching pre-upgrade data copy and image; do not blindly downgrade
the on-disk cluster state. Deleting a topic removes its retained records and is
not an undo operation for a workflow that already consumed them.

## Validation

```sh
python -m pip install pytest PyYAML 'confluent-kafka==2.11.1'
python -m pytest tests/test_redpanda.py -q
sudo --preserve-env=PATH env ODS_TEST_REDPANDA=1 python -m pytest tests/test_redpanda.py -q -s
```

The opt-in fixture uses the pinned native image, isolated storage/network, and
loopback ports. It verifies bootstrap-format rejection, native configuration,
SCRAM and Admin API denials, both metadata paths, Unicode records, reader ACLs,
explicit offset commits, replay, graceful recreation, native password rotation,
ignored obsolete bootstrap credentials, cold restore, and topic deletion.
Linux amd64 Docker on WSL2 is qualified. ARM/native macOS/Windows, TLS, remote or
multi-broker clients, sustained load, disk-full behavior, abrupt crashes,
transactions, and Kafka feature compatibility beyond this fixture remain open.

References: [26.2.2 release](https://github.com/redpanda-data/redpanda/releases/tag/v26.2.2),
[authentication](https://docs.redpanda.com/streaming/current/manage/security/authentication/),
[versioned bootstrap parser](https://github.com/redpanda-data/redpanda/blob/v26.2.2/src/v/cluster/security_frontend.cc),
and [upstream license](https://github.com/redpanda-data/redpanda/blob/v26.2.2/licenses/BSL.md).
