# BentoPDF

Merge, split and process PDFs in your browser with the self-hosted BentoPDF build.

Install from ODS **Extensions**, configure the declared settings, then enable.

- Upstream: https://github.com/alam00000/bentopdf
- Code license: AGPL-3.0
- Local host port: `11006` (override with `BENTOPDF_PORT`).
- Readiness: `http://bentopdf:8080/`.
- Image: `ghcr.io/alam00000/bentopdf-simple:2.8.8@sha256:3d62b8f8eece5fe947026ac3925ff08fda245b3d6ba2c3916b94da91e0010c74`.
- Registry manifest and image configuration inspected on 2026-09-20; runtime validation is recorded in `upstream.json`.

Uses the self-hosted simple image, not the commercial marketing build. Files are processed in the browser. Optional OCR/WASM assets may require a first download.

Disabling/removing the extension does not intentionally delete its named data volumes.
