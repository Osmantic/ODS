import asyncio
import importlib.util
import json
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / 'bin'))
spec = importlib.util.spec_from_file_location('inference_sharing_app', Path(__file__).parents[1] / 'app/main.py')
gateway = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway)
from pixel_provider.sharing import SharingStore  # noqa: E402


@pytest.fixture
def state(tmp_path):
    tmp_path.chmod(0o700)
    store = SharingStore(tmp_path)
    result = store.issue(dict(label='Laptop', catalogId='glm', runtimeModelId='GLM', ttlSeconds=3600,
        maxConcurrent=1, maxOutputTokens=64, deadlineSeconds=3, requestsPerMinute=60), expected_revision=0)
    store.set_enabled(True, expected_revision=1)
    return store, result['credential']['key']


def body(**changes):
    return dict(model='ods/shared', messages=[{'role': 'user', 'content': 'hello'}], **changes)


def metadata(**changes):
    return {'ods': dict(catalogId='glm', routedModel='GLM', routeSeq=4, **changes)}


def backend(request):
    if request.method == 'GET':
        return httpx.Response(200, json=metadata())
    return httpx.Response(200, json={'model': 'ods/shared', 'choices': []},
                          headers={'X-ODS-Route-Seq': '4'})


@pytest.fixture
def connect(state):
    from contextlib import contextmanager

    @contextmanager
    def connect(handler=backend, **kwargs):
        store, token = state
        upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        app = gateway.create_app(store, 'http://127.0.0.1:9099', upstream)
        with TestClient(app, **kwargs) as client:
            yield client, {'Authorization': 'Bearer ' + token}
        asyncio.run(upstream.aclose())
    return connect


def test_real_private_grant_json_and_header_isolation(connect):
    requests = []
    def handler(request):
        requests.append(request)
        return backend(request)
    with connect(handler) as (client, auth):
        models = client.get('/v1/models', headers=auth)
        assert models.status_code == 200
        assert models.json()['data'][0]['id'] == 'ods/shared'
        response = client.post('/v1/chat/completions', json=body(), headers={**auth,
            'Cookie': 'secret', 'X-Api-Key': 'secret', 'X-ODS-Expected-Model': 'other'})
        assert response.status_code == 200
        assert response.headers['x-ods-route-seq'] == '4'
        forwarded = requests[-1]
        assert json.loads(forwarded.content)['max_tokens'] == 64
        assert forwarded.headers['X-ODS-Expected-Model'] == 'GLM'
        assert forwarded.headers['X-ODS-Expected-Catalog'] == 'glm'
        assert forwarded.headers['X-ODS-Expected-Route'] == '4'
        assert not {'authorization', 'cookie', 'x-api-key'} & set(forwarded.headers)


@pytest.mark.parametrize('method,path', [('GET','/v1/status'), ('POST','/v1/models'),
    ('POST','/v1/responses'), ('POST','/v1/chat/completions/'), ('GET','/docs'),
    ('POST','/api/pixel/providers/save'), ('DELETE','/v1/models')])
def test_no_management_surface(connect, method, path):
    with connect(lambda request: pytest.fail('must not forward')) as (client, auth):
        assert client.request(method, path, headers=auth).status_code == 404


def test_auth_and_query_are_not_forwarded(connect):
    with connect(lambda request: pytest.fail('must not forward')) as (client, auth):
        assert client.post('/v1/chat/completions', content=b'not json').status_code == 401
        assert client.get('/v1/models', headers=[('Authorization',auth['Authorization'])]*2).status_code == 401
        assert client.get('/v1/models?target=http://evil', headers=auth).status_code == 400


@pytest.mark.parametrize('change', [dict(model='other'), dict(max_tokens=65), dict(max_tokens=True),
    dict(max_tokens=1,max_completion_tokens=1), dict(n=2), dict(stream=1), dict(api_base='http://evil'),
    dict(chat_template_kwargs={'arbitrary':True}), dict(tools=[{'type':'web_search'}]),
    dict(messages=[{'role':'user','content':[{'type':'image_url','image_url':{'url':'http://169.254.169.254'}}]}])])
def test_request_limits(connect, change):
    payload = body()
    payload.update(change)
    with connect(lambda request: pytest.fail('must not forward')) as (client, auth):
        assert client.post('/v1/chat/completions', json=payload, headers=auth).status_code == 400


def test_strict_body_limits(connect):
    with connect(lambda request: pytest.fail('must not forward')) as (client, auth):
        for raw in (b'{"model":"ods/shared","model":"other"}', b'{"n":NaN}', b'['*40):
            assert client.post('/v1/chat/completions', content=raw, headers=auth).status_code == 400
        assert client.post('/v1/chat/completions', content=b'x'*(gateway.MAX_BYTES+1), headers=auth).status_code == 413


def test_changed_route_fails_closed(connect):
    def handler(request):
        assert request.method == 'GET'
        return httpx.Response(200,json={'ods': {'catalogId':'other','routedModel':'GLM','routeSeq':5}})
    with connect(handler) as (client, auth):
        assert client.post('/v1/chat/completions', json=body(), headers=auth).status_code == 409


def test_revocation_rechecked_after_route_lookup(connect, state):
    store, _ = state
    def handler(request):
        assert request.method == 'GET'
        doc = store.load()
        store.revoke(doc['devices'][0]['id'], expected_revision=doc['revision'])
        return backend(request)
    with connect(handler) as (client, auth):
        assert client.post('/v1/chat/completions', json=body(), headers=auth).status_code == 401


def test_upstream_secret_error_is_not_reflected_and_slot_released(connect):
    calls = 0
    def handler(request):
        nonlocal calls
        if request.method == 'POST':
            calls += 1
            return httpx.Response(503, text='SECRET')
        return backend(request)
    with connect(handler) as (client, auth):
        for _ in range(2):
            response = client.post('/v1/chat/completions', json=body(), headers=auth)
            assert response.status_code == 503 and 'SECRET' not in response.text
        assert calls == 2


class Stream(httpx.AsyncByteStream):
    def __init__(self, chunks, close_error=False):
        self.chunks, self.closed, self.close_error = chunks, False, close_error
    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk
    async def aclose(self):
        self.closed = True
        if self.close_error:
            raise OSError('close failure')


def test_sse_byte_preservation_and_cleanup(connect):
    chunks = [b'data: {"model":"ods/shared","choices":[]}\n\n', b'data: [DONE]\n\n']
    stream = Stream(chunks)
    def handler(request):
        return (backend(request) if request.method == 'GET' else
                httpx.Response(200, stream=stream, headers={'content-type':'text/event-stream'}))
    with connect(handler) as (client, auth):
        response = client.post('/v1/chat/completions', json=body(stream=True), headers=auth)
        assert response.content == b''.join(chunks)
        assert stream.closed


def test_close_failure_still_releases_admission(connect):
    def handler(request):
        return (backend(request) if request.method == 'GET' else httpx.Response(503,stream=Stream([],True)))
    with connect(handler) as (client, auth):
        for _ in range(2):
            assert client.post('/v1/chat/completions', json=body(), headers=auth).status_code == 503


def test_rate_limit_is_per_device(connect, state):
    store, _ = state
    doc = store.load()
    doc['devices'][0]['requestsPerMinute'] = 1
    store.save(doc, expected_revision=doc['revision'])
    with connect() as (client, auth):
        assert client.get('/v1/models',headers=auth).status_code == 200
        assert client.get('/v1/models',headers=auth).status_code == 429


def test_total_deadline_cancels_pending_upstream(connect, state):
    store, _ = state
    doc = store.load()
    doc['devices'][0]['deadlineSeconds'] = 1
    store.save(doc, expected_revision=doc['revision'])
    cancelled = []
    async def handler(request):
        if request.method == 'GET':
            return backend(request)
        try:
            await asyncio.sleep(5)
        finally:
            cancelled.append(True)
    with connect(handler) as (client, auth):
        start = time.monotonic()
        assert client.post('/v1/chat/completions',json=body(),headers=auth).status_code == 504
        assert time.monotonic()-start < 3 and cancelled == [True]


def test_response_byte_cap_releases_upstream(connect, monkeypatch):
    monkeypatch.setattr(gateway, 'MAX_RESPONSE_BYTES', 32)
    stream = Stream([b'x'*33])
    def handler(request):
        return backend(request) if request.method == 'GET' else httpx.Response(200,stream=stream)
    with connect(handler) as (client, auth):
        assert client.post('/v1/chat/completions',json=body(),headers=auth).status_code == 502
        assert stream.closed


def test_pending_headers_cancel_on_revoke(connect, state):
    store, _ = state
    cancelled = []
    async def handler(request):
        if request.method == 'GET':
            return backend(request)
        doc = store.load()
        store.revoke(doc['devices'][0]['id'], expected_revision=doc['revision'])
        try:
            await asyncio.sleep(5)
        finally:
            cancelled.append(True)
    with connect(handler) as (client, auth):
        start = time.monotonic()
        assert client.post('/v1/chat/completions', json=body(), headers=auth).status_code == 401
        assert time.monotonic()-start < 2
        assert cancelled == [True]


@pytest.mark.parametrize('streaming', [False, True])
def test_asgi_disconnect_closes_upstream_and_releases_slot(state, streaming):
    async def exercise():
        closed = asyncio.Event()
        first_chunk = asyncio.Event()
        receiving = asyncio.Queue()

        class Hanging(httpx.AsyncByteStream):
            async def __aiter__(self):
                first_chunk.set()
                yield b'data: {"model":"ods/shared"}\n\n' if streaming else b'{'
                await asyncio.sleep(60)
            async def aclose(self):
                # An actual suspension exposes cancellation-scope leaks.
                await asyncio.sleep(0)
                closed.set()

        def handler(request):
            return backend(request) if request.method == 'GET' else httpx.Response(200,
                stream=Hanging(), headers={'content-type':'text/event-stream' if streaming else 'application/json'})
        store, token = state
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
            app = gateway.create_app(store, 'http://127.0.0.1:9099', upstream)
            async with app.router.lifespan_context(app):
                scope = {'type':'http','asgi':{'version':'3.0','spec_version':'2.3'},'http_version':'1.1',
                    'method':'POST','scheme':'http','path':'/v1/chat/completions','raw_path':b'/v1/chat/completions',
                    'query_string':b'','headers':[(b'authorization',('Bearer '+token).encode())],
                    'server':('127.0.0.1',8093),'client':('127.0.0.1',1000)}
                await receiving.put({'type':'http.request','body':json.dumps(body(stream=streaming)).encode(),'more_body':False})
                sent = []
                async def send(message):
                    sent.append(message)
                request_task = asyncio.create_task(app(scope, receiving.get, send))
                await asyncio.wait_for(first_chunk.wait(), 2)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://test') as probe:
                    assert (await probe.get('/v1/models',headers={'Authorization':'Bearer '+token})).status_code == 429
                await receiving.put({'type':'http.disconnect'})
                await asyncio.wait_for(request_task, 2)
                assert closed.is_set()
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://test') as probe:
                    # GET consumes the same per-device admission slot.
                    assert (await probe.get('/v1/models',headers={'Authorization':'Bearer '+token})).status_code == 200
    asyncio.run(exercise())


def test_stream_revocation_terminates_and_closes(connect, state):
    store, _ = state
    closed = []
    class Revoking(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"model":"ods/shared"}\n\n'
            doc = store.load()
            store.revoke(doc['devices'][0]['id'], expected_revision=doc['revision'])
            await asyncio.sleep(5)
            pytest.fail('revoked stream continued')
        async def aclose(self):
            await asyncio.sleep(0)
            closed.append(True)
    def handler(request):
        return backend(request) if request.method == 'GET' else httpx.Response(200,stream=Revoking(),
            headers={'content-type':'text/event-stream'})
    with connect(handler) as (client, auth):
        with pytest.raises(gateway.ShareError, match='credential_no_longer_valid'):
            client.post('/v1/chat/completions',json=body(stream=True),headers=auth)
    assert closed


def test_disconnect_before_stream_iterator_starts_still_cleans_up():
    async def exercise():
        cleaned = []
        async def iterator():
            pytest.fail('iterator should never start')
            yield b''
        async def cleanup():
            with gateway.anyio.CancelScope(shield=True):
                await asyncio.sleep(0)
                cleaned.append(True)
        async def receive():
            return {'type':'http.disconnect'}
        async def send(message):
            if message['type'] == 'http.response.start':
                await asyncio.sleep(5)
        response = gateway.OwnedStreamingResponse(iterator(), cleanup=cleanup)
        await asyncio.wait_for(response({'type':'http','asgi':{'spec_version':'2.3'}}, receive, send), 2)
        assert cleaned == [True]
    asyncio.run(exercise())
