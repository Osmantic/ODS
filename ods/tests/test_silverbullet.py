"""SilverBullet recipe and opt-in real authenticated file lifecycle."""

from http.cookies import SimpleCookie
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extensions/library/services/silverbullet"


@pytest.fixture
def compose_env(tmp_path):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI required")
    (tmp_path / "empty.env").write_text("")
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("SILVERBULLET_")}
    env.update(SILVERBULLET_PASSWORD=secrets.token_hex(24),
               SILVERBULLET_API_TOKEN=secrets.token_hex(24), BIND_ADDRESS="0.0.0.0")
    return env


def render(tmp_path, env, check=True):
    return subprocess.run([
        "docker", "compose", "--env-file", str(tmp_path / "empty.env"),
        "--project-directory", str(tmp_path), "-f", str(EXTENSION / "compose.yaml"),
        "config", "--format", "json", "silverbullet",
    ], env=env, check=check, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("port", ["3034", "13034"])
def test_private_single_space_recipe(tmp_path, compose_env, port):
    compose_env["SILVERBULLET_PORT"] = port
    service = json.loads(render(tmp_path, compose_env).stdout)["services"]["silverbullet"]
    assert service["ports"][0]["host_ip"] == "127.0.0.1"
    assert service["ports"][0]["published"] == port
    assert service["ports"][0]["target"] == 3000
    assert service["user"] == "1000:1000" and service["read_only"]
    assert service["volumes"][0]["source"] == str(tmp_path / "data/silverbullet")
    assert service["environment"]["SB_SHELL_BACKEND"] == "off"
    assert service["environment"]["SB_RUNTIME_API"] == "0"
    assert service["healthcheck"]["test"][-1] == "http://127.0.0.1:3000/.ping"
    assert service["entrypoint"] == ["/sbin/tini", "--", "/bin/sh", "-euc"]
    assert "--single /space" in service["command"][0]


@pytest.mark.parametrize("key", ["SILVERBULLET_PASSWORD", "SILVERBULLET_API_TOKEN"])
@pytest.mark.parametrize("value", [None, ""])
def test_missing_auth_blocks_activation(tmp_path, compose_env, key, value):
    if value is None:
        del compose_env[key]
    else:
        compose_env[key] = value
    result = render(tmp_path, compose_env, check=False)
    assert result.returncode != 0 and key in result.stderr


@pytest.mark.parametrize("key", ["SILVERBULLET_PASSWORD", "SILVERBULLET_API_TOKEN"])
@pytest.mark.parametrize("value", ["short", "x" * 129, "bad\ncredential", "no:ambiguous:value"])
def test_invalid_auth_stops_before_startup(tmp_path, compose_env, key, value):
    service = json.loads(render(tmp_path, compose_env).stdout)["services"]["silverbullet"]
    compose_env[key] = value
    command = service["command"][0].replace("$$", "$")
    result = subprocess.run(["/bin/sh", "-euc", command], env=compose_env,
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 1 and key in result.stderr
    assert value not in result.stderr and "/silverbullet: not found" not in result.stderr


@pytest.mark.skipif(os.getenv("ODS_TEST_SILVERBULLET") != "1", reason="Opt-in real Markdown workspace")
def test_live_login_files_rotation_and_cold_restore(tmp_path, compose_env):
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        pytest.skip("Host-bind qualification prepares isolated UID 1000 storage")
    project = "ods-q40-silverbullet-" + uuid.uuid4().hex[:12]
    plan = json.loads(render(tmp_path, compose_env).stdout)
    service = plan["services"]["silverbullet"]
    service.update(container_name=project, restart="no")
    service["ports"][0]["published"] = "0"
    service["labels"] = {"io.ods.quality40.validation": "true"}
    plan["networks"] = {"ods-network": {"name": project + "-network"}}
    data = tmp_path / "data/silverbullet"
    data.mkdir(parents=True)
    os.chown(data, 1000, 1000)
    config = tmp_path / "isolated.json"
    config.write_text(json.dumps(plan))
    config.chmod(0o600)
    command = ["docker", "compose", "-p", project, "-f", str(config)]
    password, token = compose_env["SILVERBULLET_PASSWORD"], compose_env["SILVERBULLET_API_TOKEN"]
    rotated_password, rotated_token = secrets.token_hex(24), secrets.token_hex(24)

    def run(*args):
        result = subprocess.run(args, env=compose_env, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    def base_url():
        binding = json.loads(run("docker", "inspect", project))[0]["NetworkSettings"]["Ports"]["3000/tcp"][0]
        assert binding["HostIp"] == "127.0.0.1"
        return "http://127.0.0.1:" + binding["HostPort"]

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(base, method, path, credential=None, payload=None, expected=200, cookie=None, form=False):
        headers = {"X-Sync-Mode": "true"}
        if credential:
            headers["Authorization"] = "Bearer " + credential
        if cookie:
            headers["Cookie"] = cookie
        if form:
            payload = urllib.parse.urlencode(payload).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif payload is not None and not isinstance(payload, bytes):
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
            return body, response.headers

    def login(base, secret, expected):
        body, headers = request(base, "POST", "/.auth", payload={"username": "ods", "password": secret}, form=True)
        assert json.loads(body)["status"] == expected
        cookies = SimpleCookie()
        for value in headers.get_all("Set-Cookie", []):
            cookies.load(value)
        return "; ".join(f"{key}={value.value}" for key, value in cookies.items())

    page = "# Local experiment\n\nGhi chú riêng tư: 你好\n".encode()
    attachment = bytes(range(256)) + b"\x00\xff local attachment"
    page_path = "/.fs/Research/Local%20experiment.md"
    attachment_path = "/.fs/Research/fixture.bin"
    try:
        run(*command, "up", "-d", "--wait", "--wait-timeout", "60")
        base = base_url()
        # Native cookie names include host and port. Keep the OS-allocated
        # port across recreation, as the production recipe's fixed port does.
        service["ports"][0]["published"] = str(urllib.parse.urlsplit(base).port)
        config.write_text(json.dumps(plan))
        request(base, "GET", "/.ping")
        for credential in (None, "incorrect"):
            request(base, "GET", "/.fs", credential, expected=401)
            request(base, "PUT", page_path, credential, page, expected=401)
        assert not login(base, "incorrect", "error")
        cookie = login(base, password, "ok")
        assert cookie
        request(base, "GET", "/.config", cookie=cookie)
        request(base, "PUT", page_path, token, page)
        request(base, "PUT", attachment_path, token, attachment)
        assert request(base, "GET", page_path, cookie=cookie)[0] == page
        assert request(base, "GET", attachment_path, token)[0] == attachment
        listing = json.loads(request(base, "GET", "/.fs", token)[0])
        assert any(item["name"] == "Research/Local experiment.md" for item in listing)
        shell = request(base, "POST", "/.shell", token, {"cmd": "touch", "args": ["/space/unwanted"]}, expected=405)[0]
        assert b"disabled" in shell and not (data / "unwanted").exists()
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "60")
        base = base_url()
        assert request(base, "GET", page_path, cookie=cookie)[0] == page
        assert request(base, "GET", attachment_path, token)[0] == attachment
        service["environment"].update(SILVERBULLET_PASSWORD=rotated_password, SILVERBULLET_API_TOKEN=rotated_token)
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "60")
        base = base_url()
        assert not login(base, password, "error")
        request(base, "GET", page_path, cookie=cookie, expected=401)
        request(base, "GET", page_path, token, expected=401)
        request(base, "PUT", page_path, token, b"must not replace", expected=401)
        new_cookie = login(base, rotated_password, "ok")
        assert request(base, "GET", page_path, cookie=new_cookie)[0] == page
        assert request(base, "GET", attachment_path, rotated_token)[0] == attachment
        run(*command, "stop", "--timeout", "30")
        backup = tmp_path / "restored-space"
        shutil.copytree(data, backup)
        for path in [backup, *backup.rglob("*")]:
            os.chown(path, 1000, 1000)
        service["volumes"][0]["source"] = str(backup)
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "60")
        base = base_url()
        assert request(base, "GET", page_path, cookie=new_cookie)[0] == page
        assert request(base, "GET", attachment_path, rotated_token)[0] == attachment
        request(base, "DELETE", attachment_path, rotated_token)
        request(base, "GET", attachment_path, rotated_token, expected=404)
        assert request(base, "GET", page_path, rotated_token)[0] == page
    finally:
        try:
            logs = subprocess.run(["docker", "logs", project], capture_output=True, text=True, check=True, timeout=15)
            diagnostics = logs.stdout + logs.stderr
            assert all(value not in diagnostics for value in (password, token, rotated_password, rotated_token))
        finally:
            run(*command, "down", "--volumes", "--timeout", "30")
