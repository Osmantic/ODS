"""Catalog and Compose contract for the File Browser library extension.

Run: pytest -q tests/test-filebrowser-extension.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / 'extensions/library/services/filebrowser'


def _catalog_entry():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / 'catalog.json'
        subprocess.run(
            [sys.executable, str(ROOT / 'scripts/generate-extensions-catalog.py'),
             '--output', str(out)], check=True)
        data = json.loads(out.read_text())
    return next(item for item in data['extensions'] if item['id'] == 'filebrowser')


def test_filebrowser_catalog_entry():
    entry = _catalog_entry()
    checked_in = json.loads((ROOT / 'config/extensions-catalog.json').read_text())
    assert entry == next(item for item in checked_in['extensions'] if item['id'] == 'filebrowser')
    assert entry['port'] == 8087
    assert entry['external_port_default'] == 8087


def test_filebrowser_compose_hardening():
    services = yaml.safe_load((SERVICE / 'compose.yaml').read_text())['services']
    svc = services['filebrowser']
    assert svc['ports'] == ['${BIND_ADDRESS:-127.0.0.1}:${FILEBROWSER_PORT:-8087}:8087']
    assert svc['read_only'] is True
    assert svc['cap_drop'] == ['ALL']
    assert svc['security_opt'] == ['no-new-privileges:true']
