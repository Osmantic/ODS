from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "build-installation-context.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("build_installation_context", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_check_json_reports_detected_install_facts(monkeypatch, tmp_path, capsys) -> None:
    module = _load_module()
    env_path = tmp_path / ".env"
    env_path.write_text(
        "ODS_DEVICE_NAME=lab-node\n"
        "GPU_BACKEND=nvidia\n"
        "LLM_MODEL=catalog/fallback\n"
        "CTX_SIZE=32768\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "_running_services", lambda _root: {"n8n", "comfyui"})
    monkeypatch.setattr(module, "_loaded_model", lambda: "runtime/model")

    result = module.main(["--env", str(env_path), "--check", "--format", "json"])

    assert result == 0
    assert json.loads(capsys.readouterr().out) == {
        "host": "lab-node",
        "gpu_backend": "NVIDIA GPU (CUDA via llama.cpp)",
        "model": "runtime/model",
        "context_size": 32768,
        "services": ["comfyui", "n8n"],
        "urls": {
            "dashboard": "http://lab-node.local",
            "talk": "http://talk.lab-node.local",
            "chat": "http://chat.lab-node.local",
        },
    }
