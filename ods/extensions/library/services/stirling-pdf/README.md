# Stirling PDF

A local PDF workbench — merge, split, compress, convert, sign, and OCR without uploading documents anywhere.

## Setup

No secrets required. Enable **Stirling PDF** from Extensions and visit `http://localhost:8920`.

## Settings

| Variable | Default | Behavior |
| --- | --- | --- |
| `STIRLING_PDF_PORT` | `8920` | Published HTTP port |
| `BIND_ADDRESS` | `127.0.0.1` | Shared ODS bind address; set `0.0.0.0` for LAN access |

## Notes

- `SYSTEM_CONNECTIONMODE=BROWSER_ONLY` keeps the UI local-first; no telemetry endpoint is contacted.
- OCR language packs persist in `data/stirling-pdf/training`.
