"""Catalog discovery plus opt-in pinned Memos lifecycle and private-note access."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / 'extensions/library/services/memos'


def test_memos_is_discoverable_with_internal_and_external_ports(tmp_path):
    output = tmp_path / 'catalog.json'
    subprocess.run(['python3', str(ROOT / 'scripts/generate-extensions-catalog.py'), '--output', str(output)], check=True)
    generated = json.loads(output.read_text())
    entry = next(item for item in generated['extensions'] if item['id'] == 'memos')
    checked = json.loads((ROOT / 'config/extensions-catalog.json').read_text())
    assert entry == next(item for item in checked['extensions'] if item['id'] == 'memos')
    assert entry['port'] == 5230
    assert entry['external_port_default'] == 8099
    assert entry['health_endpoint'] == '/healthz'
    assert 'cpu' in entry['gpu_backends']


@pytest.mark.skipif(os.environ.get('ODS_TEST_MEMOS_DOCKER') != '1', reason='Opt-in Docker lifecycle test')
def test_memos_bootstrap_private_api_and_recreation(tmp_path):
    name = f'ods-memos-test-{uuid.uuid4().hex[:10]}'
    installed = tmp_path / 'extensions/services/memos'
    shutil.copytree(SERVICE, installed)
    overlay = tmp_path / 'isolation.yaml'
    overlay.write_text(f'''services:
  memos:
    container_name: {name}
    ports: !override ["127.0.0.1:0:5230"]
networks:
  ods-network:
    name: {name}
''')
    command = ['docker', 'compose', '--project-name', name, '--project-directory', str(tmp_path), '-f', str(installed / 'compose.yaml'), '-f', str(overlay)]

    def run(*args):
        result = subprocess.run(args, capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    run('docker', 'network', 'create', name)
    try:
        plan = json.loads(run(*command, 'config', '--format', 'json'))['services']['memos']
        assert plan['ports'][0]['host_ip'] == '127.0.0.1'
        assert plan['environment']['MEMOS_PORT'] == '5230'
        assert plan['environment']['MEMOS_INSTANCE_URL'] == ''
        run(*command, 'up', '-d', '--wait', '--wait-timeout', '90')
        assert 'Uid:\t10001\t10001\t10001\t10001' in run('docker', 'exec', name, 'cat', '/proc/1/status')

        def endpoint():
            return 'http://' + run(*command, 'port', 'memos', '5230')

        password = 'local-notebook-fixture-2026'
        credentials = {'passwordCredentials': {'username': 'owner', 'password': password}}
        with httpx.Client(base_url=endpoint(), timeout=10) as client:
            assert client.get('/healthz').status_code == 200
            owner = client.post('/api/v1/users', json={'username': 'owner', 'password': password})
            assert owner.status_code == 200, owner.text
            assert owner.json()['role'] == 'ADMIN'
            signup = client.post('/api/v1/users', json={'username': 'stranger', 'password': password})
            assert signup.status_code == 403, signup.text
            signin = client.post('/api/v1/auth/signin', json=credentials)
            assert signin.status_code == 200, signin.text
            headers = {'Authorization': 'Bearer ' + signin.json()['accessToken']}
            token = client.post('/api/v1/users/owner/personalAccessTokens', json={'description': 'ODS workflow fixture', 'expiresInDays': 1}, headers=headers)
            assert token.status_code == 200, token.text
            pat = {'Authorization': 'Bearer ' + token.json()['token']}
            memo = client.post('/api/v1/memos', json={'content': '#research\nVerified local result', 'visibility': 'PRIVATE'}, headers=pat)
            assert memo.status_code == 200, memo.text
            path = '/api/v1/' + memo.json()['name']
            client.cookies.clear()
            assert client.get(path).status_code in (401, 403, 404)
            assert client.get(path, headers=pat).json()['content'] == '#research\nVerified local result'

        run(*command, 'up', '-d', '--force-recreate', '--wait', '--wait-timeout', '90')
        with httpx.Client(base_url=endpoint(), timeout=10) as client:
            assert client.get(path).status_code in (401, 403, 404)
            restored = client.get(path, headers=pat)
            assert restored.status_code == 200, restored.text
            assert restored.json()['content'] == '#research\nVerified local result'
            assert client.post('/api/v1/users', json={'username': 'stranger', 'password': password}).status_code == 403
            assert client.post('/api/v1/auth/signin', json=credentials).status_code == 200
    finally:
        run(*command, 'down', '--timeout', '10')
        run('docker', 'network', 'rm', name)
