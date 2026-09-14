"""Excalidraw catalog discovery and compose contract test."""
import json
from pathlib import Path
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / 'extensions/library/services/excalidraw'


def test_excalidraw_is_discoverable_with_consistent_manifest_and_compose(tmp_path):
    output = tmp_path / 'catalog.json'
    subprocess.run([sys.executable, str(ROOT / 'scripts/generate-extensions-catalog.py'), '--output', str(output)], check=True)
    generated = json.loads(output.read_text())
    entry = next(item for item in generated['extensions'] if item['id'] == 'excalidraw')
    checked_in = json.loads((ROOT / 'config/extensions-catalog.json').read_text())
    assert entry == next(item for item in checked_in['extensions'] if item['id'] == 'excalidraw')
    assert entry['compose_file'] == 'compose.yaml'
    assert entry['health_endpoint'] == '/'
    assert entry['external_port_default'] == 7857
    manifest = yaml.safe_load((SERVICE / 'manifest.yaml').read_text())['service']
    compose = yaml.safe_load((SERVICE / 'compose.yaml').read_text())['services']['excalidraw']
    assert manifest['container_name'] == compose['container_name']
    assert manifest['port'] == 80
    assert 'ods-network' in compose['networks']
    assert compose['deploy']['resources']['limits']['memory'] == '1G'
