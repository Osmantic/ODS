#!/usr/bin/env python3
"""Tests for scripts/render-runtime-configs.py."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "render-runtime-configs.py"


def load_renderer_module() -> ModuleType:
    name = "ods_render_runtime_configs_test"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


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


def test_external_model_uses_authenticated_gateway_without_vendor_impersonation() -> None:
    result = run_renderer("--surface", "litellm-external", "--model", "owner/model-q4",
                          "--llm-base-url", "http://10.0.2.2:18080")
    rendered = file_by_surface(result, "litellm-external")
    assert rendered["path"] == "config/litellm/local.yaml"
    config = rendered["content"]
    assert 'model_name: "ods/current"' in config
    assert config.count('model: "openai/owner/model-q4"') == 4
    assert config.count('api_base: "http://10.0.2.2:18080/v1"') == 4
    assert "master_key: os.environ/LITELLM_MASTER_KEY" in config
    assert "enable_thinking" not in config  # Do not invent backend-specific capabilities.
    assert config.count("api_key: not-needed") == 4


def test_external_gateway_rejects_credentialed_or_malformed_bases() -> None:
    for base in ("file:///tmp/model", "http://user:secret@host/v1", "http://host?key=x",
                 "http://host:99999", "http://host/v1#fragment", "http://host\n/v1"):
        result = subprocess.run([sys.executable, str(SCRIPT), "--surface", "litellm-external",
                                 "--llm-base-url", base], capture_output=True, text=True)
        assert result.returncode != 0, base
    result = run_renderer("--surface", "litellm-external", "--model", 'owner/"model',
                          "--llm-base-url", "http://[::1]:18080/v1")
    content = file_by_surface(result, "litellm-external")["content"]
    assert 'model: ' + json.dumps('openai/owner/"model') in content


def test_external_gateway_uses_runtime_key_reference_when_authenticated() -> None:
    result = run_renderer(
        "--surface", "litellm-external", "--model", "test-model",
        "--llm-base-url", "https://upstream.example/v1",
        "--external-llm-authenticated",
    )
    config = file_by_surface(result, "litellm-external")["content"]
    assert config.count("api_key: os.environ/EXTERNAL_LLM_API_KEY") == 4
    assert "api_key: not-needed" not in config
    assert "test-secret-123" not in config


def test_all_surfaces_render() -> None:
    payload = run_renderer("--surface", "all")
    surfaces = {item["surface"] for item in payload["files"]}
    assert surfaces == {
        "env", "opencode", "litellm-local", "perplexica", "hermes",
        "model-router-endpoints", "litellm-switchboard",
    }
    assert payload["mode"] == "dry-run"


def test_fresh_default_uses_stable_switchboard_alias() -> None:
    payload = run_renderer("--surface", "all")
    assert payload["inputs"]["switchboard_mode"] == "enabled"
    switchboard = file_by_surface(payload, "litellm-switchboard")["content"]
    assert "api_base: http://model-router:9099/v1" in switchboard


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


def test_switchboard_route_injects_no_thinking_settings() -> None:
    # Any-model WP4.5: a model's thinking control reaches only the consumer that
    # sends per-request kwargs (Pixel's rendered config). The switchboard route
    # must pass requests through unchanged, whatever model is active.
    enabled = run_renderer("--surface", "all", "--switchboard-mode", "enabled")
    switchboard = file_by_surface(enabled, "litellm-switchboard")["content"]
    shipped = (ROOT / "config" / "litellm" / "switchboard.yaml").read_text(encoding="utf-8")
    for content in (switchboard, shipped):
        assert "enable_thinking" not in content
        assert "chat_template_kwargs" not in content
        assert "reasoning_effort" not in content


def test_local_profiles_allow_long_agent_streams() -> None:
    local = run_renderer("--surface", "litellm-local")
    local_config = file_by_surface(local, "litellm-local")["content"]
    assert "request_timeout: 900" in local_config
    assert "stream_timeout: 900" in local_config
    assert local_config.count("enable_thinking: false") == 3

    hybrid = run_renderer("--surface", "litellm-hybrid", "--ods-mode", "hybrid")
    hybrid_config = file_by_surface(hybrid, "litellm-hybrid")["content"]
    assert "request_timeout: 900" in hybrid_config
    assert "stream_timeout: 900" in hybrid_config
    assert hybrid_config.count("enable_thinking: false") == 3


def test_all_selects_one_mode_config() -> None:
    expected = {
        "local": "litellm-local",
        "cloud": "litellm-cloud",
        "hybrid": "litellm-hybrid",
        # One release: the retired managed-AMD mode renders exactly as local.
        "lemonade": "litellm-local",
    }
    all_mode_surfaces = set(expected.values())
    for mode, expected_surface in expected.items():
        payload = run_renderer("--surface", "all", "--ods-mode", mode)
        surfaces = {item["surface"] for item in payload["files"]}
        assert surfaces & all_mode_surfaces == {expected_surface}
        assert "litellm-lemonade" not in surfaces
        mode_config = file_by_surface(payload, expected_surface)["content"]
        assert "model_name: ods/current" in mode_config
        env_content = file_by_surface(payload, "env")["content"]
        assert f"ODS_MODE={'local' if mode == 'lemonade' else mode}\n" in env_content


def test_cloud_enabled_never_renders_local_switchboard() -> None:
    payload = run_renderer(
        "--surface",
        "all",
        "--ods-mode",
        "cloud",
        "--switchboard-mode",
        "enabled",
    )
    surfaces = {item["surface"] for item in payload["files"]}
    assert "litellm-cloud" in surfaces
    assert "litellm-switchboard" not in surfaces
    cloud = file_by_surface(payload, "litellm-cloud")["content"]
    assert "model_name: ods/current" in cloud
    assert "model-router" not in cloud


def test_remote_cloud_projection_uses_internal_egress_and_state_receipt() -> None:
    payload = run_renderer(
        "--surface",
        "all",
        "--ods-mode",
        "cloud",
        "--remote-llm-enabled",
        "true",
        "--remote-llm-transport",
        "direct",
        "--remote-llm-base-url",
        "https://gpu.example.test",
        "--remote-llm-model",
        "qwen/remote:latest",
    )
    surfaces = {item["surface"] for item in payload["files"]}
    assert "litellm-cloud" in surfaces
    assert "remote-routing-state" in surfaces
    assert "litellm-switchboard" not in surfaces

    cloud = file_by_surface(payload, "litellm-cloud")["content"]
    assert "model_name: ods/current" in cloud
    assert 'model: "openai/qwen/remote:latest"' in cloud
    assert 'model_name: "qwen/remote:latest"' in cloud
    assert 'api_base: "http://remote-provider-egress:8091/v1"' in cloud
    # The egress admits only the LiteLLM gateway key (GHSA-4rpc); the value
    # stays in LiteLLM's environment, never in the rendered file.
    assert cloud.count("api_key: os.environ/LITELLM_MASTER_KEY") == 3
    assert "not-needed" not in cloud
    assert "https://gpu.example.test" not in cloud
    assert "REMOTE_LLM_API_KEY" not in cloud

    env_content = file_by_surface(payload, "env")["content"]
    assert "REMOTE_LLM_ENABLED=true" in env_content
    assert "REMOTE_LLM_TRANSPORT=direct" in env_content
    assert "REMOTE_LLM_BASE_URL=https://gpu.example.test/v1" in env_content
    assert "REMOTE_LLM_MODEL=qwen/remote:latest" in env_content

    state = json.loads(file_by_surface(payload, "remote-routing-state")["content"])
    assert state["schema"] == "ods.remote-routing-state.v1"
    assert state["enabled"] is True
    assert state["mode"] == "cloud"
    assert state["provider"] == {
        "baseUrl": "https://gpu.example.test/v1",
        "capability": "openai-compatible",
        "model": "qwen/remote:latest",
        "transport": "direct",
    }
    assert state["projection"] == {
        "consumerRoute": "gateway",
        "egressBaseUrl": "http://remote-provider-egress:8091/v1",
        "gateway": "litellm-cloud",
        "publicModel": "ods/current",
    }
    assert state["status"] == {
        "proven": False,
        "reason": "pending-provider-handshake",
    }
    assert "key" not in json.dumps(state).lower()


def test_remote_routing_state_disabled_receipt_has_no_provider() -> None:
    payload = run_renderer("--surface", "remote-routing-state")
    state = json.loads(file_by_surface(payload, "remote-routing-state")["content"])
    assert state["enabled"] is False
    assert state["provider"] is None
    assert state["status"] == {"proven": False, "reason": "disabled"}
    assert "REMOTE_LLM_API_KEY" not in json.dumps(state)


def test_remote_projection_requires_cloud_mode() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--surface",
            "all",
            "--ods-mode",
            "local",
            "--remote-llm-enabled",
            "true",
            "--remote-llm-transport",
            "direct",
            "--remote-llm-base-url",
            "https://gpu.example.test/v1",
            "--remote-llm-model",
            "qwen-remote",
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.returncode == 2
    assert "ODS_MODE=cloud" in proc.stderr


def test_remote_projection_rejects_unsafe_model_id() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--surface",
            "all",
            "--ods-mode",
            "cloud",
            "--remote-llm-enabled",
            "true",
            "--remote-llm-transport",
            "direct",
            "--remote-llm-base-url",
            "https://gpu.example.test/v1",
            "--remote-llm-model",
            "bad model; touch nope",
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.returncode == 2
    assert "model id without spaces" in proc.stderr


def test_explicit_cloud_switchboard_render_fails_closed() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--surface",
            "litellm-switchboard",
            "--ods-mode",
            "cloud",
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.returncode == 2
    assert "local-runtime-only" in proc.stderr


def test_native_local_projection_uses_host_route_and_concrete_model() -> None:
    payload = run_renderer(
        "--surface",
        "litellm-local-native",
        "--ods-mode",
        "local",
        "--gguf-file",
        "Native-Model.gguf",
        "--llm-base-url",
        "http://host.docker.internal:13306/v1",
    )
    content = file_by_surface(payload, "litellm-local-native")["content"]
    assert "model_name: ods/current" in content
    assert "model: openai/Native-Model.gguf" in content
    assert "api_base: http://host.docker.internal:13306/v1" in content
    assert "enable_thinking: false" in content
    assert "request_timeout: 900" in content
    assert "stream_timeout: 900" in content
    # An unkeyed host-native server keeps the placeholder key.
    assert content.count("api_key: not-needed") == 3


def test_native_local_projection_references_the_runtime_key_by_name() -> None:
    secret = "5e" * 32
    env = os.environ.copy()
    env["LLAMA_SERVER_API_KEY"] = secret
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--surface", "litellm-local-native", "--gguf-file", "Native-Model.gguf",
         "--llm-base-url", "http://host.docker.internal:18080/v1", "--llm-api-key-env", "LLAMA_SERVER_API_KEY"],
        cwd=ROOT, env=env, text=True, capture_output=True, check=True,
    )
    content = file_by_surface(json.loads(proc.stdout), "litellm-local-native")["content"]
    assert content.count("api_key: os.environ/LLAMA_SERVER_API_KEY") == 3
    assert "not-needed" not in content
    assert secret not in proc.stdout + proc.stderr


def test_api_key_env_must_be_a_variable_name() -> None:
    for name in ("llama_server_api_key", "KEY=value", "$(touch x)", "5E" * 40):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--surface", "model-router-endpoints", "--llm-api-key-env", name],
            cwd=ROOT, text=True, capture_output=True,
        )
        assert proc.returncode != 0, name
        assert "environment variable name" in proc.stderr


def test_keyed_host_native_consumers_use_the_gateway() -> None:
    payload = run_renderer(
        "--surface", "all", "--switchboard-mode", "observe", "--gpu-backend", "amd",
        "--gguf-file", "Native-Model.gguf", "--model", "native-model",
        "--llm-base-url", "http://host.docker.internal:18080/v1",
        "--llm-api-key-env", "LLAMA_SERVER_API_KEY", "--litellm-key", "sk-gateway",
    )
    # Consumers cannot hold the runtime key; they reach it through LiteLLM.
    opencode = json.loads(file_by_surface(payload, "opencode")["content"])
    assert opencode["baseURL"] == "http://litellm:4000/v1"
    assert opencode["apiKey"] == "sk-gateway"
    perplexica = json.loads(file_by_surface(payload, "perplexica")["content"])
    openai_provider = model_provider_by_id(perplexica, "openai")
    assert openai_provider["config"]["baseURL"] == "http://litellm:4000/v1"
    assert perplexica["preferences"]["defaultChatModel"] == "Native-Model.gguf"
    endpoints = json.loads(file_by_surface(payload, "model-router-endpoints")["content"])
    assert endpoints == {"endpoints": [{"id": "llama-server-default",
                                        "baseUrl": "http://host.docker.internal:18080",
                                        "apiKeyEnv": "LLAMA_SERVER_API_KEY"}]}


def test_checked_in_mode_configs_match_renderer() -> None:
    for mode in ("local", "cloud", "hybrid"):
        payload = run_renderer("--surface", f"litellm-{mode}", "--ods-mode", mode)
        rendered = file_by_surface(payload, f"litellm-{mode}")["content"]
        checked_in = (ROOT / "config" / "litellm" / f"{mode}.yaml").read_text(
            encoding="utf-8"
        )
        assert rendered == checked_in


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
        # Retired flag, accepted and ignored for one release (R13).
        "--lemonade-api-base", "http://lemonade:8000/api/v1",
    )
    content = json.loads(payload["files"][0]["content"])
    # One managed runtime, one endpoint: no lemonade-default row on AMD.
    assert content == {"endpoints": [{"id": "llama-server-default", "baseUrl": "http://llama-server:8080"}]}


def test_retired_lemonade_flags_are_accepted_and_ignored() -> None:
    payload = run_renderer(
        "--surface",
        "all",
        "--switchboard-mode",
        "observe",
        "--ods-mode",
        "lemonade",
        "--gpu-backend",
        "amd",
        "--gguf-file",
        "Modern-Model.gguf",
        "--model",
        "modern-model",
        "--lemonade-model-id",
        "extra.Modern-Model.gguf",
        "--lemonade-api-base",
        "http://host.docker.internal:13305/api/v1",
    )

    surfaces = {item["surface"] for item in payload["files"]}
    env_content = file_by_surface(payload, "env")["content"]
    hermes_content = file_by_surface(payload, "hermes")["content"]
    opencode = json.loads(file_by_surface(payload, "opencode")["content"])
    perplexica = json.loads(file_by_surface(payload, "perplexica")["content"])

    assert "litellm-lemonade" not in surfaces and "litellm-local" in surfaces
    assert "ODS_MODE=local\n" in env_content
    assert "LLM_BACKEND=llama-server\n" in env_content
    assert "LEMONADE" not in env_content
    assert 'default: "Modern-Model.gguf"' in hermes_content
    assert opencode["model"] == "modern-model"
    assert perplexica["preferences"]["defaultChatModel"] == "Modern-Model.gguf"
    assert "extra." not in json.dumps(payload)
    assert "13305" not in json.dumps(payload)


def test_litellm_lemonade_surface_is_retired() -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--surface", "litellm-lemonade"],
        cwd=ROOT, text=True, capture_output=True,
    )
    assert proc.returncode == 2
    assert "litellm-lemonade" in proc.stderr


def test_amd_local_env_has_no_lemonade_keys() -> None:
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
    assert "LEMONADE" not in env_content
    assert "LLM_BACKEND=llama-server\n" in env_content
    assert "GGUF_FILE=Fallback-Model.gguf\n" in env_content


def test_hermes_uses_the_gguf_alias_for_amd() -> None:
    payload = run_renderer(
        "--surface",
        "hermes",
        "--switchboard-mode",
        "observe",
        "--ods-mode",
        "local",
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
    assert 'default: "Amd.gguf"' in content
    assert 'base_url: "http://litellm:4000/v1"' in content
    assert "context_length: 65536" in content
    assert "max_tokens: 1024" in content


def test_perplexica_default_model_matches_route() -> None:
    payload = run_renderer(
        "--surface",
        "perplexica",
        "--switchboard-mode",
        "observe",
        "--ods-mode",
        "local",
        "--gpu-backend",
        "amd",
        "--gguf-file",
        "Research.gguf",
    )
    content = json.loads(file_by_surface(payload, "perplexica")["content"])
    openai_provider = model_provider_by_id(content, "openai")
    assert content["preferences"]["defaultChatModel"] == "Research.gguf"
    assert openai_provider["chatModels"][0]["name"] == "Research.gguf"


def test_write_mode_writes_under_output_root() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--surface",
                "litellm-local-native",
                "--gpu-backend",
                "amd",
                "--gguf-file",
                "Written.gguf",
                "--output-root",
                tmp,
                "--write",
                "--format",
                "json",
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        payload = json.loads(proc.stdout)
        target = Path(tmp) / "config" / "litellm" / "local.yaml"
        assert payload["mode"] == "write"
        assert target.exists()
        assert "openai/Written.gguf" in target.read_text(encoding="utf-8")
        if os.name != "nt":
            assert target.stat().st_mode & 0o777 == 0o644
        assert not list(target.parent.glob(f".{target.name}.*.tmp"))


def test_model_router_allowlist_mount_is_readable_after_private_source_staging() -> None:
    if os.name == "nt":
        return  # POSIX host modes are the contract Docker bind mounts preserve.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        endpoint_dir = root / "config" / "model-router"
        endpoint_dir.mkdir(parents=True)
        endpoint = endpoint_dir / "endpoints.json"
        endpoint.write_text('{"endpoints": []}\n', encoding="utf-8")
        endpoint_dir.chmod(0o700)
        endpoint.chmod(0o600)
        private_dir = root / "config" / "private"
        private_dir.mkdir()
        private_file = private_dir / "key.txt"
        private_file.write_text("private\n", encoding="utf-8")
        private_dir.chmod(0o700)
        private_file.chmod(0o600)

        for _ in range(2):  # Activation re-renders the same mounted path.
            run_renderer("--surface", "model-router-endpoints", "--output-root",
                         tmp, "--write", "--format", "json")
            assert endpoint_dir.stat().st_mode & 0o777 == 0o711
            assert endpoint.stat().st_mode & 0o777 == 0o644
            assert json.loads(endpoint.read_text(encoding="utf-8"))["endpoints"][0]["id"] == "llama-server-default"
            assert private_dir.stat().st_mode & 0o777 == 0o700
            assert private_file.stat().st_mode & 0o777 == 0o600
            endpoint_dir.chmod(0o700)
            endpoint.chmod(0o600)


def test_model_router_allowlist_rejects_embedded_credentials_before_public_write() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--surface", "model-router-endpoints",
             "--llm-base-url", "http://user:secret@localhost:8080/v1",
             "--output-root", tmp, "--write"],
            cwd=ROOT, text=True, capture_output=True,
        )
        assert proc.returncode != 0
        assert "secret" not in proc.stderr
        assert not (Path(tmp) / "config" / "model-router" / "endpoints.json").exists()


def test_model_router_allowlist_does_not_widen_symlinked_directory() -> None:
    if os.name == "nt":
        return
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        private = root / "private"
        private.mkdir(mode=0o700)
        config = root / "config"
        config.mkdir()
        (config / "model-router").symlink_to(private, target_is_directory=True)
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--surface", "model-router-endpoints",
             "--output-root", tmp, "--write"], cwd=ROOT, text=True,
            capture_output=True,
        )
        assert proc.returncode != 0
        assert private.stat().st_mode & 0o777 == 0o700
        assert not (private / "endpoints.json").exists()


def test_write_cli_defaults_to_secret_free_paths() -> None:
    secret = "renderer-secret-must-not-reach-output"
    with tempfile.TemporaryDirectory() as tmp:
        env = os.environ.copy()
        env["ODS_RENDER_LITELLM_KEY"] = secret
        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--surface",
                "opencode",
                "--switchboard-mode",
                "enabled",
                "--gpu-backend",
                "amd",
                "--gguf-file",
                "Private.gguf",
                "--output-root",
                tmp,
                "--write",
            ],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        combined = proc.stdout + proc.stderr
        target = Path(tmp) / ".opencode" / "auth.json"
        assert secret not in combined
        assert proc.stdout.strip() == ".opencode/auth.json"
        assert secret in target.read_text(encoding="utf-8")


def test_atomic_write_failure_preserves_known_good_config() -> None:
    renderer = load_renderer_module()
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "local.yaml"
        target.write_text("known-good\n", encoding="utf-8")
        original_replace = renderer.os.replace
        original_sleep = renderer.time.sleep

        def fail_replace(*_args, **_kwargs) -> None:
            raise PermissionError("injected replace failure")

        renderer.os.replace = fail_replace
        renderer.time.sleep = lambda _seconds: None
        try:
            try:
                renderer.atomic_write_text(target, "new-route\n")
            except PermissionError as exc:
                assert "injected replace failure" in str(exc)
            else:
                raise AssertionError("fault injection did not fail the replace")
        finally:
            renderer.os.replace = original_replace
            renderer.time.sleep = original_sleep

        assert target.read_text(encoding="utf-8") == "known-good\n"
        assert not list(target.parent.glob(f".{target.name}.*.tmp"))


def test_identical_switchboard_roundtrip_preserves_bound_inode() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "config/litellm/switchboard.yaml"
        def render(model):
            subprocess.run([sys.executable, str(SCRIPT), "--surface", "litellm-switchboard",
                            "--model", model, "--output-root", tmp, "--write"],
                           check=True, stdout=subprocess.DEVNULL)
        render("qwen3.5-9b")
        before = target.stat()
        with target.open("rb") as bound:
            content = bound.read()
            render("qwen3.5-2b")
            render("qwen3.5-9b")
            after = target.stat()
            assert (before.st_dev, before.st_ino, before.st_mtime_ns) == (after.st_dev, after.st_ino, after.st_mtime_ns)
            assert target.read_bytes() == content
            assert os.fstat(bound.fileno()).st_nlink == 1


def test_changed_bytes_and_modes_still_replace_atomically() -> None:
    renderer = load_renderer_module()
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "config"
        renderer.atomic_write_text(target, "old\n")
        with target.open("rb") as bound:
            renderer.atomic_write_text(target, "new\n")
            assert bound.read() == b"old\n"
            assert target.read_bytes() == b"new\n"
        if os.name != "nt":
            target.chmod(0o600)
            renderer.atomic_write_text(target, "new\n", file_mode=0o644)
            assert target.stat().st_mode & 0o777 == 0o644


def test_identical_symlink_and_hardlink_do_not_skip_replacement() -> None:
    if os.name == "nt":
        return
    renderer = load_renderer_module()
    with tempfile.TemporaryDirectory() as tmp:
        original = Path(tmp) / "original"
        original.write_text("same\n")
        target = Path(tmp) / "target"
        target.symlink_to(original)
        renderer.atomic_write_text(target, "same\n")
        assert not target.is_symlink()
        target.unlink()
        os.link(original, target)
        renderer.atomic_write_text(target, "same\n")
        assert original.stat().st_ino != target.stat().st_ino
        assert original.read_text() == "same\n"


def test_validation_rejects_negative_context_or_invalid_port() -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--context-length", "-10"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.returncode != 0
    assert "context length must be positive" in proc.stderr

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--opencode-port", "70000"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.returncode != 0
    assert "opencode port must be between 1 and 65535" in proc.stderr


def test_validation_rejects_control_characters_in_model_and_key() -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--model", "qwen\ninjected=true"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.returncode != 0
    assert "cannot contain control characters or newlines" in proc.stderr


def main() -> int:
    tests = [
        test_external_model_uses_authenticated_gateway_without_vendor_impersonation,
        test_external_gateway_rejects_credentialed_or_malformed_bases,
        test_all_surfaces_render,
        test_switchboard_surface_gated_on_enabled_mode,
        test_local_profiles_allow_long_agent_streams,
        test_all_selects_one_mode_config,
        test_cloud_enabled_never_renders_local_switchboard,
        test_remote_cloud_projection_uses_internal_egress_and_state_receipt,
        test_remote_routing_state_disabled_receipt_has_no_provider,
        test_remote_projection_requires_cloud_mode,
        test_remote_projection_rejects_unsafe_model_id,
        test_explicit_cloud_switchboard_render_fails_closed,
        test_native_local_projection_uses_host_route_and_concrete_model,
        test_native_local_projection_references_the_runtime_key_by_name,
        test_api_key_env_must_be_a_variable_name,
        test_keyed_host_native_consumers_use_the_gateway,
        test_checked_in_mode_configs_match_renderer,
        test_enabled_env_exports_switchboard_webui_gateway,
        test_enabled_perplexica_uses_stable_alias,
        test_enabled_hermes_uses_stable_switchboard_alias,
        test_enabled_opencode_uses_stable_switchboard_alias,
        test_router_endpoints_strip_trailing_v1,
        test_retired_lemonade_flags_are_accepted_and_ignored,
        test_litellm_lemonade_surface_is_retired,
        test_amd_local_env_has_no_lemonade_keys,
        test_hermes_uses_the_gguf_alias_for_amd,
        test_perplexica_default_model_matches_route,
        test_write_mode_writes_under_output_root,
        test_write_cli_defaults_to_secret_free_paths,
        test_atomic_write_failure_preserves_known_good_config,
        test_identical_switchboard_roundtrip_preserves_bound_inode,
        test_changed_bytes_and_modes_still_replace_atomically,
        test_identical_symlink_and_hardlink_do_not_skip_replacement,
        test_validation_rejects_negative_context_or_invalid_port,
        test_validation_rejects_control_characters_in_model_and_key,
    ]
    for test in tests:
        test()
        print(f"[PASS] {test.__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
