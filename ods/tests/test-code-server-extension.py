"""Catalog and Compose contract for the code-server (VS Code) library extension.

Run: pytest -q tests/test-code-server-extension.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / 'extensions/library/services/code-server'


def _catalog_entry():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / 'catalog.json'
        subprocess.run(
            [sys.executable, str(ROOT / 'scripts/generate-extensions-catalog.py'),
             '--output', str(out)], check=True)
        data = json.loads(out.read_text())
    return next(item for item in data['extensions'] if item['id'] == 'code-server')


def test_code_server_catalog_entry():
    entry = _catalog_entry()
    checked_in = json.loads((ROOT / 'config/extensions-catalog.json').read_text())
    assert entry == next(item for item in checked_in['extensions'] if item['id'] == 'code-server')
    assert entry['port'] == 8443
    assert entry['external_port_default'] == 8443


def test_code_server_compose_hardening():
    services = yaml.safe_load((SERVICE / 'compose.yaml').read_text())['services']
    svc = services['code-server']
    assert svc['ports'] == ['${BIND_ADDRESS:-127.0.0.1}:${CODE_SERVER_PORT:-8443}:8443']
    assert svc['read_only'] is True
    assert svc['cap_drop'] == ['ALL']
    assert svc['security_opt'] == ['no-new-privileges:true']
