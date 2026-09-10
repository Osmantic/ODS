import pytest
from fastapi.testclient import TestClient
from main import app
from routers import tests as test_router

client = TestClient(app)


def test_llm_unconfigured():
    """Should return 503 if LLM_API_URL is not set."""
    import os

    if "LLM_API_URL" in os.environ:
        del os.environ["LLM_API_URL"]
    if "OLLAMA_URL" in os.environ:
        del os.environ["OLLAMA_URL"]

    response = client.get("/api/test/llm")
    assert response.status_code == 503
    assert "not set" in response.json()["detail"]


def test_llm_reachable_mock():
    """Should return 200 if LLM is reachable (mocked)."""
    import os
    from unittest.mock import patch

    os.environ["LLM_API_URL"] = "http://mock-llm:11434"

    with patch("httpx.AsyncClient.get") as mock_get:
        mock_get.return_value.status_code = 200
        response = client.get("/api/test/llm")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


def test_rag_reachable_mock():
    """Should return 200 if RAG is reachable (mocked)."""
    import os
    from unittest.mock import patch

    os.environ["QDRANT_URL"] = "http://mock-qdrant:6333"

    with patch("httpx.AsyncClient.get") as mock_get:
        mock_get.return_value.status_code = 200
        response = client.get("/api/test/rag")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


def test_workflows_reachable_mock():
    """Should return 200 if Workflows are reachable (mocked)."""
    import os
    from unittest.mock import patch

    os.environ["N8N_URL"] = "http://mock-n8n:5678"

    with patch("httpx.AsyncClient.get") as mock_get:
        mock_get.return_value.status_code = 200
        response = client.get("/api/test/workflows")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


def test_voice_reachable_mock():
    """Should return 200 if Voice is reachable (mocked)."""
    import os
    from unittest.mock import patch

    os.environ["KOKORO_URL"] = "http://mock-kokoro:8000"

    with patch("httpx.AsyncClient.get") as mock_get:
        mock_get.return_value.status_code = 200
        response = client.get("/api/test/voice")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
