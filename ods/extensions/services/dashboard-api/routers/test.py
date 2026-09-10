"""Test endpoints for dashboard API health checks."""

from fastapi import APIRouter, Depends
from security import verify_api_key

router = APIRouter(tags=["test"])


@router.get("/test/llm")
async def test_llm(api_key: str = Depends(verify_api_key)):
    """Test LLM endpoint."""
    return {"success": True}


@router.get("/test/rag")
async def test_rag(api_key: str = Depends(verify_api_key)):
    """Test RAG endpoint."""
    return {"success": True}


@router.get("/test/workflows")
async def test_workflows(api_key: str = Depends(verify_api_key)):
    """Test workflows endpoint."""
    return {"success": True}


@router.get("/test/voice")
async def test_voice(api_key: str = Depends(verify_api_key)):
    """Test voice endpoint."""
    return {"success": True}
