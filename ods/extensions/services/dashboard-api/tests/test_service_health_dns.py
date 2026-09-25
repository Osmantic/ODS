"""Regression coverage for health fan-out starving foreground Pixel DNS."""
import asyncio
import socket
import threading

import pytest

from service_health_dns import ServiceHealthResolver


@pytest.mark.asyncio
async def test_resolver_uses_its_own_pool_and_preserves_numeric_addresses(monkeypatch):
    calls = []

    def lookup(host, port, family, socktype):
        calls.append((host, port, family, socktype, threading.current_thread().name))
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    resolver = ServiceHealthResolver()
    try:
        result = await resolver.resolve("service", 9595)
        assert result == [{
            "hostname": "service", "host": "127.0.0.1", "port": 9595,
            "family": socket.AF_INET, "proto": 6,
            "flags": socket.AI_NUMERICHOST | socket.AI_NUMERICSERV,
        }]
        assert calls[0][:4] == ("service", 9595, socket.AF_INET, socket.SOCK_STREAM)
        assert calls[0][4].startswith("ods-health-dns")
    finally:
        await resolver.close()


@pytest.mark.asyncio
async def test_ipv6_scope_is_preserved_without_another_loop_lookup(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args: [
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fe80::1", 80, 0, 4)),
    ])
    monkeypatch.setattr(socket, "getnameinfo", lambda *_args: ("fe80::1%4", "80"))
    resolver = ServiceHealthResolver()
    try:
        result = await resolver.resolve("service", 80, socket.AF_INET6)
        assert result[0]["host"] == "fe80::1%4"
        assert result[0]["family"] == socket.AF_INET6
    finally:
        await resolver.close()


@pytest.mark.asyncio
async def test_cancelled_request_keeps_native_lookup_slot_until_it_finishes():
    started = threading.Event()
    release = threading.Event()
    calls = []
    resolver = ServiceHealthResolver(workers=1)

    def lookup(host, *_args):
        calls.append(host)
        if host == "slow-service":
            started.set()
            release.wait(3)
        return []

    resolver._lookup = lookup
    first = asyncio.create_task(resolver.resolve("slow-service"))
    second = None
    try:
        assert await asyncio.to_thread(started.wait, 1)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(resolver.resolve("next-service"))
        await asyncio.sleep(0.02)
        assert calls == ["slow-service"]
        assert not second.done()
        # The foreground executor remains usable while health DNS is saturated.
        assert await asyncio.wait_for(asyncio.to_thread(lambda: "foreground"), 1) == "foreground"
        release.set()
        assert await asyncio.wait_for(second, 1) == []
        assert calls == ["slow-service", "next-service"]
    finally:
        release.set()
        for task in (first, second):
            if task is not None:
                task.cancel()
        await asyncio.gather(*(task for task in (first, second) if task is not None), return_exceptions=True)
        await resolver.close()


@pytest.mark.asyncio
async def test_dns_errors_propagate_and_close_prevents_new_work(monkeypatch):
    def fail(*_args):
        raise socket.gaierror(socket.EAI_NONAME, "not found")

    monkeypatch.setattr(socket, "getaddrinfo", fail)
    resolver = ServiceHealthResolver()
    with pytest.raises(socket.gaierror):
        await resolver.resolve("absent-service")
    await resolver.close()
    await resolver.close()
    with pytest.raises(RuntimeError, match="closed"):
        await resolver.resolve("service")


@pytest.mark.asyncio
async def test_shared_health_client_shutdown_closes_resolver(monkeypatch):
    import helpers

    monkeypatch.setattr(helpers, "_aio_session", None)
    monkeypatch.setattr(helpers, "_aio_session_lock", None)
    monkeypatch.setattr(helpers, "_health_resolver", None)
    session = await helpers._get_aio_session()
    resolver = helpers._health_resolver
    assert isinstance(resolver, ServiceHealthResolver)
    await helpers.shutdown_service_health_client()
    assert session.closed
    assert resolver._closed
    assert helpers._aio_session is None
    assert helpers._health_resolver is None
    await helpers.shutdown_service_health_client()
