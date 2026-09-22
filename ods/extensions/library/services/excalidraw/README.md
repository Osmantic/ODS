# Excalidraw

Sketch diagrams and ideas on a local browser-based whiteboard.

Install from ODS **Extensions**, configure the declared settings, then enable.

- Upstream: https://github.com/excalidraw/excalidraw
- Code license: MIT
- Local host port: `11010` (override with `EXCALIDRAW_PORT`).
- Readiness: `http://excalidraw:80/`.
- Image: `excalidraw/excalidraw:latest@sha256:f7ee194addd607bf831d2af0f0a34463dd4225e426cf35199ef0b12a803398e9`.
- Registry manifest and image configuration inspected on 2026-09-20; runtime validation is recorded in `upstream.json`.

Standalone local editor; drawings use browser storage or file export. This recipe does not provision a collaboration server or claim hosted collaboration features.

Disabling/removing the extension does not intentionally delete its named data volumes.
