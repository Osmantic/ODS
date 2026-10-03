# draw.io

Create and edit diagrams, architecture drawings and flowcharts locally.

Install from ODS **Extensions**, configure the declared settings, then enable.

- Upstream: https://github.com/jgraph/drawio
- Code license: Apache-2.0
- Local host port: `11009` (override with `DRAWIO_PORT`).
- Readiness: `http://drawio:8080/`.
- Image: `jgraph/drawio:31.4.6@sha256:4bd19d4bc36b65cabf1ae9c5d234597e564416b6c67f7d4db296c2df758fcb52`.
- Registry manifest and image configuration inspected on 2026-09-20; runtime validation is recorded in `upstream.json`.

Diagrams are saved through the browser to files or user-selected storage. No LLM is required; cloud storage integrations are optional.

Disabling/removing the extension does not intentionally delete its named data volumes.
