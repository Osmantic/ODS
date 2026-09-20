"""Catalog and Compose contract for the Linkwarden (Bookmarks) library extension.

Run: pytest -q tests/test-linkwarden-extension.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / 'extensions/library/services/linkwarden'


def _catalog_entry():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / 'catalog.json'
        subprocess.run(
            [sys.executable, str(ROOT / 'scripts/generate-extensions-catalog.py'),
             '--output', str(out)], check=True)
        data = json.loads(out.read_text())
    return next(item for item in data['extensions'] if item['id'] == 'linkwarden')


def test_linkwarden_catalog_entry():
    entry = _catalog_entry()
    checked_in = json.loads((ROOT / 'config/extensions-catalog.json').read_text())
    assert entry == next(item for item in checked_in['extensions'] if item['id'] == 'linkwarden')
    assert entry['port'] == 3200
    assert entry['external_port_default'] == 3200


def test_linkwarden_compose_hardening():
    services = yaml.safe_load((SERVICE / 'compose.yaml').read_text())['services']
    svc = services['linkwarden']
    assert svc['ports'] == ['${BIND_ADDRESS:-127.0.0.1}:${LINKWARDEN_PORT:-3200}:3200']
    assert svc['read_only'] is True
    assert svc['cap_drop'] == ['ALL']
    assert svc['security_opt'] == ['no-new-privileges:true']
    db = services['linkwarden-db']
    assert 'ports' not in db
    assert db['networks'] == ['linkwarden-private']
    nets = yaml.safe_load((SERVICE / 'compose.yaml').read_text())['networks']
    assert nets['linkwarden-private']['internal'] is True
