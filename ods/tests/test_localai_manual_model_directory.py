"""The manual model directory documented for operators must be discoverable."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import urllib.request
import uuid

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extensions/library/services/localai"


def documented_directory():
    guide = (EXTENSION / "README.md").read_text()
    match = re.search(r"Drop a YAML model config into `([^`]+)`", guide)
    assert match, "The first-model guide must identify a directory"
    return Path(match.group(1))


def test_documented_model_directory_is_mounted_at_the_model_loader_path():
    service = yaml.safe_load((EXTENSION / "compose.yaml").read_text())["services"]["localai"]
    environment = dict(item.split("=", 1) for item in service["environment"])
    mounts = dict(item.split(":", 1) for item in service["volumes"])
    assert mounts["./" + str(documented_directory())] == environment["MODELS_PATH"]


@pytest.mark.skipif(os.getenv("ODS_TEST_LOCALAI_MANUAL_MODEL") != "1", reason="Opt-in pinned LocalAI model discovery")
def test_real_image_discovers_the_documented_manual_model_after_replacement(tmp_path):
    project = "ods-q20-localai-" + uuid.uuid4().hex[:12]
    (tmp_path / "empty.env").write_text("")
    env = dict(os.environ, BIND_ADDRESS="127.0.0.1")
    command = ["docker", "compose", "--env-file", str(tmp_path / "empty.env"),
               "--project-directory", str(tmp_path), "-f", str(EXTENSION / "compose.yaml")]
    # Retain the normal service definition but isolate the unrelated llama-server
    # prerequisite; this check only observes LocalAI's own model inventory.
    recipe = yaml.safe_load((EXTENSION / "compose.yaml").read_text())
    recipe["services"]["localai"].pop("depends_on")
    source = tmp_path / "recipe.yaml"
    source.write_text(yaml.safe_dump(recipe))
    command[-1] = str(source)
    rendered = subprocess.run([*command, "config", "--format", "json"], env=env,
                              check=True, capture_output=True, text=True, timeout=30)
    plan = json.loads(rendered.stdout)
    service = plan["services"]["localai"]
    service.update(container_name=project, restart="no")
    service["ports"][0]["published"] = "0"
    service["environment"].update(LOCALAI_AUTOLOAD_GALLERIES="false", LOCALAI_AUTOLOAD_BACKEND_GALLERIES="false",
                                  LOCALAI_GALLERIES="[]", LOCALAI_BACKEND_GALLERIES="[]")
    plan["networks"] = {"ods-network": {"name": project + "-network"}}
    for mount in service["volumes"]:
        Path(mount["source"]).mkdir(parents=True)
    directory = tmp_path / documented_directory()
    directory.mkdir(parents=True, exist_ok=True)
    definition = directory / "ods-manual-fixture.yaml"
    # Listing a definition requires no inference backend, model weights or download.
    definition.write_text("name: ods-manual-fixture\nbackend: llama-cpp\nparameters:\n  model: operator-supplied.gguf\n")
    config = tmp_path / "isolated.json"
    config.write_text(json.dumps(plan))
    command = ["docker", "compose", "-p", project, "-f", str(config)]

    def run(*args):
        result = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=150)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    def inventory():
        binding = json.loads(run("docker", "inspect", project))[0]["NetworkSettings"]["Ports"]["8080/tcp"][0]
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open("http://127.0.0.1:" + binding["HostPort"] + "/v1/models", timeout=15) as response:
            assert response.status == 200
            return {model["id"] for model in json.load(response)["data"]}

    try:
        run(*command, "up", "-d", "--wait", "--wait-timeout", "120")
        assert "ods-manual-fixture" in inventory()
        run(*command, "down", "--volumes")
        run(*command, "up", "-d", "--wait", "--wait-timeout", "120")
        assert "ods-manual-fixture" in inventory()
        assert not (directory / "operator-supplied.gguf").exists()
    finally:
        print(run(*command, "logs", "--tail", "20"))
        run(*command, "down", "--volumes")
