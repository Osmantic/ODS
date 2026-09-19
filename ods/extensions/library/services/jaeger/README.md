# Jaeger (Distributed Tracing)

Optional Jaeger **2.21.0** for tracing a request across local services. It accepts
authenticated **OTLP over HTTP** and provides the native Jaeger query UI, with
Badger storage retained for 48 hours. Producers must emit spans and propagate
trace context themselves; enabling the extension does not instrument ODS or
replace an LLM evaluation/monitoring system.

## Enable and authenticate

Generate one bcrypt htpasswd record with an Apache `htpasswd` installation:

```bash
htpasswd -nB -C 10 ods
```

Enter a strong password at the prompt, then copy the complete single line
(`ods:` followed by the bcrypt hash) into `JAEGER_HTPASSWD` in **Extensions**.
Do not paste the password itself or escape the hash's dollar signs in the form.
For a manually edited `.env`, enclose the entire record in single quotes to
preserve those dollar signs. Keep the password in your password manager; ODS
stores the hash, and cannot recover the original password from it.

Enable Jaeger, open `http://localhost:16686`, and use `ods` plus that password at
the browser authentication prompt. The query listener sends the Basic challenge
needed by browsers. Both UI/query and ingestion require the same native Basic
authentication; this recipe does not implement separate read/write roles or
per-tenant isolation.

| Setting | Default / meaning |
| --- | --- |
| `JAEGER_PORT` | `16686`, authenticated query UI/API host port |
| `JAEGER_OTLP_PORT` | `4318`, authenticated OTLP HTTP host port |
| `JAEGER_HTPASSWD` | Required bcrypt htpasswd record |
| Internal OTLP destination | `http://jaeger:4318/v1/traces` |

Configure a producer's OTLP HTTP exporter with that destination and an
`Authorization: Basic ...` header encoding `ods:password`. Keep the resulting
header secret; base64 is not encryption. Only traces are configured. OTLP gRPC,
legacy Jaeger receivers, and Zipkin intake are not enabled. A loopback-only
native query gRPC listener remains internal to the Jaeger container.

The two published ports default to loopback and honor `BIND_ADDRESS` and their
port overrides. The recipe does not provision TLS; put encrypted transport in
place before transmitting credentials or traces over a network. Trace attributes
can contain prompts, headers, or personal information: explicitly redact and
sample at the producer before export.

## Health and delivery semantics

Docker and dashboard health use `/status` on internal port 13133, which is not
host-published. It is unauthenticated to trusted Docker peers and reveals
component health. The configuration-dump endpoint is explicitly disabled.
Health is an availability signal, not proof that a particular trace was indexed.

The collector batches at most 128 spans with a one-second flush timeout.
An accepted OTLP response is not a promise of immediate query visibility or
protection for an in-memory batch if the process crashes. Badger writes use
synchronous consistency after reaching storage. Native validation exercises
actual cross-service parent/child spans and queries the resulting trace, then
verifies it after graceful recreation and cold restore. No crash-loss or HA
guarantee is claimed.

## Credentials, storage, and recovery

To rotate access, generate a replacement bcrypt record, update
`JAEGER_HTPASSWD`, and recreate Jaeger. Update producers and verify that the old
password fails on both query and ingestion. This is a startup configuration,
not a seed account stored in Badger. Browser credential caches may need to be
cleared after rotation. Existing traces remain available with the new credential.

The standard extension lifecycle prepares `data/jaeger` for UID 10001 and copies
`config/jaeger.yaml`; manual Compose users must prepare the same ownership and
config before starting. Both Badger keys and values live below that directory.
TTL expiration is not a strict total disk quota. The recipe limits the container
to 2 CPUs/2 GiB, has a soft Go memory target, and applies a collector memory
limiter; Badger and runtime allocations still need capacity headroom.

For the tested same-version cold recovery, stop Jaeger, copy the entire
`data/jaeger` directory and its ODS configuration to a private backup, and retain
the authentication record/password. Restore while stopped, preserve UID/GID
10001, start the same pinned image, and query a known trace ID before resuming
producers. Disabling retains the data. Never copy only keys or only values,
overwrite a live store, or delete the original during recovery.

Take a backup before upgrading. A rollback uses the old image and its matching
pre-upgrade data, not an older binary against an already upgraded store.
Linux amd64 qualification covers native config validation, HTTP authentication
including the browser challenge, OTLP JSON ingestion, Unicode attributes,
parent/child relationships, credential rotation, recreation, and cold restore.
Browser rendering, real instrumented ODS services, ARM hardware, sustained load,
cross-version restores, and long-duration TTL reclamation remain unqualified.
