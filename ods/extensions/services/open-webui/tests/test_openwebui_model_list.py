"""Contracts for openwebui-model-list.py, which keeps Open WebUI's model picker
to ods/current instead of every LiteLLM alias for the same model (#7628)."""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

SERVICE = Path(__file__).resolve().parents[1]
ODS = SERVICE.parents[2]
SCRIPT = SERVICE / "openwebui-model-list.py"
WRAPPER = SERVICE / "openwebui-entrypoint.sh"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


model_list = load("openwebui_model_list", SCRIPT)
renderer = load("render_runtime_configs_for_model_list", ODS / "scripts/render-runtime-configs.py")

LITELLM = "http://litellm:4000"
PIXEL = "http://pixel-edge:9595/v1"


def configs(**env: str) -> dict:
    return model_list.api_configs(env)


def test_switchboard_install_lists_only_the_public_model():
    # The reported install: local mode, Switchboard on, Open WebUI on LiteLLM.
    assert configs(OPENAI_API_BASE_URL=LITELLM) == {
        "0": {"enable": True, "model_ids": ["ods/current"]},
    }


@pytest.mark.parametrize("url", [LITELLM, f"{LITELLM}/v1", f"{LITELLM}/v1/", " http://litellm:4000/v1 "])
def test_every_spelling_of_the_gateway_url_is_recognised(url):
    assert configs(OPENAI_API_BASE_URL=url, ODS_MODE="local", ODS_MODEL_SWITCHBOARD="enabled") == {
        "0": {"enable": True, "model_ids": ["ods/current"]},
    }


def test_hybrid_mode_keeps_the_named_cloud_routes():
    assert configs(OPENAI_API_BASE_URL=LITELLM, ODS_MODE="hybrid") == {
        "0": {"enable": True, "model_ids": ["ods/current", "cloud", "minimax", "minimax-fast"]},
    }


def test_pixel_connection_is_left_alone_and_the_gateway_is_keyed_by_position():
    # The Pixel overlay puts pixel-edge first; Open WebUI looks configs up by index.
    assert configs(OPENAI_API_BASE_URLS=f"{PIXEL};{LITELLM}/v1", OPENAI_API_BASE_URL="ignored") == {
        "1": {"enable": True, "model_ids": ["ods/current"]},
    }


@pytest.mark.parametrize("env", [
    # Cloud mode: LiteLLM serves cloud.yaml, whose names are all distinct models.
    {"OPENAI_API_BASE_URL": f"{LITELLM}/v1", "ODS_MODE": "cloud"},
    # Switchboard off: LiteLLM serves the mode's own config.
    {"OPENAI_API_BASE_URL": f"{LITELLM}/v1", "ODS_MODEL_SWITCHBOARD": "observe"},
    {"OPENAI_API_BASE_URL": f"{LITELLM}/v1", "ODS_MODEL_SWITCHBOARD": "legacy"},
    # Open WebUI talks to llama-server or a host runtime directly.
    {"OPENAI_API_BASE_URL": "http://llama-server:8080/v1"},
    {"OPENAI_API_BASE_URL": "http://host.docker.internal:8080/v1"},
    {"OPENAI_API_BASE_URL": "http://litellm-proxy:4000/v1"},
    {"OPENAI_API_BASE_URL": "http://litellm:4001/v1"},
    {},
])
def test_nothing_is_set_when_litellm_is_not_serving_the_switchboard_aliases(env):
    assert model_list.api_configs(env) == {}


@pytest.mark.parametrize("mode", ["local", "hybrid"])
def test_listed_names_are_the_switchboard_routes_minus_the_compatibility_aliases(mode):
    inputs = renderer.RenderInputs(
        model="m", gguf_file="m.gguf", gpu_backend="nvidia", ods_mode=mode,
        llm_base_url="http://llama-server:8080", litellm_key="k", opencode_port=3003,
        context_length=8192,
    )
    rendered = renderer.render_litellm_switchboard(inputs).content
    names = [json.loads(name) if name.startswith('"') else name
             for name in re.findall(r"^  - model_name: (.+)$", rendered, re.M)]
    aliases = {"local", "default", "*"}
    assert aliases <= set(names)
    assert model_list.visible_models(mode) == [name for name in names if name not in aliases]


def test_the_rule_matches_litellms_config_selection():
    select = (ODS / "extensions/services/litellm/select-config.sh").read_text(encoding="utf-8")
    assert 'if [ "$mode" != "cloud" ] && [ "$switchboard" = "enabled" ]; then' in select
    assert 'mode="${ODS_MODE:-local}"' in select
    assert 'switchboard="${ODS_MODEL_SWITCHBOARD:-enabled}"' in select
    litellm = (ODS / "extensions/services/litellm/compose.yaml").read_text(encoding="utf-8")
    assert "- ODS_MODE=${ODS_MODE:-local}" in litellm
    assert "- ODS_MODEL_SWITCHBOARD=${ODS_MODEL_SWITCHBOARD:-enabled}" in litellm


def test_compose_mounts_the_step_and_passes_the_litellm_mode():
    compose = (ODS / "docker-compose.base.yml").read_text(encoding="utf-8")
    block = re.search(r"^  open-webui:\n(.*?)(?=^  \S|^\S|\Z)", compose, re.M | re.S).group(1)
    assert ("      - ./extensions/services/open-webui/openwebui-model-list.py:"
            "/opt/ods/openwebui-model-list.py:ro,z\n") in block
    assert '      ODS_MODE: "${ODS_MODE:-local}"\n' in block
    assert '      ODS_MODEL_SWITCHBOARD: "${ODS_MODEL_SWITCHBOARD:-enabled}"\n' in block
    # Open WebUI must read the configs from the environment at every start.
    assert '      ENABLE_PERSISTENT_CONFIG: "false"\n' in block


def test_the_wrapper_sets_the_configs_after_the_backup_step_and_before_start():
    wrapper = WRAPPER.read_text(encoding="utf-8")
    assert "\r" not in wrapper and "\r" not in SCRIPT.read_text(encoding="utf-8")
    step = wrapper.index('model_configs="$(python3 /opt/ods/openwebui-model-list.py)"')
    assert wrapper.rindex("python3 /opt/ods/openwebui-prepare.py") < step < wrapper.index('exec "$@"')


@pytest.mark.skipif(sys.platform == "win32", reason="runs the POSIX wrapper with sh")
@pytest.mark.parametrize("mode,expected", [
    ("local", '{"0":{"enable":true,"model_ids":["ods/current"]}}'),
    ("cloud", "unset"),
])
def test_the_wrapper_hands_the_configs_to_open_webui(tmp_path, mode, expected):
    # A stand-in for the image's python3: the backup step succeeds, and the
    # model-list step runs the real script from this checkout.
    stand_in = tmp_path / "python3"
    stand_in.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = /opt/ods/openwebui-model-list.py ]; then\n'
        f'    exec "{sys.executable}" "{SCRIPT}"\n'
        "fi\n"
        "exit 0\n"
    )
    stand_in.chmod(0o755)
    environment = {key: value for key, value in os.environ.items()
                   if key not in ("OPENAI_API_CONFIGS", "OPENAI_API_BASE_URLS", "ODS_WEBUI_BIND_ADDRESS")}
    environment.update(PATH=f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
                       OPENAI_API_BASE_URL=LITELLM, ODS_MODE=mode, ODS_MODEL_SWITCHBOARD="enabled")
    started = subprocess.run(["sh", str(WRAPPER), "sh", "-c", 'printf "%s" "${OPENAI_API_CONFIGS-unset}"'],
                             capture_output=True, text=True, env=environment, check=False)
    assert started.returncode == 0, started.stderr
    assert started.stdout == expected
