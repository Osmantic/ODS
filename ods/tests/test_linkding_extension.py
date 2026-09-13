"""Catalog boundary and opt-in pinned Linkding authenticated bookmark lifecycle."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / 'extensions/library/services/linkding'


def test_linkding_catalog_exposes_the_installed_listener(tmp_path):
    output = tmp_path / 'catalog.json'
    subprocess.run([sys.executable, str(ROOT / 'scripts/generate-extensions-catalog.py'), '--output', str(output)], check=True)
    generated = json.loads(output.read_text())
    entry = next(item for item in generated['extensions'] if item['id'] == 'linkding')
    checked = json.loads((ROOT / 'config/extensions-catalog.json').read_text())
    assert entry == next(item for item in checked['extensions'] if item['id'] == 'linkding')
    assert (entry['port'], entry['external_port_default'], entry['health_endpoint']) == (9090, 8100, '/health')
    assert entry['gpu_backends'] == ['all']


@pytest.mark.skipif(os.environ.get('ODS_TEST_LINKDING_DOCKER') != '1', reason='Opt-in Docker lifecycle test')
def test_linkding_private_bookmarks_survive_recreation(tmp_path):
    name = f'ods-linkding-test-{uuid.uuid4().hex[:10]}'
    installed = tmp_path / 'extensions/services/linkding'
    shutil.copytree(SERVICE, installed)
    overlay = tmp_path / 'isolation.yaml'
    overlay.write_text(f'''services:
  linkding:
    container_name: {name}
    ports: !override ["127.0.0.1:0:9090"]
networks:
  ods-network:
    name: {name}
''')
    command = ['docker', 'compose', '--project-name', name, '--project-directory', str(tmp_path), '-f', str(installed / 'compose.yaml'), '-f', str(overlay)]

    def run(*args):
        result = subprocess.run(args, capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    def endpoint():
        return 'http://' + run(*command, 'port', 'linkding', '9090')

    run('docker', 'network', 'create', name)
    try:
        plan = json.loads(run(*command, 'config', '--format', 'json'))['services']['linkding']
        assert plan['ports'][0]['host_ip'] == '127.0.0.1'
        assert plan['environment']['LD_DISABLE_BACKGROUND_TASKS'] == 'True'
        run(*command, 'up', '-d', '--wait', '--wait-timeout', '90')
        # Exercise the documented account-management boundary without publishing credentials.
        run('docker', 'exec', '-e', 'DJANGO_SUPERUSER_PASSWORD=bookmark-fixture-password-2026', name, 'python', 'manage.py', 'createsuperuser', '--noinput', '--username', 'owner', '--email', 'owner@example.test')
        token = run('docker', 'exec', name, 'python', 'manage.py', 'shell', '-c', "from bookmarks.models import ApiToken; from django.contrib.auth.models import User; print(ApiToken.objects.create(user=User.objects.get(username='owner'), name='ODS test').key)").splitlines()[-1]
        headers = {'Authorization': 'Token ' + token}
        with httpx.Client(base_url=endpoint(), timeout=10) as client:
            assert client.get('/health').status_code == 200
            assert client.get('/api/bookmarks/').status_code == 401
            saved = client.post('/api/bookmarks/?disable_scraping', headers=headers, json={'url': 'https://example.test/research', 'title': 'Reviewed evidence', 'notes': 'Private source', 'tag_names': ['research'], 'shared': False})
            assert saved.status_code == 201, saved.text
            bookmark_id = saved.json()['id']
            assert saved.json()['shared'] is False
            found = client.get('/api/bookmarks/', params={'q': '#research'}, headers=headers)
            assert found.status_code == 200, found.text
            assert [item['id'] for item in found.json()['results']] == [bookmark_id]
            edited = client.patch(f'/api/bookmarks/{bookmark_id}/', headers=headers, json={'notes': 'Verified source receipt'})
            assert edited.status_code == 200, edited.text
            assert client.get(f'/api/bookmarks/{bookmark_id}/').status_code == 401

        run(*command, 'up', '-d', '--force-recreate', '--wait', '--wait-timeout', '90')
        with httpx.Client(base_url=endpoint(), timeout=10) as client:
            assert client.get('/api/bookmarks/').status_code == 401
            restored = client.get(f'/api/bookmarks/{bookmark_id}/', headers=headers)
            assert restored.status_code == 200, restored.text
            assert restored.json()['notes'] == 'Verified source receipt'
            assert restored.json()['tag_names'] == ['research']
            assert restored.json()['shared'] is False
    finally:
        run(*command, 'down', '--timeout', '10')
        run('docker', 'network', 'rm', name)
