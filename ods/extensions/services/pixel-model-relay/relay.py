"""Authenticated, Pixel-only host loopback bridge to the ODS model route.

Managed inference uses model-router as the dynamic model/swap authority.
Cloud and external-LLM installs have no managed router, so their fixed route
is the authenticated LiteLLM gateway. No caller can select a URL or endpoint.
"""

import asyncio
import hmac
import json
import logging
import os
import time
import uuid
from contextlib import suppress

from aiohttp import ClientSession, ClientTimeout, web

KEY = os.environ.get("PIXEL_MODEL_RELAY_KEY", "")
LITELLM_KEY = os.environ.get("LITELLM_KEY", "")


def _upstream_route(ods_mode, external_llm_url):
    if ods_mode == "cloud" or external_llm_url:
        return "http://litellm:4000", True
    return "http://model-router:9099", False


UPSTREAM, UPSTREAM_REQUIRES_KEY = _upstream_route(
    os.environ.get("ODS_MODE", "local"), os.environ.get("EXTERNAL_LLM_URL", ""))
ALIASES = {"ods/current", "default"}
MAX_BODY = 2 * 1024 * 1024
WRITE_TIMEOUT_SECONDS = 30.0  # Host-local OpenClaw must drain promptly.
LOG = logging.getLogger("pixel-model-relay")


def _generation_summary(payload):
    """Allowlist scalars only: never log prompts, tool schemas or credentials."""
    template = payload.get("chat_template_kwargs")
    thinking = template.get("enable_thinking") if isinstance(template, dict) else None
    budget = payload.get("max_tokens")
    completion_budget = payload.get("max_completion_tokens")
    tools = payload.get("tools")
    return {
        "stream": payload.get("stream") is True,
        "enable_thinking": thinking if type(thinking) is bool else None,
        "max_tokens": budget if type(budget) is int and 0 <= budget <= 10**9 else None,
        "max_completion_tokens": completion_budget if type(completion_budget) is int and 0 <= completion_budget <= 10**9 else None,
        "tool_count": len(tools) if isinstance(tools, list) else 0,
    }


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
    diagnostic_id = None
    if request.method == "POST":
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise web.HTTPBadRequest() from None
        if not isinstance(payload, dict) or payload.get("model") not in ALIASES:
            raise web.HTTPBadRequest()
        diagnostic_id = uuid.uuid4().hex
        LOG.info("generation_start %s", json.dumps({
            "id": diagnostic_id, **_generation_summary(payload)}))

    started = time.monotonic()
    first_chunk = None
    last_chunk = started
    max_gap = 0.0
    chunk_count = 0
    byte_count = 0
    async with ClientSession(timeout=ClientTimeout(total=None)) as client:
        upstream_headers = {"Content-Type": "application/json"}
        if UPSTREAM_REQUIRES_KEY:
            upstream_headers["Authorization"] = "Bearer " + LITELLM_KEY
        upstream_task = asyncio.create_task(client.request(
            request.method, UPSTREAM + request.path, data=body,
            headers=upstream_headers))
        disconnected = asyncio.create_task(_disconnect(request))
        try:
            done, _ = await asyncio.wait({upstream_task, disconnected}, return_when=asyncio.FIRST_COMPLETED)
            if disconnected in done:
                upstream_task.cancel()
                with suppress(asyncio.CancelledError):
                    await upstream_task
                return web.Response(status=499)
            upstream = await upstream_task
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
                    except StopAsyncIteration:
                        break
                    now = time.monotonic()
                    first_chunk = first_chunk if first_chunk is not None else now - started
                    max_gap = max(max_gap, now - last_chunk)
                    last_chunk = now
                    chunk_count += 1
                    byte_count += len(chunk)
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
            if diagnostic_id:
                LOG.info("generation_end %s", json.dumps({
                    "id": diagnostic_id, "seconds": round(time.monotonic() - started, 3),
                    "first_chunk_seconds": round(first_chunk, 3) if first_chunk is not None else None,
                    "max_chunk_gap_seconds": round(max_gap, 3),
                    "chunks": chunk_count, "bytes": byte_count}))
            disconnected.cancel()
            if not upstream_task.done():
                upstream_task.cancel()


async def _health(_request):
    return web.json_response({"status": "ok"})


def create_app():
    if not KEY or not KEY.isascii() or len(KEY) > 4096 \
            or any(ord(c) < 32 or ord(c) == 127 for c in KEY):
        raise RuntimeError("invalid Pixel model relay key")
    if UPSTREAM_REQUIRES_KEY and (
        not LITELLM_KEY or not LITELLM_KEY.isascii() or len(LITELLM_KEY) > 4096
        or any(ord(c) < 32 or ord(c) == 127 for c in LITELLM_KEY)
    ):
        raise RuntimeError("invalid LiteLLM model relay key")
    app = web.Application(client_max_size=MAX_BODY)
    app.router.add_get("/health", _health)
    app.router.add_route("*", "/v1/models", _inference)
    app.router.add_route("*", "/v1/chat/completions", _inference)
    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    LOG.setLevel(logging.INFO)
    web.run_app(create_app(), host="0.0.0.0", port=4102, print=None)
