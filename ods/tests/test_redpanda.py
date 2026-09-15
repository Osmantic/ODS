"""Rendered plans and native Kafka log/consumer-offset lifecycle qualification."""

import base64
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import urllib.error
import urllib.request
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extensions/library/services/redpanda"


def render(tmp_path, password="fixture-password", bind=None, port="19092", host="127.0.0.1", check=True):
    env = {key: value for key, value in os.environ.items() if not key.startswith("REDPANDA_") and key != "BIND_ADDRESS"}
    env.update(REDPANDA_PORT=port, REDPANDA_ADVERTISE_HOST=host)
    if password is not None:
        env["REDPANDA_PASSWORD"] = password
    if bind is not None:
        env["BIND_ADDRESS"] = bind
    empty = tmp_path / "empty.env"
    empty.write_text("")
    return subprocess.run(["docker", "compose", "--env-file", str(empty), "--project-directory", str(tmp_path),
                           "-f", str(EXTENSION / "compose.yaml"), "config", "--format", "json"],
                          env=env, check=check, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("bind", [None, "127.0.0.1", "0.0.0.0", "192.0.2.42"])
@pytest.mark.parametrize("port,host", [("19092", "127.0.0.1"), ("29092", "broker.example.test")])
def test_host_metadata_tracks_published_port_and_admin_is_private(tmp_path, bind, port, host):
    service = json.loads(render(tmp_path, bind=bind, port=port, host=host).stdout)["services"]["redpanda"]
    assert [(item["target"], item["host_ip"], item["published"]) for item in service["ports"]] == [(19092, bind or "127.0.0.1", port)]
    assert str(service["environment"]["REDPANDA_PORT"]) == port
    assert service["environment"]["REDPANDA_ADVERTISE_HOST"] == host
    assert service["user"] == "101:101" and service["read_only"] is True
    catalog = json.loads((ROOT / "config/extensions-catalog.json").read_text())
    entry = next(item for item in catalog["extensions"] if item["id"] == "redpanda")
    assert {item["key"] for item in entry["env_vars"] if item.get("required")} == {"REDPANDA_PASSWORD"}


@pytest.mark.parametrize("password", [None, ""])
def test_bootstrap_password_is_required(tmp_path, password):
    result = render(tmp_path, password, check=False)
    assert result.returncode != 0 and "REDPANDA_PASSWORD" in result.stderr


@pytest.mark.skipif(os.getenv("ODS_TEST_REDPANDA") != "1", reason="Opt-in native Kafka lifecycle")
@pytest.mark.parametrize("password", ["fixture:delimiter", "fixture\nnewline", "fixture\rcarriage"])
def test_native_entrypoint_rejects_invalid_bootstrap_format_without_disclosing_it(password):
    import yaml

    image = yaml.safe_load((EXTENSION / "compose.yaml").read_text())["services"]["redpanda"]["image"]
    env = {**os.environ, "REDPANDA_PASSWORD": password}
    result = subprocess.run(["docker", "run", "--rm", "--read-only", "--user", "101:101", "--entrypoint", "/bin/bash",
                             "--env", "REDPANDA_PASSWORD", "--volume", str(EXTENSION / "start.sh") + ":/ods-start.sh:ro",
                             image, "/ods-start.sh"], env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 2 and "native bootstrap credential format" in result.stderr
    assert password not in result.stdout + result.stderr


@pytest.mark.skipif(os.getenv("ODS_TEST_REDPANDA") != "1", reason="Opt-in native Kafka lifecycle")
def test_native_scram_acl_replay_consumer_offsets_rotation_and_cold_restore(tmp_path):
    import yaml
    from confluent_kafka import Consumer, KafkaError, Producer, TopicPartition
    from confluent_kafka.admin import AdminClient, AclBinding, AclOperation, AclPermissionType, NewTopic, ResourcePatternType, ResourceType

    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        pytest.skip("Qualification prepares isolated UID 101 broker storage")
    project = "ods-q20-redpanda-" + uuid.uuid4().hex[:12]
    password, reader_password = secrets.token_hex(16) + "'$\\;", secrets.token_hex(16)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        kafka_port = reservation.getsockname()[1]
    # Kafka metadata must advertise a concrete port before the broker starts.
    # A competing bind fails visibly; the fixture does not retry another port.
    data = tmp_path / "data/redpanda"
    data.mkdir(parents=True)
    os.chown(data, 101, 101)
    plan = json.loads(render(tmp_path, password, port=str(kafka_port)).stdout)
    service = plan["services"]["redpanda"]
    service.update(container_name=project, restart="no", labels={"io.ods.quality20.validation": "true"})
    next(mount for mount in service["volumes"] if mount["target"] == "/etc/ods-redpanda")["source"] = str(EXTENSION)
    # Fixture-only loopback binding exercises the otherwise-private native API.
    service["ports"].append({"target": 9644, "published": "0", "host_ip": "127.0.0.1", "protocol": "tcp"})
    plan["networks"] = {"ods-network": {"name": project + "-network"}}
    config = tmp_path / "isolated.json"
    config.write_text(json.dumps(plan))
    config.chmod(0o600)
    command = ["docker", "compose", "-p", project, "-f", str(config)]

    def run(*args, check=True):
        result = subprocess.run(args, check=False, capture_output=True, text=True, timeout=240)
        assert not check or result.returncode == 0, result.stdout + result.stderr
        return result

    def admin(path, *, method="GET", payload=None, credential=None, expected=200):
        binding = json.loads(run("docker", "inspect", project).stdout)[0]["NetworkSettings"]["Ports"]["9644/tcp"][0]
        assert binding["HostIp"] == "127.0.0.1"
        headers = {"Content-Type": "application/json"}
        if credential is not None:
            headers["Authorization"] = "Basic " + base64.b64encode(("ods:" + credential).encode()).decode()
        request = urllib.request.Request("http://127.0.0.1:" + binding["HostPort"] + path, method=method,
                                         data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            response = opener.open(request, timeout=20)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            body = response.read()
            assert response.status == expected, f"{method} {path}: HTTP {response.status}: {body[:250]!r}"
            return json.loads(body) if body else None

    def kafka_settings(username="ods", credential=None):
        return {"bootstrap.servers": f"127.0.0.1:{kafka_port}", "security.protocol": "SASL_PLAINTEXT",
                "sasl.mechanism": "SCRAM-SHA-256", "sasl.username": username,
                "sasl.password": password if credential is None else credential, "socket.timeout.ms": 10000}

    def internal_metadata(credential, accepted=True):
        result = run("docker", "exec", project, "rpk", "cluster", "info", "--format", "json", "-X", "brokers=redpanda:9092",
                     "-X", "user=ods", "-X", "pass=" + credential, "-X", "sasl.mechanism=SCRAM-SHA-256", check=False)
        if accepted:
            assert result.returncode == 0, result.stderr
            assert "redpanda" in result.stdout and "9092" in result.stdout
        else:
            assert result.returncode != 0 and "SASL_AUTHENTICATION_FAILED" in result.stderr, result.stderr

    topic, group = "ods-fixture-events", "ods-fixture-reader"

    def publish(value, username="ods", credential=None, denied=False):
        receipts = []
        producer = Producer({**kafka_settings(username, credential), "acks": "all", "retries": 0, "message.timeout.ms": 10000})
        producer.produce(topic, value=value, key=b"fixture", on_delivery=lambda error, message: receipts.append((error, message)))
        assert producer.flush(15) == 0 and len(receipts) == 1
        error, message = receipts[0]
        if denied:
            assert error is not None and error.code() == KafkaError.TOPIC_AUTHORIZATION_FAILED, error
        else:
            assert error is None, error
            return message.offset()

    def consume_next(expected):
        consumer = Consumer({**kafka_settings("reader", reader_password), "group.id": group,
                             "enable.auto.commit": False, "auto.offset.reset": "earliest"})
        try:
            consumer.subscribe([topic])
            message = consumer.poll(20)
            assert message is not None and message.error() is None, message.error() if message else "No Kafka record"
            assert message.value() == expected
            committed = consumer.commit(message, asynchronous=False)
            assert committed[0].offset == message.offset() + 1
        finally:
            consumer.close()

    def replay(expected):
        consumer = Consumer({**kafka_settings(), "group.id": "ods-replay", "enable.auto.commit": False})
        try:
            consumer.assign([TopicPartition(topic, 0, 0)])
            messages = consumer.consume(len(expected), timeout=20)
            assert len(messages) == len(expected)
            assert all(message.error() is None for message in messages)
            assert [message.value() for message in messages] == expected
        finally:
            consumer.close()

    first = json.dumps({"label": "Xin chào", "sequence": 1}, ensure_ascii=False).encode()
    second = b'{"sequence":2}'
    third = b'{"sequence":3}'
    try:
        run(*command, "up", "-d", "--wait", "--wait-timeout", "180")
        assert admin("/v1/status/ready")["status"] == "ready"
        admin("/v1/security/users", expected=403)
        admin("/v1/security/users", credential="incorrect", expected=401)
        native = admin("/v1/cluster_config", credential=password)
        for setting, expected in yaml.safe_load((EXTENSION / "bootstrap.yaml").read_text()).items():
            assert native[setting] == expected, f"Native cluster setting was not applied: {setting}"
        listeners = run("docker", "exec", project, "cat", "/proc/net/tcp", "/proc/net/tcp6").stdout.splitlines()
        addresses = [line.split()[1] for line in listeners if len(line.split()) > 3 and line.split()[3] == "0A"]
        assert not {8081, 8082} & {int(address.rsplit(":", 1)[1], 16) for address in addresses}
        assert "0100007F:8179" in addresses  # Native RPC 33145 stays on loopback.
        internal_metadata(password)
        internal_metadata("incorrect", accepted=False)
        administrator = AdminClient(kafka_settings())
        metadata = administrator.list_topics(timeout=10)
        assert {(broker.host, broker.port) for broker in metadata.brokers.values()} == {("127.0.0.1", kafka_port)}
        for future in administrator.create_topics([NewTopic(topic, num_partitions=1, replication_factor=1)]).values():
            future.result(timeout=20)
        admin("/v1/security/users", method="POST", credential=password,
              payload={"username": "reader", "password": reader_password, "algorithm": "SCRAM-SHA-256"})
        bindings = [AclBinding(ResourceType.TOPIC, topic, ResourcePatternType.LITERAL, "User:reader", "*", operation, AclPermissionType.ALLOW)
                    for operation in (AclOperation.READ, AclOperation.DESCRIBE)]
        bindings.append(AclBinding(ResourceType.GROUP, group, ResourcePatternType.LITERAL, "User:reader", "*", AclOperation.READ, AclPermissionType.ALLOW))
        for future in administrator.create_acls(bindings).values():
            future.result(timeout=20)
        del administrator
        assert publish(first) == 0
        publish(b"forbidden", "reader", reader_password, denied=True)
        consume_next(first)
        replay([first])
        print("Native SCRAM, private API auth, Kafka listener metadata, reader ACL, and committed offset verified", flush=True)
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "180")
        assert publish(second) == 1
        consume_next(second)
        replay([first, second])
        previous = password
        password = secrets.token_hex(16)
        admin("/v1/security/users/ods", method="PUT", credential=previous,
              payload={"password": password, "algorithm": "SCRAM-SHA-256"})
        admin("/v1/security/users", credential=previous, expected=401)
        # An obsolete bootstrap seed must not reset the persisted native user.
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "180")
        internal_metadata(password)
        internal_metadata(previous, accepted=False)
        run(*command, "stop", "--timeout", "45")
        restored = tmp_path / "restored"
        shutil.copytree(data, restored)
        for path in [restored, *restored.rglob("*")]:
            os.chown(path, 101, 101)
        next(mount for mount in service["volumes"] if mount["target"] == "/var/lib/redpanda/data")["source"] = str(restored)
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "180")
        assert publish(third) == 2
        consume_next(third)
        replay([first, second, third])
        cleanup = AdminClient(kafka_settings())
        for future in cleanup.delete_topics([topic]).values():
            future.result(timeout=20)
        del cleanup
        print("Replay, consumer-offset recovery, native password rotation, ignored obsolete seed, cold restore, and topic deletion verified", flush=True)
    finally:
        print(run(*command, "logs", "--tail", "25").stdout)
        run(*command, "down", "--volumes")
