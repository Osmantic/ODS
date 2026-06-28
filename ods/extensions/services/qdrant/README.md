# Qdrant

Vector database for semantic search and RAG in ODS

## Overview

Qdrant is a high-performance vector database that stores and searches embeddings for Retrieval-Augmented Generation (RAG) workflows. It enables semantic similarity search across your local documents, letting LLMs retrieve relevant context before answering questions.

## Features

- **Vector storage**: Persist high-dimensional embeddings with HNSW indexing for fast nearest-neighbor search
- **Collection management**: Organize vectors into named collections with configurable distance metrics (cosine, dot product, Euclidean)
- **Filtered search**: Combine semantic search with structured metadata filters
- **Snapshots**: Create and restore point-in-time backups of your collections
- **REST + gRPC API**: Full API access over HTTP and gRPC for high-throughput clients
- **Persistence**: All data survives container restarts via bind-mounted storage

## Configuration

Environment variables (set in `.env`):

| Variable | Default | Description |
|----------|---------|-------------|
| `QDRANT_PORT` | 6333 | External HTTP REST API port |
| `QDRANT_GRPC_PORT` | 6334 | External gRPC API port |

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /` | GET | Health check / root info |
| `GET /collections` | GET | List all collections |
| `PUT /collections/{name}` | PUT | Create a collection |
| `DELETE /collections/{name}` | DELETE | Delete a collection |
| `PUT /collections/{name}/points` | PUT | Insert/update vectors |
| `POST /collections/{name}/points/search` | POST | Similarity search |
| `GET /collections/{name}/points/{id}` | GET | Get point by ID |
| `GET /dashboard` | GET | Built-in web dashboard |

Full API docs are available at `http://localhost:6333/dashboard` when the service is running.

## Architecture

```
┌──────────────┐     REST :6333    ┌──────────────┐
│  Open-WebUI  │──────────────────▶│    Qdrant    │
│  Perplexica  │   gRPC  :6334    │  (Vector DB) │
│  Your App    │──────────────────▶│              │
└──────────────┘                   └──────┬───────┘
                                          │
                                   ┌──────▼───────┐
                                   │ ./data/qdrant│
                                   │  (storage)   │
                                   └──────────────┘
```

Qdrant works alongside the **embeddings** service: the embeddings service converts text to vectors, and Qdrant stores and retrieves them.

## Files

- `manifest.yaml` — Service metadata (port, health endpoint, GPU backends)
- `compose.yaml` — Container definition (image, volumes, ports, healthcheck)

## Troubleshooting

**Qdrant not starting:**
```bash
docker compose ps ods-qdrant
docker compose logs ods-qdrant
```

**Cannot connect on port 6333:**
- Check `QDRANT_PORT` in `.env` is not in use by another service
- Verify the container is healthy: `docker compose ps ods-qdrant`

**Data lost after restart:**
- Ensure `./data/qdrant` directory exists and has correct permissions
- Check volume mount in compose.yaml

**Collection errors:**
```bash
# List collections via REST API
curl http://localhost:6333/collections

# Check Qdrant version
curl http://localhost:6333/
```
