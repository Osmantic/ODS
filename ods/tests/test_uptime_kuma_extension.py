"""Catalog discovery and opt-in authenticated Kuma monitor lifecycle."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / 'extensions/library/services/uptime-kuma'


def test_uptime_kuma_catalog_exposes_the_internal_listener(tmp_path):
    output = tmp_path / 'catalog.json'
    subprocess.run([sys.executable, str(ROOT / 'scripts/generate-extensions-catalog.py'), '--output', str(output)], check=True)
    generated = json.loads(output.read_text())
    entry = next(item for item in generated['extensions'] if item['id'] == 'uptime-kuma')
    checked = json.loads((ROOT / 'config/extensions-catalog.json').read_text())
    assert entry == next(item for item in checked['extensions'] if item['id'] == 'uptime-kuma')
    assert (entry['port'], entry['external_port_default']) == (3001, 8102)
    assert entry['gpu_backends'] == ['all']


@pytest.mark.skipif(os.environ.get('ODS_TEST_UPTIME_KUMA_BROWSER') != '1', reason='Opt-in Docker and Chromium test')
def test_uptime_kuma_monitor_and_account_survive_recreation(tmp_path):
    from playwright.sync_api import expect, sync_playwright

    name = f'ods-kuma-test-{uuid.uuid4().hex[:10]}'
    installed = tmp_path / 'extensions/services/uptime-kuma'
    shutil.copytree(SERVICE, installed)
    overlay = tmp_path / 'isolation.yaml'
    overlay.write_text(f'''services:
  uptime-kuma-init:
    container_name: {name}-init
  uptime-kuma:
    container_name: {name}
    ports: !override ["127.0.0.1:0:3001"]
networks:
  ods-network:
    name: {name}
''')
    command = ['docker', 'compose', '--project-name', name, '--project-directory', str(tmp_path), '-f', str(installed / 'compose.yaml'), '-f', str(overlay)]

    def run(*args):
        result = subprocess.run(args, capture_output=True, text=True, timeout=150)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    def endpoint():
        return 'http://' + run(*command, 'port', 'uptime-kuma', '3001')

    run('docker', 'network', 'create', name)
    try:
        plan = json.loads(run(*command, 'config', '--format', 'json'))['services']
        assert plan['uptime-kuma']['ports'][0]['host_ip'] == '127.0.0.1'
        assert plan['uptime-kuma-init']['network_mode'] == 'none'
        assert all('docker.sock' not in volume['target'] for service in plan.values() for volume in service.get('volumes', []))
        run(*command, 'up', '-d', '--wait', '--wait-timeout', '120')
        assert run('docker', 'exec', name, 'id', '-u') == '1000'
        password = 'local-uptime-fixture-password-2026'
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(locale='en-US')
            page = context.new_page()
            page.goto(endpoint())
            page.get_by_label('Username', exact=True).fill('owner')
            page.get_by_label('Password', exact=True).fill(password)
            page.get_by_label('Repeat Password', exact=True).fill(password)
            page.get_by_role('button', name='Create', exact=True).click()
            expect(page.get_by_role('link', name='Add New Monitor')).to_be_visible(timeout=30000)
            page.get_by_role('link', name='Add New Monitor').click()
            page.locator('#name').fill('Local readiness receipt')
            page.locator('#url').fill('http://127.0.0.1:3001/')
            page.get_by_label(re.compile('Heartbeat Interval')).fill('20')
            page.get_by_role('button', name='Save', exact=True).click()
            expect(page.get_by_role('cell', name='Up', exact=True).first).to_be_visible(timeout=45000)
            monitor_url = page.url
            page.get_by_role('link', name='Edit', exact=True).click()
            page.locator('#url').fill('http://127.0.0.1:1/')
            page.get_by_role('button', name='Save', exact=True).click()
            expect(page.get_by_text('Saved.', exact=True)).to_be_visible()
            page.goto(monitor_url)
            expect(page.get_by_role('cell', name='Down', exact=True).first).to_be_visible(timeout=45000)
            anonymous = browser.new_context(locale='en-US').new_page()
            anonymous.goto(endpoint() + '/dashboard')
            expect(anonymous.get_by_role('form', name='Login Form')).to_be_visible()
            expect(anonymous.get_by_text('Local readiness receipt', exact=True)).to_have_count(0)
            browser.close()

        run(*command, 'up', '-d', '--force-recreate', '--wait', '--wait-timeout', '120')
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(locale='en-US')
            page.goto(endpoint())
            expect(page.get_by_role('form', name='Login Form')).to_be_visible()
            page.get_by_label('Username', exact=True).fill('owner')
            page.get_by_placeholder('Password', exact=True).fill(password)
            page.get_by_role('button', name='Log in', exact=True).click()
            page.get_by_role('link', name='Local readiness receipt', exact=True).first.click()
            expect(page.get_by_role('cell', name='Down', exact=True).first).to_be_visible(timeout=30000)
            browser.close()
    finally:
        run(*command, 'down', '--timeout', '10')
        run('docker', 'network', 'rm', name)
