"""Catalog discovery, compose contract, and master key generation for Meilisearch.

Validates that the Meilisearch service manifest, compose configuration, and setup hook
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
SERVICE = ROOT / 'extensions/library/services/meilisearch'


def test_meilisearch_catalog_preserves_contract(tmp_path):
    output = tmp_path / 'catalog.json'
    subprocess.run(
        [sys.executable, str(ROOT / 'scripts/generate-extensions-catalog.py'), '--output', str(output)],
        check=True,
    )
    generated = json.loads(output.read_text(encoding='utf-8'))
    entry = next(item for item in generated['extensions'] if item['id'] == 'meilisearch')
    checked_in = json.loads((ROOT / 'config/extensions-catalog.json').read_text(encoding='utf-8'))
    checked_in_entry = next(item for item in checked_in['extensions'] if item['id'] == 'meilisearch')
    assert entry == checked_in_entry
    assert entry['port'] == 7700
    assert entry['external_port_default'] == 7700
    assert entry['health_endpoint'] == '/health'

    required = {item['key'] for item in entry['env_vars'] if item.get('required')}
    assert 'MEILI_MASTER_KEY' in required

    tags = set(entry['tags'])
    assert {'search-engine', 'full-text-search', 'hybrid-search', 'vector-search', 'typo-tolerance'}.issubset(tags)

    features = {f['id'] for f in entry['features']}
    assert {'fulltext-search', 'hybrid-search', 'faceted-search'}.issubset(features)


def test_meilisearch_compose_contract():
    compose_path = SERVICE / 'compose.yaml'
    assert compose_path.is_file()
    doc = yaml.safe_load(compose_path.read_text(encoding='utf-8'))

    services = doc['services']
    assert 'meilisearch' in services
    meili = services['meilisearch']

    # Network isolation and inter-container communication
    assert meili.get('networks') == ['ods-network']
    assert doc.get('networks', {}).get('ods-network', {}).get('external') is True

    # Security and volume persistence
    assert 'no-new-privileges:true' in meili.get('security_opt', [])
    assert any('/meili_data' in str(v) for v in meili.get('volumes', []))

    # Healthcheck must probe /health
    test_cmd = meili.get('healthcheck', {}).get('test', [])
    assert any('/health' in str(part) for part in test_cmd)

    # Master key environment binding
    env = meili.get('environment', [])
    assert any('MEILI_MASTER_KEY' in e for e in env)
    assert any('MEILI_ENV' in e for e in env)


def test_meilisearch_setup_generates_and_preserves_master_key(tmp_path):
    setup_script = SERVICE / 'setup.sh'
    assert setup_script.is_file()
    assert os.access(setup_script, os.X_OK)

    # First run generates a random 32-byte hex master key
    env_file = tmp_path / '.env'
    subprocess.run(['sh', str(setup_script), str(tmp_path), 'all'], check=True)
    assert env_file.is_file()
    content1 = env_file.read_text(encoding='utf-8')
    match1 = re.search(r'^MEILI_MASTER_KEY=([0-9a-fA-F]{64})$', content1, re.MULTILINE)
    assert match1 is not None, f"Expected 64-char hex key, got: {content1}"
    generated_key = match1.group(1)
    assert 'MEILI_ENV=production' in content1

    # Second run preserves existing key (idempotent)
    subprocess.run(['sh', str(setup_script), str(tmp_path), 'all'], check=True)
    content2 = env_file.read_text(encoding='utf-8')
    match2 = re.search(r'^MEILI_MASTER_KEY=([0-9a-fA-F]{64})$', content2, re.MULTILINE)
    assert match2 is not None
    assert match2.group(1) == generated_key
