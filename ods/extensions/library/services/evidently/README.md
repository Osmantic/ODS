# Evidently

Track data drift and model-quality evaluation reports in a local monitoring dashboard.

Install from ODS **Extensions**, configure the declared settings, then enable.

- Upstream: https://github.com/evidentlyai/evidently
- Code license: Apache-2.0
- Host port: `11016` (override with `EVIDENTLY_PORT`).
- Readiness: `http://evidently:8000/`.
- Image: `evidently/evidently-service:latest@sha256:5b38690426408061068eb5391e7c1fd3cff760a4f44e950c84977a69c271f385`.
- Registry manifest and configuration inspected on 2026-09-20. Runtime validation: pending.

Open-source self-hosted monitoring UI. Send reports from the Evidently Python SDK to this service; cloud accounts and paid cloud dataset features are not required. Configure the same write secret in RemoteWorkspace clients. No hosted LLM judge is configured automatically.

Disabling the extension preserves its named data volumes, when applicable.
