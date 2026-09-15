"""Catalog discovery, compose contract, and credential generation for Qdrant vector database.

Validates that the Qdrant service manifest, compose configuration, and setup hook
conform to ODS extension architecture standards.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / 'extensions/services/qdrant'


def test_qdrant_catalog_preserves_contract(tmp_path):
    output = tmp_path / 'catalog.json'
    subprocess.run(
        [sys.executable, str(ROOT / 'scripts/generate-extensions-catalog.py'), '--output', str(output)],
        check=True,
    )
    generated = json.loads(output.read_text(encoding='utf-8'))
    entry = next(item for item in generated['extensions'] if item['id'] == 'qdrant')
    checked_in = json.loads((ROOT / 'config/extensions-catalog.json').read_text(encoding='utf-8'))
    checked_in_entry = next(item for item in checked_in['extensions'] if item['id'] == 'qdrant')
    assert entry == checked_in_entry
    assert entry['port'] == 6333
    assert entry['external_port_default'] == 6333
    assert entry['health_endpoint'] == '/readyz'
    assert any(var['key'] == 'QDRANT_API_KEY' for var in entry['env_vars'])

    tags = set(entry['tags'])
    assert {'vector-database', 'semantic-search', 'embeddings', 'rag'}.issubset(tags)

    features = {f['id'] for f in entry['features']}
    assert {'vector-search', 'payload-filtering', 'grpc-interface'}.issubset(features)


def test_qdrant_compose_contract():
    compose_path = SERVICE / 'compose.yaml'
    assert compose_path.is_file()
    doc = yaml.safe_load(compose_path.read_text(encoding='utf-8'))

    services = doc['services']
    assert 'qdrant' in services
    qdrant = services['qdrant']

    # Network isolation and inter-container communication
    assert qdrant.get('networks') == ['ods-network']
    assert doc.get('networks', {}).get('ods-network', {}).get('external') is True

    # Security and isolation
    assert 'no-new-privileges:true' in qdrant.get('security_opt', [])

    # Healthcheck must use unauthenticated /readyz endpoint
    test_cmd = qdrant.get('healthcheck', {}).get('test', [])
    assert any('/readyz' in str(part) for part in test_cmd)

    # API key environment binding
    env = qdrant.get('environment', [])
    assert any('QDRANT__SERVICE__API_KEY' in e and 'QDRANT_API_KEY' in e for e in env)


def test_qdrant_setup_generates_and_preserves_api_key(tmp_path):
    setup_script = SERVICE / 'setup.sh'
    assert setup_script.is_file()
    assert os.access(setup_script, os.X_OK)

    # First run generates a random 32-byte hex key
    env_file = tmp_path / '.env'
    subprocess.run(['sh', str(setup_script), str(tmp_path), 'all'], check=True)
    assert env_file.is_file()
    content1 = env_file.read_text(encoding='utf-8')
    match1 = re.search(r'^QDRANT_API_KEY=([0-9a-fA-F]{64})$', content1, re.MULTILINE)
    assert match1 is not None, f"Expected 64-char hex key, got: {content1}"
    generated_key = match1.group(1)

    # Second run preserves existing key (idempotent)
    subprocess.run(['sh', str(setup_script), str(tmp_path), 'all'], check=True)
    content2 = env_file.read_text(encoding='utf-8')
    match2 = re.search(r'^QDRANT_API_KEY=([0-9a-fA-F]{64})$', content2, re.MULTILINE)
    assert match2 is not None
    assert match2.group(1) == generated_key
