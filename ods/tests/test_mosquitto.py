"""Rendered plan and opt-in native MQTT 5 / WebSocket lifecycle."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import queue
import secrets
import shutil
import subprocess
import threading
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extensions/library/services/mosquitto"


def render(tmp_path, publisher="fixture-publisher", reader="fixture-reader", bind=None, port="1883", ws="9001", check=True):
    env = {key: value for key, value in os.environ.items() if not key.startswith("MOSQUITTO_") and key != "BIND_ADDRESS"}
    env.update(MOSQUITTO_PORT=port, MOSQUITTO_WEBSOCKET_PORT=ws)
    for key, value in (("MOSQUITTO_PUBLISH_PASSWORD", publisher), ("MOSQUITTO_READ_PASSWORD", reader), ("BIND_ADDRESS", bind)):
        if value is not None:
            env[key] = value
    empty = tmp_path / "empty.env"
    empty.write_text("")
    return subprocess.run(["docker", "compose", "--env-file", str(empty), "--project-directory", str(tmp_path),
                           "-f", str(EXTENSION / "compose.yaml"), "config", "--format", "json"],
                          env=env, check=check, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("bind", [None, "127.0.0.1", "0.0.0.0", "192.0.2.42"])
@pytest.mark.parametrize("port,ws", [("1883", "9001"), ("11883", "19001")])
def test_only_mqtt_listeners_are_published_and_credentials_are_declared(tmp_path, bind, port, ws):
    plan = json.loads(render(tmp_path, bind=bind, port=port, ws=ws).stdout)
    service = plan["services"]["mosquitto"]
    assert {(item["target"], item["host_ip"], item["published"]) for item in service["ports"]} == {
        (1883, bind or "127.0.0.1", port), (9001, bind or "127.0.0.1", ws)}
    assert service["user"] == "1883:1883" and service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    catalog = json.loads((ROOT / "config/extensions-catalog.json").read_text())
    entry = next(item for item in catalog["extensions"] if item["id"] == "mosquitto")
    assert {item["key"] for item in entry["env_vars"] if item.get("required")} == {
        "MOSQUITTO_PUBLISH_PASSWORD", "MOSQUITTO_READ_PASSWORD"}


@pytest.mark.parametrize("publisher,reader", [(None, "reader"), ("", "reader"), ("publisher", None), ("publisher", "")])
def test_each_role_requires_a_password(tmp_path, publisher, reader):
    result = render(tmp_path, publisher, reader, check=False)
    assert result.returncode != 0 and "MOSQUITTO_" in result.stderr


@pytest.mark.skipif(os.getenv("ODS_TEST_MOSQUITTO") != "1", reason="Opt-in real MQTT lifecycle")
def test_native_mqtt_and_websockets_auth_acl_retained_restore_and_rotation(tmp_path):
    import paho.mqtt.client as mqtt

    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        pytest.skip("Qualification prepares isolated UID 1883 storage")
    project = "ods-q20-mosquitto-" + uuid.uuid4().hex[:12]
    publisher, reader = secrets.token_hex(16) + "'$\\;", secrets.token_hex(16)
    data = tmp_path / "data/mosquitto"
    data.mkdir(parents=True)
    os.chown(data, 1883, 1883)
    plan = json.loads(render(tmp_path, publisher, reader).stdout)
    service = plan["services"]["mosquitto"]
    service.update(container_name=project, restart="no", labels={"io.ods.quality20.validation": "true"})
    for binding in service["ports"]:
        binding["published"] = "0"
    for mount in service["volumes"]:
        if mount["target"].startswith("/etc/ods-mosquitto/"):
            mount["source"] = str(EXTENSION / Path(mount["target"]).name)
    plan["networks"] = {"ods-network": {"name": project + "-network"}}
    config = tmp_path / "isolated.json"
    config.write_text(json.dumps(plan))
    config.chmod(0o600)
    command = ["docker", "compose", "-p", project, "-f", str(config)]

    def run(*args, check=True):
        result = subprocess.run(args, check=False, capture_output=True, text=True, timeout=180)
        assert not check or result.returncode == 0, result.stdout + result.stderr
        return result

    def endpoint(transport):
        target = "1883/tcp" if transport == "tcp" else "9001/tcp"
        binding = json.loads(run("docker", "inspect", project).stdout)[0]["NetworkSettings"]["Ports"][target][0]
        assert binding["HostIp"] == "127.0.0.1"
        return int(binding["HostPort"])

    @contextmanager
    def client(transport, username=None, password=None, accepted=True):
        connected = threading.Event()
        state = {}
        incoming, published, subscribed = queue.Queue(), queue.Queue(), queue.Queue()
        instance = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="ods-fixture-" + uuid.uuid4().hex,
                               protocol=mqtt.MQTTv5, transport=transport, reconnect_on_failure=False)
        if username:
            instance.username_pw_set(username, password)
        if transport == "websockets":
            instance.ws_set_options(path="/mqtt")

        def on_connect(_client, _userdata, _flags, reason_code, _properties):
            state["reason"] = reason_code.value
            connected.set()

        instance.on_connect = on_connect
        instance.on_message = lambda _client, _userdata, message: incoming.put(message)
        instance.on_publish = lambda _client, _userdata, mid, reason, _properties: published.put((mid, reason.value))
        instance.on_subscribe = lambda _client, _userdata, mid, reasons, _properties: subscribed.put((mid, [reason.value for reason in reasons]))
        instance.connect("127.0.0.1", endpoint(transport), keepalive=20)
        instance.loop_start()
        try:
            assert connected.wait(10), "Native MQTT CONNACK was not received"
            assert (state["reason"] == 0) is accepted, state
            if not accepted:
                assert state["reason"] in (134, 135), state
            yield instance, incoming, published, subscribed
        finally:
            instance.disconnect()
            instance.loop_stop()

    def publish(transport, username, password, topic, payload, accepted=True):
        with client(transport, username, password) as (connection, _incoming, receipts, _subscriptions):
            receipt = connection.publish(topic, payload, qos=1, retain=True)
            mid, reason = receipts.get(timeout=10)
            assert mid == receipt.mid
            assert reason in (0, 16) if accepted else reason == 135

    def retained(transport, expected):
        with client(transport, "reader", reader) as (connection, incoming, _receipts, subscriptions):
            rc, mid = connection.subscribe("ods/fixture/temperature", qos=1)
            assert rc == mqtt.MQTT_ERR_SUCCESS
            sub_mid, reasons = subscriptions.get(timeout=10)
            assert sub_mid == mid and reasons == [1]
            message = incoming.get(timeout=10)
            assert message.payload == expected and message.retain is True and message.qos == 1

    payload = json.dumps({"label": "Xin chào", "temperature": 23.5}, ensure_ascii=False).encode()
    try:
        run(*command, "up", "-d", "--wait", "--wait-timeout", "90")
        for transport in ("tcp", "websockets"):
            with client(transport, accepted=False):
                pass
            with client(transport, "publisher", "incorrect", accepted=False):
                pass
            publish(transport, "publisher", publisher, "ods/fixture/temperature", payload)
            retained(transport, payload)
            publish(transport, "reader", reader, "ods/fixture/temperature", b"forbidden", accepted=False)
            publish(transport, "publisher", publisher, "outside/fixture", b"forbidden", accepted=False)
            retained(transport, payload)
            with client(transport, "publisher", publisher) as (connection, incoming, _receipts, subscriptions):
                connection.subscribe("ods/fixture/temperature", qos=1)
                subscriptions.get(timeout=10)
                publish(transport, "publisher", publisher, "ods/fixture/temperature", payload)
                # Subscription acceptance does not grant receive permission.
                with pytest.raises(queue.Empty):
                    incoming.get(timeout=2)
        version = run("docker", "exec", project, "wget", "-qO-", "http://127.0.0.1:8081/api/v1/version")
        assert "2.1.2" in version.stdout
        assert "world readable permissions" not in run(*command, "logs", "mosquitto").stdout
        for path in ("listeners", "systree"):
            denied = run("docker", "exec", project, "wget", "-qO-", "http://127.0.0.1:8081/api/v1/" + path, check=False)
            assert denied.returncode != 0 and "401" in denied.stderr
        print("MQTT and WebSocket authentication, role ACLs, and retained Unicode payload verified", flush=True)
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "90")
        retained("tcp", payload)
        old = publisher
        publisher = secrets.token_hex(16)
        service["environment"]["MOSQUITTO_PUBLISH_PASSWORD"] = publisher
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "90")
        for transport in ("tcp", "websockets"):
            with client(transport, "publisher", old, accepted=False):
                pass
            publish(transport, "publisher", publisher, "ods/fixture/temperature", payload)
        run(*command, "stop", "--timeout", "30")
        restored = tmp_path / "restored"
        shutil.copytree(data, restored)
        for path in [restored, *restored.rglob("*")]:
            os.chown(path, 1883, 1883)
        next(mount for mount in service["volumes"] if mount["target"] == "/mosquitto/data")["source"] = str(restored)
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "90")
        retained("tcp", payload)
        retained("websockets", payload)
        publish("tcp", "publisher", publisher, "ods/fixture/temperature", b"")
        with client("tcp", "reader", reader) as (connection, incoming, _receipts, subscriptions):
            connection.subscribe("ods/fixture/temperature", qos=1)
            subscriptions.get(timeout=10)
            with pytest.raises(queue.Empty):
                incoming.get(timeout=2)
        print("Graceful recreation, password rotation, cold restore, and retained-message deletion verified", flush=True)
    finally:
        print(run(*command, "logs", "--tail", "20").stdout)
        run(*command, "down", "--volumes")
