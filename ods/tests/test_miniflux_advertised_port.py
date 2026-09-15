"""Custom Miniflux listener ports must reach the links users actually copy."""

import html
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import uuid

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT / "extensions/library/services/miniflux/compose.yaml"


def render(tmp_path, port, base_url=None):
    env = {key: value for key, value in os.environ.items() if not key.startswith("MINIFLUX_") and key != "BIND_ADDRESS"}
    env.update(MINIFLUX_ADMIN_PASSWORD=secrets.token_hex(24), MINIFLUX_DB_PASSWORD=secrets.token_hex(24))
    if port is not None:
        env["MINIFLUX_PORT"] = str(port)
    if base_url is not None:
        env["MINIFLUX_BASE_URL"] = base_url
    empty = tmp_path / "empty.env"
    empty.write_text("")
    result = subprocess.run(["docker", "compose", "--env-file", str(empty), "--project-directory", str(tmp_path),
                             "-f", str(RECIPE), "config", "--format", "json"],
                            env=env, check=True, capture_output=True, text=True, timeout=30)
    return json.loads(result.stdout)


@pytest.mark.parametrize("port", [None, "", "18098"])
@pytest.mark.parametrize("base_url", [None, "", "https://feeds.example.test/reader"])
def test_advertised_origin_follows_port_without_overriding_explicit_url(tmp_path, port, base_url):
    service = render(tmp_path, port, base_url)["services"]["miniflux"]
    assert service["ports"][0]["published"] == (port or "8098")
    assert service["environment"]["BASE_URL"] == (base_url or f"http://localhost:{port or '8098'}")


def test_catalog_form_default_does_not_pin_the_old_port(tmp_path):
    catalog = json.loads((ROOT / "config/extensions-catalog.json").read_text())
    entry = next(item for item in catalog["extensions"] if item["id"] == "miniflux")
    variable = next(item for item in entry["env_vars"] if item["key"] == "MINIFLUX_BASE_URL")
    service = render(tmp_path, "18098", variable.get("default", ""))["services"]["miniflux"]
    assert service["environment"]["BASE_URL"] == "http://localhost:18098"


@pytest.mark.skipif(os.getenv("ODS_TEST_MINIFLUX_LINKS") != "1", reason="Opt-in native Miniflux Web UI contract")
def test_native_integrations_page_advertises_reachable_custom_port_links(tmp_path):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    project = "ods-q20-miniflux-links-" + uuid.uuid4().hex[:12]
    plan = render(tmp_path, port)
    for key, service in plan["services"].items():
        service.update(container_name=project + "-" + key, restart="no", labels={"io.ods.quality20.validation": "true"})
    plan["networks"]["ods-network"] = {"name": project + "-network"}
    plan["networks"]["miniflux-private"]["name"] = project + "-private"
    config = tmp_path / "isolated.json"
    config.write_text(json.dumps(plan))
    config.chmod(0o600)
    command = ["docker", "compose", "-p", project, "-f", str(config)]

    def run(*args):
        result = subprocess.run(args, capture_output=True, text=True, timeout=180)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    try:
        run(*command, "up", "-d", "--wait", "--wait-timeout", "120")
        origin = f"http://localhost:{port}"
        with httpx.Client(base_url=origin, trust_env=False, follow_redirects=True, timeout=10) as client:
            login = client.get("/")
            assert login.status_code == 200
            csrf = re.search(r'name="csrf" value="([^"]+)"', login.text)
            assert csrf, "The native login page must provide its CSRF token"
            response = client.post("/login", data={
                "csrf": html.unescape(csrf.group(1)), "username": "admin",
                "password": plan["services"]["miniflux"]["environment"]["ADMIN_PASSWORD"],
            })
            assert response.status_code == 200 and response.url.path != "/login"
            page = client.get("/integrations")
            assert page.status_code == 200
            markup = html.unescape(page.text)
            assert f"{origin}/bookmarklet?uri=" in markup
            assert f"{origin}/fever/" in markup
            # Follow the generated bookmarklet destination with an empty input:
            # it reaches the authenticated Miniflux form, not another host port.
            assert client.get(origin + "/bookmarklet").status_code == 200
    finally:
        print(run(*command, "logs", "--tail", "20"))
        run(*command, "down", "--volumes")
