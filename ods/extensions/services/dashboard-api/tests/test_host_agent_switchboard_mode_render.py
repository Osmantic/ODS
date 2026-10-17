"""The host agent renders the switchboard for the install mode at startup (#7638).

The checked-in config/litellm/switchboard.yaml is the hybrid render. The
Windows installer only copies it, so a local install served the hybrid-only
cloud/minimax routes until the first model activation re-rendered it.
"""

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

_ODS = Path(__file__).resolve().parents[4]
_agent_path = _ODS / "bin" / "ods-host-agent.py"
_spec = importlib.util.spec_from_file_location("ods_host_agent_switchboard_render", _agent_path)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["ods_host_agent_switchboard_render"] = _mod
_spec.loader.exec_module(_mod)

CLOUD_MARKERS = ("model_name: cloud", "anthropic/", "api.minimax.io")


@pytest.fixture
def install(tmp_path):
    (tmp_path / "scripts").mkdir()
    shutil.copyfile(_ODS / "scripts" / "render-runtime-configs.py",
                    tmp_path / "scripts" / "render-runtime-configs.py")
    target = tmp_path / "config" / "litellm" / "switchboard.yaml"
    target.parent.mkdir(parents=True)
    shutil.copyfile(_ODS / "config" / "litellm" / "switchboard.yaml", target)
    return tmp_path, target


def _env(**overrides):
    env = {"ODS_MODE": "local", "ODS_MODEL_SWITCHBOARD": "enabled", "GPU_BACKEND": "nvidia",
           "LITELLM_KEY": "sk-test", "GGUF_FILE": "model.gguf"}
    env.update(overrides)
    return env


def test_shipped_template_carries_hybrid_routes(install):
    _, target = install
    assert all(marker in target.read_text(encoding="utf-8") for marker in CLOUD_MARKERS)


@pytest.mark.parametrize("mode", ["local", "lemonade"])
def test_local_install_drops_hybrid_cloud_routes(install, mode):
    root, target = install
    assert _mod._render_switchboard_for_install_mode(root, _env(ODS_MODE=mode)) is True
    content = target.read_text(encoding="utf-8")
    for marker in CLOUD_MARKERS:
        assert marker not in content
    assert "model_name: ods/current" in content


@pytest.mark.parametrize("env", [
    _env(ODS_MODE="hybrid"),
    _env(ODS_MODE="cloud"),
    _env(ODS_MODEL_SWITCHBOARD="observe"),
    _env(ODS_MODE="not-a-mode"),
])
def test_hybrid_cloud_and_non_switchboard_installs_are_untouched(install, env):
    root, target = install
    before = target.read_bytes()
    assert _mod._render_switchboard_for_install_mode(root, env) is False
    assert target.read_bytes() == before


def test_missing_renderer_fails_loudly(tmp_path):
    with pytest.raises(RuntimeError):
        _mod._render_switchboard_for_install_mode(tmp_path, _env())
