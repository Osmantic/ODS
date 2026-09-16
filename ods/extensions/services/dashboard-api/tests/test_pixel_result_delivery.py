"""A slow HTTP subscriber must receive the complete durable result."""
import asyncio

import pytest

from routers import pixel
from test_pixel import ConnectedRequest, FakeClient, FakeResponse, stream_body
import test_pixel_chat_results
from test_pixel_chat_results import IDENTITY, OWNER, body

store = test_pixel_chat_results.store


@pytest.mark.parametrize("terminal", [True, False])
def test_retained_stream_drains_tail_committed_during_send(store, monkeypatch, terminal):
    async def run():
        first_sent = asyncio.Event()
        release_tail = asyncio.Event()
        first = b'data: {"choices":[{"delta":{"content":"First "}}]}\n\n'
        last = b'data: {"choices":[{"delta":{"content":"last"}}]}\n\n'

        class Upstream(FakeResponse):
            async def aiter_bytes(self):
                yield first
                first_sent.set()
                await release_tail.wait()
                yield last
                if terminal:
                    yield b'data: [DONE]\n\n'

        monkeypatch.setattr(pixel.httpx, 'AsyncClient', lambda **kw: FakeClient(Upstream(content_type='text/event-stream')))

        async def cancel(*args):
            return True

        monkeypatch.setattr(pixel, '_cancel_edge_run', cancel)
        response = await pixel.pixel_chat_stream(ConnectedRequest(), body(), OWNER)
        await asyncio.wait_for(first_sent.wait(), 1)
        iterator = response.body_iterator
        received = await anext(iterator)
        # Sending the first response chunk yields control to the producer.
        # It commits its final bytes before this subscriber resumes.
        release_tail.set()
        await asyncio.gather(*list(pixel._result_tasks.values()))
        received += b''.join([chunk async for chunk in iterator])
        retained = b''.join(row['data'] for row in store.chunks(IDENTITY))
        assert b'last' in retained
        assert received == retained
        assert b'[DONE]' in received
        assert store.get(IDENTITY)['state'] == ('complete' if terminal else 'interrupted')
        # Reattachment observes the same complete receipt without new execution.
        replay = await pixel.pixel_chat_stream(ConnectedRequest(), body(), OWNER)
        assert await stream_body(replay) == retained

    asyncio.run(run())
