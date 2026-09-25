# rembg

Remove image backgrounds locally through the Gradio interface or HTTP API (x86_64 CPU image).

Install from ODS **Extensions**, configure the declared settings, then enable.

- Upstream: https://github.com/danielgatis/rembg
- Code license: MIT
- Local host port: `11001` (override with `REMBG_PORT`).
- Readiness: `http://rembg:7000/api`.
- Image: `danielgatis/rembg:latest@sha256:98e72b790093dec3b21967e22c8eb75a0a67d458fdba7ef5fcc1900cad76396b`.
- Registry manifest and image configuration inspected on 2026-09-20; runtime validation is recorded in `upstream.json`.

The upstream image currently supports amd64 only; ARM hosts require Docker x86 emulation. No CUDA acceleration is claimed. Model licenses vary; review the selected checkpoint before redistribution.

Disabling/removing the extension does not intentionally delete its named data volumes.
