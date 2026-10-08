"""LiteLLM presents its gateway key to the remote-provider egress (GHSA-4rpc).

The egress refuses callers without the LiteLLM gateway key. Configs rendered
before that carried ``api_key: not-needed`` and survive reinstalls, so the
container's start-up step sets the key on every route to the egress.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

COMPOSE = Path(__file__).resolve().parent.parent / "compose.yaml"
EGRESS = "http://remote-provider-egress:8091/v1"


def _startup_config_step() -> str:
    """The python3 -c program from the container command, as the shell runs it."""
    command = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]["litellm"]["command"][0]
    program = command.split('python3 -c "\n', 1)[1].split('\n"\n', 1)[0]
    # Compose would rewrite $; this program must not depend on that.
    assert "$" not in program
    return program


def _run(tmp_path, config, master_key):
    source = tmp_path / "config.yaml"
    source.write_text(yaml.safe_dump(config), encoding="utf-8")
    target = tmp_path / "rendered.yaml"
    program = _startup_config_step().replace("/tmp/config.yaml", target.as_posix())
    env = {key: value for key, value in os.environ.items()
           if key not in {"LITELLM_MASTER_KEY", "TOKEN_SPY_URL", "TOKEN_SPY_API_KEY", "LANGFUSE_ENABLED"}}
    env["CONFIG_PATH"] = str(source)
    if master_key is not None:
        env["LITELLM_MASTER_KEY"] = master_key
    subprocess.run([sys.executable, "-c", program], env=env, check=True)
    return yaml.safe_load(target.read_text(encoding="utf-8"))


def _remote_config(api_key="not-needed"):
    return {"model_list": [
        {"model_name": "ods/current", "litellm_params": {"model": "openai/m", "api_base": EGRESS, "api_key": api_key}},
        {"model_name": "default", "litellm_params": {"model": "openai/m", "api_base": EGRESS}},
        {"model_name": "elsewhere", "litellm_params": {"model": "anthropic/x", "api_key": "os.environ/ANTHROPIC_API_KEY"}},
    ]}


def test_stale_remote_config_gets_the_gateway_key(tmp_path):
    rendered = _run(tmp_path, _remote_config(), master_key="sk-ods-gateway")
    keys = [model["litellm_params"].get("api_key") for model in rendered["model_list"]]
    # The reference, not the value: the key stays in the environment.
    assert keys == ["os.environ/LITELLM_MASTER_KEY", "os.environ/LITELLM_MASTER_KEY",
                    "os.environ/ANTHROPIC_API_KEY"]
    assert "sk-ods-gateway" not in (tmp_path / "rendered.yaml").read_text(encoding="utf-8")


@pytest.mark.parametrize("master_key", [None, ""])
def test_missing_gateway_key_never_falls_back_to_a_provider_key(tmp_path, master_key):
    # LiteLLM's OpenAI client would otherwise send OPENAI_API_KEY to the egress.
    rendered = _run(tmp_path, _remote_config(api_key="os.environ/LITELLM_MASTER_KEY"), master_key=master_key)
    keys = [model["litellm_params"].get("api_key") for model in rendered["model_list"]]
    assert keys == ["ods-gateway-key-missing", "ods-gateway-key-missing", "os.environ/ANTHROPIC_API_KEY"]


def test_configs_without_an_egress_route_are_unchanged(tmp_path):
    local = {"model_list": [
        {"model_name": "ods/current",
         "litellm_params": {"model": "openai/q", "api_base": "http://llama-server:8080/v1", "api_key": "not-needed"}},
    ]}
    rendered = _run(tmp_path, local, master_key="sk-ods-gateway")
    assert rendered["model_list"] == local["model_list"]
