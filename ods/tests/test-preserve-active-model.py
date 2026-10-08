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
    if overrides.get("external_lemonade"):
        command.append("--external-lemonade")
    if overrides.get("native_llm"):
        command.append("--native-llm")
    if overrides.get("served_model") is not None:
        command.extend(["--served-model", str(overrides["served_model"])])
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
            "LLAMA_ARG_CHECKPOINT_EVERY_NT",
            "LLAMA_ARG_SPEC_TYPE",
            "LLAMA_ARG_SPEC_DRAFT_N_MAX",
            "LLAMA_ARG_SPLIT_MODE",
            "LLAMA_ARG_TENSOR_SPLIT",
        ):
            assert key not in values, f"inactive optional runtime key was exported: {key}"


def test_cpu_profile_host_ram_caps_are_preserved() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        env, catalog, imports, models_dir = write_model_fixture(Path(tmp))
        env.write_text(
            env.read_text(encoding="utf-8")
            + "LLAMA_ARG_CTX_CHECKPOINTS=4\nLLAMA_ARG_CACHE_RAM=1024\n",
            encoding="utf-8",
        )
        values = run_helper(env, catalog, imports, models_dir)
        assert values["LLAMA_ARG_CTX_CHECKPOINTS"] == "4"
        assert values["LLAMA_ARG_CACHE_RAM"] == "1024"
        env.write_text(
            env.read_text(encoding="utf-8").replace("LLAMA_ARG_CTX_CHECKPOINTS=4", "LLAMA_ARG_CTX_CHECKPOINTS=many"),
            encoding="utf-8",
        )
        assert "LLAMA_ARG_CTX_CHECKPOINTS" not in run_helper(env, catalog, imports, models_dir)


def test_preserved_context_is_clamped_to_the_declared_native_context() -> None:
    # The fixture declares max_context_length 131072. A recorded context above
    # it (the old 131072 auto-picks of a 40960-token Qwen3-30B-A3B) is never
    # served, so the rerun carries the native maximum; an owner's context
    # within it is kept exactly.
    with tempfile.TemporaryDirectory() as tmp:
        env, catalog, imports, models_dir = write_model_fixture(Path(tmp))
        replace_env(env, "MAX_CONTEXT=65536", "MAX_CONTEXT=262144")
        replace_env(env, "CTX_SIZE=65536", "CTX_SIZE=262144")
        assert run_helper(env, catalog, imports, models_dir)["MAX_CONTEXT"] == "131072"
        replace_env(env, "MAX_CONTEXT=262144", "MAX_CONTEXT=98304")
        replace_env(env, "CTX_SIZE=262144", "CTX_SIZE=98304")
        assert run_helper(env, catalog, imports, models_dir)["MAX_CONTEXT"] == "98304"


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


def test_catalog_revision_pin_of_same_artifact_is_preserved() -> None:
    old_url = "https://huggingface.co/osmantic/agent-test/resolve/main/Agent-Test-Q4_K_M.gguf"
    with tempfile.TemporaryDirectory() as tmp:
        env, catalog, imports, models_dir = write_model_fixture(Path(tmp))
        data = json.loads(catalog.read_text(encoding="utf-8"))
        pinned = data["models"][0]["gguf_url"]
        replace_env(env, "GGUF_URL=" + pinned, "GGUF_URL=" + old_url)
        values = run_helper(env, catalog, imports, models_dir)
        assert values["GGUF_FILE"] == "Agent-Test-Q4_K_M.gguf", values
        assert values["GGUF_URL"] == pinned
    # Same repo file but a different digest, a different repo, or no recorded
    # digest remains a different (or unproven) artifact.
    for url, digest in (
        (old_url, "b" * 64),
        ("https://huggingface.co/other/agent-test/resolve/main/Agent-Test-Q4_K_M.gguf", "a" * 64),
        (old_url, ""),
    ):
        with tempfile.TemporaryDirectory() as tmp:
            env, catalog, imports, models_dir = write_model_fixture(Path(tmp))
            pinned = json.loads(catalog.read_text(encoding="utf-8"))["models"][0]["gguf_url"]
            replace_env(env, "GGUF_URL=" + pinned, "GGUF_URL=" + url)
            replace_env(env, "GGUF_SHA256=" + "a" * 64, "GGUF_SHA256=" + digest)
            assert run_helper(env, catalog, imports, models_dir) == {}, (url, digest)


def test_dashboard_activation_records_selection_owner() -> None:
    host_agent = (ROOT / "bin" / "ods-host-agent.py").read_text(encoding="utf-8")
    assert '"MODEL_SELECTION_SOURCE": "dashboard"' in host_agent


def test_installer_keeps_recommendation_and_active_model_separate() -> None:
    installer = (ROOT / "install-core.sh").read_text(encoding="utf-8")
    detection = (ROOT / "installers" / "phases" / "02-detection.sh").read_text(encoding="utf-8")
    env_writer = (ROOT / "installers" / "phases" / "06-directories.sh").read_text(encoding="utf-8")
    assert "--reselect-model) ODS_RESELECT_MODEL=true" in installer
    recommendation = detection.index('INSTALLER_RECOMMENDED_MODEL="${LLM_MODEL:-}"')
    preservation = detection.index('_preserve_script="$SCRIPT_DIR/scripts/preserve-active-model.py"')
    # A Windows-hosted served model is this run's pick, so it is recorded
    # before the recommendation; retained-model preservation runs after it.
    projection = next((detection.index(flag) for flag in ("--project-native-llm", "--project-external-lemonade")
                       if flag in detection), None)
    assert projection is not None, "phase 02 must record the Windows-hosted served model"
    assert projection < recommendation < preservation
    assert detection.index("unset LLAMA_ARG_N_CPU_MOE", preservation) < detection.index(
        'load_model_selector_env_from_output <<< "$_preserved_model_env"'
    )
    assert '--state "$INSTALL_DIR/data/model-state.json"' in detection
    assert "MODEL_RECOMMENDED_MODEL_VALUE=\"${INSTALLER_RECOMMENDED_MODEL:-${LLM_MODEL}}\"" in env_writer
    assert "MODEL_SELECTION_SOURCE=${MODEL_SELECTION_SOURCE_VALUE}" in env_writer
    for key in ("GGUF_URL", "GGUF_SHA256", "LLM_MODEL_SIZE_MB", "MODEL_RUNTIME_PROFILE"):
        assert f"{key}=" in env_writer



def test_commented_model_contract_survives_rerun() -> None:
    for quote in ("", "'", '"'):
        with tempfile.TemporaryDirectory() as tmp:
            env, catalog, imports, models_dir = write_model_fixture(Path(tmp))
            # A normal operator edit, accepted by load_env_file and Compose.
            for key, value in (
                ("GGUF_FILE", "Agent-Test-Q4_K_M.gguf"),
                ("MAX_CONTEXT", "65536"),
                ("CTX_SIZE", "65536"),
                ("LLAMA_PARALLEL", "1"),
                ("LLAMA_ARG_CACHE_TYPE_K", "q4_0"),
            ):
                replace_env(env, f"{key}={value}", f"{key}={quote}{value}{quote} # keep this selection")
            replace_env(env, "LLAMA_PARALLEL=" + quote + "1", "LLAMA_PARALLEL=" + quote + "2")
            values = run_helper(env, catalog, imports, models_dir)
            assert values.get("GGUF_FILE") == "Agent-Test-Q4_K_M.gguf", (quote, values)
            assert values["MAX_CONTEXT"] == "65536"
            assert values["LLAMA_PARALLEL"] == "2"
            assert values["LLAMA_ARG_CACHE_TYPE_K"] == "q4_0"


def test_comments_do_not_hide_external_runtime_selection() -> None:
    for key, old, selected in (
        ("ODS_MODE", "local", "cloud"),
        ("LLM_BACKEND", "llama-server", "lemonade"),
        ("EXTERNAL_LLM_URL", "", "http://external.invalid/v1"),
        ("LEMONADE_EXTERNAL", "false", "true"),
    ):
        with tempfile.TemporaryDirectory() as tmp:
            env, catalog, imports, models_dir = write_model_fixture(Path(tmp))
            replace_env(env, f"{key}={old}", f"{key}={selected} # chosen by the operator")
            assert run_helper(env, catalog, imports, models_dir) == {}, key


def test_dashboard_selected_external_lemonade_model_survives_retained_rerun() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        env, catalog, imports, models_dir = write_model_fixture(Path(tmp))
        replace_env(env, "ODS_MODE=local", "ODS_MODE=lemonade")
        replace_env(env, "LLM_BACKEND=llama-server", "LLM_BACKEND=lemonade")
        replace_env(env, "LEMONADE_EXTERNAL=false", "LEMONADE_EXTERNAL=true")
        with env.open("a", encoding="utf-8") as handle:
            handle.write("MODEL_SELECTION_SOURCE=dashboard\nLEMONADE_MODEL=Agent-Test-Q4_K_M\n")
        # The Windows-hosted model has no GGUF in the Linux model directory.
        (models_dir / "Agent-Test-Q4_K_M.gguf").unlink()
        values = run_helper(env, catalog, imports, models_dir, external_lemonade=True)
        assert values["LLM_MODEL"] == "agent-test"
        assert values["GGUF_FILE"] == "Agent-Test-Q4_K_M.gguf"
        assert values["MAX_CONTEXT"] == "65536"
        assert values["MODEL_SELECTION_SOURCE"] == "dashboard"
        assert values["LLAMA_ARG_CACHE_TYPE_K"] == "q4_0"
        assert values["MODEL_RUNTIME_PROFILE"] == "nvidia-8gb-64k"
        assert run_helper(env, catalog, imports, models_dir) == {}


def test_external_lemonade_preservation_rejects_missing_provenance() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        env, catalog, imports, models_dir = write_model_fixture(Path(tmp))
        replace_env(env, "ODS_MODE=local", "ODS_MODE=lemonade")
        replace_env(env, "LLM_BACKEND=llama-server", "LLM_BACKEND=lemonade")
        replace_env(env, "LEMONADE_EXTERNAL=false", "LEMONADE_EXTERNAL=true")
        try:
            run_helper(env, catalog, imports, models_dir, external_lemonade=True)
            assert False, "ambiguous retained external route must stop before .env rewrite"
        except subprocess.CalledProcessError as exc:
            assert exc.returncode == 2


def test_external_lemonade_preserves_catalog_35b_without_linux_artifact() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        record = next(
            model for model in json.loads((ROOT / "config/model-library.json").read_text(encoding="utf-8"))["models"]
            if model.get("gguf_file") == "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"
        )
        env = root / ".env"
        env.write_text("\n".join((
            "ODS_MODE=lemonade", "LLM_BACKEND=lemonade", "LEMONADE_EXTERNAL=true",
            "LEMONADE_MODEL=Qwen3.6-35B-A3B-UD-Q4_K_M",
            "MODEL_SELECTION_SOURCE=dashboard", "EXTERNAL_LLM_URL=",
            f"LLM_MODEL={record['llm_model_name']}", f"GGUF_FILE={record['gguf_file']}",
            f"GGUF_URL={record['gguf_url']}", f"GGUF_SHA256={record['gguf_sha256']}",
            "LLM_MODEL_SIZE_MB=21110", "MAX_CONTEXT=131072", "CTX_SIZE=131072",
            "MODEL_RUNTIME_PROFILE=", "MODEL_RUNTIME_PROFILE_LABEL=",
            "LLAMA_ARG_CACHE_TYPE_K=f16", "LLAMA_ARG_CACHE_TYPE_V=f16",
            "LLAMA_ARG_FLASH_ATTN=auto", "ODS_ACTIVE_MODEL_STORE=default",
        )) + "\n", encoding="utf-8")
        values = run_helper(env, ROOT / "config/model-library.json", root / "no-imports.json",
                            root / "no-model-artifacts", external_lemonade=True)
        assert values["LLM_MODEL"] == "qwen3.6-35b-a3b"
        assert values["MAX_CONTEXT"] == "131072"
        assert values["MODEL_SELECTION_SOURCE"] == "dashboard"
        assert values["LLAMA_ARG_CACHE_TYPE_K"] == "f16"
        assert values["MODEL_RUNTIME_PROFILE"] == ""
        assert "ODS_ACTIVE_MODEL_STORE" not in values  # phase 06 defaults to this


def test_invalid_external_dashboard_contract_fails_closed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        env, catalog, imports, models_dir = write_model_fixture(Path(tmp))
        replace_env(env, "ODS_MODE=local", "ODS_MODE=lemonade")
        replace_env(env, "LLM_BACKEND=llama-server", "LLM_BACKEND=lemonade")
        replace_env(env, "LEMONADE_EXTERNAL=false", "LEMONADE_EXTERNAL=true")
        replace_env(env, "MAX_CONTEXT=65536", "MAX_CONTEXT=garbled")
        with env.open("a", encoding="utf-8") as handle:
            handle.write("MODEL_SELECTION_SOURCE=dashboard\nLEMONADE_MODEL=host-model\n")
        command = [sys.executable, str(HELPER), "--external-lemonade", "--env", str(env),
                   "--catalog", str(catalog), "--models-dir", str(models_dir)]
        result = subprocess.run(command, capture_output=True, text=True)
        assert result.returncode == 2
        assert result.stdout == ""
        assert "Invalid retained external Lemonade model contract" in result.stderr


def test_conflicting_external_provider_does_not_silently_reselect() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        env, catalog, imports, models_dir = write_model_fixture(Path(tmp))
        replace_env(env, "ODS_MODE=local", "ODS_MODE=lemonade")
        replace_env(env, "LLM_BACKEND=llama-server", "LLM_BACKEND=lemonade")
        replace_env(env, "LEMONADE_EXTERNAL=false", "LEMONADE_EXTERNAL=true")
        replace_env(env, "EXTERNAL_LLM_URL=", "EXTERNAL_LLM_URL=https://other.invalid/v1")
        with env.open("a", encoding="utf-8") as handle:
            handle.write("MODEL_SELECTION_SOURCE=dashboard\nLEMONADE_MODEL=host-model\n")
        command = [sys.executable, str(HELPER), "--external-lemonade", "--env", str(env),
                   "--catalog", str(catalog), "--models-dir", str(models_dir)]
        result = subprocess.run(command, capture_output=True, text=True)
        assert result.returncode == 2
        assert result.stdout == ""


def test_external_lemonade_alias_must_match_saved_catalog_artifact() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        env, catalog, imports, models_dir = write_model_fixture(Path(tmp))
        replace_env(env, "ODS_MODE=local", "ODS_MODE=lemonade")
        replace_env(env, "LLM_BACKEND=llama-server", "LLM_BACKEND=lemonade")
        replace_env(env, "LEMONADE_EXTERNAL=false", "LEMONADE_EXTERNAL=true")
        with env.open("a", encoding="utf-8") as handle:
            handle.write("MODEL_SELECTION_SOURCE=dashboard\nLEMONADE_MODEL=Different-Model\n")
        command = [sys.executable, str(HELPER), "--external-lemonade", "--env", str(env),
                   "--catalog", str(catalog), "--models-dir", str(models_dir)]
        result = subprocess.run(command, capture_output=True, text=True)
        assert result.returncode == 2
        assert result.stdout == ""

def test_literal_hashes_and_invalid_values_are_not_comments() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        env, catalog, imports, models_dir = write_model_fixture(Path(tmp))
        data = json.loads(catalog.read_text(encoding="utf-8"))
        data["models"][0]["llm_model_name"] = "agent # literal"
        data["models"][0]["gguf_url"] += "#artifact"
        catalog.write_text(json.dumps(data), encoding="utf-8")
        replace_env(env, "LLM_MODEL=agent-test", "LLM_MODEL='agent # literal' # a comment")
        replace_env(env, "GGUF_URL=" + data["models"][0]["gguf_url"].removesuffix("#artifact"),
                    "GGUF_URL=" + data["models"][0]["gguf_url"] + " # a comment")
        assert run_helper(env, catalog, imports, models_dir)["LLM_MODEL"] == "agent # literal"
    for value in ("65536#not-a-comment", "'65536 #not-a-number'", '"65536 #unclosed'):
        with tempfile.TemporaryDirectory() as tmp:
            env, catalog, imports, models_dir = write_model_fixture(Path(tmp))
            replace_env(env, "MAX_CONTEXT=65536", "MAX_CONTEXT=" + value)
            replace_env(env, "CTX_SIZE=65536", "CTX_SIZE=" + value)
            assert run_helper(env, catalog, imports, models_dir) == {}, value



def test_commented_contract_reaches_installer_safe_loader() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        env, catalog, imports, models_dir = write_model_fixture(Path(tmp))
        replace_env(env, "GGUF_FILE=Agent-Test-Q4_K_M.gguf",
                    'GGUF_FILE="Agent-Test-Q4_K_M.gguf" # selected locally')
        replace_env(env, "LLAMA_ARG_CACHE_TYPE_K=q4_0", "LLAMA_ARG_CACHE_TYPE_K=q8_0 # operator tuning")
        command = """
set -euo pipefail
source "$1/lib/safe-env.sh"
LLM_MODEL=recommended-other
GGUF_FILE=recommended-other.gguf
MAX_CONTEXT=32768
LLAMA_ARG_CACHE_TYPE_K=f16
preserved=$("$2" "$1/scripts/preserve-active-model.py" --env "$3" --catalog "$4" \\
  --imports "$5" --models-dir "$6" --backend nvidia --memory-type discrete \\
  --vram-mb 8192 --ram-gb 32 --host-arch amd64)
load_model_selector_env_from_output <<< "$preserved"
printf '%s\\n' "$LLM_MODEL" "$GGUF_FILE" "$MAX_CONTEXT" "$LLAMA_ARG_CACHE_TYPE_K"
"""
        result = subprocess.run(
            ["bash", "-c", command, "preservation-check", str(ROOT), sys.executable,
             str(env), str(catalog), str(imports), str(models_dir)],
            check=True, text=True, capture_output=True,
        )
        assert result.stdout.splitlines() == [
            "agent-test", "Agent-Test-Q4_K_M.gguf", "65536", "q8_0",
        ], result.stdout


def test_external_registered_model_store_is_preserved() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        env, catalog, imports, models_dir = write_model_fixture(root)
        external = root / "ssd-models"
        external.mkdir()
        original = models_dir / "Agent-Test-Q4_K_M.gguf"
        # The fixture models an existing SSD installation; production never moves it.
        original.rename(external / original.name)
        (root / "data/model-stores.json").write_text(json.dumps({"schemaVersion":1,"stores":[
            {"id":"ssd","hostPath":str(external),"containerPath":"/model-stores/ssd"}]}))
        with env.open("a") as handle:
            handle.write("\nODS_ACTIVE_MODEL_STORE=ssd\nLLAMA_ARG_SPEC_DRAFT_TYPE_K=q4_0\n")
        preserved = run_helper(env, catalog, imports, models_dir)
        assert preserved["ODS_ACTIVE_MODEL_STORE"] == "ssd"
        assert preserved["GGUF_FILE"] == original.name
        assert preserved["LLAMA_ARG_SPEC_DRAFT_TYPE_K"] == "q4_0"


CATALOG = ROOT / "config/model-library.json"
SERVED_35B = "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"
SERVED_35B_ID = "Qwen3.6-35B-A3B-UD-Q4_K_M"


def catalog_record(gguf_file: str) -> dict:
    return next(model for model in json.loads(CATALOG.read_text(encoding="utf-8"))["models"]
                if model.get("gguf_file") == gguf_file)


def run_mode(env: Path, catalog: Path, *mode: str) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(HELPER), "--env", str(env), "--catalog", str(catalog),
               "--models-dir", str(env.parent / "no-model-artifacts"), *mode]
    return subprocess.run(command, capture_output=True, text=True)


def parse_contract(stdout: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in stdout.splitlines():
        key, raw = line.split("=", 1)
        parsed = shlex.split(raw, comments=False, posix=True)
        values[key] = parsed[0] if parsed else ""
    return values


def write_fresh_install_mismatch(directory: Path) -> Path:
    """The .env a fresh Windows-AMD install wrote before the fix (observed on Strix Halo).

    LEMONADE_MODEL names the model Lemonade serves; the model fields are the
    Linux host's own CPU pick, because WSL cannot see the Windows GPU.
    """
    linux_pick = catalog_record("Qwen3.5-9B-Q4_K_M.gguf")
    env = directory / ".env"
    env.write_text("\n".join((
        "ODS_MODE=lemonade", "LLM_BACKEND=lemonade", "LEMONADE_EXTERNAL=true",
        f"LEMONADE_MODEL={SERVED_35B_ID}", "EXTERNAL_LLM_URL=",
        f"LLM_MODEL={linux_pick['llm_model_name']}", f"GGUF_FILE={linux_pick['gguf_file']}",
        f"GGUF_URL={linux_pick['gguf_url']}", f"GGUF_SHA256={linux_pick['gguf_sha256']}",
        "LLM_MODEL_SIZE_MB=5760", "MAX_CONTEXT=65536", "CTX_SIZE=65536",
        "MODEL_SELECTION_SOURCE=installer", "ODS_ACTIVE_MODEL_STORE=default",
        f"MODEL_RECOMMENDED_MODEL={linux_pick['llm_model_name']}",
        f"MODEL_RECOMMENDED_GGUF={linux_pick['gguf_file']}", "MODEL_RECOMMENDED_CONTEXT=65536",
        "MODEL_RUNTIME_PROFILE='cpu-64k-q8-kv'",
        "LLAMA_ARG_CACHE_TYPE_K=q8_0", "LLAMA_ARG_CACHE_TYPE_V=q8_0",
    )) + "\n", encoding="utf-8")
    return env


def test_external_lemonade_projection_records_the_served_catalog_model() -> None:
    record = catalog_record(SERVED_35B)
    with tempfile.TemporaryDirectory() as tmp:
        env = Path(tmp) / ".env"  # a fresh install has none yet
        for model_id in (SERVED_35B_ID, f"extra.{SERVED_35B}"):
            result = run_mode(env, CATALOG, "--project-external-lemonade", model_id)
            assert result.returncode == 0, result.stderr
            assert parse_contract(result.stdout) == {
                "LLM_MODEL": record["llm_model_name"],
                "GGUF_FILE": SERVED_35B,
                "GGUF_URL": record["gguf_url"],
                "GGUF_SHA256": record["gguf_sha256"],
                "MAX_CONTEXT": str(record["context_length"]),
                "LLM_MODEL_SIZE_MB": str(record["size_mb"]),
                "MODEL_RUNTIME_PROFILE": "",
                "MODEL_RUNTIME_PROFILE_LABEL": "",
                "MODEL_RUNTIME_PROFILE_SOURCE": "",
                "MODEL_SELECTION_SOURCE": "installer",
            }, result.stdout
        loaded = run_mode(env, CATALOG, "--project-external-lemonade", SERVED_35B_ID, "--context", "32768")
        assert parse_contract(loaded.stdout)["MAX_CONTEXT"] == "32768"
        above_native = run_mode(env, CATALOG, "--project-external-lemonade", SERVED_35B_ID, "--context", "9999999")
        assert parse_contract(above_native.stdout)["MAX_CONTEXT"] == str(record["max_context_length"])


def test_external_lemonade_projection_refuses_ids_it_cannot_name() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        env = root / ".env"
        # Unknown, case-changed and empty ids name no model.
        for model_id in ("Not-A-Catalog-Model", SERVED_35B_ID.lower(), SERVED_35B.lower(), ""):
            result = run_mode(env, CATALOG, "--project-external-lemonade", model_id)
            assert result.returncode == 2 and result.stdout == "", (model_id, result.stdout)
            assert "refusing to record a different model" in result.stderr
        record = catalog_record(SERVED_35B)
        twins = root / "catalog.json"
        twins.write_text(json.dumps({"models": [record, {**record, "id": "twin"}]}), encoding="utf-8")
        ambiguous = run_mode(env, twins, "--project-external-lemonade", SERVED_35B_ID)
        assert ambiguous.returncode == 2 and ambiguous.stdout == ""
        for context in ("1023", "0", "-1", "128k", "65536 "):
            result = run_mode(env, CATALOG, "--project-external-lemonade", SERVED_35B_ID, "--context", context)
            assert result.returncode == 2 and result.stdout == "", context
        both = run_mode(env, CATALOG, "--project-external-lemonade", SERVED_35B_ID, "--external-lemonade")
        assert both.returncode == 2 and both.stdout == ""


def test_projected_external_lemonade_record_passes_the_rerun_check() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        env = root / ".env"
        projected = parse_contract(run_mode(
            env, CATALOG, "--project-external-lemonade", SERVED_35B_ID, "--context", "65536").stdout)
        # Phase 06 writes the projection next to the route and its runtime defaults.
        env.write_text("\n".join([
            "ODS_MODE=lemonade", "LLM_BACKEND=lemonade", "LEMONADE_EXTERNAL=true",
            f"LEMONADE_MODEL={SERVED_35B_ID}", "EXTERNAL_LLM_URL=",
            *(f"{key}={shlex.quote(value)}" for key, value in projected.items()),
            f"CTX_SIZE={projected['MAX_CONTEXT']}", "ODS_ACTIVE_MODEL_STORE=default",
            f"MODEL_RECOMMENDED_MODEL={projected['LLM_MODEL']}",
            f"MODEL_RECOMMENDED_GGUF={projected['GGUF_FILE']}",
            "LLAMA_ARG_FLASH_ATTN=auto", "LLAMA_ARG_CACHE_TYPE_K=f16", "LLAMA_ARG_CACHE_TYPE_V=f16",
        ]) + "\n", encoding="utf-8")
        preserved = run_helper(env, CATALOG, root / "no-imports.json", root / "no-model-artifacts",
                               external_lemonade=True)
        for key in ("LLM_MODEL", "GGUF_FILE", "GGUF_URL", "GGUF_SHA256", "MAX_CONTEXT",
                    "LLM_MODEL_SIZE_MB", "MODEL_SELECTION_SOURCE", "MODEL_RUNTIME_PROFILE"):
            assert preserved[key] == projected[key], key
        # Nothing for the repair mode to do on a consistent record.
        assert run_mode(env, CATALOG, "--repair-external-lemonade").returncode == 2


def test_fresh_install_mismatch_is_repaired_from_the_served_model() -> None:
    record = catalog_record(SERVED_35B)
    with tempfile.TemporaryDirectory() as tmp:
        env = write_fresh_install_mismatch(Path(tmp))
        before = env.read_bytes()
        stopped = run_mode(env, CATALOG, "--external-lemonade")
        assert stopped.returncode == 2 and stopped.stdout == ""
        repaired = run_mode(env, CATALOG, "--repair-external-lemonade", "--context", "131072")
        assert repaired.returncode == 0, repaired.stderr
        values = parse_contract(repaired.stdout)
        assert values["LLM_MODEL"] == record["llm_model_name"]
        assert values["GGUF_FILE"] == SERVED_35B
        assert values["GGUF_SHA256"] == record["gguf_sha256"]
        assert values["MAX_CONTEXT"] == "131072"
        assert values["MODEL_SELECTION_SOURCE"] == "installer"
        assert values["MODEL_RUNTIME_PROFILE"] == ""
        assert "LLAMA_ARG_CACHE_TYPE_K" not in values  # the CPU pick's tuning is not carried over
        assert f"({SERVED_35B_ID})" in repaired.stderr
        assert f"LLM_MODEL qwen3.5-9b -> {record['llm_model_name']}" in repaired.stderr
        assert "The served model is unchanged" in repaired.stderr
        assert env.read_bytes() == before  # the installer writes .env later, from this contract
        catalog_context = run_mode(env, CATALOG, "--repair-external-lemonade")
        assert parse_contract(catalog_context.stdout)["MAX_CONTEXT"] == str(record["context_length"])


def test_repair_refuses_anything_but_the_installer_written_mismatch() -> None:
    for old, new in (
        ("MODEL_SELECTION_SOURCE=installer", "MODEL_SELECTION_SOURCE=dashboard"),
        ("MODEL_SELECTION_SOURCE=installer", "MODEL_SELECTION_SOURCE=operator"),
        ("MODEL_RECOMMENDED_GGUF=Qwen3.5-9B-Q4_K_M.gguf", "MODEL_RECOMMENDED_GGUF=Other-Q4_K_M.gguf"),
        ("MODEL_RECOMMENDED_MODEL=qwen3.5-9b", "MODEL_RECOMMENDED_MODEL=other"),
        (f"LEMONADE_MODEL={SERVED_35B_ID}", "LEMONADE_MODEL=Not-A-Catalog-Model"),
        (f"LEMONADE_MODEL={SERVED_35B_ID}", "LEMONADE_MODEL=Qwen3.5-9B-Q4_K_M"),  # already consistent
        ("ODS_ACTIVE_MODEL_STORE=default", "ODS_ACTIVE_MODEL_STORE=ssd"),
        ("EXTERNAL_LLM_URL=", "EXTERNAL_LLM_URL=https://other.invalid/v1"),
        ("LEMONADE_EXTERNAL=true", "LEMONADE_EXTERNAL=false"),
    ):
        with tempfile.TemporaryDirectory() as tmp:
            env = write_fresh_install_mismatch(Path(tmp))
            replace_env(env, old, new)
            result = run_mode(env, CATALOG, "--repair-external-lemonade")
            assert result.returncode == 2 and result.stdout == "", (new, result.stdout)
            assert "refusing to change them" in result.stderr


def write_host_native_fixture(directory: Path, *, served: str = SERVED_35B, **values: str) -> Path:
    """A migrated WSL Portal .env: llama-server.exe on Windows serves the model."""
    record = catalog_record(served)
    fields = {
        "ODS_MODE": "local", "LLM_BACKEND": "llama-server", "AMD_INFERENCE_RUNTIME": "llama-server",
        "AMD_INFERENCE_RUNTIME_MODE": "windows-portal-llama-server", "AMD_INFERENCE_LOCATION": "host",
        "ODS_HOST_LLM_TRANSPORT": "model-router",
        "NATIVE_LLM_BASE_URL": "http://localhost:13305",
        "NATIVE_LLM_CONTAINER_BASE_URL": "http://host.docker.internal:13305",
        "EXTERNAL_LLM_URL": "", "MODEL_SELECTION_SOURCE": "dashboard",
        "LLM_MODEL": record["llm_model_name"], "GGUF_FILE": record["gguf_file"],
        "GGUF_URL": record["gguf_url"], "GGUF_SHA256": record["gguf_sha256"],
        "LLM_MODEL_SIZE_MB": str(record["size_mb"]), "MAX_CONTEXT": "65536", "CTX_SIZE": "65536",
        "ODS_ACTIVE_MODEL_STORE": "default",
        "MODEL_RECOMMENDED_MODEL": record["llm_model_name"], "MODEL_RECOMMENDED_GGUF": record["gguf_file"],
        **values,
    }
    env = directory / ".env"
    env.write_text("".join(f"{key}={shlex.quote(value)}\n" for key, value in fields.items()), encoding="utf-8")
    return env


def test_native_projection_records_the_served_catalog_model() -> None:
    record = catalog_record(SERVED_35B)
    expected = {
        "LLM_MODEL": record["llm_model_name"],
        "GGUF_FILE": SERVED_35B,
        "GGUF_URL": record["gguf_url"],
        "GGUF_SHA256": record["gguf_sha256"],
        "MAX_CONTEXT": str(record["context_length"]),
        "LLM_MODEL_SIZE_MB": str(record["size_mb"]),
        "MODEL_RUNTIME_PROFILE": "",
        "MODEL_RUNTIME_PROFILE_LABEL": "",
        "MODEL_RUNTIME_PROFILE_SOURCE": "",
        "MODEL_SELECTION_SOURCE": "installer",
    }
    with tempfile.TemporaryDirectory() as tmp:
        env = Path(tmp) / ".env"  # a fresh install has none yet
        # The --alias filename first; the retired Lemonade ids for one release.
        for model_id in (SERVED_35B, SERVED_35B_ID, f"extra.{SERVED_35B}", f"user.{SERVED_35B}"):
            result = run_mode(env, CATALOG, "--project-native-llm", model_id)
            assert result.returncode == 0, (model_id, result.stderr)
            assert parse_contract(result.stdout) == expected, (model_id, result.stdout)
        legacy_flag = run_mode(env, CATALOG, "--project-external-lemonade", SERVED_35B)
        assert legacy_flag.returncode == 0 and parse_contract(legacy_flag.stdout) == expected
        loaded = run_mode(env, CATALOG, "--project-native-llm", SERVED_35B, "--context", "32768")
        assert parse_contract(loaded.stdout)["MAX_CONTEXT"] == "32768"
        above_native = run_mode(env, CATALOG, "--project-native-llm", SERVED_35B, "--context", "9999999")
        assert parse_contract(above_native.stdout)["MAX_CONTEXT"] == str(record["max_context_length"])


def test_native_projection_refuses_ids_it_cannot_name() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        env = root / ".env"
        for model_id in ("Not-A-Catalog-Model", SERVED_35B.lower(), SERVED_35B_ID.upper(),
                         f"models/{SERVED_35B}", f"C:\\models\\{SERVED_35B}", f"extra.{SERVED_35B_ID}",
                         # A Lemonade user model names a checkpoint, not a catalog file.
                         "user.Qwen3.6-35B-A3B-Vision", f"{SERVED_35B} ", ""):
            result = run_mode(env, CATALOG, "--project-native-llm", model_id)
            assert result.returncode == 2 and result.stdout == "", (model_id, result.stdout)
            assert "refusing to record a different model" in result.stderr
        record = catalog_record(SERVED_35B)
        twins = root / "catalog.json"
        twins.write_text(json.dumps({"models": [record, {**record, "id": "twin"}]}), encoding="utf-8")
        ambiguous = run_mode(env, twins, "--project-native-llm", SERVED_35B)
        assert ambiguous.returncode == 2 and ambiguous.stdout == ""
        for mode in (("--native-llm",), ("--external-lemonade",), ("--project-external-lemonade", SERVED_35B),
                     ("--repair-native-llm", SERVED_35B)):
            both = run_mode(env, CATALOG, "--project-native-llm", SERVED_35B, *mode)
            assert both.returncode == 2 and both.stdout == "", mode
        alone = run_mode(env, CATALOG, "--served-model", SERVED_35B)
        assert alone.returncode == 2 and "--served-model applies only to --native-llm" in alone.stderr


def test_projected_record_passes_the_host_native_rerun_check() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        projected = parse_contract(run_mode(
            root / ".env", CATALOG, "--project-native-llm", SERVED_35B, "--context", "65536").stdout)
        # Phase 06 writes the projection next to the migrated host-native route.
        env = write_host_native_fixture(root, **projected)  # selected by the installer
        preserved = run_helper(env, CATALOG, root / "no-imports.json", root / "no-model-artifacts",
                               native_llm=True, served_model=SERVED_35B)
        for key in ("LLM_MODEL", "GGUF_FILE", "GGUF_URL", "GGUF_SHA256", "MAX_CONTEXT",
                    "LLM_MODEL_SIZE_MB", "MODEL_SELECTION_SOURCE", "MODEL_RUNTIME_PROFILE"):
            assert preserved[key] == projected[key], key
        # Nothing for the repair mode to do on a consistent record.
        repair = run_mode(env, CATALOG, "--repair-native-llm", SERVED_35B)
        assert repair.returncode == 2 and repair.stdout == ""


def test_host_native_selection_survives_a_rerun_without_a_linux_artifact() -> None:
    record = catalog_record(SERVED_35B)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # A retired line left in the .env never relabels the served model.
        env = write_host_native_fixture(root, LEMONADE_MODEL="Different-Model", LLAMA_ARG_CACHE_TYPE_K="f16",
                                        LLAMA_ARG_CACHE_TYPE_V="f16")
        values = run_helper(env, CATALOG, root / "no-imports.json", root / "no-model-artifacts", native_llm=True)
        assert values["GGUF_FILE"] == SERVED_35B
        assert values["LLM_MODEL"] == record["llm_model_name"]
        assert values["MAX_CONTEXT"] == "65536"
        assert values["MODEL_SELECTION_SOURCE"] == "dashboard"
        assert values["LLAMA_ARG_CACHE_TYPE_K"] == "f16"
        # The local mode still requires this host's artifact.
        assert run_helper(env, CATALOG, root / "no-imports.json", root / "no-model-artifacts") == {}
        # The Windows host serves another model than the retained record: stop.
        other = run_mode(env, CATALOG, "--native-llm", "--served-model", "Qwen3.5-9B-Q4_K_M.gguf")
        assert other.returncode == 2 and other.stdout == ""
        assert "refusing to replace it" in other.stderr
        for key, value in (("MAX_CONTEXT", "garbled"), ("MODEL_SELECTION_SOURCE", ""),
                           ("EXTERNAL_LLM_URL", "https://other.invalid/v1"),
                           ("GGUF_SHA256", "0" * 64)):
            broken = write_host_native_fixture(root, **{key: value})
            result = run_mode(broken, CATALOG, "--native-llm")
            assert result.returncode == 2 and result.stdout == "", key
    with tempfile.TemporaryDirectory() as tmp:
        # A local .env is not a Windows-hosted record: nothing to preserve here.
        env, catalog, imports, models_dir = write_model_fixture(Path(tmp))
        assert run_helper(env, catalog, imports, models_dir, native_llm=True) == {}


def test_served_model_check_reads_retired_lemonade_ids_of_the_same_file() -> None:
    record = catalog_record(SERVED_35B)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        env = root / ".env"
        env.write_text("\n".join((
            "ODS_MODE=lemonade", "LLM_BACKEND=lemonade", "LEMONADE_EXTERNAL=true",
            f"LEMONADE_MODEL=extra.{SERVED_35B}",
            "MODEL_SELECTION_SOURCE=dashboard", "EXTERNAL_LLM_URL=",
            f"LLM_MODEL={record['llm_model_name']}", f"GGUF_FILE={record['gguf_file']}",
            f"GGUF_URL={record['gguf_url']}", f"GGUF_SHA256={record['gguf_sha256']}",
            "MAX_CONTEXT=131072", "CTX_SIZE=131072", "ODS_ACTIVE_MODEL_STORE=default",
        )) + "\n", encoding="utf-8")
        # The first round F rerun: the Portal names the GGUF, the .env Lemonade's id.
        values = run_helper(env, CATALOG, root / "no-imports.json", root / "no-model-artifacts",
                            native_llm=True, served_model=SERVED_35B)
        assert values["GGUF_FILE"] == SERVED_35B and values["MAX_CONTEXT"] == "131072"
        other = run_mode(env, CATALOG, "--native-llm", "--served-model", "Qwen3.5-9B-Q4_K_M.gguf")
        assert other.returncode == 2 and other.stdout == ""
        assert "Invalid retained external Lemonade model contract" in other.stderr


def test_fresh_install_mismatch_is_repaired_from_the_served_gguf() -> None:
    record = catalog_record(SERVED_35B)
    with tempfile.TemporaryDirectory() as tmp:
        # The pre-round-F .env of an earlier fresh install, repaired from the
        # GGUF the Windows host now serves.
        env = write_fresh_install_mismatch(Path(tmp))
        before = env.read_bytes()
        repaired = run_mode(env, CATALOG, "--repair-native-llm", SERVED_35B, "--context", "131072")
        assert repaired.returncode == 0, repaired.stderr
        values = parse_contract(repaired.stdout)
        assert values["GGUF_FILE"] == SERVED_35B
        assert values["LLM_MODEL"] == record["llm_model_name"]
        assert values["MAX_CONTEXT"] == "131072"
        assert values["MODEL_SELECTION_SOURCE"] == "installer"
        assert "LLAMA_ARG_CACHE_TYPE_K" not in values
        assert f"({SERVED_35B})" in repaired.stderr
        assert f"GGUF_FILE Qwen3.5-9B-Q4_K_M.gguf -> {SERVED_35B}" in repaired.stderr
        assert "The served model is unchanged" in repaired.stderr
        assert env.read_bytes() == before
    linux_pick = catalog_record("Qwen3.5-9B-Q4_K_M.gguf")
    with tempfile.TemporaryDirectory() as tmp:
        # The same mismatch after the .env migration: no Lemonade line is left,
        # so only the served GGUF names the model.
        env = write_host_native_fixture(
            Path(tmp), served="Qwen3.5-9B-Q4_K_M.gguf", MODEL_SELECTION_SOURCE="installer",
            MODEL_RECOMMENDED_MODEL=linux_pick["llm_model_name"], MODEL_RECOMMENDED_GGUF=linux_pick["gguf_file"])
        repaired = run_mode(env, CATALOG, "--repair-native-llm", SERVED_35B)
        assert repaired.returncode == 0, repaired.stderr
        assert parse_contract(repaired.stdout)["GGUF_FILE"] == SERVED_35B
        legacy_flag = run_mode(env, CATALOG, "--repair-external-lemonade")
        assert legacy_flag.returncode == 2 and legacy_flag.stdout == ""


def test_native_repair_refuses_anything_but_the_installer_written_mismatch() -> None:
    for old, new in (
        ("MODEL_SELECTION_SOURCE=installer", "MODEL_SELECTION_SOURCE=dashboard"),
        ("MODEL_SELECTION_SOURCE=installer", "MODEL_SELECTION_SOURCE=operator"),
        ("MODEL_RECOMMENDED_GGUF=Qwen3.5-9B-Q4_K_M.gguf", "MODEL_RECOMMENDED_GGUF=Other-Q4_K_M.gguf"),
        ("MODEL_RECOMMENDED_MODEL=qwen3.5-9b", "MODEL_RECOMMENDED_MODEL=other"),
        # The .env's own record of the served model names another one.
        (f"LEMONADE_MODEL={SERVED_35B_ID}", "LEMONADE_MODEL=Not-A-Catalog-Model"),
        (f"LEMONADE_MODEL={SERVED_35B_ID}", "LEMONADE_MODEL=Qwen3.5-9B-Q4_K_M"),
        ("ODS_ACTIVE_MODEL_STORE=default", "ODS_ACTIVE_MODEL_STORE=ssd"),
        ("EXTERNAL_LLM_URL=", "EXTERNAL_LLM_URL=https://other.invalid/v1"),
        ("LEMONADE_EXTERNAL=true", "LEMONADE_EXTERNAL=false"),
    ):
        with tempfile.TemporaryDirectory() as tmp:
            env = write_fresh_install_mismatch(Path(tmp))
            replace_env(env, old, new)
            result = run_mode(env, CATALOG, "--repair-native-llm", SERVED_35B)
            assert result.returncode == 2 and result.stdout == "", (new, result.stdout)
            assert "refusing to change them" in result.stderr
    with tempfile.TemporaryDirectory() as tmp:
        env = write_fresh_install_mismatch(Path(tmp))
        # The served model already is the recorded one, or is no catalog model.
        for served in ("Qwen3.5-9B-Q4_K_M.gguf", "Not-A-Catalog-Model.gguf", ""):
            result = run_mode(env, CATALOG, "--repair-native-llm", served)
            assert result.returncode == 2 and result.stdout == "", served


def test_legacy_managed_lemonade_install_is_preserved_on_the_migration_rerun() -> None:
    """R1: the first round F rerun keeps a pre-round-F managed AMD model."""
    with tempfile.TemporaryDirectory() as tmp:
        env, catalog, imports, models_dir = write_model_fixture(Path(tmp))
        replace_env(env, "ODS_MODE=local", "ODS_MODE=lemonade")
        replace_env(env, "LLM_BACKEND=llama-server", "LLM_BACKEND=lemonade")
        with env.open("a", encoding="utf-8") as handle:
            handle.write("GPU_BACKEND=amd\nMODEL_SELECTION_SOURCE=dashboard\n"
                         "LEMONADE_MODEL=extra.Agent-Test-Q4_K_M.gguf\n")
        values = run_helper(env, catalog, imports, models_dir)
        assert values["GGUF_FILE"] == "Agent-Test-Q4_K_M.gguf"
        assert values["LLM_MODEL"] == "agent-test"
        assert values["MODEL_SELECTION_SOURCE"] == "dashboard"
        assert values["MAX_CONTEXT"] == "65536"
        for old, new in (
            # Lemonade's id names another model than the recorded file.
            ("LEMONADE_MODEL=extra.Agent-Test-Q4_K_M.gguf", "LEMONADE_MODEL=extra.Other.gguf"),
            # An unmanaged or external Lemonade is not this host's model.
            ("LEMONADE_EXTERNAL=false", "LEMONADE_EXTERNAL=true"),
            ("GPU_BACKEND=amd", "GPU_BACKEND=amd\nAMD_INFERENCE_RUNTIME=lemonade\nAMD_INFERENCE_MANAGED=false"),
        ):
            replace_env(env, old, new)
            assert run_helper(env, catalog, imports, models_dir) == {}, new
            replace_env(env, new, old)
        (models_dir / "Agent-Test-Q4_K_M.gguf").unlink()
        assert run_helper(env, catalog, imports, models_dir) == {}


def main() -> int:
    tests = [
        test_valid_curated_model_is_preserved,
        test_cpu_profile_host_ram_caps_are_preserved,
        test_preserved_context_is_clamped_to_the_declared_native_context,
        test_external_registered_model_store_is_preserved,
        test_commented_model_contract_survives_rerun,
        test_commented_contract_reaches_installer_safe_loader,
        test_comments_do_not_hide_external_runtime_selection,
        test_literal_hashes_and_invalid_values_are_not_comments,
        test_valid_dashboard_import_is_preserved,
        test_verified_switchboard_state_recovers_an_interrupted_installer_env,
        test_invalid_or_unavailable_contracts_are_not_preserved,
        test_catalog_revision_pin_of_same_artifact_is_preserved,
        test_dashboard_activation_records_selection_owner,
        test_installer_keeps_recommendation_and_active_model_separate,
        # External Lemonade (Windows-hosted model). These were not listed here
        # before, so CI never ran them.
        test_dashboard_selected_external_lemonade_model_survives_retained_rerun,
        test_external_lemonade_preservation_rejects_missing_provenance,
        test_external_lemonade_preserves_catalog_35b_without_linux_artifact,
        test_invalid_external_dashboard_contract_fails_closed,
        test_conflicting_external_provider_does_not_silently_reselect,
        test_external_lemonade_alias_must_match_saved_catalog_artifact,
        test_external_lemonade_projection_records_the_served_catalog_model,
        test_external_lemonade_projection_refuses_ids_it_cannot_name,
        test_projected_external_lemonade_record_passes_the_rerun_check,
        test_fresh_install_mismatch_is_repaired_from_the_served_model,
        test_repair_refuses_anything_but_the_installer_written_mismatch,
        # Round F: the served id is the --alias GGUF filename.
        test_native_projection_records_the_served_catalog_model,
        test_native_projection_refuses_ids_it_cannot_name,
        test_projected_record_passes_the_host_native_rerun_check,
        test_host_native_selection_survives_a_rerun_without_a_linux_artifact,
        test_served_model_check_reads_retired_lemonade_ids_of_the_same_file,
        test_fresh_install_mismatch_is_repaired_from_the_served_gguf,
        test_native_repair_refuses_anything_but_the_installer_written_mismatch,
        test_legacy_managed_lemonade_install_is_preserved_on_the_migration_rerun,
    ]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"All {len(tests)} active-model preservation tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
