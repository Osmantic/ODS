"""Public origin follows the published listener unless explicitly overridden."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "extensions/library/services/baserow/compose.yaml"


def rendered(tmp_path, overrides):
    env = {key: value for key, value in os.environ.items() if not key.startswith("BASEROW_")}
    result = subprocess.run(["docker", "compose", "--project-directory", str(tmp_path), "-f", str(COMPOSE),
                             "config", "--format", "json"], env={**env, **overrides},
                            capture_output=True, text=True, timeout=30, check=True)
    return json.loads(result.stdout)["services"]["baserow"]


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker Compose CLI required")
@pytest.mark.parametrize("overrides,port,origin", [
    ({}, "3007", "http://localhost:3007"),
    ({"BASEROW_PORT": "8307"}, "8307", "http://localhost:8307"),
    ({"BASEROW_PORT": "8307", "BASEROW_PUBLIC_URL": "https://tables.example.test"}, "8307", "https://tables.example.test"),
    ({"BASEROW_PORT": "", "BASEROW_PUBLIC_URL": ""}, "3007", "http://localhost:3007"),
])
def test_compose_public_origin_precedence(tmp_path, overrides, port, origin):
    service = rendered(tmp_path, overrides)
    assert service["ports"][0]["published"] == port
    assert service["ports"][0]["target"] == 80
    assert service["environment"]["BASEROW_PUBLIC_URL"] == origin


@pytest.mark.skipif(os.environ.get("ODS_TEST_BASEROW_SETTINGS") != "1", reason="Opt-in shipped Baserow image")
@pytest.mark.parametrize("overrides,expected", [
    ({"BASEROW_PORT": "8307"}, "http://localhost:8307"),
    ({"BASEROW_PORT": "8307", "BASEROW_PUBLIC_URL": "https://tables.example.test"}, "https://tables.example.test"),
])
def test_shipped_django_settings_consume_the_rendered_origin(tmp_path, overrides, expected):
    service = rendered(tmp_path, overrides)
    name = "ods-baserow-settings-" + uuid.uuid4().hex[:10]
    command = ["docker", "run", "--rm", "--name", name, "--network", "none",
               "--entrypoint", "/baserow/venv/bin/python"]
    for key, value in service["environment"].items():
        command.extend(["-e", key + "=" + value.replace("$$", "$")])
    command.extend([service["image"], "-c",
                    "import json; from django.conf import settings; print(json.dumps([settings.PUBLIC_BACKEND_URL, settings.PUBLIC_WEB_FRONTEND_URL]))"])
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=90)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == [expected, expected]
    finally:
        remaining = subprocess.run(["docker", "ps", "-aq", "--filter", f"name=^/{name}$"],
                                   capture_output=True, text=True, timeout=20, check=True)
        if remaining.stdout.strip():
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True, timeout=30, check=True)
