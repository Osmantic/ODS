# Temporal Local — durable workflow development

Temporal CLI 1.9.1, MIT, official image pinned by digest. Upstream: https://github.com/temporalio/cli/tree/v1.9.1. This recipe uses the CLI's embedded local development server with persistent SQLite, not a separately configured production cluster. See https://docs.temporal.io/cli/command-reference/server.

## Connect a real project

Open the native UI at `http://localhost:11111/`. A Temporal SDK client/worker running on the host connects to `localhost:11112`, namespace `default`. A project container on `ods-network` uses `temporal:7233`. Choose a project-specific task queue and start the project's actual worker code before submitting workflows. Installing this extension does not create a worker, define a workflow or start jobs automatically.

Temporal records workflow history so compatible workers can resume/replay it after interruption. Application code must follow Temporal's deterministic workflow rules, place external side effects in activities, and choose appropriate retries/timeouts/idempotency. It does not turn arbitrary Python or model calls into automatically durable jobs. No AI model/provider, Portal conversation, schedule or remote endpoint is connected by this recipe.

## Persistence and boundaries

`temporal-data` persists `/home/temporal/data/temporal.db` and its SQLite sidecar files. Back up that directory consistently while stopped and preserve UID/GID 1000 ownership. Do not copy only the main database file during active writes. Retain compatible worker code and workflow versions as well as history. Removing the extension does not authorize deleting executions.

The default namespace is created by the native development server. Namespace history retention still applies; persistence is not an indefinite audit archive. This single local instance has no replicated availability. A production deployment needs Temporal's supported server/database/authentication architecture and a planned migration; a Docker image tag change does not convert this SQLite deployment into production.

No authentication/TLS is supplied by `start-dev`. Both host listeners are loopback-only, but ODS network peers can inspect/control workflows, and the upstream development server relaxes some HTTP security checks. Use it for local project development, not sensitive shared tenancy or public exposure. The Web UI news fetch is disabled. No cloud account is required.

The container retains the upstream nonroot user, uses a read-only root, persistent data and bounded `/tmp`, with two CPUs/two GiB RAM. Go memory target is 1536 MiB; this is not a hard per-workflow memory guarantee. The native gRPC health command verifies server serving status; the UI availability probe alone does not verify worker availability or workflow success.

## Platforms and validation

Official Linux amd64/arm64 image targets Docker on Windows/Linux/macOS. No GPU or selected-model dependency. Image build, real SDK worker execution, replay after restart, SQLite restore and platform runtime remain pending. No server, workflow, worker or model was started during preparation.
