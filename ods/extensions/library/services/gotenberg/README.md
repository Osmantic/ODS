# Gotenberg

Convert HTML and office documents to PDF through a local Chromium and LibreOffice API.

Install from ODS **Extensions**, configure the declared settings, then enable.

- Upstream: https://github.com/gotenberg/gotenberg
- Code license: MIT
- Host port: `11017` (override with `GOTENBERG_PORT`).
- Readiness: `http://gotenberg:3000/health`.
- Image: `gotenberg/gotenberg:8@sha256:f29984bd1e226bf1b93ba90af06000afa8b315853e99d27b9aaa41b93f15c769`.
- Registry manifest and configuration inspected on 2026-09-20. Runtime validation: pending.

Stateless document conversion API, useful for exported AI reports. Submit multipart requests to /forms/chromium/convert/html or /forms/libreoffice/convert. Converted documents are returned to the client. Remote URL conversion accesses the URLs submitted by the caller.

Disabling the extension preserves its named data volumes, when applicable.
