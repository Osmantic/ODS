"""Exercise deadline ownership while ASGI is blocked sending to a slow peer."""
import asyncio
import json

import httpx
import pytest

import test_inference_sharing
from test_inference_sharing import backend, body, gateway

state = test_inference_sharing.state


@pytest.mark.parametrize('spec_version', ['2.3', '2.4'])
@pytest.mark.parametrize('blocked_phase', ['http.response.start', 'http.response.body'])
def test_slow_response_send_cannot_hold_admission_past_deadline(state, spec_version, blocked_phase):
    store, token = state
    document = store.load()
    document['devices'][0]['deadlineSeconds'] = 1
    store.save(document, expected_revision=document['revision'])

    async def exercise():
        sending = asyncio.Event()
        closed = []
        receiving = asyncio.Queue()

        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'data: {"model":"ods/shared"}\n\n'
                await asyncio.Event().wait()

            async def aclose(self):
                await asyncio.sleep(0)
                closed.append(True)

        def handler(request):
            return backend(request) if request.method == 'GET' else httpx.Response(
                200, stream=Stream(), headers={'content-type': 'text/event-stream'})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
            app = gateway.create_app(store, 'http://127.0.0.1:9099', upstream)
            async with app.router.lifespan_context(app):
                scope = {
                    'type': 'http', 'asgi': {'version': '3.0', 'spec_version': spec_version},
                    'http_version': '1.1', 'method': 'POST', 'scheme': 'http',
                    'path': '/v1/chat/completions', 'raw_path': b'/v1/chat/completions',
                    'query_string': b'', 'headers': [(b'authorization', ('Bearer ' + token).encode())],
                    'server': ('127.0.0.1', 8093), 'client': ('127.0.0.1', 1000),
                }
                await receiving.put({'type': 'http.request', 'body': json.dumps(body(stream=True)).encode(),
                                     'more_body': False})

                async def send(message):
                    if message['type'] == blocked_phase:
                        sending.set()
                        await asyncio.Event().wait()

                request = asyncio.create_task(app(scope, receiving.get, send))
                try:
                    await asyncio.wait_for(sending.wait(), 2)
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as probe:
                        auth = {'Authorization': 'Bearer ' + token}
                        assert (await probe.get('/v1/models', headers=auth)).status_code == 429
                        with pytest.raises(gateway.ShareError, match='inference_deadline'):
                            await asyncio.wait_for(request, 3)
                        assert closed == [True]
                        assert (await probe.get('/v1/models', headers=auth)).status_code == 200
                finally:
                    if not request.done():
                        request.cancel()
                    await asyncio.gather(request, return_exceptions=True)

    asyncio.run(exercise())
