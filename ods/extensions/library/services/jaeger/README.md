# Jaeger — local application traces

Jaeger 2.21.0, Apache-2.0, official image pinned by multiarchitecture digest. Configuration follows the upstream 2.21.0 Badger example: https://github.com/jaegertracing/jaeger/blob/v2.21.0/cmd/jaeger/config-badger.yaml.

Use the native search/timeline UI at `http://localhost:11102/` to inspect spans emitted by deliberately instrumented projects. This helps diagnose application latency and errors, including AI pipelines using OpenTelemetry. It does not record a model's hidden reasoning, automatically intercept Portal conversations or provide a model evaluator by itself.

## Instrument a project explicitly

For a host application with an OpenTelemetry SDK, configure the HTTP/protobuf exporter endpoint as `http://localhost:11104` (traces are posted to `/v1/traces`), or OTLP gRPC as `localhost:11103`. Follow the selected SDK's endpoint convention; an endpoint variable specifically for traces may need the full `/v1/traces` path. Set a meaningful `service.name` and deliberate sampling policy.

For projects on `ods-network`, use `http://jaeger:4318` or `jaeger:4317`. No SDK, auto-instrumentation agent or project configuration is installed automatically. Inspect which attributes the instrumentation exports, especially prompts, documents and credentials. No remote collector, cloud account, notifications or external model is configured. The UI remains empty until real spans arrive.

## Storage and scope

The named `jaeger-data` volume contains native Badger keys/values under `/data`. Spans expire after 48 hours; this TTL is not a hard disk quota. There is no archive backend or archive retention promise. Back up the volume consistently while stopped and preserve UID/GID 10001 ownership on restore. Do not open the same Badger directory from concurrent replicas; this is a single-instance local deployment.

UI and both ingestion ports are loopback-only on the host. There is no application login in this recipe; containers on the shared ODS network can query/send traces. Deliberately configure authentication/TLS before exposing beyond that scope. Resources: two CPUs, two GiB memory and bounded temporary storage, with batched export. Sustained/high-cardinality ingestion needs appropriate sampling, disk monitoring and resource sizing.

## Compatibility and validation

Official Linux amd64/arm64 image runs through Docker Engine/Desktop on Windows/Linux/macOS. No GPU or model dependency. Original nonroot binary entrypoint is retained; filesystem is read-only except the persistent data and temporary paths. A small static HTTP probe checks Jaeger's native component health endpoint because the upstream runtime has no shell/curl assumption. It does not fabricate traces.

Source/configuration and packaging checks are distinct from runtime evidence. Image build, actual OTLP ingestion, UI queries, TTL/restore and platform behavior remain pending. No service/model was started during preparation.
