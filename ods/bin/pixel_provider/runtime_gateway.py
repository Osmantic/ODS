"""Turn-scoped OpenAI transport policy; Pixel alone owns tools/checkpoints.

Instances use an immutable owner-approved configuration and credentials. They
are never a public agent or administration endpoint. Terminal failure closes
the lease so client-library retries cannot multiply the provider attempt budget.
"""
import asyncio
import copy
import hmac
import ipaddress
import json
import socket
import ssl
import time
import uuid
from contextlib import suppress
from urllib.parse import urlsplit

import anyio
import httpx
from fastapi import FastAPI,Request
from fastapi.responses import JSONResponse,StreamingResponse

from .config import normalize_config
from .runtime_policy import select_candidates
from .store import MAX_BYTES,StoreError,decode_document

MODEL = 'ods/pixel'
MAX_RESPONSE = 2*1024*1024
TRANSIENT = {429,500,502,503,504}
FIELDS = {'model','messages','stream','stream_options','max_tokens','max_completion_tokens',
    'temperature','top_p','tools','tool_choice','parallel_tool_calls','response_format','seed','stop',
    'presence_penalty','frequency_penalty','logprobs','top_logprobs','user','n','reasoning_effort','chat_template_kwargs'}


class RuntimeErrorCode(ValueError):
    pass


def validate_request(payload):
    if (not isinstance(payload,dict) or set(payload)-FIELDS or payload.get('model') != MODEL
            or not isinstance(payload.get('messages'),list) or not payload['messages']
            or type(payload.get('stream',False)) is not bool or type(payload.get('n',1)) is not int
            or payload.get('n',1) != 1 or 'max_tokens' in payload and 'max_completion_tokens' in payload):
        raise RuntimeErrorCode('invalid-inference-request')
    for message in payload['messages']:
        if (not isinstance(message,dict) or set(message)-{'role','content','name','tool_call_id','tool_calls','function_call'}
                or message.get('role') not in ('system','developer','user','assistant','tool','function')):
            raise RuntimeErrorCode('invalid-messages')
        content = message.get('content')
        if isinstance(content,list):
            for part in content:
                if not isinstance(part,dict) or part.get('type') not in ('text','image_url'):
                    raise RuntimeErrorCode('unsupported-content')
                if part['type'] == 'image_url':
                    value = part.get('image_url')
                    url = value.get('url') if isinstance(value,dict) else None
                    if not isinstance(url,str) or not url.startswith(('data:image/png;base64,','data:image/jpeg;base64,','data:image/webp;base64,')):
                        raise RuntimeErrorCode('external-media-not-allowed')
        elif content is not None and not isinstance(content,str):
            raise RuntimeErrorCode('unsupported-content')
    if 'tools' in payload and (not isinstance(payload['tools'],list) or any(
            not isinstance(tool,dict) or tool.get('type') != 'function' for tool in payload['tools'])):
        raise RuntimeErrorCode('unsupported-tools')
    if 'chat_template_kwargs' in payload and (not isinstance(payload['chat_template_kwargs'],dict)
            or set(payload['chat_template_kwargs']) != {'enable_thinking'}
            or type(payload['chat_template_kwargs']['enable_thinking']) is not bool):
        raise RuntimeErrorCode('unsupported-template-options')
    return payload


async def pinned_target(provider):
    parts = urlsplit(provider['baseUrl'])
    port = parts.port or (443 if parts.scheme == 'https' else 80)
    addresses = await asyncio.get_running_loop().getaddrinfo(parts.hostname,port,type=socket.SOCK_STREAM)
    checked = []
    for item in addresses:
        address = ipaddress.ip_address(item[4][0])
        effective = getattr(address,'ipv4_mapped',None) or address
        if (effective.is_unspecified or effective.is_multicast or effective.is_link_local
                or parts.scheme == 'http' and not effective.is_loopback):
            raise RuntimeErrorCode('unsafe-provider-address')
        checked.append(address)
    if not checked:
        raise httpx.ConnectError('provider-unavailable')
    address = checked[0]
    host = '['+str(address)+']' if address.version == 6 else str(address)
    url = f'{parts.scheme}://{host}:{port}{parts.path}/chat/completions'
    headers = {'Host':parts.netloc,'Content-Type':'application/json','X-ODS-Pixel-Route-Hop':'1'}
    extensions = {'sni_hostname':parts.hostname} if parts.scheme == 'https' else {}
    return url,headers,extensions


class OwnedResponse(StreamingResponse):
    def __init__(self,content,cleanup,**kwargs):
        super().__init__(content,**kwargs)
        self.cleanup = cleanup
    async def __call__(self,scope,receive,send):
        try:
            await super().__call__(scope,receive,send)
        finally:
            await self.cleanup()


def create_app(config,credentials,token,*,events=None,client_factory=None):
    config = normalize_config(config)
    credentials = dict(credentials)
    events = events if events is not None else []
    app = FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
    active = False
    terminal = False
    cooldown = {}
    requests = 0

    def failure(code,status=400):
        # Non-retryable HTTP status, with a structured cause, intentionally
        # prevents an SDK from independently repeating an exhausted policy.
        return JSONResponse({'error':{'message':code,'type':'invalid_request_error','code':code}},
            status_code=status,headers={'Cache-Control':'no-store'})

    @app.post('/v1/chat/completions')
    async def complete(request:Request):
        nonlocal active,terminal,requests
        supplied = request.headers.getlist('authorization')
        if len(supplied) != 1 or not hmac.compare_digest(supplied[0],'Bearer '+token):
            return failure('invalid-runtime-credential',401)
        if request.headers.get('X-ODS-Pixel-Route-Hop'):
            return failure('provider-route-cycle',409)
        if terminal:
            return failure('provider-session-stopped',409)
        if active:
            return failure('provider-session-busy',409)
        if requests >= 128:
            terminal = True
            return failure('provider-session-call-limit',409)
        active = True
        requests += 1
        request_id = str(uuid.uuid4())
        deadline = time.monotonic()+config['policy']['deadlineSeconds']
        upstream = client = watcher = None
        transferred = response_phase = stream_finished = watcher_stopped = False

        def event(provider,result,**details):
            events.append(dict(requestId=request_id,revision=config['revision'],providerId=provider['id'],
                requestedModel=provider['model'],result=result,**details))

        async def cleanup():
            nonlocal active,upstream,client,watcher,terminal,watcher_stopped
            with anyio.CancelScope(shield=True):
                if transferred and not stream_finished:
                    terminal = True
                if watcher:
                    watcher_stopped = True
                    watcher.cancel()
                    with suppress(asyncio.CancelledError,Exception):
                        await watcher
                    watcher = None
                try:
                    if upstream:
                        with suppress(httpx.HTTPError,OSError):
                            await upstream.aclose()
                finally:
                    try:
                        if client:
                            await client.aclose()
                    finally:
                        active = False

        async def watch():
            while not watcher_stopped:
                if time.monotonic() >= deadline:
                    raise RuntimeErrorCode('provider-deadline')
                if not response_phase and await request.is_disconnected():
                    raise RuntimeErrorCode('client-disconnected')
                await asyncio.sleep(.05)

        async def guarded(awaitable):
            work = asyncio.ensure_future(awaitable)
            try:
                done,_ = await asyncio.wait((work,watcher),return_when=asyncio.FIRST_COMPLETED)
                if watcher in done:
                    if work.done() and not work.cancelled() and work.exception() is None:
                        orphan = work.result()
                        if isinstance(orphan,httpx.Response):
                            await orphan.aclose()
                    await watcher
                return await work
            finally:
                if not work.done():
                    work.cancel()
                    with suppress(asyncio.CancelledError):
                        await work

        try:
            body = bytearray()
            async with asyncio.timeout(min(10,config['policy']['deadlineSeconds'])):
                async for chunk in request.stream():
                    if len(body)+len(chunk)>MAX_BYTES:
                        raise RuntimeErrorCode('request-too-large')
                    body.extend(chunk)
            payload = validate_request(decode_document(bytes(body)))
            candidates,skipped = select_candidates(config,payload)
            events.extend(dict(requestId=request_id,revision=config['revision'],result='skipped',**item) for item in skipped)
            watcher = asyncio.create_task(watch())
            attempts = 0
            for provider in candidates:
                if cooldown.get(provider['id'],0)>time.monotonic():
                    event(provider,'cooldown'); continue
                attempts += 1
                event(provider,'attempt',attempt=attempts)
                try:
                    url,headers,extensions = await guarded(pinned_target(provider))
                    if credentials.get(provider['id']):
                        headers['Authorization'] = 'Bearer '+credentials[provider['id']]
                    outgoing = copy.deepcopy(payload)
                    outgoing['model'] = provider['model']
                    if 'max_tokens' not in outgoing and 'max_completion_tokens' not in outgoing:
                        leader = next(p for p in config['providers'] if p['id'] == config['roles']['leader'])
                        outgoing['max_tokens'] = min(1024,leader['maxOutputTokens'])
                    client = client_factory() if client_factory else httpx.AsyncClient(trust_env=False,follow_redirects=False,
                        timeout=httpx.Timeout(connect=5,read=None,write=10,pool=5),
                        limits=httpx.Limits(max_connections=1,max_keepalive_connections=0))
                    upstream = await guarded(client.send(client.build_request('POST',url,headers=headers,
                        json=outgoing,extensions=extensions),stream=True))
                    if upstream.status_code in TRANSIENT:
                        event(provider,'transient-failure',upstreamStatus=upstream.status_code)
                        cooldown[provider['id']] = time.monotonic()+30
                        await upstream.aclose(); upstream = None
                        await client.aclose(); client = None
                        await guarded(asyncio.sleep(min(.1*attempts,1)))
                        continue
                    if upstream.status_code != 200:
                        event(provider,'terminal-failure',upstreamStatus=upstream.status_code)
                        raise RuntimeErrorCode('provider-rejected-request')
                    iterator = upstream.aiter_bytes().__aiter__()
                    if payload.get('stream',False):
                        if not upstream.headers.get('content-type','').startswith('text/event-stream'):
                            raise RuntimeErrorCode('invalid-provider-stream')
                        first = await guarded(anext(iterator))
                        if not first or len(first)>MAX_RESPONSE:
                            raise RuntimeErrorCode('invalid-provider-stream')
                        event(provider,'stream-started')
                        async def stream(first=first,iterator=iterator,provider=provider):
                            nonlocal terminal,stream_finished
                            size = 0
                            tail = b''
                            done = False
                            try:
                                chunk = first
                                while True:
                                    size += len(chunk)
                                    if size>MAX_RESPONSE:
                                        raise RuntimeErrorCode('response-too-large')
                                    tail = (tail+chunk)[-MAX_RESPONSE:]
                                    done = done or any(line.strip()==b'data: [DONE]' for line in tail.splitlines())
                                    yield chunk
                                    try:
                                        chunk = await guarded(anext(iterator))
                                    except StopAsyncIteration:
                                        break
                                if not done:
                                    raise RuntimeErrorCode('provider-stream-interrupted')
                                stream_finished = True
                                event(provider,'completed')
                            except BaseException:
                                terminal = True
                                event(provider,'stream-interrupted')
                                raise
                        response_phase = True
                        transferred = True
                        return OwnedResponse(stream(),cleanup,media_type='text/event-stream',headers={
                            'Cache-Control':'no-store','X-ODS-Provider':provider['id'],
                            'X-ODS-Provider-Revision':str(config['revision']),'X-ODS-Request-Id':request_id})
                    raw = bytearray()
                    while True:
                        try:
                            chunk = await guarded(anext(iterator))
                        except StopAsyncIteration:
                            break
                        raw.extend(chunk)
                        if len(raw)>MAX_RESPONSE:
                            raise RuntimeErrorCode('response-too-large')
                    try:
                        result = json.loads(raw)
                        if (not isinstance(result,dict) or not isinstance(result.get('choices'),list)
                                or len(result['choices']) != 1 or not isinstance(result['choices'][0],dict)
                                or not isinstance(result['choices'][0].get('message'),dict)
                                or result['choices'][0]['message'].get('role') != 'assistant'):
                            raise ValueError()
                    except ValueError:
                        raise RuntimeErrorCode('invalid-provider-response') from None
                    event(provider,'completed',reportedModel=result.get('model') if isinstance(result.get('model'),str) else None)
                    return JSONResponse(result,headers={'Cache-Control':'no-store','X-ODS-Provider':provider['id'],
                        'X-ODS-Provider-Revision':str(config['revision']),'X-ODS-Request-Id':request_id})
                except (httpx.ConnectError,httpx.ConnectTimeout,httpx.ReadError,httpx.ReadTimeout,httpx.RemoteProtocolError,StopAsyncIteration) as error:
                    cause = error
                    while cause is not None:
                        if isinstance(cause,ssl.SSLError):
                            raise RuntimeErrorCode('provider-tls-verification-failed') from None
                        cause = cause.__cause__
                    event(provider,'transient-transport-failure')
                    cooldown[provider['id']] = time.monotonic()+30
                    if upstream:
                        await upstream.aclose(); upstream = None
                    if client:
                        await client.aclose(); client = None
                    await guarded(asyncio.sleep(min(.1*attempts,1)))
                    continue
            raise RuntimeErrorCode('provider-attempts-exhausted')
        except asyncio.CancelledError:
            terminal = True
            raise
        except (RuntimeErrorCode,StoreError) as error:
            terminal = True
            return failure(str(error))
        except (TimeoutError,httpx.HTTPError,OSError,ValueError):
            terminal = True
            return failure('provider-transport-failed')
        finally:
            if not transferred:
                await cleanup()

    return app
