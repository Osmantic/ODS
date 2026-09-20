"""Catalog and Compose contract for the Umami (Analytics) library extension.

Run: pytest -q tests/test-umami-extension.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / 'extensions/library/services/umami'


def _catalog_entry():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / 'catalog.json'
        subprocess.run(
            [sys.executable, str(ROOT / 'scripts/generate-extensions-catalog.py'),
             '--output', str(out)], check=True)
        data = json.loads(out.read_text())
    return next(item for item in data['extensions'] if item['id'] == 'umami')


def test_umami_catalog_entry():
    entry = _catalog_entry()
    checked_in = json.loads((ROOT / 'config/extensions-catalog.json').read_text())
    assert entry == next(item for item in checked_in['extensions'] if item['id'] == 'umami')
    assert entry['port'] == 3030
    assert entry['external_port_default'] == 3030


def test_umami_compose_hardening():
    services = yaml.safe_load((SERVICE / 'compose.yaml').read_text())['services']
    svc = services['umami']
    assert svc['ports'] == ['${BIND_ADDRESS:-127.0.0.1}:${UMAMI_PORT:-3030}:3030']
    assert svc['read_only'] is True
    assert svc['cap_drop'] == ['ALL']
    assert svc['security_opt'] == ['no-new-privileges:true']
    db = services['umami-db']
    assert 'ports' not in db
    assert db['networks'] == ['umami-private']
    nets = yaml.safe_load((SERVICE / 'compose.yaml').read_text())['networks']
    assert nets['umami-private']['internal'] is True
