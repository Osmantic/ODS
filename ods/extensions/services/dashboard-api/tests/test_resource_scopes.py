"""Filtered resource responses must not narrow shared cached snapshots."""
from unittest.mock import Mock

import pytest


@pytest.fixture
def resource_sources(monkeypatch):
    from main import _cache
    from routers import resources
    _cache.invalidate('service_resources_containers')
    _cache.invalidate('service_resources_disk')
    monkeypatch.setattr(resources, 'SERVICES', {sid: {'name': sid, 'container_name': f'ods-{sid}'} for sid in ('alpha', 'beta')})
    stats = Mock(return_value=[{'container_name': 'ods-alpha', 'cpu_percent': 10, 'memory_used_mb': 100}, {'container_name': 'ods-beta', 'cpu_percent': 20, 'memory_used_mb': 200}])
    disk = Mock(return_value={'alpha': {'data_gb': 1}, 'beta': {'data_gb': 2}, 'orphan': {'data_gb': 4}})
    monkeypatch.setattr(resources, '_fetch_container_stats', stats)
    monkeypatch.setattr(resources, '_scan_service_disk', disk)
    yield stats, disk
    _cache.invalidate('service_resources_containers')
    _cache.invalidate('service_resources_disk')


def test_scoped_totals_and_cache_isolation(test_client, resource_sources):
    response = test_client.get('/api/services/resources?service=alpha&service=alpha', headers=test_client.auth_headers)
    assert response.status_code == 200
    assert [row['id'] for row in response.json()['services']] == ['alpha']
    assert response.json()['totals'] == {'cpu_percent': 10, 'memory_used_mb': 100, 'disk_data_gb': 1}
    full = test_client.get('/api/services/resources', headers=test_client.auth_headers).json()
    assert [row['id'] for row in full['services']] == ['alpha', 'beta', 'orphan']
    assert full['totals'] == {'cpu_percent': 30, 'memory_used_mb': 300, 'disk_data_gb': 7}
    for source in resource_sources:
        source.assert_called_once()


def test_cpu_memory_only_skips_disk_without_poisoning_cache(test_client, resource_sources):
    result = test_client.get('/api/services/resources?service=beta&include_disk=false', headers=test_client.auth_headers).json()
    assert result['totals']['disk_data_gb'] is None
    assert result['services'][0]['disk'] is None
    assert result['scope'] == {'services': ['beta'], 'disk_included': False}
    resource_sources[1].assert_not_called()
    full = test_client.get('/api/services/resources', headers=test_client.auth_headers).json()
    assert full['totals']['disk_data_gb'] == 7
    resource_sources[1].assert_called_once()


@pytest.mark.parametrize('query,status', [('service=missing', 404), ('service=bad%0Aid', 400), ('service=', 400), ('include_disk=maybe', 422)])
def test_invalid_filters_are_visible(test_client, resource_sources, query, status):
    assert test_client.get(f'/api/services/resources?{query}', headers=test_client.auth_headers).status_code == status


def test_filtered_metrics_still_require_auth(test_client):
    assert test_client.get('/api/services/resources?service=alpha&include_disk=false').status_code == 401
