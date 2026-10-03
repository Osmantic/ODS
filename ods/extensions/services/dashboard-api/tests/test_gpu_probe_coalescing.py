"""Concurrent dashboard clients should share one successful GPU discovery."""
import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from fastapi import FastAPI

from models import IndividualGPU
from routers import gpu


@pytest.fixture
def app(monkeypatch):
    application = FastAPI()
    application.include_router(gpu.router)
    monkeypatch.setattr(gpu, '_detailed_cache', {'expires': 0.0, 'value': None})
    monkeypatch.setattr(gpu, '_detailed_lock', asyncio.Lock(), raising=False)
    monkeypatch.setattr(gpu, 'decode_gpu_assignment', lambda: {})
    monkeypatch.setattr(gpu, '_live_env_value', lambda key: '')
    return application


def sample():
    return IndividualGPU(index=0, uuid='GPU-test', name='Test GPU', memory_used_mb=10,
                         memory_total_mb=100, memory_percent=10, utilization_percent=20,
                         temperature_c=40)


@pytest.mark.asyncio
@pytest.mark.parametrize('backend', ['nvidia', 'amd', 'apple'])
async def test_concurrent_authenticated_requests_share_one_probe(app, monkeypatch, backend):
    monkeypatch.setenv('GPU_BACKEND', backend)
    release = asyncio.Event()
    probe = Mock(return_value=[sample()])

    async def thread_call(function, *args):
        assert function is gpu._get_raw_gpus
        result = probe(*args)
        await release.wait()
        return result

    monkeypatch.setattr(gpu, 'asyncio', SimpleNamespace(to_thread=thread_call))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://ods') as client:
        tasks = [asyncio.create_task(client.get('/api/gpu/detailed', headers={'Authorization': 'Bearer test-key-12345'})) for _ in range(6)]
        await asyncio.sleep(0)
        release.set()
        responses = await asyncio.gather(*tasks)
        assert all(response.status_code == 200 for response in responses)
        assert all(response.json() == responses[0].json() for response in responses)
        assert probe.call_count == 1
        probe.assert_called_once_with(backend)
        assert (await client.get('/api/gpu/detailed')).status_code == 401
        assert probe.call_count == 1


@pytest.mark.asyncio
async def test_ttl_starts_after_slow_probe_and_failure_does_not_poison_cache(app, monkeypatch):
    clock = [100.0]
    calls = []
    monkeypatch.setattr(gpu, 'time', SimpleNamespace(monotonic=lambda: clock[0]))

    async def thread_call(function, *args):
        calls.append(args)
        clock[0] += 10
        return None if len(calls) == 1 else [sample()]

    monkeypatch.setattr(gpu, 'asyncio', SimpleNamespace(to_thread=thread_call))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://ods', headers={'Authorization': 'Bearer test-key-12345'}) as client:
        assert (await client.get('/api/gpu/detailed')).status_code == 503
        assert (await client.get('/api/gpu/detailed')).status_code == 200
        assert len(calls) == 2
        clock[0] += 2
        assert (await client.get('/api/gpu/detailed')).status_code == 200
        assert len(calls) == 2
        clock[0] += 2
        assert (await client.get('/api/gpu/detailed')).status_code == 200
        assert len(calls) == 3
