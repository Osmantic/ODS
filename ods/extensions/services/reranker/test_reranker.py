import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_rerank_basic():
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
    response = client.post("/rerank", json={
        "query": "test query",
        "documents": ["doc1", "doc2"],
        "top_k": 10
    })
    assert response.status_code == 200
    assert len(response.json()["results"]) == 2
