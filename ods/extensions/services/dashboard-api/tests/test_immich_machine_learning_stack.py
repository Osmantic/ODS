"""Check the default Immich ML dependency and optionally exercise the real CPU image."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

import pytest


COMPOSE = Path(__file__).resolve().parents[4] / "extensions/library/services/immich/compose.yaml"
ML_SERVICE = "immich-machine-learning"


def compose_command(directory, *args):
    return ["docker", "compose", "--env-file", str(directory / "empty.env"),
            "--project-directory", str(directory), "-f", str(COMPOSE), *args]


@pytest.fixture
def compose_environment(tmp_path):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose is required for the rendered installation contract")
    (tmp_path / "empty.env").write_text("")
    environment = {key: value for key, value in os.environ.items() if not key.startswith("IMMICH_")}
    environment["IMMICH_DB_PASSWORD"] = "immich-ml-regression-password"
    return tmp_path, environment


@pytest.mark.parametrize("public_port", ["2283", "32283"])
def test_normal_immich_install_includes_default_ml_endpoint(compose_environment, public_port):
    directory, environment = compose_environment
    environment["IMMICH_PORT"] = public_port
    result = subprocess.run(
        compose_command(directory, "config", "--format", "json", "immich"),
        env=environment, capture_output=True, text=True, check=True, timeout=20,
    )
    services = json.loads(result.stdout)["services"]
    # The host agent runs `compose up -d immich`, so disconnected services do not start.
    assert ML_SERVICE in services, "The version's default ML URL has no installation dependency"
    app, worker = services["immich"], services[ML_SERVICE]
    assert ML_SERVICE in app["depends_on"]
    assert "ods-network" in worker["networks"]
    assert worker["image"].split("@", 1)[0].rsplit(":", 1)[1] == app["image"].rsplit(":", 1)[1]
    assert not worker.get("ports"), "ML is reached inside the Compose network"
    assert str(worker.get("environment", {}).get("IMMICH_PORT", 3003)) == "3003"
    assert any(port["published"] == public_port for port in app["ports"])
    assert any(volume["target"] == "/cache" and volume["source"] == str(directory / "data/immich/model-cache")
               for volume in worker["volumes"])


_INFERENCE_PROBE = r'''
import base64
import io
import json
import math
import struct
import requests
from PIL import Image

image = io.BytesIO()
Image.new("RGB", (128, 128), color="white").save(image, format="PNG")
requests_to_run = [
    ({"clip": {"textual": {"modelName": "ViT-B-32__openai"}}}, {"text": "a white square"}),
    ({"clip": {"visual": {"modelName": "ViT-B-32__openai"}}}, {}),
    ({"facial-recognition": {
        "detection": {"modelName": "buffalo_l", "options": {"minScore": 0.7}},
        "recognition": {"modelName": "buffalo_l"},
    }}, {}),
]
results = []
for entries, payload in requests_to_run:
    files = None if "text" in payload else {"image": ("blank.png", image.getvalue(), "image/png")}
    response = requests.post("http://127.0.0.1:3003/predict",
                             data={"entries": json.dumps(entries), **payload}, files=files, timeout=300)
    response.raise_for_status()
    value = response.json()
    if "clip" in value:
        vector = base64.b64decode(value["clip"])
        assert len(vector) == 512 * 4
        assert all(math.isfinite(number) for number in struct.unpack("<512f", vector))
        results.append({"embedding_dimensions": 512})
    else:
        assert value["facial-recognition"] == []
        assert (value["imageWidth"], value["imageHeight"]) == (128, 128)
        results.append({"blank_image_faces": 0})
print(json.dumps(results))
'''


@pytest.mark.skipif(os.getenv("ODS_TEST_IMMICH_ML") != "1", reason="Opt-in live CPU/model-download check")
def test_live_cpu_inference_and_offline_cache_after_recreation(compose_environment):
    directory, environment = compose_environment
    project = "ods-q40-ml-" + uuid.uuid4().hex[:12]
    container = project + "-worker"
    network = project + "-network"
    overlay = directory / "isolated.yaml"
    # Replace the cache at the same container target with a task-owned named volume.
    # Only the worker is started; no normal ODS names, ports, networks or data are used.
    overlay.write_text(f"""services:
  {ML_SERVICE}:
    container_name: {container}
    restart: "no"
    labels:
      io.ods.quality40.validation: "true"
    volumes:
      - model-cache:/cache
networks:
  ods-network:
    external: false
    name: {network}
volumes:
  model-cache:
    name: {project}-cache
""")
    command = compose_command(directory, "-p", project, "-f", str(overlay))

    def run(*args, **kwargs):
        result = subprocess.run(args, env=environment, capture_output=True, text=True,
                                stdin=subprocess.DEVNULL, timeout=1000, **kwargs)
        assert result.returncode == 0, result.stdout + result.stderr
        return result

    try:
        run(*command, "up", "-d", "--no-deps", "--wait", "--wait-timeout", "120", ML_SERVICE)
        first = run("docker", "exec", container, "python", "-c", _INFERENCE_PROBE)
        print("CPU inference:", first.stdout.strip())
        run(*command, "up", "-d", "--no-deps", "--force-recreate", "--wait", "--wait-timeout", "120", ML_SERVICE)
        # Docker exec still reaches loopback after external connectivity is removed.
        run("docker", "network", "disconnect", network, container)
        repeated = run("docker", "exec", container, "python", "-c", _INFERENCE_PROBE)
        print("Offline after recreation:", repeated.stdout.strip())
    finally:
        logs = subprocess.run(["docker", "logs", container], capture_output=True, text=True, timeout=20)
        print("Worker diagnostics:", logs.stdout, logs.stderr)
        run(*command, "down", "--volumes", "--timeout", "15")
