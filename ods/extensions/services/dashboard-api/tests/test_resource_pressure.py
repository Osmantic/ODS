"""Authenticated PSI HTTP receipts preserve kernel units and unavailable states."""
from datetime import datetime
import os
from pathlib import Path

import pytest


@pytest.fixture
def pressure_dir(tmp_path, monkeypatch):
    import routers.resources as router
    monkeypatch.setattr(router, 'PRESSURE_DIR', tmp_path, raising=False)
    return tmp_path


def request(client):
    return client.get('/api/resources/pressure', headers=client.auth_headers)


def test_pressure_requires_authentication(test_client):
    assert test_client.get('/api/resources/pressure').status_code == 401


def test_pressure_reports_kernel_units_and_scope(test_client, pressure_dir):
    (pressure_dir / 'cpu').write_text('some avg10=1.25 avg60=2.00 avg300=0.75 total=123456\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=0\n')
    for name in ('memory', 'io'):
        (pressure_dir / name).write_text('some avg10=12.50 avg60=8.00 avg300=4.00 total=999999\nfull avg10=3.50 avg60=2.00 avg300=1.00 total=55555\n')
    response = request(test_client)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['schema_version'] == 1
    assert data['scope'] == 'api-runtime-kernel'
    assert datetime.fromisoformat(data['captured_at']).tzinfo is not None
    assert data['resources']['cpu']['some'] == {'avg10_percent': 1.25, 'avg60_percent': 2.0, 'avg300_percent': 0.75, 'total_us': 123456}
    assert data['resources']['cpu']['full'] is None  # Undefined at system scope.
    assert data['resources']['memory']['full']['avg10_percent'] == 3.5
    assert data['resources']['io']['available'] is True


def test_missing_pressure_is_unavailable_instead_of_zero(test_client, pressure_dir):
    response = request(test_client)
    assert response.status_code == 200
    assert response.json()['resources'] == {
        name: {'available': False, 'reason': 'not_available', 'some': None, 'full': None}
        for name in ('cpu', 'memory', 'io')
    }


def test_partial_permission_failure_does_not_hide_other_resources(test_client, pressure_dir, monkeypatch):
    (pressure_dir / 'cpu').write_text('some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n')
    original = Path.read_text

    def read(path, *args, **kwargs):
        if path == pressure_dir / 'memory':
            raise PermissionError('fixture denied')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', read)
    response = request(test_client)
    assert response.status_code == 200
    resources = response.json()['resources']
    assert resources['cpu']['available'] is True
    assert resources['cpu']['some']['total_us'] == 0
    assert resources['memory']['reason'] == 'permission_denied'
    assert resources['io']['reason'] == 'not_available'


@pytest.mark.parametrize('line', [
    'some avg10=nan avg60=0 avg300=0 total=0',
    'some avg10=101 avg60=0 avg300=0 total=0',
    'some avg10=1 avg60=0 avg300=0 total=-1',
    'some avg10=1 avg300=0 total=0',
    'some avg10=é avg60=0 avg300=0 total=0',
    '',
])
def test_malformed_probe_never_becomes_healthy_zeroes(test_client, pressure_dir, line):
    (pressure_dir / 'cpu').write_text(line)
    response = request(test_client)
    assert response.status_code == 502
    assert response.json()['detail'] == 'Invalid cpu pressure data'


@pytest.mark.skipif(os.environ.get('ODS_TEST_LIVE_PSI') != '1', reason='Opt-in live Linux kernel probe')
def test_pressure_http_receipt_matches_live_kernel(test_client):
    def totals():
        return {
            name: int((Path('/proc/pressure') / name).read_text().splitlines()[0].split('total=')[1])
            for name in ('cpu', 'memory', 'io')
        }

    before = totals()
    response = request(test_client)
    after = totals()
    assert response.status_code == 200, response.text
    for name, measurement in response.json()['resources'].items():
        assert measurement['available'] is True
        assert before[name] <= measurement['some']['total_us'] <= after[name]
