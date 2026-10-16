# M1 Offline Mode — Fully Air-Gapped Operation

*ODS can keep running after you disconnect it from the network.*

## Overview

`--offline` prepares an install for air-gapped operation. The installation
itself still needs the network: it downloads container images, the model and
an embedding model. Once it finishes, chat, voice and local documents keep
working with the machine disconnected.

`--offline` is an option of the Linux installer (`install-core.sh`, which the
Windows WSL path also uses). The macOS installer does not have it.

### What `--offline` changes

- Skips the installer's network reachability check and the small bootstrap
  model, so the full model downloads before launch.
- Clears `BRAVE_API_KEY`, `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` in `.env`.
- Sets `DISABLE_UPDATE_CHECK=true`, which stops the dashboard's GitHub release
  check.
- Downloads `nomic-embed-text-v1.5.Q4_K_M.gguf` and writes the `.offline-mode`
  marker only after that file validates.

### What it does not change

- It does not block outbound network access. Air-gapped operation means
  disconnecting the machine.
- Portal's web search keeps its configured provider and works whenever the
  machine is online. Fresh installs use the keyless Parallel API; set
  `PIXEL_WEB_SEARCH_PROVIDER=searxng` before installing to search through the
  bundled SearXNG instead.
- `OFFLINE_MODE`, `WEB_SEARCH_ENABLED`, `LOCAL_RAG_ENABLED` and
  `DISABLE_TELEMETRY` are written to `.env`, but no service reads them yet.

Bundled services ship with their own usage telemetry and update checks turned
off in every install, not only offline ones: Open WebUI, Qdrant, LiteLLM's
cost-map fetch, n8n and the Whisper Hugging Face client.

## Installation

```bash
# Standard offline install
./install.sh --offline --all

# Offline with specific tier
./install.sh --offline --tier 2 --voice --rag
```

## What Works Offline

| Component | Status | Notes |
|-----------|--------|-------|
| llama-server (local LLM) | ✅ | All inference local |
| Open WebUI | ✅ | Local web interface |
| Whisper STT | ✅ | `--voice` flag; cache the model before disconnecting (below) |
| Kokoro TTS | ✅ | `--voice` flag |
| Qdrant (RAG) | ✅ | `--rag` flag |
| Portal (Pixel) | ⚠️ | Default agent on qualified hosts; chat works, web search and fetch need the network |
| Hermes Agent | ✅ | On with Full Stack, `--all` or `--hermes`; local LLM only |
| n8n workflows | ⚠️ | Local execution, but many integrations need internet |

## Post-Installation

### Verify Offline Operation

```bash
# Check services are running
ods status

# Test LLM (should work offline)
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "local", "messages": [{"role": "user", "content": "Hello"}]}'

# Test local embeddings/RAG if enabled.
```

### Full Air-Gap Procedure

1. Complete installation with `--offline` flag
2. If voice is enabled, run `ods stt download` to cache the Whisper model
3. Verify all services running: `ods status`
4. Test core functionality while online
5. Disconnect network (unplug ethernet / disable WiFi)
6. Verify services still work

### Reconnecting (Optional)

Images and models are pinned, so pulling them again does not update anything.
To move to a newer ODS, reconnect, follow
[How do I update ODS?](../FAQ.md#how-do-i-update-ods), then disconnect.

## Pre-Downloaded Models

An offline install downloads, while it is still online:
- **LLM** — Based on your tier selection
- **GGUF embeddings** — `nomic-embed-text-v1.5.Q4_K_M.gguf` (~300MB)
- **Whisper** — If `--voice` is enabled, a later installer phase downloads it;
  run `ods stt download` before disconnecting to be sure it is cached
- **Kokoro TTS image/data** — If `--voice` is enabled

## Replacing Web Search

Web search needs the internet. Offline, use local documents instead.

### Pre-Load a Knowledge Base

```bash
# Index local documents into Qdrant
curl -X POST http://localhost:6333/collections/knowledge/points \
  -H "Content-Type: application/json" \
  -d '{...your documents...}'
```

## Troubleshooting

### Memory Search Not Working

Check GGUF embeddings downloaded:
```bash
ls -la models/embeddings/
# Should see: nomic-embed-text-v1.5.Q4_K_M.gguf
```

### Services Won't Start Without Internet

All images should be pulled during installation. If one is missing, pull the
saved stack again while connected, from the install directory (ODS does not
use a top-level `docker-compose.yml`):
```bash
cd ~/ods
docker compose $(cat .compose-flags) pull

# Then disconnect
```

### n8n Workflows Failing

Many n8n integrations require internet (Gmail, Slack, etc.).
Use only local-compatible workflows:
- File operations
- Local API calls
- Database operations
- Webhook receivers (internal)

## Security Benefits

Air-gapped operation provides:
- **Data sovereignty** — Nothing leaves your network
- **Compliance** — Suitable for regulated environments
- **Privacy** — No usage tracking possible
- **Resilience** — Works during internet outages

## Files Created

```
ods/
├── .offline-mode              # Marker file
├── .env                       # Updated with offline settings
└── models/
    └── embeddings/
        └── nomic-embed-text-v1.5.Q4_K_M.gguf
```

---

*Part of M5: Clonable ODS Setup Server*
