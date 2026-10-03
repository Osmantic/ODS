# Meilisearch Community

Local full-text and hybrid search engine with typo tolerance and an authenticated API.

Install from ODS **Extensions**, configure the declared settings, then enable.

- Upstream: https://github.com/meilisearch/meilisearch
- Code license: MIT (Community Edition)
- Host port: `11013` (override with `MEILISEARCH_PORT`).
- Readiness: `http://meilisearch:7700/health`.
- Image: `getmeili/meilisearch:v1.53.2@sha256:c94e58ca09662dd6e65e8f1b0fd145767be3da7d5422a863a27b8d2b68e090c9`.
- Registry manifest and configuration inspected on 2026-09-20. Runtime validation: pending.

Uses the Community image, not meilisearch-enterprise. Production mode deliberately disables the development mini-dashboard. No hosted embedding provider is preconfigured; configure a local embedder before enabling semantic search.

Disabling the extension preserves its named data volumes, when applicable.
