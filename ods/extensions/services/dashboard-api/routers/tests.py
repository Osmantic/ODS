from fastapi import APIRouter, HTTPException
import httpx
import os
from typing import Any

router = APIRouter()


async def probe_service(url_env_key: str, path: str = "/health") -> dict[str, Any]:
    """Probe a service URL from environment and check for 200 OK."""
    url = os.environ.get(url_env_key)
    if not url:
        # Fallback to common ODS pattern if specific URL env is missing
        # but we can't reliably guess ports without the full config.
        # In production, we expect these to be set.
        raise HTTPException(
            status_code=503, detail=f"Environment variable {url_env_key} not set"
        )

    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{url.rstrip('/')}{path}")
            if resp.status_code == 200:
                return {"status": "ok", "detail": "Service is reachable"}
            return {"status": "error", "detail": f"Service returned {resp.status_code}"}
    except (httpx.HTTPError, OSError) as exc:
        raise HTTPException(
            status_code=503, detail=f"Could not reach {url_env_key}: {str(exc)}"
        )


@router.get("/llm")
async def test_llm():
    """Verify LLM connectivity."""
    # Most LLM backends in ODS use LLM_API_URL or OLLAMA_URL
    url_key = "LLM_API_URL" if os.environ.get("LLM_API_URL") else "OLLAMA_URL"
    return await probe_service(
        url_key, "/api/tags" if "ollama" in url_key.lower() else "/health"
    )


@router.get("/rag")
async def test_rag():
    """Verify RAG/Vector DB connectivity."""
    # Typically Qdrant or similar
    url_key = "QDRANT_URL"
    return await probe_service(url_key, "/health")


@router.get("/workflows")
async def test_workflows():
    """Verify n8n connectivity."""
    url_key = "N8N_URL"
    return await probe_service(url_key, "/healthz")


@router.get("/voice")
async def test_voice():
    """Verify Voice/TTS connectivity."""
    # Check Kokoro or Bark
    url_key = "KOKORO_URL" if os.environ.get("KOKORO_URL") else "BARK_URL"
    return await probe_service(url_key, "/health")
