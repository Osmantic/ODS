# Meilisearch

Ultra-fast, typo-tolerant full-text and hybrid search engine in ODS.

## Overview

Meilisearch provides instantaneous search-as-you-type with customizable ranking, typo tolerance, faceted filtering, and dense vector embeddings support for hybrid search workflows. It allows local applications, knowledge bases, and AI agents to query structured documents with sub-50ms latency.

## Features

- **Typo tolerance**: Matches relevant results even when users misspell queries.
- **Hybrid search**: Combines semantic vector similarity with traditional BM25 keyword relevance.
- **Faceted navigation**: Filter and aggregate results dynamically across categories, tags, or authors.
- **Custom ranking rules**: Tailor search results using words, typo count, proximity, attribute weight, sort criteria, and exactness.
- **RESTful API**: Simple JSON endpoints for indexing, searching, and managing collections.
- **Local persistence**: Preserves all search indexes across container restarts via `./data/meilisearch`.

## Configuration

Environment variables (configured in `.env`):

| Variable | Default | Description |
|----------|---------|-------------|
| `MEILISEARCH_PORT` | 7700 | External HTTP REST API port |
| `MEILI_MASTER_KEY` | *(auto-generated)* | Master API key for administrative and write operations |
| `MEILI_ENV` | `production` | Execution environment (`production` enforces API key authentication) |

## Authentication

When `MEILI_MASTER_KEY` is configured in production mode, all API requests (except `/health`) must include the authorization header:

```http
Authorization: Bearer <MEILI_MASTER_KEY>
```

The unauthenticated `GET /health` endpoint is reserved for Docker and orchestrator readiness checks.

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /health` | GET | Readiness healthcheck |
| `GET /version` | GET | Meilisearch server version |
| `GET /indexes` | GET | List all search indexes |
| `POST /indexes` | POST | Create an index |
| `POST /indexes/{index_uid}/documents` | POST | Add or replace documents |
| `POST /indexes/{index_uid}/search` | POST | Execute query (full-text, hybrid, or faceted) |
| `GET /tasks` | GET | Monitor asynchronous indexing tasks |

## Example Usage

### Healthcheck
```bash
curl -fsS http://localhost:7700/health
```

### Create Index & Add Documents
```bash
curl -X POST 'http://localhost:7700/indexes' \
  -H 'Authorization: Bearer '$MEILI_MASTER_KEY \
  -H 'Content-Type: application/json' \
  --data-binary '{"uid": "movies", "primaryKey": "id"}'

curl -X POST 'http://localhost:7700/indexes/movies/documents' \
  -H 'Authorization: Bearer '$MEILI_MASTER_KEY \
  -H 'Content-Type: application/json' \
  --data-binary '[
    {"id": 1, "title": "Inception", "genre": "Sci-Fi"},
    {"id": 2, "title": "Interstellar", "genre": "Sci-Fi"}
  ]'
```

### Search Documents
```bash
curl -X POST 'http://localhost:7700/indexes/movies/search' \
  -H 'Authorization: Bearer '$MEILI_MASTER_KEY \
  -H 'Content-Type: application/json' \
  --data-binary '{"q": "inceptio"}'
```
