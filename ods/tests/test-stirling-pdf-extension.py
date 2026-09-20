"""Catalog and Compose contract for the Stirling PDF library extension.

Run: pytest -q tests/test-stirling-pdf-extension.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / 'extensions/library/services/stirling-pdf'


def _catalog_entry():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / 'catalog.json'
        subprocess.run(
            [sys.executable, str(ROOT / 'scripts/generate-extensions-catalog.py'),
             '--output', str(out)], check=True)
        data = json.loads(out.read_text())
    return next(item for item in data['extensions'] if item['id'] == 'stirling-pdf')


def test_stirling_pdf_catalog_entry():
    entry = _catalog_entry()
    checked_in = json.loads((ROOT / 'config/extensions-catalog.json').read_text())
    assert entry == next(item for item in checked_in['extensions'] if item['id'] == 'stirling-pdf')
    assert entry['port'] == 8920
    assert entry['external_port_default'] == 8920


def test_stirling_pdf_compose_hardening():
    services = yaml.safe_load((SERVICE / 'compose.yaml').read_text())['services']
    svc = services['stirling-pdf']
    assert svc['ports'] == ['${BIND_ADDRESS:-127.0.0.1}:${STIRLING_PDF_PORT:-8920}:8920']
    assert svc['read_only'] is True
    assert svc['cap_drop'] == ['ALL']
    assert svc['security_opt'] == ['no-new-privileges:true']
