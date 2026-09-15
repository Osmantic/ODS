"""Catalog discovery, compose contract, and credential provisioning for Redis.

Run live docker test: ODS_TEST_REDIS_DOCKER=1 pytest -q tests/test_redis_extension.py
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / 'extensions/library/services/redis'


def test_redis_catalog_and_manifest_contract(tmp_path):
    output = tmp_path / 'catalog.json'
    subprocess.run([sys.executable, str(ROOT / 'scripts/generate-extensions-catalog.py'), '--output', str(output)], check=True)
    generated = json.loads(output.read_text())
    entry = next(item for item in generated['extensions'] if item['id'] == 'redis')
    checked_in = json.loads((ROOT / 'config/extensions-catalog.json').read_text())
    assert entry == next(item for item in checked_in['extensions'] if item['id'] == 'redis')
    assert entry['port'] == 6379
    assert entry['external_port_default'] == 6380

    # Required secrets check
    required = {item['key'] for item in entry['env_vars'] if item.get('required')}
    assert required == {'REDIS_PASSWORD'}

    # Compose contract check
    compose = yaml.safe_load((SERVICE / 'compose.yaml').read_text())
    assert 'redis' in compose['services']
    service_def = compose['services']['redis']

    # Verify security hardening
    assert service_def.get('security_opt') == ['no-new-privileges:true']
    assert service_def.get('networks') == ['ods-network']
    assert compose['networks']['ods-network']['external'] is True

    # Verify isolated published port
    ports = service_def['ports']
    assert any('REDIS_PORT:-6380}:6379' in p for p in ports)


def test_redis_setup_generates_credentials_and_directories(tmp_path):
    setup_script = SERVICE / 'setup.sh'
    assert os.access(setup_script, os.X_OK)

    # Execute setup against temporary test directory
    result = subprocess.run([str(setup_script), str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 0

    env_file = tmp_path / '.env'
    assert env_file.exists()
    content = env_file.read_text()
    assert 'REDIS_PORT=6380' in content
    assert 'REDIS_PASSWORD=' in content
    assert (tmp_path / 'data/redis').is_dir()

    # Verify password security length
    for line in content.splitlines():
        if line.startswith('REDIS_PASSWORD='):
            pw = line.split('=', 1)[1]
            assert len(pw) >= 16

    # Idempotency check: running setup again must preserve existing secrets
    subprocess.run([str(setup_script), str(tmp_path)], check=True)
    content_second = env_file.read_text()
    assert content == content_second


@pytest.mark.skipif(os.environ.get('ODS_TEST_REDIS_DOCKER') != '1', reason='Opt-in Docker lifecycle test')
def test_redis_docker_lifecycle_guard(tmp_path):
    name = f'ods-redis-test-{uuid.uuid4().hex[:10]}'
    overlay = tmp_path / 'isolation.yaml'
    overlay.write_text(f'''services:
  redis:
    container_name: {name}
    ports: !override ["127.0.0.1:0:6379"]
networks:
  ods-network:
    name: {name}
''')
    command = ['docker', 'compose', '--project-name', name, '--project-directory', str(tmp_path), '-f', str(SERVICE / 'compose.yaml'), '-f', str(overlay)]
    environment = {key: value for key, value in os.environ.items() if not key.startswith('REDIS_')}

    # Must refuse missing REDIS_PASSWORD
    absent = subprocess.run([*command, 'config'], env=environment, capture_output=True, text=True, timeout=30)
    assert absent.returncode != 0
    assert 'REDIS_PASSWORD' in absent.stderr
