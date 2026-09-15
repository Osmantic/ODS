# Loki: local LogQL

Loki 3.7.7 stores a small local log workload with TSDB v13, filesystem chunks and
a write-ahead log. A separate authenticated gateway exposes a narrow LogQL API.
This recipe has one store and no replication across hosts. Grafana's filesystem
backend is intended for low-volume use; it is not their supported production
object-store deployment.

## Enable and send logs

From `ods/` on Linux:

```bash
sudo install -d -m 0700 -o 10001 -g 10001 data/loki
```

Generate two distinct bcrypt password hashes using a trusted local password
tool. For example, `htpasswd -nB writer` prompts for the writer password and
prints `writer:<hash>`; keep only the hash in `LOKI_WRITER_HASH`. Repeat for
`reader` and `LOKI_READER_HASH`. The gateway requires a single 60-character bcrypt
hash for each account. Keep plaintext passwords private for your clients.

Supply both hashes in Extensions and enable Loki. Host clients use
`http://localhost:3100`; trusted ODS containers use `http://loki:8080`.
`LOKI_PORT` overrides the host port and `BIND_ADDRESS` defaults to loopback.
Use native HTTP Basic authentication:

- `writer` can only `POST /loki/api/v1/push` with native JSON log streams and
  nanosecond timestamps encoded as strings.
- `reader` can only `GET` query, query_range, labels, series and label-value
  endpoints under `/loki/api/v1`. For example, query a range with
  `{job="ods-test"} |= "artifact"`; metric queries such as
  `sum(count_over_time({job="ods-test"}[5m]))` use the native query endpoint.
- `/ready` is public service-readiness information. The gateway does not expose
  metrics, configuration, profiling, flush, delete, ruler APIs, tail WebSockets,
  OTLP ingestion, or a browser console. No log collector is installed or given
  host/Docker access. Configure producers explicitly and avoid logging secrets.

Loki's `auth_enabled` means tenant-header handling, **not password verification**.
NGINX verifies credentials and always sets tenant `ods`, replacing client-supplied
`X-Scope-OrgID`. Reader and writer cannot exchange roles. The unauthenticated
store joins only a private internal Docker network and has no published ports;
gRPC binds loopback. A Docker host administrator can still inspect that network
and storage. This is one trusted local tenant, not a tenant administration system.
HTTP Basic credentials need TLS termination before remote/untrusted transport.

## Limits and lifecycle

The store runs UID 10001 with a read-only root, owned `/loki`, 64 MiB temporary
storage, 1 CPU / 1536 MiB limits and a 1 GiB Go-memory target. The gateway runs UID
101 with 128 MiB RAM and private temporary password files (0600). The distroless
Loki image has no shell or HTTP client; **the gateway health probe calls native
`/ready` through the real proxy**. The store dependency requires process startup,
not a fabricated config-only health check. Anonymous usage reporting is disabled.

Ingestion is limited to 4 MiB/s with an 8 MiB burst, 1,000 active streams, 64 KiB
lines and 2 MiB gateway requests. Samples older than seven days are rejected;
queries have a 30-second timeout and bounded parallelism. Keep labels low-cardinality.
These settings are resource limits, not measured throughput or a disk quota.

Compactor retention is configured for seven days with a two-hour deletion delay.
Deletion is asynchronous and depends on compaction; it does not free space
precisely at a timestamp or react to disk pressure. Operator deletion APIs are
blocked by the gateway. Native deletion bookkeeping remains enabled inside the
private store so query cache-generation requests can complete. The native
lifecycle test validates admission limits but does not age
seven days of data or prove physical retention deletion.

Rotate an account by updating its hash and recreating the gateway. Existing data
still belongs to fixed tenant `ods`; the old password immediately stops working
for new requests. Password rotation does not cancel an already accepted write.
Keep the other role's password distinct.

## Recovery and qualification

Keep the complete `data/loki` directory: chunks, TSDB index, WAL and compactor
state belong together. Stop both services gracefully before taking a private cold
copy and recording image/configuration/hash values. Restore into a separate
directory owned by UID/GID 10001, start the same version and verify actual LogQL
results plus denied operations. Disable retains state. Upgrade rollback uses the
matching pre-upgrade cold copy; do not downgrade migrated files in place.

```bash
python -m pip install pytest bcrypt==5.0.0
python -m pytest ods/tests/test_loki.py -q
sudo --preserve-env=PATH env ODS_TEST_LOKI=1 python -m pytest ods/tests/test_loki.py -q -s
```

Native Linux amd64 qualification exercises Unicode push/query and metric results,
label APIs, role denials, replacement of forged tenant headers with an actual
second-tenant negative fixture, old/oversize sample rejection, blocked management
routes, recreation, hash rotation, full cold recovery and invalid-hash startup
failure/recovery. Host-level fixture access seeds the private second tenant; the
backend remains unpublished. Browser/Grafana/collector integration, retention
aging, power-loss durability, load, TLS, ARM/native macOS/Windows and cross-version
migration remain unqualified.

References: [authentication](https://github.com/grafana/loki/blob/v3.7.7/docs/sources/operations/authentication.md),
[pinned configuration](https://github.com/grafana/loki/blob/v3.7.7/docs/sources/shared/configuration.md),
[filesystem limits](https://grafana.com/docs/loki/latest/operations/storage/filesystem/).
