# Milvus

Production-grade open-source vector database built for scalable similarity search. Supports billion-scale vector data with high performance, hybrid search, and multiple index types.

## Requirements

- **GPU:** CPU only — no GPU required
- **Dependencies:** None

## Enable / Disable

```bash
dream enable milvus
dream disable milvus
```

Your data is preserved when disabling. To re-enable later: `dream enable milvus`

## Access

- **URL:** `localhost:19530` (gRPC)

## First-Time Setup

1. Enable the service: `dream enable milvus`
2. Connect using any Milvus SDK on port 19530

### Python Quick Start

```python
from pymilvus import connections, Collection

connections.connect(host="localhost", port="19530")
```

### REST API

```bash
# Create collection
curl -X POST http://localhost:19530/v2/vectordb/collections/create \
  -H "Content-Type: application/json" \
  -d '{"collectionName": "my_collection", "dimension": 768}'
```
