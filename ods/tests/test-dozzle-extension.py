"""Catalog and Compose contract for the Dozzle library extension.

Run: pytest -q tests/test-dozzle-extension.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / 'extensions/library/services/dozzle'


def _catalog_entry():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / 'catalog.json'
        subprocess.run(
            [sys.executable, str(ROOT / 'scripts/generate-extensions-catalog.py'),
             '--output', str(out)], check=True)
        data = json.loads(out.read_text())
    return next(item for item in data['extensions'] if item['id'] == 'dozzle')


def test_dozzle_catalog_entry():
    entry = _catalog_entry()
    checked_in = json.loads((ROOT / 'config/extensions-catalog.json').read_text())
    assert entry == next(item for item in checked_in['extensions'] if item['id'] == 'dozzle')
    assert entry['port'] == 8080
    assert entry['external_port_default'] == 8484
    assert entry['health_endpoint'] == '/healthcheck'


def test_dozzle_compose_hardening():
    services = yaml.safe_load((SERVICE / 'compose.yaml').read_text())['services']
    svc = services['dozzle']
    # Published port stays localhost-bound through the shared bind address.
    assert svc['ports'] == ['${BIND_ADDRESS:-127.0.0.1}:${DOZZLE_PORT:-8484}:8080']
    # The Docker socket mount must stay read-only — Dozzle only enumerates
    # and tails containers.
    assert svc['volumes'] == ['/var/run/docker.sock:/var/run/docker.sock:ro']
    assert svc['security_opt'] == ['no-new-privileges:true']
    assert svc['cap_drop'] == ['ALL']
    assert svc['read_only'] is True
    assert svc['networks'] == ['ods-network']
