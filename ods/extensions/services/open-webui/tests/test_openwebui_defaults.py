"""Check the environment Compose actually sends to the pinned Open WebUI."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

BASE = Path(__file__).resolve().parents[4] / "docker-compose.base.yml"


def render(tmp_path, override=None):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI is required; no daemon is used")
    env_file = tmp_path / "empty.env"
    env_file.write_text("", encoding="utf-8")
    command = ["docker", "compose", "--env-file", str(env_file), "-f", str(BASE)]
    if override is not None:
        extra = tmp_path / "owner-override.json"
        extra.write_text(json.dumps(override), encoding="utf-8")
        command += ["-f", str(extra)]
    env = {key: value for key, value in os.environ.items() if not key.startswith("COMPOSE_")}
    env["WEBUI_SECRET"] = "synthetic-compose-only-test-secret"
    result = subprocess.run(command + ["config", "--format", "json"], env=env,
                            capture_output=True, text=True, timeout=30, check=True)
    return json.loads(result.stdout)["services"]["open-webui"]["environment"]


def test_plain_chat_defaults_to_legacy_without_disabling_builtin_capabilities(tmp_path):
    environment = render(tmp_path)
    assert json.loads(environment["DEFAULT_MODEL_PARAMS"]) == {"function_calling": "legacy"}
    assert "DEFAULT_MODEL_METADATA" not in environment
    assert environment["ENABLE_PERSISTENT_CONFIG"] == "false"


def test_explicit_owner_compose_default_still_overrides_compatibility_default(tmp_path):
    params = {"function_calling": "native", "temperature": 0.25}
    environment = render(tmp_path, {"services": {"open-webui": {
        "environment": {"DEFAULT_MODEL_PARAMS": json.dumps(params)}
    }}})
    assert json.loads(environment["DEFAULT_MODEL_PARAMS"]) == params
