"""Catalog discovery and opt-in Compose/API persistence for Miniflux.

Run live: ODS_TEST_MINIFLUX_DOCKER=1 pytest -q tests/test_miniflux_extension.py
Requires Docker Compose >= 2.24.4 for test-only port replacement.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import httpx
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / 'extensions/library/services/miniflux'


def test_miniflux_catalog_preserves_required_credentials(tmp_path):
    output = tmp_path / 'catalog.json'
    subprocess.run([sys.executable, str(ROOT / 'scripts/generate-extensions-catalog.py'), '--output', str(output)], check=True)
    generated = json.loads(output.read_text())
    entry = next(item for item in generated['extensions'] if item['id'] == 'miniflux')
    checked_in = json.loads((ROOT / 'config/extensions-catalog.json').read_text())
    assert entry == next(item for item in checked_in['extensions'] if item['id'] == 'miniflux')
    assert entry['port'] == 8080
    assert entry['external_port_default'] == 8098
    assert entry['health_endpoint'] == '/healthcheck'
    required = {item['key'] for item in entry['env_vars'] if item.get('required')}
    assert required == {'MINIFLUX_DB_PASSWORD', 'MINIFLUX_ADMIN_PASSWORD'}
    services = yaml.safe_load((SERVICE / 'compose.yaml').read_text())['services']
    assert 'ports' not in services['miniflux-db']
    assert services['miniflux-db']['networks'] == ['miniflux-private']
    assert services['miniflux']['depends_on']['miniflux-db']['condition'] == 'service_healthy'


@pytest.mark.skipif(os.environ.get('ODS_TEST_MINIFLUX_DOCKER') != '1', reason='Opt-in Docker lifecycle test')
def test_miniflux_refuses_missing_secrets_and_preserves_authenticated_data(tmp_path):
    name = f'ods-miniflux-test-{uuid.uuid4().hex[:10]}'
    overlay = tmp_path / 'isolation.yaml'
    overlay.write_text(f'''services:
  miniflux:
    container_name: {name}
    ports: !override ["127.0.0.1:0:8080"]
  miniflux-db:
    container_name: {name}-db
networks:
  ods-network:
    name: {name}
''')
    command = ['docker', 'compose', '--project-name', name, '--project-directory', str(tmp_path), '-f', str(SERVICE / 'compose.yaml'), '-f', str(overlay)]
    environment = {key: value for key, value in os.environ.items() if not key.startswith('MINIFLUX_')}
    absent = subprocess.run([*command, 'config'], env=environment, capture_output=True, text=True, timeout=30)
    assert absent.returncode != 0
    assert 'MINIFLUX_' in absent.stderr
    # Punctuation is deliberate: the DB secret must not be parsed as URL syntax.
    environment.update(MINIFLUX_DB_PASSWORD="fixture:@/'?&$ db", MINIFLUX_ADMIN_PASSWORD='local-reader-fixture')

    def run(*args):
        result = subprocess.run(args, env=environment, capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    run('docker', 'network', 'create', name)
    try:
        plan = json.loads(run(*command, 'config', '--format', 'json'))['services']
        assert plan['miniflux']['ports'][0]['host_ip'] == '127.0.0.1'
        assert 'ports' not in plan['miniflux-db']
        run(*command, 'up', '-d', '--wait', '--wait-timeout', '90')
        actual_env = json.loads(run('docker', 'inspect', '--format', '{{json .Config.Env}}', name))
        assert 'PGPASSWORD=' + environment['MINIFLUX_DB_PASSWORD'] in actual_env

        def endpoint():
            return 'http://' + run(*command, 'port', 'miniflux', '8080')

        credentials = ('admin', environment['MINIFLUX_ADMIN_PASSWORD'])
        with httpx.Client(base_url=endpoint(), timeout=10) as client:
            assert client.get('/healthcheck').status_code == 200
            assert client.get('/v1/me').status_code == 401
            assert client.get('/v1/me', auth=('admin', 'wrong')).status_code == 401
            assert client.get('/v1/me', auth=credentials).json()['is_admin'] is True
            category = client.post('/v1/categories', json={'title': 'Local research fixture'}, auth=credentials)
            assert category.status_code == 201
            category_id = category.json()['id']

        run(*command, 'up', '-d', '--force-recreate', '--wait', '--wait-timeout', '90')
        with httpx.Client(base_url=endpoint(), timeout=10) as client:
            categories = client.get('/v1/categories', auth=credentials)
            assert categories.status_code == 200
            assert any(item['id'] == category_id and item['title'] == 'Local research fixture' for item in categories.json())
            assert client.get('/v1/categories').status_code == 401
    finally:
        run(*command, 'down', '--timeout', '10')
        run('docker', 'network', 'rm', name)
