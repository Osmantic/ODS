"""Read-only edge polls reuse transport, never availability or credentials."""
import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from routers import pixel


@pytest.mark.asyncio
@pytest.mark.parametrize('read_identity', [False, True])
async def test_status_reuses_connection_across_normal_poll_interval(monkeypatch, read_identity):
    from test_pixel_runtime_identity import observed

    connections = []
    writers = []
    requests = []
    identity = observed()
    config = {'key': 'test-key'}

    async def reply(reader, writer):
        connections.append(writer)
        writers.append(writer)
        try:
            while True:
                headers = (await reader.readuntil(b'\r\n\r\n')).decode('ascii')
                requests.append(headers)
                body = json.dumps(identity if headers.startswith('GET /v1/runtime-identity ')
                                  else {'data': [{'id': 'portal/default'}]}).encode()
                writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: '
                             + str(len(body)).encode() + b'\r\n\r\n' + body)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(reply, '127.0.0.1', 0)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setattr(pixel, '_pixel_config', lambda: (f'http://127.0.0.1:{port}', config['key']))
    monkeypatch.setattr(pixel, '_host_model_status', AsyncMock(return_value={}))
    monkeypatch.setattr(pixel, '_model_readiness_issue_for_status', AsyncMock(return_value=None))
    monkeypatch.setattr(pixel, '_local_inference_issue', AsyncMock(return_value=None))
    monkeypatch.setattr(pixel, '_active_runtime_projection', lambda value: {'source': 'remote-provider', 'model': 'cloud'})
    monkeypatch.setattr(pixel, '_model_support_from_status', lambda value: None)
    if not read_identity:
        monkeypatch.setattr(pixel, '_current_runtime_identity', AsyncMock(return_value=pixel.unknown_runtime_identity()))
    monkeypatch.setattr(pixel, '_current_access_readiness', AsyncMock(return_value=(None, 'access-probe-unavailable')))
    try:
        async with httpx.AsyncClient(trust_env=False, limits=httpx.Limits(keepalive_expiry=30)) as client:
            monkeypatch.setattr(pixel, 'get_edge_read_client', lambda: client, raising=False)
            first = await pixel.pixel_status()
            assert first['available'] is True
            config['key'] = 'rotated-key'
            await asyncio.sleep(5.1)
            second = await pixel.pixel_status()
            assert second['available'] is True
            assert len(connections) == 1
            if read_identity:
                assert first['runtimeIdentity'] == second['runtimeIdentity'] == identity
                assert [request.split(' ')[1] for request in requests] == ['/v1/models', '/v1/runtime-identity'] * 2
                assert all('Authorization: Bearer test-key\r\n' in request for request in requests[:2])
                assert all('Authorization: Bearer rotated-key\r\n' in request for request in requests[2:])
            server.close()
            for writer in writers:
                writer.close()
                await writer.wait_closed()
            await server.wait_closed()
            # A reused connection is transport only: a stopped edge still fails.
            assert (await pixel.pixel_status())['available'] is False
    finally:
        server.close()
        for writer in writers:
            writer.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_lifespan_required_owned_by_loop_and_fresh_after_shutdown(monkeypatch):
    import pixel_edge_read_client as pool

    with pytest.raises(RuntimeError, match='active application lifespan'):
        pool.get_edge_read_client()
    async with pool.edge_read_client_lifespan() as first:
        assert pool.get_edge_read_client() is first
        async with pool.borrow_edge_read_client() as borrowed:
            assert borrowed is first
        assert not first.is_closed
        with pytest.raises(RuntimeError, match='already has an owner'):
            async with pool.edge_read_client_lifespan():
                pass
        with monkeypatch.context() as patch:
            patch.setattr(pool, '_owner_loop', object())
            with pytest.raises(RuntimeError, match='active application lifespan'):
                pool.get_edge_read_client()
    assert first.is_closed
    with pytest.raises(RuntimeError, match='active application lifespan'):
        pool.get_edge_read_client()
    async with pool.edge_read_client_lifespan() as second:
        assert second is not first
    assert second.is_closed


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['status', 'context'])
async def test_auth_rotates_and_pool_never_retains_cookies_or_credentials(monkeypatch, operation):
    import pixel_edge_read_client as pool

    seen = []
    settings = []
    async def handle(request):
        seen.append(request)
        body = {'data': [{'id': 'portal/default'}]} if operation == 'status' else {'ok': True}
        return httpx.Response(200, json=body, headers={'set-cookie': 'credential=private; Path=/'})
    real_factory = httpx.AsyncClient
    def factory(**kwargs):
        settings.append(kwargs)
        return real_factory(transport=httpx.MockTransport(handle), **kwargs)
    monkeypatch.setattr(pool.httpx, 'AsyncClient', factory)
    monkeypatch.setattr(pixel, 'public_context', lambda payload: payload)
    monkeypatch.setattr(pixel, '_host_model_status', AsyncMock(return_value={}))
    monkeypatch.setattr(pixel, '_model_readiness_issue_for_status', AsyncMock(return_value=None))
    monkeypatch.setattr(pixel, '_local_inference_issue', AsyncMock(return_value=None))
    monkeypatch.setattr(pixel, '_active_runtime_projection', lambda value: {'source': 'remote-provider', 'model': 'cloud'})
    monkeypatch.setattr(pixel, '_model_support_from_status', lambda value: None)
    monkeypatch.setattr(pixel, '_current_runtime_identity', AsyncMock(return_value=pixel.unknown_runtime_identity()))
    monkeypatch.setattr(pixel, '_current_access_readiness', AsyncMock(return_value=(None, 'access-probe-unavailable')))
    async with pool.edge_read_client_lifespan() as client:
        for key in ('first-key', 'rotated-key'):
            monkeypatch.setattr(pixel, '_pixel_config', lambda: ('http://pixel-edge:9595', key))
            if operation == 'status':
                assert (await pixel.pixel_status())['available'] is True
            else:
                assert await pixel.pixel_chat_context(pixel.ChatCancelRequest(chat_id='test-chat')) == {'ok': True}
        assert len(client.cookies) == 0
        assert 'authorization' not in client.headers
        assert client.auth is None
    assert len(settings) == 1
    limits = settings[0]['limits']
    assert (limits.max_connections, limits.max_keepalive_connections, limits.keepalive_expiry) == (8, 4, 30)
    assert settings[0]['trust_env'] is False and settings[0]['follow_redirects'] is False
    assert [request.headers['authorization'] for request in seen] == ['Bearer first-key', 'Bearer rotated-key']
    assert all('cookie' not in request.headers for request in seen)
    expected_timeout = ({'connect': 5.0, 'read': 5.0, 'write': 5.0, 'pool': 5.0} if operation == 'status'
                        else {'connect': 3.0, 'read': 20.0, 'write': 5.0, 'pool': 3.0})
    assert all(request.extensions['timeout'] == expected_timeout for request in seen)


@pytest.mark.asyncio
async def test_cancellation_releases_request_without_closing_pool_and_no_failure_retry(monkeypatch):
    import pixel_edge_read_client as pool

    entered = asyncio.Event()
    calls = 0
    async def handle(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await asyncio.Event().wait()
        raise httpx.ConnectTimeout('test failure')
    real_factory = httpx.AsyncClient
    monkeypatch.setattr(pool.httpx, 'AsyncClient', lambda **kw: real_factory(transport=httpx.MockTransport(handle), **kw))
    monkeypatch.setattr(pixel, '_pixel_config', lambda: ('http://pixel-edge:9595', 'key'))
    async with pool.edge_read_client_lifespan() as client:
        task = asyncio.create_task(pixel.pixel_chat_context(pixel.ChatCancelRequest(chat_id='test-chat')))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not client.is_closed
        with pytest.raises(pixel.HTTPException) as error:
            await pixel.pixel_chat_context(pixel.ChatCancelRequest(chat_id='test-chat'))
        assert error.value.status_code == 503
        assert calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['startup', 'body', 'cleanup', 'none'])
async def test_application_lifespan_closes_pool_during_startup_and_shutdown_failure(monkeypatch, failure):
    import main
    import pixel_edge_read_client as pool

    made = []
    factory = httpx.AsyncClient
    def create(**kwargs):
        if len(made) == 1 and failure == 'startup':
            raise RuntimeError('startup')
        client = factory(**kwargs)
        made.append(client)
        return client
    monkeypatch.setattr(main.httpx, 'AsyncClient', create)
    for name in ('collect_metrics', '_poll_service_health', 'shutdown_agent_clients', 'shutdown_llm_client'):
        monkeypatch.setattr(main, name, AsyncMock())
    monkeypatch.setattr(main.gpu_router, 'poll_gpu_history', AsyncMock())
    monkeypatch.setattr(main, 'shutdown_service_health_client', AsyncMock(side_effect=RuntimeError('cleanup') if failure == 'cleanup' else None))
    async def run():
        async with main._lifespan(main.app):
            assert pool.get_edge_read_client() is made[0]
            if failure == 'body':
                raise RuntimeError('body')
    if failure == 'none':
        await run()
    else:
        with pytest.raises(RuntimeError, match=failure):
            await run()
    assert all(client.is_closed for client in made)
    with pytest.raises(RuntimeError, match='active application lifespan'):
        pool.get_edge_read_client()
