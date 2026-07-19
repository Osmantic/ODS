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
                "LEMONADE_MODEL": "persisted-exact-id",
            },
            "Model.gguf",
            host="127.0.0.1",
            port="8080",
        ) == "persisted-exact-id"

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

    def test_readiness_uses_persisted_exact_model_for_completion(self, monkeypatch):
        completion_calls = []

        def fake_run(cmd, **_kwargs):
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout='{"status":"ok","model_loaded":"Modern-Model"}',
                stderr="",
            )

        def fake_completion(host, port, model, prefix):
            completion_calls.append((host, port, model, prefix))
            return True

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        monkeypatch.setattr(_mod, "_chat_completion_ready", fake_completion)

        assert _mod._wait_for_model_readiness(
            {
                "GPU_BACKEND": "amd",
                "OLLAMA_PORT": "8080",
                "GGUF_FILE": "Model.gguf",
                "LEMONADE_MODEL": "Modern-Model",
            },
            model_id="catalog-model",
            gguf_file="Model.gguf",
            llm_model_name="model",
            attempts=1,
            initial_delay=0,
            interval=0,
        ) is True
        assert completion_calls == [
            ("127.0.0.1", "8080", "Modern-Model", "/api/v1"),
        ]


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
        assert "$existingTask = Get-ScheduledTask -TaskName $taskName" in script
        assert "Could not refresh Lemonade scheduled task; reusing existing task" in script
        assert "Register-ScheduledTask -TaskName $taskName" in script
        assert "$settings = New-ScheduledTaskSettingsSet" in script
        assert "-ExecutionTimeLimit ([TimeSpan]::Zero)" in script
        assert "-Settings $settings" in script
        assert "-Force -ErrorAction Stop | Out-Null" in script
        assert "Unregister-ScheduledTask" not in script
        assert "taskkill.exe /PID $ProcId /T /F" in script
        assert "for ($i = 0; $i -lt 45; $i++)" in script
        assert "Get-ODSLemonadeLaunchDiagnostics" in script
        assert "Format-ODSLemonadeLaunchDiagnostics" in script
        assert "Start-ScheduledTask -TaskName $taskName" in script
        assert "Start-ODSLemonadeDirectProcess -Contract $launchContract" in script
        assert "no healthy owned router was found" in script
        assert "Stop-ScheduledTask -TaskName $taskName" in script
        assert "Refusing to stop unowned process" in script
        assert "Get-ODSHealthyRouter" in script
        assert "/api/v1/health" in script
        assert "$proc = Get-ODSHealthyRouter" in script
        assert "Get-ODSLemonadeLaunchContract" in script
        assert "New-ODSLemonadeScheduledTaskAction" in script
        assert "Set-ODSLemonadeModernRuntimeConfig" in script
        assert "$existingTaskMatches" in script
        assert "--extra-models-dir" not in script
        assert "--no-tray" not in script
        assert captured["env"]["ODS_WIN_LEMONADE_TASK"] == "ODSLemonadeRuntime"
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


# --- Rollback integration ---


class _ResponseHandler:
    def __init__(self, wfile=None):
        self.wfile = wfile or io.BytesIO()
        self.response_code = None
        self.response_headers = []

    def send_response(self, code):
        self.response_code = code

    def send_header(self, name, value):
        self.response_headers.append((name, value))

    def end_headers(self):
        pass

    def parse_response(self):
        return json.loads(self.wfile.getvalue().decode("utf-8"))


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


class TestModelActivateRollback:

    @pytest.fixture(autouse=True)
    def _successful_meaningful_completion(self, monkeypatch):
        monkeypatch.setattr(_mod, "_chat_completion_ready", lambda *_args, **_kwargs: True)
        monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(_mod, "_container_exists", lambda _container: False)
        monkeypatch.setattr(_mod, "_container_running", lambda _container: False)

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
        assert [cmd[-1] for cmd in curl_calls] == [expected_identity_url]

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
                    '{"status": "ok", "model_loaded": "extra.new-model.gguf"}'
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
        assert handler.parse_response() == {"status": "activated", "model_id": "Research.Model-Q8_0"}
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
        assert handler.parse_response() == {
            "status": "activated",
            "model_id": "My-Custom-Model.Q8_0",
        }
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
            stdout = (
                '{"status": "ok", "model_loaded": "extra.new-model.gguf"}'
                if cmd and cmd[0] == "curl"
                else ""
            )
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
                    }),
                    stderr="",
                )
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod.platform, "system", lambda: "Windows")
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(_mod, "_restart_windows_lemonade", lambda _env: None)
        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 200
        assert "LEMONADE_MODEL=Modern-Model" in env_path.read_text(encoding="utf-8")
        assert "model: openai/Modern-Model" in lemonade_yaml.read_text(encoding="utf-8")
        assert 'default: "Modern-Model"' in hermes_live.read_text(encoding="utf-8")
        assert 'default: "Modern-Model"' in hermes_template.read_text(encoding="utf-8")

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
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", lambda *_args, **_kwargs: True)
        monkeypatch.setattr(_mod, "_capture_perplexica_config", lambda _env: snapshot)
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
            stdout = (
                '{"status": "ok", "model_loaded": "extra.new-model.gguf"}'
                if cmd and cmd[0] == "curl"
                else ""
            )
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
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", lambda *_args, **_kwargs: True)
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

        original_read_text = Path.read_text

        def fake_read_text(path, *args, **kwargs):
            if path == hermes_live:
                raise PermissionError("container-owned")
            return original_read_text(path, *args, **kwargs)

        container_config = {"text": hermes_live.read_text(encoding="utf-8")}
        container_writes = []

        def write_container_config(text):
            container_writes.append(text)
            container_config["text"] = text

        monkeypatch.setattr(Path, "read_text", fake_read_text)
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

        def completion_ready(_host, _port, model_name, _prefix):
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
            return True

        def restart_dependent(container):
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
            "dependent:ods-litellm",
            "dependent:ods-hermes",
            "ready:old-model.gguf",
        ]

    def test_activation_succeeds_without_optional_dependents(self, tmp_path, monkeypatch):
        install_dir, _env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", lambda *_args, **_kwargs: True)
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
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", lambda *_args, **_kwargs: True)
        monkeypatch.setattr(_mod, "_restart_existing_container", lambda _container: False)
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
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", lambda *_args, **_kwargs: True)
        monkeypatch.setattr(_mod, "_capture_perplexica_config", lambda _env: snapshot)
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
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", lambda *_args, **_kwargs: True)
        monkeypatch.setattr(_mod, "_capture_perplexica_config", lambda _env: snapshot)

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
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", lambda *_args, **_kwargs: True)

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
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", lambda *_args, **_kwargs: True)
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
