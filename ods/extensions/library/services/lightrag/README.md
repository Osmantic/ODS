# LightRAG — document knowledge graph

This recipe connects LightRAG's document index and graph retrieval to ODS's current chat-model gateway and shared embedding service. It does not create another Ollama instance, select a different chat model or overwrite the model's context configuration.

## Configure before enabling

1. Configure/enable ODS `litellm` and `embeddings`. The chat route is `http://litellm:4000/v1` with model `ods/current`; it uses the existing `LITELLM_KEY`.
2. Set a private `LIGHTRAG_API_KEY` for this application. It is distinct from the model gateway key. Only `/health` is whitelisted: upstream's broad `/api/*` exemption is not retained.
3. Inspect the actual embedding model/service and set `LIGHTRAG_EMBEDDING_DIM` and `LIGHTRAG_EMBEDDING_TOKEN_LIMIT` to its dimension and maximum input tokens. Do not copy values from an unrelated OpenAI example. The model name follows ODS `EMBEDDING_MODEL`; the default name is the existing TEI default, not a new model download requested by this recipe.
4. Set `LIGHTRAG_MAX_TOTAL_TOKENS` to a retrieval budget below the active chat model's context, leaving space for prompts and generated output. Reassess it when switching to a smaller model. This is an application retrieval budget, not an automatic context-size change in Lemonade or ODS.
5. Enable and open `/webui` at `http://localhost:11032/webui`, supplying the application key when prompted. Start with one small text document, wait for indexing, then ask a question whose answer is present in it and inspect its supporting context.

## Model compatibility

Embeddings use `http://embeddings:80/v1`; float output is requested without an OpenAI-specific dimensions parameter. Changing embedding models, even to another with equal dimensions, requires a deliberate new index/reindex because vector spaces differ. Preserve original documents before migration.

Graph extraction depends on the selected model following the application's extraction instructions. A reachable OpenAI-compatible endpoint does not establish that every small model can do this reliably. Ingestion may make multiple model calls; concurrency is limited to one for chat/embedding work to avoid flooding a local machine. Reranking is not configured and no fake reranker endpoint is supplied.

The main image supports Linux amd64 and arm64. Windows/macOS use Linux containers through Docker Desktop; Linux uses Docker Engine. The current shared ODS TEI image is amd64-only, so Apple Silicon requires emulation or a separately verified compatible embedding deployment. Main-image ARM64 support alone does not prove the full dependency chain runs natively. GPU requirements belong to the chosen inference services, not this web application's manifest.

## Data and access

`lightrag-data` retains inputs, graph/vector/index state, prompts and bundled tokenizer cache under `/app/data`. The official image prepares these directories for UID 1000; the recipe starts directly as that user, so its entrypoint skips root ownership repair. Fresh named volumes preserve that layout across host operating systems. Existing root-owned volumes need an explicit migration rather than silently escalating privileges.

The default storage uses local JSON, NetworkX and NanoVectorDB backends, with no external graph database or commercial service. Back up the complete volume with ingestion stopped. Original inputs and a graph file alone are not a complete consistent index backup. Disabling preserves the data.

Only the UI/API port is published on loopback, default 11032. No host folders or Docker socket are mounted. The API key authorizes document operations; keep it private. Changing the key requires updating your clients. `/health` is a readiness endpoint and may expose service metadata; it does not validate retrieval accuracy or authorize access to indexed documents.

The image includes native document processing dependencies, but optional OCR/multimodal pipelines can have additional model, license, download and resource requirements. This recipe does not claim they are all configured. The 3 GB container limit is separate from inference memory and from large-document processing needs.

## Validation status

Versioned upstream configuration, entrypoint and official multiarch image were inspected. Schema, Compose, catalog uniqueness and ODS installation staging are checked separately. No real inference, indexing or browser session was launched; those runtime checks remain pending. This recipe is not evidence that every GPU/model/OS combination has been validated.

- [Versioned project and MIT license](https://github.com/HKUDS/LightRAG/tree/v1.5.7)
- [Configuration contract](https://github.com/HKUDS/LightRAG/blob/v1.5.7/env.example)
- [Image and storage ownership](https://github.com/HKUDS/LightRAG/blob/v1.5.7/Dockerfile)
- [API/UI documentation](https://github.com/HKUDS/LightRAG/blob/v1.5.7/docs/LightRAG-API-Server.md)
