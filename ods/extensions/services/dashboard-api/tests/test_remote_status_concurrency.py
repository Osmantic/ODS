"""HTTP status reads must not serialize independent health observations."""
import asyncio

import httpx
import pytest
from fastapi import FastAPI

from routers import remote_provider_status as rps


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel', [False, True])
async def test_status_starts_all_observations_before_waiting(monkeypatch, cancel):
    app = FastAPI()
    app.include_router(rps.router)
    started = {name: asyncio.Event() for name in ['activation', 'egress', 'ssh']}
    finished = set()
    release = asyncio.Event()
    activation = {'valid': False, 'proven': False, 'reason': 'consumer_drift'}
    egress = {'reachable': True, 'ready': True}
    ssh = {'ready': False, 'reason': 'not_configured'}
    async def observe(name, result):
        started[name].set()
        try:
            await release.wait()
            return result
        finally:
            finished.add(name)
    async def reconcile(value):
        assert value is activation
        return await observe('activation', value)
    async def egress_health():
        return await observe('egress', egress)
    async def ssh_health():
        return await observe('ssh', ssh)
    monkeypatch.setattr(rps, '_read_route_state', lambda: {'valid': True, 'enabled': True})
    monkeypatch.setattr(rps, '_read_activation', lambda: activation)
    monkeypatch.setattr(rps, '_reconcile_activation_with_host', reconcile)
    monkeypatch.setattr(rps, '_fetch_egress_health', egress_health)
    monkeypatch.setattr(rps, '_fetch_ssh_supervisor_status', ssh_health)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        request = asyncio.create_task(client.get('/api/remote-provider/status', headers={'Authorization': 'Bearer test-key-12345'}))
        try:
            await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started.values())), 1)
            assert not request.done()
            if cancel:
                request.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await request
                assert finished == set(started)
            else:
                release.set()
                response = await request
                assert response.status_code == 200
                body = response.json()
                assert body['activation'] == activation
                assert body['egress'] == egress
                assert body['sshSupervisor'] == ssh
                assert body['status'] == 'degraded'
                assert body['capabilities']['inference'] is False
        finally:
            release.set()
            if not request.done():
                await request
