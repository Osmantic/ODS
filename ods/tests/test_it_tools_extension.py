"""Catalog discovery and opt-in real-browser local conversion workflows."""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import urlsplit
import uuid

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / 'extensions/library/services/it-tools'


def test_it_tools_catalog_keeps_the_internal_listener(tmp_path):
    output = tmp_path / 'catalog.json'
    subprocess.run([sys.executable, str(ROOT / 'scripts/generate-extensions-catalog.py'), '--output', str(output)], check=True)
    generated = json.loads(output.read_text())
    entry = next(item for item in generated['extensions'] if item['id'] == 'it-tools')
    checked = json.loads((ROOT / 'config/extensions-catalog.json').read_text())
    assert entry == next(item for item in checked['extensions'] if item['id'] == 'it-tools')
    assert (entry['port'], entry['external_port_default']) == (8080, 8101)
    assert entry['gpu_backends'] == ['all']


@pytest.mark.skipif(os.environ.get('ODS_TEST_IT_TOOLS_BROWSER') != '1', reason='Opt-in Docker and Chromium test')
def test_it_tools_converts_locally_in_a_browser(tmp_path):
    from playwright.sync_api import expect, sync_playwright

    name = f'ods-it-tools-test-{uuid.uuid4().hex[:10]}'
    installed = tmp_path / 'extensions/services/it-tools'
    shutil.copytree(SERVICE, installed)
    overlay = tmp_path / 'isolation.yaml'
    overlay.write_text(f'''services:
  it-tools:
    container_name: {name}
    ports: !override ["127.0.0.1:0:8080"]
networks:
  ods-network:
    name: {name}
''')
    command = ['docker', 'compose', '--project-name', name, '--project-directory', str(tmp_path), '-f', str(installed / 'compose.yaml'), '-f', str(overlay)]

    def run(*args):
        result = subprocess.run(args, capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    run('docker', 'network', 'create', name)
    try:
        plan = json.loads(run(*command, 'config', '--format', 'json'))['services']['it-tools']
        assert plan['ports'][0]['host_ip'] == '127.0.0.1'
        assert plan['user'] == '101:101'
        run(*command, 'up', '-d', '--wait', '--wait-timeout', '60')
        assert run('docker', 'exec', name, 'id', '-u') == '101'
        origin = 'http://' + run(*command, 'port', 'it-tools', '8080')
        response = httpx.get(origin + '/base64-string-converter', timeout=10)
        assert response.status_code == 200
        assert "connect-src 'self'" in response.headers['content-security-policy']
        assert response.headers['referrer-policy'] == 'no-referrer'

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context()
            page = context.new_page()
            requests, errors = [], []
            page.on('request', lambda request: requests.append(request.url))
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(origin + '/base64-string-converter')
            expect(page.get_by_placeholder('Put your string here...')).to_be_visible()
            context.set_offline(True)
            value = 'ODS: Tiếng Việt ✓'
            encoded = base64.b64encode(value.encode()).decode()
            page.get_by_placeholder('Put your string here...').fill(value)
            expect(page.get_by_placeholder('The base64 encoding of your string will be here')).to_have_value(encoded)
            page.get_by_placeholder('Your base64 string...').fill(encoded)
            expect(page.get_by_placeholder('The decoded string will be here')).to_have_value(value)
            context.set_offline(False)
            page.goto(origin + '/json-to-yaml-converter')
            expect(page.get_by_placeholder('Paste your JSON here...')).to_be_visible()
            context.set_offline(True)
            page.get_by_placeholder('Paste your JSON here...').fill('{"source":"local","tags":["research","reviewed"]}')
            expect(page.locator('code')).to_have_text('source: local\ntags:\n  - research\n  - reviewed')
            assert not errors, errors
            assert all(urlsplit(url).netloc == urlsplit(origin).netloc for url in requests), requests
            browser.close()
    finally:
        run(*command, 'down', '--timeout', '10')
        run('docker', 'network', 'rm', name)
