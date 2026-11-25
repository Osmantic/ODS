"""The long-running host agent follows the installed WebUI Compose choice."""

import importlib.util
from pathlib import Path
import subprocess
import sys


def test_compose_resolver_uses_persisted_webui_choice_after_cache_invalidation(tmp_path, monkeypatch):
    agent_path = Path(__file__).resolve().parents[1] / "bin/ods-host-agent.py"
    spec = importlib.util.spec_from_file_location("webui_compose_agent", agent_path)
    agent = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = agent
    try:
        spec.loader.exec_module(agent)
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        (scripts / "resolve-compose-stack.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        monkeypatch.setattr(agent, "INSTALL_DIR", tmp_path)
        monkeypatch.setattr(agent, "_find_usable_bash", lambda: "bash")
        monkeypatch.setattr(agent.platform, "system", lambda: "Linux")
        monkeypatch.setenv("ENABLE_OPEN_WEBUI", "true")
        monkeypatch.setenv("ODS_GATEWAY_ONLY", "false")
        monkeypatch.delenv("EXTERNAL_LLM_URL", raising=False)

        child_envs = []

        def run_resolver(command, **kwargs):
            child_envs.append(kwargs["env"].copy())
            return subprocess.CompletedProcess(
                command, 0, stdout="-f docker-compose.base.yml\n", stderr=""
            )

        monkeypatch.setattr(agent.subprocess, "run", run_resolver)
        env_file = tmp_path / ".env"
        env_file.write_text(
            "ODS_MODE=local\nENABLE_OPEN_WEBUI=false\nODS_GATEWAY_ONLY=false\n"
            "EXTERNAL_LLM_URL=https://model.example.test/v1\n"
            "SYNTHETIC_PRIVATE_VALUE=keep-out-of-child-env\n", encoding="utf-8"
        )
        agent.resolve_compose_flags()
        assert child_envs[0]["ENABLE_OPEN_WEBUI"] == "false"
        assert child_envs[0]["ODS_GATEWAY_ONLY"] == "false"
        # The resolver gets only the external route's presence, never the
        # credential-bearing URL.
        assert child_envs[0]["ODS_EXTERNAL_LLM_SELECTED"] == "true"
        assert "EXTERNAL_LLM_URL" not in child_envs[0]
        assert "SYNTHETIC_PRIVATE_VALUE" not in child_envs[0]

        # A later UI action changes persisted selection while this process
        # still has the old startup environment and a saved Compose cache.
        (tmp_path / ".compose-flags").write_text("-f stale.yml\n", encoding="utf-8")
        env_file.write_text(
            "ODS_MODE=local\nENABLE_OPEN_WEBUI=true\nODS_GATEWAY_ONLY=true\n"
            "EXTERNAL_LLM_URL=https://model.example.test/v1\n",
            encoding="utf-8",
        )
        agent.invalidate_compose_cache()
        assert not (tmp_path / ".compose-flags").exists()
        agent.resolve_compose_flags()
        assert child_envs[1]["ENABLE_OPEN_WEBUI"] == "true"
        assert child_envs[1]["ODS_GATEWAY_ONLY"] == "true"
    finally:
        sys.modules.pop(spec.name, None)
