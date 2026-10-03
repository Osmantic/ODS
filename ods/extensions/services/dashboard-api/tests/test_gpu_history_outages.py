"""GPU history must advance through telemetry outages instead of freezing."""
import asyncio
from collections import deque
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from fastapi import FastAPI

from models import IndividualGPU
from routers import gpu


@pytest.mark.asyncio
@pytest.mark.parametrize('outage_length,recover', [(2, False), (2, True), (60, False)])
async def test_poll_outages_advance_history_and_age_out_old_metrics(monkeypatch, outage_length, recover):
    card = IndividualGPU(index=0, uuid='test', name='GPU', memory_used_mb=10,
                         memory_total_mb=100, memory_percent=10,
                         utilization_percent=0, temperature_c=40)
    results = [[card], None, OSError('probe disconnected')] + [None] * (outage_length - 2)
    if recover:
        results.append([card])
    probe = Mock(side_effect=results)
    ticks = 0

    async def next_tick(seconds):
        nonlocal ticks
        assert seconds == gpu._HISTORY_POLL_INTERVAL
        ticks += 1
        if ticks == len(results):
            raise asyncio.CancelledError

    monkeypatch.setattr(gpu, '_GPU_HISTORY', deque(maxlen=60))
    monkeypatch.setattr(gpu, '_get_raw_gpus', probe)
    monkeypatch.setattr(gpu, 'asyncio', SimpleNamespace(to_thread=asyncio.to_thread, sleep=next_tick))
    with pytest.raises(asyncio.CancelledError):
        await gpu.poll_gpu_history()

    application = FastAPI()
    application.include_router(gpu.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url='http://ods') as client:
        assert (await client.get('/api/gpu/history')).status_code == 401
        response = await client.get('/api/gpu/history', headers={'Authorization': 'Bearer test-key-12345'})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload['timestamps']) == min(60, len(results))
    if outage_length == 2:
        assert payload['gpus']['0']['utilization'] == [0, None, None] + ([0] if recover else [])
        assert payload['gpus']['0']['memory_percent'] == [10, None, None] + ([10] if recover else [])
        assert payload['gpus']['0']['temperature'] == [40, None, None] + ([40] if recover else [])
    else:
        assert payload['gpus'] == {}
