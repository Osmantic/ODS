# TEI (Embeddings)

Text-to-vector embedding service for RAG and semantic search in ODS

## Overview

The embeddings service runs Hugging Face's Text Embeddings Inference (TEI) server, which converts text into dense vector representations. These vectors are stored in Qdrant and used by RAG pipelines to retrieve relevant context before sending queries to the LLM.

## Features

- **High-performance inference**: Optimized TEI server with batching and caching for low-latency embedding generation
- **OpenAI-compatible API**: Drop-in replacement for OpenAI's embeddings endpoint
- **Configurable model**: Switch embedding models via a single environment variable
- **Persistent model cache**: Downloaded models are stored locally and survive restarts
- **CPU-packaged runtime**: The shipped Compose service uses the pinned TEI CPU image across supported hosts

## Configuration

Environment variables (set in `.env`):

| Variable | Default | Description |
|----------|---------|-------------|
| `EMBEDDINGS_PORT` | 8090 | External port for the embeddings API |
| `EMBEDDING_MODEL` | `BAAI/bge-base-en-v1.5` | Hugging Face model ID to load |
| `EMBEDDINGS_MEMORY_LIMIT` | `4G` | Container memory limit; larger models may need `6G` or more |
| `RAG_EMBEDDING_MODEL` | empty | Optional Open WebUI-only override for an external embeddings provider |
| `RAG_OPENAI_API_BASE_URL` | bundled TEI | OpenAI-compatible endpoint used by Open WebUI RAG |
| `RAG_OPENAI_API_KEY` | empty | Optional credential for an authenticated external embeddings provider |

> **Changing the model:** Set `EMBEDDING_MODEL` in `.env` to a TEI-compatible Hugging Face repository ID. Open WebUI uses the same value on first boot when it uses bundled TEI; existing installs retain their Admin Panel value until it is updated there. The model is downloaded on first start and cached in `./data/embeddings`.

The bundled service is Hugging Face Text Embeddings Inference, not llama.cpp.
GGUF files and GGUF/Q4 repositories cannot be used as `EMBEDDING_MODEL`.
For example, use `BAAI/bge-m3`, not a BGE-M3 GGUF quantization. Operators
with another OpenAI-compatible embeddings server may set
`RAG_OPENAI_API_BASE_URL`, `RAG_EMBEDDING_MODEL`, and, when required,
`RAG_OPENAI_API_KEY`; those explicit overrides do not change the bundled TEI
model. External endpoint URLs must include a valid HTTP(S) host. Keep
credentials in `RAG_OPENAI_API_KEY`, rather than embedding them in the URL.
Dashboard Settings exposes an explicit **Clear stored secret** action for this
optional credential; leaving the masked input blank keeps its current value.

When the embeddings extension is disabled, Dashboard Settings saves model and
memory changes without trying to start it. The pending configuration is applied
the next time the extension is enabled. Required Open WebUI synchronization and
reindex steps remain visible in that browser until they are marked complete.
Saving and runtime application are separate operations: the `.env` change and
its timestamped backup remain durable if a container recreation fails, while
the pending apply plan stays available for retry.

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /health` | GET | Health check (returns 200 when ready) |
| `POST /embed` | POST | Generate embeddings for a list of texts |
| `GET /info` | GET | Model info (name, max sequence length, embedding dimension) |
| `GET /metrics` | GET | Prometheus metrics |

### Example Usage

```bash
# Check service health
curl http://localhost:8090/health

# Generate embeddings
curl http://localhost:8090/embed \
  -H "Content-Type: application/json" \
  -d '{"inputs": ["Hello world", "ODS is great"]}'

# Get model information
curl http://localhost:8090/info
```

## Architecture

```
┌──────────────┐    POST /embed     ┌──────────────┐
│  Your App /  │───────────────────▶│  Embeddings  │
│  RAG Pipeline│◀───────────────────│  (TEI Server)│
└──────────────┘  float32 vectors   └──────┬───────┘
                                           │
                                    ┌──────────────────┐
                                    │./data/embeddings │
                                    │  (model cache)   │
                                    └──────────────────┘
```

The embeddings service is typically paired with Qdrant: text goes in → vectors come out → vectors are stored in Qdrant for retrieval.

## Resource Limits

The container enforces CPU and memory limits to prevent resource starvation:

| Limit | Value |
|-------|-------|
| CPU limit | 2 cores |
| Memory limit | 4 GB by default (`EMBEDDINGS_MEMORY_LIMIT`) |
| CPU reservation | 0.5 cores |
| Memory reservation | 1 GB |

## Files

- `manifest.yaml` — Service metadata (port, health endpoint, GPU backends)
- `compose.yaml` — Container definition (image, environment, resource limits)

## Troubleshooting

**Embeddings service not ready (health check failing):**

The service downloads the model on first start, which can take several minutes depending on model size. The healthcheck allows a 600-second start period; check progress with:
```bash
docker compose logs embeddings --follow
```

**Out of memory errors:**
- The default `BAAI/bge-base-en-v1.5` model requires ~1 GB RAM
- Larger models (for example `BAAI/bge-m3`) require more memory; set `EMBEDDINGS_MEMORY_LIMIT=6G` or higher in `.env` instead of editing Compose

**Connection refused on port 8090:**
```bash
docker compose ps embeddings
docker compose logs embeddings
```

**Changing models:**
1. Edit `.env` and change the canonical model:

   ```env
   EMBEDDING_MODEL=BAAI/bge-m3
   # Larger models may require this:
   EMBEDDINGS_MEMORY_LIMIT=6G
   ```

2. Recreate the embeddings and Open WebUI containers so the new environment
   reaches both consumers:

   ```bash
   # Linux / macOS
   ods restart embeddings
   ods restart open-webui
   ```

   ```powershell
   # Windows, from $env:USERPROFILE\ods (or the custom install directory)
   .\ods.ps1 restart embeddings
   .\ods.ps1 restart open-webui
   ```

3. In Open WebUI, open **Admin Panel / Settings / Documents**, set the
   embedding engine, endpoint, and model to the values above, then run
   **Reindex**. Open WebUI persists these settings in its database after first
   boot, so recreating its container does not override an existing Admin Panel
   value. Embeddings from different models are not in the same vector space.
   Existing knowledge bases must be re-embedded, and files attached directly
   to old chats must be uploaded again.

Do not use `docker compose restart` for this change: Docker restart preserves
the old container environment. The ODS lifecycle commands recreate the
affected containers.
