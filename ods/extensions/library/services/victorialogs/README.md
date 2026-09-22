# VictoriaLogs

VictoriaLogs stores structured logs locally and provides LogsQL search through
an HTTP API and web UI. Install it from the Extensions library after setting a
strong `VLOGS_PASSWORD`. The fixed HTTP Basic Auth username is `ods`.

The default UI is `http://localhost:9428/select/vmui/`. `VLOGS_PORT` changes the
published port; connected containers use `http://victorialogs:9428` on
`ods-network`. Ingestion, queries and the UI require the configured password.
`/health` remains an unauthenticated availability probe without log contents.
ODS checks this HTTP endpoint using the manifest. The upstream image contains
only the service executable, so the recipe has no container health command;
Docker's `running` state alone does not attest HTTP readiness.

This optional service starts empty. Producers must be configured explicitly;
the recipe does not read Docker sockets, system journals or application files.
Use the bundled UI directly; a Grafana data source/plugin is a separate setup.

## Send and query a sample

These commands prompt for the password without putting it in shell history:

```bash
printf '%s\n' '{"_msg":"ODS log ingestion check","service":"sample","level":"info"}' |
  curl --fail-with-body --user ods -H 'Content-Type: application/stream+json' \
    --data-binary @- 'http://localhost:9428/insert/jsonline?_stream_fields=service'
curl --fail-with-body --user ods --data-urlencode 'query=service:sample' \
  'http://localhost:9428/select/logsql/query'
```

Without an explicit timestamp, newly ingested records use the current time.
New records may take a short time to become searchable. See the
[ingestion documentation](https://docs.victoriametrics.com/victorialogs/data-ingestion/)
for supported protocols and the [query documentation](https://docs.victoriametrics.com/victorialogs/querying/)
for LogsQL clients. Send only the records you intend to retain.

## Storage and lifecycle

Data is stored in `data/victorialogs`. Disable/re-enable or container recreation
keeps that directory. The default `VLOGS_RETENTION=7d` discards older records;
accepted duration suffixes in ODS settings are `d`, `w`, `M` and `y`, with a
positive integer. Retention is time based, not a hard disk quota. Size storage
for the actual ingestion rate. Shortening retention can permanently remove
records, and increasing it cannot recover previously expired data.

Apply password or retention changes by recreating the service. A changed
password applies to existing data immediately after recreation; update every
producer and client. For a cold backup, disable the service and copy the complete
data directory before re-enabling. Restore with the same pinned version first;
do not assume an arbitrary older release can read a newer storage format.

The recipe pins VictoriaLogs `1.52.0`, caps container memory at 1 GiB with a
512 MiB cache budget, and permits two concurrent searches with a 15-second
query limit. These defaults suit modest local ingestion; they are not a measured
throughput promise. Existing ODS containers, log retention and logging drivers
are unaffected. Linux container images are used across ODS platforms; native
platform installation and sustained fleet ingestion require separate validation.
