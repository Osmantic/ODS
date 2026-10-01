"""Exercise actual phase 08 planning without contacting Docker or registries."""
import os
from pathlib import Path
import subprocess

import pytest

PHASE = Path(__file__).resolve().parents[1] / "installers/phases/08-images.sh"


@pytest.mark.parametrize("backend", ["cpu", "amd", "nvidia"])
@pytest.mark.parametrize("mode", ["cloud", "local"])
@pytest.mark.parametrize("external", ["true", "false"])
@pytest.mark.parametrize("perplexica", ["true", "false"])
def test_inference_images_follow_selected_route(backend, mode, external, perplexica):
    command = r'''
set -eu
ods_progress() { :; }
show_phase() { :; }
ai() { :; }
DRY_RUN=true
ENABLE_VOICE=false
ENABLE_WORKFLOWS=false
ENABLE_RAG=false
ENABLE_HERMES=false
ENABLE_OPENCLAW=false
ENABLE_COMFYUI=false
source "$1"
printf '%s\n' "${PULL_LIST[@]}"
'''
    result = subprocess.run(
        ["bash", "-c", command, "test", str(PHASE)],
        env={**os.environ, "GPU_BACKEND": backend, "ODS_MODE": mode,
             "LEMONADE_EXTERNAL": external, "ENABLE_PERPLEXICA": perplexica, "COMPOSE_FLAGS": ""},
        capture_output=True, text=True, check=True,
    )
    images = result.stdout
    assert "OPEN WEBUI" in images
    assert ("PERPLEXICA" in images) == (perplexica == "true")
    if mode == "cloud" or external == "true":
        assert "LLAMA-SERVER" not in images
        assert "LEMONADE" not in images
    else:
        assert ("LEMONADE" if backend == "amd" else "LLAMA-SERVER") in images
