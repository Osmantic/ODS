"""Authenticated, Pixel-only host loopback bridge to the ODS model router.

The router remains the single dynamic model/swap/telemetry authority. This
bridge exists because the shared LiteLLM proxy can retain inference after an
OpenClaw client disconnects. It cannot forward arbitrary URLs or endpoints.
"""

import asyncio
import hmac
import json
import os
from contextlib import suppress

from aiohttp import ClientError, ClientSession, ClientTimeout, web

KEY = os.environ.get("PIXEL_MODEL_RELAY_KEY", "")
UPSTREAM = "http://model-router:9099"
ALIASES = {"ods/current", "default"}
MAX_BODY = 2 * 1024 * 1024
WRITE_TIMEOUT_SECONDS = 30.0  # Host-local OpenClaw must drain promptly.


async def _disconnect(request):
    while request.transport is not None and not request.transport.is_closing():
        await asyncio.sleep(0.05)


async def _write(response, chunk):
    await asyncio.wait_for(response.write(chunk), timeout=WRITE_TIMEOUT_SECONDS)


async def _inference(request):
    if request.query_string or request.method not in {"GET", "POST"}:
        raise web.HTTPNotFound()
    if request.path == "/v1/models" and request.method != "GET":
        raise web.HTTPNotFound()
    if request.path == "/v1/chat/completions" and request.method != "POST":
        raise web.HTTPNotFound()
    if not hmac.compare_digest(request.headers.get("Authorization", ""), "Bearer " + KEY):
        raise web.HTTPUnauthorized()
    body = await request.read()
    if len(body) > MAX_BODY:
        raise web.HTTPRequestEntityTooLarge(max_size=MAX_BODY, actual_size=len(body))
    if request.method == "POST":
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise web.HTTPBadRequest() from None
        if not isinstance(payload, dict) or payload.get("model") not in ALIASES:
            raise web.HTTPBadRequest()

    async with ClientSession(timeout=ClientTimeout(total=None)) as client:
        data = body if request.method == "POST" else None
        headers = {"Content-Type": "application/json"} if data is not None else {}
        upstream_task = asyncio.create_task(client.request(
            request.method, UPSTREAM + request.path, data=data,
            headers=headers))
        disconnected = asyncio.create_task(_disconnect(request))
        try:
            done, _ = await asyncio.wait({upstream_task, disconnected}, return_when=asyncio.FIRST_COMPLETED)
            if disconnected in done:
                upstream_task.cancel()
                with suppress(asyncio.CancelledError):
                    await upstream_task
                return web.Response(status=499)
            try:
                upstream = await upstream_task
            except (ClientError, OSError, asyncio.TimeoutError):
                return web.json_response(
                    {"error": {"message": "upstream model router unavailable", "type": "bad_gateway", "code": "502"}},
                    status=502,
                )
            async with upstream:
                response = web.StreamResponse(status=upstream.status, headers={
                    "Content-Type": upstream.headers.get("Content-Type", "application/json"),
                    "Cache-Control": "no-store"})
                await response.prepare(request)
                iterator = upstream.content.iter_chunked(4096)
                while True:
                    chunk_task = asyncio.create_task(iterator.__anext__())
                    done, _ = await asyncio.wait({chunk_task, disconnected}, return_when=asyncio.FIRST_COMPLETED)
                    if disconnected in done:
                        chunk_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await chunk_task
                        break
                    try:
                        chunk = await chunk_task
                    except (StopAsyncIteration, ClientError):
                        break
                    try:
                        await _write(response, chunk)
                    except (asyncio.TimeoutError, ConnectionError, RuntimeError):
                        break
                upstream.close()
                if not disconnected.done() and request.transport is not None \
                        and not request.transport.is_closing():
                    with suppress(ConnectionError, RuntimeError):
                        await response.write_eof()
                return response
        finally:
            disconnected.cancel()
            if not upstream_task.done():
                upstream_task.cancel()


async def _health(_request):
    return web.json_response({"status": "ok"})


def create_app():
    if not KEY or not KEY.isascii() or len(KEY) > 4096 \
            or any(ord(c) < 32 or ord(c) == 127 for c in KEY):
        raise RuntimeError("invalid Pixel model relay key")
    app = web.Application(client_max_size=MAX_BODY)
    app.router.add_get("/health", _health)
    app.router.add_route("*", "/v1/models", _inference)
    app.router.add_route("*", "/v1/chat/completions", _inference)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=4102, print=None)
