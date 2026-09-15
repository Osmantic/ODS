"""Rendered library installation and opt-in real SMTP/inbox lifecycle."""

import base64
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
import json
import os
from pathlib import Path
import secrets
import shutil
import smtplib
import subprocess
import urllib.error
import urllib.request
import uuid

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extensions/library/services/mailpit"


@pytest.fixture
def compose_env(tmp_path):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI required")
    (tmp_path / "empty.env").write_text("")
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("MAILPIT_", "MP_"))}
    env.update(MAILPIT_UI_PASSWORD=secrets.token_hex(24),
               MAILPIT_SMTP_PASSWORD=secrets.token_hex(24), BIND_ADDRESS="127.0.0.1")
    return env


def render(tmp_path, env, check=True):
    return subprocess.run([
        "docker", "compose", "--env-file", str(tmp_path / "empty.env"),
        "--project-directory", str(tmp_path), "-f", str(EXTENSION / "compose.yaml"),
        "config", "--format", "json", "mailpit",
    ], env=env, capture_output=True, text=True, check=check, timeout=30)


@pytest.mark.parametrize("bind_address", [None, "127.0.0.1", "0.0.0.0", "192.0.2.42"])
def test_bind_address_opt_in_preserves_native_configuration(tmp_path, compose_env, bind_address):
    compose_env.pop("BIND_ADDRESS", None)
    baseline = json.loads(render(tmp_path, compose_env).stdout)["services"]["mailpit"]
    if bind_address is not None:
        compose_env["BIND_ADDRESS"] = bind_address
    service = json.loads(render(tmp_path, compose_env).stdout)["services"]["mailpit"]
    assert all(port["host_ip"] == (bind_address or "127.0.0.1") for port in service["ports"])
    assert [(p["published"], p["target"]) for p in service["ports"]] == [
        (p["published"], p["target"]) for p in baseline["ports"]]
    assert service["environment"] == baseline["environment"]
    assert service["volumes"] == baseline["volumes"]


@pytest.mark.parametrize("ports", [("8025", "1025"), ("18025", "11025")])
def test_library_render_preserves_capture_ports_storage_and_credentials(tmp_path, compose_env, ports):
    compose_env.update(MAILPIT_PORT=ports[0], MAILPIT_SMTP_PORT=ports[1])
    service = json.loads(render(tmp_path, compose_env).stdout)["services"]["mailpit"]
    assert [(p["host_ip"], p["published"], p["target"]) for p in service["ports"]] == [
        ("127.0.0.1", ports[0], 8025), ("127.0.0.1", ports[1], 1025)]
    assert service["user"] == "1000:1000" and service["read_only"]
    assert service["cap_drop"] == ["ALL"]
    assert service["volumes"][0]["source"] == str(tmp_path / "data/mailpit")
    assert service["volumes"][1]["source"] == str(tmp_path / "config/mailpit/entrypoint.sh")
    assert service["volumes"][1]["read_only"]
    env = service["environment"]
    assert env["MP_DATABASE"] == "/data/mailpit.db"
    assert env["MP_DISABLE_VERSION_CHECK"] == "true"
    assert env["MP_SMTP_DISABLE_RDNS"] == "true"
    assert env["MP_MAX_MESSAGE_SIZE"] == "10"
    assert not any(key.startswith(("MP_SMTP_RELAY", "MP_SMTP_FORWARD", "MP_WEBHOOK")) for key in env)
    assert service["healthcheck"]["test"] == ["CMD", "/mailpit", "readyz"]
    manifest = yaml.safe_load((EXTENSION / "manifest.yaml").read_text())
    assert manifest["service"]["health"] == "/readyz"


@pytest.mark.parametrize("key", ["MAILPIT_UI_PASSWORD", "MAILPIT_SMTP_PASSWORD"])
@pytest.mark.parametrize("value", [None, ""])
def test_missing_credential_blocks_install_plan(tmp_path, compose_env, key, value):
    if value is None:
        del compose_env[key]
    else:
        compose_env[key] = value
    result = render(tmp_path, compose_env, check=False)
    assert result.returncode != 0 and key in result.stderr


@pytest.mark.parametrize("key", ["MAILPIT_UI_PASSWORD", "MAILPIT_SMTP_PASSWORD"])
@pytest.mark.parametrize("value", ["short", "a" * 129, "space password 1234", "colon:password1234", "é" * 24])
def test_startup_rejects_ambiguous_native_auth_without_echoing_value(compose_env, key, value):
    compose_env[key] = value
    result = subprocess.run(["sh", str(EXTENSION / "config/mailpit/entrypoint.sh")],
                            env=compose_env, capture_output=True, text=True, timeout=5)
    assert result.returncode != 0
    assert key in result.stderr and value not in result.stderr
    assert "/mailpit: not found" not in result.stderr


@pytest.mark.skipif(os.getenv("ODS_TEST_MAILPIT") != "1", reason="Opt-in real SMTP and HTTP capture")
def test_live_smtp_capture_inbox_recreation_rotation_and_no_release(tmp_path, compose_env):
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        pytest.skip("Host-bind qualification prepares isolated UID 1000 storage")
    project = "ods-q40-mailpit-" + uuid.uuid4().hex[:12]
    plan = json.loads(render(tmp_path, compose_env).stdout)
    service = plan["services"]["mailpit"]
    service["container_name"] = project
    service["restart"] = "no"
    for port in service["ports"]:
        port["published"] = "0"
    service["labels"] = {"io.ods.quality40.validation": "true"}
    plan["networks"] = {"ods-network": {"name": project + "-network"}}
    data = tmp_path / "data/mailpit"
    data.mkdir(parents=True)
    os.chown(data, 1000, 1000)
    guard = tmp_path / "config/mailpit/entrypoint.sh"
    guard.parent.mkdir(parents=True)
    shutil.copyfile(EXTENSION / "config/mailpit/entrypoint.sh", guard)
    guard.chmod(0o644)
    config = tmp_path / "isolated.json"
    config.write_text(json.dumps(plan))
    config.chmod(0o600)
    command = ["docker", "compose", "-p", project, "-f", str(config)]

    def run(*args):
        result = subprocess.run(args, env=compose_env, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    def ports():
        bindings = json.loads(run("docker", "inspect", project))[0]["NetworkSettings"]["Ports"]
        return {int(port.split("/")[0]): int(value[0]["HostPort"])
                for port, value in bindings.items() if value}

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def http(port, method, path, password=None, payload=None, expected=200):
        headers = {}
        if password is not None:
            headers["Authorization"] = "Basic " + base64.b64encode(("ods:" + password).encode()).decode()
        if payload is not None:
            payload = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(f"http://127.0.0.1:{port}" + path,
                                         data=payload, method=method, headers=headers)
        try:
            response = opener.open(request, timeout=20)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            body = response.read()
            assert response.status == expected, f"{method} {path}: {response.status}: {body!r}"
            return body

    ui_password = compose_env["MAILPIT_UI_PASSWORD"]
    smtp_password = compose_env["MAILPIT_SMTP_PASSWORD"]
    new_ui, new_smtp = secrets.token_hex(24), secrets.token_hex(24)
    message = EmailMessage()
    message["From"] = "workflow@example.invalid"
    message["To"] = "preview@example.invalid"
    message["Subject"] = "Workflow thử nghiệm"
    message.set_content("Synthetic local preview only.\nSecond line.")
    attachment = b"\x00\x01synthetic attachment\xff\r\n"
    message.add_attachment(attachment, maintype="application", subtype="octet-stream", filename="fixture.bin")
    try:
        run(*command, "up", "-d", "--wait", "--wait-timeout", "60")
        bound = ports()
        http(bound[8025], "GET", "/readyz")
        for password in (None, "incorrect", smtp_password):
            http(bound[8025], "GET", "/api/v1/messages", password, expected=401)
            http(bound[8025], "POST", "/api/v1/send", password, {}, expected=401)
        with smtplib.SMTP("127.0.0.1", bound[1025], timeout=15) as smtp:
            smtp.ehlo()
            with pytest.raises(smtplib.SMTPSenderRefused):
                smtp.send_message(message)
            with pytest.raises(smtplib.SMTPAuthenticationError):
                smtp.login("ods", ui_password)
            smtp.login("ods", smtp_password)
            assert smtp.send_message(message) == {}
        inbox = json.loads(http(bound[8025], "GET", "/api/v1/messages", ui_password))
        assert inbox["total"] == 1, inbox
        record = inbox["messages"][0]
        assert record["Subject"] == str(message["Subject"])
        message_id = record["ID"]
        raw_path = f"/api/v1/message/{message_id}/raw"
        raw = http(bound[8025], "GET", raw_path, ui_password)
        decoded = BytesParser(policy=policy.default).parsebytes(raw)
        wire_message = BytesParser(policy=policy.default).parsebytes(message.as_bytes(policy=policy.SMTP))
        assert decoded.get_body(preferencelist=("plain",)).get_content() == wire_message.get_body().get_content()
        assert next(decoded.iter_attachments()).get_payload(decode=True) == attachment
        http(bound[8025], "POST", f"/api/v1/message/{message_id}/release", ui_password,
             {"To": ["preview@example.invalid"]}, expected=400)
        run(*command, "stop", "--timeout", "30")
        service["environment"].update(MAILPIT_UI_PASSWORD=new_ui, MAILPIT_SMTP_PASSWORD=new_smtp)
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "60")
        bound = ports()
        http(bound[8025], "GET", raw_path, ui_password, expected=401)
        assert http(bound[8025], "GET", raw_path, new_ui) == raw
        with smtplib.SMTP("127.0.0.1", bound[1025], timeout=15) as smtp:
            with pytest.raises(smtplib.SMTPAuthenticationError):
                smtp.login("ods", smtp_password)
            smtp.login("ods", new_smtp)
            assert smtp.send_message(message) == {}
        inbox = json.loads(http(bound[8025], "GET", "/api/v1/messages", new_ui))
        assert inbox["total"] == 2
        http(bound[8025], "DELETE", "/api/v1/messages", new_ui, {"IDs": [message_id]})
        assert json.loads(http(bound[8025], "GET", "/api/v1/messages", new_ui))["total"] == 1
        logs = subprocess.run(["docker", "logs", project], check=True, capture_output=True, text=True, timeout=20)
        assert all(secret not in logs.stdout + logs.stderr
                   for secret in (ui_password, smtp_password, new_ui, new_smtp))
        print("Authenticated SMTP capture, MIME attachment, disabled release, persistence, rotation and deletion passed")
    finally:
        run(*command, "down", "--volumes", "--timeout", "30")
