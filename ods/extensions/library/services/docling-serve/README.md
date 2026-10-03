# Docling Serve

Convert PDFs, Office files and images into structured documents with a local API and upload interface.

Install from ODS **Extensions**, configure the declared settings, then enable.

- Upstream: https://github.com/docling-project/docling-serve
- Code license: MIT
- Local host port: `11000` (override with `DOCLING_SERVE_PORT`).
- Readiness: `http://docling-serve:5001/health`.
- Image: `ghcr.io/docling-project/docling-serve-cpu:v1.34.0@sha256:0525640504db7ed8a53e0e443882727888f6a3746948b1b7a8820efe95567bd3`.
- Registry manifest and image configuration inspected on 2026-09-20; runtime validation is recorded in `upstream.json`.

CPU image. Model cache persists in a named volume. Each OCR/model checkpoint retains its upstream license.

Disabling/removing the extension does not intentionally delete its named data volumes.
