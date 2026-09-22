# LocalRecall for ODS

LocalRecall stores document collections and their vector indexes for retrieval by agents or applications. This recipe uses its official backend, the existing ODS embeddings service and a small ODS access adapter. It preserves the original browser UI and REST endpoints; it does not silently copy Portal history into a knowledge base.

## Setup and access

Configure `LOCALRECALL_API_KEY` with at least 24 random printable ASCII characters, excluding spaces and commas. Enable the **embeddings** dependency and LocalRecall. Open `http://localhost:11024` (or `LOCALRECALL_PORT`), use username `ods` and the configured key as the browser password. API clients use `Authorization: Bearer <key>` against the same address or `http://localrecall:8080` from the ODS network.

The upstream `API_KEYS` setting protects even its HTML routes. The ODS adapter accepts browser HTTP Basic authentication, then supplies the verified key to upstream as Bearer authentication. This lets the original same-origin interface operate without removing upstream protection. Do not expose Basic authentication over unencrypted remote HTTP; an intentional remote deployment needs HTTPS. Rotate the configured key by recreating both extension containers; the browser may retain its old Basic credentials until you sign in again.

Create a collection, upload a small text file and search for a known passage. The original API is under `/api`: `POST /api/collections`, `POST /api/collections/{name}/upload`, and `POST /api/collections/{name}/search`. Uploads pass through the adapter with a 128 MiB request limit. Authenticated writes from a different browser origin are rejected. The adapter only targets the fixed LocalRecall backend; it is not an arbitrary forward proxy.

## Embeddings and model changes

Embedding requests go to `http://embeddings:80/v1`, using the same `EMBEDDING_MODEL` configuration as the ODS TEI service. They never go to `ods/current` or change the chat model. The default is the existing `BAAI/bge-base-en-v1.5` embedding configuration, not an additional chat model.

Existing collections must continue using the embedding model/dimensions they were indexed with. After intentionally changing that model, rebuild affected collections from preserved source documents; do not mix old and new vectors. Chat-model changes do not require reindexing. Retrieval quality depends on the chosen embedding model and language. Initial dependency setup may download its model weights and requires their separate license review.

## Storage and platforms

`localrecall-data` stores the Chromem indexes in `/state/db` and originals in `/state/assets`. Stop the extension before taking a consistent full-volume backup. Disabling stops both the adapter and `localrecall-backend` through the updated host agent; it retains the volume and leaves shared embeddings independently managed.

The backend image is pinned and supports amd64/arm64. The adapter builds a static binary for the Docker target architecture from a pinned Go toolchain; no Go installation is required on the host. Both work in Linux-container Docker on Windows/Linux/macOS. The current ODS TEI recipe is amd64-only and needs emulation on Apple Silicon: this dependency is not native arm64. No CUDA assumption or native Metal support is claimed. Budget the embeddings service separately from the roughly 1.1 GB container caps here.

## Readiness and validation

The adapter's `/health` performs an authenticated upstream collection-list request and returns only HTTP 204 or 503, never collection data. The public route does not authorize access to documents or other API routes. Both Docker and dashboard checks use that bounded probe. It confirms backend availability, not successful embedding inference.

Adapter protocol tests verify browser/API authentication, denial of unauthenticated requests, request forwarding and health behavior on redirects/errors. Application runtime validation remains pending: enable dependencies, authenticate in the browser, upload/search a synthetic document, restart the extension, verify persistence, and verify key rotation. No real model inference was started during development checks.

Upstream: [LocalRecall](https://github.com/mudler/LocalRecall), MIT. `upstream.json` records the original image and platform evidence; `Dockerfile` pins the adapter's toolchain. The additional adapter is part of this one extension, not a separate catalog entry.
