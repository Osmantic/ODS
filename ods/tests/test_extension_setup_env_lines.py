"""Run deployable credential hooks and parse their output with Docker Compose."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

LIBRARY = Path(__file__).resolve().parents[1] / "extensions/library/services"
KEYS = {
    "flowise": ["FLOWISE_USERNAME", "FLOWISE_PASSWORD"],
    "weaviate": ["WEAVIATE_API_KEY"],
    "anythingllm": ["ANYTHINGLLM_JWT_SECRET", "ANYTHINGLLM_AUTH_TOKEN"],
    "librechat": ["JWT_SECRET", "JWT_REFRESH_SECRET", "LIBRECHAT_MONGO_PASSWORD",
                  "LIBRECHAT_MEILI_KEY", "CREDS_KEY", "CREDS_IV"],
    "frigate": ["FRIGATE_RTSP_PASSWORD"],
    "open-interpreter": ["OPEN_INTERPRETER_API_KEY"],
    "paperless-ngx": ["PAPERLESS_SECRET_KEY"],
    "jupyter": ["JUPYTER_TOKEN"],
}


@pytest.mark.parametrize("service", KEYS)
@pytest.mark.parametrize("ending", ["", "\n", "\r\n"])
@pytest.mark.parametrize("quoted", [False, True])
def test_setup_preserves_last_assignment_and_generates_separate_keys(tmp_path, service, ending, quoted):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI required")
    installation = tmp_path / "install with spaces"
    installation.mkdir()
    env_file = installation / ".env"
    expected = "operator value" if quoted else "operator-value"
    original = ('KEEP="operator value"' if quoted else 'KEEP=operator-value') + ending
    env_file.write_bytes(original.encode())
    manifest = yaml.safe_load((LIBRARY / service / "manifest.yaml").read_text())
    hook = LIBRARY / service / manifest["service"]["setup_hook"]
    env = {key: value for key, value in os.environ.items()
           if key not in {"KEEP", *[name for keys in KEYS.values() for name in keys]}}

    def setup():
        subprocess.run(["sh", str(hook), str(installation), "cpu"],
                       env=env, check=True, capture_output=True, text=True, timeout=30)

    setup()
    generated = env_file.read_bytes()
    assert generated.startswith(original.encode())
    # Parse the actual recipe: its required-variable interpolation must succeed.
    command = ["docker", "compose", "--env-file", str(env_file),
               "--project-directory", str(installation), "-f",
               str(LIBRARY / service / "compose.yaml")]
    if service == "anythingllm":
        command += ["-f", str(LIBRARY / "ollama/compose.yaml")]
    result = subprocess.run([*command, "config", "--format", "json"], env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["services"]

    # Compose's public environment view also proves the preceding value was not
    # extended and every generated key remains available to the next consumer.
    result = subprocess.run([*command, "config", "--environment"], env=env,
                            capture_output=True, text=True, check=True, timeout=30)
    values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    assert values["KEEP"] == expected
    for key in KEYS[service]:
        assert values[key]
    setup()
    assert env_file.read_bytes() == generated, "Repeated setup must not rotate existing credentials"
