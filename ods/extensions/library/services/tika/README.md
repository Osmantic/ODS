# Apache Tika

Extract text and metadata from documents through a local parsing API.

Install from ODS **Extensions**, configure the declared settings, then enable.

- Upstream: https://github.com/apache/tika
- Code license: Apache-2.0
- Host port: `11011` (override with `TIKA_PORT`).
- Readiness: `http://tika:9998/version`.
- Image: `apache/tika:latest@sha256:a8b442501f601fb15015de974f9afe13dd242b0f14e49a2b144fadcb214a555b`.
- Registry manifest and configuration inspected on 2026-09-20. Runtime validation: pending.

Uses the lightweight Tika 4.0.0 image. OCR executables are not included; use Docling Serve for scanned pages. Parsed output is returned to the caller and is not stored by this stateless service.

Disabling the extension preserves its named data volumes, when applicable.
