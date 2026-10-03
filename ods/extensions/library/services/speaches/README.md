# Speaches

OpenAI-compatible streaming transcription and speech API with on-demand local audio models.

Install from ODS **Extensions**, configure the declared settings, then enable.

- Upstream: https://github.com/speaches-ai/speaches
- Code license: MIT
- Host port: `11014` (override with `SPEACHES_PORT`).
- Readiness: `http://speaches:8000/health`.
- Image: `ghcr.io/speaches-ai/speaches:latest-cpu@sha256:21e3df06d842fb7802ab470dd77c25f0e8c0d22950e8d8c6ae886e851af53ef8`.
- Registry manifest and configuration inspected on 2026-09-20. Runtime validation: pending.

CPU edition. Models download on demand into the persisted cache. Voice/model licenses are separate from the server license; select an appropriate model. This is an audio API, not a chat-model provider. Other ODS services can call http://speaches:8000/v1.

Disabling the extension preserves its named data volumes, when applicable.
