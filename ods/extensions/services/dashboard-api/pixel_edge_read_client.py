"""Lifespan-owned transport for read-only Portal edge queries."""
import asyncio
from contextlib import asynccontextmanager
from http.cookiejar import CookieJar, DefaultCookiePolicy

import httpx

_client: httpx.AsyncClient | None = None
_owner_loop: asyncio.AbstractEventLoop | None = None


class _NoCookies(DefaultCookiePolicy):
    def set_ok(self, cookie, request):
        return False

    def return_ok(self, cookie, request):
        return False


@asynccontextmanager
async def edge_read_client_lifespan():
    """No lazy/per-call fallback: the application owns creation and cleanup."""
    global _client, _owner_loop
    if _client is not None:
        raise RuntimeError('Portal edge read client already has an owner')
    async with httpx.AsyncClient(
        timeout=5.0, trust_env=False, follow_redirects=False,
        limits=httpx.Limits(max_connections=8, max_keepalive_connections=4, keepalive_expiry=30.0),
        cookies=CookieJar(policy=_NoCookies()),
    ) as client:
        _client, _owner_loop = client, asyncio.get_running_loop()
        try:
            yield client
        finally:
            _client, _owner_loop = None, None


def get_edge_read_client() -> httpx.AsyncClient:
    if _client is None or _client.is_closed or _owner_loop is not asyncio.get_running_loop():
        raise RuntimeError('Portal edge read client requires an active application lifespan')
    return _client


@asynccontextmanager
async def borrow_edge_read_client():
    # A request must release its response, never close the shared transport.
    yield get_edge_read_client()
