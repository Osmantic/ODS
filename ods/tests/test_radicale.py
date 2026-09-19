"""Library rendering and opt-in CalDAV/CardDAV protocol and persistence checks."""

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
import yaml

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extensions/library/services/radicale"
LIVE = pytest.mark.skipif(os.getenv("ODS_TEST_RADICALE") != "1", reason="Opt-in real DAV lifecycle")


@pytest.fixture
def compose_env(tmp_path):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI required")
    (tmp_path / "empty.env").write_text("")
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("RADICALE_") and key != "BIND_ADDRESS"}
    env["RADICALE_PASSWORD"] = secrets.token_hex(24) + "$:'\"%#[]"
    return env


def render(tmp_path, env, check=True):
    return subprocess.run([
        "docker", "compose", "--env-file", str(tmp_path / "empty.env"),
        "--project-directory", str(tmp_path), "-f", str(EXTENSION / "compose.yaml"),
        "config", "--format", "json", "radicale",
    ], env=env, capture_output=True, text=True, check=check, timeout=30)


@pytest.mark.parametrize("port", ["5232", "15232"])
def test_library_installation_keeps_private_storage_and_nonroot_runtime(tmp_path, compose_env, port):
    compose_env["RADICALE_PORT"] = port
    service = json.loads(render(tmp_path, compose_env).stdout)["services"]["radicale"]
    assert service["ports"][0]["published"] == port
    assert service["ports"][0]["host_ip"] == "127.0.0.1"
    assert service["ports"][0]["target"] == 5232
    assert service["user"] == "1000:1000"
    assert service["read_only"] and service["cap_drop"] == ["ALL"]
    assert service["volumes"][0]["source"] == str(tmp_path / "data/radicale")
    guard = service["volumes"][1]
    assert guard["source"] == str(tmp_path / "config/radicale/entrypoint.py")
    assert guard["read_only"] and not guard["bind"].get("create_host_path", False)
    manifest = yaml.safe_load((EXTENSION / "manifest.yaml").read_text())
    assert manifest["service"]["container_uid"] == 1000
    assert manifest["service"]["health"] == "/.web/"
    catalog = json.loads((ROOT / "config/extensions-catalog.json").read_text())
    entry = next(item for item in catalog["extensions"] if item["id"] == "radicale")
    assert entry["features"][0]["launch"]["service"] == "radicale"


@pytest.mark.parametrize("password", [None, ""])
def test_missing_password_blocks_activation(tmp_path, compose_env, password):
    if password is None:
        del compose_env["RADICALE_PASSWORD"]
    else:
        compose_env["RADICALE_PASSWORD"] = password
    result = render(tmp_path, compose_env, check=False)
    assert result.returncode != 0
    assert "RADICALE_PASSWORD" in result.stderr


@LIVE
@pytest.mark.parametrize("password", ["too-short", "é" * 37])
def test_image_rejects_invalid_credential_without_echoing_it(tmp_path, compose_env, password):
    image = json.loads(render(tmp_path, compose_env).stdout)["services"]["radicale"]["image"]
    name = "ods-q40-dav-invalid-" + uuid.uuid4().hex[:10]
    guard = EXTENSION / "config/radicale/entrypoint.py"
    try:
        subprocess.run(["docker", "create", "--name", name, "--network", "none", "--read-only",
                        "--entrypoint", "/app/bin/python", "-e", "RADICALE_PASSWORD=" + password,
                        "--mount", f"type=bind,src={guard},dst=/guard.py,readonly", image, "/guard.py"],
                       check=True, capture_output=True, text=True, timeout=30)
        result = subprocess.run(["docker", "start", "--attach", name],
                                capture_output=True, text=True, timeout=30)
        assert result.returncode != 0
        assert "16 to 72 UTF-8 bytes" in result.stdout + result.stderr
        assert password not in result.stdout + result.stderr
    finally:
        # A timed-out create can still leave a container registered by the daemon.
        created = subprocess.run(["docker", "ps", "--all", "--quiet", "--filter", f"name=^/{name}$"],
                                 check=True, capture_output=True, text=True, timeout=30)
        if created.stdout.strip():
            subprocess.run(["docker", "rm", "--force", "--volumes", name],
                           check=True, capture_output=True, text=True, timeout=30)


@LIVE
def test_live_calendar_contacts_etags_recreation_and_rotation(tmp_path, compose_env):
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        pytest.skip("This host-bind qualification prepares UID 1000 storage in an isolated root workspace")
    project = "ods-q40-dav-" + uuid.uuid4().hex[:12]
    plan = json.loads(render(tmp_path, compose_env).stdout)
    service = plan["services"]["radicale"]
    service["container_name"] = project
    service["restart"] = "no"
    service["ports"] = [{"target": 5232, "published": "0", "host_ip": "127.0.0.1", "protocol": "tcp"}]
    service["labels"] = {"io.ods.quality40.validation": "true"}
    plan["networks"] = {"ods-network": {"name": project + "-network"}}
    # Match the existing host-agent preparation; its authenticated config-sync
    # and numeric UID handoff are independently exercised by test_radicale_install.
    storage = tmp_path / "data/radicale"
    storage.mkdir(parents=True)
    os.chown(storage, 1000, 1000)
    guard = tmp_path / "config/radicale/entrypoint.py"
    guard.parent.mkdir(parents=True)
    shutil.copyfile(EXTENSION / "config/radicale/entrypoint.py", guard)
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

    def base_url():
        data = json.loads(run("docker", "inspect", project))[0]
        binding = data["NetworkSettings"]["Ports"]["5232/tcp"][0]
        assert binding["HostIp"] == "127.0.0.1"
        return "http://127.0.0.1:" + binding["HostPort"]

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(base, method, path, password=None, body=None, expected=200, **headers):
        if password is not None:
            headers["Authorization"] = "Basic " + base64.b64encode(("ods:" + password).encode()).decode()
        req = urllib.request.Request(base + path, method=method, data=body, headers=headers)
        try:
            response = opener.open(req, timeout=20)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            assert response.status == expected, f"{method} {path}: HTTP {response.status}"
            return response.read(), response.headers

    password = compose_env["RADICALE_PASSWORD"]
    rotated = secrets.token_hex(24)
    event = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//ODS//DAV Test//EN\r\n"
             "BEGIN:VEVENT\r\nUID:ods-dav-fixture\r\nDTSTAMP:20260915T000000Z\r\n"
             "DTSTART:20300101T100000Z\r\nDTEND:20300101T110000Z\r\n"
             "SUMMARY:Planning thử nghiệm\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n").encode()
    card = ("BEGIN:VCARD\r\nVERSION:3.0\r\nUID:ods-card\r\nFN:Local Fixture\r\n"
            "N:Fixture;Local;;;\r\nEMAIL:fixture@example.invalid\r\nEND:VCARD\r\n").encode()
    xml = {"Content-Type": "application/xml; charset=utf-8"}
    try:
        run(*command, "up", "-d", "--wait", "--wait-timeout", "60")
        base = base_url()
        request(base, "PROPFIND", "/", expected=401, Depth="0")
        request(base, "PROPFIND", "/", "incorrect", expected=401, Depth="0")
        request(base, "PROPFIND", "/ods/", password, expected=207, Depth="0")
        request(base, "MKCALENDAR", "/ods/calendar/", password, expected=201)
        path = "/ods/calendar/event.ics"
        _, created = request(base, "PUT", path, password, event, expected=201,
                             **{"Content-Type": "text/calendar", "If-None-Match": "*"})
        original_etag = created["ETag"]
        updated = event.replace("Planning thử nghiệm".encode(), b"Updated planning")
        request(base, "PUT", path, password, updated, expected=412,
                **{"Content-Type": "text/calendar", "If-Match": '"stale"'})
        original, headers = request(base, "GET", path, password)
        assert "Planning thử nghiệm".encode() in original and headers["ETag"] == original_etag
        request(base, "PUT", path, password, updated, expected=204,
                **{"Content-Type": "text/calendar", "If-Match": original_etag})
        report = b'<C:calendar-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav"><D:prop><D:getetag/><C:calendar-data/></D:prop><C:filter><C:comp-filter name="VCALENDAR"><C:comp-filter name="VEVENT"/></C:comp-filter></C:filter></C:calendar-query>'
        calendar, _ = request(base, "REPORT", "/ods/calendar/", password, report, expected=207,
                              Depth="1", **xml)
        assert b"Updated planning" in calendar
        addressbook = b'<D:mkcol xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:carddav"><D:set><D:prop><D:resourcetype><D:collection/><C:addressbook/></D:resourcetype></D:prop></D:set></D:mkcol>'
        request(base, "MKCOL", "/ods/contacts/", password, addressbook, expected=201, **xml)
        request(base, "PUT", "/ods/contacts/person.vcf", password, card, expected=201,
                **{"Content-Type": "text/vcard"})
        query = b'<C:addressbook-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:carddav"><D:prop><D:getetag/><C:address-data/></D:prop><C:filter/></C:addressbook-query>'
        contacts, _ = request(base, "REPORT", "/ods/contacts/", password, query, expected=207,
                              Depth="1", **xml)
        assert b"fixture@example.invalid" in contacts
        request(base, "PUT", "/another/calendar/event.ics", password, event, expected=403,
                **{"Content-Type": "text/calendar"})
        request(base, "GET", path, expected=401)
        assert run("docker", "exec", project, "/app/bin/python", "-c",
                   "from pathlib import Path; print(b'RADICALE_PASSWORD=' in Path('/proc/1/environ').read_bytes())").strip() == "False"
        run(*command, "stop", "--timeout", "30")
        service["environment"]["RADICALE_PASSWORD"] = rotated
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "60")
        base = base_url()
        request(base, "GET", path, password, expected=401)
        request(base, "PUT", path, password, event, expected=401, **{"Content-Type": "text/calendar"})
        preserved, _ = request(base, "GET", path, rotated)
        assert b"Updated planning" in preserved
        preserved_card, _ = request(base, "GET", "/ods/contacts/person.vcf", rotated)
        assert b"fixture@example.invalid" in preserved_card
        logs = subprocess.run(["docker", "logs", project], check=True, capture_output=True, text=True, timeout=20)
        assert password not in logs.stdout + logs.stderr and rotated not in logs.stdout + logs.stderr
        print("CalDAV/CardDAV, owner permissions, stale ETags, persisted host-bind data and credential rotation passed")
    finally:
        try:
            diagnostics = subprocess.run(["docker", "logs", project], capture_output=True,
                                         text=True, check=True, timeout=20)
            text = diagnostics.stdout + diagnostics.stderr
            assert password not in text and rotated not in text
            print("Radicale diagnostics:", text[-3000:])
        finally:
            run(*command, "down", "--volumes", "--timeout", "30")
