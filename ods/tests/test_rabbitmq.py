"""Rendered recipe and opt-in real AMQP work-queue lifecycle."""

import base64
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import urllib.error
import urllib.request
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extensions/library/services/rabbitmq"


@pytest.fixture
def compose_env(tmp_path):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI required")
    (tmp_path / "empty.env").write_text("")
    shutil.copytree(EXTENSION / "config", tmp_path / "config")
    env = {key: value for key, value in os.environ.items() if not key.startswith("RABBITMQ_")}
    env.update(RABBITMQ_PASSWORD=secrets.token_hex(24), BIND_ADDRESS="127.0.0.1")
    return env


def render(tmp_path, env, check=True):
    return subprocess.run([
        "docker", "compose", "--env-file", str(tmp_path / "empty.env"),
        "--project-directory", str(tmp_path), "-f", str(EXTENSION / "compose.yaml"),
        "config", "--format", "json", "rabbitmq",
    ], env=env, check=check, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("bind", [None, "127.0.0.1", "0.0.0.0", "192.0.2.42"])
@pytest.mark.parametrize("ports", [("15672", "5672"), ("25672", "15673")])
def test_recipe_preserves_auth_state_and_explicit_port_binding(tmp_path, compose_env, bind, ports):
    compose_env.pop("BIND_ADDRESS")
    if bind is not None:
        compose_env["BIND_ADDRESS"] = bind
    compose_env.update(RABBITMQ_PORT=ports[0], RABBITMQ_AMQP_PORT=ports[1])
    service = json.loads(render(tmp_path, compose_env).stdout)["services"]["rabbitmq"]
    published = {p["target"]: (p["host_ip"], p["published"]) for p in service["ports"]}
    assert published == {15672: (bind or "127.0.0.1", ports[0]), 5672: (bind or "127.0.0.1", ports[1])}
    assert service["environment"]["RABBITMQ_DEFAULT_PASS"] == compose_env["RABBITMQ_PASSWORD"]
    assert service["environment"]["RABBITMQ_NODENAME"] == "rabbit@localhost"
    assert service["environment"]["ERL_EPMD_ADDRESS"] == "127.0.0.1"
    assert service["user"] == "999:999" and service["read_only"] is True
    mounts = {item["target"]: item for item in service["volumes"]}
    assert mounts["/var/lib/rabbitmq"]["source"] == str(tmp_path / "data/rabbitmq")
    assert mounts["/etc/rabbitmq/conf.d/99-ods.conf"]["read_only"] is True
    assert "check_local_alarms" in service["healthcheck"]["test"][1]
    catalog = json.loads((ROOT / "config/extensions-catalog.json").read_text())
    entry = next(item for item in catalog["extensions"] if item["id"] == "rabbitmq")
    assert entry["features"][0]["launch"] == {"type": "service", "service": "rabbitmq", "path": "/"}
    assert next(item for item in entry["env_vars"] if item["key"] == "RABBITMQ_PASSWORD")["required"]


@pytest.mark.parametrize("password", [None, ""])
def test_empty_credentials_cannot_start_guest_broker(tmp_path, compose_env, password):
    if password is None:
        del compose_env["RABBITMQ_PASSWORD"]
    else:
        compose_env["RABBITMQ_PASSWORD"] = password
    result = render(tmp_path, compose_env, check=False)
    assert result.returncode != 0 and "RABBITMQ_PASSWORD" in result.stderr


@pytest.mark.skipif(os.getenv("ODS_TEST_RABBITMQ") != "1", reason="Opt-in real RabbitMQ lifecycle")
def test_amqp_auth_acknowledgements_recreation_rotation_and_cold_restore(tmp_path, compose_env):
    import pika

    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        pytest.skip("Host-bind qualification prepares isolated UID 999 storage")
    project = "ods-q20-rabbitmq-" + uuid.uuid4().hex[:12]
    plan = json.loads(render(tmp_path, compose_env).stdout)
    service = plan["services"]["rabbitmq"]
    service.update(container_name=project, restart="no")
    service["labels"] = {"io.ods.quality20.validation": "true"}
    for port in service["ports"]:
        port["published"] = "0"
    plan["networks"] = {"ods-network": {"name": project + "-network"}}
    data = tmp_path / "data/rabbitmq"
    data.mkdir(parents=True)
    os.chown(data, 999, 999)
    config = tmp_path / "isolated.json"
    config.write_text(json.dumps(plan))
    config.chmod(0o600)
    command = ["docker", "compose", "-p", project, "-f", str(config)]
    password, rotated = compose_env["RABBITMQ_PASSWORD"], secrets.token_hex(24)

    def run(*args):
        result = subprocess.run(args, env=compose_env, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=180)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    def port(number):
        binding = json.loads(run("docker", "inspect", project))[0]["NetworkSettings"]["Ports"][f"{number}/tcp"][0]
        assert binding["HostIp"] == "127.0.0.1"
        return int(binding["HostPort"])

    def connect(credential, username="ods"):
        return pika.BlockingConnection(pika.ConnectionParameters(
            "127.0.0.1", port(5672), "ods", pika.PlainCredentials(username, credential),
            connection_attempts=1, socket_timeout=5, stack_timeout=10, blocked_connection_timeout=5,
        ))

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def http(path, credential=None, method="GET", payload=None, expected=200):
        headers = {"Content-Type": "application/json"}
        if credential:
            headers["Authorization"] = "Basic " + base64.b64encode(("ods:" + credential).encode()).decode()
        request = urllib.request.Request(f"http://127.0.0.1:{port(15672)}{path}", method=method,
                                         data=None if payload is None else json.dumps(payload).encode(), headers=headers)
        try:
            response = opener.open(request, timeout=15)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            body = response.read()
            assert response.status == expected, f"{method} {path}: HTTP {response.status}"
            return body

    payload = 'Local job: trích xuất tài liệu'.encode()
    try:
        run(*command, "up", "-d", "--wait", "--wait-timeout", "120")
        assert b"RabbitMQ" in http("/")
        for bad in (None, "incorrect"):
            http("/api/overview", bad, expected=401)
        for username, credential in (("ods", "incorrect"), ("guest", "guest")):
            with pytest.raises(pika.exceptions.ProbableAuthenticationError):
                connect(credential, username)
        with connect(password) as connection:
            channel = connection.channel()
            channel.queue_declare(queue="ods-fixture", durable=True)
            channel.confirm_delivery()
            channel.basic_publish(exchange="", routing_key="ods-fixture", body=payload,
                                  properties=pika.BasicProperties(delivery_mode=2), mandatory=True)
            delivery, properties, received = channel.basic_get("ods-fixture", auto_ack=False)
            assert received == payload and properties.delivery_mode == 2
            channel.basic_nack(delivery.delivery_tag, requeue=True)
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120")
        with connect(password) as connection:
            delivery, _, received = connection.channel().basic_get("ods-fixture", auto_ack=False)
            assert received == payload and delivery.redelivered is True
        http("/api/users/ods", password, "PUT", {"password": rotated, "tags": "administrator"}, expected=204)
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120")
        http("/api/overview", password, expected=401)
        assert json.loads(http("/api/overview", rotated))["rabbitmq_version"] == "4.3.5"
        with pytest.raises(pika.exceptions.ProbableAuthenticationError):
            connect(password)
        run(*command, "stop", "--timeout", "60")
        restored = tmp_path / "restored"
        shutil.copytree(data, restored)
        for path in [restored, *restored.rglob("*")]:
            os.chown(path, 999, 999)
        next(item for item in service["volumes"] if item["target"] == "/var/lib/rabbitmq")["source"] = str(restored)
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120")
        with connect(rotated) as connection:
            channel = connection.channel()
            delivery, _, received = channel.basic_get("ods-fixture", auto_ack=False)
            assert received == payload
            channel.basic_ack(delivery.delivery_tag)
            assert channel.basic_get("ods-fixture", auto_ack=True)[0] is None
            channel.queue_delete("ods-fixture")
        run("docker", "exec", project, "rabbitmq-diagnostics", "-q", "check_local_alarms")
        assert json.loads(run("docker", "inspect", project))[0]["State"]["Health"]["Status"] == "healthy"
        print("Validated AMQP auth, publisher confirms, requeue, persistence, native rotation and cold restore")
    finally:
        print(run(*command, "logs", "--tail", "30"))
        run(*command, "down", "--volumes")
