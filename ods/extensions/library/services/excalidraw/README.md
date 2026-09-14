# Excalidraw Extension

Self-hosted, browser-based virtual whiteboard for sketching hand-drawn style diagrams, flowcharts, wireframes, and architecture diagrams locally with zero cloud telemetry.

## Quick Start

```bash
docker compose -f compose.yaml up -d
```

Visit `http://localhost:7857` in your browser.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `EXCALIDRAW_PORT` | `7857` | Published host port for the web canvas UI |

## Integration with ODS

Excalidraw connects to `ods-network` as an optional extension service. Sketches and whiteboard state are maintained in-browser in local storage and can be saved or exported locally as SVG, PNG, or `.excalidraw` JSON files.
