# ChromaDB

AI-native open-source vector database for building embeddings-based applications. Store and query vector embeddings with metadata filtering via a simple REST API.

## Requirements

- **GPU:** NVIDIA, AMD, or Apple Silicon
- **Dependencies:** None

## Enable / Disable

```bash
dream enable chromadb
dream disable chromadb
```

Your data is preserved when disabling. To re-enable later: `dream enable chromadb`

## Access

- **URL:** `http://localhost:8000`

## First-Time Setup

1. Enable the service: `dream enable chromadb`
2. Use the REST API at `http://localhost:8000` to create collections and add embeddings

### API Examples

```bash
# Health check
curl http://localhost:8000/api/v2/heartbeat

# Create a collection
curl -X POST http://localhost:8000/api/v2/collections \
  -H "Content-Type: application/json" \
  -d '{"name": "my_collection"}'
```
