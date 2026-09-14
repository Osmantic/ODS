"""Installable Kopia contract and opt-in real encrypted snapshot/restore drill."""

import base64
import http.cookiejar
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import urllib.error
import urllib.request
import uuid

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extensions/library/services/kopia"


@pytest.fixture
def compose_env(tmp_path):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI required")
    (tmp_path / "empty.env").write_text("")
    env = {key: value for key, value in os.environ.items() if not key.startswith("KOPIA_")}
    env.update(KOPIA_PASSWORD=secrets.token_hex(24), KOPIA_SERVER_PASSWORD=secrets.token_hex(24))
    return env


def render(tmp_path, env, check=True):
    return subprocess.run([
        "docker", "compose", "--env-file", str(tmp_path / "empty.env"),
        "--project-directory", str(tmp_path), "-f", str(EXTENSION / "compose.yaml"),
        "config", "--format", "json", "kopia",
    ], env=env, capture_output=True, text=True, check=check, timeout=30)


@pytest.mark.parametrize("port", ["51515", "15151"])
def test_catalog_installs_a_scoped_persistent_snapshot_service(tmp_path, compose_env, port):
    compose_env["KOPIA_PORT"] = port
    service = json.loads(render(tmp_path, compose_env).stdout)["services"]["kopia"]
    assert service["ports"][0]["published"] == port
    assert service["ports"][0]["host_ip"] == "127.0.0.1"
    mounts = {item["target"]: item for item in service["volumes"]}
    assert set(mounts) == {"/app/config", "/app/cache", "/app/logs", "/repository", "/source", "/restore"}
    assert mounts["/source"]["read_only"] is True
    for mount in mounts.values():
        assert Path(mount["source"]).is_relative_to(tmp_path / "data/kopia")
    assert not service.get("privileged")
    assert not service.get("devices")
    assert service["hostname"] == "ods-kopia"
    assert "--disable-csrf-token-checks" not in service["command"]
    assert "--no-grpc" in service["command"]
    manifest = yaml.safe_load((EXTENSION / "manifest.yaml").read_text())
    assert manifest["service"]["default_host"] == "kopia"
    assert manifest["service"]["health"] == "/metrics"
    catalog = json.loads((ROOT / "config/extensions-catalog.json").read_text())
    entry = next(item for item in catalog["extensions"] if item["id"] == "kopia")
    assert entry["features"][0]["launch"]["service"] == "kopia"
    required = {item["key"] for item in entry["env_vars"] if item.get("required")}
    assert required == {"KOPIA_PASSWORD", "KOPIA_SERVER_PASSWORD"}


@pytest.mark.parametrize("key", ["KOPIA_PASSWORD", "KOPIA_SERVER_PASSWORD"])
@pytest.mark.parametrize("empty", [True, False])
def test_missing_secret_blocks_start(tmp_path, compose_env, key, empty):
    if empty:
        compose_env[key] = ""
    else:
        del compose_env[key]
    result = render(tmp_path, compose_env, check=False)
    assert result.returncode != 0
    assert key in result.stderr


@pytest.mark.skipif(os.getenv("ODS_TEST_KOPIA") != "1", reason="Opt-in Kopia restore drill")
def test_live_authenticated_creation_snapshot_and_restore_after_recreation(tmp_path, compose_env):
    project = "ods-q40-kopia-" + uuid.uuid4().hex[:12]
    plan = json.loads(render(tmp_path, compose_env).stdout)
    service = plan["services"]["kopia"]
    service["container_name"] = project
    service["restart"] = "no"
    service["ports"] = [{"target": 51515, "published": "0", "host_ip": "127.0.0.1", "protocol": "tcp"}]
    service["labels"] = {"io.ods.quality40.validation": "true"}
    volumes = {}
    for i, mount in enumerate(service["volumes"]):
        key = f"validation-{i}"
        mount.update(type="volume", source=key)
        mount.pop("bind", None)
        volumes[key] = {"name": project + f"-data-{i}"}
    plan["volumes"] = volumes
    plan["networks"] = {"ods-network": {"name": project + "-network"}}
    config = tmp_path / "isolated.json"
    config.write_text(json.dumps(plan))
    config.chmod(0o600)
    command = ["docker", "compose", "-p", project, "-f", str(config)]

    def run(*args, check=True, input_text=None):
        result = subprocess.run(args, env=compose_env, input=input_text,
                                capture_output=True, text=True, timeout=180)
        if check:
            assert result.returncode == 0, result.stdout + result.stderr
        return result

    def base_url():
        container = json.loads(run("docker", "inspect", project).stdout)[0]
        binding = container["NetworkSettings"]["Ports"]["51515/tcp"][0]
        assert binding["HostIp"] == "127.0.0.1"
        return "http://127.0.0.1:" + binding["HostPort"]

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(jar))
    token = None

    def request(base, path, auth=False, body=None, expected=200, csrf=True):
        headers = {"Content-Type": "application/json"}
        if auth:
            credentials = ("ods:" + compose_env["KOPIA_SERVER_PASSWORD"]).encode()
            headers["Authorization"] = "Basic " + base64.b64encode(credentials).decode()
        if csrf and token:
            headers["X-Kopia-Csrf-Token"] = token
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(base + path, data=data, headers=headers)
        try:
            response = opener.open(req, timeout=30)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            assert response.status == expected, f"{path}: HTTP {response.status}"
            return response.read()

    def login(base):
        page = request(base, "/", auth=True)
        match = re.search(rb'<meta name="kopia-csrf-token" content="([a-f0-9]+)"', page)
        assert match, "Authenticated UI must supply a CSRF token"
        return match.group(1).decode()

    try:
        # Seed only the test-owned source volume, while the service itself keeps it read-only.
        source_volume = next(m["source"] for m in service["volumes"] if m["target"] == "/source")
        source_name = volumes[source_volume]["name"]
        run("docker", "volume", "create", source_name)
        run("docker", "run", "--rm", "--network", "none", "--entrypoint", "/bin/sh",
            "-v", source_name + ":/fixture", service["image"], "-c",
            "printf 'ODS synthetic restore fixture\\n' > /fixture/sample.txt")
        run(*command, "up", "-d", "--wait", "--wait-timeout", "90")
        base = base_url()
        request(base, "/metrics")
        request(base, "/", expected=401)
        request(base, "/api/v1/repo/status", expected=401)
        token = login(base)
        assert not json.loads(request(base, "/api/v1/repo/status", auth=True))["connected"]
        create = {"storage": {"type": "filesystem", "config": {"path": "/repository"}},
                  "password": compose_env["KOPIA_PASSWORD"]}
        request(base, "/api/v1/repo/create", auth=True, body=create, expected=401, csrf=False)
        request(base, "/api/v1/repo/create", auth=True, body=create)
        assert json.loads(request(base, "/api/v1/repo/status", auth=True))["connected"]
        blocked = run("docker", "exec", project, "/bin/sh", "-c", "touch /source/unwanted", check=False)
        assert blocked.returncode != 0 and "Read-only file system" in blocked.stderr
        snapshot = json.loads(run("docker", "exec", project, "kopia", "snapshot", "create", "/source", "--json").stdout)
        root_id = snapshot["rootEntry"]["obj"]
        run(*command, "stop", "--timeout", "30")
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "90")
        jar.clear()
        base = base_url()
        token = login(base)
        assert json.loads(request(base, "/api/v1/repo/status", auth=True))["connected"]
        run("docker", "exec", project, "kopia", "snapshot", "restore", root_id, "/restore/drill")
        restored = run("docker", "exec", project, "cat", "/restore/drill/sample.txt").stdout
        assert restored == "ODS synthetic restore fixture\n"
        wrong = run("docker", "exec", "-e", "KOPIA_PASSWORD=incorrect", project,
                    "kopia", "repository", "status", check=False)
        assert wrong.returncode != 0 and "invalid repository password" in wrong.stderr.lower()
        print("Auth and CSRF enforced; snapshot persisted across recreation; restore matched source; wrong encryption key rejected")
    finally:
        logs = subprocess.run(["docker", "logs", "--tail", "20", project], capture_output=True, text=True, timeout=20)
        print("Service diagnostics:", logs.stdout, logs.stderr)
        run(*command, "down", "--volumes", "--timeout", "30")
