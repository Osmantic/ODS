"""CouchDB recipe, fail-closed bootstrap and opt-in real document lifecycle."""

import base64
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
EXTENSION = ROOT / "extensions/library/services/couchdb"


@pytest.fixture
def compose_env(tmp_path):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI required")
    (tmp_path / "empty.env").write_text("")
    env = {key: value for key, value in os.environ.items() if not key.startswith("COUCHDB_")}
    env.update(COUCHDB_PASSWORD=secrets.token_hex(24), COUCHDB_SECRET=secrets.token_hex(24),
               BIND_ADDRESS="127.0.0.1")
    shutil.copytree(EXTENSION / "config", tmp_path / "config")
    return env


def render(tmp_path, env, check=True):
    return subprocess.run([
        "docker", "compose", "--env-file", str(tmp_path / "empty.env"),
        "--project-directory", str(tmp_path), "-f", str(EXTENSION / "compose.yaml"),
        "config", "--format", "json", "couchdb",
    ], env=env, check=check, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("bind_address", [None, "127.0.0.1", "0.0.0.0", "192.0.2.42"])
def test_bind_address_opt_in_preserves_native_configuration(tmp_path, compose_env, bind_address):
    compose_env.pop("BIND_ADDRESS", None)
    baseline = json.loads(render(tmp_path, compose_env).stdout)["services"]["couchdb"]
    if bind_address is not None:
        compose_env["BIND_ADDRESS"] = bind_address
    service = json.loads(render(tmp_path, compose_env).stdout)["services"]["couchdb"]
    assert all(port["host_ip"] == (bind_address or "127.0.0.1") for port in service["ports"])
    assert [(p["published"], p["target"]) for p in service["ports"]] == [
        (p["published"], p["target"]) for p in baseline["ports"]]
    assert service["environment"] == baseline["environment"]
    assert service["volumes"] == baseline["volumes"]


@pytest.mark.parametrize("port", ["5984", "15984"])
def test_private_single_node_recipe(tmp_path, compose_env, port):
    compose_env["COUCHDB_PORT"] = port
    service = json.loads(render(tmp_path, compose_env).stdout)["services"]["couchdb"]
    assert len(service["ports"]) == 1
    assert service["ports"][0]["host_ip"] == "127.0.0.1"
    assert service["ports"][0]["published"] == port
    assert service["ports"][0]["target"] == 5984
    assert service["user"] == "5984:5984" and service["read_only"]
    mounts = {item["target"]: item for item in service["volumes"]}
    assert mounts["/opt/couchdb/data"]["source"] == str(tmp_path / "data/couchdb/databases")
    assert mounts["/opt/couchdb/etc/local.ini"]["read_only"] is True
    assert service["environment"]["ERL_EPMD_ADDRESS"] == "127.0.0.1"
    assert service["healthcheck"]["test"][-1] == "http://127.0.0.1:5984/_up"
    catalog = json.loads((ROOT / "config/extensions-catalog.json").read_text())
    entry = next(item for item in catalog["extensions"] if item["id"] == "couchdb")
    assert entry["features"][0]["launch"] == {"type": "service", "service": "couchdb", "path": "/_utils/"}


@pytest.mark.parametrize("key", ["COUCHDB_PASSWORD", "COUCHDB_SECRET"])
@pytest.mark.parametrize("value", [None, ""])
def test_missing_auth_blocks_activation(tmp_path, compose_env, key, value):
    if value is None:
        del compose_env[key]
    else:
        compose_env[key] = value
    result = render(tmp_path, compose_env, check=False)
    assert result.returncode != 0 and key in result.stderr


@pytest.mark.parametrize("key", ["COUCHDB_PASSWORD", "COUCHDB_SECRET"])
@pytest.mark.parametrize("value", ["short", "x" * 129, "line\n[admins]\nevil=x", "no spaces permitted"])
def test_invalid_auth_stops_before_native_bootstrap(tmp_path, compose_env, key, value):
    service = json.loads(render(tmp_path, compose_env).stdout)["services"]["couchdb"]
    compose_env[key] = value
    result = subprocess.run(["bash", "-euc", service["command"][0].replace("$$", "$")],
                            env=compose_env, capture_output=True, text=True, timeout=5)
    assert result.returncode == 1 and key in result.stderr
    assert value not in result.stderr and "/docker-entrypoint.sh" not in result.stderr


@pytest.mark.skipif(os.getenv("ODS_TEST_COUCHDB") != "1", reason="Opt-in real CouchDB documents and replication")
def test_live_auth_revisions_permissions_replication_rotation_and_restore(tmp_path, compose_env):
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        pytest.skip("Host-bind qualification prepares isolated UID 5984 storage")
    project = "ods-q40-couchdb-" + uuid.uuid4().hex[:12]
    plan = json.loads(render(tmp_path, compose_env).stdout)
    service = plan["services"]["couchdb"]
    service.update(container_name=project, restart="no")
    service["ports"][0]["published"] = "0"
    service["labels"] = {"io.ods.quality40.validation": "true"}
    plan["networks"] = {"ods-network": {"name": project + "-network"}}
    data = tmp_path / "data/couchdb"
    for path in (data, data / "databases", data / "config"):
        path.mkdir(parents=True, exist_ok=True)
        os.chown(path, 5984, 5984)
    config = tmp_path / "isolated.json"
    config.write_text(json.dumps(plan))
    config.chmod(0o600)
    command = ["docker", "compose", "-p", project, "-f", str(config)]
    password, secret = compose_env["COUCHDB_PASSWORD"], compose_env["COUCHDB_SECRET"]
    writer_password, outsider_password = secrets.token_hex(24), secrets.token_hex(24)
    rotated_password, rotated_secret = secrets.token_hex(24), secrets.token_hex(24)

    def run(*args):
        result = subprocess.run(args, env=compose_env, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=180)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    def base_url():
        binding = json.loads(run("docker", "inspect", project))[0]["NetworkSettings"]["Ports"]["5984/tcp"][0]
        assert binding["HostIp"] == "127.0.0.1"
        return "http://127.0.0.1:" + binding["HostPort"]

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(base, method, path, credential=None, payload=None, expected=200, username="ods", cookie=None, form=False):
        headers = {}
        if credential:
            headers["Authorization"] = "Basic " + base64.b64encode((username + ":" + credential).encode()).decode()
        if cookie:
            headers["Cookie"] = cookie
        if form:
            payload = urllib.parse.urlencode(payload).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif payload is not None and not isinstance(payload, bytes):
            payload = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        elif isinstance(payload, bytes):
            headers["Content-Type"] = "application/octet-stream"
        req = urllib.request.Request(base + path, data=payload, method=method, headers=headers)
        try:
            response = opener.open(req, timeout=20)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            body = response.read()
            assert response.status == expected, f"{method} {path}: {response.status}: {body!r}"
            return body, response.headers

    def document(base, credential):
        return json.loads(request(base, "GET", "/research/fixture", credential)[0])

    attachment = bytes(range(256)) + b"\x00\xff local fixture"
    try:
        run(*command, "up", "-d", "--wait", "--wait-timeout", "90")
        base = base_url()
        assert json.loads(request(base, "GET", "/_up")[0])["status"] == "ok"
        for credential in (None, "incorrect"):
            request(base, "GET", "/_all_dbs", credential, expected=401)
            request(base, "PUT", "/denied", credential, expected=401)
        dbs = json.loads(request(base, "GET", "/_all_dbs", password)[0])
        assert "_users" in dbs and "_replicator" in dbs
        assert b"Fauxton" in request(base, "GET", "/_utils/", password)[0]
        request(base, "PUT", "/research", password, expected=201)
        request(base, "PUT", "/research/_security", password,
                {"admins": {"names": ["ods"], "roles": []}, "members": {"names": [], "roles": ["researchers"]}})
        for name, credential, roles in (("writer", writer_password, ["researchers"]), ("outsider", outsider_password, [])):
            request(base, "PUT", "/_users/org.couchdb.user:" + name, password,
                    {"name": name, "password": credential, "type": "user", "roles": roles}, expected=201)
        request(base, "GET", "/research/_all_docs", outsider_password, username="outsider", expected=403)
        created = json.loads(request(base, "PUT", "/research/fixture", writer_password,
                                     {"dataset": "local", "note": "Ghi chú riêng tư 你好", "score": 0.875},
                                     username="writer", expected=201)[0])
        initial_rev = created["rev"]
        request(base, "PUT", "/research/fixture/report.bin?rev=" + initial_rev,
                writer_password, attachment, username="writer", expected=201)
        recorded = document(base, password)
        request(base, "PUT", "/research/fixture", writer_password,
                {"_rev": initial_rev, "note": "stale write"}, username="writer", expected=409)
        assert document(base, password) == recorded
        request(base, "GET", "/research/fixture/report.bin", outsider_password, username="outsider", expected=403)
        assert request(base, "GET", "/research/fixture/report.bin", writer_password, username="writer")[0] == attachment
        request(base, "POST", "/research/_index", password, {"index": {"fields": ["dataset"]}, "name": "by-dataset"})
        found = json.loads(request(base, "POST", "/research/_find", writer_password,
                                   {"selector": {"dataset": "local"}}, username="writer")[0])["docs"]
        assert len(found) == 1 and found[0]["_id"] == "fixture"
        sequence = json.loads(request(base, "GET", "/research/_changes", password)[0])["last_seq"]
        request(base, "PUT", "/research/second", password, {"dataset": "second"}, expected=201)
        changes = json.loads(request(base, "GET", "/research/_changes?since=" + urllib.parse.quote(str(sequence)), password)[0])
        assert [item["id"] for item in changes["results"]] == ["second"]
        replicated = json.loads(request(base, "POST", "/_replicate", password,
                                        {"source": "research", "target": "research_copy", "create_target": True})[0])
        assert replicated["ok"] is True
        assert request(base, "GET", "/research_copy/fixture/report.bin", password)[0] == attachment
        request(base, "PUT", "/_node/_local/_config/log/level", password, "warning")
        body, headers = request(base, "POST", "/_session", payload={"name": "ods", "password": password}, form=True)
        assert json.loads(body)["ok"] is True
        cookies = SimpleCookie()
        for value in headers.get_all("Set-Cookie", []):
            cookies.load(value)
        cookie = "; ".join(f"{key}={value.value}" for key, value in cookies.items())
        assert cookie
        request(base, "GET", "/research/fixture", cookie=cookie)
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "90")
        base = base_url()
        assert document(base, password) == recorded
        assert request(base, "GET", "/research/fixture/report.bin", writer_password, username="writer")[0] == attachment
        assert json.loads(request(base, "GET", "/_node/_local/_config/log/level", password)[0]) == "warning"
        request(base, "GET", "/research/fixture", cookie=cookie)
        request(base, "PUT", "/_node/_local/_config/chttpd_auth/secret", password, rotated_secret)
        request(base, "PUT", "/_node/_local/_config/admins/ods", password, rotated_password)
        # Native admin hashing/cache invalidation is asynchronous. Recreate
        # before verifying rotated access, rather than assuming PUT is a receipt.
        # Native persisted config takes precedence over the original env seeds.
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "90")
        base = base_url()
        request(base, "GET", "/research/fixture", password, expected=401)
        request(base, "GET", "/research/fixture", cookie=cookie, expected=401)
        assert document(base, rotated_password) == recorded
        run(*command, "stop", "--timeout", "30")
        backup = tmp_path / "restored-databases"
        shutil.copytree(data, backup)
        for path in [backup, *backup.rglob("*")]:
            os.chown(path, 5984, 5984)
        for item in service["volumes"]:
            if item["target"] == "/opt/couchdb/data":
                item["source"] = str(backup / "databases")
            elif item["target"] == "/opt/couchdb/etc/local.d":
                item["source"] = str(backup / "config")
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "90")
        base = base_url()
        assert document(base, rotated_password) == recorded
        assert json.loads(request(base, "GET", "/_node/_local/_config/log/level", rotated_password)[0]) == "warning"
        request(base, "GET", "/research/fixture", password, expected=401)
        sockets = run("docker", "exec", project, "cat", "/proc/net/tcp", "/proc/net/tcp6")
        for row in sockets.splitlines():
            columns = row.split()
            if len(columns) > 3 and columns[3] == "0A":
                address, port = columns[1].rsplit(":", 1)
                if int(port, 16) in (4369, 9100):
                    assert address in ("0100007F", "00000000000000000000000001000000"), row
        assert request(base, "GET", "/research_copy/fixture/report.bin", rotated_password)[0] == attachment
        assert request(base, "GET", "/research/fixture/report.bin", writer_password, username="writer")[0] == attachment
        request(base, "GET", "/research/fixture", outsider_password, username="outsider", expected=403)
    finally:
        try:
            logs = subprocess.run(["docker", "logs", project], capture_output=True, text=True, check=True, timeout=20)
            diagnostics = logs.stdout + logs.stderr
            assert all(value not in diagnostics for value in
                       (password, secret, writer_password, outsider_password, rotated_password, rotated_secret))
        finally:
            run(*command, "down", "--volumes", "--timeout", "30")
