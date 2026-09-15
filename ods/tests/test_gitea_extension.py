"""Pinned Gitea Manifest v2 catalog and one-click compatibility contract."""

import json
from pathlib import Path
import subprocess
import sys

import jsonschema
import yaml


ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / 'extensions/library/services/gitea'


def test_gitea_catalog_preserves_oneclick_and_exposes_v2_plan(tmp_path):
    manifest = yaml.safe_load((SERVICE / 'manifest.yaml').read_text())
    schema = json.loads((ROOT / 'extensions/library/schema/service-manifest.v2.json').read_text())
    jsonschema.validate(manifest, schema)
    output = tmp_path / 'catalog.json'
    subprocess.run(
        [sys.executable, str(ROOT / 'scripts/generate-extensions-catalog.py'),
         '--output', str(output)],
        check=True,
    )
    generated = json.loads(output.read_text())
    checked_in = json.loads((ROOT / 'config/extensions-catalog.json').read_text())
    entry = next(item for item in generated['extensions'] if item['id'] == 'gitea')
    assert entry == next(item for item in checked_in['extensions'] if item['id'] == 'gitea')
    assert entry['external_port_default'] == 7830
    assert entry['health_endpoint'] == '/api/healthz'
    assert entry['manifest_schema_version'] == 'ods.services.v2'
    assert {item['key'] for item in entry['env_vars']} == {
        'GITEA_HOST', 'GITEA_PORT', 'GITEA_SSH_PORT', 'GITEA_APP_NAME',
    }

    planning = entry['planning']
    assert planning['legacy'] is False
    assert planning['provides'] == ['git-hosting@1']
    assert planning['resources']['hostPorts'] == [
        {'port': 2222, 'protocol': 'tcp', 'configurationKey': 'GITEA_SSH_PORT'},
        {'port': 7830, 'protocol': 'tcp', 'configurationKey': 'GITEA_PORT'},
    ]
    assert planning['estimates']['downloadBytes'] == sum(
        image['downloadBytes'] for image in planning['artifacts']['images']
    )
    assert planning['data'] == [{
        'path': 'data/gitea', 'backupClass': 'required', 'owner': 'user',
        'uninstall': 'preserve', 'purge': 'separate-approval',
    }]
    assert all(not item['secret'] for item in planning['configuration'])
    assert next(item for item in planning['configuration']
                if item['key'] == 'GITEA_SSH_PORT')['default'] == 2222

    compose = yaml.safe_load((SERVICE / 'compose.yaml').read_text())['services']['gitea']
    image = planning['artifacts']['images'][0]
    assert compose['image'] == f"{image['reference']}@{image['digest']}"
    assert './data/gitea:/var/lib/gitea:rw' in compose['volumes']
    assert len(compose['ports']) == 2
