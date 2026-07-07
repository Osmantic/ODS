"""Tests for the Native RAG Knowledge Base API."""

import pytest
from unittest.mock import MagicMock


@pytest.fixture
def auth_headers(test_client):
    return test_client.auth_headers


def test_knowledge_unauthenticated_rejected(test_client):
    resp = test_client.get("/api/knowledge/documents")
    assert resp.status_code == 401

    resp = test_client.delete("/api/knowledge/documents/some-id")
    assert resp.status_code == 401

    resp = test_client.post(
        "/api/knowledge/upload", files={"file": ("test.txt", b"hello", "text/plain")}
    )
    assert resp.status_code == 401


def test_knowledge_qdrant_unavailable(test_client, auth_headers, monkeypatch):
    import routers.knowledge as knowledge

    async def mock_get_qdrant():
        return None

    monkeypatch.setattr(knowledge, "get_qdrant", mock_get_qdrant)

    # List should return empty list
    resp = test_client.get("/api/knowledge/documents", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == {"documents": []}

    # Upload should return 503
    resp = test_client.post(
        "/api/knowledge/upload",
        headers=auth_headers,
        files={"file": ("test.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 503

    # Delete should return 503
    resp = test_client.delete("/api/knowledge/documents/some-id", headers=auth_headers)
    assert resp.status_code == 503


def test_knowledge_unsupported_file_format(test_client, auth_headers):
    resp = test_client.post(
        "/api/knowledge/upload",
        headers=auth_headers,
        files={"file": ("test.exe", b"binary data", "application/octet-stream")},
    )
    assert resp.status_code == 400
    assert "Unsupported file format" in resp.json()["detail"]


def test_knowledge_upload_size_limit(test_client, auth_headers):
    # 10MB + 1 byte
    large_content = b"a" * (10 * 1024 * 1024 + 1)

    resp = test_client.post(
        "/api/knowledge/upload",
        headers=auth_headers,
        files={"file": ("test.txt", large_content, "text/plain")},
    )
    assert resp.status_code == 413
    assert "File is too large" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_knowledge_deletion_targets_doc_id(
    test_client, auth_headers, monkeypatch
):
    import routers.knowledge as knowledge

    mock_qdrant = MagicMock()

    async def mock_get_qdrant():
        return mock_qdrant

    monkeypatch.setattr(knowledge, "get_qdrant", mock_get_qdrant)

    resp = test_client.delete(
        "/api/knowledge/documents/test-doc-123", headers=auth_headers
    )
    assert resp.status_code == 200

    mock_qdrant.delete.assert_called_once()
    call_kwargs = mock_qdrant.delete.call_args[1]
    assert call_kwargs["collection_name"] == knowledge.COLLECTION_NAME

    filters = call_kwargs["points_selector"].must
    assert len(filters) == 1
    assert filters[0].key == "doc_id"
    assert filters[0].match.value == "test-doc-123"


def test_talk_attachment_use_knowledge(test_client, monkeypatch):
    import session_signer

    monkeypatch.setenv("ODS_SESSION_SECRET", "test-secret")
    session_signer._set_secret_for_tests("test-secret")
    cookie = session_signer.issue(ttl_seconds=3600)
    test_client.cookies.set("ods-session", cookie)

    async def mock_search(query: str):
        return "This is retrieved knowledge context."

    monkeypatch.setattr("routers.knowledge.search_knowledge_base", mock_search)

    # We mock out _stream_hermes_sse to capture the final prompt
    captured_prompt = None

    async def mock_stream(session_key, prompt, request):
        nonlocal captured_prompt
        captured_prompt = prompt
        yield b"event: message\ndata: ok\n\n"

    monkeypatch.setattr("routers.talk._stream_hermes_sse", mock_stream)

    resp = test_client.post(
        "/api/talk/attachment",
        data={"text": "Here is a file", "use_knowledge": "true"},
        files={"file": ("hello.txt", b"file content", "text/plain")},
    )

    assert resp.status_code == 200
    assert "Context from Knowledge Base" in captured_prompt
    assert "This is retrieved knowledge context." in captured_prompt
    assert "User Message:" in captured_prompt
    assert "hello.txt" in captured_prompt
