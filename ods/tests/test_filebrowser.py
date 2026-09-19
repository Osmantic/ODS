"""File Browser recipe and opt-in native file/account lifecycle."""

import asyncio
import base64
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import uuid

import aiohttp
import pytest

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extensions/library/services/filebrowser"


@pytest.fixture
def compose_env(tmp_path):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI required")
    (tmp_path / "empty.env").write_text("")
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("FILEBROWSER_")}
    env.update(FILEBROWSER_PASSWORD=secrets.token_hex(24), BIND_ADDRESS="127.0.0.1")
    return env


def render(tmp_path, env, check=True):
    return subprocess.run([
        "docker", "compose", "--env-file", str(tmp_path / "empty.env"),
        "--project-directory", str(tmp_path), "-f", str(EXTENSION / "compose.yaml"),
        "config", "--format", "json", "filebrowser",
    ], env=env, check=check, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("bind_address", [None, "127.0.0.1", "0.0.0.0", "192.0.2.42"])
def test_bind_address_opt_in_preserves_native_configuration(tmp_path, compose_env, bind_address):
    compose_env.pop("BIND_ADDRESS", None)
    baseline = json.loads(render(tmp_path, compose_env).stdout)["services"]["filebrowser"]
    if bind_address is not None:
        compose_env["BIND_ADDRESS"] = bind_address
    service = json.loads(render(tmp_path, compose_env).stdout)["services"]["filebrowser"]
    assert all(port["host_ip"] == (bind_address or "127.0.0.1") for port in service["ports"])
    assert [(p["published"], p["target"]) for p in service["ports"]] == [
        (p["published"], p["target"]) for p in baseline["ports"]]
    assert service["environment"] == baseline["environment"]
    assert service["volumes"] == baseline["volumes"]


@pytest.mark.parametrize("port", ["8095", "18095"])
def test_isolated_files_and_account_storage(tmp_path, compose_env, port):
    compose_env["FILEBROWSER_PORT"] = port
    service = json.loads(render(tmp_path, compose_env).stdout)["services"]["filebrowser"]
    assert service["ports"][0]["host_ip"] == "127.0.0.1"
    assert service["ports"][0]["published"] == port
    assert service["ports"][0]["target"] == 8080
    assert service["user"] == "1000:1000" and service["read_only"]
    assert {v["target"]: v["source"] for v in service["volumes"]} == {
        "/srv": str(tmp_path / "data/filebrowser/files"),
        "/database": str(tmp_path / "data/filebrowser/database"),
        "/config": str(tmp_path / "data/filebrowser/config"),
    }
    assert service["environment"]["FB_DISABLE_EXEC"] == "true"
    assert service["environment"]["FB_FOLLOW_EXTERNAL_SYMLINKS"] == "false"
    assert service["environment"]["FB_TOKEN_EXPIRATION_TIME"] == "30m"
    assert service["healthcheck"]["test"][-1] == "http://127.0.0.1:8080/health"


@pytest.mark.parametrize("value", [None, ""])
def test_missing_password_blocks_activation(tmp_path, compose_env, value):
    if value is None:
        del compose_env["FILEBROWSER_PASSWORD"]
    else:
        compose_env["FILEBROWSER_PASSWORD"] = value
    result = render(tmp_path, compose_env, check=False)
    assert result.returncode != 0 and "FILEBROWSER_PASSWORD" in result.stderr


@pytest.mark.parametrize("value", ["short", "x" * 73, "bad\ncredential", "no:ambiguous:value"])
def test_invalid_password_stops_before_native_hash(tmp_path, compose_env, value):
    service = json.loads(render(tmp_path, compose_env).stdout)["services"]["filebrowser"]
    compose_env["FILEBROWSER_PASSWORD"] = value
    command = service["command"][0].replace("$$", "$")
    result = subprocess.run(["/bin/bash", "-euc", command], env=compose_env,
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 1 and "FILEBROWSER_PASSWORD" in result.stderr
    assert value not in result.stderr and "not found" not in result.stderr


@pytest.mark.skipif(os.getenv("ODS_TEST_FILEBROWSER") != "1", reason="Opt-in real file workspace")
def test_live_files_permissions_password_rotation_and_restore(tmp_path, compose_env):
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        pytest.skip("Host-bind qualification prepares isolated UID 1000 storage")
    project = "ods-q40-filebrowser-" + uuid.uuid4().hex[:12]
    plan = json.loads(render(tmp_path, compose_env).stdout)
    service = plan["services"]["filebrowser"]
    service.update(container_name=project, restart="no")
    service["ports"][0]["published"] = "0"
    service["labels"] = {"io.ods.quality40.validation": "true"}
    plan["networks"] = {"ods-network": {"name": project + "-network"}}
    for volume in service["volumes"]:
        path = Path(volume["source"])
        path.mkdir(parents=True)
        os.chown(path, 1000, 1000)
    data = tmp_path / "data/filebrowser"
    config = tmp_path / "isolated.json"
    config.write_text(json.dumps(plan))
    config.chmod(0o600)
    command = ["docker", "compose", "-p", project, "-f", str(config)]
    password = compose_env["FILEBROWSER_PASSWORD"]
    new_seed, rotated, viewer_password = [secrets.token_hex(24) for _ in range(3)]

    def run(*args):
        result = subprocess.run(args, env=compose_env, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    def base_url():
        binding = json.loads(run("docker", "inspect", project))[0]["NetworkSettings"]["Ports"]["8080/tcp"][0]
        assert binding["HostIp"] == "127.0.0.1"
        return "http://127.0.0.1:" + binding["HostPort"]

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(base, method, path, token=None, payload=None, expected=200):
        headers = {"X-Auth": token} if token else {}
        if payload is not None and not isinstance(payload, bytes):
            payload = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(base + path, data=payload, method=method, headers=headers)
        try:
            response = opener.open(req, timeout=15)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            body = response.read()
            assert response.status == expected, f"{method} {path}: {response.status}: {body!r}"
            return body

    def login(base, username, secret, expected=200):
        return request(base, "POST", "/api/login", payload={
            "username": username, "password": secret, "recaptcha": "",
        }, expected=expected).decode()

    async def denied_shell(base, token):
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(base + "/api/command", headers={"X-Auth": token}) as websocket:
                await websocket.send_str("touch /srv/unwanted")
                message = await websocket.receive(timeout=10)
                assert message.type == aiohttp.WSMsgType.TEXT
                assert message.data == "Command not allowed."

    note = "Private local files: tiếng Việt, 你好\n".encode()
    binary = bytes(range(256)) + b"\x00\xff attachment"
    try:
        run(*command, "up", "-d", "--wait", "--wait-timeout", "60")
        base = base_url()
        request(base, "GET", "/health")
        assert b"<html" in request(base, "GET", "/").lower()
        for token in (None, "incorrect"):
            request(base, "GET", "/api/resources/", token, expected=401)
            request(base, "POST", "/api/resources/no.txt", token, b"denied", expected=401)
        login(base, "ods", "incorrect", expected=403)
        admin = login(base, "ods", password)
        claims = json.loads(base64.urlsafe_b64decode(admin.split(".")[1] + "===").decode())
        assert 0 < claims["exp"] - time.time() <= 1800
        request(base, "POST", "/api/resources/shared/", admin, b"")
        request(base, "POST", "/api/resources/shared/note.txt", admin, note)
        request(base, "POST", "/api/resources/shared/data.bin", admin, binary)
        request(base, "POST", "/api/resources/private.txt", admin, b"outside viewer scope")
        request(base, "POST", "/api/resources/shared/note.txt", admin, b"must not overwrite", expected=409)
        assert request(base, "GET", "/api/raw/shared/note.txt", admin) == note
        listing = json.loads(request(base, "GET", "/api/resources/shared/", admin))
        assert {item["name"] for item in listing["items"]} == {"note.txt", "data.bin"}
        request(base, "POST", "/api/users", admin, {
            "what": "user", "which": [], "current_password": password,
            "data": {"username": "viewer", "password": viewer_password,
                     "scope": "shared", "perm": {"download": True}},
        }, expected=201)
        users = json.loads(request(base, "GET", "/api/users", admin))
        viewer_id = next(u["id"] for u in users if u["username"] == "viewer")
        viewer = login(base, "viewer", viewer_password)
        assert request(base, "GET", "/api/raw/note.txt", viewer) == note
        assert request(base, "GET", "/api/raw/data.bin", viewer) == binary
        request(base, "GET", "/api/raw/private.txt", viewer, expected=404)
        request(base, "POST", "/api/resources/injected.txt", viewer, b"denied", expected=403)
        request(base, "DELETE", "/api/resources/note.txt", viewer, expected=403)
        request(base, "GET", "/api/users", viewer, expected=403)
        asyncio.run(denied_shell(base, admin))
        assert not (data / "files/unwanted").exists()
        # Recreating with a different bootstrap seed does not replace existing accounts.
        service["environment"]["FILEBROWSER_PASSWORD"] = new_seed
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "60")
        base = base_url()
        login(base, "ods", new_seed, expected=403)
        admin = login(base, "ods", password)
        assert request(base, "GET", "/api/raw/data.bin", viewer) == binary
        admin_id = claims["user"]["id"]
        request(base, "PUT", f"/api/users/{admin_id}", admin, {
            "what": "user", "which": ["password"], "current_password": password,
            "data": {"id": admin_id, "password": rotated},
        })
        login(base, "ods", password, expected=403)
        new_admin = login(base, "ods", rotated)
        # Native password rotation does not revoke a token or its renewal ability.
        assert request(base, "GET", "/api/raw/shared/note.txt", admin) == note
        assert request(base, "POST", "/api/renew", admin).count(b".") == 2
        run(*command, "stop", "--timeout", "30")
        backup = tmp_path / "restored"
        shutil.copytree(data, backup)
        for path in [backup, *backup.rglob("*")]:
            os.chown(path, 1000, 1000)
        for volume in service["volumes"]:
            volume["source"] = str(backup / Path(volume["source"]).name)
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "60")
        base = base_url()
        login(base, "ods", password, expected=403)
        new_admin = login(base, "ods", rotated)
        assert request(base, "GET", "/api/raw/data.bin", viewer) == binary
        request(base, "POST", "/api/resources/denied.txt", viewer, b"denied", expected=403)
        request(base, "DELETE", f"/api/users/{viewer_id}", new_admin, {"current_password": rotated})
        login(base, "viewer", viewer_password, expected=403)
        # Exact v2.63.23 returns 500 for an otherwise valid JWT whose user was deleted.
        request(base, "GET", "/api/raw/note.txt", viewer, expected=500)
        request(base, "POST", "/api/renew", viewer, expected=500)
        request(base, "DELETE", "/api/resources/shared/data.bin", new_admin, expected=204)
        request(base, "GET", "/api/raw/shared/data.bin", new_admin, expected=404)
        assert request(base, "GET", "/api/raw/shared/note.txt", new_admin) == note
    finally:
        try:
            logs = subprocess.run(["docker", "logs", project], capture_output=True, text=True, check=True, timeout=15)
            diagnostics = logs.stdout + logs.stderr
            assert all(value not in diagnostics for value in (password, new_seed, rotated, viewer_password))
        finally:
            run(*command, "down", "--volumes", "--timeout", "30")
