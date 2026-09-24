"""The installed RVC command must never pretend an ignored key enables auth."""

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
EXTENSION = ROOT / "extensions/library/services/rvc"
ERROR = ("RVC_API_KEY is unsupported by the pinned RVC image. Startup refused; "
         "use a separately authenticated proxy or an explicitly local unauthenticated configuration.")


@pytest.fixture
def render_plan(tmp_path):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI required")
    empty = tmp_path / "empty.env"
    empty.write_text("")

    def render(key):
        env = {name: value for name, value in os.environ.items()
               if name not in {"RVC_API_KEY", "RVC_PORT", "BIND_ADDRESS"}}
        if key is not None:
            env["RVC_API_KEY"] = key
        env["RVC_PORT"] = "17809"
        result = subprocess.run([
            "docker", "compose", "--env-file", str(empty), "--project-directory", str(tmp_path),
            "-f", str(EXTENSION / "compose.yaml"), "config", "--format", "json", "rvc",
        ], env=env, capture_output=True, text=True, check=True, timeout=30)
        return json.loads(result.stdout), env

    return render


@pytest.mark.parametrize("key", [None, "", "configured-test-key", " \n ", "quote'$key:\"with spaces"])
def test_rendered_startup_preserves_local_mode_and_rejects_ignored_keys(render_plan, tmp_path, key):
    plan, env = render_plan(key)
    service = plan["services"]["rvc"]
    assert service.get("entrypoint") is None
    assert service["ports"][0]["target"] == 7865
    assert service["ports"][0]["published"] == "17809"
    assert service["ports"][0]["host_ip"] == "127.0.0.1"
    assert {volume["target"] for volume in service["volumes"]} == {
        "/app/assets/weights", "/app/opt", "/app/dataset", "/app/logs"}
    native = tmp_path / "bin"
    native.mkdir()
    receipt = tmp_path / "python-argv"
    # Only the heavyweight Python server is controlled. Execute the installed
    # Compose command with the real Bash parser and its rendered environment.
    launcher = native / "python3"
    launcher.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$RVC_TEST_ARGV"\n')
    launcher.chmod(0o755)
    env.update(PATH=str(native), RVC_TEST_ARGV=str(receipt))
    # Compose JSON escapes dollars for another Compose read; direct exec needs
    # the single-dollar command that the actual container receives.
    command = [arg.replace("$$", "$") for arg in service["command"]]
    result = subprocess.run(command, env=env, cwd=tmp_path, capture_output=True, text=True, timeout=5)
    if key:
        assert result.returncode == 1
        assert result.stderr == ERROR + "\n" and result.stdout == ""
        assert not receipt.exists()
    else:
        assert result.returncode == 0, result.stderr
        assert receipt.read_text() == "infer-web.py\n"


def test_catalog_does_not_advertise_unsupported_authentication():
    manifest = yaml.safe_load((EXTENSION / "manifest.yaml").read_text())
    env = next(item for item in manifest["service"]["env_vars"] if item["key"] == "RVC_API_KEY")
    assert "Unsupported legacy setting" in env["description"]
    catalog = json.loads((ROOT / "config/extensions-catalog.json").read_text())
    entry = next(item for item in catalog["extensions"] if item["id"] == "rvc")
    assert next(item for item in entry["env_vars"] if item["key"] == "RVC_API_KEY")["description"] == env["description"]


@pytest.mark.skipif(os.getenv("ODS_TEST_RVC_AUTH") != "1", reason="Opt-in pinned RVC image startup")
@pytest.mark.parametrize("configured", [False, True])
def test_live_pinned_image_startup_and_http_access(render_plan, tmp_path, configured):
    key = secrets.token_hex(24) + "'$:[]\nsecond-line" if configured else ""
    plan, env = render_plan(key)
    service = plan["services"]["rvc"]
    project = "ods-q40-rvc-" + uuid.uuid4().hex[:12]
    service["container_name"] = project
    service["restart"] = "no"
    service["network_mode"] = "none"
    del service["networks"]
    del service["ports"]
    plan.pop("networks", None)
    service["labels"] = {"io.ods.quality40.validation": "true"}
    # No GPU/model qualification is claimed; this exercises the real CPU-side
    # application entrypoint and HTTP contract, with external networking off.
    service["deploy"]["resources"]["limits"] = {"cpus": "2.0", "memory": "4G"}
    for volume in service["volumes"]:
        Path(volume["source"]).mkdir(parents=True)
    config = tmp_path / "isolated.json"
    config.write_text(json.dumps(plan))
    config.chmod(0o600)
    command = ["docker", "compose", "-p", project, "-f", str(config)]

    def run(*args, timeout=120):
        result = subprocess.run(args, env=env, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=timeout)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    try:
        if configured:
            run(*command, "up", "-d")
            assert run("docker", "wait", project, timeout=45).strip() == "1"
            state = json.loads(run("docker", "inspect", project))[0]["State"]
            assert state["Status"] == "exited" and state["ExitCode"] == 1
            logs = subprocess.run(["docker", "logs", project], check=True, capture_output=True,
                                  text=True, timeout=20)
            assert ERROR in logs.stdout + logs.stderr
            assert key not in logs.stdout + logs.stderr
            assert "Use Language:" not in logs.stdout + logs.stderr
        else:
            run(*command, "up", "-d", "--wait", "--wait-timeout", "90")
            probe = (
                "import json,urllib.request; "
                "r=urllib.request.urlopen('http://127.0.0.1:7865/'); "
                "assert r.status==200; assert b'gradio' in r.read().lower(); "
                "r=urllib.request.urlopen('http://127.0.0.1:7865/config'); "
                "assert r.status==200; data=json.load(r); "
                "assert data['version']=='4.23.0'; print('Original Gradio UI and config remain reachable without authentication')"
            )
            print(run("docker", "exec", project, "python3", "-c", probe).strip())
    finally:
        run(*command, "down", "--volumes", "--timeout", "30")
