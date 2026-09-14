# ChromaDB

AI-native open-source vector database for building embeddings-based applications. Store and query vector embeddings with metadata filtering via a simple REST API.

## Requirements

- **GPU:** Not required for storing or querying supplied embeddings
- **Dependencies:** None

## Enable / Disable

```bash
ods enable chromadb
ods disable chromadb
```

Your data is preserved when disabling. To re-enable later: `ods enable chromadb`

## Existing installation: recover data before recreating

Chroma 1.5.3 writes its database and vector index under `/data`. Older ODS recipes
mounted the host directory at `/chromadb` instead, leaving the active database in
the container's writable layer. Before upgrading an existing installation, stop
clients and **keep the old container** until its data has been recovered. Do not
uninstall it or run Compose with `--force-recreate` first.

From the ODS installation directory, stop the container and copy its complete
database directory into a new backup directory:

```bash
docker stop ods-chromadb
mkdir chromadb-pre-mount-fix
docker cp ods-chromadb:/data/. ./chromadb-pre-mount-fix/
```

Verify that the backup contains `chroma.sqlite3` and retain all other files and
subdirectories, including any SQLite journal files and vector indexes. Preserve
any existing `data/chromadb` separately, then put the recovered directory's entire
contents in `data/chromadb`. Do not merge two populated databases. The corrected
recipe mounts that directory at `/data`; update the installed extension recipe
before enabling it again. Verify your known collections and queries after startup.

If the old container was already removed, its writable-layer database is no
longer available through `docker cp`; restore an earlier backup or rebuild it
from source documents and embeddings. This change cannot recover deleted data.

For later backups, stop Chroma before copying the complete `data/chromadb`
directory. Keep the image version with the backup. Rolling back this mount fix
would leave `/data` unmounted again, so retain the corrected mount even if an
unrelated application change is rolled back. Never delete the recovery backup
until collection contents and vector queries have been verified.

## Access

- **URL:** `http://localhost:8000`

## First-Time Setup

1. Enable the service: `ods enable chromadb`
2. Use the REST API at `http://localhost:8000` to create collections and add embeddings

### API Examples

```bash
# Health check
curl http://localhost:8000/api/v2/heartbeat

# Create a collection
curl -X POST http://localhost:8000/api/v2/tenants/default_tenant/databases/default_database/collections \
  -H "Content-Type: application/json" \
  -d '{"name": "my_collection"}'
```

`CHROMADB_PORT` changes the published port. The API binds to loopback by default
and this recipe does not configure authentication; protect access before changing
the bind address. The persistence test uses supplied vectors and does not download
an embedding model.
