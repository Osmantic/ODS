"""Tests for the Native RAG Knowledge Base API."""

import pytest
from unittest.mock import AsyncMock


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

    mock_qdrant = AsyncMock()

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


def test_talk_image_attachment_use_knowledge(test_client, monkeypatch):
    """Image attachments with use_knowledge=true should inject KB context
    into the prompt sent to the vision model."""
    import session_signer

    monkeypatch.setenv("ODS_SESSION_SECRET", "test-secret")
    session_signer._set_secret_for_tests("test-secret")
    cookie = session_signer.issue(ttl_seconds=3600)
    test_client.cookies.set("ods-session", cookie)

    async def mock_search(query: str):
        return "Knowledge about the image subject."

    monkeypatch.setattr("routers.knowledge.search_knowledge_base", mock_search)

    # Mock _stream_vision_chat to capture the prompt_text it receives
    captured_prompt = None

    async def mock_vision_stream(image_bytes, content_type, prompt_text):
        nonlocal captured_prompt
        captured_prompt = prompt_text
        yield b'data: {"type":"session","session_id":"vision-oneshot"}\n\n'
        yield b'data: {"type":"complete","session_id":"vision-oneshot","text":"ok","status":"ok"}\n\n'
        yield b'data: {"type":"done"}\n\n'

    monkeypatch.setattr("routers.talk._stream_vision_chat", mock_vision_stream)

    # A minimal PNG header so _classify_attachment identifies it as an image
    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
    )

    resp = test_client.post(
        "/api/talk/attachment",
        data={"text": "What is in this image?", "use_knowledge": "true"},
        files={"file": ("photo.png", png_bytes, "image/png")},
    )

    assert resp.status_code == 200
    assert captured_prompt is not None
    assert "Context from Knowledge Base" in captured_prompt
    assert "Knowledge about the image subject." in captured_prompt
    assert "What is in this image?" in captured_prompt


@pytest.mark.asyncio
async def test_knowledge_qdrant_retry(monkeypatch):
    import routers.knowledge as knowledge

    # Reset state
    knowledge._qdrant_initialized = False
    knowledge._qdrant_client = None

    call_count = 0

    class DummyClient:
        async def collection_exists(self, name):
            return True

    # We mock AsyncQdrantClient inside get_qdrant, but it's easier to just mock AsyncQdrantClient directly
    def mock_qdrant_client(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("First call fails")
        return DummyClient()

    monkeypatch.setattr(knowledge, "AsyncQdrantClient", mock_qdrant_client)

    # First call should return None (caught exception) and leave _qdrant_initialized = False
    client1 = await knowledge.get_qdrant()
    assert client1 is None
    assert knowledge._qdrant_initialized is False

    # Second call should succeed, return DummyClient, and set _qdrant_initialized = True
    client2 = await knowledge.get_qdrant()
    assert client2 is not None
    assert knowledge._qdrant_initialized is True


def test_talk_message_use_knowledge_unavailable(test_client, monkeypatch):
    """If a user requests use_knowledge but Qdrant is down, the request should
    fail explicitly with 503 rather than silently querying the LLM without context."""
    import session_signer
    import routers.knowledge as knowledge

    monkeypatch.setenv("ODS_SESSION_SECRET", "test-secret")
    session_signer._set_secret_for_tests("test-secret")
    cookie = session_signer.issue(ttl_seconds=3600)
    test_client.cookies.set("ods-session", cookie)

    # Force get_qdrant to fail
    async def mock_get_qdrant():
        return None

    monkeypatch.setattr(knowledge, "get_qdrant", mock_get_qdrant)

    resp = test_client.post(
        "/api/talk/message/stream",
        json={"text": "Hello", "use_knowledge": True}
    )

    assert resp.status_code == 503
    assert "Knowledge base unavailable" in resp.json()["detail"]
