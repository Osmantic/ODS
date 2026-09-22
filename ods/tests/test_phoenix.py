"""Rendered Phoenix catalog contract and opt-in authenticated image lifecycle."""

import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import uuid

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extensions/library/services/phoenix"
SECRETS = ("PHOENIX_SECRET", "PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD")


@pytest.fixture
def compose_env(tmp_path):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI required")
    (tmp_path / "empty.env").write_text("")
    env = {key: value for key, value in os.environ.items() if not key.startswith("PHOENIX_")}
    env.update({key: "test1-" + secrets.token_hex(24) for key in SECRETS})
    return env


def render(tmp_path, env, check=True):
    return subprocess.run([
        "docker", "compose", "--env-file", str(tmp_path / "empty.env"),
        "--project-directory", str(tmp_path), "-f", str(EXTENSION / "compose.yaml"),
        "config", "--format", "json", "phoenix",
    ], env=env, capture_output=True, text=True, check=check, timeout=30)


@pytest.mark.parametrize("public_port", ["8606", "18606"])
def test_rendered_catalog_launches_authenticated_local_collector(tmp_path, compose_env, public_port):
    compose_env["PHOENIX_PORT"] = public_port
    service = json.loads(render(tmp_path, compose_env).stdout)["services"]["phoenix"]
    manifest = yaml.safe_load((EXTENSION / "manifest.yaml").read_text())
    assert manifest["service"]["default_host"] == "phoenix"
    assert manifest["service"]["health"] == "/readyz"
    assert manifest["features"][0]["launch"]["service"] == "phoenix"
    assert service["ports"][0]["published"] == public_port
    assert service["ports"][0]["host_ip"] == "127.0.0.1"
    assert len(service["ports"]) == 1
    assert service["environment"]["PHOENIX_PORT"] == "6006"
    assert service["environment"]["PHOENIX_ENABLE_AUTH"] == "true"
    for key in ("PHOENIX_TELEMETRY_ENABLED", "PHOENIX_ALLOW_EXTERNAL_RESOURCES", "PHOENIX_ENABLE_MCP_SERVER"):
        assert service["environment"][key] == "false"
    assert service["environment"]["PHOENIX_DISABLE_AGENT_ASSISTANT"] == "true"
    assert service["volumes"][0]["source"] == str(tmp_path / "data/phoenix")
    assert service["volumes"][0]["target"] == service["environment"]["PHOENIX_WORKING_DIR"]
    catalog = json.loads((ROOT / "config/extensions-catalog.json").read_text())
    entry = next(item for item in catalog["extensions"] if item["id"] == "phoenix")
    assert entry["external_port_default"] == 8606
    assert {item["key"] for item in entry["env_vars"] if item.get("required")} == set(SECRETS)


@pytest.mark.parametrize("key", SECRETS)
@pytest.mark.parametrize("empty", [True, False])
def test_missing_credentials_block_compose_start(tmp_path, compose_env, key, empty):
    if empty:
        compose_env[key] = ""
    else:
        del compose_env[key]
    result = render(tmp_path, compose_env, check=False)
    assert result.returncode != 0
    assert key in result.stderr


@pytest.mark.skipif(os.getenv("ODS_TEST_PHOENIX") != "1", reason="Opt-in real Phoenix image lifecycle")
def test_live_auth_trace_persistence_and_key_revocation(tmp_path, compose_env):
    project = "ods-q40-phoenix-" + uuid.uuid4().hex[:12]
    plan = json.loads(render(tmp_path, compose_env).stdout)
    service = plan["services"]["phoenix"]
    service["container_name"] = project
    service["restart"] = "no"
    service["ports"] = []
    service["labels"] = {"io.ods.quality40.validation": "true"}
    service["volumes"] = [{"type": "volume", "source": "validation-data", "target": "/data/phoenix"}]
    plan["networks"] = {"ods-network": {"name": project + "-network"}}
    plan["volumes"] = {"validation-data": {"name": project + "-data"}}
    config = tmp_path / "isolated.json"
    config.write_text(json.dumps(plan))
    config.chmod(0o600)
    command = ["docker", "compose", "-p", project, "-f", str(config)]

    def run(*args):
        result = subprocess.run(args, env=compose_env, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=240)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    probe = ROOT / "tests/fixtures/phoenix-lifecycle.py"
    try:
        run(*command, "up", "-d", "--wait", "--wait-timeout", "150")
        run("docker", "network", "disconnect", project + "-network", project)
        print(run("docker", "exec", project, "/usr/bin/python3.13", "-c", probe.read_text(), "write"))
        # Graceful stop flushes the collector queue before the old container is removed.
        run(*command, "stop", "--timeout", "30")
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "150")
        run("docker", "network", "disconnect", project + "-network", project)
        print(run("docker", "exec", project, "/usr/bin/python3.13", "-c", probe.read_text(), "read"))
    finally:
        logs = subprocess.run(["docker", "logs", "--tail", "50", project], capture_output=True, text=True, timeout=20)
        print("Worker diagnostics:", logs.stdout, logs.stderr)
        run(*command, "down", "--volumes", "--timeout", "30")
