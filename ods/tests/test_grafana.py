"""Installed Grafana contract and opt-in real HTTP/persistence/metrics drill."""

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
EXTENSION = ROOT / "extensions/library/services/grafana"
PROBE_IMAGE = "python:3.13-alpine@sha256:7415fbc3c9e4979cc717d92377ab2bc7b2b4a2af1ac03cc52b5f3f88efedaf3a"


@pytest.fixture
def installation(tmp_path):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI required")
    installed = tmp_path / "data/user-extensions/grafana"
    shutil.copytree(EXTENSION, installed)
    (tmp_path / "empty.env").write_text("")
    env = {key: value for key, value in os.environ.items() if not key.startswith("GRAFANA_")}
    env.pop("BIND_ADDRESS", None)
    env.update(GRAFANA_ADMIN_PASSWORD=secrets.token_hex(24), GRAFANA_SECRET_KEY=secrets.token_hex(32))
    return tmp_path, installed, env


def render(installation):
    directory, installed, env = installation
    return subprocess.run(["docker", "compose", "--env-file", str(directory / "empty.env"),
        "--project-directory", str(directory), "-f", str(installed / "compose.yaml"),
        "config", "--format", "json"], env=env, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("port", ["3033", "13033"])
def test_installed_dashboard_keeps_url_ports_and_owned_data_consistent(installation, port):
    directory, installed, env = installation
    env["GRAFANA_PORT"] = port
    result = render(installation)
    assert result.returncode == 0, result.stderr
    service = json.loads(result.stdout)["services"]["grafana"]
    assert service["environment"]["GF_SERVER_ROOT_URL"] == "http://localhost:" + port
    assert service["ports"][0]["published"] == port
    assert service["ports"][0]["host_ip"] == "127.0.0.1"
    assert service["user"] == "472:472"
    assert service["read_only"] is True
    assert service["volumes"][0]["source"] == str(directory / "data/grafana")
    assert service["volumes"][0]["target"] == "/var/lib/grafana"
    manifest = yaml.safe_load((installed / "manifest.yaml").read_text())
    assert manifest["service"]["uid"] == 472
    assert manifest["features"][0]["launch"]["service"] == "grafana"


@pytest.mark.parametrize("key", ["GRAFANA_ADMIN_PASSWORD", "GRAFANA_SECRET_KEY"])
def test_missing_credentials_stop_compose(installation, key):
    del installation[2][key]
    result = render(installation)
    assert result.returncode != 0
    assert key in result.stderr


def test_explicit_public_url_and_resolver_contract(installation):
    directory, _, env = installation
    env["GRAFANA_ROOT_URL"] = "https://metrics.example.test/"
    plan = json.loads(render(installation).stdout)
    service = plan["services"]["grafana"]
    assert service["environment"]["GF_SERVER_ROOT_URL"] == env["GRAFANA_ROOT_URL"]
    assert service["environment"]["GF_SECURITY_SECRET_KEY"] == env["GRAFANA_SECRET_KEY"]
    assert service["environment"]["GF_SECRETS_MANAGER_ENCRYPTION_SECRET_KEY_V1_SECRET_KEY"] == env["GRAFANA_SECRET_KEY"]
    assert service["environment"]["GF_AUTH_ANONYMOUS_ENABLED"] == "false"
    assert service["environment"]["GF_ANALYTICS_REPORTING_ENABLED"] == "false"
    assert service["environment"]["GF_PLUGINS_PREINSTALL_DISABLED"] == "true"
    (directory / "docker-compose.base.yml").write_text("services:\n  fixture-core:\n    image: busybox\n")
    result = subprocess.run(["bash", str(ROOT / "scripts/resolve-compose-stack.sh"),
        "--script-dir", str(directory), "--gpu-backend", "cpu"],
        env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "data/user-extensions/grafana/compose.yaml" in result.stdout


@pytest.mark.skipif(os.getenv("ODS_TEST_GRAFANA") != "1", reason="Opt-in real Grafana HTTP and storage drill")
def test_live_saved_dashboard_and_secret_backed_queries_survive_recreation(installation):
    directory, _, env = installation
    project = "ods-q40-grafana-" + uuid.uuid4().hex[:12]
    plan = json.loads(render(installation).stdout)
    service = plan["services"]["grafana"]
    service.update(container_name=project, restart="no", network_mode="none")
    service.pop("ports")
    service.pop("networks")
    plan.pop("networks")
    plan["volumes"] = {"data": {"name": project + "-data"}}
    service["volumes"] = [{"type": "volume", "source": "data", "target": "/var/lib/grafana"}]
    env["ODS_METRICS_PASSWORD"] = secrets.token_hex(24)
    env["ODS_GRAFANA_LOGIN_PASSWORD"] = env["GRAFANA_ADMIN_PASSWORD"]
    env["ODS_GRAFANA_RESEED_PASSWORD"] = secrets.token_hex(24)
    metrics = (ROOT / "tests/fixtures/grafana-metrics.py").read_text()
    probe = (ROOT / "tests/fixtures/grafana-http.py").read_text()
    plan["services"]["metrics"] = {
        "image": PROBE_IMAGE, "container_name": project + "-metrics",
        "network_mode": "service:grafana",
        "environment": {"ODS_METRICS_PASSWORD": env["ODS_METRICS_PASSWORD"]},
        "command": ["python", "-u", "-c", metrics],
        "depends_on": {"grafana": {"condition": "service_healthy"}},
        "healthcheck": {"test": ["CMD", "python", "-c",
            "import urllib.request; urllib.request.urlopen('http://127.0.0.1:18080/seen', timeout=2).read()"],
            "interval": "1s", "timeout": "3s", "retries": 20},
    }
    config = directory / "isolated.json"
    config.write_text(json.dumps(plan))
    config.chmod(0o600)
    command = ["docker", "compose", "-p", project, "-f", str(config)]

    def run(*args, check=True, timeout=180):
        result = subprocess.run(args, env=env, capture_output=True, text=True,
                                stdin=subprocess.DEVNULL, timeout=timeout)
        if check:
            assert result.returncode == 0, result.stdout + result.stderr
        return result

    def exercise(seed):
        run("docker", "exec", "-e", "ODS_GRAFANA_LOGIN_PASSWORD", "-e",
            "ODS_GRAFANA_RESEED_PASSWORD", "-e", "ODS_GRAFANA_SEED=" + ("1" if seed else "0"),
            project + "-metrics", "python", "-c", probe)

    try:
        run("docker", "volume", "create", project + "-data")
        run("docker", "run", "--rm", "--network", "none", "--user", "0",
            "--entrypoint", "sh", "-v", project + "-data:/var/lib/grafana", service["image"],
            "-c", "chown 472:472 /var/lib/grafana")
        run(*command, "up", "-d", "--wait", "--wait-timeout", "300", timeout=360)
        exercise(seed=True)
        service["environment"]["GF_SECURITY_ADMIN_PASSWORD"] = env["ODS_GRAFANA_RESEED_PASSWORD"]
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "300", timeout=360)
        exercise(seed=False)
        logs = run("docker", "logs", project)
        for key in ("GRAFANA_SECRET_KEY", "GRAFANA_ADMIN_PASSWORD", "ODS_METRICS_PASSWORD"):
            assert env[key] not in logs.stdout + logs.stderr
        print("Grafana real authentication, persisted dashboard, source-secret decryption/query and initial-password semantics passed")
    finally:
        logs = run("docker", "logs", "--tail", "12", project, check=False)
        print("Grafana diagnostics:", logs.stdout, logs.stderr)
        run(*command, "down", "--volumes", "--timeout", "30")
