"""Validate the installed compose plan and opt-in pinned Gitea lifecycle."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

import httpx
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "extensions/library/services/gitea"


def run(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=150)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker Compose CLI required")
def test_rootless_compose_persists_both_state_directories(tmp_path):
    plan = json.loads(run("docker", "compose", "--project-directory", str(tmp_path),
                          "-f", str(SERVICE / "compose.yaml"), "config", "--format", "json"))
    mounts = {item["target"]: item for item in plan["services"]["gitea"]["volumes"]}
    for target in ("/var/lib/gitea", "/etc/gitea"):
        assert mounts[target]["type"] == "bind"
        assert mounts[target].get("read_only", False) is False


@pytest.mark.skipif(os.environ.get("ODS_TEST_GITEA_DOCKER") != "1", reason="Opt-in Docker lifecycle")
@pytest.mark.parametrize("migrate_legacy", [False, True], ids=["fresh", "legacy-config-copy"])
def test_private_repository_and_configuration_survive_down_up(tmp_path, migrate_legacy):
    name = f"ods-gitea-state-{uuid.uuid4().hex[:10]}"
    installed = tmp_path / "extensions/services/gitea"
    shutil.copytree(SERVICE, installed)
    compose_file = installed / "compose.yaml"
    updated_recipe = compose_file.read_text()
    if migrate_legacy:
        # Reproduce the previous recipe's anonymous /etc/gitea image volume.
        legacy = yaml.safe_load(updated_recipe)
        image = legacy["services"].pop("gitea-init")["image"]
        legacy["services"]["gitea"].pop("depends_on")
        legacy["services"]["gitea"]["volumes"] = ["./data/gitea:/var/lib/gitea:rw"]
        compose_file.write_text(yaml.safe_dump(legacy))
        data = tmp_path / "data/gitea"
        data.mkdir(parents=True)
        run("docker", "run", "--rm", "--network", "none", "--user", "0:0", "--entrypoint", "chown",
            "--mount", f"type=bind,source={data},target=/var/lib/gitea", image, "1000:1000", "/var/lib/gitea")
    overlay = tmp_path / "isolation.yaml"
    overlay.write_text(f'''services:
  gitea:
    container_name: {name}
    ports: !override ["127.0.0.1:0:3000"]
networks:
  ods-network:
    name: {name}
''')
    command = ["docker", "compose", "--project-name", name, "--project-directory", str(tmp_path),
               "-f", str(installed / "compose.yaml"), "-f", str(overlay)]
    run("docker", "network", "create", name)
    anonymous_volumes = set()

    def start():
        started = subprocess.run([*command, "up", "-d", "--wait", "--wait-timeout", "60"],
                                 capture_output=True, text=True, timeout=150)
        assert started.returncode == 0, started.stderr + "\n" + run("docker", "logs", name)
        mounts = json.loads(run("docker", "inspect", name))[0]["Mounts"]
        anonymous_volumes.update(item["Name"] for item in mounts if item["Type"] == "volume")
        return "http://" + run(*command, "port", "gitea", "3000")

    try:
        # The fresh case has no host chown or pre-created data directory.
        url = start()
        password = "fixture-" + uuid.uuid4().hex
        run("docker", "exec", name, "gitea", "admin", "user", "create", "--username", "owner",
            "--password", password, "--email", "owner@example.test", "--admin",
            "--must-change-password=false")
        config_hash = run("docker", "exec", name, "sha256sum", "/etc/gitea/app.ini")
        with httpx.Client(base_url=url, timeout=10) as client:
            assert client.get("/api/v1/user").status_code == 403
            created = client.post("/api/v1/user/repos", auth=("owner", password),
                                  json={"name": "private-receipt", "private": True, "auto_init": True})
            assert created.status_code == 201, created.text
            repository_id = created.json()["id"]
            assert client.get("/api/v1/repos/owner/private-receipt").status_code == 403
        if migrate_legacy:
            run("docker", "stop", name)
            destination = tmp_path / "data/gitea-config"
            destination.mkdir()
            run("docker", "cp", "-a", f"{name}:/etc/gitea/.", str(destination))
            compose_file.write_text(updated_recipe)
            start()
            assert run("docker", "exec", name, "sha256sum", "/etc/gitea/app.ini") == config_hash
        run(*command, "down", "--timeout", "10")
        url = start()
        assert run("docker", "exec", name, "sha256sum", "/etc/gitea/app.ini") == config_hash
        with httpx.Client(base_url=url, timeout=10) as client:
            restored = client.get("/api/v1/repos/owner/private-receipt", auth=("owner", password))
            assert restored.status_code == 200, restored.text
            assert restored.json()["id"] == repository_id
            assert restored.json()["private"] is True
            commits = client.get("/api/v1/repos/owner/private-receipt/commits", auth=("owner", password))
            assert commits.status_code == 200 and len(commits.json()) == 1
            assert client.get("/api/v1/repos/owner/private-receipt").status_code == 403
    finally:
        run(*command, "down", "--volumes", "--timeout", "10")
        for volume in anonymous_volumes:
            if run("docker", "volume", "ls", "--quiet", "--filter", f"name=^{volume}$"):
                run("docker", "volume", "rm", volume)
        run("docker", "network", "rm", name)
