"""Lemonade-era installations move to the llama.cpp runtime without losing
the active model (scripts/migrate-lemonade-install.py).

The fixtures are the .env lines the Lemonade-era installer (Phase 06) wrote
for a managed Linux AMD install, the Windows Portal and an owner's own
Lemonade server.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


MIGRATE = load("migrate_lemonade_install", "scripts/migrate-lemonade-install.py")
UNINSTALL = load("uninstall_compose_volumes", "scripts/uninstall-compose-volumes.py")

PROVIDER_KEY = "sk-ods-lemonade-" + "0123456789abcdef" * 2
LITELLM_KEY = "sk-litellm-test-fixture"
OWNER_KEY = "owner-lemonade-test-fixture"

COMMON = f"""#=== LLM Backend Mode ===
ODS_GATEWAY_ONLY=false
ENABLE_OPEN_WEBUI=true
LLM_MODEL=qwen3.6-35b-a3b
GGUF_FILE=Qwen3.6-35B-A3B-Q4_K_M.gguf
GGUF_URL=https://huggingface.co/fixture/Qwen3.6-35B-A3B-Q4_K_M.gguf
GGUF_SHA256={"ab" * 32}
LLM_MODEL_SIZE_MB=21000
MAX_CONTEXT=131072
CTX_SIZE=131072
MODEL_SELECTION_SOURCE=dashboard
ODS_ACTIVE_MODEL_STORE=default
MODEL_RECOMMENDED_MODEL=qwen3.6-27b
MODEL_RECOMMENDED_GGUF=Qwen3.6-27B-Q4_K_M.gguf
LITELLM_KEY={LITELLM_KEY}
"""

MANAGED_AMD = COMMON + f"""ODS_MODE=lemonade
ODS_MODEL_SWITCHBOARD=enabled
LLM_API_URL=http://litellm:4000
OPEN_WEBUI_LLM_BASE_URL=http://model-router:4100/v1
LLM_BACKEND=lemonade
LLM_API_BASE_PATH=/api/v1
EXTERNAL_LLM_URL=
SKIP_MODEL_DOWNLOAD=false
AMD_INFERENCE_RUNTIME=lemonade
AMD_INFERENCE_BACKEND=rocm
AMD_INFERENCE_LOCATION=container
AMD_INFERENCE_PORT=8080
AMD_INFERENCE_SUPPORTED_BACKENDS=rocm
AMD_INFERENCE_RUNTIME_MODE=linux-container
AMD_INFERENCE_MANAGED=true
LEMONADE_EXTERNAL=false
LEMONADE_HOST_TRANSPORT=direct
LEMONADE_BASE_URL=
LEMONADE_CONTAINER_BASE_URL=
LEMONADE_API_BASE_PATH=/api/v1
LEMONADE_MODEL=extra.Qwen3.6-35B-A3B-Q4_K_M.gguf
GPU_BACKEND=amd
VIDEO_GID=44
RENDER_GID=992
LEMONADE_SERVER_IMAGE=ghcr.io/lemonade-sdk/lemonade-server:v10.2.0@sha256:{"08" * 32}
HSA_OVERRIDE_GFX_VERSION=11.5.1
HSA_XNACK=1
ROCBLAS_USE_HIPBLASLT=1
AMDGPU_TARGET=gfx1151
LLAMA_CPP_REF=b8763
LITELLM_LEMONADE_API_KEY={PROVIDER_KEY}
"""

PORTAL = COMMON + f"""ODS_MODE=lemonade
LLM_API_URL=http://litellm:4000
LLM_BACKEND=lemonade
LLM_API_BASE_PATH=/api/v1
AMD_INFERENCE_RUNTIME=lemonade
AMD_INFERENCE_BACKEND=auto
AMD_INFERENCE_LOCATION=host
AMD_INFERENCE_PORT=13305
AMD_INFERENCE_SUPPORTED_BACKENDS=auto
AMD_INFERENCE_RUNTIME_MODE=external-lemonade
AMD_INFERENCE_MANAGED=false
LEMONADE_EXTERNAL=true
LEMONADE_HOST_TRANSPORT=model-router
LEMONADE_BASE_URL=http://127.0.0.1:13305
LEMONADE_CONTAINER_BASE_URL=http://host.docker.internal:13305
LEMONADE_API_BASE_PATH=/api/v1
LEMONADE_MODEL=extra.Qwen3.6-35B-A3B-Q4_K_M.gguf
GPU_BACKEND=amd
LITELLM_LEMONADE_API_KEY={PROVIDER_KEY}
"""

OWNER_EXTERNAL = COMMON + f"""ODS_MODE=lemonade
LLM_API_URL=http://litellm:4000
LLM_BACKEND=lemonade
LLM_API_BASE_PATH=/api/v1
AMD_INFERENCE_RUNTIME=lemonade
AMD_INFERENCE_BACKEND=auto
AMD_INFERENCE_LOCATION=host
AMD_INFERENCE_PORT=13305
AMD_INFERENCE_RUNTIME_MODE=external-lemonade
AMD_INFERENCE_MANAGED=false
LEMONADE_EXTERNAL=true
LEMONADE_HOST_TRANSPORT=direct
LEMONADE_BASE_URL=http://192.168.50.20:13305/api/v1
LEMONADE_API_BASE_PATH=/api/v1
LEMONADE_MODEL=user.Qwen3-Coder-Next
GPU_BACKEND=nvidia
LITELLM_LEMONADE_API_KEY={OWNER_KEY}
OPEN_WEBUI_LLM_API_KEY=
HERMES_LLM_API_KEY=
"""

# Every Lemonade-era installer wrote these lines, whatever the GPU.
NVIDIA = COMMON + f"""ODS_MODE=local
LLM_API_URL=http://llama-server:8080
LLM_BACKEND=llama-server
LLM_API_BASE_PATH=/v1
AMD_INFERENCE_RUNTIME=
AMD_INFERENCE_BACKEND=
LEMONADE_EXTERNAL=false
LEMONADE_HOST_TRANSPORT=direct
LEMONADE_BASE_URL=
LEMONADE_CONTAINER_BASE_URL=
LEMONADE_API_BASE_PATH=/api/v1
LEMONADE_MODEL=
GPU_BACKEND=nvidia
LITELLM_LEMONADE_API_KEY={PROVIDER_KEY}
"""

PORTAL_PROOF = {
    "schemaVersion": 1,
    "planPath": "/mnt/c/Users/owner/AppData/Local/ODS/model-store-plan.json",
    "modelStoreId": "store-0123",
}


def env_dict(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = MIGRATE.ASSIGNMENT_RE.match(line)
        if match and match.group(2) not in values:
            values[match.group(2)] = MIGRATE.parse_env_value(match.group(3))
    return values


def install(tmp_path: Path, text: str) -> Path:
    root = tmp_path / "ods"
    (root / "data").mkdir(parents=True)
    (root / ".env").write_text(text, encoding="utf-8", newline="\n")
    os.chmod(root / ".env", 0o600)
    return root


def migrate(root: Path, *, dry_run: bool = False) -> tuple[int, str]:
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        status = MIGRATE.migrate_env(root, dry_run=dry_run, render=False)
    return status, stdout.getvalue()


def assert_no_secret_printed(output: str) -> None:
    for secret in (PROVIDER_KEY, LITELLM_KEY, OWNER_KEY):
        assert secret not in output


def assert_active_model_kept(env: dict[str, str]) -> None:
    assert env["LLM_MODEL"] == "qwen3.6-35b-a3b"
    assert env["GGUF_FILE"] == "Qwen3.6-35B-A3B-Q4_K_M.gguf"
    assert env["MAX_CONTEXT"] == env["CTX_SIZE"] == "131072"
    assert env["MODEL_SELECTION_SOURCE"] == "dashboard"
    assert env["GGUF_SHA256"] == "ab" * 32


def assert_retired_keys_gone(env: dict[str, str]) -> None:
    for key in MIGRATE.RETIRED_KEYS:
        assert key not in env, key


def test_managed_amd_moves_to_llama_cpp_and_keeps_the_active_model(tmp_path):
    root = install(tmp_path, MANAGED_AMD)
    status, output = migrate(root)
    assert status == 0
    env = env_dict(root / ".env")
    assert_active_model_kept(env)
    assert_retired_keys_gone(env)
    assert env["ODS_MODE"] == "local"
    assert env["LLM_BACKEND"] == "llama-server"
    assert env["LLM_API_BASE_PATH"] == "/v1"
    # The Lemonade-era value looped through LiteLLM; llama-server is direct.
    assert env["LLM_API_URL"] == "http://llama-server:8080"
    assert env["AMD_INFERENCE_RUNTIME"] == "llama-server"
    assert env["AMD_INFERENCE_BACKEND"] == "vulkan"
    assert env["AMD_INFERENCE_LOCATION"] == "container"
    assert env["AMD_INFERENCE_PORT"] == "8080"
    assert env["AMD_INFERENCE_SUPPORTED_BACKENDS"] == "vulkan,rocm"
    assert env["AMD_INFERENCE_RUNTIME_MODE"] == "linux-container"
    assert env["AMD_INFERENCE_MANAGED"] == "true"
    # Strix Halo workarounds for Lemonade's ROCm build go with it.
    assert "HSA_OVERRIDE_GFX_VERSION" not in env
    assert "ROCBLAS_USE_HIPBLASLT" not in env
    # Unrelated settings stay exactly as they were.
    assert env["OPEN_WEBUI_LLM_BASE_URL"] == "http://model-router:4100/v1"
    assert env["LITELLM_KEY"] == LITELLM_KEY
    assert env["VIDEO_GID"] == "44"
    assert "lemonade-migration: managed" in output
    assert_no_secret_printed(output)
    if os.name == "posix":
        assert stat.S_IMODE((root / ".env").stat().st_mode) == 0o600


def test_managed_amd_records_the_lemonade_volumes_for_uninstall(tmp_path):
    root = install(tmp_path, MANAGED_AMD)
    _, output = migrate(root)
    record = json.loads((root / "data/lemonade-retired-volumes.json").read_text(encoding="utf-8"))
    assert record == {
        "schemaVersion": 1,
        "installDir": str(root.resolve()),
        "volumeKeys": ["lemonade-cache", "lemonade-llama", "lemonade-recipe"],
    }
    # The uninstaller accepts exactly this record for this installation.
    assert UNINSTALL.retired_volume_keys(root.resolve()) == {
        "lemonade-cache", "lemonade-llama", "lemonade-recipe",
    }
    assert ("docker volume rm ods_lemonade-cache ods_lemonade-llama ods_lemonade-recipe "
            "&& docker image rm ods-lemonade-server:latest") in output


def test_portal_becomes_the_host_native_llama_server_route(tmp_path):
    root = install(tmp_path, PORTAL)
    (root / "data/wsl-lemonade-runtime.json").write_text(json.dumps(PORTAL_PROOF), encoding="utf-8")
    status, output = migrate(root)
    assert status == 0
    env = env_dict(root / ".env")
    assert_active_model_kept(env)
    assert_retired_keys_gone(env)
    assert env["NATIVE_LLM_BASE_URL"] == "http://127.0.0.1:13305"
    assert env["NATIVE_LLM_CONTAINER_BASE_URL"] == "http://host.docker.internal:13305"
    assert env["ODS_HOST_LLM_TRANSPORT"] == "model-router"
    assert env["AMD_INFERENCE_RUNTIME"] == "llama-server"
    assert env["AMD_INFERENCE_LOCATION"] == "host"
    assert env["AMD_INFERENCE_PORT"] == "13305"
    assert env["AMD_INFERENCE_RUNTIME_MODE"] == "windows-portal-llama-server"
    assert env["AMD_INFERENCE_MANAGED"] == "true"
    assert env["ODS_MODE"] == "local"
    assert env["LLM_BACKEND"] == "llama-server"
    assert env["LLM_API_BASE_PATH"] == "/v1"
    assert "lemonade-migration: portal" in output
    assert not (root / "data/lemonade-retired-volumes.json").exists()
    assert_no_secret_printed(output)


def test_owner_lemonade_becomes_the_generic_external_route(tmp_path):
    root = install(tmp_path, OWNER_EXTERNAL)
    status, output = migrate(root)
    assert status == 0
    env = env_dict(root / ".env")
    assert_retired_keys_gone(env)
    assert env["EXTERNAL_LLM_URL"] == "http://192.168.50.20:13305"
    assert env["EXTERNAL_LLM_CONTAINER_URL"] == "http://192.168.50.20:13305"
    assert env["EXTERNAL_LLM_PROVIDER"] == "openai-compatible"
    assert env["EXTERNAL_LLM_MODEL"] == env["LLM_MODEL"] == "user.Qwen3-Coder-Next"
    assert env["LLM_BACKEND"] == "external"
    assert env["ODS_MODE"] == "local"
    assert env["SKIP_MODEL_DOWNLOAD"] == "true"
    assert env["ODS_MODEL_SWITCHBOARD"] == "observe"
    assert env["OPEN_WEBUI_LLM_API_KEY"] == env["HERMES_LLM_API_KEY"] == LITELLM_KEY
    assert all(env[key] == "" for key in env if key.startswith("AMD_INFERENCE_"))
    key_file = root / "config/litellm/external-upstream.key"
    assert key_file.read_text(encoding="ascii") == OWNER_KEY + "\n"
    if os.name == "posix":
        assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    assert "lemonade-migration: external" in output
    assert not (root / "data/lemonade-retired-volumes.json").exists()
    assert_no_secret_printed(output)


def test_generated_provider_key_is_not_kept_as_an_owner_key(tmp_path):
    root = install(tmp_path, OWNER_EXTERNAL.replace(OWNER_KEY, PROVIDER_KEY))
    assert migrate(root)[0] == 0
    assert not (root / "config/litellm/external-upstream.key").exists()


def test_external_route_without_a_model_is_refused_unchanged(tmp_path):
    text = OWNER_EXTERNAL.replace("LEMONADE_MODEL=user.Qwen3-Coder-Next\n", "")
    root = install(tmp_path, text)
    with pytest.raises(MIGRATE.MigrationError, match="--external-llm-model"):
        migrate(root)
    assert (root / ".env").read_text(encoding="utf-8") == text


def test_non_amd_install_only_loses_the_retired_lines(tmp_path):
    root = install(tmp_path, NVIDIA)
    before = env_dict(root / ".env")
    status, output = migrate(root)
    assert status == 0
    env = env_dict(root / ".env")
    assert_retired_keys_gone(env)
    expected = {key: value for key, value in before.items() if key not in MIGRATE.RETIRED_KEYS}
    assert env == expected
    assert not (root / "data/lemonade-retired-volumes.json").exists()
    assert "docker volume rm" not in output


def test_dry_run_and_rerun_change_nothing(tmp_path):
    root = install(tmp_path, MANAGED_AMD)
    status, output = migrate(root, dry_run=True)
    assert status == 0 and "would change" in output
    assert (root / ".env").read_text(encoding="utf-8") == MANAGED_AMD
    assert not (root / "data/lemonade-retired-volumes.json").exists()
    migrate(root)
    migrated = (root / ".env").read_text(encoding="utf-8")
    status, output = migrate(root)
    assert status == 0 and output.strip() == "lemonade-migration: none"
    assert (root / ".env").read_text(encoding="utf-8") == migrated
