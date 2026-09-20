# Karakeep (AI Bookmarks)

A self-hosted bookmark hoarder: saves pages with a headless Chrome snapshot, indexes them in a private Meilisearch, and can auto-tag content through the **local llama-server** — no external AI key needed.

## Setup

Set `KARAKEEP_SECRET` and `MEILI_MASTER_KEY` in `.env`, enable **Karakeep**, then visit `http://localhost:3300`. With `KARAKEEP_USE_LOCAL_LLM=true` (default) inference is routed to `http://llama-server:8080/v1`.

## Settings

| Variable | Default | Behavior |
| --- | --- | --- |
| `KARAKEEP_PORT` | `3300` | Published HTTP port |
| `KARAKEEP_SECRET` | `—` | NextAuth secret for session signing (openssl rand -hex 32) |
| `MEILI_MASTER_KEY` | `—` | Meilisearch master key for the private search index |
| `KARAKEEP_USE_LOCAL_LLM` | `true` | Auto-tag with the local llama-server instead of an external API |
| `BIND_ADDRESS` | `127.0.0.1` | Shared ODS bind address; set `0.0.0.0` for LAN access |

## Notes

- Meilisearch and headless Chrome run on a private internal network with no published ports.
- Set `KARAKEEP_USE_LOCAL_LLM=false` to disable auto-tagging, or point `OPENAI_BASE_URL`/`INFERENCE_TEXT_MODEL` at another OpenAI-compatible endpoint in compose overrides.
- First startup downloads browser + search images (~1 GB).
