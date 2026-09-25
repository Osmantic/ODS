"""ntfy catalog contract and opt-in real Compose lifecycle coverage.

Run live: ODS_TEST_NTFY_DOCKER=1 pytest -q tests/test_ntfy_extension.py
Requires Docker Compose >= 2.24.4 for the isolated port override.
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
SERVICE = ROOT / 'extensions/library/services/ntfy'


def test_ntfy_is_discoverable_with_consistent_manifest_and_compose(tmp_path):
    output = tmp_path / 'catalog.json'
    subprocess.run([sys.executable, str(ROOT / 'scripts/generate-extensions-catalog.py'), '--output', str(output)], check=True)
    generated = json.loads(output.read_text())
    entry = next(item for item in generated['extensions'] if item['id'] == 'ntfy')
    checked_in = json.loads((ROOT / 'config/extensions-catalog.json').read_text())
    assert entry == next(item for item in checked_in['extensions'] if item['id'] == 'ntfy')
    assert entry['compose_file'] == 'compose.yaml'
    assert entry['health_endpoint'] == '/v1/health'
    assert entry['external_port_default'] == 8097
    manifest = yaml.safe_load((SERVICE / 'manifest.yaml').read_text())['service']
    compose = yaml.safe_load((SERVICE / 'compose.yaml').read_text())['services']['ntfy']
    assert manifest['container_name'] == compose['container_name']
    assert manifest['port'] == 8080
    assert compose['environment']['NTFY_AUTH_DEFAULT_ACCESS'] == 'deny-all'
    assert compose['environment']['NTFY_UPSTREAM_BASE_URL'] == ''
    assert './data/ntfy:/var/lib/ntfy' in compose['volumes']


def test_ntfy_writes_owner_prepared_data_dir_without_capabilities():
    # ODS creates data/ntfy as the non-root install owner (0755) and does not
    # chown it. Root without CAP_DAC_OVERRIDE cannot create SQLite files there,
    # so ntfy must run as that owner rather than regain a capability.
    compose = yaml.safe_load((SERVICE / 'compose.yaml').read_text())['services']['ntfy']
    assert compose['user'] == '${ODS_UID:-1000}:${ODS_GID:-1000}'
    assert compose['read_only'] is True
    assert compose['cap_drop'] == ['ALL']
    assert 'cap_add' not in compose
    assert 'no-new-privileges:true' in compose['security_opt']
    for key in ('NTFY_AUTH_FILE', 'NTFY_CACHE_FILE'):
        assert compose['environment'][key].startswith('/var/lib/ntfy/')


@pytest.mark.skipif(os.environ.get('ODS_TEST_NTFY_DOCKER') != '1', reason='Opt-in Docker lifecycle test')
def test_ntfy_auth_notification_and_recreate_persistence(tmp_path, monkeypatch):
    name = f'ods-ntfy-test-{uuid.uuid4().hex[:10]}'
    overlay = tmp_path / 'isolation.yaml'
    overlay.write_text(yaml.safe_dump({
        'services': {'ntfy': {'container_name': name}},
        'networks': {'ods-network': {'name': name}},
    }) + 'x-test-isolated: true\n')
    # Compose override replaces the shipped host port with an ephemeral loopback port.
    overlay.write_text(overlay.read_text().replace(f'container_name: {name}', f'container_name: {name}\n    ports: !override ["127.0.0.1:0:8080"]'))
    command = ['docker', 'compose', '--project-name', name, '--project-directory', str(tmp_path), '-f', str(SERVICE / 'compose.yaml'), '-f', str(overlay)]
    # Match an installed host: .env carries the install owner's ids, and the
    # host agent pre-creates the bind source as that owner (mode 0755). A
    # missing source would be created root-owned by Docker and mask the bug.
    owner = (os.getuid(), os.getgid())
    monkeypatch.setenv('ODS_UID', str(owner[0]))
    monkeypatch.setenv('ODS_GID', str(owner[1]))
    data = tmp_path / 'data/ntfy'
    data.mkdir(parents=True)
    data.chmod(0o755)

    def run(*args, **kwargs):
        return subprocess.run(args, check=True, capture_output=True, text=True, timeout=120, **kwargs).stdout.strip()

    run('docker', 'network', 'create', name)
    try:
        plan = json.loads(run(*command, 'config', '--format', 'json'))['services']['ntfy']
        assert plan['ports'][0]['host_ip'] == '127.0.0.1'
        assert plan['ports'][0]['target'] == 8080
        assert plan['user'] == f'{owner[0]}:{owner[1]}'
        run(*command, 'up', '-d', '--wait', '--wait-timeout', '60')
        for database in ('auth.db', 'cache.db'):
            status = (data / database).stat()
            assert (status.st_uid, status.st_gid) == owner
        # Fixture credentials exist only in this disposable test service.
        run('docker', 'exec', '-e', 'NTFY_PASSWORD=ods-local-integration-fixture', name, 'ntfy', 'user', 'add', 'automation')
        run('docker', 'exec', name, 'ntfy', 'access', 'automation', 'ods-jobs', 'rw')

        def endpoint():
            return 'http://' + run(*command, 'port', 'ntfy', '8080')

        credentials = ('automation', 'ods-local-integration-fixture')
        with httpx.Client(base_url=endpoint(), timeout=10) as client:
            assert client.get('/v1/health').json()['healthy'] is True
            assert client.post('/ods-jobs', content='anonymous').status_code == 403
            assert client.get('/ods-jobs/json?poll=1').status_code == 403
            assert client.post('/ungranted-topic', content='blocked', auth=credentials).status_code == 403
            response = client.post('/ods-jobs', content='Local fixture completed', auth=credentials)
            assert response.status_code == 200
            message_id = response.json()['id']
            assert message_id in client.get('/ods-jobs/json?poll=1', auth=credentials).text

        run(*command, 'up', '-d', '--force-recreate', '--wait', '--wait-timeout', '60')
        with httpx.Client(base_url=endpoint(), timeout=10) as client:
            saved = client.get('/ods-jobs/json?poll=1', auth=credentials)
            assert saved.status_code == 200
            assert message_id in saved.text
            assert client.post('/ods-jobs', content='anonymous').status_code == 403
    finally:
        run(*command, 'down', '--timeout', '10')
        run('docker', 'network', 'rm', name)
