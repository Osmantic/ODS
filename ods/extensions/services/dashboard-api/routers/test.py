"""Router for ODS capability test endpoints."""

import logging
import os
from typing import Any

from fastapi import APIRouter, Depends

import hermes_bridge
from config import SERVICES
from security import verify_api_key
from search_probe import classify_search_result, probe_searxng

logger = logging.getLogger(__name__)

router = APIRouter(tags=["test"])


@router.get("/api/test/llm")
async def test_llm(api_key: str = Depends(verify_api_key)) -> dict[str, Any]:
    """Test LLM capability and health."""
    from helpers import check_service_health

    cfg = SERVICES.get("llama-server")
    if not cfg:
        return {"success": False, "error": "LLM service is not configured"}

    status = await check_service_health("llama-server", cfg)
    return {"success": status.status == "healthy"}


@router.get("/api/test/voice")
async def test_voice(api_key: str = Depends(verify_api_key)) -> dict[str, Any]:
    """Test voice services health."""
    from routers.voice import voice_status

    res = await voice_status(api_key=api_key)
    return {"success": res.get("available", False)}


@router.get("/api/test/rag")
async def test_rag(api_key: str = Depends(verify_api_key)) -> dict[str, Any]:
    """Test document chat / RAG (Qdrant) health."""
    from helpers import check_service_health

    cfg = SERVICES.get("qdrant")
    if not cfg:
        return {"success": False, "error": "Qdrant service is not configured"}

    status = await check_service_health("qdrant", cfg)
    return {"success": status.status == "healthy"}


@router.get("/api/test/workflows")
async def test_workflows(api_key: str = Depends(verify_api_key)) -> dict[str, Any]:
    """Test workflows (n8n) health."""
    from routers.workflows import check_n8n_available

    available = await check_n8n_available()
    return {"success": available}


@router.get("/api/test/search")
async def test_search(api_key: str = Depends(verify_api_key)) -> dict[str, Any]:
    """Run a search capability probe and classify the outcome."""
    query = "Search the web for Osmantic ODS"
    searxng_url = os.environ.get("SEARXNG_URL") or "http://searxng:8080"

    # 1. Run direct SearXNG probe
    hits = await probe_searxng(searxng_url, query)

    # 2. Run agent prompt and trace if tool was called
    tool_was_called = False
    agent_reply = ""
    try:
        async for event in hermes_bridge.stream_prompt("search-probe-session", query):
            if event.get("type") == "tool_start" and event.get("tool") == "web_search":
                tool_was_called = True
            elif event.get("type") == "complete":
                agent_reply = event.get("text", "")
    except Exception as exc:
        logger.warning("Search capability probe Hermes stream failed: %s", exc)
        return {
            "success": False,
            "verdict": "fail",
            "reason": f"hermes_unavailable: {exc}",
        }

    # 3. Classify
    verdict, reason = classify_search_result(
        agent_reply=agent_reply,
        tool_was_called=tool_was_called,
        searxng_hit_count=hits,
    )

    return {
        "success": verdict == "ok",
        "verdict": verdict,
        "reason": reason,
        "hits": hits,
        "tool_was_called": tool_was_called,
        "agent_reply": agent_reply,
    }
