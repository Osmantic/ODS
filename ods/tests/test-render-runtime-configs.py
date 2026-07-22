#!/usr/bin/env python3
"""Tests for scripts/render-runtime-configs.py."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "render-runtime-configs.py"


def run_renderer(*args: str) -> dict[str, object]:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return json.loads(proc.stdout)


def file_by_surface(payload: dict[str, object], surface: str) -> dict[str, str]:
    for item in payload["files"]:
        if item["surface"] == surface:
            return item
    raise AssertionError(f"missing surface {surface}")


def model_provider_by_id(settings: dict[str, object], provider_id: str) -> dict[str, object]:
    for provider in settings["modelProviders"]:
        if provider["id"] == provider_id:
            return provider
    raise AssertionError(f"missing model provider {provider_id}")


def test_all_surfaces_render() -> None:
    payload = run_renderer("--surface", "all")
    surfaces = {item["surface"] for item in payload["files"]}
    assert surfaces == {
        "env", "opencode", "litellm-lemonade", "perplexica", "hermes",
        "model-router-endpoints",
    }
    assert payload["mode"] == "dry-run"


def test_switchboard_surface_gated_on_enabled_mode() -> None:
    observed = run_renderer("--surface", "all", "--switchboard-mode", "observe")
    assert "litellm-switchboard" not in {i["surface"] for i in observed["files"]}
    enabled = run_renderer("--surface", "all", "--switchboard-mode", "enabled")
    surfaces = {i["surface"] for i in enabled["files"]}
    assert "litellm-switchboard" in surfaces
    switchboard = next(i for i in enabled["files"] if i["surface"] == "litellm-switchboard")
    assert "model_name: ods/current" in switchboard["content"]
    assert "model_name: local" in switchboard["content"]
    assert "model_name: default" in switchboard["content"]
    assert 'model_name: "*"' in switchboard["content"]
    assert "api_base: http://model-router:9099/v1" in switchboard["content"]
    assert switchboard["content"].count("http://model-router:9099/v1") == 4


def test_enabled_env_exports_switchboard_webui_gateway() -> None:
    payload = run_renderer(
        "--surface",
        "env",
        "--switchboard-mode",
        "enabled",
        "--litellm-key",
        "sk-test-litellm",
    )
    content = file_by_surface(payload, "env")["content"]
    assert "ODS_MODEL_SWITCHBOARD=enabled" in content
    assert "OPEN_WEBUI_LLM_BASE_URL=http://litellm:4000" in content
    assert "OPEN_WEBUI_LLM_API_KEY=sk-test-litellm" in content


def test_enabled_perplexica_uses_stable_alias() -> None:
    payload = run_renderer(
        "--surface",
        "perplexica",
        "--switchboard-mode",
        "enabled",
        "--ods-mode",
        "lemonade",
        "--gpu-backend",
        "amd",
        "--gguf-file",
        "Concrete.gguf",
        "--litellm-key",
        "sk-test-litellm",
    )
    content = json.loads(file_by_surface(payload, "perplexica")["content"])
    openai_provider = model_provider_by_id(content, "openai")
    assert content["preferences"]["defaultChatModel"] == "ods/current"
    assert openai_provider["config"]["baseURL"] == "http://litellm:4000/v1"
    assert openai_provider["config"]["apiKey"] == "sk-test-litellm"
    assert openai_provider["chatModels"][0]["key"] == "ods/current"


def test_enabled_hermes_uses_stable_switchboard_alias() -> None:
    payload = run_renderer(
        "--surface",
        "hermes",
        "--switchboard-mode",
        "enabled",
        "--gguf-file",
        "Raw-Runtime.gguf",
        "--llm-base-url",
        "http://llama-server:8080/v1",
    )
    content = file_by_surface(payload, "hermes")["content"]
    assert 'default: "ods/current"' in content
    assert "Raw-Runtime.gguf" not in content
    assert 'base_url: "http://litellm:4000/v1"' in content


def test_enabled_opencode_uses_stable_switchboard_alias() -> None:
    payload = run_renderer(
        "--surface",
        "opencode",
        "--switchboard-mode",
        "enabled",
        "--litellm-key",
        "switch-secret",
    )
    content = json.loads(file_by_surface(payload, "opencode")["content"])
    assert content["model"] == "ods/current"
    assert content["baseURL"] == "http://litellm:4000/v1"
    assert content["apiKey"] == "switch-secret"


def test_router_endpoints_strip_trailing_v1() -> None:
    payload = run_renderer(
        "--surface", "model-router-endpoints",
        "--llm-base-url", "http://llama-server:8080/v1",
        "--gpu-backend", "amd",
        "--lemonade-api-base", "http://lemonade:8000/api/v1",
    )
    import json as _json
    content = _json.loads(payload["files"][0]["content"])
    by_id = {e["id"]: e["baseUrl"] for e in content["endpoints"]}
    assert by_id["llama-server-default"] == "http://llama-server:8080"
    assert by_id["lemonade-default"] == "http://lemonade:8000/api"


def test_lemonade_disables_thinking_and_uses_extra_alias() -> None:
    payload = run_renderer(
        "--surface",
        "litellm-lemonade",
        "--ods-mode",
        "lemonade",
        "--gpu-backend",
        "amd",
        "--gguf-file",
        "Model.gguf",
        "--litellm-key",
        "sk-test",
    )
    content = file_by_surface(payload, "litellm-lemonade")["content"]
    assert "model: openai/extra.Model.gguf" in content
    assert "api_key: sk-test" in content
    assert "enable_thinking: false" in content


def test_external_lemonade_uses_supplied_model_and_api_base() -> None:
    payload = run_renderer(
        "--surface",
        "litellm-lemonade",
        "--ods-mode",
        "lemonade",
        "--gpu-backend",
        "amd",
        "--lemonade-model-id",
        "Qwen3-0.6B-GGUF",
        "--lemonade-api-base",
        "http://host.docker.internal:13305/api/v1",
        "--litellm-key",
        "lemonade-secret",
    )
    content = file_by_surface(payload, "litellm-lemonade")["content"]
    assert "model: openai/Qwen3-0.6B-GGUF" in content
    assert "api_base: http://host.docker.internal:13305/api/v1" in content
    assert "api_key: lemonade-secret" in content


def test_exact_lemonade_id_propagates_to_every_runtime_surface() -> None:
    payload = run_renderer(
        "--surface",
        "all",
        "--ods-mode",
        "lemonade",
        "--gpu-backend",
        "amd",
        "--gguf-file",
        "Modern-Model.gguf",
        "--lemonade-model-id",
        "Modern-Model",
    )

    env_content = file_by_surface(payload, "env")["content"]
    litellm_content = file_by_surface(payload, "litellm-lemonade")["content"]
    hermes_content = file_by_surface(payload, "hermes")["content"]
    opencode = json.loads(file_by_surface(payload, "opencode")["content"])
    perplexica = json.loads(file_by_surface(payload, "perplexica")["content"])

    assert "LEMONADE_MODEL=Modern-Model" in env_content
    assert "model: openai/Modern-Model" in litellm_content
    assert 'default: "Modern-Model"' in hermes_content
    assert opencode["model"] == "Modern-Model"
    assert perplexica["preferences"]["defaultChatModel"] == "Modern-Model"


def test_amd_local_env_does_not_invent_a_lemonade_model() -> None:
    payload = run_renderer(
        "--surface",
        "env",
        "--ods-mode",
        "local",
        "--gpu-backend",
        "amd",
        "--gguf-file",
        "Fallback-Model.gguf",
    )

    env_content = file_by_surface(payload, "env")["content"]
    assert "LEMONADE_MODEL=\n" in env_content
    assert "LEMONADE_MODEL=extra.Fallback-Model.gguf" not in env_content


def test_hermes_uses_lemonade_model_id_for_amd() -> None:
    payload = run_renderer(
        "--surface",
        "hermes",
        "--ods-mode",
        "lemonade",
        "--gpu-backend",
        "amd",
        "--gguf-file",
        "Amd.gguf",
        "--llm-base-url",
        "http://litellm:4000/v1",
        "--context-length",
        "65536",
    )
    content = file_by_surface(payload, "hermes")["content"]
    assert 'default: "extra.Amd.gguf"' in content
    assert 'base_url: "http://litellm:4000/v1"' in content
    assert "context_length: 65536" in content


def test_perplexica_default_model_matches_route() -> None:
    payload = run_renderer(
        "--surface",
        "perplexica",
        "--ods-mode",
        "lemonade",
        "--gpu-backend",
        "amd",
        "--gguf-file",
        "Research.gguf",
    )
    content = json.loads(file_by_surface(payload, "perplexica")["content"])
    openai_provider = model_provider_by_id(content, "openai")
    assert content["preferences"]["defaultChatModel"] == "extra.Research.gguf"
    assert openai_provider["chatModels"][0]["name"] == "extra.Research.gguf"


def test_write_mode_writes_under_output_root() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--surface",
                "litellm-lemonade",
                "--ods-mode",
                "lemonade",
                "--gpu-backend",
                "amd",
                "--gguf-file",
                "Written.gguf",
                "--output-root",
                tmp,
                "--write",
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        payload = json.loads(proc.stdout)
        target = Path(tmp) / "config" / "litellm" / "lemonade.yaml"
        assert payload["mode"] == "write"
        assert target.exists()
        assert "openai/extra.Written.gguf" in target.read_text(encoding="utf-8")


def main() -> int:
    tests = [
        test_all_surfaces_render,
        test_switchboard_surface_gated_on_enabled_mode,
        test_enabled_env_exports_switchboard_webui_gateway,
        test_enabled_perplexica_uses_stable_alias,
        test_enabled_hermes_uses_stable_switchboard_alias,
        test_enabled_opencode_uses_stable_switchboard_alias,
        test_lemonade_disables_thinking_and_uses_extra_alias,
        test_external_lemonade_uses_supplied_model_and_api_base,
        test_exact_lemonade_id_propagates_to_every_runtime_surface,
        test_amd_local_env_does_not_invent_a_lemonade_model,
        test_hermes_uses_lemonade_model_id_for_amd,
        test_perplexica_default_model_matches_route,
        test_write_mode_writes_under_output_root,
    ]
    for test in tests:
        test()
        print(f"[PASS] {test.__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
