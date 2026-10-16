"""An enable response must hand current observations to the next Library read."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

import helpers
from host_agent_client import AgentClientError
from models import ServiceStatus
from routers import extensions
from test_extensions import _make_catalog_ext, _patch_extensions_config, _patch_mutation_config


def status(service_id, value):
    return ServiceStatus(id=service_id, name=service_id, port=5678,
                         external_port=5678, status=value)


@pytest.fixture
def enable_health(monkeypatch, tmp_path):
    catalog = [{**_make_catalog_ext('n8n', 'n8n'), 'catalog_source': 'builtin'}]
    _patch_extensions_config(monkeypatch, catalog, tmp_path=tmp_path)
    _patch_mutation_config(monkeypatch, tmp_path)
    monkeypatch.setattr(extensions, '_check_agent_health', lambda: True)
    builtin = tmp_path / 'builtin' / 'n8n'
    builtin.mkdir(parents=True)
    (builtin / 'compose.yaml.disabled').write_text('services: {n8n: {image: n8n}}\n')
    monkeypatch.setattr(extensions, '_scan_installed_compose', lambda *a, **k: None)
    monkeypatch.setattr(extensions, '_refuse_missing_owner_configuration', lambda *a, **k: None)
    config = {'name': 'n8n', 'port': 5678, 'external_port': 5678,
              'type': 'docker', 'container_name': 'ods-n8n'}
    monkeypatch.setattr(helpers, 'SERVICES', {'unrelated': {'name': 'unrelated', 'port': 5678}})
    monkeypatch.setattr(helpers, 'load_extension_manifests', lambda *a, **k: ({'n8n': config}, [], []))
    monkeypatch.setattr(helpers, 'LLM_BACKEND', 'llama')
    monkeypatch.setattr(helpers, '_services_cache', None)
    monkeypatch.setattr(helpers, '_services_cache_revision', 0)
    return builtin


def read_library(client):
    catalog = client.get('/api/extensions/catalog', headers=client.auth_headers)
    detail = client.get('/api/extensions/n8n', headers=client.auth_headers)
    assert catalog.status_code == detail.status_code == 200
    return [next(row for row in catalog.json()['extensions'] if row['id'] == 'n8n'), detail.json()]


@pytest.mark.parametrize('prior', ['missing', 'down'])
@pytest.mark.parametrize('probe,state,health,expected', [
    ('down', 'running', 'starting', 'installing'),
    ('healthy', 'running', 'healthy', 'enabled'),
    ('down', 'running', 'unhealthy', 'unhealthy'),
    ('unhealthy', 'running', 'starting', 'unhealthy'),
    ('down', 'exited', 'starting', 'stopped'),
    ('down', 'running', None, 'stopped'),  # Unavailable host cannot prove startup.
])
def test_enable_refreshes_absent_or_stale_row_before_catalog(
        enable_health, monkeypatch, tmp_path, test_client, prior, probe, state, health, expected):
    unrelated = status('unrelated', 'healthy')
    stale = [unrelated] + ([] if prior == 'missing' else [status('n8n', 'down')])
    helpers.set_services_cache(stale)
    old_revision = helpers.get_services_cache_revision()
    probe_mock = AsyncMock(return_value=status('n8n', probe))
    monkeypatch.setattr(helpers, 'check_service_health', probe_mock)
    host = AsyncMock(return_value={'schema_version': 'ods.host-service-health.v1', 'containers': [{
        'service_id': 'n8n', 'container_name': 'ods-n8n', 'state': state, 'health': health,
    }]})
    if health is None:
        host.side_effect = AgentClientError('fixture host unavailable')
    monkeypatch.setattr(helpers, 'request_agent_json', host)

    response = test_client.post('/api/extensions/n8n/enable', headers=test_client.auth_headers)
    assert response.status_code == 200
    assert response.json()['failed_services'] == []
    assert enable_health.joinpath('compose.yaml').is_file()
    assert not (tmp_path / 'extension-progress/n8n.json').exists()  # No fabricated grace.
    assert probe_mock.await_count == 1
    assert probe_mock.await_args.args[0] == 'n8n'  # No unrelated service fan-out.
    assert probe_mock.await_args.kwargs['timeout'].total == 5
    assert helpers.set_services_cache(stale, expected_revision=old_revision) is False
    assert next(row for row in helpers.get_cached_services() if row.id == 'unrelated') == unrelated
    assert len([row for row in helpers.get_cached_services() if row.id == 'n8n']) == 1
    for row in read_library(test_client):
        assert row['status'] == expected
        assert row['runtime_starting'] is (expected == 'installing')


@pytest.mark.asyncio
async def test_refresh_fences_polls_before_and_during_probe_and_preserves_other_owner(
        enable_health, monkeypatch):
    helpers.set_services_cache([status('unrelated', 'down')])
    before_revision = helpers.get_services_cache_revision()
    started, finish = asyncio.Event(), asyncio.Event()

    async def probe(*args, **kwargs):
        started.set()
        await finish.wait()
        return status('n8n', 'healthy')

    monkeypatch.setattr(helpers, 'check_service_health', probe)
    task = asyncio.create_task(helpers.refresh_cached_builtin_services(['n8n']))
    await started.wait()
    during_revision = helpers.get_services_cache_revision()
    assert helpers.set_services_cache([], expected_revision=before_revision) is False
    # A concurrent *different* owner refresh must not be overwritten by our merge.
    monkeypatch.setattr(helpers, 'check_service_health', AsyncMock(return_value=status('unrelated', 'healthy')))
    await helpers.refresh_cached_service_status('unrelated')
    finish.set()
    await task
    assert helpers.set_services_cache([], expected_revision=during_revision) is False
    assert {row.id: row.status for row in helpers.get_cached_services()} == {
        'unrelated': 'healthy', 'n8n': 'healthy',
    }


def test_refused_start_keeps_error_and_does_not_claim_runtime_startup(
        enable_health, monkeypatch, test_client):
    helpers.set_services_cache([])
    probe = AsyncMock()
    monkeypatch.setattr(helpers, 'check_service_health', probe)
    monkeypatch.setattr(extensions, '_call_agent', lambda *args: False)
    response = test_client.post('/api/extensions/n8n/enable', headers=test_client.auth_headers)
    assert response.status_code == 200
    assert response.json()['failed_services'] == ['n8n']
    probe.assert_not_called()
    for row in read_library(test_client):
        assert row['status'] == 'error'
        assert row['runtime_starting'] is False


@pytest.mark.asyncio
@pytest.mark.parametrize('poll_timing', ['before_actions', 'during_probes'])
@pytest.mark.parametrize('first_ready', ['open-webui', 'n8n'])
@pytest.mark.parametrize('fresh_status', ['healthy', 'unhealthy'])
async def test_webui_and_n8n_actions_survive_a_delayed_background_poll(
        enable_health, monkeypatch, poll_timing, first_ready, fresh_status):
    """Exercise both real action endpoints and the production poll loop together."""
    import main
    import security

    configs = {
        'open-webui': {'name': 'Open WebUI', 'port': 8080, 'external_port': 3000},
        'unrelated': {'name': 'unrelated', 'port': 5678},
    }
    monkeypatch.setattr(helpers, 'SERVICES', configs)
    stale = [status('unrelated', 'healthy'), status('open-webui', 'down')]
    helpers.set_services_cache(stale)  # n8n has not yet entered the poller cache.
    entered = {name: asyncio.Event() for name in ['open-webui', 'n8n']}
    finish = {name: asyncio.Event() for name in entered}
    poll_entered, finish_poll, poll_applied = asyncio.Event(), asyncio.Event(), asyncio.Event()
    never = asyncio.Event()

    async def probe(service_id, config, **kwargs):
        entered[service_id].set()
        await finish[service_id].wait()
        return status(service_id, fresh_status)

    async def stale_poll():
        poll_entered.set()
        await finish_poll.wait()
        return stale

    async def poll_sleep(delay):
        if delay == 2:  # The initial startup delay is unrelated to this race.
            return
        poll_applied.set()  # The real loop has attempted its cache publication.
        await never.wait()

    monkeypatch.setattr(helpers, 'check_service_health', probe)
    monkeypatch.setattr(helpers, 'request_agent_json', AsyncMock(return_value={
        'schema_version': 'ods.host-service-health.v1', 'containers': [{
            'service_id': 'n8n', 'container_name': 'ods-n8n',
            'state': 'running', 'health': fresh_status,
        }],
    }))
    selection = Mock(return_value={'enabled': True, 'action': 'enabled'})
    monkeypatch.setattr(extensions, 'request_agent_json', selection)
    monkeypatch.setattr(main, 'get_all_services', stale_poll)
    # Do not replace asyncio.sleep process-wide or rely on wall-clock races.
    monkeypatch.setattr(main, 'asyncio', SimpleNamespace(sleep=poll_sleep))
    tasks = []
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),
                                base_url='http://test', headers={
                                    'Authorization': f'Bearer {security.DASHBOARD_API_KEY}',
                                }) as client:
        try:
            if poll_timing == 'before_actions':
                tasks.append(asyncio.create_task(main._poll_service_health()))
                await asyncio.wait_for(poll_entered.wait(), 2)
            actions = {
                'open-webui': asyncio.create_task(client.post('/api/webui/selection',
                                                              json={'enabled': True})),
                'n8n': asyncio.create_task(client.post('/api/extensions/n8n/enable')),
            }
            tasks.extend(actions.values())
            await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered.values())), 2)
            if poll_timing == 'during_probes':
                tasks.append(asyncio.create_task(main._poll_service_health()))
                await asyncio.wait_for(poll_entered.wait(), 2)
            for service_id in [first_ready, next(name for name in entered if name != first_ready)]:
                finish[service_id].set()
                response = await asyncio.wait_for(actions[service_id], 2)
                assert response.status_code == 200
                if service_id == 'n8n':
                    assert response.json()['failed_services'] == []
            finish_poll.set()
            await asyncio.wait_for(poll_applied.wait(), 2)
            assert {row.id: row.status for row in helpers.get_cached_services()} == {
                'unrelated': 'healthy', 'open-webui': fresh_status, 'n8n': fresh_status,
            }
            assert len(helpers.get_cached_services()) == 3
            selection.assert_called_once_with('POST', '/v1/webui/selection',
                                              payload={'enabled': True}, timeout=900)
            catalog = await client.get('/api/extensions/catalog')
            n8n = next(row for row in catalog.json()['extensions'] if row['id'] == 'n8n')
            assert n8n['status'] == ('enabled' if fresh_status == 'healthy' else 'unhealthy')
            assert n8n['runtime_starting'] is False
        finally:
            for event in finish.values():
                event.set()
            finish_poll.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
