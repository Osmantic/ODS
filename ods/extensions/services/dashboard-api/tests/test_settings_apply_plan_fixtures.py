import pytest
from pathlib import Path
import sys

dashboard_api_dir = Path(__file__).resolve().parents[1]
if str(dashboard_api_dir) not in sys.path:
    sys.path.insert(0, str(dashboard_api_dir))

from settings import _compute_env_apply_plan, _match_apply_service

def test_match_apply_service_llama():
    assert _match_apply_service("CTX_SIZE") == "llama-server"
    assert _match_apply_service("GGUF_FILE") == "llama-server"

def test_match_apply_service_webui():
    assert _match_apply_service("AUDIO_TTS_ENGINE") == "open-webui"
    assert _match_apply_service("ENABLE_IMAGE_GENERATION") == "open-webui"

def test_compute_apply_plan_restarts():
    current_env = {"CTX_SIZE": "8192"}
    updated_env = {"CTX_SIZE": "16384"}
    plan = _compute_env_apply_plan(current_env, updated_env)
    assert "llama-server" in plan["services"]
    assert plan["status"] == "ready"

def test_compute_apply_plan_no_changes():
    env = {"CTX_SIZE": "8192"}
    plan = _compute_env_apply_plan(env, env)
    assert plan["status"] == "none"
    assert len(plan["services"]) == 0
