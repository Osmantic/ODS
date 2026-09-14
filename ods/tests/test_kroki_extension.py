"""Real rendering and safe include handling through the installed Kroki API."""

import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import threading
import uuid
import xml.etree.ElementTree as ET
import zlib

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "extensions/library/services/kroki"


def test_kroki_catalog_exposes_local_renderer(tmp_path):
    output = tmp_path / "catalog.json"
    subprocess.run([sys.executable, str(ROOT / "scripts/generate-extensions-catalog.py"),
                    "--output", str(output)], check=True)
    entry = next(item for item in json.loads(output.read_text())["extensions"] if item["id"] == "kroki")
    assert (entry["port"], entry["external_port_default"], entry["health_endpoint"]) == (8000, 7829, "/health")
    checked = json.loads((ROOT / "config/extensions-catalog.json").read_text())["extensions"]
    assert entry == next(item for item in checked if item["id"] == "kroki")


def assert_svg(response, labels):
    assert response.status_code == 200, response.text[:300]
    assert response.headers["content-type"].startswith("image/svg+xml")
    root = ET.fromstring(response.content)
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    text = " ".join(root.itertext())
    assert all(label in text for label in labels)


@pytest.mark.skipif(os.environ.get("ODS_TEST_KROKI_DOCKER") != "1",
                    reason="Opt-in exact-image rendering and include-isolation test")
def test_diagrams_request_limits_and_include_isolation(tmp_path):
    name = "ods-kroki-test-" + uuid.uuid4().hex[:10]
    installed = tmp_path / "extensions/services/kroki"
    shutil.copytree(SERVICE, installed)
    canary = tmp_path / "canary.puml"
    canary.write_text("Alice -> Bob : ODS_CANARY_MUST_NOT_RENDER\n")
    seen_requests = []

    class SourceHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen_requests.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(canary.read_bytes())

        def log_message(self, *_args):
            return

    source = ThreadingHTTPServer(("0.0.0.0", 0), SourceHandler)
    thread = threading.Thread(target=source.serve_forever, daemon=True)
    thread.start()
    overlay = tmp_path / "isolation.yaml"
    overlay.write_text(f'''services:
  kroki:
    container_name: {name}
    ports: !override ["127.0.0.1:0:8000"]
    extra_hosts: ["host.docker.internal:host-gateway"]
    volumes: ["{canary}:/canary.puml:ro"]
networks:
  ods-network:
    name: {name}
''')
    command = ["docker", "compose", "--project-name", name, "--project-directory", str(tmp_path),
               "-f", str(installed / "compose.yaml"), "-f", str(overlay)]

    def run(*args):
        result = subprocess.run(args, capture_output=True, text=True, timeout=360)
        if result.returncode != 0 and "up" in args:
            logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True, timeout=30)
            print(logs.stdout + logs.stderr)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    run("docker", "network", "create", name)
    try:
        plan = json.loads(run(*command, "config", "--format", "json"))["services"]["kroki"]
        assert plan["ports"][0]["host_ip"] == "127.0.0.1"
        assert plan["read_only"] is True
        for _ in range(2):
            run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "180")
            origin = "http://" + run(*command, "port", "kroki", "8000")
            source_url = f"http://host.docker.internal:{source.server_port}/include.puml"
            # Prove the source is reachable; a missing network route must not
            # accidentally make an unsafe renderer pass this negative check.
            assert "ODS_CANARY" in run("docker", "exec", name, "wget", "-q", "-O", "-", source_url)
            before = len(seen_requests)
            with httpx.Client(base_url=origin, timeout=45) as client:
                health = client.get("/health")
                assert health.status_code == 200
                assert health.json()["version"]["kroki"]["number"] == "0.32.1"
                dot = 'digraph { input [label="Tài liệu"]; input -> answer; }'
                svg = client.post("/graphviz/svg", content=dot.encode(), headers={"Content-Type": "text/plain"})
                assert_svg(svg, ["Tài liệu", "answer"])
                encoded = base64.urlsafe_b64encode(zlib.compress(dot.encode())).decode()
                assert_svg(client.get("/graphviz/svg/" + encoded), ["Tài liệu", "answer"])
                png = client.post("/graphviz/png", content=dot.encode(), headers={"Content-Type": "text/plain"})
                assert png.status_code == 200
                assert png.content.startswith(b"\x89PNG\r\n\x1a\n")
                assert all(size > 0 for size in struct.unpack(">II", png.content[16:24]))
                for target in (source_url, "/canary.puml"):
                    uml = f"@startuml\n!include {target}\nAlice -> Bob : Local diagram\n@enduml"
                    rendered = client.post("/plantuml/svg", content=uml, headers={"Content-Type": "text/plain"})
                    assert_svg(rendered, ["Alice", "Bob", "Local diagram"])
                    assert "ODS_CANARY_MUST_NOT_RENDER" not in " ".join(ET.fromstring(rendered.content).itertext())
                assert len(seen_requests) == before
                malformed = client.post("/graphviz/svg", content="digraph { -> }", headers={"Content-Type": "text/plain"})
                assert malformed.status_code == 400
                oversized = client.post("/graphviz/svg", content=b"x" * 1048577,
                                        headers={"Content-Type": "text/plain"})
                assert oversized.status_code == 413
    finally:
        source.shutdown()
        source.server_close()
        thread.join(timeout=5)
        run(*command, "down", "--timeout", "10")
        run("docker", "network", "rm", name)
