# Promptfoo

Compare prompts and models, inspect evaluation results and run local AI quality checks.

Install from ODS **Extensions**, configure the declared settings, then enable.

- Upstream: https://github.com/promptfoo/promptfoo
- Code license: MIT
- Local host port: `11003` (override with `PROMPTFOO_PORT`).
- Readiness: `http://promptfoo:3000/health`.
- Image: `ghcr.io/promptfoo/promptfoo:0.123.1@sha256:2dfddde000886e9a0bcce799478095a2e7d1e4438a6c6669ac04001e8ae29b85`.
- Registry manifest and image configuration inspected on 2026-09-20; runtime validation is recorded in `upstream.json`.

The included evaluation example routes to ods/current. Custom evaluation files can intentionally select other providers; keep them local when privacy is required.

To run the example, copy `promptfooconfig.yaml` from this directory into the running container with `docker cp promptfooconfig.yaml ods-promptfoo:/tmp/ods-eval.yaml`, then run `docker exec ods-promptfoo promptfoo eval -c /tmp/ods-eval.yaml` after a local model is ready. Results are stored in the same persisted workspace and can be opened in the UI. ODS does not run evaluations automatically when installing this extension.

Disabling/removing the extension does not intentionally delete its named data volumes.

LLM requests use `http://litellm:4000/v1` and `ods/current`, following the ODS swap-safe contract. No concrete model filename is persisted by this recipe.
