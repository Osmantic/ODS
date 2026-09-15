"""PocketBase install/build contract and opt-in native account, rules and file lifecycle."""

import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extensions/library/services/pocketbase"


@pytest.fixture
def compose_env(tmp_path):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI required")
    (tmp_path / "empty.env").write_text("")
    env = {k: v for k, v in os.environ.items() if not k.startswith("POCKETBASE_")}
    env.update(POCKETBASE_PASSWORD=secrets.token_hex(24), POCKETBASE_ENCRYPTION_KEY=secrets.token_hex(16), BIND_ADDRESS="0.0.0.0")
    return env


def render(tmp_path, env, check=True):
    return subprocess.run([
        "docker", "compose", "--env-file", str(tmp_path / "empty.env"),
        "--project-directory", str(tmp_path), "-f", str(EXTENSION / "compose.yaml"),
        "config", "--format", "json", "pocketbase",
    ], env=env, check=check, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("port", ["8090", "18090"])
def test_local_image_and_persistent_nonroot_backend(tmp_path, compose_env, port):
    compose_env["POCKETBASE_PORT"] = port
    service = json.loads(render(tmp_path, compose_env).stdout)["services"]["pocketbase"]
    assert service["image"] == "ods-pocketbase:0.40.4-r1" and service["pull_policy"] == "never"
    assert "build" not in service
    assert service["ports"][0]["host_ip"] == "127.0.0.1"
    assert service["ports"][0]["published"] == port
    assert service["environment"]["POCKETBASE_PORT"] == port
    assert service["user"] == "1000:1000" and service["read_only"] and service["init"]
    assert service["volumes"][0]["source"] == str(tmp_path / "data/pocketbase")
    assert service["volumes"][0]["target"] == "/pb/pb_data"


@pytest.mark.parametrize("key", ["POCKETBASE_PASSWORD", "POCKETBASE_ENCRYPTION_KEY"])
@pytest.mark.parametrize("value", [None, ""])
def test_missing_secrets_block_activation(tmp_path, compose_env, key, value):
    if value is None:
        del compose_env[key]
    else:
        compose_env[key] = value
    result = render(tmp_path, compose_env, check=False)
    assert result.returncode != 0 and key in result.stderr


@pytest.mark.parametrize("key,value", [
    ("POCKETBASE_PASSWORD", "short"), ("POCKETBASE_PASSWORD", "x" * 73),
    ("POCKETBASE_PASSWORD", "bad\ncredential"), ("POCKETBASE_ENCRYPTION_KEY", "short"),
    ("POCKETBASE_ENCRYPTION_KEY", "x" * 33), ("POCKETBASE_ENCRYPTION_KEY", "!" * 32),
])
def test_invalid_secrets_stop_before_database_access(compose_env, key, value):
    compose_env[key] = value
    result = subprocess.run(["/bin/bash", str(EXTENSION / "start-pocketbase.sh")],
                            env=compose_env, capture_output=True, text=True, timeout=5)
    assert result.returncode == 1 and key in result.stderr
    assert value not in result.stderr and "/pb/" not in result.stderr


def test_setup_propagates_failed_build_with_exact_context(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "docker").write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$BUILD_RECEIPT"\nexit 19\n')
    (bindir / "docker").chmod(0o755)
    receipt = tmp_path / "build.args"
    env = dict(os.environ, PATH=str(bindir) + os.pathsep + os.environ["PATH"], BUILD_RECEIPT=str(receipt))
    result = subprocess.run(["bash", str(EXTENSION / "setup.sh")], cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=5)
    assert result.returncode == 19
    assert receipt.read_text().splitlines() == ["build", "--tag", "ods-pocketbase:0.40.4-r1", "--file", str(EXTENSION / "Dockerfile"), str(EXTENSION)]


@pytest.mark.skipif(os.getenv("ODS_TEST_POCKETBASE") != "1", reason="Opt-in native PocketBase lifecycle and build")
def test_live_private_records_files_settings_rotation_and_cold_restore(tmp_path, compose_env):
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        pytest.skip("Host-bind qualification prepares isolated UID 1000 storage")
    project = "ods-q40-pocketbase-" + uuid.uuid4().hex[:12]
    plan = json.loads(render(tmp_path, compose_env).stdout)
    service = plan["services"]["pocketbase"]
    service.update(container_name=project, restart="no")
    service["ports"][0]["published"] = "0"
    service["labels"] = {"io.ods.quality40.validation": "true"}
    plan["networks"] = {"ods-network": {"name": project + "-network"}}
    data = tmp_path / "data/pocketbase"
    data.mkdir(parents=True)
    os.chown(data, 1000, 1000)
    config = tmp_path / "isolated.json"
    config.write_text(json.dumps(plan))
    config.chmod(0o600)
    command = ["docker", "compose", "-p", project, "-f", str(config)]
    password = compose_env["POCKETBASE_PASSWORD"]
    rotated, seed = [secrets.token_hex(24) for _ in range(2)]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def run(*args):
        result = subprocess.run(args, env=compose_env, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=240)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout + result.stderr if args[:2] == ("docker", "logs") else result.stdout

    def url():
        binding = json.loads(run("docker", "inspect", project))[0]["NetworkSettings"]["Ports"]["8090/tcp"][0]
        assert binding["HostIp"] == "127.0.0.1"
        return "http://127.0.0.1:" + binding["HostPort"]

    def request(base, method, path, payload=None, token=None, expected=200, headers=None):
        headers = dict(headers or {})
        if token:
            headers["Authorization"] = token
        if payload is not None and not isinstance(payload, bytes):
            payload = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(base + path, data=payload, headers=headers, method=method)
        try:
            response = opener.open(req, timeout=10)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            body = response.read()
            assert response.status == expected, f"{method} {path}: {response.status}: {body[:350]!r}"
            return body, response.headers

    def api(base, method, path, **kwargs):
        return json.loads(request(base, method, path, **kwargs)[0])

    def auth(base, email, secret, collection="_superusers", expected=200):
        return api(base, "POST", f"/api/collections/{collection}/auth-with-password",
                   payload={"identity": email, "password": secret}, expected=expected)

    def ready():
        initial = json.loads(run("docker", "inspect", project))[0]["State"]
        assert initial["Running"], run("docker", "logs", project)
        base = url()
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            state = json.loads(run("docker", "inspect", project))[0]["State"]
            assert state["Running"], run("docker", "logs", project)
            if state.get("Health", {}).get("Status") == "healthy":
                assert api(base, "GET", "/api/health")["code"] == 200
                return base
            time.sleep(0.25)
        pytest.fail("Native PocketBase did not become healthy")

    def rewrite():
        config.write_text(json.dumps(plan))

    def chown_tree():
        for path in [data, *data.rglob("*")]:
            os.chown(path, 1000, 1000)

    try:
        # Use the same setup hook the authenticated ODS install invokes.
        run("bash", str(EXTENSION / "setup.sh"))
        run(*command, "up", "-d")
        base = ready()
        assert b"<!doctype html" in request(base, "GET", "/_/")[0].lower()
        api(base, "GET", "/api/collections", expected=401)
        auth(base, "ods@localhost.invalid", "incorrect", expected=400)
        admin = auth(base, "ods@localhost.invalid", password)
        token, admin_id = admin["token"], admin["record"]["id"]
        _, cors = request(base, "OPTIONS", "/api/health", headers={"Origin": "http://localhost:8090", "Access-Control-Request-Method": "GET"}, expected=204)
        assert cors.get("Access-Control-Allow-Origin") == "http://localhost:8090"
        _, foreign = request(base, "OPTIONS", "/api/health", headers={"Origin": "https://foreign.invalid", "Access-Control-Request-Method": "GET"}, expected=204)
        assert foreign.get("Access-Control-Allow-Origin") is None
        members = api(base, "POST", "/api/collections", token=token,
                      payload={"name": "members", "type": "auth", "passwordAuth": {"enabled": True, "identityFields": ["email"]}})
        member_password = secrets.token_hex(20)
        owner = api(base, "POST", "/api/collections/members/records", token=token,
                    payload={"email": "reader@localhost.invalid", "password": member_password, "passwordConfirm": member_password})
        other = api(base, "POST", "/api/collections/members/records", token=token,
                    payload={"email": "other@localhost.invalid", "password": member_password, "passwordConfirm": member_password})
        owner_token = auth(base, owner["email"], member_password, "members")["token"]
        other_token = auth(base, other["email"], member_password, "members")["token"]
        rule = '@request.auth.id != "" && owner = @request.auth.id'
        notes = api(base, "POST", "/api/collections", token=token, payload={
            "name": "notes", "type": "base", "listRule": rule, "viewRule": rule,
            "createRule": rule, "updateRule": rule + ' && @request.body.owner:changed = false', "deleteRule": rule,
            "fields": [{"name": "owner", "type": "relation", "collectionId": members["id"], "maxSelect": 1, "required": True},
                       {"name": "text", "type": "text", "required": True},
                       {"name": "attachment", "type": "file", "maxSelect": 1, "maxSize": 1048576, "protected": True}],
        })
        note = api(base, "POST", "/api/collections/notes/records", token=owner_token,
                   payload={"owner": owner["id"], "text": "Ghi chú riêng tư 你好"})
        path = "/api/collections/notes/records/" + note["id"]
        api(base, "GET", path, token=other_token, expected=404)
        assert api(base, "GET", "/api/collections/notes/records", token=other_token)["items"] == []
        api(base, "POST", "/api/collections/notes/records", token=other_token,
            payload={"owner": owner["id"], "text": "forged owner"}, expected=400)
        api(base, "PATCH", path, token=owner_token, payload={"owner": other["id"]}, expected=404)
        file_bytes = b"private binary\x00\xff" + " có dấu".encode()
        boundary = "ods" + uuid.uuid4().hex
        multipart = (f'--{boundary}\r\nContent-Disposition: form-data; name="attachment"; filename="evidence.bin"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
                     + file_bytes + f"\r\n--{boundary}--\r\n".encode())
        note = api(base, "PATCH", path, token=owner_token, payload=multipart,
                   headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
        download = f'/api/files/{notes["id"]}/{note["id"]}/{note["attachment"]}'
        request(base, "GET", download, expected=404)
        file_token = api(base, "POST", "/api/files/token", token=owner_token)["token"]
        assert request(base, "GET", download + "?token=" + file_token)[0] == file_bytes
        other_file_token = api(base, "POST", "/api/files/token", token=other_token)["token"]
        request(base, "GET", download + "?token=" + other_file_token, expected=404)
        changed = api(base, "PATCH", path, token=owner_token, payload={"text": "Saved revision 42"})
        assert changed["text"] == "Saved revision 42"
        run(*command, "stop")
        with sqlite3.connect(data / "data.db") as db:
            settings_blob = db.execute("SELECT value FROM _params WHERE id='settings'").fetchone()[0]
            assert "ODS PocketBase" not in str(settings_blob) and not str(settings_blob).startswith("{")
        service["environment"]["POCKETBASE_PASSWORD"] = seed
        rewrite()
        run(*command, "up", "-d", "--force-recreate")
        base = ready()
        auth(base, "ods@localhost.invalid", seed, expected=400)
        token = auth(base, "ods@localhost.invalid", password)["token"]
        api(base, "PATCH", "/api/collections/_superusers/records/" + admin_id, token=token,
            payload={"oldPassword": password, "password": rotated, "passwordConfirm": rotated})
        api(base, "GET", "/api/collections", token=token, expected=401)
        auth(base, "ods@localhost.invalid", password, expected=400)
        fresh_token = auth(base, "ods@localhost.invalid", rotated)["token"]
        assert api(base, "GET", "/api/settings", token=fresh_token)["meta"]["appName"] == "ODS PocketBase"
        run(*command, "stop")
        backup = tmp_path / "cold-backup"
        shutil.copytree(data, backup)
        data.rename(tmp_path / "before-restore")
        shutil.copytree(backup, data)
        chown_tree()
        run(*command, "up", "-d", "--force-recreate")
        base = ready()
        auth(base, "ods@localhost.invalid", rotated)
        owner_token = auth(base, owner["email"], member_password, "members")["token"]
        assert api(base, "GET", path, token=owner_token)["text"] == "Saved revision 42"
        file_token = api(base, "POST", "/api/files/token", token=owner_token)["token"]
        assert request(base, "GET", download + "?token=" + file_token)[0] == file_bytes
        # A partial restore must not silently create a fresh database.
        run(*command, "stop")
        (data / "data.db").rename(data / "data.db.saved")
        run(*command, "up", "-d", "--force-recreate")
        assert run("docker", "wait", project).strip() == "1"
        assert "data.db is missing" in run("docker", "logs", project)
        assert not (data / "data.db").exists()
        (data / "data.db.saved").rename(data / "data.db")
        # Wrong encryption keys fail visibly without replacing the settings.
        service["environment"]["POCKETBASE_ENCRYPTION_KEY"] = secrets.token_hex(16)
        rewrite()
        run(*command, "up", "-d", "--force-recreate")
        assert run("docker", "wait", project).strip() == "1"
        assert "authentication failed" in run("docker", "logs", project)
        with sqlite3.connect(data / "data.db") as db:
            assert db.execute("SELECT value FROM _params WHERE id='settings'").fetchone()[0] == settings_blob
            db.execute('DELETE FROM "_superusers"')
        service["environment"]["POCKETBASE_ENCRYPTION_KEY"] = compose_env["POCKETBASE_ENCRYPTION_KEY"]
        rewrite()
        run(*command, "up", "-d", "--force-recreate")
        assert run("docker", "wait", project).strip() == "1"
        assert "has no superuser" in run("docker", "logs", project)
        # Native offline recovery explicitly bypasses the normal startup hook.
        run(*command, "run", "--rm", "-T", "--no-deps", "--entrypoint", "/usr/local/bin/pocketbase",
            "pocketbase", "superuser", "upsert", "ods@localhost.invalid", rotated,
            "--dir=/pb/pb_data", "--hooksDir=/tmp/recovery-hooks",
            "--encryptionEnv=POCKETBASE_ENCRYPTION_KEY")
        run(*command, "up", "-d", "--force-recreate")
        base = ready()
        auth(base, "ods@localhost.invalid", rotated)
        owner_token = auth(base, owner["email"], member_password, "members")["token"]
        assert api(base, "GET", path, token=owner_token)["text"] == "Saved revision 42"
        file_token = api(base, "POST", "/api/files/token", token=owner_token)["token"]
        assert request(base, "GET", download + "?token=" + file_token)[0] == file_bytes
        request(base, "DELETE", path, token=owner_token, expected=204)
        request(base, "GET", download + "?token=" + file_token, expected=404)
        logs = run("docker", "logs", project)
        assert all(secret not in logs for secret in [password, seed, rotated, compose_env["POCKETBASE_ENCRYPTION_KEY"]])
    finally:
        run(*command, "down", "--volumes", "--remove-orphans")
