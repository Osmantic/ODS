"""Disconnect must cross the router, not merely close the outer sharing hop."""
import asyncio
import json
import time

import httpx
import pytest
from test_router import router  # noqa: F401


@pytest.mark.parametrize('phase', ['admission','route-queue','headers','json-body','stream-body'])
def test_disconnect_cancels_work_and_releases_admission(router, phase):
    mod, _client, write_state, _calls = router
    write_state(queue=phase == 'route-queue')

    async def exercise():
        started = asyncio.Event()
        closed = asyncio.Event()
        pending = asyncio.Queue()
        forwarded = []
        class Hanging(httpx.AsyncByteStream):
            async def __aiter__(self):
                started.set()
                yield b'data: {"model":"Concrete.gguf"}\n\n' if phase == 'stream-body' else b'{'
                await asyncio.sleep(60)
            async def aclose(self):
                await asyncio.sleep(0)
                closed.set()
        async def handler(request):
            forwarded.append(request)
            if phase == 'headers':
                started.set()
                try:
                    await asyncio.sleep(60)
                finally:
                    closed.set()
            return httpx.Response(200, stream=Hanging(),headers={'content-type':'text/event-stream'})
        previous = mod.app.state.http
        if phase == 'admission':
            mod._swap_gate = {'expiresAt':time.monotonic()+60}
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
            mod.app.state.http = transport
            scope = {'type':'http','asgi':{'version':'3.0','spec_version':'2.3'},'http_version':'1.1',
                'method':'POST','scheme':'http','path':'/v1/chat/completions','raw_path':b'/v1/chat/completions',
                'query_string':b'','headers':[],'server':('127.0.0.1',9099),'client':('127.0.0.1',1000)}
            await pending.put({'type':'http.request','body':json.dumps({'model':'ods/shared',
                'messages':[{'role':'user','content':'synthetic'}],'stream':phase == 'stream-body'}).encode(),'more_body':False})
            async def send(_message):
                pass
            task = asyncio.create_task(mod.app(scope,pending.get,send))
            try:
                if phase in ('admission','route-queue'):
                    async def reserved():
                        while not (mod._waiting if phase == 'admission' else mod._inflight):
                            await asyncio.sleep(.01)
                    await asyncio.wait_for(reserved(),2)
                else:
                    await asyncio.wait_for(started.wait(),2)
                await pending.put({'type':'http.disconnect'})
                await asyncio.wait_for(task,2)
                assert mod._waiting == mod._inflight == 0
                if phase in ('admission','route-queue'):
                    assert not forwarded
                else:
                    assert closed.is_set()
            finally:
                if not task.done():
                    task.cancel()
                mod.app.state.http = previous
                mod._swap_gate = None
    asyncio.run(exercise())


def test_response_disconnect_tie_disposes_unclaimed_stream(router):
    mod, *_ = router
    async def exercise():
        cleaned = []
        async def cleanup():
            await asyncio.sleep(0)
            cleaned.append(True)
        async def operation():
            return mod._OwnedStream(iter([]),cleanup=cleanup)
        class Gone:
            async def is_disconnected(self):
                return True
        response = await mod._while_connected(Gone(),operation())
        assert response.status_code == 499 and cleaned == [True]
    asyncio.run(exercise())
