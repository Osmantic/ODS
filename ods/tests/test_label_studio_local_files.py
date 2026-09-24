"""The shipped local-file API must be limited to the selected dataset mount."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "extensions/library/services/label-studio/compose.yaml"


@pytest.fixture
def rendered(tmp_path):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI required")
    (tmp_path / "empty.env").write_text("")
    result = subprocess.run([
        "docker", "compose", "--env-file", str(tmp_path / "empty.env"),
        "--project-directory", str(tmp_path), "-f", str(COMPOSE), "config", "--format", "json",
    ], capture_output=True, text=True, check=True, timeout=30)
    return json.loads(result.stdout)


def test_local_files_root_is_the_existing_dataset_mount(rendered, tmp_path):
    service = rendered["services"]["label-studio"]
    root = service["environment"].get("LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT", "/")
    assert root == "/label-studio/upload"
    mount = next(item for item in service["volumes"] if item["target"] == root)
    assert mount["source"] == str(tmp_path / "upload")
    assert service["environment"]["LABEL_STUDIO_LOCAL_FILES_SERVING_ENABLED"] == "true"


@pytest.mark.skipif(os.getenv("ODS_TEST_LABEL_STUDIO") != "1", reason="Opt-in actual Label Studio API")
def test_live_local_storage_api_rejects_outside_paths(rendered, tmp_path):
    project = "ods-q40-label-" + uuid.uuid4().hex[:12]
    service = rendered["services"]["label-studio"]
    service["container_name"] = project
    service["restart"] = "no"
    service.pop("ports", None)
    service.pop("healthcheck", None)  # One-shot API request process, not a web daemon.
    service["network_mode"] = "none"
    service.pop("networks", None)
    rendered.pop("networks", None)
    volumes = {}
    for i, mount in enumerate(service["volumes"]):
        key = f"validation-{i}"
        mount.update(type="volume", source=key)
        mount.pop("bind", None)
        volumes[key] = {"name": project + f"-data-{i}"}
    rendered["volumes"] = volumes
    config = tmp_path / "isolated.json"
    config.write_text(json.dumps(rendered))
    command = ["docker", "compose", "-p", project, "-f", str(config)]

    def run(*args):
        result = subprocess.run(args, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=420)
        print(result.stdout, result.stderr)
        assert result.returncode == 0
        return result

    upload_key = next(m["source"] for m in service["volumes"] if m["target"] == "/label-studio/upload")
    upload_name = volumes[upload_key]["name"]
    try:
        run("docker", "volume", "create", upload_name)
        run("docker", "run", "--rm", "--network", "none", "--user", "0", "--entrypoint", "/bin/sh",
            "-v", upload_name + ":/fixture", service["image"], "-c",
            "mkdir -p /fixture/fixture-dataset && printf 'synthetic selected dataset\\n' > /fixture/fixture-dataset/sample.txt")
        probe = (ROOT / "tests/fixtures/label-studio-local-files.py").read_text()
        run(*command, "run", "--rm", "--no-deps", "--entrypoint", "python", "label-studio", "-c", probe)
    finally:
        run(*command, "down", "--volumes", "--timeout", "20")
