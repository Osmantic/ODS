from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sentence_transformers import CrossEncoder
import os
import logging


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MAX_DOCUMENTS = int(os.getenv("RERANKER_MAX_DOCUMENTS", "25"))

MODEL_NAME = os.getenv(
    "RERANKER_MODEL",
    "BAAI/bge-reranker-base"
)


def get_model() -> CrossEncoder:
    """Load and return the reranker model. Injectable for testing."""
    logger.info(f"Loading reranker model: {MODEL_NAME}")
    m = CrossEncoder(MODEL_NAME)
    logger.info("Reranker model loaded successfully")
    return m


model: CrossEncoder = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global model
    model = get_model()
    yield


app = FastAPI(
    title="ODS Reranker",
    description="Cross-encoder reranking for RAG pipelines",
    version="1.0.0",
    lifespan=lifespan
)


class RerankRequest(BaseModel):
    query: str
    documents: list[str]
    top_k: int = 5


class RerankResult(BaseModel):
    document: str
    score: float
    original_index: int


class RerankResponse(BaseModel):
    results: list[RerankResult]
    model: str


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_NAME}


@app.post("/rerank", response_model=RerankResponse)
def rerank(request: RerankRequest):
    if not request.documents:
        raise HTTPException(
            status_code=400,
            detail="documents list cannot be empty"
        )

    if len(request.documents) > MAX_DOCUMENTS:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum {MAX_DOCUMENTS} documents allowed per request"
        )

    if not request.query.strip():
        raise HTTPException(
            status_code=400,
            detail="query cannot be empty"
        )

    try:
        pairs = [[request.query, doc] for doc in request.documents]
        scores = model.predict(pairs)

        scored = sorted(
            zip(scores, request.documents, range(len(request.documents))),
            key=lambda x: x[0],
            reverse=True
        )

        top_k = min(request.top_k, len(request.documents))
        results = [
            RerankResult(
                document=doc,
                score=float(score),
                original_index=idx
            )
            for score, doc, idx in scored[:top_k]
        ]

        return RerankResponse(results=results, model=MODEL_NAME)

    except ValueError as e:
        logger.error(f"Reranking input error: {e}")
        raise HTTPException(
            status_code=422,
            detail="Invalid input format"
        )

    except Exception as e:
        logger.error(f"Reranking failed: {e}")
        raise HTTPException(
            status_code=500,
            detail="Internal reranking error"
        )