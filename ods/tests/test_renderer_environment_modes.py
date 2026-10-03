"""Environment defaults must obey the same routing choices as CLI options."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("key,option,bad,valid", [
    ("ODS_MODEL_SWITCHBOARD", "--switchboard-mode", "enabledd", "enabled"),
    ("REMOTE_LLM_ENABLED", "--remote-llm-enabled", "tru", "false"),
    ("REMOTE_LLM_TRANSPORT", "--remote-llm-transport", "sshh", ""),
])
def test_invalid_environment_mode_cannot_write_configs(tmp_path, key, option, bad, valid):
    script = Path(__file__).resolve().parents[1] / "scripts/render-runtime-configs.py"
    environment = dict(os.environ, ODS_MODEL_SWITCHBOARD="enabled", REMOTE_LLM_ENABLED="false",
                       REMOTE_LLM_TRANSPORT="")
    environment[key] = bad
    existing = tmp_path / ".env.generated"
    existing.write_text("known-good\n")
    command = [sys.executable, str(script), "--write", "--output-root", str(tmp_path)]
    result = subprocess.run(command, env=environment, capture_output=True, text=True, check=False)
    assert result.returncode == 2, result.stdout
    assert option in result.stderr
    assert existing.read_text() == "known-good\n"
    assert list(tmp_path.iterdir()) == [existing]

    # An explicit valid CLI value takes precedence over the invalid default.
    corrected = subprocess.run(command + [option, valid], env=environment,
                               capture_output=True, text=True, check=False)
    assert corrected.returncode == 0, corrected.stderr
    assert existing.read_text() != "known-good\n"
