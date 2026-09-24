"""Exercise both authenticated HTTP routes and their real subprocess configuration."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

SERVER = Path(__file__).resolve().parents[1] / "extensions/library/services/open-interpreter/server.py"


@pytest.fixture
def interpreter_client(tmp_path, monkeypatch):
    # Replace only the heavyweight interpreter package. The HTTP handlers,
    # generated runner scripts, stdin JSON and subprocess execution are real.
    package = tmp_path / "interpreter"
    package.mkdir()
    (package / "__init__.py").write_text(textwrap.dedent('''
        from types import SimpleNamespace
        class Interpreter:
            llm = SimpleNamespace()
            def chat(self, message, stream):
                return [{"message":message, "base":self.llm.api_base,
                         "model":self.llm.model, "key":self.llm.api_key,
                         "auto_run":self.auto_run}]
        interpreter = Interpreter()
    '''))
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("OPEN_INTERPRETER_API_KEY", "boundary-client-key")
    monkeypatch.setenv("OPEN_INTERPRETER_AUTO_RUN", "false")
    def load(base, api_path="/v1", model="openai/local-model", key="boundary-model-key"):
        monkeypatch.setenv("LLM_API_URL", base)
        monkeypatch.setenv("LLM_API_BASE_PATH", api_path)
        monkeypatch.setenv("OPEN_INTERPRETER_MODEL", model)
        monkeypatch.setenv("OPEN_INTERPRETER_LLM_API_KEY", key)
        spec = importlib.util.spec_from_file_location("interpreter_boundary_server", SERVER)
        module = importlib.util.module_from_spec(spec)
        # The wrapper's existing unused /app/data mkdir belongs to container
        # startup; it must not create host directories in this HTTP fixture.
        with patch.object(Path, "mkdir"):
            spec.loader.exec_module(module)
        return TestClient(module.app)
    return load


@pytest.mark.parametrize("route", ["/chat", "/chat/stream"])
@pytest.mark.parametrize(("base", "api_path", "expected"), [
    ("http://llama-server:8080", "/v1", "http://llama-server:8080/v1"),
    ("http://host.docker.internal:8080/", "/v1", "http://host.docker.internal:8080/v1"),
    ("http://host.docker.internal:8000", "/api/v1", "http://host.docker.internal:8000/api/v1"),
    ("http://gateway:4000/v1/", "/v1", "http://gateway:4000/v1"),
    ("http://gateway:4000/custom/v1", "/api/v1", "http://gateway:4000/custom/v1"),
])
def test_runner_receives_complete_openai_connection(interpreter_client, route, base, api_path, expected):
    with interpreter_client(base, api_path) as client:
        response = client.post(route, headers={"Authorization":"Bearer boundary-client-key"},
                               json={"message":"A literal $HOME and `date`", "stream":route.endswith("stream")})
    assert response.status_code == 200
    output = response.json()["output"] if route == "/chat" else response.text
    assert expected in output
    assert "openai/local-model" in output
    assert "boundary-model-key" in output
    assert "A literal $HOME and `date`" in output
    assert "'auto_run': False" in output


def test_connection_configuration_does_not_bypass_http_auth(interpreter_client):
    with interpreter_client("http://llama-server:8080") as client:
        for route in ("/chat", "/chat/stream"):
            response = client.post(route, json={"message":"hello"})
            assert response.status_code in (401, 403)


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker Compose is required")
def test_compose_passes_the_complete_provider_connection(interpreter_client, tmp_path):
    empty_env = tmp_path / ".env"
    empty_env.touch()
    env = {key:value for key,value in os.environ.items()
           if not key.startswith(("LLM_API_", "OPEN_INTERPRETER_"))}
    env.update(OPEN_INTERPRETER_API_KEY="boundary-client-key",
               OPEN_INTERPRETER_LLM_API_KEY="boundary-model-key",
               OPEN_INTERPRETER_MODEL="openai/local-model", LLM_API_BASE_PATH="/v1")
    rendered = subprocess.run(["docker", "compose", "--env-file", str(empty_env), "-f",
                               str(SERVER.with_name("compose.yaml")), "config", "--format", "json"],
                              env=env, capture_output=True, text=True, check=True, timeout=30)
    connection = json.loads(rendered.stdout)["services"]["open-interpreter"]["environment"]
    assert connection["LLM_API_URL"] == "http://llama-server:8080"
    with interpreter_client(connection["LLM_API_URL"], connection["LLM_API_BASE_PATH"],
                            connection["OPEN_INTERPRETER_MODEL"], connection["OPEN_INTERPRETER_LLM_API_KEY"]) as client:
        response = client.post("/chat", headers={"Authorization":"Bearer " + connection["OPEN_INTERPRETER_API_KEY"]},
                               json={"message":"compose handoff"})
    assert response.status_code == 200
    assert "http://llama-server:8080/v1" in response.json()["output"]
    assert "boundary-model-key" in response.json()["output"]
