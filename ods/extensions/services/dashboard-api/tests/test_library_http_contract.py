"""Exercise installed GPU library discovery and Compose probes at HTTP boundaries.

The pinned image's 0.8.0 application has /docs but no /health, and its CMD
uses CONTAINER_PORT (default 8020), not PORT. The server below models only
that external HTTP contract. InvokeAI 6.11.1 exposes /api/v1/app/version on
9090, not /health. These peers do not run speech or image inference.
"""

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
from urllib.parse import urlsplit

import pytest
import aiohttp

import config
import helpers


@pytest.fixture(params=[("xtts", "nvidia"), ("xtts", "amd"),
                        ("invokeai", "nvidia"), ("invokeai", "amd")])
def library_installation(tmp_path, monkeypatch, request):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI is required to render the shipped recipe")
    service_id, backend = request.param
    source = Path(__file__).resolve().parents[4] / "extensions/library/services" / service_id
    installed = tmp_path / "extensions" / service_id
    shutil.copytree(source, installed)
    env_file = tmp_path / "empty.env"
    env_file.write_text("")
    monkeypatch.setattr(config, "INSTALL_DIR", str(tmp_path))
    monkeypatch.delenv(f"{service_id.upper()}_HOST", raising=False)
    definitions, _, errors = config.load_extension_manifests(installed.parent, backend)
    assert errors == []
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("XTTS_", "INVOKEAI_")) and key != "BIND_ADDRESS"}

    def render(host_port):
        result = subprocess.run(
            ["docker", "compose", "--env-file", str(env_file), "--project-directory", str(tmp_path),
             "-f", str(installed / "compose.yaml"),
             "-f", str(installed / f"compose.{backend}.yaml"), "config", "--format", "json"],
            env={**environment, **({f"{service_id.upper()}_PORT": str(host_port)} if host_port else {})},
            capture_output=True, text=True, check=True, timeout=20,
        )
        return json.loads(result.stdout)["services"][service_id]

    return service_id, definitions[service_id], render


@pytest.mark.parametrize("host_port", [None, 38100])
def test_published_and_discovered_port_match_pinned_image_listener(library_installation, host_port):
    service_id, service, render = library_installation
    compose = render(host_port)
    # Exact image CMD: -p ${CONTAINER_PORT:-8020}; PORT is not consumed.
    listener = 8020 if service_id == "xtts" else 9090
    published = host_port or (8100 if service_id == "xtts" else 9090)
    assert compose["ports"] == [{"mode": "ingress", "host_ip": "127.0.0.1",
                                 "target": listener, "published": str(published), "protocol": "tcp"}]
    assert service["port"] == listener


@pytest.fixture
def library_http_peer():
    paths = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            paths.append(self.path)
            self.send_response(200 if self.path in ("/docs", "/api/v1/app/version") else 404)
            self.end_headers()

    server = HTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server.server_port, paths
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


@pytest.mark.asyncio
async def test_compose_and_dashboard_probe_existing_application_route(
    library_installation, library_http_peer, monkeypatch,
):
    service_id, service, render = library_installation
    compose = render(None)
    port, paths = library_http_peer
    command = compose["healthcheck"]["test"]
    assert command[:3] == ["CMD", "python3", "-c"]
    # Only remap the isolated listener; execute the shipped probe and path.
    probe_url = re.search(r"urlopen\('([^']+)'", command[3]).group(1)
    parsed = urlsplit(probe_url)
    assert parsed.hostname == "127.0.0.1"
    assert parsed.port == (8020 if service_id == "xtts" else 9090)
    probe = command[3].replace(parsed.netloc, f"127.0.0.1:{port}")
    subprocess.run([command[1], "-c", probe], check=True, capture_output=True, text=True, timeout=10)
    service = {**service, "host": "127.0.0.1", "health_port": port}
    async with aiohttp.ClientSession() as session:
        async def health_session():
            return session
        monkeypatch.setattr(helpers, "_get_aio_session", health_session)
        status = await helpers.check_service_health(service_id, service)
    assert status.status == "healthy"
    expected = "/docs" if service_id == "xtts" else "/api/v1/app/version"
    assert paths == [expected, expected]
