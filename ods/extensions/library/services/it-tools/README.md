# IT Tools

A single-page toolbox of developer utilities — JSON/YAML formatters, base64, JWT decode, hash, regex tester — running entirely in the browser against a local static bundle.

## Setup

No secrets required. Enable **IT Tools** from Extensions and visit `http://localhost:8085`.

## Settings

| Variable | Default | Behavior |
| --- | --- | --- |
| `IT_TOOLS_PORT` | `8085` | Published HTTP port |
| `BIND_ADDRESS` | `127.0.0.1` | Shared ODS bind address; set `0.0.0.0` for LAN access |

## Notes

- All utilities run client-side; nothing leaves the browser. Safe for secrets-adjacent data like JWTs and keys.
