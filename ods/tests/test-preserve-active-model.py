#!/usr/bin/env python3
"""Regression tests for active local model preservation across installer reruns."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "preserve-active-model.py"


def write_model_fixture(root: Path, *, imported: bool = False) -> tuple[Path, Path, Path, Path]:
    models_dir = root / "data" / "models"
    models_dir.mkdir(parents=True)
    model_file = models_dir / "Agent-Test-Q4_K_M.gguf"
    model_file.write_bytes(b"G" * 4096)
    record = {
        "id": "agent-test-q4",
        "name": "Agent Test",
        "llm_model_name": "agent-test",
        "gguf_file": model_file.name,
        "gguf_url": "https://huggingface.co/osmantic/agent-test/resolve/pinned/Agent-Test-Q4_K_M.gguf",
        "gguf_sha256": "a" * 64,
        "size_bytes": 4096,
        "size_mb": 1,
        "context_length": 65536,
        "max_context_length": 131072,
        "runtime_profiles": [
            {
                "id": "nvidia-8gb-64k",
                "label": "NVIDIA 8GB 64K",
                "backend": "nvidia",
                "memory_type": "discrete",
                "host_arch": ["amd64"],
                "vram_min_gb": 7.5,
                "vram_max_gb": 8.5,
                "system_ram_min_gb": 16,
                "context_length": 65536,
                "env": {
                    "LLAMA_PARALLEL": "1",
                    "LLAMA_ARG_FLASH_ATTN": "on",
                    "LLAMA_ARG_CACHE_TYPE_K": "q4_0",
                    "LLAMA_ARG_CACHE_TYPE_V": "q4_0",
                },
            }
        ],
    }
    catalog = root / "catalog.json"
    imports = root / "model-imports.json"
    if imported:
        record["source"] = "huggingface"
        catalog.write_text(json.dumps({"models": []}), encoding="utf-8")
        imports.write_text(json.dumps({"models": [record]}), encoding="utf-8")
    else:
        catalog.write_text(json.dumps({"models": [record]}), encoding="utf-8")
    env = root / ".env"
    env.write_text(
        "\n".join(
            [
                "ODS_MODE=local",
                "LLM_BACKEND=llama-server",
                "EXTERNAL_LLM_URL=",
                "LEMONADE_EXTERNAL=false",
                "LLM_MODEL=agent-test",
                f"GGUF_FILE={model_file.name}",
                f"GGUF_URL={record['gguf_url']}",
                f"GGUF_SHA256={record['gguf_sha256']}",
                "LLM_MODEL_SIZE_MB=1",
                "MAX_CONTEXT=65536",
                "CTX_SIZE=65536",
                "MODEL_RECOMMENDED_GGUF=recommended-other.gguf",
                "MODEL_RUNTIME_PROFILE=nvidia-8gb-64k",
                "MODEL_RUNTIME_PROFILE_LABEL='NVIDIA 8GB 64K'",
                "LLAMA_PARALLEL=1",
                "LLAMA_ARG_FLASH_ATTN=on",
                "LLAMA_ARG_CACHE_TYPE_K=q4_0",
                "LLAMA_ARG_CACHE_TYPE_V=q4_0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return env, catalog, imports, models_dir


def run_helper(env: Path, catalog: Path, imports: Path, models_dir: Path, **overrides: object) -> dict[str, str]:
    command = [
        sys.executable,
        str(HELPER),
        "--env",
        str(env),
        "--catalog",
        str(catalog),
        "--imports",
        str(imports),
        "--models-dir",
        str(models_dir),
        "--backend",
        str(overrides.get("backend", "nvidia")),
        "--memory-type",
        str(overrides.get("memory_type", "discrete")),
        "--vram-mb",
        str(overrides.get("vram_mb", 8192)),
        "--ram-gb",
        str(overrides.get("ram_gb", 32)),
        "--host-arch",
        str(overrides.get("host_arch", "amd64")),
    ]
    if overrides.get("state") is not None:
        command.extend(["--state", str(overrides["state"])])
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, raw = line.split("=", 1)
        parsed = shlex.split(raw, comments=False, posix=True)
        values[key] = parsed[0] if parsed else ""
    return values


def replace_env(env: Path, old: str, new: str) -> None:
    text = env.read_text(encoding="utf-8")
    assert old in text
    env.write_text(text.replace(old, new), encoding="utf-8")


def test_valid_curated_model_is_preserved() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        env, catalog, imports, models_dir = write_model_fixture(Path(tmp))
        values = run_helper(env, catalog, imports, models_dir)
        assert values["LLM_MODEL"] == "agent-test"
        assert values["GGUF_FILE"] == "Agent-Test-Q4_K_M.gguf"
        assert values["MAX_CONTEXT"] == "65536"
        assert values["MODEL_SELECTION_SOURCE"] == "dashboard"
        assert values["MODEL_RUNTIME_PROFILE"] == "nvidia-8gb-64k"
        assert values["LLAMA_ARG_CACHE_TYPE_K"] == "q4_0"
        for key in (
            "LLAMA_ARG_N_CPU_MOE",
            "LLAMA_ARG_NO_CACHE_PROMPT",
            "LLAMA_ARG_CHECKPOINT_EVERY_N_TOKENS",
            "LLAMA_ARG_SPEC_TYPE",
            "LLAMA_ARG_SPEC_DRAFT_N_MAX",
            "LLAMA_ARG_SPLIT_MODE",
            "LLAMA_ARG_TENSOR_SPLIT",
        ):
            assert key not in values, f"inactive optional runtime key was exported: {key}"


def test_valid_dashboard_import_is_preserved() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        env, catalog, imports, models_dir = write_model_fixture(Path(tmp), imported=True)
        values = run_helper(env, catalog, imports, models_dir)
        assert values["GGUF_FILE"] == "Agent-Test-Q4_K_M.gguf"


def test_verified_switchboard_state_recovers_an_interrupted_installer_env() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        env, catalog, imports, models_dir = write_model_fixture(root)
        state = root / "data" / "model-state.json"
        state.write_text(
            json.dumps(
                {
                    "schema": "ods.model-state.v1",
                    "seq": 5,
                    "routeSeq": 4,
                    "operation": None,
                    "desired": {"catalogId": "agent-test-q4"},
                    "active": {
                        "routeSeq": 4,
                        "catalogId": "agent-test-q4",
                        "runtimeModelId": "Agent-Test-Q4_K_M.gguf",
                        "publicModel": "ods/current",
                        "backend": {
                            "kind": "llama-server",
                            "endpointId": "llama-server-default",
                            "nativeRoute": None,
                        },
                        "contextLength": 65536,
                        "verifiedAt": "2026-08-31T11:43:34Z",
                        "proof": {
                            "identity": "Agent-Test-Q4_K_M.gguf",
                            "completion": True,
                        },
                    },
                    "availability": {"mode": "serve_active", "queueDeadline": None},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        replace_env(env, "LLM_MODEL=agent-test", "LLM_MODEL=bootstrap-model")
        replace_env(env, "GGUF_FILE=Agent-Test-Q4_K_M.gguf", "GGUF_FILE=Bootstrap-2B.gguf")
        replace_env(env, "MAX_CONTEXT=65536", "MAX_CONTEXT=32768")
        replace_env(env, "CTX_SIZE=65536", "CTX_SIZE=32768")

        values = run_helper(
            env,
            catalog,
            imports,
            models_dir,
            state=state,
            vram_mb=4096,
        )
        assert values["LLM_MODEL"] == "agent-test"
        assert values["GGUF_FILE"] == "Agent-Test-Q4_K_M.gguf"
        assert values["MAX_CONTEXT"] == "65536"
        assert values["MODEL_SELECTION_SOURCE"] == "dashboard"
        assert values["MODEL_RUNTIME_PROFILE"] == ""
        assert values["LLAMA_ARG_CACHE_TYPE_K"] == "q4_0"
        assert values["LLAMA_ARG_CACHE_TYPE_V"] == "q4_0"

        replace_env(env, "LLM_MODEL=bootstrap-model", "LLM_MODEL=agent-test")
        replace_env(env, "GGUF_FILE=Bootstrap-2B.gguf", "GGUF_FILE=Agent-Test-Q4_K_M.gguf")
        replace_env(env, "MAX_CONTEXT=32768", "MAX_CONTEXT=65536")
        replace_env(env, "CTX_SIZE=32768", "CTX_SIZE=65536")
        matching_env_values = run_helper(
            env,
            catalog,
            imports,
            models_dir,
            state=state,
            vram_mb=4096,
        )
        assert matching_env_values["GGUF_FILE"] == "Agent-Test-Q4_K_M.gguf"
        assert matching_env_values["MODEL_RUNTIME_PROFILE"] == ""
        assert matching_env_values["LLAMA_ARG_CACHE_TYPE_K"] == "q4_0"
        assert matching_env_values["LLAMA_ARG_CACHE_TYPE_V"] == "q4_0"

        replace_env(env, "MODEL_RUNTIME_PROFILE=nvidia-8gb-64k", "MODEL_RUNTIME_PROFILE=")
        replace_env(env, "LLAMA_ARG_CACHE_TYPE_K=q4_0", "LLAMA_ARG_CACHE_TYPE_K=f16")
        replace_env(env, "LLAMA_ARG_CACHE_TYPE_V=q4_0", "LLAMA_ARG_CACHE_TYPE_V=f16")
        profileless_values = run_helper(
            env,
            catalog,
            imports,
            models_dir,
            state=state,
            vram_mb=4096,
        )
        assert profileless_values["MODEL_RUNTIME_PROFILE"] == ""
        assert profileless_values["LLAMA_ARG_CACHE_TYPE_K"] == "q4_0"
        assert profileless_values["LLAMA_ARG_CACHE_TYPE_V"] == "q4_0"

        replace_env(env, "LLM_MODEL=agent-test", "LLM_MODEL=bootstrap-model")
        replace_env(env, "GGUF_FILE=Agent-Test-Q4_K_M.gguf", "GGUF_FILE=Bootstrap-2B.gguf")
        payload = json.loads(state.read_text(encoding="utf-8"))
        payload["active"]["proof"]["completion"] = False
        state.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        assert run_helper(env, catalog, imports, models_dir, state=state, vram_mb=4096) == {}

        state.unlink()
        state.symlink_to(env)
        assert run_helper(env, catalog, imports, models_dir, state=state, vram_mb=4096) == {}


def test_invalid_or_unavailable_contracts_are_not_preserved() -> None:
    mutations = (
        ("GGUF_URL=https://huggingface.co/", "GGUF_URL=https://example.invalid/"),
        ("MAX_CONTEXT=65536", "MAX_CONTEXT=not-a-number"),
        ("ODS_MODE=local", "ODS_MODE=cloud"),
        ("LLAMA_ARG_CACHE_TYPE_K=q4_0", "LLAMA_ARG_CACHE_TYPE_K=evil;touch"),
    )
    for old_fragment, new_fragment in mutations:
        with tempfile.TemporaryDirectory() as tmp:
            env, catalog, imports, models_dir = write_model_fixture(Path(tmp))
            replace_env(env, old_fragment, new_fragment)
            values = run_helper(env, catalog, imports, models_dir)
            assert values == {}, (old_fragment, new_fragment, values)

    with tempfile.TemporaryDirectory() as tmp:
        env, catalog, imports, models_dir = write_model_fixture(Path(tmp))
        (models_dir / "Agent-Test-Q4_K_M.gguf").unlink()
        assert run_helper(env, catalog, imports, models_dir) == {}

    with tempfile.TemporaryDirectory() as tmp:
        env, catalog, imports, models_dir = write_model_fixture(Path(tmp))
        assert run_helper(env, catalog, imports, models_dir, vram_mb=4096) == {}


def test_dashboard_activation_records_selection_owner() -> None:
    host_agent = (ROOT / "bin" / "ods-host-agent.py").read_text(encoding="utf-8")
    assert '"MODEL_SELECTION_SOURCE": "dashboard"' in host_agent


def test_installer_keeps_recommendation_and_active_model_separate() -> None:
    installer = (ROOT / "install-core.sh").read_text(encoding="utf-8")
    detection = (ROOT / "installers" / "phases" / "02-detection.sh").read_text(encoding="utf-8")
    env_writer = (ROOT / "installers" / "phases" / "06-directories.sh").read_text(encoding="utf-8")
    assert "--reselect-model) ODS_RESELECT_MODEL=true" in installer
    assert detection.index('INSTALLER_RECOMMENDED_MODEL="${LLM_MODEL:-}"') < detection.index(
        'preserve-active-model.py'
    )
    assert detection.index("unset LLAMA_ARG_N_CPU_MOE") < detection.index(
        'load_model_selector_env_from_output <<< "$_preserved_model_env"'
    )
    assert '--state "$INSTALL_DIR/data/model-state.json"' in detection
    assert "MODEL_RECOMMENDED_MODEL_VALUE=\"${INSTALLER_RECOMMENDED_MODEL:-${LLM_MODEL}}\"" in env_writer
    assert "MODEL_SELECTION_SOURCE=${MODEL_SELECTION_SOURCE_VALUE}" in env_writer
    for key in ("GGUF_URL", "GGUF_SHA256", "LLM_MODEL_SIZE_MB", "MODEL_RUNTIME_PROFILE"):
        assert f"{key}=" in env_writer


def main() -> int:
    tests = [
        test_valid_curated_model_is_preserved,
        test_valid_dashboard_import_is_preserved,
        test_verified_switchboard_state_recovers_an_interrupted_installer_env,
        test_invalid_or_unavailable_contracts_are_not_preserved,
        test_dashboard_activation_records_selection_owner,
        test_installer_keeps_recommendation_and_active_model_separate,
    ]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"All {len(tests)} active-model preservation tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
