# pgvector — PostgreSQL 16 with Vector Similarity Search

An optional, high-performance PostgreSQL 16 relational database preloaded with the official `pgvector` extension. Use it for unified relational data, metadata filtering, transactional consistency (ACID), and high-dimensional vector embeddings supporting both exact nearest neighbor search, IVFFlat indexing, and hierarchical navigable small world (HNSW) graphs.

## Setup

Set credentials in your ODS `.env` file before enabling **pgvector (PostgreSQL & Vector Store)**:

- `PGVECTOR_USER`: administrative username (defaults to `postgres`).
- `PGVECTOR_PASSWORD`: administrative password (required, minimum 8 characters).
- `PGVECTOR_DB`: default database name (defaults to `postgres`).
- `PGVECTOR_PORT`: published host port (defaults to `5435` to avoid port collisions with host Postgres on `5432` or Token Spy on `5434`).

If absent, running `setup.sh` automatically provisions a secure random 32-character password into `.env`. Compose refuses missing or empty passwords.

Once enabled, connect via:
- **Host Endpoint**: `postgresql://postgres:<password>@localhost:5435/postgres`
- **Internal Network Endpoint**: `postgresql://postgres:<password>@pgvector:5432/postgres` (for services on `ods-network`)

## Settings

| Variable | Default | Description |
| --- | --- | --- |
| `PGVECTOR_PORT` | `5435` | Published PostgreSQL port on the host (internal listener remains `5432`) |
| `PGVECTOR_USER` | `postgres` | Superuser username for PostgreSQL |
| `PGVECTOR_PASSWORD` | required | Superuser password for PostgreSQL |
| `PGVECTOR_DB` | `postgres` | Initial database created on startup |

The default binding is loopback (`127.0.0.1`). Container privilege escalation is restricted via `no-new-privileges:true`.

## Usage & Vector Operations

### Enabling the Extension in SQL

Connect with any PostgreSQL client (such as `psql`, DBeaver, or pgAdmin) and enable the vector extension:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

### Table Schema with Vector Embeddings

```sql
CREATE TABLE items (
    id SERIAL PRIMARY KEY,
    content TEXT NOT NULL,
    embedding vector(1536)
);

-- Fast approximate nearest neighbor search index (HNSW)
CREATE INDEX ON items USING hnsw (embedding vector_cosine_ops);
```

### Cosine Distance Query

```sql
SELECT id, content, 1 - (embedding <=> '[0.012, -0.045, ...]') AS similarity
FROM items
ORDER BY embedding <=> '[0.012, -0.045, ...]'
LIMIT 5;
```

### Python (`psycopg` / `langchain`)

```python
from langchain_community.vectorstores import PGVector
from langchain_community.embeddings import OllamaEmbeddings

CONNECTION_STRING = "postgresql+psycopg://postgres:<PASSWORD>@localhost:5435/postgres"

db = PGVector.from_documents(
    documents=docs,
    embedding=OllamaEmbeddings(model="nomic-embed-text"),
    collection_name="ods_knowledge",
    connection_string=CONNECTION_STRING,
)
```

## Data and Persistence

All PostgreSQL database clusters and tables persist on the host under `./data/pgvector`. Disabling or restarting the container retains all stored data and vector indices.

References: [pgvector GitHub](https://github.com/pgvector/pgvector), [PostgreSQL 16 Documentation](https://www.postgresql.org/docs/16/index.html).
