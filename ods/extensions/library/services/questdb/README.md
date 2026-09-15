# QuestDB — local time-series SQL

Store timestamped workflow events and analyze them with SQL. QuestDB is an
optional, separate database; enabling it does not replace Token Spy storage or
send ODS telemetry to it. Configure each producer explicitly.

## Enable and connect

Install QuestDB from Extensions, provide a strong `QUESTDB_PASSWORD`, then
enable it. The username is `ods` for both HTTP and PostgreSQL wire protocol.
Open the Web Console at `http://localhost:9100/index.html`. The default HTTP port is 9100
(`QUESTDB_PORT`); PostgreSQL clients use 8812 (`QUESTDB_PG_PORT`). ODS containers
on `ods-network` use `questdb:9000` for HTTP and `questdb:8812` for PostgreSQL.

Write a synthetic event with HTTP InfluxDB Line Protocol (curl prompts for the
password). A timestamp is optional; the server supplies ingestion time here:

```bash
curl --fail --user ods -H 'Content-Type: text/plain' \
  'http://localhost:9100/write?precision=n' \
  --data-binary 'workflow_events,workflow=example duration_ms=42.5,status="ok"'
curl --fail --user ods --get 'http://localhost:9100/exec' \
  --data-urlencode 'query=SELECT * FROM workflow_events ORDER BY timestamp DESC LIMIT 10'
```

HTTP ingestion acknowledges a WAL commit; query visibility follows WAL apply.
For SQL clients, connect to database `qdb`, username `ods`, with your password.
QuestDB speaks PostgreSQL wire protocol but has its own SQL engine; it is not a
drop-in replacement for PostgreSQL extensions or applications.

## Access and resources

HTTP query/ingestion and PostgreSQL require authentication. Legacy ILP TCP/UDP
and QWP UDP are disabled to avoid separate unconfigured ingestion paths. The
read-only PostgreSQL account is disabled. The health listener is confined to
container loopback and is not published. The dashboard's `/index.html` probe checks Web
Console reachability; the container's `/status` probe checks the native service.

Host bindings default to loopback and preserve an explicit `BIND_ADDRESS`.
Open Source authentication does not provide TLS or per-user RBAC. Use a trusted
TLS tunnel/proxy before remote access. Do not apply these Open Source settings
to an Enterprise image, whose access-control configuration differs.

The recipe runs as UID 10001, with 2 CPUs, 2 GiB container memory, 512 MiB JVM
heap, and two threads per shared worker pool. Native/mapped memory also consumes
the container budget. Size your data and workload accordingly. Anonymous
telemetry is disabled. Tables have no blanket retention policy: use partitioned
tables and an explicit TTL suitable for your events; monitor disk space.

## Persistence, backup, and password rotation

`data/questdb` persists the complete native data/configuration directory.
Disabling the extension retains it. For a cold backup, stop all writers, disable
the extension, copy this directory, then re-enable. Restore while stopped with
UID 10001 ownership. Keep a pre-upgrade backup: reverting an image does not
undo an on-disk format migration. Live snapshots and clustered recovery are
outside this recipe's qualification.

`QUESTDB_PASSWORD` configures both listeners at startup. Change it and recreate
the container to rotate access; existing data remains. Update clients together.
The value is not a first-boot user seed, and no default `admin/quest` account is
enabled by this recipe.

The opt-in test `ODS_TEST_QUESTDB=1 python -m pytest ods/tests/test_questdb.py -q`
uses the exact pinned image for HTTP/PG authentication, ingestion/query,
recreation, password rotation, and cold restore. Linux amd64 is qualified;
native macOS/Windows and ARM remain unverified.

References: [QuestDB HTTP configuration](https://questdb.com/docs/configuration/http-server/),
[Docker deployment](https://questdb.com/docs/deployment/docker/), and
[PostgreSQL clients](https://questdb.com/docs/query/overview/).
