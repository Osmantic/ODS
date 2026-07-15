# ODS Reranker

A cross-encoder reranking service for ODS RAG pipelines.

## What it does

After your vector database returns top-N candidates via ANN search,
the reranker scores each document against your query using a
cross-encoder model — improving retrieval precision before
passing context to the LLM.

## Why this matters

ANN search (Qdrant) optimises for recall — it finds candidates
that are roughly similar to your query. Cross-encoder reranking
optimises for precision — it scores each candidate properly
against the query and returns the most relevant results first.

Combining both gives significantly better RAG output quality
without changing your vector database.

## Usage

Send a POST request to /rerank with your query and candidate documents:

    POST http://localhost:3010/rerank
    {
      "query": "your search query",
      "documents": ["doc1", "doc2", "doc3"],
      "top_k": 3
    }

Response:

    {
      "results": [
        {"document": "doc1", "score": 0.92, "original_index": 0},
        {"document": "doc3", "score": 0.71, "original_index": 2}
      ],
      "model": "BAAI/bge-reranker-base"
    }

## Models

Default: BAAI/bge-reranker-base
- ~560MB RAM
- CPU-friendly
- Works on Tier 0 (4GB RAM, no GPU)

Higher quality option (requires ~1.1GB RAM):
- Set RERANKER_MODEL=BAAI/bge-reranker-v2-m3 in your .env file

Configurable document cap (default 25):
- Set RERANKER_MAX_DOCUMENTS=50 in your .env file for larger pipelines

## Requirements

- No GPU required
- 1.5GB RAM allocated (512MB minimum reservation)
- Works with any vector database (Qdrant, Chroma, FAISS, Pinecone)
- Compatible with all ODS hardware tiers

## Testing

    pip install pytest httpx
    cd ods/extensions/services/reranker
    pytest test_reranker.py -v

## Endpoints

- GET  /health  Returns service status and loaded model name
- POST /rerank  Reranks documents by relevance to query
