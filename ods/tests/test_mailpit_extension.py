"""Catalog discovery, compose contract, and setup lifecycle for Mailpit.

Run live docker test: ODS_TEST_MAILPIT_DOCKER=1 pytest -q tests/test_mailpit_extension.py
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
SERVICE = ROOT / 'extensions/library/services/mailpit'


def test_mailpit_catalog_and_manifest_contract(tmp_path):
    output = tmp_path / 'catalog.json'
    subprocess.run([sys.executable, str(ROOT / 'scripts/generate-extensions-catalog.py'), '--output', str(output)], check=True)
    generated = json.loads(output.read_text())
    entry = next(item for item in generated['extensions'] if item['id'] == 'mailpit')
    checked_in = json.loads((ROOT / 'config/extensions-catalog.json').read_text())
    assert entry == next(item for item in checked_in['extensions'] if item['id'] == 'mailpit')
    assert entry['port'] == 8025
    assert entry['external_port_default'] == 8025
    assert entry['health_endpoint'] == '/api/v1/info'

    # Compose contract check
    compose = yaml.safe_load((SERVICE / 'compose.yaml').read_text())
    assert 'mailpit' in compose['services']
    service_def = compose['services']['mailpit']

    # Verify security hardening
    assert service_def.get('security_opt') == ['no-new-privileges:true']
    assert service_def.get('networks') == ['ods-network']
    assert compose['networks']['ods-network']['external'] is True

    # Verify published port mappings
    ports = service_def['ports']
    assert any('MAILPIT_PORT:-8025}:8025' in p for p in ports)
    assert any('MAILPIT_SMTP_PORT:-1025}:1025' in p for p in ports)


def test_mailpit_setup_generates_env_and_directories(tmp_path):
    setup_script = SERVICE / 'setup.sh'
    assert os.access(setup_script, os.X_OK)

    # Execute setup against temporary test directory
    result = subprocess.run([str(setup_script), str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 0

    env_file = tmp_path / '.env'
    assert env_file.exists()
    content = env_file.read_text()
    assert 'MAILPIT_PORT=8025' in content
    assert 'MAILPIT_SMTP_PORT=1025' in content
    assert (tmp_path / 'data/mailpit').is_dir()

    # Idempotency check: running setup again must preserve existing config
    subprocess.run([str(setup_script), str(tmp_path)], check=True)
    content_second = env_file.read_text()
    assert content == content_second


@pytest.mark.skipif(os.environ.get('ODS_TEST_MAILPIT_DOCKER') != '1', reason='Opt-in Docker lifecycle test')
def test_mailpit_docker_lifecycle_guard(tmp_path):
    name = f'ods-mailpit-test-{uuid.uuid4().hex[:10]}'
    overlay = tmp_path / 'isolation.yaml'
    overlay.write_text(f'''services:
  mailpit:
    container_name: {name}
    ports: !override ["127.0.0.1:0:8025", "127.0.0.1:0:1025"]
networks:
  ods-network:
    name: {name}
''')
    command = ['docker', 'compose', '--project-name', name, '--project-directory', str(tmp_path), '-f', str(SERVICE / 'compose.yaml'), '-f', str(overlay)]
    config_check = subprocess.run([*command, 'config'], capture_output=True, text=True, timeout=30)
    assert config_check.returncode == 0
