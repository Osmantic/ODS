# TEI (Embeddings)

Text-to-vector embedding service for RAG and semantic search in ODS

## Overview

The embeddings service runs Hugging Face's Text Embeddings Inference (TEI) server, which converts text into dense vector representations. These vectors are stored in Qdrant and used by RAG pipelines to retrieve relevant context before sending queries to the LLM.

## Features

- **High-performance inference**: Optimized TEI server with batching and caching for low-latency embedding generation
- **OpenAI-compatible API**: Drop-in replacement for OpenAI's embeddings endpoint
- **Configurable model**: Switch embedding models via a single environment variable
- **Persistent model cache**: Downloaded models are stored locally and survive restarts
- **CPU-only by default**: Runs on CPU with optional GPU acceleration on NVIDIA/AMD

## Configuration

Environment variables (set in `.env`):

| Variable | Default | Description |
|----------|---------|-------------|
| `EMBEDDINGS_PORT` | 8090 | External port for the embeddings API |
| `EMBEDDING_MODEL` | `BAAI/bge-base-en-v1.5` | Hugging Face model ID to load |

> **Changing the model:** Set `EMBEDDING_MODEL` in `.env` to any compatible sentence-transformer model from Hugging Face. The model will be downloaded automatically on first start and cached in `./data/embeddings`.

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
| Memory limit | 4 GB |
| CPU reservation | 0.5 cores |
| Memory reservation | 1 GB |

## Files

- `manifest.yaml` — Service metadata (port, health endpoint, GPU backends)
- `compose.yaml` — Container definition (image, environment, resource limits)

## Troubleshooting

**Embeddings service not ready (health check failing):**

The service downloads the model on first start, which can take several minutes depending on model size. Wait for the start period (60s) to elapse, then check logs:
```bash
docker compose logs ods-embeddings --follow
```

**Out of memory errors:**
- The default `BAAI/bge-base-en-v1.5` model requires ~1 GB RAM
- Larger models (e.g. `BAAI/bge-large-en-v1.5`) require more memory; increase the memory limit in `compose.yaml` if needed

**Connection refused on port 8090:**
```bash
docker compose ps ods-embeddings
docker compose logs ods-embeddings
```

**Changing models:**
```bash
# Update .env
EMBEDDING_MODEL=BAAI/bge-large-en-v1.5

# Restart the service
docker compose restart embeddings
```
