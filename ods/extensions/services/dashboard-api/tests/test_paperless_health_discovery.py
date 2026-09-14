"""The shipped Paperless health target must exist on its rendered Docker network."""

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import aiohttp
import pytest

import config
import helpers


@pytest.fixture
def paperless_installation(tmp_path, monkeypatch):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI is required to render the shipped definition")
    source = Path(__file__).resolve().parents[4] / "extensions/library/services/paperless-ngx"
    installed = tmp_path / "extensions" / "paperless-ngx"
    shutil.copytree(source, installed)
    env_file = tmp_path / "empty.env"
    env_file.write_text("")
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PAPERLESS_")}
    environment["PAPERLESS_SECRET_KEY"] = "paperless-health-regression-key"
    rendered = subprocess.run(
        ["docker", "compose", "--env-file", str(env_file), "--project-directory", str(tmp_path),
         "-f", str(installed / "compose.yaml"), "config", "--format", "json"],
        env=environment, capture_output=True, text=True, check=True, timeout=20,
    )
    app = json.loads(rendered.stdout)["services"]["paperless-ngx"]
    aliases = (app["networks"]["ods-network"] or {}).get("aliases", [])
    names = {"paperless-ngx", app["container_name"], *aliases}
    monkeypatch.setattr(config, "INSTALL_DIR", str(tmp_path))
    monkeypatch.delenv("PAPERLESS_HOST", raising=False)
    return installed.parent, names


@pytest.fixture
def login_server():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            requests.append(self.path)
            self.send_response(200 if self.path == "/accounts/login/" else 404)
            self.end_headers()

    server = HTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server.server_port, requests
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


@pytest.mark.asyncio
@pytest.mark.parametrize("host_override", [None, "paperless-ngx"])
async def test_installed_paperless_health_resolves_rendered_network_identity(
    paperless_installation, login_server, monkeypatch, host_override,
):
    directory, names = paperless_installation
    if host_override:
        monkeypatch.setenv("PAPERLESS_HOST", host_override)
    services, _, errors = config.load_extension_manifests(directory, "nvidia")
    assert errors == []
    service = services["paperless-ngx"]
    port, requests = login_server
    service["health_port"] = port  # Route the isolated HTTP peer through an ephemeral port.
    resolved = []

    class ComposeResolver(aiohttp.abc.AbstractResolver):
        async def resolve(self, host, port=0, family=socket.AF_INET):
            resolved.append(host)
            if host not in names:
                raise OSError(socket.EAI_NONAME, "Name or service not known")
            return [{"hostname": host, "host": "127.0.0.1", "port": port,
                     "family": socket.AF_INET, "proto": 0, "flags": 0}]

        async def close(self):
            pass

    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(resolver=ComposeResolver())) as session:
        async def health_session():
            return session

        monkeypatch.setattr(helpers, "_get_aio_session", health_session)
        status = await helpers.check_service_health("paperless-ngx", service)
    assert status.status == "healthy"
    assert resolved == [host_override or "paperless"]
    assert requests == ["/accounts/login/"]
