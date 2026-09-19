# Phoenix — local trace lab

Phoenix 20.11.0 collects and displays OpenInference/OpenTelemetry traces from applications you explicitly instrument. Use it to inspect retrieval steps, latency, model names and token counts. It runs independently of Langfuse and does not automatically instrument ODS, copy existing traces, or send prompts to a model provider.

## Setup

Set two distinct strong values in ODS `.env` before installing **Phoenix (Local LLM Traces)** from Extensions:

- `PHOENIX_SECRET`: at least 32 characters including a lowercase letter and a digit. Retain this signing key with your database backup.
- `PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD`: the initial password for `admin@localhost`.

Compose refuses missing or empty values. Visit `http://localhost:8606`, sign in, and create a separate system API key in Phoenix for your trace producer. Store that key in the producer's credential store. Changing the initial-password variable does not reset an existing account; use Phoenix's account/password management.

For a producer on the ODS network, configure its OTLP HTTP exporter to `http://phoenix:6006/v1/traces` with `Authorization: Bearer <producer-key>`. A host-side producer uses `http://localhost:8606/v1/traces`. The container's gRPC and metrics ports are not published. This profile does not configure a cloud provider, automatic evaluations or an external exporter.

Traces can contain prompts, responses and retrieved documents. Configure the producer to omit sensitive content before exporting it. Phoenix authentication is enabled from first boot, browser telemetry and external resources are disabled, and the integrated assistant/MCP server are disabled. These application settings do not provide a network firewall. The default host binding is loopback; use a protected HTTPS deployment and configure Phoenix's secure-cookie/origin settings before remote access.

## Settings and storage

| Variable | Default | Meaning |
| --- | --- | --- |
| `PHOENIX_PORT` | `8606` | Published HTTP port; internal port stays `6006` |
| `PHOENIX_SECRET` | required | Signing key for authentication |
| `PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD` | required | First-boot admin password |
| `PHOENIX_RETENTION_DAYS` | `30` | Default trace retention; `0` is indefinite. Existing project policies are managed in Phoenix |

SQLite and application data persist in `data/phoenix`. The service uses up to 2 GB RAM and 2 CPUs, with no GPU allocation. Disable/recreate preserves accounts, keys and traces. Retention can delete old traces according to each project's policy; export records you need to keep.

For a consistent filesystem backup, stop `ods-phoenix`, copy the entire `data/phoenix` directory plus the private signing-key configuration, then start it again. Protect the backup as you would the trace content. Upgrades can migrate SQLite: keep a verified pre-upgrade backup, and restore that backup with the original image for rollback instead of opening a migrated database with an older version. Do not remove the data directory during recovery.

The image is pinned by version and digest. CPU operation is intended for the library's Docker profiles; the checked-in opt-in lifecycle test records the architecture actually exercised. Full ODS activation and platform-specific filesystem/remote-access behavior require validation on those hosts.

References: [self-hosting](https://arize.com/docs/phoenix/self-hosting/deploying-phoenix), [authentication](https://arize.com/docs/phoenix/deployment/authentication), [v20.11.0 configuration](https://github.com/Arize-ai/phoenix/blob/arize-phoenix-v20.11.0/src/phoenix/config.py).
