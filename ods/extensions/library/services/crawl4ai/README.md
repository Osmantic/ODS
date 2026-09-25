# Crawl4AI

Read websites and export clean Markdown or structured data with a local browser and API.

Install from ODS **Extensions**, configure the declared settings, then enable.

- Upstream: https://github.com/unclecode/crawl4ai
- Code license: Apache-2.0
- Local host port: `11002` (override with `CRAWL4AI_PORT`).
- Readiness: `http://crawl4ai:11235/health`.
- Image: `unclecode/crawl4ai:0.9.3@sha256:84751dab794259db05d5bd4e5c766a8041a65f0554326e4516e620abdf2fa18b`.
- Registry manifest and image configuration inspected on 2026-09-20; runtime validation is recorded in `upstream.json`.

Browser sessions and background jobs are ephemeral; callers should save exported results. Basic extraction does not require a paid LLM. Optional LLM extraction is configured per request.

Disabling/removing the extension does not intentionally delete its named data volumes.
