"""Readeck recipe and opt-in native capture, export and account lifecycle."""

from html.parser import HTMLParser
from http.cookiejar import CookieJar
import io
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from xml.etree import ElementTree

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extensions/library/services/readeck"


@pytest.fixture
def compose_env(tmp_path):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI required")
    (tmp_path / "empty.env").write_text("")
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("READECK_")}
    env.update(READECK_PASSWORD=secrets.token_hex(24), BIND_ADDRESS="127.0.0.1")
    return env


def render(tmp_path, env, check=True):
    return subprocess.run([
        "docker", "compose", "--env-file", str(tmp_path / "empty.env"),
        "--project-directory", str(tmp_path), "-f", str(EXTENSION / "compose.yaml"),
        "config", "--format", "json", "readeck",
    ], env=env, check=check, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("bind_address", [None, "127.0.0.1", "0.0.0.0", "192.0.2.42"])
def test_bind_address_opt_in_preserves_native_configuration(tmp_path, compose_env, bind_address):
    compose_env.pop("BIND_ADDRESS", None)
    baseline = json.loads(render(tmp_path, compose_env).stdout)["services"]["readeck"]
    if bind_address is not None:
        compose_env["BIND_ADDRESS"] = bind_address
    service = json.loads(render(tmp_path, compose_env).stdout)["services"]["readeck"]
    assert all(port["host_ip"] == (bind_address or "127.0.0.1") for port in service["ports"])
    assert [(p["published"], p["target"]) for p in service["ports"]] == [
        (p["published"], p["target"]) for p in baseline["ports"]]
    assert service["environment"] == baseline["environment"]
    assert service["volumes"] == baseline["volumes"]


@pytest.mark.parametrize("port", ["8096", "18096"])
def test_local_reading_library_recipe(tmp_path, compose_env, port):
    compose_env["READECK_PORT"] = port
    service = json.loads(render(tmp_path, compose_env).stdout)["services"]["readeck"]
    assert service["ports"][0]["host_ip"] == "127.0.0.1"
    assert service["ports"][0]["published"] == port
    assert service["ports"][0]["target"] == 8000
    assert service["user"] == "1000:1000" and service["read_only"] and service["init"]
    assert service["volumes"][0]["source"] == str(tmp_path / "data/readeck")
    assert service["environment"]["READECK_DATABASE_SOURCE"] == "sqlite3:/readeck/data/db.sqlite3"
    assert service["environment"]["READECK_ALLOWED_HOSTS"] == "localhost,127.0.0.1,readeck"
    assert service["environment"]["READECK_TRUSTED_PROXIES"] == "127.0.0.1/32,::1/128"
    assert "-password env:READECK_PASSWORD" in service["command"][0]
    assert service["healthcheck"]["test"] == ["CMD", "/bin/readeck", "healthcheck", "-config", "/readeck/config.toml"]


@pytest.mark.parametrize("value", [None, ""])
def test_missing_password_blocks_activation(tmp_path, compose_env, value):
    if value is None:
        del compose_env["READECK_PASSWORD"]
    else:
        compose_env["READECK_PASSWORD"] = value
    result = render(tmp_path, compose_env, check=False)
    assert result.returncode != 0 and "READECK_PASSWORD" in result.stderr


@pytest.mark.parametrize("value", ["short", "x" * 129, "bad\ncredential", "no:ambiguous:value"])
def test_invalid_password_stops_before_account_bootstrap(tmp_path, compose_env, value):
    service = json.loads(render(tmp_path, compose_env).stdout)["services"]["readeck"]
    compose_env["READECK_PASSWORD"] = value
    result = subprocess.run(["/bin/bash", "-euc", service["command"][0].replace("$$", "$")],
                            cwd=tmp_path, env=compose_env, capture_output=True, text=True, timeout=5)
    assert result.returncode == 1 and "READECK_PASSWORD" in result.stderr
    assert value not in result.stderr and "not found" not in result.stderr


@pytest.mark.parametrize("present", ["config.toml", "data/db.sqlite3"])
def test_partial_restore_stops_before_reinitialization(tmp_path, compose_env, present):
    path = tmp_path / present
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("existing operator state")
    service = json.loads(render(tmp_path, compose_env).stdout)["services"]["readeck"]
    result = subprocess.run(["/bin/bash", "-euc", service["command"][0].replace("$$", "$")],
                            cwd=tmp_path, env=compose_env, capture_output=True, text=True, timeout=5)
    assert result.returncode == 1 and "pair is incomplete" in result.stderr
    assert path.read_text() == "existing operator state"
    assert "not found" not in result.stderr


@pytest.mark.skipif(os.getenv("ODS_TEST_READECK") != "1", reason="Opt-in native reading library")
def test_live_article_capture_exports_tokens_rotation_and_restore(tmp_path, compose_env):
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        pytest.skip("Host-bind qualification prepares isolated UID 1000 storage")
    project = "ods-q40-readeck-" + uuid.uuid4().hex[:12]
    plan = json.loads(render(tmp_path, compose_env).stdout)
    service = plan["services"]["readeck"]
    service.update(container_name=project, restart="no")
    service["ports"][0]["published"] = "0"
    service["labels"] = {"io.ods.quality40.validation": "true"}
    plan["networks"] = {"ods-network": {"name": project + "-network"}}
    data = tmp_path / "data/readeck"
    data.mkdir(parents=True)
    os.chown(data, 1000, 1000)
    source = tmp_path / "article"
    source.mkdir()
    source.chmod(0o755)
    paragraph = ("A local reading experiment preserves the original text after its source is gone. "
                 "The archive must retain enough context to understand the evidence. "
                 "This fixture describes an isolated system and contains no user data. ")
    article = '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Local research note</title></head><body><article><h1>Local research note</h1><p>Ghi chú riêng tư: 你好</p>' + ("<p>" + paragraph + "</p>") * 12 + "</article></body></html>"
    (source / "essay.html").write_text(article)
    (source / "essay.html").chmod(0o644)
    # The exact Readeck image includes BusyBox httpd; no extra test image is needed.
    plan["services"]["article"] = {
        "image": service["image"], "entrypoint": ["/bin/busybox", "httpd", "-f", "-p", "8888", "-h", "/www"],
        "user": "1000:1000", "read_only": True, "restart": "no",
        "volumes": [{"type": "bind", "source": str(source), "target": "/www", "read_only": True}],
        "networks": ["ods-network"], "cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true"],
    }
    config = tmp_path / "isolated.json"
    config.write_text(json.dumps(plan))
    config.chmod(0o600)
    command = ["docker", "compose", "-p", project, "-f", str(config)]
    password = compose_env["READECK_PASSWORD"]
    new_seed, rotated = [secrets.token_hex(24) for _ in range(2)]
    token = ""

    def run(*args):
        result = subprocess.run(args, env=compose_env, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    def base_url():
        binding = json.loads(run("docker", "inspect", project))[0]["NetworkSettings"]["Ports"]["8000/tcp"][0]
        assert binding["HostIp"] == "127.0.0.1"
        return "http://127.0.0.1:" + binding["HostPort"]

    jar = CookieJar()
    browser = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(jar))
    plain = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(base, method, path, payload=None, expected=200, session=False, credential=None, form=False, headers=None):
        headers = dict(headers or {})
        headers.setdefault("Origin", base)
        if credential:
            headers["Authorization"] = "Bearer " + credential
        if payload is not None:
            if form:
                payload = urllib.parse.urlencode(payload, doseq=True).encode()
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            else:
                payload = json.dumps(payload).encode()
                headers["Content-Type"] = "application/json"
        req = urllib.request.Request(base + path, data=payload, method=method, headers=headers)
        try:
            response = (browser if session else plain).open(req, timeout=20)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            body = response.read()
            assert response.status == expected, f"{method} {path}: {response.status}: {body[:500]!r}"
            return body, response.headers, response.geturl()

    def login(base, secret, expected=200):
        return request(base, "POST", "/login", {"username": "ods", "password": secret},
                       expected=expected, session=True, form=True)

    class Inputs(HTMLParser):
        def __init__(self):
            super().__init__()
            self.values = []

        def handle_starttag(self, tag, attrs):
            values = dict(attrs)
            if tag == "input" and values.get("data-clipboard-target") == "content":
                self.values.append(values["value"])

    try:
        run(*command, "up", "-d", "--wait", "--wait-timeout", "60")
        base = base_url()
        request(base, "GET", "/login")
        request(base, "GET", "/api/bookmarks", expected=401)
        request(base, "POST", "/api/bookmarks", {"url": "http://article:8888/essay.html"}, expected=401)
        login(base, "incorrect", expected=401)
        login(base, password)
        request(base, "GET", "/api/profile", session=True)
        request(base, "POST", "/profile/tokens", {}, expected=403, session=True, form=True,
                headers={"Origin": "https://foreign.invalid", "Sec-Fetch-Site": "cross-site"})
        page, _, token_url = request(base, "POST", "/profile/tokens", {}, session=True, form=True)
        inputs = Inputs()
        inputs.feed(page.decode())
        token = inputs.values[0]
        assert token and not token.startswith("Authorization:")
        token_path = urllib.parse.urlsplit(token_url).path
        token_uid = token_path.rsplit("/", 1)[1]
        request(base, "GET", "/api/profile", credential=token)
        _, headers, _ = request(base, "POST", "/api/bookmarks", {
            "url": "http://article:8888/essay.html", "labels": ["local", "validation"],
        }, expected=202, credential=token)
        bookmark_id = headers["bookmark-id"]
        bookmark_path = "/api/bookmarks/" + bookmark_id
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            saved = json.loads(request(base, "GET", bookmark_path, credential=token)[0])
            if saved["loaded"]:
                break
            time.sleep(0.25)
        assert saved["loaded"] and saved["state"] == 0, saved
        assert saved["title"] == "Local research note"
        assert set(saved["labels"]) == {"local", "validation"}
        html = request(base, "GET", bookmark_path + "/article", credential=token)[0]
        assert "Ghi chú riêng tư: 你好".encode() in html
        markdown = request(base, "GET", bookmark_path + "/article.md", credential=token)[0]
        assert paragraph.strip().encode() in markdown
        epub = request(base, "GET", bookmark_path + "/article.epub", credential=token)[0]
        with zipfile.ZipFile(io.BytesIO(epub)) as archive:
            assert archive.read("mimetype") == b"application/epub+zip"
            container = ElementTree.fromstring(archive.read("META-INF/container.xml"))
            package = container.find(".//{*}rootfile").attrib["full-path"]
            manifest = ElementTree.fromstring(archive.read(package))
            page_names = [str(PurePosixPath(package).parent / item.attrib["href"])
                          for item in manifest.findall(".//{*}item")
                          if item.attrib["media-type"] == "application/xhtml+xml"]
            assert page_names
            text = " ".join(" ".join(ElementTree.fromstring(archive.read(name)).itertext())
                            for name in page_names)
            assert paragraph.strip() in " ".join(text.split())
        hits = json.loads(request(base, "GET", "/api/bookmarks?search=isolated", credential=token)[0])
        assert any(hit["id"] == bookmark_id for hit in hits)
        request(base, "POST", token_path, {"application": "read-only fixture", "is_enabled": "1",
                "roles": ["bookmarks:read"]}, session=True, form=True)
        request(base, "GET", bookmark_path, credential=token)
        request(base, "POST", "/api/bookmarks", {"url": "http://article:8888/essay.html"}, expected=403, credential=token)
        # The fetched article must remain readable without contacting its source.
        run(*command, "stop", "article")
        service["environment"]["READECK_PASSWORD"] = new_seed
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "60", "readeck")
        base = base_url()
        jar.clear()
        login(base, new_seed, expected=401)
        login(base, password)
        old_cookie = "; ".join(c.name + "=" + c.value for c in jar)
        request(base, "POST", "/profile/password", {"action": "change", "current": password, "password": rotated},
                session=True, form=True)
        request(base, "GET", "/api/profile", headers={"Cookie": old_cookie}, expected=401)
        jar.clear()
        login(base, password, expected=401)
        login(base, rotated)
        # Password rotation changes session seeds; API tokens have a separate lifecycle.
        assert request(base, "GET", bookmark_path + "/article.md", credential=token)[0] == markdown
        run(*command, "stop", "--timeout", "30", "readeck")
        backup = tmp_path / "restored"
        shutil.copytree(data, backup)
        for path in [backup, *backup.rglob("*")]:
            os.chown(path, 1000, 1000)
        service["volumes"][0]["source"] = str(backup)
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "60", "readeck")
        base = base_url()
        jar.clear()
        login(base, password, expected=401)
        login(base, rotated)
        assert request(base, "GET", bookmark_path + "/article.md", credential=token)[0] == markdown
        request(base, "POST", "/api/bookmarks", {"url": "http://article:8888/essay.html"}, expected=403, credential=token)
        request(base, "DELETE", "/api/profile/tokens/" + token_uid, expected=204, session=True)
        request(base, "GET", bookmark_path, credential=token, expected=401)
        assert request(base, "GET", bookmark_path + "/article.md", session=True)[0] == markdown
    finally:
        try:
            logs = subprocess.run(["docker", "logs", project], capture_output=True, text=True, check=True, timeout=15)
            diagnostics = logs.stdout + logs.stderr
            values = (password, new_seed, rotated, token) if token else (password, new_seed, rotated)
            assert all(value not in diagnostics for value in values)
            print(diagnostics[-3000:])
        finally:
            run(*command, "down", "--volumes", "--timeout", "30")


@pytest.mark.skipif(os.getenv("ODS_TEST_READECK") != "1", reason="Opt-in native bootstrap recovery")
@pytest.mark.parametrize("broken", ["missing_account", "missing_config"])
def test_live_interrupted_bootstrap_requires_explicit_recovery(tmp_path, compose_env, broken):
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        pytest.skip("Host-bind qualification prepares isolated UID 1000 storage")
    project = "ods-q40-readeck-recovery-" + uuid.uuid4().hex[:12]
    plan = json.loads(render(tmp_path, compose_env).stdout)
    service = plan["services"]["readeck"]
    service.update(container_name=project, restart="no")
    service["ports"][0]["published"] = "0"
    plan["networks"] = {"ods-network": {"name": project + "-network"}}
    data = tmp_path / "data/readeck"
    data.mkdir(parents=True)
    os.chown(data, 1000, 1000)
    config = tmp_path / "isolated.json"
    config.write_text(json.dumps(plan))
    config.chmod(0o600)
    command = ["docker", "compose", "-p", project, "-f", str(config)]

    def run(*args, check=True):
        return subprocess.run(args, env=compose_env, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, check=check, timeout=120)

    native = [*command, "run", "--rm", "-T", "--no-deps", "--entrypoint", "/bin/readeck",
              "readeck", "user", "-config", "config.toml", "-user", "ods"]
    try:
        initial = run(*native, "-dry-run", "-json")
        assert json.loads(initial.stdout)["exists"] is False
        assert (data / "data/db.sqlite3").is_file()
        saved_config = (data / "config.toml").read_bytes()
        if broken == "missing_config":
            (data / "config.toml").unlink()
        result = run(*command, "run", "--rm", "-T", "--no-deps", "readeck", check=False)
        assert result.returncode == 1
        expected = "account is missing" if broken == "missing_account" else "pair is incomplete"
        assert expected in result.stderr
        assert compose_env["READECK_PASSWORD"] not in result.stdout + result.stderr
        # Recover the same state using native CLI, without deleting/reinitializing it.
        if broken == "missing_config":
            (data / "config.toml").write_bytes(saved_config)
            os.chown(data / "config.toml", 1000, 1000)
        run(*native, "-email", "ods@localhost.invalid", "-group", "admin", "-password", "env:READECK_PASSWORD")
        assert json.loads(run(*native, "-dry-run", "-json").stdout)["exists"] is True
        run(*command, "up", "-d", "--wait", "--wait-timeout", "60")
        binding = json.loads(run("docker", "inspect", project).stdout)[0]["NetworkSettings"]["Ports"]["8000/tcp"][0]
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open("http://127.0.0.1:" + binding["HostPort"] + "/login", timeout=10) as response:
            assert response.status == 200 and b"onboarding" not in response.geturl().encode()
    finally:
        run(*command, "down", "--volumes", "--timeout", "30")
