#!/usr/bin/env python3
"""Static contract tests for Perplexica's ODS entrypoint patch."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

try:
    import pytest
except ModuleNotFoundError:
    pytest = None


ROOT = Path(__file__).resolve().parents[1]
ENV_SCHEMA = ROOT / ".env.schema.json"
SERVICE_DIR = ROOT / "extensions" / "services" / "perplexica"
COMPOSE = SERVICE_DIR / "compose.yaml"
MANIFEST = SERVICE_DIR / "manifest.yaml"
ENTRYPOINT = SERVICE_DIR / "docker-entrypoint.sh"
SYNC_SCRIPT = SERVICE_DIR / "sync-model-config.js"
SEARCH_SYNC_SCRIPT = SERVICE_DIR / "sync-search-config.js"
WHISPER_COMPOSE = ROOT / "extensions" / "services" / "whisper" / "compose.yaml"
BRAVE_DIR = ROOT / "extensions" / "services" / "brave-search"
HEALTH_PHASE = ROOT / "installers" / "phases" / "12-health.sh"
SUMMARY_PHASE = ROOT / "installers" / "phases" / "13-summary.sh"
REPAIR_SCRIPT = ROOT / "scripts" / "repair" / "repair-perplexica.sh"


def _node_cmd_or_skip() -> str | None:
    node = shutil.which("node")
    if node:
        return node
    if pytest is not None:
        pytest.skip("Node.js is required")
    print("[SKIP] Node.js is required")
    return None


def _bash_cmd_or_skip() -> str | None:
    bash = shutil.which("bash")
    if bash:
        return bash
    if pytest is not None:
        pytest.skip("bash is required")
    print("[SKIP] bash is required")
    return None


def _slice_block(path: Path, start_marker: str, end_line: str) -> str:
    """Return the shell block starting at `start_marker` up to `end_line`.

    `end_line` is matched against the whole (untrimmed) line so indentation
    picks the closing `fi` of the intended block rather than a nested one.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if start_marker in line)
    end = next(i for i in range(start + 1, len(lines)) if lines[i].rstrip() == end_line)
    return "\n".join(lines[start:end + 1])


def _resolve_expected_model(bash: str, block: str, env: dict[str, str], var: str) -> str:
    """Run an extracted model-id block with a fixed environment and read `var`."""
    assignments = "\n".join(f"{key}={value!r}" for key, value in sorted(env.items()))
    script = f"set -euo pipefail\n{assignments}\n{block}\nprintf '%s\\n' \"${{{var}}}\"\n"
    result = subprocess.run(
        [bash, "-s"], input=script, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_compose_uses_ods_entrypoint() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")
    assert "PERPLEXICA_SCRAPE_URL_MAX_CHARS=${PERPLEXICA_SCRAPE_URL_MAX_CHARS:-30000}" in compose
    assert "/app/ods-entrypoint.sh" in compose
    assert "./extensions/services/perplexica/docker-entrypoint.sh:/app/ods-entrypoint.sh:ro" in compose
    assert 'exec /bin/sh /app/ods-entrypoint.sh \\"$@\\"' in compose
    assert "OPENAI_BASE_URL=${HERMES_LLM_BASE_URL:-${LLM_API_URL:-http://llama-server:8080}/v1}" in compose
    assert "OPENAI_API_KEY=${HERMES_LLM_API_KEY:-${LITELLM_KEY:-${OPENAI_API_KEY:-no-key}}}" in compose
    assert "LEMONADE_MODEL=${LEMONADE_MODEL:-}" in compose
    assert "sync-model-config.js:/app/ods-sync-model-config.js:ro" in compose
    assert "sync-search-config.js:/app/ods-sync-search-config.js:ro" in compose
    assert "SEARXNG_API_URL=http://searxng:8080" in compose
    assert "PERPLEXICA_SEARXNG_API_URL=${PERPLEXICA_SEARXNG_API_URL:-}" in compose


def test_search_adapter_config_and_secret_contracts() -> None:
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    env_vars = {
        item["key"]: item
        for item in manifest["service"]["env_vars"]
    }
    adapter = env_vars["PERPLEXICA_SEARXNG_API_URL"]
    assert adapter["required"] is False
    assert adapter["secret"] is False
    assert adapter["default"] == ""

    brave_manifest = yaml.safe_load(
        (BRAVE_DIR / "manifest.yaml").read_text(encoding="utf-8")
    )
    brave_env = {
        item["key"]: item
        for item in brave_manifest["service"]["env_vars"]
    }
    assert brave_env["BRAVE_SEARCH_API_KEY"]["secret"] is True
    assert brave_env["BRAVE_SEARCH_SEARXNG_COMPAT"]["default"] == "0"

    brave_compose = (BRAVE_DIR / "compose.yaml").read_text(encoding="utf-8")
    assert "BRAVE_SEARCH_API_KEY=${BRAVE_SEARCH_API_KEY:-}" in brave_compose
    assert "BRAVE_SEARCH_SEARXNG_COMPAT=${BRAVE_SEARCH_SEARXNG_COMPAT:-0}" in brave_compose
    assert "BRAVE_SEARCH_UPSTREAM_URL" not in brave_compose


def test_bind_mounted_entrypoints_do_not_require_executable_bit() -> None:
    service_entrypoints = (
        (COMPOSE, "/app/ods-entrypoint.sh", 'exec /bin/sh /app/ods-entrypoint.sh \\"$@\\"'),
        (WHISPER_COMPOSE, "/app/docker-entrypoint.sh", "exec /bin/sh /app/docker-entrypoint.sh"),
    )
    for compose_path, mounted_script, shell_exec in service_entrypoints:
        compose = compose_path.read_text(encoding="utf-8")
        assert f"until [ -f {mounted_script} ]" in compose
        assert f"until [ -x {mounted_script} ]" not in compose
        assert shell_exec in compose
        assert f"exec {mounted_script}" not in compose


def test_entrypoint_patches_scrape_url_result_content() -> None:
    script = ENTRYPOINT.read_text(encoding="utf-8")
    assert "name:\"scrape_url\"" in script
    assert "PERPLEXICA_SCRAPE_URL_MAX_CHARS" in script
    assert "content:k.slice(0,${max})" in script

    sample = 'g.push({content:k,metadata:{url:a,title:j}})'
    pattern = re.compile(
        r"([A-Za-z_$][\w$]*\.push\(\{content:)"
        r"([A-Za-z_$][\w$]*)"
        r"(,metadata:\{url:[A-Za-z_$][\w$]*,title:[A-Za-z_$][\w$]*\}\}\))"
    )
    patched = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}.slice(0,30000){m.group(3)}", sample)
    assert patched == 'g.push({content:k.slice(0,30000),metadata:{url:a,title:j}})'


def test_env_schema_allows_scrape_cap_override() -> None:
    schema = json.loads(ENV_SCHEMA.read_text(encoding="utf-8"))
    property_schema = schema["properties"]["PERPLEXICA_SCRAPE_URL_MAX_CHARS"]
    assert property_schema["type"] == "integer"
    assert property_schema["default"] == 30000
    assert property_schema["minimum"] == 1000


def test_compose_restores_image_command() -> None:
    # Setting `entrypoint:` in compose drops the upstream image's CMD
    # (`node server.js`). The override must restate it or the patched
    # entrypoint exits 0 with no app process, restart-looping.
    compose = COMPOSE.read_text(encoding="utf-8")
    assert 'command: ["node", "server.js"]' in compose


def test_entrypoint_falls_back_to_node_server_when_no_args() -> None:
    # Belt-and-suspenders: even if a future compose change drops `command:`,
    # the entrypoint should still launch the app instead of exiting 0.
    script = ENTRYPOINT.read_text(encoding="utf-8")
    assert 'if [ "$#" -eq 0 ]' in script
    assert "set -- node server.js" in script


def test_entrypoint_reconciles_persisted_model_route_on_every_start() -> None:
    script = ENTRYPOINT.read_text(encoding="utf-8")
    compose = COMPOSE.read_text(encoding="utf-8")
    sync_script = SYNC_SCRIPT.read_text(encoding="utf-8")
    assert "sync_model_route" in script
    assert "node /app/ods-sync-model-config.js" in script
    assert "PERPLEXICA_MODEL_SYNC_ATTEMPTS" in script
    assert "ODS_MODEL_SWITCHBOARD" in compose
    assert 'switchboardMode === "enabled"' in sync_script


def test_entrypoint_reconciles_explicit_search_route_independently() -> None:
    script = ENTRYPOINT.read_text(encoding="utf-8")
    assert "sync_search_route" in script
    assert "node /app/ods-sync-search-config.js" in script
    assert "PERPLEXICA_SEARCH_SYNC_ATTEMPTS" in script


def test_sync_script_persists_exact_lemonade_route() -> None:
    node = _node_cmd_or_skip()
    if node is None:
        return

    state = {
        "modelProviders": [{
            "id": "openai-provider",
            "type": "openai",
            "chatModels": [{"key": "old", "name": "old"}],
            "config": {"baseURL": "http://old/v1", "apiKey": "old-key"},
        }],
        "preferences": {
            "defaultChatModel": "old",
            "defaultChatProvider": "openai-provider",
        },
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"values": state}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            state[payload["key"]] = payload["value"]
            body = b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = os.environ.copy()
        env.update({
            "PERPLEXICA_CONFIG_URL": f"http://127.0.0.1:{server.server_port}/api/config",
            "ODS_MODE": "lemonade",
            "AMD_INFERENCE_RUNTIME": "lemonade",
            "LEMONADE_MODEL": "Modern-Model",
            "GGUF_FILE": "Modern-Model.gguf",
            "OPENAI_BASE_URL": "http://litellm:4000/v1",
            "OPENAI_API_KEY": "litellm-key",
        })
        result = subprocess.run(
            [node, str(SYNC_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "Modern-Model"
    provider = state["modelProviders"][0]
    assert provider["chatModels"] == [{"key": "Modern-Model", "name": "Modern-Model"}]
    assert provider["config"] == {
        "baseURL": "http://litellm:4000/v1",
        "apiKey": "litellm-key",
    }
    assert state["preferences"]["defaultChatModel"] == "Modern-Model"


def test_sync_script_uses_stable_alias_when_switchboard_enabled() -> None:
    node = _node_cmd_or_skip()
    if node is None:
        return

    state = {
        "modelProviders": [{
            "id": "openai-provider",
            "type": "openai",
            "chatModels": [{"key": "Qwen3.5-2B-Q4_K_M", "name": "Qwen3.5-2B-Q4_K_M"}],
            "config": {"baseURL": "http://litellm:4000/v1", "apiKey": "old-key"},
        }],
        "preferences": {
            "defaultChatModel": "Qwen3.5-2B-Q4_K_M",
            "defaultChatProvider": "openai-provider",
        },
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"values": state}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            state[payload["key"]] = payload["value"]
            body = b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = os.environ.copy()
        env.update({
            "PERPLEXICA_CONFIG_URL": f"http://127.0.0.1:{server.server_port}/api/config",
            "ODS_MODEL_SWITCHBOARD": "enabled",
            "ODS_MODE": "lemonade",
            "AMD_INFERENCE_RUNTIME": "lemonade",
            "LEMONADE_MODEL": "Qwen3.5-2B-Q4_K_M",
            "GGUF_FILE": "Qwen3.5-2B-Q4_K_M.gguf",
            "OPENAI_BASE_URL": "http://litellm:4000",
            "OPENAI_API_KEY": "litellm-key",
        })
        result = subprocess.run(
            [node, str(SYNC_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ods/current"
    provider = state["modelProviders"][0]
    assert provider["chatModels"] == [{"key": "ods/current", "name": "ods/current"}]
    assert provider["config"] == {
        "baseURL": "http://litellm:4000/v1",
        "apiKey": "litellm-key",
    }
    assert state["preferences"]["defaultChatModel"] == "ods/current"


def test_sync_script_falls_back_to_extra_gguf_when_exact_lemonade_id_is_absent() -> None:
    node = _node_cmd_or_skip()
    if node is None:
        return

    state = {
        "modelProviders": [{
            "id": "openai-provider",
            "type": "openai",
            "chatModels": [],
            "config": {},
        }],
        "preferences": {},
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"values": state}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            state[payload["key"]] = payload["value"]
            body = b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = os.environ.copy()
        env.update({
            "PERPLEXICA_CONFIG_URL": f"http://127.0.0.1:{server.server_port}/api/config",
            "ODS_MODE": "lemonade",
            "AMD_INFERENCE_RUNTIME": "lemonade",
            "LEMONADE_MODEL": "",
            "GGUF_FILE": "Modern-Model.gguf",
            "OPENAI_BASE_URL": "http://litellm:4000/v1",
            "OPENAI_API_KEY": "litellm-key",
        })
        result = subprocess.run(
            [node, str(SYNC_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "extra.Modern-Model.gguf"
    assert state["modelProviders"][0]["chatModels"] == [{
        "key": "extra.Modern-Model.gguf",
        "name": "extra.Modern-Model.gguf",
    }]


def test_sync_script_normalizes_base_url_without_v1_suffix() -> None:
    node = _node_cmd_or_skip()
    if node is None:
        return

    state = {
        "modelProviders": [{
            "id": "openai-provider",
            "type": "openai",
            "chatModels": [],
            "config": {},
        }],
        "preferences": {},
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"values": state}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            state[payload["key"]] = payload["value"]
            body = b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = os.environ.copy()
        env.update({
            "PERPLEXICA_CONFIG_URL": f"http://127.0.0.1:{server.server_port}/api/config",
            "ODS_MODE": "local",
            "GGUF_FILE": "Modern-Model.gguf",
            "OPENAI_BASE_URL": "http://custom-litellm:4000/",
            "OPENAI_API_KEY": "custom-key",
        })
        result = subprocess.run(
            [node, str(SYNC_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert result.returncode == 0, result.stderr
    assert state["modelProviders"][0]["config"] == {
        "baseURL": "http://custom-litellm:4000/v1",
        "apiKey": "custom-key",
    }


def _run_search_route_sync(
    endpoint: str,
    current_endpoint: str = "http://searxng:8080",
) -> tuple[subprocess.CompletedProcess[str], dict, list[dict]]:
    node = _node_cmd_or_skip()
    if node is None:
        raise RuntimeError("Node.js is required")

    state = {
        "modelProviders": [],
        "preferences": {},
        "search": {"searxngURL": current_endpoint},
    }
    writes: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"values": state}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            writes.append(payload)
            if payload["key"] == "search.searxngURL":
                state["search"]["searxngURL"] = payload["value"]
            body = b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = os.environ.copy()
        env.update({
            "PERPLEXICA_CONFIG_URL": f"http://127.0.0.1:{server.server_port}/api/config",
            "PERPLEXICA_SEARXNG_API_URL": endpoint,
            "OPENAI_BASE_URL": "",
            "GGUF_FILE": "",
            "LLM_MODEL": "",
            "LEMONADE_MODEL": "",
            "ODS_MODEL_SWITCHBOARD": "",
            "ODS_MODE": "",
            "AMD_INFERENCE_RUNTIME": "",
            "LLM_BACKEND": "",
            "BRAVE_SEARCH_API_KEY": "must-not-enter-perplexica-config",
        })
        result = subprocess.run(
            [node, str(SEARCH_SYNC_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    return result, state, writes


def test_explicit_search_adapter_updates_persisted_install() -> None:
    result, state, writes = _run_search_route_sync("http://brave-search:8585/")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "http://brave-search:8585"
    assert state["search"]["searxngURL"] == "http://brave-search:8585"
    assert writes == [{
        "key": "search.searxngURL",
        "value": "http://brave-search:8585",
    }]
    assert "must-not-enter-perplexica-config" not in json.dumps(writes)


def test_empty_search_adapter_preserves_existing_searxng_setting() -> None:
    result, state, writes = _run_search_route_sync("")

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert state["search"]["searxngURL"] == "http://searxng:8080"
    assert writes == []


def test_search_adapter_sync_is_idempotent() -> None:
    result, state, writes = _run_search_route_sync(
        "http://brave-search:8585",
        current_endpoint="http://brave-search:8585",
    )

    assert result.returncode == 0, result.stderr
    assert state["search"]["searxngURL"] == "http://brave-search:8585"
    assert writes == []


def test_invalid_search_adapter_fails_closed_without_mutating_config() -> None:
    result, state, writes = _run_search_route_sync(
        "https://user:password@example.com/search?token=secret"
    )

    assert result.returncode == 1
    assert "PERPLEXICA_SEARXNG_API_URL" in result.stderr
    assert state["search"]["searxngURL"] == "http://searxng:8080"
    assert writes == []


# The model id Perplexica must end up with is decided in five places: the
# container-side sync script, scripts/bootstrap-upgrade.sh, the seeding step in
# phase 12, the post-install validation in phase 13, and the repair script. The
# last three used to read `${LLM_BACKEND:-${AMD_INFERENCE_RUNTIME:-}}`, which
# can never see AMD_INFERENCE_RUNTIME because phase 06 always writes a
# non-empty LLM_BACKEND.
# The matrix below pins the resolution rule for every runtime combination the
# installer can produce.
_MODEL_ID_CASES = (
    (
        "amd_local_runs_lemonade_under_llama_server_backend",
        {
            "GGUF_FILE": "Modern-Model.gguf",
            "LLM_BACKEND": "llama-server",
            "AMD_INFERENCE_RUNTIME": "lemonade",
            "LEMONADE_MODEL": "",
        },
        "extra.Modern-Model.gguf",
    ),
    (
        "external_lemonade_uses_the_discovered_model_id",
        {
            "GGUF_FILE": "Modern-Model.gguf",
            "LLM_BACKEND": "lemonade",
            "AMD_INFERENCE_RUNTIME": "lemonade",
            "LEMONADE_MODEL": "Qwen3-8B-GGUF",
        },
        "Qwen3-8B-GGUF",
    ),
    (
        "llama_server_backends_use_the_bare_gguf_id",
        {
            "GGUF_FILE": "Modern-Model.gguf",
            "LLM_BACKEND": "llama-server",
            "AMD_INFERENCE_RUNTIME": "",
            "LEMONADE_MODEL": "",
        },
        "Modern-Model.gguf",
    ),
)


def test_health_phase_seeds_the_same_model_id_as_the_sync_script() -> None:
    bash = _bash_cmd_or_skip()
    if bash is None:
        return

    block = _slice_block(
        HEALTH_PHASE,
        'PERPLEXICA_MODEL="${LLM_MODEL:-default}"',
        "    fi",
    )
    for name, env, expected in _MODEL_ID_CASES:
        resolved = _resolve_expected_model(
            bash, block, {"LLM_MODEL": "qwen3-30b-a3b", **env}, "PERPLEXICA_MODEL"
        )
        assert resolved == expected, f"{name}: expected {expected}, got {resolved}"


def test_post_install_validation_resolves_the_same_model_id_as_the_sync_script() -> None:
    bash = _bash_cmd_or_skip()
    if bash is None:
        return

    block = _slice_block(
        SUMMARY_PHASE,
        '_perplexica_model="${LLM_MODEL:-qwen3-30b-a3b}"',
        "        fi",
    )
    for name, env, expected in _MODEL_ID_CASES:
        resolved = _resolve_expected_model(
            bash, block, {"LLM_MODEL": "qwen3-30b-a3b", **env}, "_perplexica_model"
        )
        assert resolved == expected, f"{name}: expected {expected}, got {resolved}"


def test_repair_script_resolves_the_same_model_id_as_the_sync_script() -> None:
    bash = _bash_cmd_or_skip()
    if bash is None:
        return

    block = _slice_block(REPAIR_SCRIPT, 'if [[ -z "$PERPLEXICA_MODEL" ]]; then', "fi")
    for name, env, expected in _MODEL_ID_CASES:
        resolved = _resolve_expected_model(
            bash,
            block,
            {"LLM_MODEL": "qwen3-30b-a3b", "PERPLEXICA_MODEL": "", **env},
            "PERPLEXICA_MODEL",
        )
        assert resolved == expected, f"{name}: expected {expected}, got {resolved}"


if __name__ == "__main__":
    test_compose_uses_ods_entrypoint()
    test_search_adapter_config_and_secret_contracts()
    test_bind_mounted_entrypoints_do_not_require_executable_bit()
    test_entrypoint_patches_scrape_url_result_content()
    test_env_schema_allows_scrape_cap_override()
    test_compose_restores_image_command()
    test_entrypoint_falls_back_to_node_server_when_no_args()
    test_entrypoint_reconciles_persisted_model_route_on_every_start()
    test_entrypoint_reconciles_explicit_search_route_independently()
    test_sync_script_persists_exact_lemonade_route()
    test_sync_script_uses_stable_alias_when_switchboard_enabled()
    test_sync_script_falls_back_to_extra_gguf_when_exact_lemonade_id_is_absent()
    test_sync_script_normalizes_base_url_without_v1_suffix()
    test_explicit_search_adapter_updates_persisted_install()
    test_empty_search_adapter_preserves_existing_searxng_setting()
    test_search_adapter_sync_is_idempotent()
    test_invalid_search_adapter_fails_closed_without_mutating_config()
    test_health_phase_seeds_the_same_model_id_as_the_sync_script()
    test_post_install_validation_resolves_the_same_model_id_as_the_sync_script()
    test_repair_script_resolves_the_same_model_id_as_the_sync_script()
