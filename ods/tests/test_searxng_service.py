"""Catalog discovery, compose contract, and network isolation for SearXNG.

Run live docker test: ODS_TEST_SEARXNG_DOCKER=1 pytest -q tests/test_searxng_service.py
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
SERVICE = ROOT / 'extensions/services/searxng'


def test_searxng_catalog_and_manifest_contract(tmp_path):
    output = tmp_path / 'catalog.json'
    subprocess.run([sys.executable, str(ROOT / 'scripts/generate-extensions-catalog.py'), '--output', str(output)], check=True)
    generated = json.loads(output.read_text())
    entry = next(item for item in generated['extensions'] if item['id'] == 'searxng')
    checked_in = json.loads((ROOT / 'config/extensions-catalog.json').read_text())
    assert entry == next(item for item in checked_in['extensions'] if item['id'] == 'searxng')
    assert entry['port'] == 8080
    assert entry['external_port_default'] == 8888
    assert entry['health_endpoint'] == '/healthz'

    # Required secrets check
    required = {item['key'] for item in entry['env_vars'] if item.get('required')}
    assert required == {'SEARXNG_SECRET'}

    # Compose contract check
    compose = yaml.safe_load((SERVICE / 'compose.yaml').read_text())
    assert 'searxng' in compose['services']
    service_def = compose['services']['searxng']

    # Verify security hardening
    assert service_def.get('security_opt') == ['no-new-privileges:true']
    assert service_def.get('networks') == ['ods-network']
    assert compose['networks']['ods-network']['external'] is True

    # Verify published port mapping
    ports = service_def['ports']
    assert any('SEARXNG_PORT:-8888}:8080' in p for p in ports)


@pytest.mark.skipif(os.environ.get('ODS_TEST_SEARXNG_DOCKER') != '1', reason='Opt-in Docker lifecycle test')
def test_searxng_compose_rejects_missing_secret(tmp_path):
    command = ['docker', 'compose', '--project-directory', str(tmp_path), '-f', str(SERVICE / 'compose.yaml'), 'config']
    environment = {key: value for key, value in os.environ.items() if not key.startswith('SEARXNG_')}
    result = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert 'SEARXNG_SECRET' in result.stderr


@pytest.mark.skipif(os.environ.get('ODS_TEST_SEARXNG_DOCKER') != '1', reason='Opt-in Docker lifecycle test')
def test_searxng_docker_lifecycle_guard(tmp_path):
    name = f'ods-searxng-test-{uuid.uuid4().hex[:10]}'
    overlay = tmp_path / 'isolation.yaml'
    overlay.write_text(f'''services:
  searxng:
    container_name: {name}
    ports: !override ["127.0.0.1:0:8080"]
networks:
  ods-network:
    name: {name}
''')
    command = ['docker', 'compose', '--project-name', name, '--project-directory', str(tmp_path), '-f', str(SERVICE / 'compose.yaml'), '-f', str(overlay)]
    environment = {key: value for key, value in os.environ.items() if not key.startswith('SEARXNG_')}
    environment['SEARXNG_SECRET'] = 'test-secret-value-12345'
    config_check = subprocess.run([*command, 'config'], env=environment, capture_output=True, text=True, timeout=30)
    assert config_check.returncode == 0
