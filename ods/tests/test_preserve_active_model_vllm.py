"""An installer rerun (ods update) keeps a vLLM install on vLLM.

scripts/preserve-active-model.py's default mode used to keep only a
llama-server GGUF route, so a .env with LLM_BACKEND=vllm got no contract and
the rerun fell back to the tier's GGUF. It now keeps the vLLM route while the
catalog snapshot is installed, clears the GGUF keys, and exits 2 rather than
fall back when it cannot. Runs the helper with this Python; no installer,
Docker or network.
"""

from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "preserve-active-model.py"
sys.path.insert(0, str(ROOT / "extensions" / "services" / "dashboard-api"))
import model_vllm  # noqa: E402

REVISION = "0123456789abcdef0123456789abcdef01234567"
FILES = {"config.json": b'{"model_type": "test"}', "model-00001-of-00001.safetensors": b"w" * 4096,
         "model.safetensors.index.json": b"{}", "tokenizer_config.json": b"{}"}
LFS = {"model-00001-of-00001.safetensors", "model.safetensors.index.json"}
ARGS = ["--max-model-len", "65536", "--enable-auto-tool-choice", "--tool-call-parser", "glm47",
        "--reasoning-parser", "glm47", "--speculative-config.method=mtp"]


def blob_sha1(content: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()


def entry() -> dict:
    files = [{"path": path, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()} if path in LFS
             else {"path": path, "size": len(data), "git_blob_sha1": blob_sha1(data)}
             for path, data in FILES.items()]
    return model_vllm.require_valid({
        "id": "test-model-nvfp4-vllm", "name": "Test Model NVFP4 (vLLM)", "runtime": "vllm",
        "hf_repo": "nvidia/Test-Model-NVFP4", "revision": REVISION, "files": files,
        "served_model_name": "Test-Model-NVFP4", "llm_model_name": "test-model-nvfp4",
        "context_length": 65536, "min_gpu_mib": 1024, "compute_capability": ["10.0"],
        "reasoning": "medium", "tool_call_parser": "glm47", "reasoning_parser": "glm47",
        "thinking_template_kwargs": {"off": {"reasoning_effort": "low"}, "low": {"reasoning_effort": "low"},
                                     "medium": {"reasoning_effort": "high"}, "high": {}},
        "vllm_args": ARGS, "selection": {"discrete": 0, "unified": 0, "cpu": 0, "vllm": 1},
    })


FOLDER = "nvidia--Test-Model-NVFP4@01234567"
VLLM_ENV = {
    "ODS_MODE": "local", "LLM_BACKEND": "vllm", "LLM_MODEL": "test-model-nvfp4",
    "GGUF_FILE": "", "MAX_CONTEXT": "65536", "CTX_SIZE": "65536",
    "VLLM_MODEL_DIR": FOLDER, "VLLM_SERVED_MODEL": "Test-Model-NVFP4",
    # The args an older release measured; a rerun takes this release's.
    "VLLM_ARGS": "--max-model-len 65536 --old-flag", "VLLM_GPU_UUID": "GPU-0f1e2d3c-aaaa-bbbb-cccc-0123456789ab",
    "VLLM_GPU_MEMORY_UTILIZATION": "0.85", "MODEL_SELECTION_SOURCE": "installer",
}


@pytest.fixture()
def install(tmp_path):
    gguf = {"id": "tiny-q4", "name": "Tiny", "llm_model_name": "tiny", "gguf_file": "tiny.gguf",
            "gguf_url": "https://huggingface.co/a/b/resolve/main/tiny.gguf"}
    (tmp_path / "model-library.json").write_text(json.dumps({"models": [gguf, entry()]}), encoding="utf-8")
    snapshot = tmp_path / "data" / "models" / FOLDER
    snapshot.mkdir(parents=True)
    for path, data in FILES.items():
        (snapshot / path).write_bytes(data)
    return tmp_path


def run(install: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    (install / ".env").write_text("".join(f"{key}={value}\n" for key, value in env.items()), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(HELPER), "--env", str(install / ".env"),
         "--catalog", str(install / "model-library.json"), "--models-dir", str(install / "data" / "models"),
         "--state", str(install / "data" / "model-state.json"), "--backend", "nvidia", "--local-model"],
        capture_output=True, text=True, timeout=60)


def contract(stdout: str) -> dict[str, str]:
    return {key: shlex.split(value)[0] if value != '""' else ""
            for key, _, value in (line.partition("=") for line in stdout.splitlines())}


def test_an_installed_vllm_route_is_kept_and_no_gguf_is_named(install):
    result = run(install, VLLM_ENV)

    assert result.returncode == 0, result.stderr
    assert contract(result.stdout) == {
        "LLM_BACKEND": "vllm",
        "LLM_MODEL": "test-model-nvfp4",
        "GGUF_FILE": "", "GGUF_URL": "", "GGUF_SHA256": "",
        "MAX_CONTEXT": "65536",
        "LLM_MODEL_SIZE_MB": "0",
        "MODEL_SELECTION_SOURCE": "installer",
        "VLLM_MODEL_DIR": FOLDER,
        "VLLM_SERVED_MODEL": "Test-Model-NVFP4",
        "VLLM_ARGS": " ".join(ARGS),
        "VLLM_GPU_UUID": VLLM_ENV["VLLM_GPU_UUID"],
        "VLLM_GPU_MEMORY_UTILIZATION": "0.85",
    }
    # Empty GGUF keys are written (they replace the tier map's), unlike the
    # optional llama.cpp settings the helper omits.
    assert 'GGUF_FILE=""' in result.stdout.splitlines()


def test_backend_value_is_read_case_insensitively(install):
    result = run(install, {**VLLM_ENV, "LLM_BACKEND": "VLLM"})
    assert result.returncode == 0, result.stderr
    assert contract(result.stdout)["LLM_BACKEND"] == "vllm"


@pytest.mark.parametrize("damage", ["missing", "short", "partial-only", "linked-folder"])
def test_an_incomplete_snapshot_stops_the_rerun_instead_of_falling_back(install, damage):
    snapshot = install / "data" / "models" / FOLDER
    weights = snapshot / "model-00001-of-00001.safetensors"
    if damage == "missing":
        weights.unlink()
    elif damage == "short":
        weights.write_bytes(b"w" * 10)
    elif damage == "partial-only":
        weights.rename(weights.with_name(weights.name + ".partial"))
    else:
        real = install / "elsewhere"
        snapshot.rename(real)
        try:
            snapshot.symlink_to(real, target_is_directory=True)
        except OSError:
            pytest.skip("this host cannot create directory symlinks")
    result = run(install, VLLM_ENV)

    assert result.returncode == 2
    assert result.stdout == ""
    assert "vLLM engine" in result.stderr and "refusing to replace it" in result.stderr


@pytest.mark.parametrize("change", [
    {"VLLM_MODEL_DIR": "nvidia--Test-Model-NVFP4@fedcba98"},
    {"VLLM_SERVED_MODEL": "Other-Model"},
    {"LLM_MODEL": "tiny"},
    {"VLLM_GPU_UUID": ""},
    {"VLLM_GPU_UUID": "GPU 0; rm -rf /"},
    {"VLLM_GPU_MEMORY_UTILIZATION": "1.5"},
    {"VLLM_GPU_MEMORY_UTILIZATION": "0"},
    {"ODS_MODE": "cloud"},
])
def test_a_route_that_is_not_the_catalog_snapshot_is_never_rewritten(install, change):
    result = run(install, {**VLLM_ENV, **change})
    assert result.returncode == 2
    assert result.stdout == ""


def test_llama_server_routes_keep_their_behaviour(install):
    # No vLLM route and no installed GGUF: nothing is preserved, as before.
    result = run(install, {"ODS_MODE": "local", "LLM_BACKEND": "llama-server",
                           "LLM_MODEL": "tiny", "GGUF_FILE": "tiny.gguf"})
    assert (result.returncode, result.stdout) == (0, "")
