# Paperless GPT

Generate document titles and tags in Paperless-ngx using the current ODS model, with local Docling OCR.

Install from ODS **Extensions**, configure the declared settings, then enable.

- Upstream: https://github.com/icereed/paperless-gpt
- Code license: MIT
- Host port: `11015` (override with `PAPERLESS_GPT_PORT`).
- Readiness: `http://paperless-gpt:8080/`.
- Image: `icereed/paperless-gpt:v0.28.0@sha256:413af73ff5415e1f61327ccb1c63cb14e84e86b761969be6eaaee61b4f39bef4`.
- Registry manifest and configuration inspected on 2026-09-20. Runtime validation: pending.

Requires a Paperless-ngx API token created in your existing Paperless account. Documents are processed through explicit paperless-gpt tags; install does not import or tag the entire archive. OCR uses Docling standard/EasyOCR, without a cloud OCR account. The web UI has no built-in login and stays loopback-bound by default.

Disabling the extension preserves its named data volumes, when applicable.
