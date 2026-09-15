# Typesense — In-Memory Typo-Tolerant Search Engine

An optional, fast in-memory search engine written in C++. Typesense is built from the ground up for developer simplicity and predictable sub-50ms search latency, providing typo tolerance, multi-field filtering, facet aggregation, and native vector search for local AI applications.

## Setup

Set configuration parameters in your ODS `.env` file before enabling **Typesense (In-Memory Search)**:

- `TYPESENSE_API_KEY`: master authentication key for indexing and administrative operations (minimum 8 characters).
- `TYPESENSE_PORT`: published HTTP API port (defaults to `8108`).

If absent, running `setup.sh` automatically provisions a secure random 32-character API key into `.env`. Compose refuses missing or empty API keys.

Once enabled:
- **Search API Endpoint**: `http://localhost:8108`
- **Internal Network Endpoint**: `http://typesense:8108` (for services on `ods-network`)

## Settings

| Variable | Default | Description |
| --- | --- | --- |
| `TYPESENSE_PORT` | `8108` | Published HTTP API port on the host |
| `TYPESENSE_API_KEY` | required | Master API key required in the `X-TYPESENSE-API-KEY` request header |

The default binding is loopback (`127.0.0.1`). Container privilege escalation is restricted via `no-new-privileges:true`.

## Usage & Client Integration

### Python (`typesense`)

```python
import typesense

client = typesense.Client({
    "nodes": [{"host": "localhost", "port": "8108", "protocol": "http"}],
    "api_key": "<YOUR_TYPESENSE_API_KEY>",
    "connection_timeout_seconds": 2,
})

# Create collection
schema = {
    "name": "books",
    "fields": [
        {"name": "title", "type": "string"},
        {"name": "author", "type": "string", "facet": True},
        {"name": "ratings", "type": "int32"},
    ],
}
client.collections.create(schema)

# Search with automatic typo-tolerance
search_parameters = {"q": "scince", "query_by": "title"}
results = client.collections["books"].documents.search(search_parameters)
```

### Healthcheck

```bash
curl http://localhost:8108/health
```

## Data and Persistence

Typesense keeps index structures in RAM for high performance while continuously persisting data snapshots and Raft write-ahead logs under `./data/typesense`. Restarting the container reloads all collections into memory without data loss.

References: [Typesense Documentation](https://typesense.org/docs/), [Typesense API Reference](https://typesense.org/docs/latest/api/).
