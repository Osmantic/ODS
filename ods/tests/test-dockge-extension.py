"""Catalog and Compose contract for the Dockge (Compose Manager) library extension.

Run: pytest -q tests/test-dockge-extension.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / 'extensions/library/services/dockge'


def _catalog_entry():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / 'catalog.json'
        subprocess.run(
            [sys.executable, str(ROOT / 'scripts/generate-extensions-catalog.py'),
             '--output', str(out)], check=True)
        data = json.loads(out.read_text())
    return next(item for item in data['extensions'] if item['id'] == 'dockge')


def test_dockge_catalog_entry():
    entry = _catalog_entry()
    checked_in = json.loads((ROOT / 'config/extensions-catalog.json').read_text())
    assert entry == next(item for item in checked_in['extensions'] if item['id'] == 'dockge')
    assert entry['port'] == 5001
    assert entry['external_port_default'] == 5001


def test_dockge_compose_hardening():
    services = yaml.safe_load((SERVICE / 'compose.yaml').read_text())['services']
    svc = services['dockge']
    assert svc['ports'] == ['${BIND_ADDRESS:-127.0.0.1}:${DOCKGE_PORT:-5001}:5001']
    assert svc['security_opt'] == ['no-new-privileges:true']
