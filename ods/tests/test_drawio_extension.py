"""Real diagram editing and lossless reopen through the editor's embed protocol."""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import unquote, urlsplit
import uuid
import xml.etree.ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "extensions/library/services/drawio"


def test_drawio_catalog_matches_the_installed_listener(tmp_path):
    output = tmp_path / "catalog.json"
    subprocess.run([sys.executable, str(ROOT / "scripts/generate-extensions-catalog.py"), "--output", str(output)], check=True)
    entry = next(item for item in json.loads(output.read_text())["extensions"] if item["id"] == "drawio")
    checked = json.loads((ROOT / "config/extensions-catalog.json").read_text())
    assert entry == next(item for item in checked["extensions"] if item["id"] == "drawio")
    assert (entry["port"], entry["external_port_default"]) == (8080, 8106)


@pytest.mark.skipif(os.environ.get("ODS_TEST_DRAWIO_BROWSER") != "1", reason="Opt-in Docker/Chromium diagram test")
def test_browser_edits_saves_exports_and_reopens_a_diagram(tmp_path):
    from playwright.sync_api import expect, sync_playwright

    name = "ods-drawio-test-" + uuid.uuid4().hex[:10]
    installed = tmp_path / "extensions/services/drawio"
    shutil.copytree(SERVICE, installed)
    overlay = tmp_path / "isolation.yaml"
    overlay.write_text(f'''services:
  drawio:
    container_name: {name}
    ports: !override ["127.0.0.1:0:8080"]
networks:
  ods-network:
    name: {name}
''')
    command = ["docker", "compose", "--project-name", name, "--project-directory", str(tmp_path),
               "-f", str(installed / "compose.yaml"), "-f", str(overlay)]

    def run(*args):
        result = subprocess.run(args, capture_output=True, text=True, timeout=180)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    diagram = '''<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>
      <mxCell id="2" value="Local model" style="rounded=1;whiteSpace=wrap;html=1;" vertex="1" parent="1">
        <mxGeometry x="80" y="80" width="160" height="60" as="geometry"/></mxCell>
      <mxCell id="3" value="Private result" style="rounded=1;whiteSpace=wrap;html=1;" vertex="1" parent="1">
        <mxGeometry x="340" y="80" width="160" height="60" as="geometry"/></mxCell>
      <mxCell id="4" edge="1" parent="1" source="2" target="3"><mxGeometry relative="1" as="geometry"/></mxCell>
      </root></mxGraphModel>'''
    run("docker", "network", "create", name)
    try:
        run(*command, "up", "-d", "--wait", "--wait-timeout", "90")
        base = "http://" + run(*command, "port", "drawio", "8080")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(viewport={"width": 1400, "height": 1000}, locale="en-US")
            requests, errors = [], []
            context.on("request", lambda request: requests.append(request.url))
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            # Only the small host page is supplied by the test. The iframe is the shipped app.
            context.route(base + "/ods-test-host", lambda route: route.fulfill(content_type="text/html", body='''
              <iframe id="editor" style="width:1350px;height:900px"
                src="/?embed=1&amp;proto=json&amp;offline=1&amp;https=0&amp;ui=kennedy&amp;lang=en"></iframe>
              <script>window.receipts=[]; addEventListener('message', event => {
                if(event.origin===location.origin && event.source===editor.contentWindow)
                  receipts.push(JSON.parse(event.data));
              }); window.send = message => editor.contentWindow.postMessage(JSON.stringify(message), location.origin);</script>'''))
            page.goto(base + "/ods-test-host")
            page.wait_for_function("receipts.some(r=>r.event==='init')", timeout=45000)
            page.evaluate("xml=>send({action:'load',xml,fit:1})", diagram)
            editor = page.frame_locator("#editor")
            label = editor.get_by_text("Local model", exact=True)
            expect(label).to_be_visible(timeout=30000)
            label.dblclick()
            editable = editor.locator(".mxCellEditor")
            expect(editable).to_be_visible()
            editable.fill("Local model — verified")
            editable.press("Control+Enter")
            expect(editor.get_by_text("Local model — verified", exact=True)).to_be_visible()
            editor.locator("body").press("Control+s")
            page.wait_for_function("receipts.some(r=>r.event==='save')")
            saved = page.evaluate("receipts.find(r=>r.event==='save').xml")
            assert "verified" in saved
            page.evaluate("send({action:'export',format:'svg'})")
            page.wait_for_function("receipts.some(r=>r.event==='export')", timeout=30000)
            exported = page.evaluate("receipts.find(r=>r.event==='export').data")
            prefix, content = exported.split(",", 1)
            svg = base64.b64decode(content).decode() if ";base64" in prefix else unquote(content)
            assert ET.fromstring(svg).tag == "{http://www.w3.org/2000/svg}svg"
            assert "verified" in svg and "Private result" in svg
            policy = editor.locator('meta[http-equiv="Content-Security-Policy"]').get_attribute("content")
            assert "connect-src 'self'" in policy and "https://" not in policy
            page.reload()
            page.wait_for_function("receipts.some(r=>r.event==='init')", timeout=45000)
            page.evaluate("xml=>send({action:'load',xml,fit:1})", saved)
            expect(page.frame_locator("#editor").get_by_text("Local model — verified", exact=True)).to_be_visible(timeout=30000)
            expect(page.frame_locator("#editor").get_by_text("Private result", exact=True)).to_be_visible()
            assert not errors
            assert all(urlsplit(url).netloc == urlsplit(base).netloc for url in requests if url.startswith(("http:", "https:")))
            browser.close()
    finally:
        run(*command, "down", "--timeout", "10")
        run("docker", "network", "rm", name)
