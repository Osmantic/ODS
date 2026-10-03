import asyncio
import json
from types import SimpleNamespace

from fastapi import HTTPException
import httpx
import pytest

from routers import pixel


def test_image_wire_preserves_unicode_in_bounded_ascii_chunks_and_text_is_unchanged():
    payload = {'text': ('Ol\u00e1 \U0001f642\r\n\\"' * 12000)}
    async def collect():
        kwargs = pixel._edge_request_arguments(payload, image_turn=True)
        chunks = [item async for item in kwargs['content']]
        assert all(item.isascii() and len(item) <= 32768 for item in chunks)
        assert json.loads(b''.join(chunks)) == payload
    asyncio.run(collect())
    assert pixel._edge_request_arguments(payload, image_turn=False) == {'json': payload}


def test_encoded_limit_counts_unicode_escapes_and_rejects_before_transport():
    with pytest.raises(HTTPException) as error:
        pixel._edge_request_arguments({'text': '\x01' * (3 * 1024 * 1024)}, image_turn=True)
    assert error.value.status_code == 413


def test_real_retained_producer_uses_ascii_content_transport(monkeypatch):
    payload = {'message': 'Ol\u00e1 \U0001f642'}
    captured = []
    async def handler(request):
        content = await request.aread()
        assert content.isascii()
        captured.append(json.loads(content))
        return httpx.Response(200, headers={'Content-Type': 'text/event-stream'},
            content=b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
    original = httpx.AsyncClient
    monkeypatch.setattr(pixel.httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(pixel, '_edge_chat_body', lambda *args, **kwargs: payload)
    class Store:
        def append(self, *args, **kwargs):
            pass
        def finish(self, identity, state):
            assert state == 'complete'
    body = SimpleNamespace(messages=[SimpleNamespace(images=[object()])], chat_id='chat')
    asyncio.run(pixel._produce_retained_result(Store(), ('owner', 'chat', 'turn'), body,
                                              ('http://edge', 'fixture'), []))
    assert captured == [payload]
