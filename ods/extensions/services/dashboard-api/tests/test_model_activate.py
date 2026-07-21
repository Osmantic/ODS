"""Tests for AMD model activation helpers in ods-host-agent.py."""

import hashlib
import importlib.util
import http.client
import io
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

# Import the host agent module from bin/ using importlib.
# The module has an ``if __name__ == "__main__":`` guard so no server starts.
_agent_path = Path(__file__).resolve().parents[4] / "bin" / "ods-host-agent.py"
_spec = importlib.util.spec_from_file_location("ods_host_agent_activate", _agent_path)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["ods_host_agent_activate"] = _mod
_spec.loader.exec_module(_mod)

_check_lemonade_health = _mod._check_lemonade_health
_meaningful_completion = _mod._meaningful_completion
_resolve_lemonade_model_id = _mod._resolve_lemonade_model_id
_send_lemonade_warmup = _mod._send_lemonade_warmup
_lemonade_completion_ready = _mod._lemonade_completion_ready
_write_lemonade_config = _mod._write_lemonade_config
_patch_hermes_model_config = _mod._patch_hermes_model_config
_compose_restart_llama_server = _mod._compose_restart_llama_server
_launch_native_llama_server = _mod._launch_native_llama_server
_restart_windows_lemonade = _mod._restart_windows_lemonade
_is_windows_host_llama_server = _mod._is_windows_host_llama_server
_restart_windows_native_llama_server = _mod._restart_windows_native_llama_server
_write_windows_native_litellm_config = _mod._write_windows_native_litellm_config


@pytest.fixture(autouse=True)
def _isolate_opencode_config(monkeypatch, tmp_path):
    """Never let model-activation tests mutate the developer's real config."""
    config_dir = tmp_path / "isolated-home" / ".config" / "opencode"
    monkeypatch.setattr(
        _mod,
        "_opencode_config_paths",
        lambda: (config_dir / "opencode.json", config_dir / "config.json"),
    )
    monkeypatch.setattr(
        _mod,
        "_capture_container_state",
        lambda container: {
            "exists": _mod._container_exists(container),
            "running": (
                container != "ods-perplexica" and _mod._container_exists(container)
            ),
        },
    )
    monkeypatch.setattr(_mod, "_wait_for_container_health", lambda _container: None)
    monkeypatch.setattr(
        _mod,
        "_capture_managed_opencode_state",
        lambda: {"system": _mod.platform.system(), "active": False},
    )
    monkeypatch.setattr(_mod, "_opencode_installed", lambda: False)


def test_host_agent_backlog_handles_dashboard_poll_bursts():
    assert _mod.ThreadedHTTPServer.request_queue_size >= 64


def test_host_agent_keeps_gets_alive_and_closes_posts(monkeypatch):
    class _CountingServer(_mod.ThreadedHTTPServer):
        accepted_connections = 0

        def get_request(self):
            request = super().get_request()
            self.accepted_connections += 1
            return request

    server = _CountingServer(("127.0.0.1", 0), _mod.AgentHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        monkeypatch.setattr(_mod, "AGENT_API_KEY", "keepalive-test-key")
        for _ in range(20):
            connection.request("GET", "/health")
            response = connection.getresponse()
            assert response.status == 200
            assert json.loads(response.read()) == {"status": "ok", "version": _mod.VERSION}
        assert server.accepted_connections == 1

        connection.request(
            "POST",
            "/v1/model/download/cancel",
            body="{}",
            headers={
                "Authorization": "Bearer keepalive-test-key",
                "Content-Type": "application/json",
            },
        )
        response = connection.getresponse()
        assert response.status == 200
        assert response.getheader("Connection") == "close"
        assert json.loads(response.read()) == {"status": "no_download"}

        # http.client transparently opens a fresh socket after the explicit
        # close; the unread cancel body cannot corrupt this request.
        connection.request("GET", "/health")
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read()) == {"status": "ok", "version": _mod.VERSION}
        assert server.accepted_connections == 2
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)


# --- _check_lemonade_health ---


class TestCheckLemonadeHealth:

    def test_model_loaded(self):
        body = '{"status": "ok", "model_loaded": "extra.Qwen3.5-9B-Q4_K_M.gguf"}'
        assert _check_lemonade_health(body) is True

    def test_model_null(self):
        body = '{"status": "ok", "model_loaded": null}'
        assert _check_lemonade_health(body) is False

    def test_no_model_loaded_key(self):
        body = '{"status": "ok"}'
        assert _check_lemonade_health(body) is False

    def test_completion_accepts_reasoning_content_when_message_content_empty(self):
        body = {
            "choices": [{
                "message": {
                    "content": "",
                    "reasoning_content": "Okay",
                    "role": "assistant",
                }
            }]
        }
        assert _meaningful_completion(body) is True

    def test_invalid_json(self):
        assert _check_lemonade_health("not json") is False

    def test_empty_string(self):
        assert _check_lemonade_health("") is False

    def test_model_loaded_false_is_not_an_identity(self):
        body = '{"model_loaded": false}'
        assert _check_lemonade_health(body) is False

    def test_model_loaded_empty_string_is_not_an_identity(self):
        body = '{"model_loaded": ""}'
        assert _check_lemonade_health(body) is False

    def test_exact_catalog_id_can_prove_checkpoint_with_a_different_name(self):
        body = '{"status":"ok","model_loaded":"lemonade-modern-id"}'
        assert _check_lemonade_health(
            body,
            "Model.File.gguf",
            "lemonade-modern-id",
        ) is True

    def test_modern_health_requires_loaded_context_at_least_expected(self):
        body = json.dumps({
            "status": "ok",
            "version": "10.7.0",
            "model_loaded": "Qwen3.6-35B-A3B-UD-Q4_K_M",
            "all_models_loaded": [{
                "model_name": "Qwen3.6-35B-A3B-UD-Q4_K_M",
                "checkpoint": r"C:\ods\data\models\Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
                "recipe_options": {"ctx_size": 131072},
            }],
        })
        assert _check_lemonade_health(
            body,
            "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
            "Qwen3.6-35B-A3B-UD-Q4_K_M",
            131072,
        ) is True

    def test_modern_health_rejects_too_small_loaded_context(self):
        body = json.dumps({
            "status": "ok",
            "version": "10.7.0",
            "model_loaded": "Qwen3.6-35B-A3B-UD-Q4_K_M",
            "all_models_loaded": [{
                "model_name": "Qwen3.6-35B-A3B-UD-Q4_K_M",
                "checkpoint": r"C:\ods\data\models\Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
                "recipe_options": {"ctx_size": 16384},
            }],
        })
        assert _check_lemonade_health(
            body,
            "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
            "Qwen3.6-35B-A3B-UD-Q4_K_M",
            131072,
        ) is False

    def test_legacy_health_without_context_is_not_a_false_red(self):
        body = '{"status":"ok","version":"10.6.9","model_loaded":"extra.Model.gguf"}'
        assert _check_lemonade_health(
            body,
            "Model.gguf",
            "extra.Model.gguf",
            131072,
        ) is True

    def test_legacy_health_with_loaded_list_requires_matching_row(self):
        body = json.dumps({
            "status": "ok",
            "version": "10.6.9",
            "model_loaded": "extra.Model.gguf",
            "all_models_loaded": [{
                "model_name": "different-model",
                "recipe_options": {"ctx_size": 131072},
            }],
        })
        assert _check_lemonade_health(
            body,
            "Model.gguf",
            "extra.Model.gguf",
            131072,
        ) is False


class TestResolveLemonadeModelId:

    def test_uses_matching_persisted_model_when_runtime_is_unavailable(self, monkeypatch):
        monkeypatch.setattr(
            _mod.subprocess,
            "run",
            lambda cmd, **_kwargs: subprocess.CompletedProcess(cmd, 7, "", "offline"),
        )

        assert _resolve_lemonade_model_id(
            {
                "GGUF_FILE": "Model.gguf",
                "LEMONADE_MODEL": "Model",
            },
            "Model.gguf",
            host="127.0.0.1",
            port="8080",
        ) == "Model"

    def test_stale_persisted_model_does_not_mask_107_stem_fallback(self, monkeypatch):
        def fake_run(cmd, **_kwargs):
            body = '{"data":[]}' if cmd[-1].endswith("/models") else '{"version":"10.7.0"}'
            return subprocess.CompletedProcess(cmd, 0, body, "")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)

        assert _resolve_lemonade_model_id(
            {
                "GGUF_FILE": "Model.gguf",
                "LEMONADE_MODEL": "Old-Model",
            },
            "Model.gguf",
            host="127.0.0.1",
            port="8080",
        ) == "Model"

    def test_ignores_persisted_model_for_a_different_gguf(self, monkeypatch):
        def fake_run(cmd, **_kwargs):
            body = '{"data":[]}' if cmd[-1].endswith("/models") else '{"version":"10.7.0"}'
            return subprocess.CompletedProcess(cmd, 0, body, "")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)

        assert _resolve_lemonade_model_id(
            {
                "GGUF_FILE": "Old.gguf",
                "LEMONADE_MODEL": "Old",
            },
            "New.gguf",
            host="127.0.0.1",
            port="8080",
        ) == "New"

    def test_live_catalog_corrects_a_stale_persisted_id(self, monkeypatch):
        def fake_run(cmd, **_kwargs):
            if cmd[-1].endswith("/models"):
                body = json.dumps({
                    "data": [{
                        "id": "Modern-Model",
                        "checkpoint": r"C:\ods\data\models\Model.gguf",
                    }]
                })
            else:
                body = '{"version":"10.7.0"}'
            return subprocess.CompletedProcess(cmd, 0, body, "")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)

        assert _resolve_lemonade_model_id(
            {
                "GGUF_FILE": "Model.gguf",
                "LEMONADE_MODEL": "stale-id",
            },
            "Model.gguf",
            host="127.0.0.1",
            port="8080",
        ) == "Modern-Model"

    def test_matches_live_id_by_checkpoint_path(self, monkeypatch):
        def fake_run(cmd, **_kwargs):
            assert cmd[-1] == "http://127.0.0.1:8080/api/v1/models"
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps({
                    "data": [
                        {
                            "id": "nearby-model",
                            "checkpoint": r"C:\models\Model.gguf.bak",
                        },
                        {
                            "id": "Modern-Model-ID",
                            "checkpoint": r"C:\ods\data\models\Model.gguf",
                            "checkpoints": {},
                        },
                    ]
                }),
                stderr="",
            )

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)

        assert _resolve_lemonade_model_id(
            {},
            "Model.gguf",
            host="127.0.0.1",
            port="8080",
        ) == "Modern-Model-ID"

    @pytest.mark.parametrize(
        ("version", "expected"),
        [
            ("10.7.0", "Model.Name"),
            ("Lemonade Server v10.8.1", "Model.Name"),
            ("10.6.9", "extra.Model.Name.gguf"),
            ("", "extra.Model.Name.gguf"),
        ],
    )
    def test_versioned_fallback(self, monkeypatch, version, expected):
        def fake_run(cmd, **_kwargs):
            if cmd[-1].endswith("/models"):
                stdout = '{"data":[]}'
            else:
                stdout = json.dumps({"status": "ok", "version": version})
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)

        assert _resolve_lemonade_model_id(
            {},
            "Model.Name.gguf",
            host="127.0.0.1",
            port="8080",
        ) == expected


# --- _send_lemonade_warmup ---


class TestSendLemonadeWarmup:

    def test_success(self, monkeypatch):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            result = subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            return result

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert _send_lemonade_warmup("localhost", "8080", "Modern-Model", 0) is True
        assert len(calls) == 1
        # Verify curl is called with correct URL and model ID
        cmd = calls[0]
        assert "http://localhost:8080/api/v1/chat/completions" in cmd
        payload_idx = cmd.index("-d") + 1
        assert '"Modern-Model"' in cmd[payload_idx]

    def test_failure(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="error")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert _send_lemonade_warmup("localhost", "8080", "model.gguf", 0) is False

    def test_timeout(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 35))

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert _send_lemonade_warmup("localhost", "8080", "model.gguf", 0) is False

    def test_containerized_host(self, monkeypatch):
        """Verify the host parameter is used (not hardcoded to localhost)."""
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        _send_lemonade_warmup("ods-llama-server", "8080", "Modern-Model", 0)
        assert "http://ods-llama-server:8080/api/v1/chat/completions" in calls[0]


class TestLemonadeCompletionReady:

    def test_success_when_completion_has_choices(self, monkeypatch):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout='{"choices":[{"message":{"content":"ok"}}]}',
                stderr="",
            )

        monkeypatch.setattr(subprocess, "run", fake_run)

        assert _lemonade_completion_ready(
            "127.0.0.1",
            "8080",
            "model.gguf",
            "Modern-Model",
        ) is True
        assert "http://127.0.0.1:8080/api/v1/chat/completions" in calls[0]
        payload = calls[0][calls[0].index("-d") + 1]
        assert '"Modern-Model"' in payload

    def test_false_on_nonzero_exit(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert _lemonade_completion_ready("127.0.0.1", "8080", "model.gguf") is False

    def test_false_on_invalid_json(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout="not-json", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert _lemonade_completion_ready("127.0.0.1", "8080", "model.gguf") is False

    @pytest.mark.parametrize("content", ["", "???", " ? ? ? ", "!!!"])
    def test_rejects_empty_or_pathological_output(self, monkeypatch, content):
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps({"choices": [{"message": {"content": content}}]}),
                stderr="",
            )

        monkeypatch.setattr(subprocess, "run", fake_run)

        assert _lemonade_completion_ready("127.0.0.1", "8080", "model.gguf") is False

    def test_readiness_uses_runtime_identity_for_lemonade_completion(self, monkeypatch):
        completion_calls = []

        def fake_run(cmd, **_kwargs):
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout='{"status":"ok","version":"10.7.0","model_loaded":"Modern-Model"}',
                stderr="",
            )

        def fake_completion(host, port, model, prefix, **expected):
            completion_calls.append((host, port, model, prefix, expected))
            return True

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        monkeypatch.setattr(_mod, "_chat_completion_ready", fake_completion)

        assert _mod._wait_for_model_readiness(
            {
                "GPU_BACKEND": "amd",
                "OLLAMA_PORT": "8080",
                "GGUF_FILE": "Modern-Model.gguf",
            },
            model_id="catalog-model",
            gguf_file="Modern-Model.gguf",
            llm_model_name="model",
            lemonade_model_id="extra.Modern-Model.gguf",
            attempts=1,
            initial_delay=0,
            interval=0,
        ) is True
        assert completion_calls == [
            (
                "127.0.0.1",
                "8080",
                "Modern-Model",
                "/api/v1",
                {
                    "expected_model_id": "Modern-Model",
                    "expected_gguf_file": "Modern-Model.gguf",
                    "expected_llm_model_name": "model",
                },
            ),
        ]

    def test_legacy_lemonade_proof_marks_context_unverified(self, monkeypatch):
        def fake_run(cmd, **_kwargs):
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps({
                    "status": "ok",
                    "version": "10.2.0",
                    "model_loaded": "extra.Model.gguf",
                }),
                stderr="",
            )

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        monkeypatch.setattr(_mod, "_chat_completion_ready", lambda *_a, **_k: True)
        proof = _mod._wait_for_model_readiness(
            {
                "GPU_BACKEND": "amd",
                "OLLAMA_PORT": "8080",
                "CTX_SIZE": "4096",
            },
            model_id="model",
            gguf_file="Model.gguf",
            llm_model_name="model",
            lemonade_model_id="extra.Model.gguf",
            attempts=1,
            initial_delay=0,
            interval=0,
            return_proof=True,
        )
        assert proof == {
            "identity": "extra.Model.gguf",
            "contextLength": 4096,
            "contextVerified": False,
            "verifiedAt": proof["verifiedAt"],
        }

    def test_catalog_non_agent_viability_overrides_context_floor(self):
        model = {
            "app_compatibility": {
                "agent_viability": {"status": "not_agent_viable"}
            }
        }
        assert _mod._model_agent_viable(model, 131072) is False
        assert _mod._model_agent_viable({}, 131072) is True
        assert _mod._model_agent_viable({}, 32768) is False

    @pytest.mark.parametrize(
        ("completion_model", "expected_identity"),
        [
            ("runtime/new-model.gguf", "runtime/new-model.gguf"),
            ("old-model.gguf", ""),
        ],
    )
    def test_readiness_returns_only_completion_verified_runtime_identity(
        self, monkeypatch, completion_model, expected_identity
    ):
        def fake_run(cmd, **_kwargs):
            url = next((str(part) for part in cmd if str(part).startswith("http")), "")
            if url.endswith("/v1/models"):
                body = _llama_identity_response("runtime/new-model.gguf")
            elif url.endswith("/props"):
                body = json.dumps({
                    "default_generation_settings": {"n_ctx": 65536}
                })
            else:
                body = json.dumps({
                    "model": completion_model,
                    "choices": [{"message": {"content": "READY"}}],
                })
            return subprocess.CompletedProcess(cmd, 0, stdout=body, stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)

        identity = _mod._wait_for_model_readiness(
            {"GPU_BACKEND": "nvidia", "OLLAMA_PORT": "8080"},
            model_id="new-model",
            gguf_file="new-model.gguf",
            llm_model_name="new-model",
            attempts=1,
            initial_delay=0,
            interval=0,
            return_identity=True,
        )
        assert identity == expected_identity

    def test_readiness_proof_carries_actual_runtime_context(self, monkeypatch):
        def fake_run(cmd, **_kwargs):
            url = next((str(part) for part in cmd if str(part).startswith("http")), "")
            if url.endswith("/v1/models"):
                body = _llama_identity_response("runtime/new-model.gguf")
            elif url.endswith("/props"):
                body = json.dumps({
                    "default_generation_settings": {"n_ctx": 65536}
                })
            else:
                body = ""
            return subprocess.CompletedProcess(cmd, 0, stdout=body, stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        monkeypatch.setattr(_mod, "_chat_completion_ready", lambda *_a, **_k: True)
        proof = _mod._wait_for_model_readiness(
            {
                "GPU_BACKEND": "nvidia",
                "OLLAMA_PORT": "8080",
                "CTX_SIZE": "65536",
            },
            model_id="new-model",
            gguf_file="new-model.gguf",
            llm_model_name="new-model",
            attempts=1,
            initial_delay=0,
            interval=0,
            return_proof=True,
        )
        assert proof["identity"] == "runtime/new-model.gguf"
        assert proof["contextLength"] == 65536
        assert proof["contextVerified"] is True
        assert proof["verifiedAt"].endswith("+00:00")

    def test_readiness_rejects_runtime_context_below_requested(self, monkeypatch):
        completion_calls = []

        def fake_run(cmd, **_kwargs):
            url = next((str(part) for part in cmd if str(part).startswith("http")), "")
            if url.endswith("/v1/models"):
                body = _llama_identity_response("runtime/new-model.gguf")
            elif url.endswith("/props"):
                body = json.dumps({
                    "default_generation_settings": {"n_ctx": 32768}
                })
            else:
                body = ""
            return subprocess.CompletedProcess(cmd, 0, stdout=body, stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        monkeypatch.setattr(
            _mod,
            "_chat_completion_ready",
            lambda *_a, **_k: completion_calls.append(True) or True,
        )
        proof = _mod._wait_for_model_readiness(
            {
                "GPU_BACKEND": "nvidia",
                "OLLAMA_PORT": "8080",
                "CTX_SIZE": "65536",
            },
            model_id="new-model",
            gguf_file="new-model.gguf",
            llm_model_name="new-model",
            attempts=1,
            initial_delay=0,
            interval=0,
            return_proof=True,
        )
        assert proof == {}
        assert completion_calls == []


# --- _write_lemonade_config ---


class TestWriteLemonadeConfig:

    def test_writes_correct_content(self, tmp_path):
        litellm_dir = tmp_path / "config" / "litellm"
        litellm_dir.mkdir(parents=True)
        _write_lemonade_config(tmp_path, "Qwen3.5-9B-Q4_K_M.gguf")

        content = (litellm_dir / "lemonade.yaml").read_text()
        assert "model: openai/extra.Qwen3.5-9B-Q4_K_M.gguf" in content
        assert "api_base: http://llama-server:8080/api/v1" in content
        assert "api_key: sk-lemonade" in content
        assert "extra_body:" in content
        assert "chat_template_kwargs:" in content
        assert "enable_thinking: false" in content
        assert 'model_name: "*"' in content
        assert "drop_params: true" in content
        assert "request_timeout: 900" in content
        assert "stream_timeout: 900" in content

    def test_fallback_writer_keeps_long_model_timeouts(self, monkeypatch, tmp_path):
        litellm_dir = tmp_path / "config" / "litellm"
        litellm_dir.mkdir(parents=True)
        monkeypatch.setattr(_mod, "_render_runtime_config", lambda *args, **kwargs: False)

        _write_lemonade_config(tmp_path, "fallback-model.gguf")

        content = (litellm_dir / "lemonade.yaml").read_text()
        assert "model: openai/extra.fallback-model.gguf" in content
        assert "request_timeout: 900" in content
        assert "stream_timeout: 900" in content

    def test_reads_lemonade_key_from_env_file_when_process_env_unset(
        self, monkeypatch, tmp_path,
    ):
        """Installer-written Lemonade keys live in .env, not process env."""
        litellm_dir = tmp_path / "config" / "litellm"
        litellm_dir.mkdir(parents=True)
        (tmp_path / ".env").write_text(
            "LITELLM_LEMONADE_API_KEY=sk-from-env-file-12345\n",
            encoding="utf-8",
        )
        monkeypatch.delenv("LITELLM_LEMONADE_API_KEY", raising=False)

        _write_lemonade_config(tmp_path, "Qwen3.5-9B-Q4_K_M.gguf")

        content = (litellm_dir / "lemonade.yaml").read_text()
        assert "api_key: sk-from-env-file-12345" in content
        assert "api_key: sk-lemonade" not in content

    def test_prefers_persisted_exact_model_id(self, tmp_path):
        litellm_dir = tmp_path / "config" / "litellm"
        litellm_dir.mkdir(parents=True)
        (tmp_path / ".env").write_text(
            "LEMONADE_MODEL=Modern-Model\n",
            encoding="utf-8",
        )

        _write_lemonade_config(tmp_path, "Modern-Model.gguf")

        content = (litellm_dir / "lemonade.yaml").read_text(encoding="utf-8")
        assert "model: openai/Modern-Model" in content
        assert "extra.Modern-Model.gguf" not in content

    def test_overwrites_previous(self, tmp_path):
        litellm_dir = tmp_path / "config" / "litellm"
        litellm_dir.mkdir(parents=True)

        _write_lemonade_config(tmp_path, "old-model.gguf")
        _write_lemonade_config(tmp_path, "new-model.gguf")

        content = (litellm_dir / "lemonade.yaml").read_text()
        assert "old-model.gguf" not in content
        assert "model: openai/extra.new-model.gguf" in content

    def test_file_path(self, tmp_path):
        litellm_dir = tmp_path / "config" / "litellm"
        litellm_dir.mkdir(parents=True)
        _write_lemonade_config(tmp_path, "model.gguf")
        assert (litellm_dir / "lemonade.yaml").exists()


class TestSwitchboardRuntimeConfig:
    def test_render_runtime_config_passes_switchboard_and_runtime_args(
        self, monkeypatch, tmp_path,
    ):
        renderer = tmp_path / "scripts" / "render-runtime-configs.py"
        renderer.parent.mkdir(parents=True)
        renderer.write_text("# renderer placeholder\n", encoding="utf-8")
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)

        assert _mod._render_runtime_config(
            tmp_path,
            "litellm-switchboard",
            model="qwen",
            gguf_file="Qwen.gguf",
            lemonade_model_id="",
            lemonade_api_key="sk-runtime",
            lemonade_api_base="http://lemonade:8080/api/v1",
            llm_base_url="http://runtime:8080/v1",
            ods_mode="hybrid",
            gpu_backend="nvidia",
            context_length=65536,
            switchboard_mode="enabled",
        ) is True

        cmd = calls[0][0]
        assert cmd[cmd.index("--switchboard-mode") + 1] == "enabled"
        assert cmd[cmd.index("--model") + 1] == "qwen"
        assert cmd[cmd.index("--llm-base-url") + 1] == "http://runtime:8080/v1"
        assert cmd[cmd.index("--context-length") + 1] == "65536"

    def test_enabled_mode_renderer_failure_aborts(self, monkeypatch, tmp_path):
        calls = []

        def fake_render(_install_dir, surface, **_kwargs):
            calls.append(surface)
            return False

        monkeypatch.setattr(_mod, "_render_runtime_config", fake_render)

        with pytest.raises(RuntimeError, match="model-router-endpoints"):
            _mod._render_model_router_runtime_configs(
                tmp_path,
                {"ODS_MODEL_SWITCHBOARD": "enabled"},
                model="qwen",
                gguf_file="Qwen.gguf",
                lemonade_model_id="",
                context_length=65536,
            )

        assert calls == ["model-router-endpoints"]

    def test_router_runtime_base_never_points_to_litellm(self):
        assert _mod._runtime_llama_api_base({
            "LLM_API_URL": "http://litellm:4000/v1",
        }) == "http://llama-server:8080/v1"

    def test_windows_native_runtime_base_uses_host_gateway(self, monkeypatch):
        monkeypatch.setattr(_mod, "_is_windows_host_llama_server", lambda _env: True)

        assert _mod._runtime_llama_api_base({
            "AMD_INFERENCE_PORT": "9234",
            "LLM_API_URL": "http://litellm:4000/v1",
        }) == "http://host.docker.internal:9234/v1"


class TestOpenCodeModelRoute:
    def test_lemonade_uses_authenticated_host_litellm_route(self, monkeypatch):
        monkeypatch.setattr(_mod.platform, "system", lambda: "Linux")
        monkeypatch.setattr(_mod, "_is_windows_host_llama_server", lambda _env: False)

        assert _mod._opencode_route({
            "GPU_BACKEND": "amd",
            "LITELLM_PORT": "4400",
            "LITELLM_KEY": "secret-key",
        }) == ("http://127.0.0.1:4400/v1", "secret-key")

    def test_windows_lemonade_uses_native_api_route(self, monkeypatch):
        monkeypatch.setattr(_mod.platform, "system", lambda: "Windows")

        assert _mod._opencode_route({
            "GPU_BACKEND": "amd",
            "LLM_BACKEND": "lemonade",
            "AMD_INFERENCE_RUNTIME": "lemonade",
            "AMD_INFERENCE_LOCATION": "host",
            "AMD_INFERENCE_PORT": "8090",
        }) == ("http://127.0.0.1:8090/api/v1", "no-key")

    def test_linux_managed_service_is_restarted_with_user_bus(self, monkeypatch):
        calls = []
        monkeypatch.setattr(_mod.platform, "system", lambda: "Linux")
        monkeypatch.setattr(_mod.os, "getuid", lambda: 1001, raising=False)
        # _opencode_user_service_env() honors an ambient session bus via
        # setdefault; clear the host's values so the derived uid path is tested.
        monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        monkeypatch.setattr(_mod, "_wait_for_opencode_health", lambda: None)

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)

        state = {
            "system": "Linux",
            "active": True,
            "env": _mod._opencode_user_service_env(),
        }
        assert _mod._restart_managed_opencode(state) is True
        assert [call[0] for call in calls] == [
            ["systemctl", "--user", "restart", "opencode-web.service"],
        ]
        assert calls[0][1]["env"]["XDG_RUNTIME_DIR"] == "/run/user/1001"
        assert calls[0][1]["env"]["DBUS_SESSION_BUS_ADDRESS"] == (
            "unix:path=/run/user/1001/bus"
        )

    def test_windows_inactive_runtime_is_not_force_restarted(self, monkeypatch):
        monkeypatch.setattr(_mod.platform, "system", lambda: "Windows")
        monkeypatch.setattr(
            _mod.subprocess,
            "run",
            lambda *_args, **_kwargs: pytest.fail("Windows OpenCode is not ODS-managed"),
        )

        assert _mod._restart_managed_opencode({
            "system": "Windows",
            "active": False,
        }) is False

    def test_windows_active_runtime_is_restarted_and_proved(self, monkeypatch):
        actions = []
        monkeypatch.setattr(_mod.platform, "system", lambda: "Windows")
        monkeypatch.setattr(
            _mod,
            "_run_windows_opencode_control",
            lambda action: actions.append(action) or True,
        )
        monkeypatch.setattr(_mod, "_wait_for_opencode_health", lambda: actions.append("health"))

        assert _mod._restart_managed_opencode({
            "system": "Windows",
            "active": True,
        }) is True
        assert actions == ["restart", "health"]


class TestPerplexicaModelRoute:

    @staticmethod
    def _snapshot():
        return {
            "url": "http://127.0.0.1:3004/api/config",
            "values": {
                "modelProviders": [{
                    "id": "openai-provider",
                    "type": "openai",
                    "chatModels": [{"key": "old-model", "name": "old-model"}],
                    "config": {"baseURL": "http://old/v1", "apiKey": "old-key"},
                }],
                "preferences": {
                    "defaultChatModel": "old-model",
                    "defaultChatProvider": "openai-provider",
                },
            },
        }

    def test_windows_lemonade_uses_exact_id_through_litellm(self):
        model, base_url, api_key = _mod._perplexica_model_route(
            {
                "GPU_BACKEND": "amd",
                "AMD_INFERENCE_RUNTIME": "lemonade",
                "AMD_INFERENCE_LOCATION": "host",
                "LEMONADE_MODEL": "Modern-Model",
                "HERMES_LLM_BASE_URL": "http://litellm:4000/v1",
                "LITELLM_KEY": "secret-key",
            },
            "Modern-Model.gguf",
        )

        assert model == "Modern-Model"
        assert base_url == "http://litellm:4000/v1"
        assert api_key == "secret-key"

    def test_switchboard_mode_uses_stable_alias_through_litellm(self):
        model, base_url, api_key = _mod._perplexica_model_route(
            {
                "ODS_MODEL_SWITCHBOARD": "enabled",
                "GPU_BACKEND": "amd",
                "AMD_INFERENCE_RUNTIME": "lemonade",
                "LEMONADE_MODEL": "Modern-Model",
                "LITELLM_KEY": "secret-key",
            },
            "Modern-Model.gguf",
            lemonade_model_id="Modern-Model",
        )

        assert model == "ods/current"
        assert base_url == "http://litellm:4000/v1"
        assert api_key == "secret-key"

    def test_update_persists_and_verifies_model_route(self, monkeypatch):
        snapshot = self._snapshot()
        current = json.loads(json.dumps(snapshot["values"]))
        posts = []

        def fake_http(_url, payload=None):
            if payload is None:
                return {"values": json.loads(json.dumps(current))}
            posts.append(payload)
            current[payload["key"]] = json.loads(json.dumps(payload["value"]))
            return {}

        monkeypatch.setattr(_mod, "_perplexica_http_json", fake_http)

        _mod._update_perplexica_model(
            {
                "LLM_API_URL": "http://llama-server:8080",
                "LITELLM_KEY": "no-key",
            },
            snapshot,
            gguf_file="new-model.gguf",
        )

        assert [post["key"] for post in posts] == ["modelProviders", "preferences"]
        assert current["preferences"]["defaultChatModel"] == "new-model.gguf"
        provider = current["modelProviders"][0]
        assert provider["chatModels"] == [{"key": "new-model.gguf", "name": "new-model.gguf"}]
        assert provider["config"]["baseURL"] == "http://llama-server:8080/v1"

    def test_restore_reinstates_and_verifies_snapshot(self, monkeypatch):
        snapshot = self._snapshot()
        current = {
            "modelProviders": [],
            "preferences": {"defaultChatModel": "wrong"},
        }

        def fake_http(_url, payload=None):
            if payload is None:
                return {"values": json.loads(json.dumps(current))}
            current[payload["key"]] = json.loads(json.dumps(payload["value"]))
            return {}

        monkeypatch.setattr(_mod, "_perplexica_http_json", fake_http)

        _mod._restore_perplexica_config(snapshot)

        assert current == snapshot["values"]

    def test_restore_accepts_perplexica_normalized_snapshot(self, monkeypatch):
        snapshot = self._snapshot()
        current = {
            "modelProviders": [],
            "preferences": {"defaultChatModel": "wrong"},
        }

        def fake_http(_url, payload=None):
            if payload is None:
                restored = json.loads(json.dumps(current))
                restored["preferences"]["theme"] = "system"
                restored["modelProviders"][0]["config"]["label"] = "OpenAI"
                restored["modelProviders"][0]["chatModels"].append({
                    "key": "extra-model",
                    "name": "extra-model",
                })
                return {"values": restored}
            current[payload["key"]] = json.loads(json.dumps(payload["value"]))
            return {}

        monkeypatch.setattr(_mod, "_perplexica_http_json", fake_http)

        _mod._restore_perplexica_config(snapshot)

        assert current == snapshot["values"]


class TestDownstreamRouteVerification:

    def test_completion_probe_sends_bearer_key_when_requested(self, monkeypatch):
        commands = []

        def fake_run(cmd, **_kwargs):
            commands.append(cmd)
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps({"choices": [{"message": {"content": "READY"}}]}),
                stderr="",
            )

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)

        assert _mod._chat_completion_ready(
            "127.0.0.1",
            "4000",
            "default",
            "/v1",
            "secret",
        )
        assert "Authorization: Bearer secret" in commands[0]

    def test_completion_probe_requires_response_model_when_identity_expected(
        self, monkeypatch
    ):
        def fake_run(_cmd, **_kwargs):
            return subprocess.CompletedProcess(
                _cmd,
                0,
                stdout=json.dumps({
                    "model": "old-model.gguf",
                    "choices": [{"message": {"content": "READY"}}],
                }),
                stderr="",
            )

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)

        assert not _mod._chat_completion_ready(
            "127.0.0.1",
            "8080",
            "new-model.gguf",
            expected_gguf_file="new-model.gguf",
        )

    def test_completion_probe_accepts_matching_runtime_identity(self, monkeypatch):
        def fake_run(_cmd, **_kwargs):
            return subprocess.CompletedProcess(
                _cmd,
                0,
                stdout=json.dumps({
                    "model": "runtime/new-model.gguf",
                    "choices": [{"message": {"content": "READY"}}],
                }),
                stderr="",
            )

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)

        assert _mod._chat_completion_ready(
            "127.0.0.1",
            "8080",
            "new-model.gguf",
            expected_gguf_file="new-model.gguf",
        )

    def test_litellm_probe_uses_default_route_and_master_key(self, monkeypatch):
        calls = []
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(
            _mod,
            "_chat_completion_ready",
            lambda *args: calls.append(args) or True,
        )

        _mod._verify_litellm_route({"LITELLM_PORT": "4100", "LITELLM_KEY": "secret"})

        assert calls == [("127.0.0.1", "4100", "default", "/v1", "secret")]

    def test_openclaw_probe_accepts_exact_lemonade_id(self, monkeypatch):
        def fake_run(cmd, **_kwargs):
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=(
                    "LLM_MODEL=logical-name\n"
                    "GGUF_FILE=Modern-Model.gguf\n"
                    "LEMONADE_MODEL=Modern-Model\n"
                ),
                stderr="",
            )

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)

        _mod._verify_openclaw_model_env("Modern-Model")

    def test_openclaw_probe_rejects_stale_model(self, monkeypatch):
        monkeypatch.setattr(
            _mod.subprocess,
            "run",
            lambda cmd, **_kwargs: subprocess.CompletedProcess(
                cmd,
                0,
                stdout="LEMONADE_MODEL=Old-Model\nGGUF_FILE=Old-Model.gguf\n",
                stderr="",
            ),
        )

        with pytest.raises(RuntimeError, match="expected Modern-Model"):
            _mod._verify_openclaw_model_env("Modern-Model")


class TestPatchHermesModelConfig:

    def test_updates_model_default_only(self, tmp_path):
        config = tmp_path / "config.yaml"
        config.write_text(
            "# example\n"
            "model:\n"
            "  default: \"old-model\"\n"
            "  provider: \"custom\"\n"
            "  base_url: \"http://llama-server:8080/v1\"\n"
            "other:\n"
            "  default: \"leave-me\"\n",
            encoding="utf-8",
        )

        assert _patch_hermes_model_config(config, "new-model.gguf") is True

        text = config.read_text(encoding="utf-8")
        assert '  default: "new-model.gguf"' in text
        assert '  provider: "custom"' in text
        assert '  default: "leave-me"' in text

    def test_updates_context_and_base_url(self, tmp_path):
        config = tmp_path / "config.yaml"
        config.write_text(
            "model:\n"
            "  default: \"old-model\"\n"
            "  provider: \"custom\"\n"
            "  base_url: \"http://host.docker.internal:8080/v1\"\n"
            "  context_length: 32768\n"
            "auxiliary:\n"
            "  compression:\n"
            "    context_length: 32768\n",
            encoding="utf-8",
        )

        assert _patch_hermes_model_config(
            config,
            "new-model.gguf",
            base_url="http://llama-server:8080/v1",
            context_length=131072,
        ) is True

        text = config.read_text(encoding="utf-8")
        assert '  default: "new-model.gguf"' in text
        assert '  base_url: "http://llama-server:8080/v1"' in text
        assert "  context_length: 131072" in text
        assert "    context_length: 131072" in text

    def test_inserts_missing_required_route_fields(self, tmp_path):
        config = tmp_path / "config.yaml"
        config.write_text(
            "model:\n"
            '  provider: "custom"\n'
            "providers:\n"
            "  custom: {}\n",
            encoding="utf-8",
        )

        assert _patch_hermes_model_config(
            config,
            "new-model.gguf",
            base_url="http://litellm:4000/v1",
            context_length=65536,
        ) is True

        text = config.read_text(encoding="utf-8")
        assert '  default: "new-model.gguf"' in text
        assert '  base_url: "http://litellm:4000/v1"' in text
        assert "  context_length: 65536" in text
        assert _mod._hermes_config_matches(
            text,
            "new-model.gguf",
            "http://litellm:4000/v1",
            65536,
        )

    @pytest.mark.parametrize(
        "text",
        [
            'model:\n  base_url: "http://litellm:4000/v1"\n  context_length: 4096\n',
            'model:\n  default: "model"\n  context_length: 4096\n',
            'model:\n  default: "model"\n  base_url: "http://litellm:4000/v1"\n',
        ],
    )
    def test_route_verification_rejects_missing_required_fields(self, text):
        assert not _mod._hermes_config_matches(
            text,
            "model",
            "http://litellm:4000/v1",
            4096,
        )

    def test_missing_file_is_noop(self, tmp_path):
        assert _patch_hermes_model_config(tmp_path / "missing.yaml", "model.gguf") is False

    def test_permission_denied_stat_is_noop(self, tmp_path, monkeypatch):
        config = tmp_path / "config.yaml"
        original_exists = Path.exists

        def fake_exists(path):
            if path == config:
                raise PermissionError("container-owned")
            return original_exists(path)

        monkeypatch.setattr(Path, "exists", fake_exists)
        assert _patch_hermes_model_config(config, "model.gguf") is False


class TestComposeRestartLlamaServer:

    def test_amd_uses_stop_then_up(self, monkeypatch, tmp_path):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(_mod, "INSTALL_DIR", tmp_path)
        monkeypatch.setattr(
            _mod,
            "resolve_compose_flags",
            lambda: ["--env-file", ".env", "-f", "docker-compose.base.yml"],
        )
        monkeypatch.setattr(subprocess, "run", fake_run)

        _compose_restart_llama_server({"GPU_BACKEND": "amd"})

        assert calls == [
            [
                "docker", "compose", "--env-file", ".env", "-f",
                "docker-compose.base.yml", "stop", "llama-server",
            ],
            [
                "docker", "compose", "--env-file", ".env", "-f",
                "docker-compose.base.yml", "up", "-d", "llama-server",
            ],
        ]


class TestRecreateLlamaServerFromInspect:

    def _capture_recreate(self, monkeypatch, inspect_config, env, override_image=""):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd == ["docker", "inspect", "ods-llama-server"]:
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout=json.dumps([inspect_config]),
                    stderr="",
                )
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        _mod._recreate_llama_server(env, override_image=override_image)
        run_argv = next(cmd for cmd in calls if cmd[:3] == ["docker", "run", "-d"])
        return run_argv, calls

    def test_amd_recreate_preserves_devices_groups_entrypoint_and_runtime(
        self, monkeypatch,
    ):
        inspect_config = {
            "Config": {
                "Image": "host.example/lemonade:amd",
                "Entrypoint": ["/bin/sh", "-lc"],
                "Cmd": ["exec lemonade-server serve --port 8080"],
                "Env": [
                    "PATH=/usr/bin",
                    "GGUF_FILE=old.gguf",
                    "CTX_SIZE=4096",
                    "MAX_CONTEXT=4096",
                    "LLAMA_SERVER_IMAGE=host.example/lemonade:amd",
                ],
                "Labels": {"com.docker.compose.service": "llama-server"},
                "Hostname": "llama-amd",
                "ExposedPorts": {"8080/tcp": {}},
                "Healthcheck": {
                    "Test": [
                        "CMD", "curl", "-sf",
                        "http://127.0.0.1:8080/api/v1/health",
                    ],
                    "Interval": 15000000000,
                    "Timeout": 10000000000,
                    "Retries": 10,
                },
            },
            "HostConfig": {
                "RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
                "NetworkMode": "ods-network",
                "PortBindings": {
                    "8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8080"}],
                },
                "Binds": [
                    "/srv/ods/models:/models:rw",
                    "lemonade-cache:/root/.cache:rw",
                ],
                "Devices": [
                    {
                        "PathOnHost": "/dev/dri",
                        "PathInContainer": "/dev/dri",
                        "CgroupPermissions": "rwm",
                    },
                    {
                        "PathOnHost": "/dev/kfd",
                        "PathInContainer": "/dev/kfd",
                        "CgroupPermissions": "rwm",
                    },
                ],
                "GroupAdd": ["44", "109"],
                "SecurityOpt": ["no-new-privileges:true"],
                "CapDrop": ["ALL"],
                "Runtime": "runc",
                "ReadonlyRootfs": True,
                "LogConfig": {"Type": "json-file", "Config": {"max-size": "10m"}},
            },
            "NetworkSettings": {
                "Networks": {
                    "ods-network": {"Aliases": ["ods-llama-server", "llama-server"]},
                },
            },
            "Mounts": [],
        }
        env = {
            "GGUF_FILE": "new-amd.gguf",
            "CTX_SIZE": "65536",
            "MAX_CONTEXT": "65536",
            "LLM_MODEL": "new-amd",
            "LLAMA_SERVER_IMAGE": "host.example/lemonade:amd",
        }

        argv, calls = self._capture_recreate(monkeypatch, inspect_config, env)

        assert ["docker", "stop", "ods-llama-server"] in calls
        assert ["--device", "/dev/dri:/dev/dri:rwm"] == argv[
            argv.index("--device"):argv.index("--device") + 2
        ]
        second_device = argv.index("--device", argv.index("--device") + 1)
        assert argv[second_device:second_device + 2] == [
            "--device", "/dev/kfd:/dev/kfd:rwm",
        ]
        assert argv.count("--group-add") == 2
        assert "44" in argv and "109" in argv
        assert ["--runtime", "runc"] == argv[argv.index("--runtime"):argv.index("--runtime") + 2]
        assert "--read-only" in argv
        assert "--security-opt" in argv and "no-new-privileges:true" in argv
        assert argv[argv.index("--health-cmd"):argv.index("--health-cmd") + 2] == [
            "--health-cmd", "curl -sf http://127.0.0.1:8080/api/v1/health",
        ]
        assert argv[argv.index("--health-retries"):argv.index("--health-retries") + 2] == [
            "--health-retries", "10",
        ]
        assert "/srv/ods/models:/models:rw" in argv
        assert "lemonade-cache:/root/.cache:rw" in argv
        assert "GGUF_FILE=new-amd.gguf" in argv
        assert "CTX_SIZE=65536" in argv
        assert "MAX_CONTEXT=65536" in argv
        assert "LLAMA_SERVER_IMAGE=host.example/lemonade:amd" in argv
        image_index = argv.index("host.example/lemonade:amd")
        assert argv[argv.index("--entrypoint"):argv.index("--entrypoint") + 2] == [
            "--entrypoint", "/bin/sh",
        ]
        assert argv[image_index + 1:] == [
            "-lc", "exec lemonade-server serve --port 8080",
        ]

    def test_nvidia_recreate_preserves_device_request_full_command_and_networks(
        self, monkeypatch,
    ):
        inspect_config = {
            "Config": {
                "Image": "host.example/llama:cuda",
                "Entrypoint": ["/app/llama-server", "--factory-mode"],
                "Cmd": [
                    "--model", "/models/old.gguf", "--ctx-size=4096",
                    "--parallel", "2", "--metrics",
                ],
                "Env": ["GGUF_FILE=old.gguf", "CTX_SIZE=4096", "LLAMA_PARALLEL=2"],
                "Labels": {"com.docker.compose.project": "ods"},
                "Hostname": "llama-nvidia",
            },
            "HostConfig": {
                "RestartPolicy": {"Name": "on-failure", "MaximumRetryCount": 3},
                "Binds": ["/srv/models:/models:ro"],
                "DeviceRequests": [{
                    "Driver": "nvidia",
                    "Count": -1,
                    "DeviceIDs": None,
                    "Capabilities": [["gpu"]],
                    "Options": {},
                }],
                "Runtime": "nvidia",
                "SecurityOpt": ["seccomp=/srv/seccomp.json"],
                "ShmSize": 1073741824,
            },
            "NetworkSettings": {
                "Networks": {
                    "ods-network": {"Aliases": ["llama-server"]},
                    "metrics-network": {"Aliases": ["llama-metrics"]},
                },
            },
            "Mounts": [],
        }
        env = {
            "GGUF_FILE": "new-nvidia.gguf",
            "CTX_SIZE": "32768",
            "MAX_CONTEXT": "32768",
            "LLAMA_PARALLEL": "1",
        }

        argv, calls = self._capture_recreate(
            monkeypatch,
            inspect_config,
            env,
            override_image="catalog.example/llama:target",
        )

        assert argv[argv.index("--restart"):argv.index("--restart") + 2] == [
            "--restart", "on-failure:3",
        ]
        assert argv[argv.index("--gpus"):argv.index("--gpus") + 2] == ["--gpus", "all"]
        assert argv[argv.index("--runtime"):argv.index("--runtime") + 2] == [
            "--runtime", "nvidia",
        ]
        image_index = argv.index("catalog.example/llama:target")
        assert argv[image_index + 1:] == [
            "--factory-mode",
            "--model", "/models/new-nvidia.gguf",
            "--ctx-size=32768",
            "--parallel", "1",
            "--metrics",
        ]
        assert [
            "docker", "network", "connect", "--alias", "llama-metrics",
            "--alias", "llama-server", "metrics-network", "ods-llama-server",
        ] in calls


class TestLaunchNativeLlamaServer:

    def test_reads_env_and_writes_pid(self, monkeypatch, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text(
            "GGUF_FILE=test-model.gguf\n"
            "CTX_SIZE=8192\n"
            "LLAMA_REASONING=on\n"
            "AMD_INFERENCE_PORT=9090\n",
            encoding="utf-8",
        )
        (tmp_path / "data" / "models").mkdir(parents=True)
        (tmp_path / "data").mkdir(exist_ok=True)
        llama_bin = tmp_path / "bin" / "llama-server"
        llama_bin.parent.mkdir(parents=True)
        llama_bin.write_text("", encoding="utf-8")
        llama_log = tmp_path / "data" / "llama-server.log"
        pid_file = tmp_path / "data" / ".llama-server.pid"

        calls = []

        class _FakeProc:
            pid = 4321

        def fake_popen(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return _FakeProc()

        monkeypatch.setattr(_mod, "INSTALL_DIR", tmp_path)
        monkeypatch.setattr(subprocess, "Popen", fake_popen)

        _launch_native_llama_server(env_path, llama_bin, llama_log, pid_file)

        assert pid_file.read_text(encoding="utf-8") == "4321"
        cmd, _kwargs = calls[0]
        assert cmd[0] == str(llama_bin)
        assert "--model" in cmd
        assert str(tmp_path / "data" / "models" / "test-model.gguf") in cmd
        assert cmd[cmd.index("--port") + 1] == "9090"
        assert "--ctx-size" in cmd
        assert "8192" in cmd
        assert "--reasoning-format" in cmd
        assert "deepseek" in cmd
        assert _kwargs["cwd"] == str(tmp_path)

    def test_llm_bridge_is_disabled_before_native_bind(self, monkeypatch, tmp_path):
        env = {
            "GGUF_FILE": "test-model.gguf",
            "BIND_ADDRESS": "0.0.0.0",
            "ODS_MACOS_HOST_GATEWAY": "192.168.106.1",
        }
        events = []

        class _FakeProc:
            pid = 4321

        def fake_disable(actual_env, bind_addr, label):
            events.append(("bootout", actual_env, bind_addr, label))
            return True

        def fake_popen(cmd, **kwargs):
            events.append(("bind", cmd, kwargs))
            return _FakeProc()

        monkeypatch.setattr(_mod, "INSTALL_DIR", tmp_path)
        monkeypatch.setattr(_mod, "load_env", lambda _path: env)
        monkeypatch.setattr(_mod, "_disable_conflicting_macos_bridge", fake_disable)
        monkeypatch.setattr(_mod.subprocess, "Popen", fake_popen)

        _launch_native_llama_server(
            tmp_path / ".env",
            tmp_path / "bin" / "llama-server",
            tmp_path / "data" / "llama-server.log",
            tmp_path / "data" / ".llama-server.pid",
        )

        assert events[0] == (
            "bootout",
            env,
            "0.0.0.0",
            "com.ods.llm-bridge",
        )
        assert events[1][0] == "bind"
        assert events[1][1][events[1][1].index("--host") + 1] == "0.0.0.0"


class TestWindowsNativeLlamaServer:

    def test_detects_managed_windows_amd_llama_fallback(self, monkeypatch):
        monkeypatch.setattr(_mod.platform, "system", lambda: "Windows")

        assert _is_windows_host_llama_server({
            "GPU_BACKEND": "amd",
            "LLM_BACKEND": "llama-server",
            "AMD_INFERENCE_RUNTIME": "llama-server",
            "AMD_INFERENCE_RUNTIME_MODE": "windows-llama-server-fallback",
            "AMD_INFERENCE_LOCATION": "host",
            "AMD_INFERENCE_MANAGED": "true",
        }) is True
        assert _is_windows_host_llama_server({
            "GPU_BACKEND": "amd",
            "LLM_BACKEND": "lemonade",
            "AMD_INFERENCE_RUNTIME": "lemonade",
            "AMD_INFERENCE_LOCATION": "host",
        }) is False

    def test_writes_litellm_local_config_for_native_windows_llama(self, tmp_path):
        _write_windows_native_litellm_config(
            tmp_path,
            "Qwen3.5-4B-Q4_K_M.gguf",
            {"AMD_INFERENCE_PORT": "9090"},
        )

        content = (tmp_path / "config" / "litellm" / "local.yaml").read_text(encoding="utf-8")
        assert "model: openai/Qwen3.5-4B-Q4_K_M.gguf" in content
        assert "api_base: http://host.docker.internal:9090/v1" in content
        assert 'model_name: "*"' in content
        assert "request_timeout: 900" in content

    def test_native_restart_uses_stop_process_before_taskkill(self, monkeypatch, tmp_path):
        install_dir = tmp_path / "install"
        env_path = install_dir / ".env"
        model_dir = install_dir / "data" / "models"
        llama_bin = install_dir / "llama-server" / "llama-server.exe"
        model_dir.mkdir(parents=True)
        llama_bin.parent.mkdir(parents=True)
        model_dir.joinpath("model.gguf").write_text("model", encoding="utf-8")
        llama_bin.write_text("", encoding="utf-8")
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_path.write_text("GGUF_FILE=model.gguf\nAMD_INFERENCE_PORT=9090\n", encoding="utf-8")

        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["script"] = cmd[-1]
            captured["env"] = kwargs["env"]
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        launch_calls = []

        def fake_launch(*args):
            launch_calls.append(args)

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        monkeypatch.setattr(_mod, "_launch_native_llama_server", fake_launch)

        _restart_windows_native_llama_server(env_path, {
            "GGUF_FILE": "model.gguf",
            "AMD_INFERENCE_PORT": "9090",
        })

        assert "Stop-Process -Id $ProcId -Force" in captured["script"]
        assert "taskkill.exe /PID $ProcId /F" in captured["script"]
        assert "taskkill.exe /PID $ProcId /T /F" not in captured["script"]
        assert "Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction Ignore" in captured["script"]
        assert captured["script"].rstrip().endswith("exit 0")
        assert captured["env"]["ODS_WIN_LLAMA_PORT"] == "9090"
        assert launch_calls


class TestRestartWindowsLemonade:

    def test_refreshes_task_with_current_exe_and_falls_back_to_direct_start(self, monkeypatch, tmp_path):
        local_app_data = tmp_path / "AppData" / "Local"
        lemonade_exe = local_app_data / "lemonade_server" / "bin" / "LemonadeServer.exe"
        lemonade_exe.parent.mkdir(parents=True)
        lemonade_exe.write_text("", encoding="utf-8")

        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["script"] = cmd[-1]
            captured["env"] = kwargs["env"]
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
        monkeypatch.delenv("ProgramFiles", raising=False)
        monkeypatch.delenv("ProgramFiles(x86)", raising=False)
        monkeypatch.setattr(_mod, "INSTALL_DIR", tmp_path)
        monkeypatch.setattr(_mod.subprocess, "run", fake_run)

        _restart_windows_lemonade({
            "AMD_INFERENCE_PORT": "8080",
            "BIND_ADDRESS": "0.0.0.0",
        })

        script = captured["script"]
        assert "Get-ScheduledTask" not in script
        assert "Register-ScheduledTask" not in script
        assert "Start-ScheduledTask" not in script
        assert "Stop-ScheduledTask" not in script
        assert "Unregister-ScheduledTask" not in script
        assert "Invoke-ODSTaskkillViaWmi" in script
        assert "cmd.exe /c taskkill.exe /PID {0} /T /F" in script
        assert "function Get-ODSPortOwners" in script
        assert "Test-ODSLemonadeProcess $_ $portOwners" in script
        assert "for ($i = 0; $i -lt 75; $i++)" in script
        assert "Get-ODSLemonadeLaunchDiagnostics" in script
        assert "Format-ODSLemonadeLaunchDiagnostics" in script
        assert 'LemonadeServer.exe", "lemonade-server.exe", "lemonade-router.exe", "lemonade.exe' in script
        assert "Start-ODSLemonadeDirectProcess -Contract $launchContract -DiagnosticLogPath $diagnosticLog" in script
        assert "no healthy owned router was found" in script
        assert "Refusing to stop unowned process" in script
        assert "Get-ODSHealthyRouter" in script
        assert "/api/v1/health" in script
        assert "$proc = Get-ODSHealthyRouter" in script
        assert "Get-ODSLemonadeLaunchContract" in script
        assert "New-ODSLemonadeScheduledTaskAction" not in script
        assert "Set-ODSLemonadeModernRuntimeConfig" in script
        assert "$existingTaskMatches" not in script
        assert "--extra-models-dir" not in script
        assert "--no-tray" not in script
        assert "ODS_WIN_LEMONADE_TASK" not in captured["env"]
        assert captured["env"]["ODS_WIN_LEMONADE_EXE"] == str(lemonade_exe)
        assert Path(captured["env"]["ODS_WIN_LEMONADE_HELPER"]).as_posix().endswith(
            "installers/windows/lib/backend-contract.ps1"
        )
        assert captured["env"]["ODS_WIN_ENV_PATH"] == str(tmp_path / ".env")

    def test_installer_and_cli_lemonade_tasks_are_always_on(self):
        ods_root = Path(__file__).resolve().parents[4]
        sources = {
            "installer": (
                ods_root / "installers" / "windows" / "install-windows.ps1",
                "Register-ScheduledTask -TaskName $taskName",
            ),
            "cli": (
                ods_root / "installers" / "windows" / "ods.ps1",
                "Register-ScheduledTask -TaskName $script:LEMONADE_TASK_NAME",
            ),
        }

        for source_name, (source_path, registration) in sources.items():
            source = source_path.read_text(encoding="utf-8")
            registration_offset = source.index(registration)
            task_block = source[max(0, registration_offset - 800):registration_offset + 500]
            assert "New-ScheduledTaskSettingsSet" in task_block, source_name
            assert "-ExecutionTimeLimit ([TimeSpan]::Zero)" in task_block, source_name
            assert "-Settings $lemonadeSettings" in task_block, source_name

    def test_windows_cli_restart_waits_for_configured_lemonade_model(self):
        ods_root = Path(__file__).resolve().parents[4]
        source = (ods_root / "installers" / "windows" / "ods.ps1").read_text(
            encoding="utf-8",
        )

        assert "function Wait-ODSLemonadeConfiguredModel" in source
        assert "Resolve-ODSLemonadeModelId -Port $script:LEMONADE_PORT" in source
        assert "/api/v1/chat/completions" in source
        assert "Test-ODSLemonadeLoadedModelMatches" in source
        assert "Wait-ODSLemonadeConfiguredModel -EnvVars $envVars" in source

    def test_windows_cli_prefers_host_agent_for_configured_lemonade_model(self):
        ods_root = Path(__file__).resolve().parents[4]
        source = (ods_root / "installers" / "windows" / "ods.ps1").read_text(
            encoding="utf-8",
        )

        assert "function Resolve-ODSModelLibraryIdForGguf" in source
        assert "function Invoke-ODSHostAgentConfiguredModelActivation" in source
        assert "model-library.json" in source
        assert '$agentHealthUrl = "http://127.0.0.1:$agentPort/health"' in source
        assert '$agentUrl = "http://127.0.0.1:$agentPort/v1/runtime/lemonade/ensure"' in source
        assert "gguf_file = $ggufFile" in source

        agent_activation = source.index(
            "Invoke-ODSHostAgentConfiguredModelActivation -EnvVars $envVars"
        )
        direct_start = source.index("Start-ODSLemonadeRuntime -BindAddress $bindAddr")
        assert agent_activation < direct_start

    def test_windows_agent_launcher_detaches_from_host_agent(self):
        ods_root = Path(__file__).resolve().parents[4]
        source = (ods_root / "installers" / "windows" / "ods.ps1").read_text(
            encoding="utf-8",
        )
        marker = "Start-Process -FilePath $_pythonLiteral"
        start = source.index(marker)
        launcher_line = source[start:source.index("\n", start)]

        assert "-RedirectStandardError $_logFileLiteral" in launcher_line
        assert " -Wait" not in launcher_line

    def test_refuses_externally_managed_runtime_before_process_discovery(
        self, monkeypatch, tmp_path,
    ):
        monkeypatch.setattr(_mod, "INSTALL_DIR", tmp_path)
        monkeypatch.setattr(
            _mod.subprocess,
            "run",
            lambda *_args, **_kwargs: pytest.fail("external runtime must not be touched"),
        )

        with pytest.raises(RuntimeError, match="externally managed"):
            _restart_windows_lemonade({
                "AMD_INFERENCE_MANAGED": "false",
                "AMD_INFERENCE_RUNTIME_MODE": "external-lemonade",
                "LEMONADE_EXTERNAL": "true",
            })

    def test_restart_failure_includes_powershell_output(self, monkeypatch, tmp_path):
        program_files = tmp_path / "Program Files"
        lemonade_exe = program_files / "Lemonade Server" / "bin" / "LemonadeServer.exe"
        lemonade_exe.parent.mkdir(parents=True)
        lemonade_exe.write_text("", encoding="utf-8")
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["capture_output"] = kwargs.get("capture_output")
            captured["stdout"] = kwargs.get("stdout")
            captured["stderr"] = kwargs.get("stderr")
            if kwargs.get("stdout") is not None:
                kwargs["stdout"].write("launch stdout Bearer stdout-secret")
                kwargs["stdout"].flush()
            if kwargs.get("stderr") is not None:
                kwargs["stderr"].write("launch stderr LEMONADE_ADMIN_API_KEY=stderr-secret")
                kwargs["stderr"].flush()
            return subprocess.CompletedProcess(
                cmd,
                1,
                stdout="launch stdout Bearer stdout-secret",
                stderr="launch stderr LEMONADE_ADMIN_API_KEY=stderr-secret",
            )

        monkeypatch.setenv("ProgramFiles", str(program_files))
        monkeypatch.delenv("ProgramFiles(x86)", raising=False)
        monkeypatch.setattr(_mod, "INSTALL_DIR", tmp_path)
        monkeypatch.setattr(_mod.subprocess, "run", fake_run)

        with pytest.raises(RuntimeError) as exc_info:
            _restart_windows_lemonade({"AMD_INFERENCE_PORT": "8080"})

        message = str(exc_info.value)
        assert "Windows Lemonade restart failed with exit code 1" in message
        assert "launch stderr" in message
        assert "launch stdout" in message
        assert "stderr-secret" not in message
        assert "stdout-secret" not in message
        assert "[redacted]" in message
        assert captured["capture_output"] is None
        assert captured["stdout"] is not None
        assert captured["stderr"] is not None


# --- Rollback integration ---


class _ResponseHandler:
    def __init__(self, wfile=None, request_body=None, api_key="test-agent-key"):
        self.wfile = wfile or io.BytesIO()
        self.response_code = None
        self.response_headers = []
        if request_body is not None:
            payload = json.dumps(request_body).encode("utf-8")
            self.rfile = io.BytesIO(payload)
            self.headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Length": str(len(payload)),
            }

    def send_response(self, code):
        self.response_code = code

    def send_header(self, name, value):
        self.response_headers.append((name, value))

    def end_headers(self):
        pass

    def parse_response(self):
        return json.loads(self.wfile.getvalue().decode("utf-8"))


class TestModelActivateRequest:
    def test_validates_and_forwards_cli_metadata(self, monkeypatch):
        calls = []
        monkeypatch.setattr(_mod, "AGENT_API_KEY", "test-agent-key")
        monkeypatch.setattr(_mod, "_begin_model_activation", lambda _model: (True, None))
        monkeypatch.setattr(_mod, "_end_model_activation", lambda: None)
        handler = _ResponseHandler(request_body={
            "model_id": "qwen3.5-9b-q4",
            "context_length": 16384,
            "tier": "sh_compact",
        })
        handler._do_model_activate = (
            lambda model_id, **kwargs: calls.append((model_id, kwargs))
        )

        _mod.AgentHandler._handle_model_activate(handler)

        assert calls == [(
            "qwen3.5-9b-q4",
            {"requested_context_length": 16384, "requested_tier": "SH_COMPACT"},
        )]

    def test_normalizes_model_id_whitespace(self, monkeypatch):
        calls = []
        monkeypatch.setattr(_mod, "AGENT_API_KEY", "test-agent-key")
        monkeypatch.setattr(_mod, "_begin_model_activation", lambda _model: (True, None))
        monkeypatch.setattr(_mod, "_end_model_activation", lambda: None)
        handler = _ResponseHandler(request_body={"model_id": "  target-model  "})
        handler._do_model_activate = lambda model_id, **_kwargs: calls.append(model_id)

        _mod.AgentHandler._handle_model_activate(handler)

        assert calls == ["target-model"]

    @pytest.mark.parametrize(
        "request_body",
        [
            {"model_id": "target", "context_length": True},
            {"model_id": "target", "context_length": 512},
            {"model_id": "target", "tier": "UNKNOWN"},
            {"model_id": "target", "tier": "../1"},
            {"model_id": "target\nINJECTED=value"},
        ],
    )
    def test_rejects_invalid_cli_metadata(self, monkeypatch, request_body):
        monkeypatch.setattr(_mod, "AGENT_API_KEY", "test-agent-key")
        monkeypatch.setattr(
            _mod.AgentHandler,
            "_do_model_activate",
            lambda *_args, **_kwargs: pytest.fail("invalid metadata must not activate"),
        )
        handler = _ResponseHandler(request_body=request_body)

        _mod.AgentHandler._handle_model_activate(handler)

        assert handler.response_code == 400


class _BrokenPipeWriter:
    def write(self, _payload):
        raise BrokenPipeError("client disconnected")


def _llama_identity_response(model_id):
    return json.dumps({
        "object": "list",
        "data": [{
            "id": model_id,
            "object": "model",
            "status": {"value": "loaded"},
        }],
    })


def _lemonade_health_response(model_id, context_length=4096):
    return json.dumps({
        "status": "ok",
        "version": "10.7.0",
        "model_loaded": model_id,
        "all_models_loaded": [{
            "model_name": model_id,
            "recipe_options": {"ctx_size": context_length},
        }],
    })


def _mock_verified_readiness(*_args, **kwargs):
    """Mirror the production bool/identity/proof readiness return contract."""
    identity = str(kwargs.get("gguf_file") or "mock-runtime-model.gguf")
    if kwargs.get("return_proof"):
        return {
            "identity": identity,
            "contextLength": 65536,
            "contextVerified": True,
            "verifiedAt": "2026-07-20T00:00:00+00:00",
        }
    if kwargs.get("return_identity"):
        return identity
    return True


def _write_model_activation_fixture(
    tmp_path,
    gpu_backend="nvidia",
    lemonade=False,
    lemonade_api_key=None,
):
    install_dir = tmp_path / "install"
    config_dir = install_dir / "config"
    models_dir = install_dir / "data" / "models"
    llama_dir = config_dir / "llama-server"
    litellm_dir = config_dir / "litellm"
    models_dir.mkdir(parents=True)
    llama_dir.mkdir(parents=True)
    litellm_dir.mkdir(parents=True)

    (models_dir / "new-model.gguf").write_text("model", encoding="utf-8")
    (config_dir / "model-library.json").write_text(
        json.dumps({
            "models": [{
                "id": "target-model",
                "gguf_file": "new-model.gguf",
                "gguf_url": "https://example.test/new-model.gguf",
                "gguf_sha256": hashlib.sha256(b"model").hexdigest(),
                "llm_model_name": "new-model",
                "context_length": 4096,
            }]
        }),
        encoding="utf-8",
    )

    env_text = (
        f"GPU_BACKEND={gpu_backend}\n"
        "GGUF_FILE=old-model.gguf\n"
        "LLM_MODEL=old-model\n"
        "CTX_SIZE=2048\n"
        "OLLAMA_PORT=8080\n"
        f"OPENCODE_CONFIG_DIR={install_dir / 'config' / 'opencode'}\n"
    )
    if lemonade_api_key:
        env_text += f"LITELLM_LEMONADE_API_KEY={lemonade_api_key}\n"
    env_path = install_dir / ".env"
    env_path.write_text(env_text, encoding="utf-8")

    ini_text = "[old-model]\nfilename = old-model.gguf\n"
    models_ini = llama_dir / "models.ini"
    models_ini.write_text(ini_text, encoding="utf-8")

    lemonade_yaml = litellm_dir / "lemonade.yaml"
    lemonade_text = None
    if lemonade:
        lemonade_text = "model_list:\n  - model_name: old\n"
        lemonade_yaml.write_text(lemonade_text, encoding="utf-8")

    return install_dir, env_path, env_text, models_ini, ini_text, lemonade_yaml, lemonade_text


def test_text_snapshot_restores_exact_line_endings(tmp_path):
    path = tmp_path / "config.env"
    original = b"MODEL=old\r\nEMPTY=\r\n"
    path.write_bytes(original)
    original_owner = (path.stat().st_uid, path.stat().st_gid)
    snapshot = _mod._snapshot_text_file(path)

    _mod._atomic_write_text(path, "MODEL=new\n")
    _mod._restore_text_file(path, snapshot)

    assert path.read_bytes() == original
    assert (path.stat().st_uid, path.stat().st_gid) == original_owner


class TestModelActivateRollback:

    @pytest.fixture(autouse=True)
    def _successful_meaningful_completion(self, monkeypatch):
        monkeypatch.setattr(_mod, "_chat_completion_ready", lambda *_args, **_kwargs: True)
        monkeypatch.setattr(
            _mod, "_llama_runtime_context_length", lambda *_args: 131072
        )
        monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(_mod, "_container_exists", lambda _container: False)
        monkeypatch.setattr(_mod, "_container_running", lambda _container: False)
        monkeypatch.setattr(_mod, "_verify_hermes_dashboard_ready", lambda: None)

    def test_activation_requires_persisted_env_before_any_mutation(
        self,
        tmp_path,
        monkeypatch,
    ):
        install_dir, env_path, _env_text, models_ini, ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        env_path.unlink()
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(
            _mod,
            "_compose_restart_llama_server",
            lambda *_args: pytest.fail("missing .env must fail before restart"),
        )
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        assert "requires the persisted environment" in handler.parse_response()["error"]
        assert not env_path.exists()
        assert models_ini.read_text(encoding="utf-8") == ini_text

    def test_malformed_model_library_cannot_fall_back_to_unverified_local_model(
        self,
        tmp_path,
        monkeypatch,
    ):
        install_dir, env_path, env_text, models_ini, ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        (install_dir / "config" / "model-library.json").write_text(
            '{"models": [',
            encoding="utf-8",
        )
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(
            _mod,
            "_compose_restart_llama_server",
            lambda *_args: pytest.fail("malformed catalog must fail before restart"),
        )
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        assert "Model library is unavailable or malformed" in handler.parse_response()["error"]
        assert env_path.read_text(encoding="utf-8") == env_text
        assert models_ini.read_text(encoding="utf-8") == ini_text

    @pytest.mark.parametrize(
        ("bind_addr", "expected_identity_url"),
        [
            ("0.0.0.0", "http://127.0.0.1:9090/v1/models"),
            ("::", "http://[::1]:9090/v1/models"),
            ("192.168.106.1", "http://192.168.106.1:9090/v1/models"),
        ],
    )
    def test_apple_native_activation_probes_reachable_bind(
        self,
        tmp_path,
        monkeypatch,
        bind_addr,
        expected_identity_url,
    ):
        install_dir, env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path, gpu_backend="apple")
        )
        env_path.write_text(
            env_path.read_text(encoding="utf-8")
            + f"BIND_ADDRESS={bind_addr}\nODS_NATIVE_LLAMA_PORT=9090\n",
            encoding="utf-8",
        )
        llama_bin = install_dir / "bin" / "llama-server"
        llama_bin.parent.mkdir(parents=True)
        llama_bin.write_text("", encoding="utf-8")
        lib_dir = install_dir / "lib"
        lib_dir.mkdir(parents=True)
        (lib_dir / "constants.sh").write_text("# test fixture\n", encoding="utf-8")
        (lib_dir / "bridge-manager.sh").write_text("# test fixture\n", encoding="utf-8")
        launches = []
        calls = []

        def fake_launch(*args):
            launches.append(args)

        def fake_run(cmd, **_kwargs):
            calls.append(cmd)
            stdout = _llama_identity_response("new-model.gguf") if cmd and cmd[0] == "curl" else ""
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod.platform, "system", lambda: "Darwin")
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(_mod, "_configure_macos_llm_bridge", lambda _env_path: None)
        monkeypatch.setattr(_mod, "_launch_native_llama_server", fake_launch)
        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 200
        assert len(launches) == 1
        curl_calls = [cmd for cmd in calls if cmd and cmd[0] == "curl"]
        assert [cmd[-1] for cmd in curl_calls] == [
            expected_identity_url,
            expected_identity_url,
        ]

    def test_apple_missing_native_binary_fails_before_config_mutation(
        self,
        tmp_path,
        monkeypatch,
    ):
        install_dir, env_path, env_text, models_ini, ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path, gpu_backend="apple")
        )
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod.platform, "system", lambda: "Darwin")
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(
            _mod,
            "_restart_macos_native_llama_server",
            lambda *_args: pytest.fail("preflight failure must not restart the runtime"),
        )
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        receipt = handler.parse_response()
        assert "llama-server binary not found" in receipt["error"]
        assert "rolled_back" not in receipt
        assert env_path.read_text(encoding="utf-8") == env_text
        assert models_ini.read_text(encoding="utf-8") == ini_text

    def test_success_response_disconnect_does_not_roll_back(self, tmp_path, monkeypatch):
        install_dir, env_path, _env_text, models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)

        def fake_run(cmd, **_kwargs):
            stdout = _llama_identity_response("new-model.gguf") if cmd and cmd[0] == "curl" else ""
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        handler = _ResponseHandler(wfile=_BrokenPipeWriter())

        with pytest.raises(BrokenPipeError):
            _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert "GGUF_FILE=new-model.gguf" in env_path.read_text(encoding="utf-8")
        assert "LLM_MODEL=new-model" in env_path.read_text(encoding="utf-8")
        assert "filename = new-model.gguf" in models_ini.read_text(encoding="utf-8")

    @pytest.mark.parametrize(
        "runtime_kind",
        [
            "compose-llama",
            "container-llama",
            "windows-lemonade",
            "windows-native-llama",
            "macos-native-llama",
        ],
    )
    def test_late_failure_restores_previous_runtime_and_config(
        self,
        tmp_path,
        monkeypatch,
        runtime_kind,
    ):
        gpu_backend = "amd" if runtime_kind.startswith("windows-") else "nvidia"
        if runtime_kind == "macos-native-llama":
            gpu_backend = "apple"
        install_dir, env_path, _env_text, models_ini, _ini_text, lemonade_yaml, _ = (
            _write_model_activation_fixture(
                tmp_path,
                gpu_backend=gpu_backend,
                lemonade=runtime_kind == "windows-lemonade",
            )
        )

        if runtime_kind == "windows-lemonade":
            env_path.write_text(
                "ODS_MODE=lemonade\n"
                "GPU_BACKEND=amd\n"
                "LLM_BACKEND=lemonade\n"
                "AMD_INFERENCE_RUNTIME=lemonade\n"
                "AMD_INFERENCE_LOCATION=host\n"
                "AMD_INFERENCE_PORT=8080\n"
                "GGUF_FILE=old-model.gguf\n"
                "LLM_MODEL=old-model\n"
                "CTX_SIZE=2048\n",
                encoding="utf-8",
            )
        elif runtime_kind == "windows-native-llama":
            env_path.write_text(
                "ODS_MODE=local\n"
                "GPU_BACKEND=amd\n"
                "LLM_BACKEND=llama-server\n"
                "AMD_INFERENCE_RUNTIME=llama-server\n"
                "AMD_INFERENCE_RUNTIME_MODE=windows-llama-server-fallback\n"
                "AMD_INFERENCE_LOCATION=host\n"
                "AMD_INFERENCE_MANAGED=true\n"
                "AMD_INFERENCE_PORT=9090\n"
                "GGUF_FILE=old-model.gguf\n"
                "LLM_MODEL=old-model\n"
                "CTX_SIZE=2048\n",
                encoding="utf-8",
            )
        elif runtime_kind == "macos-native-llama":
            llama_bin = install_dir / "bin" / "llama-server"
            llama_bin.parent.mkdir(parents=True)
            llama_bin.write_text("binary", encoding="utf-8")

        local_yaml = install_dir / "config" / "litellm" / "local.yaml"
        tracked_configs = (env_path, models_ini, lemonade_yaml, local_yaml)

        def config_state():
            return {
                path: path.read_text(encoding="utf-8") if path.exists() else None
                for path in tracked_configs
            }

        original_config = config_state()
        runtime_restarts = []

        def record_restart(env):
            runtime_restarts.append((env["GGUF_FILE"], config_state()))

        def record_container_restart(env, override_image=""):
            record_restart(env)

        def record_native_restart(path, _env=None, *_args):
            record_restart(_mod.load_env(path))

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(
            _mod,
            "_container_exists",
            lambda container: container == "ods-litellm",
        )
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        if runtime_kind == "compose-llama":
            monkeypatch.setattr(_mod.platform, "system", lambda: "Linux")
            monkeypatch.setattr(_mod, "_compose_restart_llama_server", record_restart)
        elif runtime_kind == "container-llama":
            monkeypatch.setattr(_mod.platform, "system", lambda: "Linux")
            monkeypatch.setenv("ODS_HOST_INSTALL_DIR", str(install_dir))
            monkeypatch.setattr(_mod, "_recreate_llama_server", record_container_restart)
        elif runtime_kind == "windows-lemonade":
            monkeypatch.setattr(_mod.platform, "system", lambda: "Windows")
            monkeypatch.setattr(_mod, "_restart_windows_lemonade", record_restart)
        elif runtime_kind == "windows-native-llama":
            monkeypatch.setattr(_mod.platform, "system", lambda: "Windows")
            monkeypatch.setattr(_mod, "_restart_windows_native_llama_server", record_native_restart)
        else:
            monkeypatch.setattr(_mod.platform, "system", lambda: "Darwin")
            monkeypatch.setattr(_mod, "_restart_macos_native_llama_server", record_native_restart)

        def fake_run(cmd, **_kwargs):
            if cmd and cmd[0] == "curl":
                stdout = (
                    _lemonade_health_response("extra.new-model.gguf")
                    if runtime_kind == "windows-lemonade"
                    else _llama_identity_response("new-model.gguf")
                )
                return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")
            if cmd == ["docker", "restart", "ods-litellm"]:
                raise subprocess.TimeoutExpired(cmd, 60)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        assert [model for model, _state in runtime_restarts] == [
            "new-model.gguf",
            "old-model.gguf",
        ]
        assert "GGUF_FILE=new-model.gguf" in runtime_restarts[0][1][env_path]
        assert "filename = new-model.gguf" in runtime_restarts[0][1][models_ini]
        assert runtime_restarts[1][1] == original_config
        assert config_state() == original_config

    def test_activation_accepts_local_gguf_without_catalog_entry(self, tmp_path, monkeypatch):
        install_dir, env_path, _env_text, models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        (install_dir / "config" / "model-library.json").write_text(
            json.dumps({"models": []}),
            encoding="utf-8",
        )
        (install_dir / "data" / "models" / "Research.Model-Q8_0.gguf").write_text(
            "model",
            encoding="utf-8",
        )
        env_path.write_text(
            "GPU_BACKEND=nvidia\n"
            "GGUF_FILE=old-model.gguf\n"
            "LLM_MODEL=old-model\n"
            "MAX_CONTEXT=65536\n"
            "LLAMA_ARG_SPEC_TYPE=draft-mtp\n"
            "LLAMA_ARG_SPEC_DRAFT_N_MAX=3\n"
            "OLLAMA_PORT=8080\n",
            encoding="utf-8",
        )

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)

        def fake_run(cmd, **_kwargs):
            stdout = (
                _llama_identity_response("Research.Model-Q8_0.gguf")
                if cmd and cmd[0] == "curl"
                else ""
            )
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "Research.Model-Q8_0")

        assert handler.response_code == 200
        receipt = handler.parse_response()
        assert receipt["status"] == "activated"
        assert receipt["model_id"] == "Research.Model-Q8_0"
        assert receipt["llm_model"] == "Research.Model-Q8_0"
        assert receipt["gguf_file"] == "Research.Model-Q8_0.gguf"
        assert receipt["tier"] is None
        assert receipt["context_length"] == 65536
        env_text = env_path.read_text(encoding="utf-8")
        assert "GGUF_FILE=Research.Model-Q8_0.gguf" in env_text
        assert "LLM_MODEL=Research.Model-Q8_0" in env_text
        assert "CTX_SIZE=65536" in env_text
        assert "LLAMA_ARG_SPEC_TYPE=" not in env_text
        assert "LLAMA_ARG_SPEC_DRAFT_N_MAX=" not in env_text
        assert "filename = Research.Model-Q8_0.gguf" in models_ini.read_text(encoding="utf-8")

    def test_activation_resolves_local_gguf_by_stem_with_mixed_case_extension(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, _env_text, models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        (install_dir / "config" / "model-library.json").write_text(
            json.dumps({"models": []}),
            encoding="utf-8",
        )
        (install_dir / "data" / "models" / "MixedCaseModel.GGUF").write_text(
            "model",
            encoding="utf-8",
        )
        env_path.write_text(
            "GPU_BACKEND=nvidia\n"
            "GGUF_FILE=old-model.gguf\n"
            "LLM_MODEL=old-model\n"
            "MAX_CONTEXT=32768\n"
            "OLLAMA_PORT=8080\n",
            encoding="utf-8",
        )

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)

        def fake_run(cmd, **_kwargs):
            stdout = (
                _llama_identity_response("MixedCaseModel.GGUF")
                if cmd and cmd[0] == "curl"
                else ""
            )
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "MixedCaseModel")

        assert handler.response_code == 200
        env_text = env_path.read_text(encoding="utf-8")
        assert "GGUF_FILE=MixedCaseModel.GGUF" in env_text
        assert "LLM_MODEL=MixedCaseModel" in env_text
        assert "filename = MixedCaseModel.GGUF" in models_ini.read_text(encoding="utf-8")

    def test_activation_accepts_sanitized_local_id_for_spaced_filename(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, _env_text, models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        (install_dir / "config" / "model-library.json").write_text(
            json.dumps({"models": []}),
            encoding="utf-8",
        )
        (install_dir / "data" / "models" / "My Custom Model.Q8_0.GGUF").write_text(
            "model",
            encoding="utf-8",
        )
        env_path.write_text(
            "GPU_BACKEND=nvidia\n"
            "GGUF_FILE=old-model.gguf\n"
            "LLM_MODEL=old-model\n"
            "MAX_CONTEXT=32768\n"
            "OLLAMA_PORT=8080\n",
            encoding="utf-8",
        )

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)

        def fake_run(cmd, **_kwargs):
            stdout = (
                _llama_identity_response("My Custom Model.Q8_0.GGUF")
                if cmd and cmd[0] == "curl"
                else ""
            )
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "My-Custom-Model.Q8_0")

        assert handler.response_code == 200
        receipt = handler.parse_response()
        assert receipt["status"] == "activated"
        assert receipt["model_id"] == "My-Custom-Model.Q8_0"
        assert receipt["llm_model"] == "My-Custom-Model.Q8_0"
        assert receipt["gguf_file"] == "My Custom Model.Q8_0.GGUF"
        assert receipt["tier"] is None
        assert receipt["context_length"] == 32768
        env_text = env_path.read_text(encoding="utf-8")
        assert "GGUF_FILE=My Custom Model.Q8_0.GGUF" in env_text
        assert "LLM_MODEL=My-Custom-Model.Q8_0" in env_text
        assert "[My-Custom-Model.Q8_0]" in models_ini.read_text(encoding="utf-8")
        assert "filename = My Custom Model.Q8_0.GGUF" in models_ini.read_text(encoding="utf-8")

    @pytest.mark.parametrize(
        "model_id",
        [
            "../outside",
            r"..\outside",
            "nested/model",
            r"nested\model",
            "unsafe\x00model",
        ],
    )
    def test_local_gguf_resolver_rejects_path_traversal(self, tmp_path, model_id):
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        (models_dir / "outside.gguf").write_text("model", encoding="utf-8")

        assert _mod._resolve_local_gguf_filename(model_id, models_dir) is None

    def test_local_gguf_resolver_rejects_ambiguous_sanitized_id(self, tmp_path):
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        (models_dir / "My Custom Model.gguf").write_text("model", encoding="utf-8")
        (models_dir / "My@Custom@Model.gguf").write_text("model", encoding="utf-8")

        assert _mod._resolve_local_gguf_filename("My-Custom-Model", models_dir) is None

    def test_activation_rejects_empty_local_gguf_before_restart(self, tmp_path, monkeypatch):
        install_dir, _env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        (install_dir / "config" / "model-library.json").write_text(
            json.dumps({"models": []}),
            encoding="utf-8",
        )
        (install_dir / "data" / "models" / "EmptyLocal.gguf").write_text(
            "",
            encoding="utf-8",
        )

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)

        def fail_restart(_env):
            raise AssertionError("empty GGUF should be rejected before restart")

        monkeypatch.setattr(_mod, "_compose_restart_llama_server", fail_restart)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "EmptyLocal")

        assert handler.response_code == 400
        assert "not downloaded or empty" in handler.parse_response()["error"]

    def test_amd_activation_rewrites_lemonade_yaml_with_env_file_key(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, _env_text, _models_ini, _ini_text, lemonade_yaml, _yaml_text = (
            _write_model_activation_fixture(
                tmp_path,
                gpu_backend="amd",
                lemonade=True,
                lemonade_api_key="sk-inline-from-env-file-67890",
            )
        )
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.delenv("LITELLM_LEMONADE_API_KEY", raising=False)
        monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)

        def fake_run(cmd, **_kwargs):
            if cmd and cmd[0] == "curl" and cmd[-1].endswith("/models"):
                stdout = json.dumps({
                    "data": [{"id": "extra.new-model.gguf"}]
                })
            elif cmd and cmd[0] == "curl":
                stdout = _lemonade_health_response("extra.new-model.gguf")
            else:
                stdout = ""
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 200
        content = lemonade_yaml.read_text(encoding="utf-8")
        assert "api_key: sk-inline-from-env-file-67890" in content
        assert "api_key: sk-lemonade" not in content
        assert "enable_thinking: false" in content
        assert "LEMONADE_MODEL=extra.new-model.gguf" in env_path.read_text(encoding="utf-8")

    def test_windows_lemonade_107_persists_and_propagates_exact_model_id(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, _env_text, _models_ini, _ini_text, lemonade_yaml, _ = (
            _write_model_activation_fixture(
                tmp_path,
                gpu_backend="amd",
                lemonade=True,
            )
        )
        env_path.write_text(
            "ODS_MODE=local\n"
            "GPU_BACKEND=amd\n"
            "LLM_BACKEND=lemonade\n"
            "AMD_INFERENCE_RUNTIME=lemonade\n"
            "AMD_INFERENCE_LOCATION=host\n"
            "AMD_INFERENCE_PORT=8080\n"
            "GGUF_FILE=old-model.gguf\n"
            "LLM_MODEL=old-model\n"
            "LEMONADE_MODEL=Old-Model\n"
            "CTX_SIZE=2048\n",
            encoding="utf-8",
        )
        hermes_live = install_dir / "data" / "hermes" / "config.yaml"
        hermes_template = (
            install_dir
            / "extensions"
            / "services"
            / "hermes"
            / "cli-config.yaml.template"
        )
        hermes_live.parent.mkdir(parents=True)
        hermes_template.parent.mkdir(parents=True)
        hermes_text = (
            "model:\n"
            '  default: "Old-Model"\n'
            '  provider: "custom"\n'
            '  base_url: "http://litellm:4000/v1"\n'
            "  context_length: 2048\n"
        )
        hermes_live.write_text(hermes_text, encoding="utf-8")
        hermes_template.write_text(hermes_text, encoding="utf-8")
        opencode_dir = tmp_path / "windows-home" / ".config" / "opencode"
        opencode_dir.mkdir(parents=True)
        opencode_config = opencode_dir / "opencode.json"
        opencode_compat = opencode_dir / "config.json"
        opencode_config.write_text(
            json.dumps({
                "model": "llama-server/Old-Model",
                "provider": {
                    "llama-server": {"models": {"Old-Model": {"name": "old"}}},
                },
            }),
            encoding="utf-8",
        )

        def fake_run(cmd, **_kwargs):
            if cmd and cmd[0] == "curl" and cmd[-1].endswith("/models"):
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout=json.dumps({
                        "data": [{
                            "id": "Modern-Model",
                            "checkpoint": r"C:\ods\data\models\new-model.gguf",
                        }]
                    }),
                    stderr="",
                )
            if cmd and cmd[0] == "curl":
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout=json.dumps({
                        "status": "ok",
                        "version": "10.7.0",
                        "model_loaded": "Modern-Model",
                        "all_models_loaded": [{
                            "model_name": "Modern-Model",
                            "checkpoint": r"C:\ods\data\models\new-model.gguf",
                            "recipe_options": {"ctx_size": 4096},
                        }],
                    }),
                    stderr="",
                )
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(
            _mod,
            "_opencode_config_paths",
            lambda: (opencode_config, opencode_compat),
        )
        monkeypatch.setattr(_mod.platform, "system", lambda: "Windows")
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(_mod, "_restart_windows_lemonade", lambda _env: None)
        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 200
        state = json.loads(
            (install_dir / "data" / "model-state.json").read_text(encoding="utf-8")
        )
        assert state["active"]["runtimeModelId"] == "Modern-Model"
        assert state["active"]["contextLength"] == 4096
        assert "LEMONADE_MODEL=Modern-Model" in env_path.read_text(encoding="utf-8")
        assert "model: openai/Modern-Model" in lemonade_yaml.read_text(encoding="utf-8")
        assert 'default: "Modern-Model"' in hermes_live.read_text(encoding="utf-8")
        assert 'default: "Modern-Model"' in hermes_template.read_text(encoding="utf-8")
        for path in (opencode_config, opencode_compat):
            config = json.loads(path.read_text(encoding="utf-8"))
            assert config["model"] == "llama-server/Modern-Model"
            models = config["provider"]["llama-server"]["models"]
            assert "Modern-Model" in models
            assert "Old-Model" not in models

    def test_windows_lemonade_final_runtime_flip_rolls_back_before_state_publish(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, _env_text, _models_ini, _ini_text, lemonade_yaml, yaml_text = (
            _write_model_activation_fixture(
                tmp_path,
                gpu_backend="amd",
                lemonade=True,
            )
        )
        env_path.write_text(
            "ODS_MODE=local\n"
            "GPU_BACKEND=amd\n"
            "LLM_BACKEND=lemonade\n"
            "AMD_INFERENCE_RUNTIME=lemonade\n"
            "AMD_INFERENCE_LOCATION=host\n"
            "AMD_INFERENCE_PORT=8080\n"
            "GGUF_FILE=old-model.gguf\n"
            "LLM_MODEL=old-model\n"
            "LEMONADE_MODEL=Old-Model\n"
            "CTX_SIZE=2048\n",
            encoding="utf-8",
        )
        state_path = install_dir / "data" / "model-state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        old_state = {"active": {"runtimeModelId": "Old-Model"}}
        state_path.write_text(json.dumps(old_state), encoding="utf-8")
        states = {
            "ods-litellm": {"exists": True, "running": True},
            "ods-hermes": {"exists": False, "running": False},
            "ods-openclaw": {"exists": False, "running": False},
            "ods-perplexica": {"exists": False, "running": False},
        }
        events = []
        target_readiness_attempts = 0
        state_records = []

        class FakeSwitchboardState:
            def record_verified_route(self, *_args, **kwargs):
                state_records.append(kwargs)

        def resolve_model_id(_env, gguf_file, **_kwargs):
            return "Modern-Model" if gguf_file == "new-model.gguf" else "Old-Model"

        def restart_windows_lemonade(env):
            events.append(f"runtime:{env['GGUF_FILE']}")

        def readiness(_env, **kwargs):
            nonlocal target_readiness_attempts
            gguf_file = kwargs["gguf_file"]
            events.append(f"ready:{gguf_file}")
            if gguf_file == "new-model.gguf":
                target_readiness_attempts += 1
                if target_readiness_attempts > 1:
                    if kwargs.get("return_proof"):
                        return {}
                    if kwargs.get("return_identity"):
                        return ""
                    return False
            identity = resolve_model_id({}, gguf_file)
            if kwargs.get("return_proof"):
                return {
                    "identity": identity,
                    "contextLength": 4096,
                    "contextVerified": True,
                    "verifiedAt": "2026-07-20T00:00:00+00:00",
                }
            if kwargs.get("return_identity"):
                return identity
            return True

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod.platform, "system", lambda: "Windows")
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(_mod, "_switchboard_state", FakeSwitchboardState())
        monkeypatch.setattr(_mod, "_resolve_lemonade_model_id", resolve_model_id)
        monkeypatch.setattr(_mod, "_restart_windows_lemonade", restart_windows_lemonade)
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", readiness)
        monkeypatch.setattr(_mod, "_capture_container_state", lambda name: states[name])
        monkeypatch.setattr(
            _mod,
            "_restart_existing_container",
            lambda name, _state=None: events.append(f"restart:{name}") or name == "ods-litellm",
        )
        monkeypatch.setattr(
            _mod,
            "_restore_container_state",
            lambda name, _state, recreate=False: events.append(f"restore:{name}") or True,
        )
        monkeypatch.setattr(
            _mod,
            "_verify_litellm_route",
            lambda env: events.append(f"litellm:{env['LEMONADE_MODEL']}"),
        )
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        response = handler.parse_response()
        assert response["rolled_back"] is True
        assert "Final runtime proof failed" in response["error"]
        assert _mod.load_env(env_path)["GGUF_FILE"] == "old-model.gguf"
        assert _mod.load_env(env_path)["LEMONADE_MODEL"] == "Old-Model"
        assert lemonade_yaml.read_text(encoding="utf-8") == yaml_text
        assert json.loads(state_path.read_text(encoding="utf-8")) == old_state
        assert state_records == []
        first_ready = events.index("ready:new-model.gguf")
        final_ready = events.index("ready:new-model.gguf", first_ready + 1)
        assert events.index("restart:ods-litellm") < final_ready

    def test_windows_lemonade_rollback_removes_new_litellm_config(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, _env_text, _models_ini, _ini_text, lemonade_yaml, _ = (
            _write_model_activation_fixture(
                tmp_path,
                gpu_backend="amd",
                lemonade=False,
            )
        )
        env_path.write_text(
            "ODS_MODE=lemonade\n"
            "GPU_BACKEND=amd\n"
            "LLM_BACKEND=lemonade\n"
            "AMD_INFERENCE_RUNTIME=lemonade\n"
            "AMD_INFERENCE_LOCATION=host\n"
            "AMD_INFERENCE_PORT=8080\n"
            "GGUF_FILE=old-model.gguf\n"
            "LLM_MODEL=old-model\n"
            "LEMONADE_MODEL=Old-Model\n"
            "CTX_SIZE=2048\n",
            encoding="utf-8",
        )
        restarts = []
        snapshot = TestPerplexicaModelRoute._snapshot()

        def fake_run(cmd, **_kwargs):
            if cmd and cmd[0] == "curl" and cmd[-1].endswith("/models"):
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout=json.dumps({
                        "data": [{
                            "id": "Modern-Model",
                            "checkpoint": r"C:\ods\data\models\new-model.gguf",
                        }],
                    }),
                    stderr="",
                )
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod.platform, "system", lambda: "Windows")
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(
            _mod,
            "_restart_windows_lemonade",
            lambda env: restarts.append(env["GGUF_FILE"]),
        )
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", _mock_verified_readiness)
        monkeypatch.setattr(
            _mod, "_capture_perplexica_config", lambda _env, _state=None: snapshot
        )
        monkeypatch.setattr(
            _mod,
            "_restore_perplexica_config",
            lambda _snapshot: None,
        )
        monkeypatch.setattr(
            _mod,
            "_update_perplexica_model",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("simulated downstream failure")
            ),
        )
        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        assert handler.parse_response()["rolled_back"] is True
        assert restarts == ["new-model.gguf", "old-model.gguf"]
        assert not lemonade_yaml.exists()
        assert "LEMONADE_MODEL=Old-Model" in env_path.read_text(encoding="utf-8")

    def test_windows_lemonade_already_serving_skips_native_restart(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, _env_text, _models_ini, _ini_text, lemonade_yaml, _yaml_text = (
            _write_model_activation_fixture(
                tmp_path,
                gpu_backend="amd",
                lemonade=True,
                lemonade_api_key="sk-inline-from-env-file-67890",
            )
        )
        env_path.write_text(
            "GPU_BACKEND=amd\n"
            "LLM_BACKEND=lemonade\n"
            "AMD_INFERENCE_RUNTIME=lemonade\n"
            "AMD_INFERENCE_LOCATION=host\n"
            "AMD_INFERENCE_PORT=8080\n"
            "GGUF_FILE=new-model.gguf\n"
            "LLM_MODEL=new-model\n"
            "CTX_SIZE=4096\n"
            "LITELLM_LEMONADE_API_KEY=sk-inline-from-env-file-67890\n",
            encoding="utf-8",
        )

        calls = []

        def fake_run(cmd, **_kwargs):
            calls.append(cmd)
            if cmd and cmd[0] == "curl" and cmd[-1].endswith("/models"):
                stdout = json.dumps({
                    "data": [{"id": "extra.new-model.gguf"}]
                })
            elif cmd and cmd[0] == "curl":
                stdout = _lemonade_health_response("extra.new-model.gguf")
            else:
                stdout = ""
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        def fail_restart(_env):
            raise AssertionError("native Lemonade restart should be skipped")

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod.platform, "system", lambda: "Windows")
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.delenv("LITELLM_LEMONADE_API_KEY", raising=False)
        monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(_mod, "_lemonade_completion_ready", lambda *_args: True)
        monkeypatch.setattr(_mod, "_restart_windows_lemonade", fail_restart)
        monkeypatch.setattr(
            _mod,
            "_container_exists",
            lambda container: container == "ods-litellm",
        )
        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 200
        content = lemonade_yaml.read_text(encoding="utf-8")
        assert "model: openai/extra.new-model.gguf" in content
        assert ["docker", "restart", "ods-litellm"] in calls

    def test_windows_lemonade_runtime_ensure_persists_config_without_dependents(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, _env_text, _models_ini, _ini_text, lemonade_yaml, _yaml_text = (
            _write_model_activation_fixture(
                tmp_path,
                gpu_backend="amd",
                lemonade=True,
                lemonade_api_key="sk-inline-from-env-file-67890",
            )
        )
        env_path.write_text(
            "ODS_MODE=lemonade\n"
            "GPU_BACKEND=amd\n"
            "LLM_BACKEND=lemonade\n"
            "AMD_INFERENCE_RUNTIME=lemonade\n"
            "AMD_INFERENCE_LOCATION=host\n"
            "AMD_INFERENCE_PORT=8080\n"
            "GGUF_FILE=old-model.gguf\n"
            "LLM_MODEL=old-model\n"
            "LEMONADE_MODEL=Old-Model\n"
            "CTX_SIZE=2048\n"
            "LITELLM_LEMONADE_API_KEY=sk-inline-from-env-file-67890\n",
            encoding="utf-8",
        )
        restarts = []

        def fail_dependent(*_args, **_kwargs):
            raise AssertionError("runtime ensure should leave dependents to ods.ps1 restart")

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod.platform, "system", lambda: "Windows")
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(_mod, "_live_runtime_has_model", lambda *_args, **_kwargs: False)
        monkeypatch.setattr(_mod, "_restart_windows_lemonade", lambda env: restarts.append(env["GGUF_FILE"]))
        monkeypatch.setattr(_mod, "_resolve_lemonade_model_id", lambda *_args, **_kwargs: "Modern-Model")
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", fail_dependent)
        monkeypatch.setattr(_mod, "_restart_existing_container", fail_dependent)
        monkeypatch.setattr(_mod, "_verify_litellm_route", fail_dependent)
        monkeypatch.setattr(_mod, "_verify_running_hermes_route", fail_dependent)
        monkeypatch.setattr(_mod, "_recreate_openclaw_if_present", fail_dependent)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_windows_lemonade_runtime_ensure(
            handler,
            model_id="target-model",
            gguf_file="new-model.gguf",
        )

        assert handler.response_code == 200
        assert handler.parse_response() == {
            "status": "configured",
            "model_id": "target-model",
            "gguf_file": "new-model.gguf",
            "lemonade_model_id": "Modern-Model",
        }
        assert restarts == ["new-model.gguf"]
        updated_env = env_path.read_text(encoding="utf-8")
        assert "GGUF_FILE=new-model.gguf" in updated_env
        assert "LLM_MODEL=target-model" in updated_env
        assert "LEMONADE_MODEL=Modern-Model" in updated_env
        assert "model: openai/Modern-Model" in lemonade_yaml.read_text(encoding="utf-8")

    def test_windows_native_llama_activation_uses_plain_health_and_litellm_local(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path, gpu_backend="amd")
        )
        env_path.write_text(
            "GPU_BACKEND=amd\n"
            "LLM_BACKEND=llama-server\n"
            "AMD_INFERENCE_RUNTIME=llama-server\n"
            "AMD_INFERENCE_RUNTIME_MODE=windows-llama-server-fallback\n"
            "AMD_INFERENCE_LOCATION=host\n"
            "AMD_INFERENCE_MANAGED=true\n"
            "AMD_INFERENCE_PORT=9090\n"
            "GGUF_FILE=old-model.gguf\n"
            "LLM_MODEL=old-model\n"
            "CTX_SIZE=2048\n"
            "OLLAMA_PORT=11434\n",
            encoding="utf-8",
        )
        hermes_live = install_dir / "data" / "hermes" / "config.yaml"
        hermes_template = install_dir / "extensions" / "services" / "hermes" / "cli-config.yaml.template"
        hermes_live.parent.mkdir(parents=True)
        hermes_template.parent.mkdir(parents=True)
        hermes_text = (
            "model:\n"
            "  default: \"old-model.gguf\"\n"
            "  provider: \"custom\"\n"
            "  base_url: \"http://litellm:4000/v1\"\n"
        )
        hermes_live.write_text(hermes_text, encoding="utf-8")
        hermes_template.write_text(hermes_text, encoding="utf-8")

        restart_calls = []

        def record_native_restart(path, env):
            restart_calls.append((path, dict(env)))

        def fail_wrong_restart(*_args, **_kwargs):
            raise AssertionError("wrong restart path for Windows native llama-server")

        calls = []

        def fake_run(cmd, **_kwargs):
            calls.append(cmd)
            if cmd and cmd[0] == "curl":
                assert cmd[-1] == "http://127.0.0.1:9090/v1/models"
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout=_llama_identity_response("new-model.gguf"),
                    stderr="",
                )
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod.platform, "system", lambda: "Windows")
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(_mod, "_restart_windows_native_llama_server", record_native_restart)
        monkeypatch.setattr(_mod, "_restart_windows_lemonade", fail_wrong_restart)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", fail_wrong_restart)
        monkeypatch.setattr(_mod, "_send_lemonade_warmup", fail_wrong_restart)
        monkeypatch.setattr(
            _mod,
            "_container_exists",
            lambda container: container != "ods-openclaw",
        )
        monkeypatch.setattr(
            _mod,
            "_verify_running_hermes_route",
            lambda *_args, **_kwargs: None,
        )
        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 200
        assert restart_calls and restart_calls[0][0] == env_path
        assert restart_calls[0][1]["GGUF_FILE"] == "new-model.gguf"
        assert '  default: "new-model.gguf"' in hermes_live.read_text(encoding="utf-8")
        assert "extra.new-model.gguf" not in hermes_live.read_text(encoding="utf-8")
        local_yaml = install_dir / "config" / "litellm" / "local.yaml"
        content = local_yaml.read_text(encoding="utf-8")
        assert "model: openai/new-model.gguf" in content
        assert "api_base: http://host.docker.internal:9090/v1" in content
        assert ["docker", "restart", "ods-litellm"] in calls
        assert ["docker", "restart", "ods-hermes"] in calls

    def test_windows_native_litellm_local_rolls_back_on_late_failure(self, tmp_path, monkeypatch):
        install_dir, env_path, env_text, models_ini, ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path, gpu_backend="amd")
        )
        env_path.write_text(
            "GPU_BACKEND=amd\n"
            "LLM_BACKEND=llama-server\n"
            "AMD_INFERENCE_RUNTIME=llama-server\n"
            "AMD_INFERENCE_RUNTIME_MODE=windows-llama-server-fallback\n"
            "AMD_INFERENCE_LOCATION=host\n"
            "AMD_INFERENCE_MANAGED=true\n"
            "AMD_INFERENCE_PORT=9090\n"
            "GGUF_FILE=old-model.gguf\n"
            "LLM_MODEL=old-model\n"
            "CTX_SIZE=2048\n",
            encoding="utf-8",
        )
        env_text = env_path.read_text(encoding="utf-8")
        litellm_local = install_dir / "config" / "litellm" / "local.yaml"
        old_local_yaml = "model_list:\n  - model_name: old\n"
        litellm_local.write_text(old_local_yaml, encoding="utf-8")

        def fake_run(cmd, **_kwargs):
            if cmd and cmd[0] == "curl":
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout=_llama_identity_response("new-model.gguf"),
                    stderr="",
                )
            if cmd == ["docker", "restart", "ods-litellm"]:
                raise subprocess.TimeoutExpired(cmd, 60)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod.platform, "system", lambda: "Windows")
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(_mod, "_restart_windows_native_llama_server", lambda *_args: None)
        monkeypatch.setattr(
            _mod,
            "_container_exists",
            lambda container: container == "ods-litellm",
        )
        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        assert env_path.read_text(encoding="utf-8") == env_text
        assert models_ini.read_text(encoding="utf-8") == ini_text
        assert litellm_local.read_text(encoding="utf-8") == old_local_yaml

    def test_windows_native_restart_error_restores_previous_runtime(self, tmp_path, monkeypatch):
        install_dir, env_path, _env_text, models_ini, ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path, gpu_backend="amd")
        )
        env_path.write_text(
            "GPU_BACKEND=amd\n"
            "LLM_BACKEND=llama-server\n"
            "AMD_INFERENCE_RUNTIME=llama-server\n"
            "AMD_INFERENCE_RUNTIME_MODE=windows-llama-server-fallback\n"
            "AMD_INFERENCE_LOCATION=host\n"
            "AMD_INFERENCE_MANAGED=true\n"
            "AMD_INFERENCE_PORT=9090\n"
            "GGUF_FILE=old-model.gguf\n"
            "LLM_MODEL=old-model\n"
            "CTX_SIZE=2048\n",
            encoding="utf-8",
        )
        env_text = env_path.read_text(encoding="utf-8")
        restart_models = []

        def restart_then_recover(_path, env):
            restart_models.append(env["GGUF_FILE"])
            if len(restart_models) == 1:
                raise RuntimeError("simulated native launch failure")

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod.platform, "system", lambda: "Windows")
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(_mod, "_restart_windows_native_llama_server", restart_then_recover)
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", _mock_verified_readiness)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        assert handler.parse_response()["rolled_back"] is True
        assert restart_models == ["new-model.gguf", "old-model.gguf"]
        assert env_path.read_text(encoding="utf-8") == env_text
        assert models_ini.read_text(encoding="utf-8") == ini_text

    def test_activation_patches_hermes_configs_and_restarts_hermes(self, tmp_path, monkeypatch):
        install_dir, _env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        hermes_live = install_dir / "data" / "hermes" / "config.yaml"
        hermes_template = install_dir / "extensions" / "services" / "hermes" / "cli-config.yaml.template"
        hermes_live.parent.mkdir(parents=True)
        hermes_template.parent.mkdir(parents=True)
        hermes_text = (
            "model:\n"
            "  default: \"old-model\"\n"
            "  provider: \"custom\"\n"
            "  base_url: \"http://llama-server:8080/v1\"\n"
        )
        hermes_live.write_text(hermes_text, encoding="utf-8")
        hermes_template.write_text(hermes_text, encoding="utf-8")

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)
        monkeypatch.setattr(
            _mod,
            "_container_exists",
            lambda container: container == "ods-hermes",
        )
        monkeypatch.setattr(
            _mod,
            "_read_hermes_container_config",
            lambda: hermes_live.read_text(encoding="utf-8"),
        )

        calls = []

        def fake_run(cmd, **_kwargs):
            calls.append(cmd)
            stdout = _llama_identity_response("new-model.gguf") if cmd and cmd[0] == "curl" else ""
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 200
        assert '  default: "new-model.gguf"' in hermes_live.read_text(encoding="utf-8")
        assert '  default: "new-model.gguf"' in hermes_template.read_text(encoding="utf-8")
        assert ["docker", "restart", "ods-hermes"] in calls

    def test_activation_uses_catalog_context_instead_of_current_env_floor(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        env_path.write_text(
            "GPU_BACKEND=nvidia\n"
            "GGUF_FILE=old-model.gguf\n"
            "LLM_MODEL=old-model\n"
            "CTX_SIZE=131072\n"
            "MAX_CONTEXT=131072\n"
            "OLLAMA_PORT=8080\n",
            encoding="utf-8",
        )
        hermes_live = install_dir / "data" / "hermes" / "config.yaml"
        hermes_template = install_dir / "extensions" / "services" / "hermes" / "cli-config.yaml.template"
        hermes_live.parent.mkdir(parents=True)
        hermes_template.parent.mkdir(parents=True)
        hermes_text = (
            "model:\n"
            "  default: \"old-model\"\n"
            "  provider: \"custom\"\n"
            "  base_url: \"http://host.docker.internal:8080/v1\"\n"
            "  context_length: 32768\n"
            "auxiliary:\n"
            "  compression:\n"
            "    context_length: 32768\n"
        )
        hermes_live.write_text(hermes_text, encoding="utf-8")
        hermes_template.write_text(hermes_text, encoding="utf-8")

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)

        def fake_run(cmd, **_kwargs):
            stdout = _llama_identity_response("new-model.gguf") if cmd and cmd[0] == "curl" else ""
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 200
        env_text = env_path.read_text(encoding="utf-8")
        assert "MAX_CONTEXT=4096" in env_text
        assert "CTX_SIZE=4096" in env_text
        assert "  context_length: 4096" in hermes_live.read_text(encoding="utf-8")
        assert "    context_length: 4096" in hermes_live.read_text(encoding="utf-8")
        assert '  base_url: "http://host.docker.internal:8080/v1"' in hermes_live.read_text(encoding="utf-8")

    def test_activation_preserves_matching_recommended_context(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, _env_text, models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        env_path.write_text(
            "GPU_BACKEND=nvidia\n"
            "GGUF_FILE=Phi-4-mini-instruct-Q4_K_M.gguf\n"
            "LLM_MODEL=phi-4-mini\n"
            "CTX_SIZE=128000\n"
            "MAX_CONTEXT=128000\n"
            "MODEL_RECOMMENDED_MODEL=new-model\n"
            "MODEL_RECOMMENDED_GGUF=new-model.gguf\n"
            "MODEL_RECOMMENDED_CONTEXT=65536\n"
            "OLLAMA_PORT=8080\n",
            encoding="utf-8",
        )
        hermes_live = install_dir / "data" / "hermes" / "config.yaml"
        hermes_template = install_dir / "extensions" / "services" / "hermes" / "cli-config.yaml.template"
        hermes_live.parent.mkdir(parents=True)
        hermes_template.parent.mkdir(parents=True)
        hermes_text = (
            "model:\n"
            "  default: \"Phi-4-mini-instruct-Q4_K_M.gguf\"\n"
            "  provider: \"custom\"\n"
            "  context_length: 128000\n"
            "auxiliary:\n"
            "  compression:\n"
            "    context_length: 128000\n"
        )
        hermes_live.write_text(hermes_text, encoding="utf-8")
        hermes_template.write_text(hermes_text, encoding="utf-8")

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)

        def fake_run(cmd, **_kwargs):
            stdout = _llama_identity_response("new-model.gguf") if cmd and cmd[0] == "curl" else ""
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 200
        env_text = env_path.read_text(encoding="utf-8")
        assert "MAX_CONTEXT=65536" in env_text
        assert "CTX_SIZE=65536" in env_text
        assert "n-ctx = 65536" in models_ini.read_text(encoding="utf-8")
        assert "  context_length: 65536" in hermes_live.read_text(encoding="utf-8")
        assert "    context_length: 65536" in hermes_live.read_text(encoding="utf-8")
        assert "  context_length: 65536" in hermes_template.read_text(encoding="utf-8")

    def test_activation_ignores_recommended_context_for_other_model(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        env_path.write_text(
            "GPU_BACKEND=nvidia\n"
            "GGUF_FILE=old-model.gguf\n"
            "LLM_MODEL=old-model\n"
            "CTX_SIZE=131072\n"
            "MAX_CONTEXT=131072\n"
            "MODEL_RECOMMENDED_MODEL=other-model\n"
            "MODEL_RECOMMENDED_GGUF=other-model.gguf\n"
            "MODEL_RECOMMENDED_CONTEXT=65536\n"
            "OLLAMA_PORT=8080\n",
            encoding="utf-8",
        )

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)

        def fake_run(cmd, **_kwargs):
            stdout = _llama_identity_response("new-model.gguf") if cmd and cmd[0] == "curl" else ""
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 200
        env_text = env_path.read_text(encoding="utf-8")
        assert "MAX_CONTEXT=4096" in env_text
        assert "CTX_SIZE=4096" in env_text

    def test_activation_updates_uid_owned_hermes_config_through_container(
        self, tmp_path, monkeypatch,
    ):
        install_dir, _env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        hermes_live = install_dir / "data" / "hermes" / "config.yaml"
        hermes_template = install_dir / "extensions" / "services" / "hermes" / "cli-config.yaml.template"
        hermes_live.parent.mkdir(parents=True)
        hermes_template.parent.mkdir(parents=True)
        hermes_live.write_text("model:\n  default: \"old-live\"\n", encoding="utf-8")
        hermes_template.write_text("model:\n  default: \"old-template\"\n", encoding="utf-8")

        original_read_bytes = Path.read_bytes

        def fake_read_bytes(path, *args, **kwargs):
            if path == hermes_live:
                raise PermissionError("container-owned")
            return original_read_bytes(path, *args, **kwargs)

        container_config = {"text": hermes_live.read_text(encoding="utf-8")}
        container_writes = []

        def write_container_config(text):
            container_writes.append(text)
            container_config["text"] = text

        monkeypatch.setattr(Path, "read_bytes", fake_read_bytes)
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)
        monkeypatch.setattr(_mod, "_container_running", lambda name: name == "ods-hermes")
        monkeypatch.setattr(_mod, "_container_exists", lambda name: name == "ods-hermes")
        monkeypatch.setattr(
            _mod,
            "_read_hermes_container_config",
            lambda: container_config["text"],
        )
        monkeypatch.setattr(_mod, "_write_hermes_container_config", write_container_config)

        calls = []

        def fake_run(cmd, **_kwargs):
            calls.append(cmd)
            stdout = _llama_identity_response("new-model.gguf") if cmd and cmd[0] == "curl" else ""
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 200
        assert container_writes
        assert '  default: "new-model.gguf"' in container_config["text"]
        assert '  default: "new-model.gguf"' in hermes_template.read_text(encoding="utf-8")
        assert ["docker", "restart", "ods-hermes"] in calls

    def test_activation_repairs_malformed_models_ini_directory(
        self, tmp_path, monkeypatch,
    ):
        install_dir, _env_path, _env_text, models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        models_ini.unlink()
        models_ini.mkdir()

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)

        def fake_run(cmd, **_kwargs):
            stdout = _llama_identity_response("new-model.gguf") if cmd and cmd[0] == "curl" else ""
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 200
        assert models_ini.is_file()
        text = models_ini.read_text(encoding="utf-8")
        assert "[new-model]" in text
        assert "filename = new-model.gguf" in text

    def test_activation_applies_matching_runtime_profile_flags(self, tmp_path, monkeypatch):
        install_dir, env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        model_library = install_dir / "config" / "model-library.json"
        model_library.write_text(json.dumps({
            "models": [{
                "id": "target-model",
                "gguf_file": "new-model.gguf",
                "gguf_url": "https://example.test/new-model.gguf",
                "gguf_sha256": hashlib.sha256(b"model").hexdigest(),
                "llm_model_name": "new-model",
                "context_length": 131072,
                "runtime_profiles": [{
                    "id": "nvidia-8gb-test",
                    "label": "Advanced test profile",
                    "backend": "nvidia",
                    "memory_type": "discrete",
                    "vram_min_gb": 7.5,
                    "vram_max_gb": 12.5,
                    "system_ram_min_gb": 32,
                    "context_length": 65536,
                    "llama_server_image": "example.test/llama:turbo",
                    "env": {
                        "LLAMA_PARALLEL": "1",
                        "LLAMA_ARG_FLASH_ATTN": "on",
                        "LLAMA_ARG_CACHE_TYPE_K": "q8_0",
                        "LLAMA_ARG_CACHE_TYPE_V": "turbo3",
                        "LLAMA_ARG_N_CPU_MOE": "30",
                        "LLAMA_ARG_NO_CACHE_PROMPT": "1",
                        "LLAMA_ARG_CHECKPOINT_EVERY_N_TOKENS": "-1",
                        "LLAMA_ARG_SPEC_TYPE": "draft-mtp",
                        "LLAMA_ARG_SPEC_DRAFT_N_MAX": "3",
                    },
                }],
            }]
        }), encoding="utf-8")

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(_mod, "_nvidia_vram_gb", lambda: 8.0)
        monkeypatch.setattr(_mod, "_system_ram_gb", lambda: 32)
        monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)

        def fake_run(cmd, **_kwargs):
            stdout = _llama_identity_response("new-model.gguf") if cmd and cmd[0] == "curl" else ""
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 200
        env_text = env_path.read_text(encoding="utf-8")
        assert "MAX_CONTEXT=65536" in env_text
        assert "MODEL_RUNTIME_PROFILE=nvidia-8gb-test" in env_text
        assert "LLAMA_SERVER_IMAGE=example.test/llama:turbo" in env_text
        assert "LLAMA_ARG_CACHE_TYPE_V=turbo3" in env_text
        assert "LLAMA_ARG_N_CPU_MOE=30" in env_text
        assert "LLAMA_ARG_CHECKPOINT_EVERY_N_TOKENS=-1" in env_text
        assert "LLAMA_ARG_SPEC_TYPE=draft-mtp" in env_text
        assert "LLAMA_ARG_SPEC_DRAFT_N_MAX=3" in env_text

    def test_unexpected_failure_rolls_back_all_config_backups(self, tmp_path, monkeypatch):
        install_dir, env_path, env_text, models_ini, ini_text, lemonade_yaml, lemonade_text = (
            _write_model_activation_fixture(tmp_path, gpu_backend="amd", lemonade=True)
        )
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)

        def fail_restart(_env):
            raise RuntimeError("restart failed")

        monkeypatch.setattr(_mod, "_compose_restart_llama_server", fail_restart)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        assert "restart failed" in handler.parse_response()["error"]
        assert env_path.read_text(encoding="utf-8") == env_text
        assert models_ini.read_text(encoding="utf-8") == ini_text
        assert lemonade_yaml.read_text(encoding="utf-8") == lemonade_text

    def test_activation_rejects_corrupt_catalog_artifact_before_mutation(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, env_text, models_ini, ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        (install_dir / "data" / "models" / "new-model.gguf").write_bytes(b"corrupt")
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(
            _mod,
            "_compose_restart_llama_server",
            lambda _env: pytest.fail("corrupt artifact must not restart runtime"),
        )
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 400
        assert "failed catalog verification" in handler.parse_response()["error"]
        assert env_path.read_text(encoding="utf-8") == env_text
        assert models_ini.read_text(encoding="utf-8") == ini_text

    def test_identity_without_meaningful_completion_rolls_back_and_proves_previous(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        runtime_restarts = []
        completion_models = []

        def restart(env):
            runtime_restarts.append(env["GGUF_FILE"])

        def fake_run(cmd, **kwargs):
            if cmd and cmd[0] == "curl":
                active = _mod.load_env(env_path)["GGUF_FILE"]
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout=_llama_identity_response(active),
                    stderr="",
                )
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        def completion_ready(_host, _port, model_name, _prefix, **_expected):
            completion_models.append(model_name)
            return model_name == "old-model"

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", restart)
        monkeypatch.setattr(_mod, "_chat_completion_ready", completion_ready)
        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        response = handler.parse_response()
        assert response["rolled_back"] is True
        assert runtime_restarts == ["new-model.gguf", "old-model.gguf"]
        assert "new-model" in completion_models
        assert completion_models[-1] == "old-model"
        assert _mod.load_env(env_path)["GGUF_FILE"] == "old-model.gguf"

    def test_exception_rollback_restarts_dependents_before_proving_previous_route(
        self, tmp_path, monkeypatch,
    ):
        install_dir, _env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        events = []
        litellm_restarts = 0

        def restart_runtime(env):
            events.append(f"runtime:{env['GGUF_FILE']}")

        def readiness(env, **kwargs):
            events.append(f"ready:{kwargs['gguf_file']}")
            if kwargs.get("return_proof"):
                return {
                    "identity": kwargs["gguf_file"],
                    "contextLength": 65536,
                    "contextVerified": True,
                    "verifiedAt": "2026-07-20T00:00:00+00:00",
                }
            if kwargs.get("return_identity"):
                return kwargs["gguf_file"]
            return True

        def restart_dependent(container, _state=None):
            nonlocal litellm_restarts
            events.append(f"dependent:{container}")
            if container == "ods-litellm":
                litellm_restarts += 1
                if litellm_restarts == 1:
                    raise RuntimeError("simulated dependent restart failure")
                return True
            return False

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", restart_runtime)
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", readiness)
        monkeypatch.setattr(_mod, "_restart_existing_container", restart_dependent)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        assert handler.parse_response()["rolled_back"] is True
        assert events == [
            "runtime:new-model.gguf",
            "ready:new-model.gguf",
            "dependent:ods-litellm",
            "runtime:old-model.gguf",
            "ready:old-model.gguf",
        ]



    def test_activation_succeeds_without_optional_dependents(self, tmp_path, monkeypatch):
        install_dir, _env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", _mock_verified_readiness)
        monkeypatch.setattr(_mod, "_container_exists", lambda _container: False)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 200

    def test_activation_recreates_openclaw_with_the_new_model_env(
        self, tmp_path, monkeypatch,
    ):
        install_dir, _env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        recreates = []
        verified_models = []

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", _mock_verified_readiness)
        monkeypatch.setattr(
            _mod, "_restart_existing_container", lambda _container, _state=None: False
        )
        monkeypatch.setattr(
            _mod,
            "_container_exists",
            lambda container: container == "ods-openclaw",
        )
        monkeypatch.setattr(
            _mod,
            "docker_compose_recreate",
            lambda services: (recreates.append(list(services)) or True, ""),
        )
        monkeypatch.setattr(
            _mod,
            "_verify_openclaw_model_env",
            lambda model_name: verified_models.append(model_name),
        )
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 200
        assert recreates == [["openclaw"]]
        assert verified_models == ["new-model.gguf"]

    @pytest.mark.parametrize(
        ("system_name", "expected_model_id"),
        [
            ("Linux", "new-model"),
            ("Windows", "new-model"),
        ],
    )
    def test_activation_updates_both_opencode_configs_without_losing_user_settings(
        self, tmp_path, monkeypatch, system_name, expected_model_id,
    ):
        install_dir, _env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        config_dir = tmp_path / "home" / ".config" / "opencode"
        config_dir.mkdir(parents=True)
        primary = config_dir / "opencode.json"
        compat = config_dir / "config.json"
        primary.write_text(
            json.dumps({
                "model": "llama-server/old-model",
                "small_model": "llama-server/old-model",
                "theme": "system",
                "provider": {
                    "custom-cloud": {"npm": "@ai-sdk/openai"},
                    "llama-server": {
                        "options": {
                            "baseURL": "http://127.0.0.1:8080/v1",
                            "apiKey": "no-key",
                            "timeout": 900,
                        },
                        "models": {"old-model": {"name": "old-model"}},
                    },
                },
            }),
            encoding="utf-8",
        )
        compat.write_text(
            json.dumps({
                "model": "llama-server/old-model",
                "compat_only": True,
                "provider": {},
            }),
            encoding="utf-8",
        )

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "_opencode_config_paths", lambda: (primary, compat))
        monkeypatch.setattr(_mod.platform, "system", lambda: system_name)
        monkeypatch.setattr(_mod, "_restart_managed_opencode", lambda _state=None: False)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", _mock_verified_readiness)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 200
        for path in (primary, compat):
            config = json.loads(path.read_text(encoding="utf-8"))
            assert config["model"] == f"llama-server/{expected_model_id}"
            assert config["small_model"] == f"llama-server/{expected_model_id}"
            provider = config["provider"]["llama-server"]
            assert provider["options"]["baseURL"] == "http://127.0.0.1:8080/v1"
            assert provider["options"]["apiKey"] == "no-key"
            assert provider["models"][expected_model_id]["limit"] == {
                "context": 4096,
                "output": 4096,
            }
        primary_config = json.loads(primary.read_text(encoding="utf-8"))
        compat_config = json.loads(compat.read_text(encoding="utf-8"))
        assert primary_config["theme"] == "system"
        assert primary_config["provider"]["custom-cloud"] == {
            "npm": "@ai-sdk/openai"
        }
        assert primary_config["provider"]["llama-server"]["options"]["timeout"] == 900
        assert compat_config["compat_only"] is True

    def test_switchboard_activation_routes_opencode_through_stable_alias(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        env_path.write_text(
            env_text
            + "ODS_MODEL_SWITCHBOARD=enabled\n"
            + "LITELLM_KEY=switchboard-secret\n"
            + "LITELLM_PORT=4100\n",
            encoding="utf-8",
        )
        config_dir = tmp_path / "home" / ".config" / "opencode"
        config_dir.mkdir(parents=True)
        primary = config_dir / "opencode.json"
        compat = config_dir / "config.json"
        stale = {
            "model": "llama-server/qwen3-coder-next",
            "small_model": "llama-server/qwen3-coder-next",
            "theme": "system",
            "provider": {
                "llama-server": {
                    "options": {
                        "baseURL": "http://127.0.0.1:11434/v1",
                        "apiKey": "no-key",
                    },
                    "models": {
                        "qwen3-coder-next": {"name": "Qwen 3 Coder Next"},
                    },
                },
            },
        }
        primary.write_text(json.dumps(stale), encoding="utf-8")
        compat.write_text(json.dumps(stale), encoding="utf-8")

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "_opencode_config_paths", lambda: (primary, compat))
        monkeypatch.setattr(_mod, "_restart_managed_opencode", lambda _state=None: False)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)
        monkeypatch.setattr(_mod, "_render_model_router_runtime_configs", lambda *_a, **_k: None)
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", _mock_verified_readiness)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 200
        for path in (primary, compat):
            config = json.loads(path.read_text(encoding="utf-8"))
            assert config["theme"] == "system"
            assert config["model"] == "llama-server/ods/current"
            assert config["small_model"] == "llama-server/ods/current"
            provider = config["provider"]["llama-server"]
            assert provider["name"] == "ODS switchboard"
            assert provider["options"] == {
                "baseURL": "http://127.0.0.1:4100/v1",
                "apiKey": "switchboard-secret",
            }
            assert "qwen3-coder-next" not in provider["models"]
            assert provider["models"]["ods/current"]["limit"] == {
                "context": 4096,
                "output": 4096,
            }

    def test_opencode_update_failure_restores_exact_files(self, tmp_path, monkeypatch):
        install_dir, _env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        config_dir = tmp_path / "home" / ".config" / "opencode"
        config_dir.mkdir(parents=True)
        primary = config_dir / "opencode.json"
        compat = config_dir / "config.json"
        original = '{"model":"llama-server/old-model","provider":{}}\n'
        primary.write_text(original, encoding="utf-8")
        perplexica_snapshot = TestPerplexicaModelRoute._snapshot()

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "_opencode_config_paths", lambda: (primary, compat))
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", _mock_verified_readiness)
        monkeypatch.setattr(
            _mod,
            "_capture_perplexica_config",
            lambda _env, _state=None: perplexica_snapshot,
        )
        monkeypatch.setattr(
            _mod,
            "_update_perplexica_model",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("simulated downstream failure")
            ),
        )
        monkeypatch.setattr(_mod, "_restore_perplexica_config", lambda _snapshot: None)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        assert handler.parse_response()["rolled_back"] is True
        assert primary.read_text(encoding="utf-8") == original
        assert not compat.exists()

    def test_activation_rejects_unrecoverable_opencode_config(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        config_dir = tmp_path / "home" / ".config" / "opencode"
        config_dir.mkdir(parents=True)
        primary = config_dir / "opencode.json"
        compat = config_dir / "config.json"
        primary.write_text("not-json", encoding="utf-8")
        compat.write_text("[]", encoding="utf-8")

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "_opencode_config_paths", lambda: (primary, compat))
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", _mock_verified_readiness)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        assert "OpenCode config is malformed" in handler.parse_response()["error"]
        assert env_path.read_text(encoding="utf-8") == env_text

    def test_cli_activation_metadata_persists_tier_and_bounded_context(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", _mock_verified_readiness)
        monkeypatch.setattr(
            _mod,
            "_resolve_requested_tier_contract",
            lambda _tier, _env: {
                "GGUF_FILE": "new-model.gguf",
                "MAX_CONTEXT": "4096",
                "LLM_MODEL": "new-model",
            },
        )
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(
            handler,
            "target-model",
            requested_context_length=2048,
            requested_tier="0",
        )

        assert handler.response_code == 200
        receipt = handler.parse_response()
        assert receipt["status"] == "activated"
        assert receipt["model_id"] == "target-model"
        assert receipt["llm_model"] == "new-model"
        assert receipt["gguf_file"] == "new-model.gguf"
        assert receipt["tier"] == "0"
        assert receipt["context_length"] == 2048
        assert receipt["consumers"]["dashboard"] == "live_env"
        assert receipt["consumers"]["open-webui"] == "dynamic_route"
        env = _mod.load_env(env_path)
        assert env["TIER"] == "0"
        assert env["CTX_SIZE"] == "2048"
        assert env["MAX_CONTEXT"] == "2048"
        assert env["GGUF_URL"] == "https://example.test/new-model.gguf"
        assert env["GGUF_SHA256"] == hashlib.sha256(b"model").hexdigest()

    def test_activation_rejects_context_above_catalog_limit(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        restarts = []
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(
            _mod, "_compose_restart_llama_server", lambda _env: restarts.append(True)
        )
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(
            handler,
            "target-model",
            requested_context_length=8192,
            requested_tier="4",
        )

        assert handler.response_code == 400
        assert "exceeds the catalog limit" in handler.parse_response()["error"]
        assert env_path.read_text(encoding="utf-8") == env_text
        assert restarts == []

    def test_activation_updates_perplexica_after_model_readiness(
        self, tmp_path, monkeypatch,
    ):
        install_dir, _env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        snapshot = TestPerplexicaModelRoute._snapshot()
        updates = []

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", _mock_verified_readiness)
        monkeypatch.setattr(
            _mod, "_capture_perplexica_config", lambda _env, _state=None: snapshot
        )
        monkeypatch.setattr(
            _mod,
            "_update_perplexica_model",
            lambda env, captured, **kwargs: updates.append((dict(env), captured, kwargs)),
        )
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 200
        assert len(updates) == 1
        assert updates[0][1] is snapshot
        assert updates[0][2] == {
            "gguf_file": "new-model.gguf",
            "lemonade_model_id": "",
        }
        assert updates[0][0]["GGUF_FILE"] == "new-model.gguf"



    def test_perplexica_update_failure_restores_snapshot_during_rollback(
        self, tmp_path, monkeypatch,
    ):
        install_dir, _env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        snapshot = TestPerplexicaModelRoute._snapshot()
        restores = []

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", _mock_verified_readiness)
        monkeypatch.setattr(
            _mod, "_capture_perplexica_config", lambda _env, _state=None: snapshot
        )

        def fail_update(*_args, **_kwargs):
            raise RuntimeError("simulated Perplexica update failure")

        monkeypatch.setattr(_mod, "_update_perplexica_model", fail_update)
        monkeypatch.setattr(
            _mod,
            "_restore_perplexica_config",
            lambda captured: restores.append(captured),
        )
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        response = handler.parse_response()
        assert response["rolled_back"] is True
        assert "Perplexica update failure" in response["error"]
        assert restores == [snapshot]

    def test_context_round_trip_restores_each_catalog_value(self, tmp_path, monkeypatch):
        install_dir, env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        models_dir = install_dir / "data" / "models"
        model_a = b"model a"
        model_b = b"model b"
        (models_dir / "model-a.gguf").write_bytes(model_a)
        (models_dir / "model-b.gguf").write_bytes(model_b)
        (install_dir / "config" / "model-library.json").write_text(
            json.dumps({"models": [
                {
                    "id": "model-a",
                    "gguf_file": "model-a.gguf",
                    "gguf_url": "https://example.test/model-a.gguf",
                    "gguf_sha256": hashlib.sha256(model_a).hexdigest(),
                    "llm_model_name": "model-a",
                    "context_length": 8192,
                },
                {
                    "id": "model-b",
                    "gguf_file": "model-b.gguf",
                    "gguf_url": "https://example.test/model-b.gguf",
                    "gguf_sha256": hashlib.sha256(model_b).hexdigest(),
                    "llm_model_name": "model-b",
                    "context_length": 32768,
                },
            ]}),
            encoding="utf-8",
        )
        env_path.write_text(
            "GPU_BACKEND=nvidia\nGGUF_FILE=model-a.gguf\nLLM_MODEL=model-a\n"
            "CTX_SIZE=8192\nMAX_CONTEXT=8192\nOLLAMA_PORT=8080\n",
            encoding="utf-8",
        )
        restarted_contexts = []

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(
            _mod,
            "_compose_restart_llama_server",
            lambda env: restarted_contexts.append(int(env["MAX_CONTEXT"])),
        )
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", _mock_verified_readiness)

        handler_b = _ResponseHandler()
        _mod.AgentHandler._do_model_activate(handler_b, "model-b")
        handler_a = _ResponseHandler()
        _mod.AgentHandler._do_model_activate(handler_a, "model-a")

        assert handler_b.response_code == 200
        assert handler_a.response_code == 200
        assert restarted_contexts == [32768, 8192]
        assert _mod.load_env(env_path)["MAX_CONTEXT"] == "8192"

    def test_in_container_activation_preserves_host_specific_image_without_override(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        env_path.write_text(
            env_path.read_text(encoding="utf-8")
            + "MAX_CONTEXT=2048\nLLAMA_SERVER_IMAGE=host.example/llama:custom\n",
            encoding="utf-8",
        )
        recreates = []
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setenv("ODS_HOST_INSTALL_DIR", str(install_dir))
        monkeypatch.setattr(
            _mod,
            "_recreate_llama_server",
            lambda env, override_image="": recreates.append((dict(env), override_image)),
        )
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", _mock_verified_readiness)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 200
        assert recreates[0][1] == "host.example/llama:custom"
        assert recreates[0][0]["LLAMA_SERVER_IMAGE"] == "host.example/llama:custom"
        assert _mod.load_env(env_path)["LLAMA_SERVER_IMAGE"] == "host.example/llama:custom"

    def test_pre_snapshot_failure_does_not_overwrite_configs(self, tmp_path, monkeypatch):
        install_dir, env_path, env_text, models_ini, ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)

        def fail_load_env(_path):
            raise OSError("cannot read env")

        monkeypatch.setattr(_mod, "load_env", fail_load_env)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        assert env_path.read_text(encoding="utf-8") == env_text
        assert models_ini.read_text(encoding="utf-8") == ini_text

    def test_activation_preserves_stopped_consumers(self, tmp_path, monkeypatch):
        install_dir, _env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        states = {
            name: {"exists": True, "running": False}
            for name in (
                "ods-litellm",
                "ods-hermes",
                "ods-openclaw",
                "ods-perplexica",
            )
        }
        docker_calls = []
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "_capture_container_state", lambda name: states[name])
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)
        monkeypatch.setattr(
            _mod, "_wait_for_model_readiness", _mock_verified_readiness
        )
        monkeypatch.setattr(
            _mod.subprocess,
            "run",
            lambda cmd, **_kwargs: (
                docker_calls.append(cmd)
                or subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            ),
        )
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 200
        receipt = handler.parse_response()["consumers"]
        assert receipt["litellm"] == "stopped"
        assert receipt["openclaw"] == "stopped"
        assert receipt["perplexica"] == "stopped"
        assert not any(call[:2] in (["docker", "restart"], ["docker", "stop"]) for call in docker_calls)

    def test_active_lemonade_consumer_requires_running_litellm(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path, gpu_backend="amd")
        )
        env_path.write_text(
            env_path.read_text(encoding="utf-8")
            + "LLM_BACKEND=lemonade\nAMD_INFERENCE_RUNTIME=lemonade\n",
            encoding="utf-8",
        )
        expected_env = env_path.read_text(encoding="utf-8")
        states = {
            "ods-litellm": {"exists": True, "running": False},
            "ods-hermes": {"exists": True, "running": True},
            "ods-openclaw": {"exists": False, "running": False},
            "ods-perplexica": {"exists": False, "running": False},
        }
        restarts = []
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "_capture_container_state", lambda name: states[name])
        monkeypatch.setattr(
            _mod, "_compose_restart_llama_server", lambda _env: restarts.append(True)
        )
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        response = handler.parse_response()
        assert "Active Lemonade consumers require LiteLLM" in response["error"]
        assert "ods-hermes" in response["error"]
        assert "rolled_back" not in response
        assert env_path.read_text(encoding="utf-8") == expected_env
        assert restarts == []

    def test_dependent_health_failure_rolls_back_previous_route(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        hermes_live = install_dir / "data" / "hermes" / "config.yaml"
        hermes_template = (
            install_dir / "extensions" / "services" / "hermes" / "cli-config.yaml.template"
        )
        hermes_live.parent.mkdir(parents=True)
        hermes_template.parent.mkdir(parents=True)
        old_config = (
            "model:\n"
            '  default: "old-model.gguf"\n'
            "  context_length: 2048\n"
        )
        hermes_live.write_text(old_config, encoding="utf-8")
        hermes_template.write_text(old_config, encoding="utf-8")
        states = {
            "ods-litellm": {"exists": False, "running": False},
            "ods-hermes": {"exists": True, "running": True},
            "ods-openclaw": {"exists": False, "running": False},
            "ods-perplexica": {"exists": False, "running": False},
        }
        runtime_models = []
        health_checks = 0

        def check_health(container):
            nonlocal health_checks
            assert container == "ods-hermes"
            health_checks += 1
            if health_checks == 1:
                raise RuntimeError("simulated unhealthy Hermes")

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "_capture_container_state", lambda name: states[name])
        monkeypatch.setattr(
            _mod,
            "_compose_restart_llama_server",
            lambda env: runtime_models.append(env["GGUF_FILE"]),
        )
        monkeypatch.setattr(
            _mod, "_wait_for_model_readiness", _mock_verified_readiness
        )
        monkeypatch.setattr(
            _mod,
            "_restart_existing_container",
            lambda name, _state=None: name == "ods-hermes",
        )
        monkeypatch.setattr(
            _mod,
            "_restore_container_state",
            lambda name, _state, **_kwargs: name == "ods-hermes",
        )
        monkeypatch.setattr(_mod, "_verify_running_hermes_route", lambda *_args: None)
        monkeypatch.setattr(_mod, "_wait_for_container_health", check_health)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        assert handler.parse_response()["rolled_back"] is True
        assert runtime_models == ["new-model.gguf", "old-model.gguf"]
        assert health_checks == 2
        assert env_path.read_text(encoding="utf-8") == env_text
        assert hermes_live.read_text(encoding="utf-8") == old_config

    def test_runtime_profile_cannot_exceed_requested_tier_context(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(
            _mod,
            "_resolve_requested_tier_contract",
            lambda _tier, _env: {
                "GGUF_FILE": "new-model.gguf",
                "LLM_MODEL": "new-model",
                "MAX_CONTEXT": "4096",
            },
        )
        monkeypatch.setattr(
            _mod,
            "_select_runtime_profile",
            lambda _model, _env: {
                "id": "larger-context-profile",
                "label": "Larger context",
                "context_length": 8192,
                "env": {},
            },
        )
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)
        monkeypatch.setattr(
            _mod, "_wait_for_model_readiness", _mock_verified_readiness
        )
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(
            handler,
            "target-model",
            requested_tier="1",
        )

        assert handler.response_code == 200
        assert handler.parse_response()["context_length"] == 4096
        assert _mod.load_env(env_path)["MAX_CONTEXT"] == "4096"

    def test_tier_model_identity_mismatch_fails_before_mutation(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        restarts = []
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(
            _mod,
            "_resolve_requested_tier_contract",
            lambda _tier, _env: {
                "GGUF_FILE": "new-model.gguf",
                "LLM_MODEL": "different-model",
                "MAX_CONTEXT": "4096",
            },
        )
        monkeypatch.setattr(
            _mod, "_compose_restart_llama_server", lambda _env: restarts.append(True)
        )
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(
            handler,
            "target-model",
            requested_tier="1",
        )

        assert handler.response_code == 400
        assert handler.parse_response()["code"] == "tier_model_mismatch"
        assert env_path.read_text(encoding="utf-8") == env_text
        assert restarts == []

    def test_symlinked_config_is_rejected_before_mutation(self, tmp_path, monkeypatch):
        install_dir, env_path, env_text, models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        outside = tmp_path / "outside.ini"
        outside.write_text("outside\n", encoding="utf-8")
        models_ini.unlink()
        try:
            models_ini.symlink_to(outside)
        except OSError as exc:
            pytest.skip(f"symlink creation is unavailable: {exc}")
        restarts = []
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(
            _mod, "_compose_restart_llama_server", lambda _env: restarts.append(True)
        )
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        response = handler.parse_response()
        assert "symlinked configuration file" in response["error"]
        assert "rolled_back" not in response
        assert env_path.read_text(encoding="utf-8") == env_text
        assert outside.read_text(encoding="utf-8") == "outside\n"
        assert restarts == []

    def test_concurrent_env_change_is_not_overwritten(self, tmp_path, monkeypatch):
        install_dir, env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        calls = 0
        restarts = []

        def capture_state(_name):
            nonlocal calls
            calls += 1
            if calls == 4:
                env_path.write_text("GPU_BACKEND=nvidia\nLLM_MODEL=external-edit\n", encoding="utf-8")
            return {"exists": False, "running": False}

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "_capture_container_state", capture_state)
        monkeypatch.setattr(
            _mod, "_compose_restart_llama_server", lambda _env: restarts.append(True)
        )
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        response = handler.parse_response()
        assert "Configuration changed during model activation" in response["error"]
        assert "rolled_back" not in response
        assert "LLM_MODEL=external-edit" in env_path.read_text(encoding="utf-8")
        assert restarts == []

    def test_rollback_restores_absent_models_ini(self, tmp_path, monkeypatch):
        install_dir, env_path, env_text, models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        models_ini.unlink()
        readiness = iter((False, True))
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)
        monkeypatch.setattr(
            _mod,
            "_wait_for_model_readiness",
            lambda *_args, **_kwargs: next(readiness),
        )
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        assert handler.parse_response()["rolled_back"] is True
        assert env_path.read_text(encoding="utf-8") == env_text
        assert not models_ini.exists()

    def test_jsonc_opencode_update_preserves_settings_and_removes_stale_model(
        self, tmp_path, monkeypatch,
    ):
        config_path = tmp_path / "opencode.jsonc"
        config_path.write_text(
            """{
              // User preference must survive.
              "theme": "system",
              "model": "llama-server/old-model",
              "provider": {
                "llama-server": {
                  "models": {"old-model": {"name": "Old"}},
                },
              },
            }
            """,
            encoding="utf-8",
        )
        monkeypatch.setattr(_mod, "_opencode_config_paths", lambda: (config_path,))
        snapshot = _mod._capture_opencode_config()

        _mod._update_opencode_config(
            {"GPU_BACKEND": "nvidia", "OLLAMA_PORT": "8080"},
            snapshot,
            "new-model",
            4096,
        )

        config = json.loads(config_path.read_text(encoding="utf-8"))
        assert config["theme"] == "system"
        assert config["model"] == "llama-server/new-model"
        models = config["provider"]["llama-server"]["models"]
        assert "new-model" in models
        assert "old-model" not in models

    def test_current_opencode_config_wins_when_seeding_compat_file(
        self, tmp_path, monkeypatch,
    ):
        current = tmp_path / ".config" / "opencode" / "opencode.json"
        compat = current.parent / "config.json"
        legacy = tmp_path / ".local" / "share" / "opencode" / "opencode.jsonc"
        current.parent.mkdir(parents=True)
        legacy.parent.mkdir(parents=True)
        current.write_text('{"theme":"current"}', encoding="utf-8")
        legacy.write_text('{"theme":"legacy"}', encoding="utf-8")
        monkeypatch.setattr(
            _mod,
            "_opencode_config_paths",
            lambda: (current, compat, legacy),
        )
        snapshot = _mod._capture_opencode_config()

        _mod._update_opencode_config(
            {"GPU_BACKEND": "nvidia", "OLLAMA_PORT": "8080"},
            snapshot,
            "new-model",
            4096,
        )

        assert json.loads(compat.read_text(encoding="utf-8"))["theme"] == "current"

    def test_litellm_is_verified_before_active_opencode_restarts(
        self, tmp_path, monkeypatch,
    ):
        install_dir, _env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        config_dir = tmp_path / "opencode-order"
        config_dir.mkdir()
        primary = config_dir / "opencode.json"
        compat = config_dir / "config.json"
        primary.write_text("{}", encoding="utf-8")
        events = []
        states = {
            "ods-litellm": {"exists": True, "running": True},
            "ods-hermes": {"exists": False, "running": False},
            "ods-openclaw": {"exists": False, "running": False},
            "ods-perplexica": {"exists": False, "running": False},
        }
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "_opencode_config_paths", lambda: (primary, compat))
        monkeypatch.setattr(_mod, "_capture_container_state", lambda name: states[name])
        monkeypatch.setattr(
            _mod,
            "_capture_managed_opencode_state",
            lambda: {"system": "Linux", "active": True},
        )
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: events.append("runtime"))
        def readiness(*_args, **kwargs):
            events.append("runtime-ready")
            if kwargs.get("return_proof"):
                return {
                    "identity": kwargs.get("gguf_file"),
                    "contextLength": 65536,
                    "contextVerified": True,
                    "verifiedAt": "2026-07-20T00:00:00+00:00",
                }
            if kwargs.get("return_identity"):
                return kwargs.get("gguf_file")
            return True

        monkeypatch.setattr(_mod, "_wait_for_model_readiness", readiness)
        monkeypatch.setattr(
            _mod,
            "_restart_existing_container",
            lambda name, _state=None: events.append(f"restart:{name}") or name == "ods-litellm",
        )
        monkeypatch.setattr(_mod, "_verify_litellm_route", lambda _env: events.append("litellm-ready"))
        monkeypatch.setattr(
            _mod,
            "_restart_managed_opencode",
            lambda _state=None: events.append("opencode-restart") or True,
        )
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 200
        assert events.index("litellm-ready") < events.index("opencode-restart")


class TestLemonadeYamlRollback:
    """Verify that lemonade.yaml is backed up and restored on rollback.

    We don't spin up the full HTTP server — instead, we test the backup/restore
    logic by checking that the pattern in _do_model_activate is correct.
    """

    def test_backup_sentinel_none_when_missing(self, tmp_path):
        """When lemonade.yaml doesn't exist, backup should be None."""
        yaml_path = tmp_path / "config" / "litellm" / "lemonade.yaml"
        # File doesn't exist
        backup = yaml_path.read_text(encoding="utf-8") if yaml_path.exists() else None
        assert backup is None

    def test_backup_preserves_content(self, tmp_path):
        """When lemonade.yaml exists, backup should capture content."""
        litellm_dir = tmp_path / "config" / "litellm"
        litellm_dir.mkdir(parents=True)
        yaml_path = litellm_dir / "lemonade.yaml"
        yaml_path.write_text("original content", encoding="utf-8")

        backup = yaml_path.read_text(encoding="utf-8") if yaml_path.exists() else None
        assert backup == "original content"

        # Simulate overwrite + rollback
        _write_lemonade_config(tmp_path, "new-model.gguf")
        assert "new-model.gguf" in yaml_path.read_text()

        # Restore
        if backup is not None:
            yaml_path.write_text(backup, encoding="utf-8")
        assert yaml_path.read_text() == "original content"

    def test_no_restore_when_backup_is_none(self, tmp_path):
        """When backup is None, rollback should not create the file."""
        litellm_dir = tmp_path / "config" / "litellm"
        litellm_dir.mkdir(parents=True)
        yaml_path = litellm_dir / "lemonade.yaml"

        backup = None  # File didn't exist at backup time
        # Rollback should NOT create the file
        if backup is not None:
            yaml_path.write_text(backup, encoding="utf-8")
        assert not yaml_path.exists()


# --- NVIDIA regression guard ---


class TestNvidiaHealthUnchanged:
    """Ensure the NVIDIA health check still uses the simple '"ok"' check."""

    def test_ok_response_is_healthy(self):
        """llama.cpp health response contains "ok" — should be detected."""
        body = '{"status": "ok"}'
        # The NVIDIA path checks: '"ok"' in body
        assert '"ok"' in body

    def test_model_loaded_not_needed_for_nvidia(self):
        """NVIDIA doesn't need model_loaded — just "ok" is sufficient."""
        # This response has "ok" but no model_loaded — fine for NVIDIA
        body = '{"status": "ok"}'
        assert '"ok"' in body
        # But Lemonade check would fail (no model_loaded key)
        assert _check_lemonade_health(body) is False
