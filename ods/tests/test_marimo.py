"""Installed marimo definition, real authentication, execution and recreation."""

import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extensions/library/services/marimo"
NOTEBOOK = '''import marimo
__generated_with = "0.24.2"
app = marimo.App()

@app.cell
def _(source_value):
    from pathlib import Path
    answer = source_value * 2
    Path("/workspace/result.txt").write_text(str(answer))
    return

@app.cell
def _():
    source_value = 21
    return (source_value,)

if __name__ == "__main__":
    app.run()
'''


@pytest.fixture
def installation(tmp_path):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI required")
    installed = tmp_path / "data/user-extensions/marimo"
    shutil.copytree(EXTENSION, installed)
    (tmp_path / "empty.env").write_text("")
    env = {key: value for key, value in os.environ.items() if not key.startswith("MARIMO_")}
    env.pop("BIND_ADDRESS", None)
    env["MARIMO_PASSWORD"] = secrets.token_hex(24)
    return tmp_path, installed, env


def render(installation):
    directory, installed, env = installation
    return subprocess.run([
        "docker", "compose", "--env-file", str(directory / "empty.env"),
        "--project-directory", str(directory), "-f", str(installed / "compose.yaml"),
        "config", "--format", "json",
    ], env=env, capture_output=True, text=True, timeout=30)


def test_installation_uses_scoped_writable_data_and_private_editor(installation):
    directory, installed, env = installation
    env["MARIMO_PORT"] = "12718"
    result = render(installation)
    assert result.returncode == 0, result.stderr
    service = json.loads(result.stdout)["services"]["marimo"]
    assert service["image"] == "ods-marimo:0.24.2-r1"
    assert service["pull_policy"] == "never"
    assert "build" not in service
    assert service["user"] == "1000:1000"
    assert service["read_only"] is True
    assert service["ports"][0]["published"] == "12718"
    assert service["ports"][0]["host_ip"] == "127.0.0.1"
    mounts = {mount["target"]: mount["source"] for mount in service["volumes"]}
    assert mounts == {"/workspace": str(directory / "data/marimo/workspace"),
                      "/home/appuser": str(directory / "data/marimo/home")}
    catalog = json.loads((ROOT / "config/extensions-catalog.json").read_text())
    entry = next(item for item in catalog["extensions"] if item["id"] == "marimo")
    assert entry["features"][0]["launch"]["service"] == "marimo"
    assert entry["features"][0]["requirements"]["vram_gb"] == 0


@pytest.mark.parametrize("empty", [True, False])
def test_missing_editor_password_blocks_compose(installation, empty):
    env = installation[2]
    if empty:
        env["MARIMO_PASSWORD"] = ""
    else:
        del env["MARIMO_PASSWORD"]
    result = render(installation)
    assert result.returncode != 0
    assert "MARIMO_PASSWORD" in result.stderr


@pytest.mark.parametrize("exit_code", [0, 23])
def test_setup_builds_the_declared_image_and_propagates_failure(installation, exit_code):
    directory, installed, env = installation
    expected_image = json.loads(render(installation).stdout)["services"]["marimo"]["image"]
    tools = directory / "fake-bin"
    tools.mkdir()
    record = directory / "docker-call.json"
    docker = tools / "docker"
    docker.write_text(
        f"#!{sys.executable}\nimport json, pathlib, sys\n"
        f"pathlib.Path({str(record)!r}).write_text(json.dumps(sys.argv[1:]))\n"
        f"sys.exit({exit_code})\n"
    )
    docker.chmod(0o755)
    env["PATH"] = str(tools) + os.pathsep + env["PATH"]
    result = subprocess.run(["bash", str(installed / "setup.sh"), str(directory), "cpu"],
                            cwd=directory, env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == exit_code, result.stderr
    assert json.loads(record.read_text()) == [
        "build", "--tag", expected_image, "--file", str(installed / "Dockerfile"), str(installed),
    ]


def test_installed_editor_is_accepted_by_the_real_stack_resolver(installation):
    directory, installed, env = installation
    (directory / "docker-compose.base.yml").write_text("services:\n  fixture-core:\n    image: busybox\n")
    result = subprocess.run(["bash", str(ROOT / "scripts/resolve-compose-stack.sh"),
                             "--script-dir", str(directory), "--gpu-backend", "cpu"],
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "data/user-extensions/marimo/compose.yaml" in result.stdout


@pytest.mark.skipif(os.getenv("ODS_TEST_MARIMO") != "1", reason="Opt-in actual editor build and execution")
def test_live_authentication_notebook_execution_and_recreation(installation):
    directory, installed, env = installation
    project = "ods-q40-marimo-" + uuid.uuid4().hex[:12]
    plan = json.loads(render(installation).stdout)
    service = plan["services"]["marimo"]
    # Build the same Dockerfile under an owned tag; never overwrite an
    # operator's local deployment image during the runtime test.
    image = project + ":test"
    service["image"] = image
    service.update(container_name=project, restart="no", network_mode="none")
    service.pop("networks")
    service.pop("ports")
    plan.pop("networks")
    plan["volumes"] = {name: {"name": project + "-" + name} for name in ("workspace", "home")}
    for mount in service["volumes"]:
        mount.update(type="volume", source="workspace" if mount["target"] == "/workspace" else "home")
        mount.pop("bind", None)
    config = directory / "isolated.json"
    config.write_text(json.dumps(plan))
    config.chmod(0o600)
    command = ["docker", "compose", "-p", project, "-f", str(config)]

    def run(*args, check=True, environment=env, timeout=180):
        result = subprocess.run(args, env=environment, capture_output=True, text=True,
                                stdin=subprocess.DEVNULL, timeout=timeout)
        if check:
            assert result.returncode == 0, result.stdout + result.stderr
        return result

    probe = (ROOT / "tests/fixtures/marimo-http.py").read_text()
    try:
        run("docker", "build", "--tag", image, "--file",
            str(installed / "Dockerfile"), str(installed), timeout=480)
        # The host install path runs pull after the setup hook; never contact
        # a registry for this deliberately local build artifact.
        run(*command, "pull", "marimo")
        for volume in plan["volumes"].values():
            run("docker", "volume", "create", volume["name"])
        run("docker", "run", "--rm", "--network", "none", "--user", "0",
            "--entrypoint", "sh", "-v", project + "-workspace:/workspace",
            "-v", project + "-home:/home/appuser", image,
            "-c", "chown 1000:1000 /workspace /home/appuser")
        run(*command, "up", "-d", "--wait", "--wait-timeout", "120")
        version = run("docker", "exec", project, "python", "-c", "import marimo; print(marimo.__version__)")
        assert version.stdout.strip() == "0.24.2"
        run("docker", "exec", project, "python", "-c",
            "from pathlib import Path; Path('/workspace/analysis.py').write_text(" + repr(NOTEBOOK) + ")")
        run("docker", "exec", project, "marimo", "--quiet", "export", "html",
            "/workspace/analysis.py", "-o", "/workspace/report.html")
        result = run("docker", "exec", project, "cat", "/workspace/result.txt")
        assert result.stdout == "42"
        run("docker", "exec", project, "python", "-c", probe)
        old = env["MARIMO_PASSWORD"]
        initial_logs = run("docker", "logs", project)
        assert old not in initial_logs.stdout + initial_logs.stderr
        env["ODS_OLD_MARIMO_PASSWORD"] = old
        env["MARIMO_PASSWORD"] = secrets.token_hex(24)
        service["environment"]["MARIMO_PASSWORD"] = env["MARIMO_PASSWORD"]
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120")
        run("docker", "exec", "-e", "ODS_OLD_MARIMO_PASSWORD", project, "python", "-c", probe)
        result = run("docker", "exec", project, "cat", "/workspace/result.txt")
        assert result.stdout == "42"
        run("docker", "exec", project, "test", "-s", "/workspace/report.html")
        logs = run("docker", "logs", project)
        assert old not in logs.stdout + logs.stderr
        assert env["MARIMO_PASSWORD"] not in logs.stdout + logs.stderr
        print("Real marimo 0.24.2 HTTP auth, dependency execution, persisted files and password rotation passed")
    finally:
        diagnostics = run("docker", "logs", "--tail", "15", project, check=False)
        print("Editor diagnostics:", diagnostics.stdout, diagnostics.stderr)
        run(*command, "down", "--volumes", "--timeout", "30")
        run("docker", "image", "rm", image)
