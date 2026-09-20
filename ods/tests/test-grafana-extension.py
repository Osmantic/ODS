"""Catalog and Compose contract for the Grafana (Dashboards) library extension.

Run: pytest -q tests/test-grafana-extension.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / 'extensions/library/services/grafana'


def _catalog_entry():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / 'catalog.json'
        subprocess.run(
            [sys.executable, str(ROOT / 'scripts/generate-extensions-catalog.py'),
             '--output', str(out)], check=True)
        data = json.loads(out.read_text())
    return next(item for item in data['extensions'] if item['id'] == 'grafana')


def test_grafana_catalog_entry():
    entry = _catalog_entry()
    checked_in = json.loads((ROOT / 'config/extensions-catalog.json').read_text())
    assert entry == next(item for item in checked_in['extensions'] if item['id'] == 'grafana')
    assert entry['port'] == 3101
    assert entry['external_port_default'] == 3101


def test_grafana_compose_hardening():
    services = yaml.safe_load((SERVICE / 'compose.yaml').read_text())['services']
    svc = services['grafana']
    assert svc['ports'] == ['${BIND_ADDRESS:-127.0.0.1}:${GRAFANA_PORT:-3101}:3101']
    assert svc['read_only'] is True
    assert svc['cap_drop'] == ['ALL']
    assert svc['security_opt'] == ['no-new-privileges:true']
