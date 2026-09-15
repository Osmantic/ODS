"""Installed catalog and actual browser-to-browser WebRTC transfer boundaries."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "extensions/library/services/pairdrop"


def test_pairdrop_catalog_matches_the_installed_listener(tmp_path):
    output = tmp_path / "catalog.json"
    subprocess.run([sys.executable, str(ROOT / "scripts/generate-extensions-catalog.py"), "--output", str(output)], check=True)
    entry = next(item for item in json.loads(output.read_text())["extensions"] if item["id"] == "pairdrop")
    checked = json.loads((ROOT / "config/extensions-catalog.json").read_text())
    assert entry == next(item for item in checked["extensions"] if item["id"] == "pairdrop")
    assert (entry["port"], entry["external_port_default"], entry["health_endpoint"]) == (3000, 8104, "/")


@pytest.mark.skipif(os.environ.get("ODS_TEST_PAIRDROP_BROWSER") != "1", reason="Opt-in Docker/Chromium WebRTC test")
def test_receiver_declines_then_accepts_a_real_webrtc_file(tmp_path):
    from playwright.sync_api import expect, sync_playwright

    name = "ods-pairdrop-test-" + uuid.uuid4().hex[:10]
    installed = tmp_path / "extensions/services/pairdrop"
    shutil.copytree(SERVICE, installed)
    overlay = tmp_path / "isolation.yaml"
    overlay.write_text(f'''services:
  pairdrop:
    container_name: {name}
    ports: !override ["127.0.0.1:0:3000"]
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

    payload = bytes(range(256)) * 16 + "Xin chào ODS".encode()
    fixture = tmp_path / "ods-transfer.bin"
    fixture.write_bytes(payload)
    declined = tmp_path / "declined.bin"
    declined.write_bytes(b"This request must not be downloaded")
    run("docker", "network", "create", name)
    try:
        run(*command, "up", "-d", "--wait", "--wait-timeout", "60")
        base = "http://" + run(*command, "port", "pairdrop", "3000")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            sender_context = browser.new_context(locale="en-US")
            receiver_context = browser.new_context(locale="en-US", accept_downloads=True)
            for context in (sender_context, receiver_context):
                # Observe native connections; the transport and signaling remain real.
                context.add_init_script('''window.odsConnections = [];
                    const Native = window.RTCPeerConnection;
                    window.RTCPeerConnection = new Proxy(Native, {construct(target, args) {
                        const connection = Reflect.construct(target, args);
                        window.odsConnections.push(connection);
                        return connection;
                    }});''')
            sender = sender_context.new_page()
            receiver = receiver_context.new_page()
            downloads = []
            receiver.on("download", lambda download: downloads.append(download))
            sender.goto(base)
            receiver.goto(base)
            expect(sender.locator("x-peer")).to_have_count(1, timeout=30000)
            expect(receiver.locator("x-peer")).to_have_count(1, timeout=30000)
            sender.locator('x-peer input[type="file"]').set_input_files(declined)
            expect(receiver.locator("#decline-request")).to_be_visible(timeout=30000)
            receiver.locator("#decline-request").click()
            expect(receiver.locator("#receive-request-dialog")).not_to_be_visible()
            # The vendor dialog completes its close animation before accepting another request.
            expect(receiver.locator("#accept-request")).to_be_disabled()
            expect(sender.locator("x-peer")).not_to_have_attribute("status", "wait")
            assert not downloads
            sender.locator('x-peer input[type="file"]').set_input_files(fixture)
            expect(receiver.locator("#accept-request")).to_be_visible(timeout=15000)
            with receiver.expect_download(timeout=30000) as event:
                receiver.locator("#accept-request").click()
            downloaded = event.value
            assert downloaded.suggested_filename == fixture.name
            assert hashlib.sha256(Path(downloaded.path()).read_bytes()).digest() == hashlib.sha256(payload).digest()
            for page in (sender, receiver):
                connections = page.evaluate("odsConnections.map(c => ({state:c.connectionState, servers:c.getConfiguration().iceServers}))")
                assert connections and any(c["state"] == "connected" for c in connections)
                assert all(c["servers"] == [] for c in connections)
            browser.close()
    finally:
        run(*command, "down", "--timeout", "10")
        run("docker", "network", "rm", name)
