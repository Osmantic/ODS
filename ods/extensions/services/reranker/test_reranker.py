import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient


# Create mock model before importing app
# This prevents the real CrossEncoder from loading during tests
mock_model = MagicMock()
mock_model.predict.return_value = [0.9, 0.5, 0.3]

with patch("main.get_model", return_value=mock_model):
    from main import app
    import main
    main.model = mock_model

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_rerank_basic():
    mock_model.predict.return_value = [0.9, 0.2, 0.6]
    response = client.post("/rerank", json={
        "query": "what is machine learning",
        "documents": [
            "Machine learning is a subset of AI",
            "Python is a programming language",
            "Deep learning uses neural networks"
        ],
        "top_k": 2
    })
    assert response.status_code == 200
    data = response.json()
    assert len(data["results"]) == 2
    assert data["results"][0]["score"] > data["results"][1]["score"]


def test_rerank_empty_documents():
    response = client.post("/rerank", json={
        "query": "test",
        "documents": [],
        "top_k": 5
    })
    assert response.status_code == 400


def test_rerank_empty_query():
    response = client.post("/rerank", json={
        "query": "",
        "documents": ["some document"],
        "top_k": 1
    })
    assert response.status_code == 400


def test_rerank_top_k_capped():
    mock_model.predict.return_value = [0.9, 0.5]
    response = client.post("/rerank", json={
        "query": "test query",
        "documents": ["doc1", "doc2"],
        "top_k": 10
    })
    assert response.status_code == 200
    assert len(response.json()["results"]) == 2


def test_rerank_max_documents_exceeded():
    docs = [f"document {i}" for i in range(26)]
    response = client.post("/rerank", json={
        "query": "test",
        "documents": docs,
        "top_k": 5
    })
    assert response.status_code == 400