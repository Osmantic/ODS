"""Concurrent authenticated Dashboard reads share expensive resource work."""
import asyncio
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from helpers import clear_dir_size_cache
from main import _cache, app
from routers import resources

HEADERS = {'Authorization': 'Bearer test-key-12345'}
URL = '/api/services/resources'
KEYS = ('service_resources_containers', 'service_resources_disk')


@pytest.fixture
def sources(tmp_path, monkeypatch):
    for key in KEYS:
        _cache.invalidate(key)
    clear_dir_size_cache()
    data = tmp_path / 'open-webui'
    data.mkdir()
    # Apparent-size accounting is the production contract; no large allocation.
    with (data / 'store.db').open('wb') as stream:
        stream.truncate(16 * 1024 * 1024)
    monkeypatch.setattr(resources, 'DATA_DIR', str(tmp_path))
    monkeypatch.setattr(resources, 'SERVICES', {'open-webui': {
        'name': 'WebUI', 'container_name': 'ods-webui',
    }})
    release = Event()
    original_scan = resources._scan_service_disk

    def scan():
        assert release.wait(10), 'test must release the disk collector'
        return original_scan()

    def stats(method, path, **kwargs):
        assert (method, path) == ('GET', '/v1/service/stats')
        assert release.wait(10), 'test must release the host receipt'
        return {'containers': [{'container_name': 'ods-webui',
                                'cpu_percent': 12.5, 'memory_used_mb': 128}]}

    disk = Mock(side_effect=scan)
    host = Mock(side_effect=stats)
    monkeypatch.setattr(resources, '_scan_service_disk', disk)
    monkeypatch.setattr(resources, 'request_agent_json', host)
    yield release, disk, host
    release.set()
    assert not resources._resource_refreshes
    for key in KEYS:
        _cache.invalidate(key)
    clear_dir_size_cache()


@pytest.mark.parametrize('cancel_count', [0, 1, 8])
def test_cold_wave_collects_once_even_when_clients_disconnect(sources, monkeypatch, cancel_count):
    release, disk, host = sources

    async def scenario():
        entered = set()
        all_entered = asyncio.Event()
        original_get = _cache.get

        def get(key):
            if key == KEYS[0]:
                entered.add(asyncio.current_task())
                if len(entered) >= 8:
                    all_entered.set()
            return original_get(key)

        monkeypatch.setattr(_cache, 'get', get)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            requests = [asyncio.create_task(client.get(URL, headers=HEADERS)) for _ in range(8)]
            try:
                await asyncio.wait_for(all_entered.wait(), 5)
                for request in requests[:cancel_count]:
                    request.cancel()
                await asyncio.gather(*requests[:cancel_count], return_exceptions=True)
                remaining = requests[cancel_count:]
                if not remaining:
                    remaining = [asyncio.create_task(client.get(URL, headers=HEADERS))]
            finally:
                release.set()
            responses = await asyncio.gather(*remaining)
            assert all(response.status_code == 200 for response in responses)
            assert all(response.json()['totals'] == {
                'cpu_percent': 12.5, 'memory_used_mb': 128, 'disk_data_gb': 0.02,
            } for response in responses)
            # A warm read must keep the independently cached full snapshots.
            assert (await client.get(URL, headers=HEADERS)).json() == responses[0].json()
            assert (host.call_count, disk.call_count) == (1, 1)

    asyncio.run(scenario())


def test_failed_collection_can_be_requested_again(sources, monkeypatch):
    release, disk, _host = sources
    release.set()
    original = disk.side_effect
    disk.side_effect = OSError('fixture disk unavailable')

    async def scenario():
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
            assert (await client.get(URL, headers=HEADERS)).status_code == 500
            disk.side_effect = original
            result = await client.get(URL, headers=HEADERS)
            assert result.status_code == 200
            assert result.json()['totals']['disk_data_gb'] == 0.02
            assert disk.call_count == 2

    asyncio.run(scenario())


def test_container_and_disk_ttls_remain_independent(sources, monkeypatch):
    release, disk, host = sources
    release.set()
    clock = [100.0]
    monkeypatch.setattr('main.time', SimpleNamespace(monotonic=lambda: clock[0]))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            for instant, counts in [(100, (1, 1)), (119, (1, 1)), (121, (2, 1)), (161, (3, 2))]:
                clock[0] = instant
                assert (await client.get(URL, headers=HEADERS)).status_code == 200
                assert (host.call_count, disk.call_count) == counts

    asyncio.run(scenario())


def test_pre_restart_collection_cannot_replace_the_new_snapshot(sources):
    release, _disk, host = sources
    release.set()
    old_release = Event()

    async def scenario():
        loop = asyncio.get_running_loop()
        old_started = asyncio.Event()
        reads = []

        def stats(method, path, **kwargs):
            if method == 'POST':
                assert path == '/v1/service/restart'
                return {'success': True}
            reads.append(path)
            cpu = 10 if len(reads) == 1 else 20
            if cpu == 10:
                loop.call_soon_threadsafe(old_started.set)
                assert old_release.wait(10)
            return {'containers': [{'container_name': 'ods-webui',
                                    'cpu_percent': cpu, 'memory_used_mb': 128}]}

        host.side_effect = stats
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            old = asyncio.create_task(client.get(URL, headers=HEADERS))
            try:
                await asyncio.wait_for(old_started.wait(), 5)
                restarted = await client.post('/api/services/open-webui/restart', headers=HEADERS)
                assert restarted.status_code == 200
                current = await asyncio.wait_for(client.get(URL, headers=HEADERS), 5)
                assert current.json()['totals']['cpu_percent'] == 20
            finally:
                old_release.set()
            assert (await old).json()['totals']['cpu_percent'] == 10
            warm = await client.get(URL, headers=HEADERS)
            assert warm.json()['totals']['cpu_percent'] == 20
            assert len(reads) == 2

    asyncio.run(scenario())
