"""Catalog and Compose contract for the Karakeep (AI Bookmarks) library extension.

Run: pytest -q tests/test-karakeep-extension.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / 'extensions/library/services/karakeep'


def _catalog_entry():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / 'catalog.json'
        subprocess.run(
            [sys.executable, str(ROOT / 'scripts/generate-extensions-catalog.py'),
             '--output', str(out)], check=True)
        data = json.loads(out.read_text())
    return next(item for item in data['extensions'] if item['id'] == 'karakeep')


def test_karakeep_catalog_entry():
    entry = _catalog_entry()
    checked_in = json.loads((ROOT / 'config/extensions-catalog.json').read_text())
    assert entry == next(item for item in checked_in['extensions'] if item['id'] == 'karakeep')
    assert entry['port'] == 3300
    assert entry['external_port_default'] == 3300


def test_karakeep_compose_hardening():
    services = yaml.safe_load((SERVICE / 'compose.yaml').read_text())['services']
    svc = services['karakeep']
    assert svc['ports'] == ['${BIND_ADDRESS:-127.0.0.1}:${KARAKEEP_PORT:-3300}:3000']
    assert svc['read_only'] is True
    assert '/run:rw,exec,size=64m,mode=755' in svc['tmpfs']
    assert svc['environment']['S6_READ_ONLY_ROOT'] == '1'
    assert svc['cap_drop'] == ['ALL']
    assert svc['security_opt'] == ['no-new-privileges:true']


def test_karakeep_browser_can_fetch_external_pages_without_meili_exposure():
    compose = yaml.safe_load((SERVICE / 'compose.yaml').read_text())
    services = compose['services']
    networks = compose['networks']

    assert set(services['karakeep']['networks']) == {
        'ods-network', 'karakeep-private', 'karakeep-crawl'}
    assert services['karakeep-chrome']['networks'] == ['karakeep-crawl']
    assert services['karakeep-chrome']['image'] == 'zenika/alpine-chrome:124'
    assert services['karakeep-chrome']['command'] == [
        '--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage',
        '--remote-debugging-address=0.0.0.0', '--remote-debugging-port=9222',
        '--hide-scrollbars',
    ]
    assert services['karakeep-meilisearch']['networks'] == ['karakeep-private']
    assert networks['karakeep-private']['internal'] is True
    assert networks['karakeep-crawl'].get('internal', False) is False
    assert 'ports' not in services['karakeep-chrome']
    assert 'ports' not in services['karakeep-meilisearch']
