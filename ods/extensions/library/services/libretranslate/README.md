# LibreTranslate

Translate text locally with an open-source web interface and API.

Install from ODS **Extensions**, configure the declared settings, then enable.

- Upstream: https://github.com/LibreTranslate/LibreTranslate
- Code license: AGPL-3.0
- Local host port: `11008` (override with `LIBRETRANSLATE_PORT`).
- Readiness: `http://libretranslate:5000/health`.
- Image: `libretranslate/libretranslate:v1.9.6@sha256:1de2d7056bb8ad607a412f4563d9abe324ff632b43b5be9428bcc8e213aebb32`.
- Registry manifest and image configuration inspected on 2026-09-20; runtime validation is recorded in `upstream.json`.

Downloads the selected Argos language models on first start; afterwards translation runs locally. The default language set is English, Portuguese and Spanish.

Disabling/removing the extension does not intentionally delete its named data volumes.
