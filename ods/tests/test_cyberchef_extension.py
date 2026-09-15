"""Catalog discovery and real-browser decoding/decompression of local data."""
import base64
import gzip
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import urlencode, urlsplit
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "extensions/library/services/cyberchef"


def test_cyberchef_catalog_matches_the_installed_listener(tmp_path):
    output = tmp_path / "catalog.json"
    subprocess.run([sys.executable, str(ROOT / "scripts/generate-extensions-catalog.py"), "--output", str(output)], check=True)
    generated = json.loads(output.read_text())
    entry = next(item for item in generated["extensions"] if item["id"] == "cyberchef")
    checked = json.loads((ROOT / "config/extensions-catalog.json").read_text())
    assert entry == next(item for item in checked["extensions"] if item["id"] == "cyberchef")
    assert (entry["port"], entry["external_port_default"], entry["health_endpoint"]) == (8080, 8103, "/")


@pytest.mark.skipif(os.environ.get("ODS_TEST_CYBERCHEF_BROWSER") != "1", reason="Opt-in Docker/Chromium recipe test")
def test_browser_replays_a_multistage_recipe_offline(tmp_path):
    from playwright.sync_api import expect, sync_playwright

    name = "ods-cyberchef-test-" + uuid.uuid4().hex[:10]
    installed = tmp_path / "extensions/services/cyberchef"
    shutil.copytree(SERVICE, installed)
    overlay = tmp_path / "isolation.yaml"
    overlay.write_text(f'''services:
  cyberchef:
    container_name: {name}
    ports: !override ["127.0.0.1:0:8080"]
networks:
  ods-network:
    name: {name}
''')
    command = ["docker", "compose", "--project-name", name, "--project-directory", str(tmp_path),
               "-f", str(installed / "compose.yaml"), "-f", str(overlay)]

    def run(*args):
        result = subprocess.run(args, capture_output=True, text=True, timeout=150)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    def recipe_url(base, message):
        encoded = base64.b64encode(gzip.compress(message.encode(), mtime=0))
        return base + "/#" + urlencode({
            "recipe": "From_Base64('A-Za-z0-9+/=',true,false)Gunzip()",
            "input": base64.b64encode(encoded).decode(),
        })

    run("docker", "network", "create", name)
    try:
        run(*command, "up", "-d", "--wait", "--wait-timeout", "60")
        base = "http://" + run(*command, "port", "cyberchef", "8080")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context()
            page = context.new_page()
            requests, errors = [], []
            context.on("request", lambda request: requests.append(request.url))
            page.on("pageerror", lambda error: errors.append(str(error)))
            first = "Xin chào ODS — decoded locally"
            page.goto(recipe_url(base, first))
            expect(page.get_by_text(first, exact=True)).to_be_visible(timeout=30000)
            context.set_offline(True)
            second = "Second payload — same recipe, offline"
            page.goto(recipe_url(base, second))
            expect(page.get_by_text(second, exact=True)).to_be_visible(timeout=15000)
            assert not errors
            assert all(urlsplit(url).netloc == urlsplit(base).netloc for url in requests if url.startswith(("http:", "https:")))
            browser.close()
    finally:
        run(*command, "down", "--timeout", "10")
        run("docker", "network", "rm", name)
