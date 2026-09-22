# MLflow

Track local model experiments, metrics and artifacts through the MLflow interface and API.

Install from ODS **Extensions**, configure the declared settings, then enable.

- Upstream: https://github.com/mlflow/mlflow
- Code license: Apache-2.0
- Local host port: `11004` (override with `MLFLOW_PORT`).
- Readiness: `http://mlflow:5000/health`.
- Image: `ghcr.io/mlflow/mlflow:v3.16.1@sha256:6b6ec62130dd9a273b24e53e905bdc728b915fbd2dfb71f2fb7eff9a22f7fa67`.
- Registry manifest and image configuration inspected on 2026-09-20; runtime validation is recorded in `upstream.json`.

SQLite metadata and proxied artifacts persist together. Clients use the published tracking URL; no local artifact filesystem is exposed to clients.

Disabling/removing the extension does not intentionally delete its named data volumes.
