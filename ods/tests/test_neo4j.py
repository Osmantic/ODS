"""Neo4j installation and opt-in Bolt/HTTP transaction and recovery contracts."""

import base64
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
EXTENSION = ROOT / "extensions/library/services/neo4j"


@pytest.fixture
def installation(tmp_path):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI required")
    installed = tmp_path / "data/user-extensions/neo4j"
    shutil.copytree(EXTENSION, installed)
    # The real Dashboard-to-host-agent HTTP sync is exercised separately by
    # dashboard-api/tests/test_neo4j_install.py before startup is accepted.
    shutil.copytree(installed / "config", tmp_path / "config")
    (tmp_path / "empty.env").write_text("")
    env = {key: value for key, value in os.environ.items() if not key.startswith("NEO4J_")}
    env["NEO4J_PASSWORD"] = secrets.token_hex(24) + "$:[%]"
    return tmp_path, installed, env


def render(installation, check=True):
    directory, installed, env = installation
    return subprocess.run([
        "docker", "compose", "--env-file", str(directory / "empty.env"),
        "--project-directory", str(directory), "-f", str(installed / "compose.yaml"),
        "config", "--format", "json",
    ], env=env, capture_output=True, text=True, check=check, timeout=30)


@pytest.mark.parametrize("http,bolt", [("7474", "7687"), ("17474", "17687")])
def test_installation_advertises_the_published_client_ports(installation, http, bolt):
    directory, _, env = installation
    env.update(NEO4J_PORT=http, NEO4J_BOLT_PORT=bolt)
    service = json.loads(render(installation).stdout)["services"]["neo4j"]
    ports = {p["target"]: p for p in service["ports"]}
    assert ports[7474]["published"] == http
    assert ports[7687]["published"] == bolt
    assert all(p["host_ip"] == "127.0.0.1" for p in ports.values())
    assert service["environment"]["NEO4J_server_http_advertised__address"] == "localhost:" + http
    assert service["environment"]["NEO4J_server_bolt_advertised__address"] == "localhost:" + bolt
    mounts = {m["target"]: m for m in service["volumes"]}
    assert mounts["/data"]["source"] == str(directory / "data/neo4j/data")
    assert mounts["/opt/ods-neo4j-entrypoint.sh"]["read_only"]
    assert Path(mounts["/opt/ods-neo4j-entrypoint.sh"]["source"]).is_file()
    catalog = json.loads((ROOT / "config/extensions-catalog.json").read_text())
    entry = next(item for item in catalog["extensions"] if item["id"] == "neo4j")
    assert entry["features"][0]["launch"]["service"] == "neo4j"


@pytest.mark.parametrize("empty", [True, False])
def test_missing_initial_password_blocks_start(installation, empty):
    env = installation[2]
    if empty:
        env["NEO4J_PASSWORD"] = ""
    else:
        del env["NEO4J_PASSWORD"]
    result = render(installation, check=False)
    assert result.returncode != 0
    assert "NEO4J_PASSWORD" in result.stderr


@pytest.mark.skipif(os.getenv("ODS_TEST_NEO4J") != "1", reason="Opt-in actual Neo4j recovery drill")
def test_live_bolt_http_transactions_and_offline_copy_recovery(installation):
    directory, installed, env = installation
    project = "ods-q40-neo4j-" + uuid.uuid4().hex[:12]
    plan = json.loads(render(installation).stdout)
    service = plan["services"]["neo4j"]
    service["container_name"] = project
    service["restart"] = "no"
    service["ports"] = [{"target": 7474, "published": "0", "host_ip": "127.0.0.1", "protocol": "tcp"}]
    service["labels"] = {"io.ods.quality40.validation": "true"}
    volumes = {"validation-data": {"name": project + "-data"},
               "validation-logs": {"name": project + "-logs"},
               "recovery-data": {"name": project + "-recovery"}}
    for mount in service["volumes"]:
        if mount["target"] in {"/data", "/logs"}:
            mount.update(type="volume", source="validation-" + mount["target"].lstrip("/"))
            mount.pop("bind", None)
    plan["volumes"] = volumes
    plan["networks"] = {"ods-network": {"name": project + "-network"}}
    config = directory / "isolated.json"
    config.write_text(json.dumps(plan))
    config.chmod(0o600)
    command = ["docker", "compose", "-p", project, "-f", str(config)]

    def run(*args, check=True, environment=env):
        result = subprocess.run(args, env=environment, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=240)
        if check:
            assert result.returncode == 0, result.stdout + result.stderr
        return result

    def base_url():
        container = json.loads(run("docker", "inspect", project).stdout)[0]
        binding = container["NetworkSettings"]["Ports"]["7474/tcp"][0]
        return "http://127.0.0.1:" + binding["HostPort"]

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(base, path, body=None, password=None, method=None, expected=(202,)):
        headers = {"Content-Type": "application/json"}
        if password is not None:
            headers["Authorization"] = "Basic " + base64.b64encode(("neo4j:" + password).encode()).decode()
        req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers=headers, method=method)
        try:
            response = opener.open(req, timeout=30)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            assert response.status in expected, f"{path}: HTTP {response.status}"
            raw = response.read()
            return json.loads(raw) if raw else {}

    endpoint = "/db/neo4j/query/v2"
    password = env["NEO4J_PASSWORD"]
    read_graph = {"statement": "MATCH (a:ODSFixture)-[:LINKS_TO]->(b) RETURN a.name, b.name ORDER BY a.name"}

    def assert_graph(base):
        value = request(base, endpoint, read_graph, password)
        assert not value.get("errors"), value
        assert value["data"]["values"] == [["alpha", "beta"]]
        bolt = run("docker", "exec", "-e", "NEO4J_USERNAME=neo4j", "-e", "NEO4J_PASSWORD",
                   project, "cypher-shell", "-a", "bolt://127.0.0.1:7687", "--format", "plain",
                   "MATCH (n:ODSFixture) RETURN count(n) AS nodes;")
        assert bolt.stdout.strip().splitlines() == ["nodes", "2"]

    try:
        for invalid in ("short", "unsupported/secret", "line\nbreak-password"):
            rejected_env = dict(env, ODS_NEO4J_PASSWORD=invalid)
            bad = run("docker", "run", "--rm", "--network", "none", "--entrypoint", "bash",
                      "-e", "ODS_NEO4J_PASSWORD", "-v", str(installed / "config/neo4j/entrypoint.sh") + ":/guard:ro",
                      service["image"], "/guard", "neo4j", check=False, environment=rejected_env)
            assert bad.returncode != 0
            assert invalid not in bad.stdout + bad.stderr
            assert "NEO4J_PASSWORD must have" in bad.stderr
        run(*command, "up", "-d", "--wait", "--wait-timeout", "180")
        base = base_url()
        request(base, endpoint, read_graph, expected=(401,))
        request(base, endpoint, read_graph, "incorrect", expected=(401,))
        created = request(base, endpoint, {
            "statement": "CREATE (a:ODSFixture {name:$a})-[:LINKS_TO]->(b:ODSFixture {name:$b}) RETURN a.name,b.name",
            "parameters": {"a": "alpha", "b": "beta"},
        }, password)
        assert not created.get("errors"), created
        assert_graph(base)
        tx = request(base, endpoint + "/tx", {"statement": "CREATE (:ODSFixture {name:'rolled-back'})"}, password)
        assert not tx.get("errors"), tx
        tx_id = urllib.parse.quote(tx["transaction"]["id"], safe="")
        request(base, endpoint + "/tx/" + tx_id, password=password, method="DELETE", expected=(200, 202))
        assert_graph(base)
        run(*command, "stop", "--timeout", "60")
        run("docker", "volume", "create", volumes["recovery-data"]["name"])
        run("docker", "run", "--rm", "--network", "none", "--user", "0", "--entrypoint", "bash",
            "-v", volumes["validation-data"]["name"] + ":/from:ro",
            "-v", volumes["recovery-data"]["name"] + ":/to", service["image"],
            "-c", "cp -a /from/. /to/")
        data_mount = next(m for m in service["volumes"] if m["target"] == "/data")
        data_mount["source"] = "recovery-data"
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "180")
        assert_graph(base_url())
        print("HTTP and Bolt auth/query passed; explicit transaction rolled back; quiesced data copy restored graph and credentials")
    finally:
        logs = subprocess.run(["docker", "logs", "--tail", "25", project], capture_output=True, text=True, timeout=20)
        print("Service diagnostics:", logs.stdout, logs.stderr)
        run(*command, "down", "--volumes", "--timeout", "60")
