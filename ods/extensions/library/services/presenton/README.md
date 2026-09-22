# Presenton

Generate and edit presentations using the current ODS model through the local gateway.

Install from ODS **Extensions**, configure the declared settings, then enable.

- Upstream: https://github.com/presenton/presenton
- Code license: Apache-2.0
- Local host port: `11007` (override with `PRESENTON_PORT`).
- Readiness: `http://presenton:80/`.
- Image: `ghcr.io/presenton/presenton:latest@sha256:ff59562c2e7683f64f892eb798e32bfcdedd05a94f05c2302ececed94119c77a`.
- Registry manifest and image configuration inspected on 2026-09-20; runtime validation is recorded in `upstream.json`.

Create the first administrator in the local UI. Image generation is disabled by default, so no stock-image or paid image key is required. The active ODS model must support the structured output required by Presenton.

Disabling/removing the extension does not intentionally delete its named data volumes.

LLM requests use `http://litellm:4000/v1` and `ods/current`, following the ODS swap-safe contract. No concrete model filename is persisted by this recipe.
