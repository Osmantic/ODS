import io
import os
import uuid
import logging
from typing import Any, List, Optional

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, Depends
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, ScoredPoint
from pydantic import BaseModel

from security import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(tags=["knowledge"])

SESSION_COOKIE_NAME = "dream-session"
QDRANT_URL = os.environ.get("QDRANT_URL", "http://qdrant:6333")
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY", "")
EMBEDDINGS_URL = os.environ.get("EMBEDDINGS_URL", "http://embeddings:80/v1/embeddings")
COLLECTION_NAME = "dream_knowledge"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

_qdrant_client: Optional[QdrantClient] = None
_qdrant_initialized = False

async def get_embeddings(texts: List[str]) -> List[List[float]]:
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                EMBEDDINGS_URL,
                json={"input": texts, "model": "local-model"},
                timeout=60.0
            )
            resp.raise_for_status()
            data = resp.json()
            return [item["embedding"] for item in data["data"]]
    except Exception as e:
        logger.error("Failed to fetch embeddings: %s", e)
        raise HTTPException(status_code=502, detail=f"Embeddings service unavailable: {e}")

async def get_qdrant() -> Optional[QdrantClient]:
    global _qdrant_client, _qdrant_initialized
    if _qdrant_initialized:
        return _qdrant_client

    try:
        client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
        if not client.collection_exists(COLLECTION_NAME):
            test_embedding = await get_embeddings(["test"])
            dim = len(test_embedding[0])
            client.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
            )
        _qdrant_client = client
    except Exception as e:
        logger.warning("Failed to connect to Qdrant: %s", e)
        _qdrant_client = None

    _qdrant_initialized = True
    return _qdrant_client

def extract_text(file_content: bytes, filename: str) -> str:
    if filename.lower().endswith(".pdf"):
        try:
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(file_content))
            return "\n".join(page.extract_text() for page in reader.pages if page.extract_text())
        except Exception as e:
            logger.error("Failed to parse PDF: %s", e)
            raise HTTPException(status_code=400, detail="Could not parse PDF file.")
    elif filename.lower().endswith((".txt", ".md", ".csv")):
        return file_content.decode("utf-8", errors="replace")
    else:
        raise HTTPException(status_code=400, detail="Unsupported file format. Please upload PDF, TXT, MD, or CSV.")

def chunk_text(text: str, chunk_words: int = 250, overlap_words: int = 50) -> List[str]:
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunk = " ".join(words[i:i + chunk_words])
        if chunk.strip():
            chunks.append(chunk)
        i += (chunk_words - overlap_words)
    return chunks

@router.post("/api/knowledge/upload")
async def upload_document(request: Request, file: UploadFile = File(...), api_key: str = Depends(verify_api_key)):
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"File is too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)} MB).")

    text = extract_text(content, file.filename)
    if not text.strip():
        raise HTTPException(status_code=400, detail="Document contains no extractable text.")

    doc_id = str(uuid.uuid4())
    chunks = chunk_text(text)
    if not chunks:
        raise HTTPException(status_code=400, detail="Document chunking failed.")

    qdrant = await get_qdrant()
    if qdrant is None:
        raise HTTPException(status_code=503, detail="Qdrant vector database is not connected.")

    embeddings = await get_embeddings(chunks)

    points = []
    for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
        points.append(PointStruct(
            id=str(uuid.uuid4()),
            vector=emb,
            payload={
                "doc_id": doc_id,
                "filename": file.filename,
                "chunk_index": i,
                "text": chunk
            }
        ))

    # Batch upsert points
    batch_size = 100
    for i in range(0, len(points), batch_size):
        qdrant.upsert(
            collection_name=COLLECTION_NAME,
            points=points[i:i + batch_size]
        )

    return {"status": "ok", "doc_id": doc_id, "filename": file.filename, "chunks": len(chunks)}

@router.get("/api/knowledge/documents")
async def list_documents(request: Request, api_key: str = Depends(verify_api_key)):
    qdrant = await get_qdrant()
    if qdrant is None:
        return {"documents": []}

    # Hacky way to get unique documents: scroll and group by doc_id
    try:
        records, _ = qdrant.scroll(
            collection_name=COLLECTION_NAME,
            limit=10000,
            with_payload=["doc_id", "filename"]
        )
        docs = {}
        for r in records:
            d_id = r.payload.get("doc_id")
            if d_id and d_id not in docs:
                docs[d_id] = r.payload.get("filename")
        return {"documents": [{"id": k, "filename": v} for k, v in docs.items()]}
    except Exception as e:
        logger.error("Failed to list docs: %s", e)
        return {"documents": []}

@router.delete("/api/knowledge/documents/{doc_id}")
async def delete_document(doc_id: str, request: Request, api_key: str = Depends(verify_api_key)):
    qdrant = await get_qdrant()
    if qdrant is None:
        raise HTTPException(status_code=503, detail="Qdrant vector database is not connected.")

    from qdrant_client.http.models import Filter, FieldCondition, MatchValue
    qdrant.delete(
        collection_name=COLLECTION_NAME,
        points_selector=Filter(
            must=[
                FieldCondition(
                    key="doc_id",
                    match=MatchValue(value=doc_id)
                )
            ]
        )
    )
    return {"status": "ok"}

async def search_knowledge_base(query: str, top_k: int = 3) -> str:
    """Helper to be called from talk.py"""
    qdrant = await get_qdrant()
    if qdrant is None:
        return ""
    try:
        query_emb = (await get_embeddings([query]))[0]
        results = qdrant.search(
            collection_name=COLLECTION_NAME,
            query_vector=query_emb,
            limit=top_k
        )
        if not results:
            return ""

        context_parts = []
        for r in results:
            text = r.payload.get("text", "")
            fname = r.payload.get("filename", "unknown")
            context_parts.append(f"From {fname}:\n{text}")

        return "\n\n".join(context_parts)
    except Exception as e:
        logger.error("Failed to search knowledge base: %s", e)
        return ""
