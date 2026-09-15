"""Catalog discovery, compose security contract, and setup lifecycle for MinIO.

Run live docker test: ODS_TEST_MINIO_DOCKER=1 pytest -q tests/test_minio_extension.py
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
SERVICE = ROOT / 'extensions/library/services/minio'


def test_minio_catalog_and_manifest_contract(tmp_path):
    output = tmp_path / 'catalog.json'
    subprocess.run([sys.executable, str(ROOT / 'scripts/generate-extensions-catalog.py'), '--output', str(output)], check=True)
    generated = json.loads(output.read_text())
    entry = next(item for item in generated['extensions'] if item['id'] == 'minio')
    checked_in = json.loads((ROOT / 'config/extensions-catalog.json').read_text())
    assert entry == next(item for item in checked_in['extensions'] if item['id'] == 'minio')
    assert entry['port'] == 9000
    assert entry['external_port_default'] == 9020
    assert entry['health_endpoint'] == '/minio/health/ready'
    
    # Required secrets check
    required = {item['key'] for item in entry['env_vars'] if item.get('required')}
    assert required == {'MINIO_ROOT_PASSWORD'}

    # Compose contract check
    compose = yaml.safe_load((SERVICE / 'compose.yaml').read_text())
    assert 'minio' in compose['services']
    service_def = compose['services']['minio']
    
    # Verify security hardening
    assert service_def.get('security_opt') == ['no-new-privileges:true']
    assert service_def.get('networks') == ['ods-network']
    assert compose['networks']['ods-network']['external'] is True

    # Port isolation: must publish to 9020 and 9021 by default, not 9000
    ports = service_def['ports']
    assert any('MINIO_PORT:-9020}:9000' in p for p in ports)
    assert any('MINIO_CONSOLE_PORT:-9021}:9001' in p for p in ports)


def test_minio_setup_script_generates_env_and_data_dir(tmp_path):
    setup_script = SERVICE / 'setup.sh'
    assert os.access(setup_script, os.X_OK)

    # Execute setup against temporary test directory
    result = subprocess.run([str(setup_script), str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 0

    env_file = tmp_path / '.env'
    assert env_file.exists()
    content = env_file.read_text()
    assert 'MINIO_ROOT_USER=minioadmin' in content
    assert 'MINIO_ROOT_PASSWORD=' in content
    assert 'MINIO_PORT=9020' in content
    assert 'MINIO_CONSOLE_PORT=9021' in content
    assert (tmp_path / 'data/minio').is_dir()

    # Extract generated password to ensure minimum security length
    for line in content.splitlines():
        if line.startswith('MINIO_ROOT_PASSWORD='):
            pw = line.split('=', 1)[1]
            assert len(pw) >= 16

    # Idempotency check: running setup again must preserve existing secrets
    subprocess.run([str(setup_script), str(tmp_path)], check=True)
    content_second = env_file.read_text()
    assert content == content_second


@pytest.mark.skipif(os.environ.get('ODS_TEST_MINIO_DOCKER') != '1', reason='Opt-in Docker lifecycle test')
def test_minio_docker_lifecycle_and_health(tmp_path):
    name = f'ods-minio-test-{uuid.uuid4().hex[:10]}'
    overlay = tmp_path / 'isolation.yaml'
    overlay.write_text(f'''services:
  minio:
    container_name: {name}
    ports: !override ["127.0.0.1:0:9000", "127.0.0.1:0:9001"]
networks:
  ods-network:
    name: {name}
''')
    command = ['docker', 'compose', '--project-name', name, '--project-directory', str(tmp_path), '-f', str(SERVICE / 'compose.yaml'), '-f', str(overlay)]
    environment = {key: value for key, value in os.environ.items() if not key.startswith('MINIO_')}
    
    # Must refuse missing MINIO_ROOT_PASSWORD
    absent = subprocess.run([*command, 'config'], env=environment, capture_output=True, text=True, timeout=30)
    assert absent.returncode != 0
    assert 'MINIO_ROOT_PASSWORD' in absent.stderr
