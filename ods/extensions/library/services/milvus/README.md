# Milvus

Production-grade open-source vector database built for scalable similarity search. Supports billion-scale vector data with high performance, hybrid search, and multiple index types.

## Requirements

- **GPU:** CPU only — no GPU required
- **Dependencies:** None

## Enable / Disable

```bash
ods enable milvus
ods disable milvus
```

Your data is preserved when disabling. To re-enable later: `ods enable milvus`

## Metadata persistence and existing installations

New installations store embedded etcd metadata in `/var/lib/milvus/etcd`,
inside the same `./data/milvus` bind mount as vector data and the message queue.
This follows the [Milvus v2.4.5 standalone launcher](https://github.com/milvus-io/milvus/blob/v2.4.5/scripts/standalone_embed.sh).
The previous ODS configuration left etcd at `default.etcd`, outside that mount;
recreating that container could lose collection metadata even with vector files intact.

**Existing installations require migration before applying this compose change.**
Stop writes and stop the existing container before copying its etcd directory.
Locate its effective `etcd.data.dir` (the old default is relative to the container
working directory), preserve a separate copy, and copy the complete directory
into `./data/milvus/etcd` with its ownership and permissions intact. Do not merge
it into an already populated etcd directory. Keep the old stopped container
and a full copy of `./data/milvus` until recovery is verified.

Before production rollout, verify collection names, row counts, and a query
after starting the migrated instance and again after a container recreation.
The repository persistence test checks configuration containment only; it does
not prove this data migration. If validation fails, stop the new instance and
restore the saved metadata/vector-data pair with the original configuration.
Do not simply remove `ETCD_DATA_DIR` after the new instance has accepted writes.

## Access

- **URL:** `localhost:19530` (gRPC)

## First-Time Setup

1. Enable the service: `ods enable milvus`
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
