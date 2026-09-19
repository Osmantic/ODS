# InfluxDB (Flux Workflows)

Optional InfluxDB OSS **2.9.1** for local measurements consumed through the v2
write API, Flux queries, and native scheduled tasks. This deliberately uses the
2.x Flux engine; it is not an InfluxDB 3 deployment. It does not collect ODS
telemetry automatically or replace another service's database.

## Enable and connect

In **Extensions**, set `INFLUXDB_PASSWORD` (at least 8 characters) and
`INFLUXDB_ADMIN_TOKEN` (a separate random token, for example `openssl rand -hex 32`),
then enable InfluxDB. The standard extension lifecycle prepares both data
directories for UID 1000. Manual Compose users must do that before starting.

| Setting | Default / meaning |
| --- | --- |
| `INFLUXDB_PORT` | `8086`, UI and API on the host |
| `INFLUXDB_PASSWORD` | Required **initial** password for user `ods` |
| `INFLUXDB_ADMIN_TOKEN` | Required **initial operator token**, retain privately |
| Organization / initial bucket | `ods` / `workflows` |
| Initial bucket retention | 168 hours (7 days) |

Open `http://localhost:8086` and log in as `ods`. Containers on `ods-network`
use `http://influxdb:8086`. Changing the host port does not change this internal
address. Host publication follows `BIND_ADDRESS` and defaults to loopback;
use an authenticated TLS endpoint before sending credentials across a network.

Use **Load Data → API Tokens** to create a token restricted to the required
bucket and operation. Give an ingestion workflow write permission and a reader
read permission; keep the operator token out of normal workflows. For example,
with a scoped token in `INFLUX_TOKEN`:

```bash
curl --fail-with-body -X POST \
  'http://localhost:8086/api/v2/write?org=ods&bucket=workflows&precision=s' \
  -H "Authorization: Token $INFLUX_TOKEN" \
  --data-binary 'workflow_latency,workflow=sample duration_ms=42.5'
```

Query with a read token in the Data Explorer or `POST /api/v2/query?org=ods`:

```flux
from(bucket: "workflows")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "workflow_latency")
  |> mean()
```

## State and access lifecycle

The official entrypoint initializes a fresh database on internal port **9999**,
then stops that process and serves the initialized database on **8086**. Only
8086 is published. The setup port is briefly reachable by peers on the shared
Docker network: use this extension only with trusted ODS containers. Public
`/health` and `/ping` report availability; data APIs require authentication.
Health does not prove that a particular workflow token has sufficient rights.

Recreation keeps the database, users, buckets, tasks, and token permissions.
Changing either initial credential in ODS does **not** rotate an initialized
account. Use InfluxDB's user/password and API-token controls, update clients,
verify the new credential, then revoke the old token. Revocation survives
restart. In 2.9 tokens are stored hashed in the database; save a new token when
it is shown. The native CLI configuration under `data/influxdb/config` retains
the bootstrap operator token in plaintext: protect it along with `.env` and
backups. Native CLI backup/restore may need the retained original operator
token; do not assume a hashed token can be retrieved later.

## Retention, resources, and recovery

The initial `workflows` bucket expires data after seven days according to
InfluxDB's retention schedule. This is not a total disk quota. Set retention
explicitly for any additional bucket; changing bootstrap retention does not
modify an existing bucket. Keep secrets and personal content out of measurements
unless their retention and access controls have been deliberately configured.

The recipe limits the container to 2 CPUs and 2 GiB, two concurrent Flux queries,
128 MiB per query, and eight queued queries. The 128 MiB storage cache limit is
**per shard**, and Go's 1 GiB memory target is soft. Many buckets/shards or large
queries can still exhaust the container budget. Reporting, unauthenticated
metrics, pprof, and template file URLs are disabled. Authenticated Flux tasks
can perform network requests; this is not an outbound-network sandbox.

For the tested same-version cold recovery: stop InfluxDB, copy **both**
`data/influxdb/storage` and `data/influxdb/config` to a private backup, and retain
the credentials. Restore both while stopped, preserve UID/GID 1000 ownership,
and start the same pinned image. Verify a known Flux query and token permissions
before resuming writers. Disabling the extension retains these directories.
Never overwrite a live database or delete the original during recovery.

Take a backup before changing the image. Do not run an older binary against a
database already upgraded by a newer binary; rollback uses the corresponding
pre-upgrade backup and image. The regression exercises fresh setup, scoped
tokens, write/query, revocation, recreation, and same-version cold restore on
Linux amd64. It does not qualify HA, large-data performance, live backup,
cross-version restores, ARM hardware, or browser interactions.
