"""Optional inference-only sharing perimeter for the existing ODS model router.

One process: per-device rate/concurrency limits are local admission controls,
not durable billing quotas. No arbitrary forwarding, agent or management API.
"""

import asyncio
import ipaddress
import json
import os
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import urlsplit

import anyio
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pixel_provider.sharing import PUBLIC_MODEL, SharingStore
from pixel_provider.store import MAX_BYTES, StoreError, decode_document

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_INFLIGHT = 8
POLL_SECONDS = 0.25
FIELDS = {'model', 'messages', 'stream', 'stream_options', 'max_tokens', 'max_completion_tokens',
          'temperature', 'top_p', 'tools', 'tool_choice', 'parallel_tool_calls', 'response_format',
          'seed', 'stop', 'presence_penalty', 'frequency_penalty', 'logprobs', 'top_logprobs',
          'user', 'n', 'reasoning_effort', 'chat_template_kwargs'}


class ShareError(ValueError):
    def __init__(self, status, code):
        self.status, self.code = status, code
        super().__init__(code)


class OwnedStreamingResponse(StreamingResponse):
    """Own resources even when disconnect precedes the first iterator step."""
    def __init__(self, content, *, cleanup, **kwargs):
        super().__init__(content, **kwargs)
        self.cleanup = cleanup

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self.cleanup()


def _failure(status, code):
    return JSONResponse({'error': {'message': code, 'type': code, 'code': str(status)}},
                        status_code=status, headers={'Cache-Control': 'no-store'})


def _router_url(value):
    url = urlsplit(value)
    try:
        loopback = ipaddress.ip_address(url.hostname or '').is_loopback
    except ValueError:
        loopback = url.hostname == 'localhost'
    if (url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password
            or url.query or url.fragment or url.path not in ('', '/')
            or url.scheme == 'http' and not (loopback or url.hostname == 'model-router')):
        raise ValueError('invalid-router-origin')
    return value.rstrip('/')


def _prepare(payload, grant):
    if (not isinstance(payload, dict) or set(payload) - FIELDS or payload.get('model') != PUBLIC_MODEL
            or not isinstance(payload.get('messages'), list) or not payload['messages']
            or type(payload.get('stream', False)) is not bool
            or type(payload.get('n', 1)) is not int or payload.get('n', 1) != 1
            or 'max_tokens' in payload and 'max_completion_tokens' in payload):
        raise ShareError(400, 'unsupported_inference_request')
    # Inline images are permitted; inference-only access must not cause the
    # backend to fetch arbitrary URLs, local files or instance metadata.
    for message in payload['messages']:
        if (not isinstance(message, dict)
                or set(message) - {'role', 'content', 'name', 'tool_call_id', 'tool_calls', 'function_call'}
                or message.get('role') not in ('system', 'developer', 'user', 'assistant', 'tool', 'function')):
            raise ShareError(400, 'invalid_messages')
        content = message.get('content')
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict) or part.get('type') not in ('text', 'image_url'):
                    raise ShareError(400, 'unsupported_content')
                if part['type'] == 'image_url':
                    url = (part.get('image_url') or {}).get('url') if isinstance(part.get('image_url'), dict) else None
                    if not isinstance(url, str) or not url.startswith(('data:image/png;base64,', 'data:image/jpeg;base64,', 'data:image/webp;base64,')):
                        raise ShareError(400, 'external_media_not_allowed')
        elif content is not None and not isinstance(content, str):
            raise ShareError(400, 'unsupported_content')
    if 'tools' in payload and (not isinstance(payload['tools'], list) or any(
            not isinstance(tool, dict) or tool.get('type') != 'function' for tool in payload['tools'])):
        raise ShareError(400, 'unsupported_tools')
    if 'chat_template_kwargs' in payload and (
            not isinstance(payload['chat_template_kwargs'], dict)
            or set(payload['chat_template_kwargs']) != {'enable_thinking'}
            or type(payload['chat_template_kwargs']['enable_thinking']) is not bool):
        raise ShareError(400, 'unsupported_template_options')
    token_field = 'max_completion_tokens' if 'max_completion_tokens' in payload else 'max_tokens'
    limit = payload.get(token_field, min(1024, grant['maxOutputTokens']))
    if type(limit) is not int or not 1 <= limit <= grant['maxOutputTokens']:
        raise ShareError(400, 'output_limit_exceeded')
    result = dict(payload)
    result[token_field] = limit
    return result


async def _guarded(awaitable, watcher):
    work = asyncio.ensure_future(awaitable)
    try:
        done, _ = await asyncio.wait((work, watcher), return_when=asyncio.FIRST_COMPLETED)
        if watcher in done:
            if work.done() and not work.cancelled() and work.exception() is None:
                orphan = work.result()
                if isinstance(orphan, httpx.Response):
                    await orphan.aclose()
            await watcher
        return await work
    finally:
        if not work.done():
            work.cancel()
            with suppress(asyncio.CancelledError):
                await work


def create_app(store=None, router_url=None, client=None):
    storage = store or SharingStore(Path(os.environ.get('ODS_INFERENCE_STATE_DIR', '/state')))
    origin = _router_url(router_url or os.environ.get('ODS_INFERENCE_ROUTER_URL', 'http://model-router:9099'))
    active = defaultdict(int)
    recent = defaultdict(deque)
    lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(app):
        app.state.http = client or httpx.AsyncClient(base_url=origin, trust_env=False, follow_redirects=False,
            timeout=httpx.Timeout(connect=5, read=None, write=10, pool=5),
                limits=httpx.Limits(max_connections=MAX_INFLIGHT, max_keepalive_connections=MAX_INFLIGHT))
        try:
            yield
        finally:
            if client is None:
                await app.state.http.aclose()

    app = FastAPI(title='ODS Inference Sharing', lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)

    async def authorize(request):
        values = request.headers.getlist('authorization')
        if len(values) != 1 or not values[0].startswith('Bearer '):
            raise ShareError(401, 'invalid_credential')
        token = values[0][7:]
        try:
            return token, await asyncio.to_thread(storage.authenticate, token)
        except StoreError as error:
            raise ShareError(401 if error.code == 'invalid-credential' else 503,
                             'invalid_credential' if error.code == 'invalid-credential' else 'sharing_unavailable') from None

    @app.get('/health')
    async def health():
        return {'status': 'ok', 'surface': 'inference-only'}

    @app.api_route('/{path:path}', methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'HEAD', 'CONNECT'])
    async def inference(path, request: Request):
        if (request.method, path) not in (('GET', 'v1/models'), ('POST', 'v1/chat/completions')):
            return _failure(404, 'not_forwarded')
        if request.url.query:
            return _failure(400, 'query_not_allowed')
        watcher = upstream = None
        reserved = False
        grant = None
        transferred = False
        response_phase = [False]
        watcher_stopped = False

        async def cleanup():
            nonlocal reserved, watcher_stopped
            # Starlette cancels the streaming task on disconnect. Shield its
            # cleanup so the socket and admission slot cannot survive that turn.
            with anyio.CancelScope(shield=True):
                try:
                    if watcher is not None:
                        watcher_stopped = True
                        watcher.cancel()
                        with suppress(asyncio.CancelledError, ShareError):
                            await watcher
                finally:
                    try:
                        if upstream is not None:
                            with suppress(httpx.HTTPError, OSError):
                                await upstream.aclose()
                    finally:
                        if reserved:
                            async with lock:
                                active[grant['id']] -= 1
                                if active[grant['id']] == 0:
                                    active.pop(grant['id'])
                                reserved = False
        try:
            token, grant = await authorize(request)
            started = time.monotonic()
            async with lock:
                for device_id, history in list(recent.items()):
                    while history and history[0] <= started - 60:
                        history.popleft()
                    if not history and not active.get(device_id):
                        recent.pop(device_id)
                times = recent[grant['id']]
                while times and times[0] <= started - 60:
                    times.popleft()
                if (active[grant['id']] >= grant['maxConcurrent'] or sum(active.values()) >= MAX_INFLIGHT
                        or len(times) >= grant['requestsPerMinute']):
                    raise ShareError(429, 'device_limit_reached')
                times.append(started)
                active[grant['id']] += 1
                reserved = True

            async def watch():
                while not watcher_stopped:
                    await asyncio.sleep(POLL_SECONDS)
                    if time.monotonic() - started >= grant['deadlineSeconds']:
                        raise ShareError(504, 'inference_deadline')
                    if not response_phase[0] and await request.is_disconnected():
                        raise ShareError(499, 'client_disconnected')
                    try:
                        fresh = await asyncio.to_thread(storage.authenticate, token)
                    except StoreError:
                        raise ShareError(401, 'credential_no_longer_valid') from None
                    if fresh != grant:
                        raise ShareError(409, 'grant_changed')

            # Read the bounded body before polling ASGI disconnect messages.
            payload = None
            if request.method == 'POST':
                raw = bytearray()
                async with asyncio.timeout(min(10, grant['deadlineSeconds'])):
                    async for chunk in request.stream():
                        if len(raw) + len(chunk) > MAX_BYTES:
                            raise ShareError(413, 'request_too_large')
                        raw.extend(chunk)
                payload = _prepare(decode_document(bytes(raw)), grant)
            watcher = asyncio.create_task(watch())
            route_response = await _guarded(app.state.http.get(origin + '/v1/models'), watcher)
            if route_response.status_code != 200:
                raise ShareError(503, 'model_router_unavailable')
            try:
                metadata = route_response.json()['ods']
                if (metadata['catalogId'] != grant['catalogId'] or metadata['routedModel'] != grant['runtimeModelId']
                        or type(metadata['routeSeq']) is not int or not 0 <= metadata['routeSeq'] <= 2**53 - 1):
                    raise ShareError(409, 'shared_model_not_active')
            except (KeyError, TypeError, ValueError):
                raise ShareError(409, 'shared_model_not_active') from None
            # Re-authorize immediately before forwarding, not only on a timer.
            _, fresh = await authorize(request)
            if fresh != grant:
                raise ShareError(409, 'grant_changed')
            if request.method == 'GET':
                return JSONResponse({'object': 'list', 'data': [{'id': PUBLIC_MODEL, 'object': 'model', 'owned_by': 'ods'}],
                    'ods': {'catalogId': grant['catalogId'], 'routedModel': grant['runtimeModelId'],
                            'identitySource': 'ods-verified-route', 'routeSeq': metadata['routeSeq'],
                            'contextLength': metadata.get('contextLength'), 'capabilities': metadata.get('capabilities'),
                            'maxOutputTokens': grant['maxOutputTokens'], 'expiresAt': grant['expiresAt'],
                            'execution': 'client-owned'}}, headers={'Cache-Control': 'no-store'})

            headers = {'Content-Type': 'application/json', 'X-ODS-Expected-Catalog': grant['catalogId'],
                       'X-ODS-Expected-Model': grant['runtimeModelId'], 'X-ODS-Expected-Route': str(metadata['routeSeq'])}
            outgoing = app.state.http.build_request('POST', origin + '/v1/chat/completions',
                headers=headers, content=json.dumps(payload, allow_nan=False).encode('utf-8'))
            upstream = await _guarded(app.state.http.send(outgoing, stream=True), watcher)
            if upstream.status_code != 200:
                raise ShareError(upstream.status_code if upstream.status_code in (400, 409, 429, 502, 503, 504) else 502,
                                 'model_inference_failed')
            public_headers = {'Cache-Control': 'no-store', 'X-ODS-Share-Request-Id': str(uuid.uuid4())}
            for name in ('X-ODS-Request-Id', 'X-ODS-Routed-Model', 'X-ODS-Route-Seq', 'X-ODS-Backend'):
                if name in upstream.headers:
                    public_headers[name] = upstream.headers[name]
            async def chunks():
                size = 0
                iterator = upstream.aiter_bytes().__aiter__()
                while True:
                    try:
                        chunk = await _guarded(anext(iterator), watcher)
                    except StopAsyncIteration:
                        break
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise ShareError(502, 'response_too_large')
                    yield chunk

            if payload.get('stream', False):
                if not upstream.headers.get('content-type', '').lower().startswith('text/event-stream'):
                    raise ShareError(502, 'invalid_stream_response')
                response_phase[0] = True  # StreamingResponse now owns disconnect reception.
                transferred = True
                return OwnedStreamingResponse(chunks(), cleanup=cleanup,
                    media_type='text/event-stream', headers=public_headers)
            raw_response = bytearray()
            async for chunk in chunks():
                raw_response.extend(chunk)
            try:
                result = json.loads(bytes(raw_response))
                if not isinstance(result, dict) or result.get('model') != PUBLIC_MODEL:
                    raise ValueError('invalid response')
            except (ValueError, TypeError):
                raise ShareError(502, 'invalid_model_response') from None
            return JSONResponse(result, headers=public_headers)
        except ShareError as error:
            return _failure(error.status, error.code)
        except StoreError:
            return _failure(400, 'invalid_request')
        except (TimeoutError, httpx.TimeoutException):
            return _failure(504, 'inference_deadline')
        except (httpx.HTTPError, ValueError, OSError):
            return _failure(502, 'inference_unavailable')
        finally:
            if not transferred:
                await cleanup()

    return app


app = create_app()
