# Typesense

Local text and vector search API for document collections and AI applications.

Install from ODS **Extensions**, configure the declared settings, then enable.

- Upstream: https://github.com/typesense/typesense
- Code license: GPL-3.0
- Local host port: `11005` (override with `TYPESENSE_PORT`).
- Readiness: `http://typesense:8108/health`.
- Image: `typesense/typesense:30.2@sha256:610f2d34b1f93d00762869da2c67736775e5798d19a2c8b91b014b8a0cc1e110`.
- Registry manifest and image configuration inspected on 2026-09-20; runtime validation is recorded in `upstream.json`.

API-only extension: no misleading Open UI button. Set an API key in extension settings before enabling. Collections persist in the data volume.

Disabling/removing the extension does not intentionally delete its named data volumes.
