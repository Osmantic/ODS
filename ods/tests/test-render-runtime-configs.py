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


def test_all_surfaces_render() -> None:
    payload = run_renderer("--surface", "all")
    surfaces = {item["surface"] for item in payload["files"]}
    assert surfaces == {
        "env", "opencode", "litellm-local", "perplexica", "hermes",
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


def test_all_selects_one_mode_config() -> None:
    expected = {
        "local": "litellm-local",
        "cloud": "litellm-cloud",
        "hybrid": "litellm-hybrid",
        "lemonade": "litellm-lemonade",
    }
    all_mode_surfaces = set(expected.values())
    for mode, expected_surface in expected.items():
        payload = run_renderer("--surface", "all", "--ods-mode", mode)
        surfaces = {item["surface"] for item in payload["files"]}
        assert surfaces & all_mode_surfaces == {expected_surface}


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
    assert "api_key: not-needed" in cloud
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
    assert "model: openai/Native-Model.gguf" in content
    assert "api_base: http://host.docker.internal:13306/v1" in content
    assert "enable_thinking: false" in content
    assert "request_timeout: 900" in content
    assert "stream_timeout: 900" in content


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
    assert "max_tokens: 1024" in content


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
        if os.name != "nt":
            assert target.stat().st_mode & 0o777 == 0o644
        assert not list(target.parent.glob(f".{target.name}.*.tmp"))


def test_atomic_write_failure_preserves_known_good_config() -> None:
    renderer = load_renderer_module()
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "lemonade.yaml"
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


def test_write_path_routes_through_the_atomic_replace() -> None:
    """The --write loop itself must not truncate a live config in place.

    config/litellm/<mode>.yaml and config/model-router/endpoints.json are
    bind-mounted into running containers, so a re-render that truncates first
    leaves a window where the container reads an empty or partial file. The
    helper being atomic is not enough — the write loop has to actually use it,
    and a regression back to a plain write_text would still produce the same
    final bytes, the same 0644 mode and no temp leftovers. Failing the replace
    is what separates the two: atomic leaves the previous file intact, an
    in-place write has already destroyed it.
    """
    renderer = load_renderer_module()
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "config" / "model-router" / "endpoints.json"
        target.parent.mkdir(parents=True)
        target.write_text('{"endpoints": ["known-good"]}\n', encoding="utf-8")

        args = renderer.parse_args([
            "--surface", "model-router-endpoints",
            "--switchboard-mode", "enabled",
            "--output-root", tmp,
            "--write",
        ])
        original_replace = renderer.os.replace
        original_sleep = renderer.time.sleep

        def fail_replace(*_args, **_kwargs) -> None:
            raise PermissionError("injected replace failure")

        renderer.os.replace = fail_replace
        renderer.time.sleep = lambda _seconds: None
        try:
            try:
                renderer.render(args)
            except PermissionError as exc:
                assert "injected replace failure" in str(exc)
            else:
                raise AssertionError(
                    "the write loop did not go through the atomic replace"
                )
        finally:
            renderer.os.replace = original_replace
            renderer.time.sleep = original_sleep

        assert target.read_text(encoding="utf-8") == '{"endpoints": ["known-good"]}\n'
        assert not list(target.parent.glob(f".{target.name}.*.tmp"))


def main() -> int:
    tests = [
        test_all_surfaces_render,
        test_switchboard_surface_gated_on_enabled_mode,
        test_all_selects_one_mode_config,
        test_cloud_enabled_never_renders_local_switchboard,
        test_remote_cloud_projection_uses_internal_egress_and_state_receipt,
        test_remote_routing_state_disabled_receipt_has_no_provider,
        test_remote_projection_requires_cloud_mode,
        test_remote_projection_rejects_unsafe_model_id,
        test_explicit_cloud_switchboard_render_fails_closed,
        test_native_local_projection_uses_host_route_and_concrete_model,
        test_checked_in_mode_configs_match_renderer,
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
        test_atomic_write_failure_preserves_known_good_config,
        test_write_path_routes_through_the_atomic_replace,
    ]
    for test in tests:
        test()
        print(f"[PASS] {test.__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
