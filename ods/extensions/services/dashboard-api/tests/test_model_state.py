"""Model Switchboard PR 1 tests: state module, API endpoint, observe hook."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

_BIN_DIR = Path(__file__).resolve().parents[4] / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

from model_switchboard import state as sb  # noqa: E402

_SCHEMA_PATH = Path(__file__).resolve().parents[4] / "config" / "model-state.schema.v1.json"


def _record(path, model="qwen3.5-9b", runtime="Qwen3.5-9B-Q4_K_M.gguf", backend="llama-server",
            endpoint="llama-server-default", context=32768):
    return sb.record_verified_route(
        path,
        catalog_id=model,
        runtime_model_id=runtime,
        backend_kind=backend,
        endpoint_id=endpoint,
        context_length=context,
        capabilities={"chat": True, "tools": False, "vision": False, "agentViable": context >= 65536},
        proof_identity=runtime,
    )


class TestStateModule:
    def test_roundtrip_and_schema_agreement(self, tmp_path):
        path = tmp_path / "model-state.json"
        doc = _record(path)
        assert doc["seq"] == 1 and doc["routeSeq"] == 1
        read, errors = sb.read_state(path)
        assert errors == [] and read == doc
        assert read["active"]["runtimeModelId"] == "Qwen3.5-9B-Q4_K_M.gguf"
        assert read["active"]["proof"] == {"identity": "Qwen3.5-9B-Q4_K_M.gguf", "completion": True}
        assert sb.validate_state(read) == []
        jsonschema = pytest.importorskip("jsonschema")
        schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
        jsonschema.validate(read, schema)

    def test_seq_monotonic_routeseq_only_on_change(self, tmp_path):
        path = tmp_path / "model-state.json"
        _record(path)
        doc = _record(path)  # identical route: seq moves, routeSeq does not
        assert doc["seq"] == 2 and doc["routeSeq"] == 1 and doc["history"] == []
        doc = _record(path, model="phi-4-mini", runtime="Phi-4-mini-Q4_K_M.gguf")
        assert doc["seq"] == 3 and doc["routeSeq"] == 2
        assert doc["history"][0]["runtimeModelId"] == "Qwen3.5-9B-Q4_K_M.gguf"
        assert doc["history"][0]["routeSeq"] == 1

    def test_history_bounded(self, tmp_path):
        path = tmp_path / "model-state.json"
        for index in range(13):
            _record(path, model=f"m-{index}", runtime=f"M-{index}.gguf")
        doc, errors = sb.read_state(path)
        assert errors == []
        assert len(doc["history"]) == sb.HISTORY_LIMIT
        assert doc["history"][0]["runtimeModelId"] == "M-11.gguf"

    def test_interrupted_write_keeps_original(self, tmp_path, monkeypatch):
        path = tmp_path / "model-state.json"
        _record(path)
        original = path.read_text(encoding="utf-8")
        real_replace = os.replace

        def exploding_replace(src, dst):
            raise OSError("simulated crash before rename")

        monkeypatch.setattr(sb.os, "replace", exploding_replace)
        with pytest.raises(OSError):
            _record(path, model="other", runtime="Other.gguf")
        monkeypatch.setattr(sb.os, "replace", real_replace)
        assert path.read_text(encoding="utf-8") == original
        assert not list(tmp_path.glob("*.tmp"))

    def test_concurrent_readers_never_see_partial(self, tmp_path):
        path = tmp_path / "model-state.json"
        _record(path)
        stop = threading.Event()
        problems: list[str] = []

        writer_error: list[str] = []

        def writer():
            try:
                for index in range(60):
                    _record(path, model=f"w-{index % 3}", runtime=f"W-{index % 3}.gguf")
            except Exception as exc:  # never hang pytest on a writer fault
                writer_error.append(repr(exc))
            finally:
                stop.set()

        thread = threading.Thread(target=writer)
        thread.start()
        while not stop.is_set():
            doc, errors = sb.read_state(path)
            if doc is None or sb.validate_state(doc):
                problems.append(f"bad read: {errors}")
                break
            time.sleep(0.001)
        thread.join(timeout=30)
        assert writer_error == []
        assert problems == []

    def test_malformed_state_is_diagnostic_never_promoted(self, tmp_path):
        path = tmp_path / "model-state.json"
        path.write_text("{not json", encoding="utf-8")
        doc, errors = sb.read_state(path)
        assert doc is None and errors
        with pytest.raises(sb.StateError):
            _record(path)
        assert sb.initialize_if_missing(path, {"GGUF_FILE": "X.gguf"}) is None
        assert path.read_text(encoding="utf-8") == "{not json"

    def test_validate_rejects_bad_shapes(self, tmp_path):
        doc = sb.initial_state()
        doc["seq"] = -1
        assert any("seq" in e for e in sb.validate_state(doc))
        doc = sb.initial_state()
        doc["operation"] = {"id": "x", "phase": "warp", "requestedModelId": "m", "startedAt": "t"}
        assert any("phase" in e for e in sb.validate_state(doc))
        doc = _record(tmp_path / "s.json")
        doc["active"]["backend"]["kind"] = "banana"
        assert any("backend.kind" in e for e in sb.validate_state(doc))
        doc = _record(tmp_path / "extra.json")
        doc["unexpected"] = True
        assert any("unexpected keys" in e for e in sb.validate_state(doc))
        doc = _record(tmp_path / "negative.json")
        doc["active"]["routeSeq"] = -1
        assert any("active.routeSeq" in e for e in sb.validate_state(doc))

    @pytest.mark.parametrize(
        "env,expected",
        [
            ({"GGUF_FILE": "Qwen3.5-9B-Q4_K_M.gguf", "LLM_MODEL": "qwen3.5-9b"},
             {"catalogId": "qwen3.5-9b", "runtimeModelId": "Qwen3.5-9B-Q4_K_M.gguf", "backendKind": "llama-server"}),
            ({"LEMONADE_MODEL": "extra.Qwen3.5-9B-Q4_K_M.gguf", "LLM_BACKEND": "lemonade"},
             {"catalogId": "Qwen3.5-9B-Q4_K_M", "runtimeModelId": "extra.Qwen3.5-9B-Q4_K_M.gguf", "backendKind": "lemonade"}),
            ({"GGUF_FILE": "M.gguf", "AMD_INFERENCE_RUNTIME": "lemonade"},
             {"catalogId": "M", "runtimeModelId": "M.gguf", "backendKind": "lemonade"}),
            ({"LLM_MODEL": "native-model"},
             {"catalogId": "native-model", "runtimeModelId": "native-model", "backendKind": "llama-server"}),
        ],
    )
    def test_migrate_env_forms(self, env, expected):
        got = sb.migrate_env_identity(env)
        for key, value in expected.items():
            assert got[key] == value

    def test_migrate_cloud_only_yields_none(self):
        env = {
            "ODS_MODE": "cloud",
            "LLM_MODEL": "anthropic/claude-sonnet-4-5-20250514",
            "GGUF_FILE": "",
            "MAX_CONTEXT": "200000",
        }
        assert sb.migrate_env_identity(env) is None

    def test_initialize_if_missing_reconstructs_once(self, tmp_path):
        path = tmp_path / "model-state.json"
        env = {"GGUF_FILE": "Qwen3.5-9B-Q4_K_M.gguf", "LLM_MODEL": "qwen3.5-9b", "MAX_CONTEXT": "131072"}
        doc = sb.initialize_if_missing(path, env)
        assert doc["active"]["reconstructed"] is True
        assert doc["active"]["verifiedAt"] is None
        assert doc["active"]["proof"]["completion"] is False
        assert doc["active"]["capabilities"]["agentViable"] is True
        before = path.read_text(encoding="utf-8")
        assert sb.initialize_if_missing(path, {"GGUF_FILE": "Other.gguf"}) is None
        assert path.read_text(encoding="utf-8") == before

    def test_initialize_cloud_only_writes_nothing(self, tmp_path):
        path = tmp_path / "model-state.json"
        env = {
            "ODS_MODE": "cloud",
            "LLM_MODEL": "anthropic/claude-sonnet-4-5-20250514",
            "MAX_CONTEXT": "200000",
        }
        assert sb.initialize_if_missing(path, env) is None
        assert not path.exists()

    def test_reconstructed_route_never_enters_verified_history(self, tmp_path):
        path = tmp_path / "model-state.json"
        reconstructed = sb.initialize_if_missing(
            path,
            {
                "ODS_MODE": "local",
                "LLM_MODEL": "stale-model",
                "GGUF_FILE": "Stale.gguf",
                "MAX_CONTEXT": "65536",
            },
        )
        assert reconstructed["active"]["proof"]["completion"] is False

        verified = _record(
            path,
            model="current-model",
            runtime="Current.gguf",
            context=65536,
        )
        assert verified["active"]["proof"]["completion"] is True
        assert verified["history"] == []


class TestModelStateEndpoint:
    def _point_at(self, monkeypatch, tmp_path):
        data_dir = tmp_path / "mounted-data"
        data_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("ODS_DATA_DIR", str(data_dir))
        monkeypatch.setenv("ODS_MODEL_STATE_SCHEMA_PATH", str(_SCHEMA_PATH))
        return data_dir / "model-state.json"

    def test_state_path_uses_data_mount_environment(self, monkeypatch, tmp_path):
        from routers import model_state as ms

        data_dir = tmp_path / "compose-data-mount"
        monkeypatch.setenv("ODS_DATA_DIR", str(data_dir))
        monkeypatch.setattr(ms, "INSTALL_DIR", str(tmp_path / "ods"))
        assert ms._state_path() == data_dir / "model-state.json"

    def test_missing_state(self, test_client, monkeypatch, tmp_path):
        self._point_at(monkeypatch, tmp_path)
        resp = test_client.get("/api/models/state", headers=test_client.auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["exists"] is False and body["valid"] is False
        assert body["active"] is None and body["historyCount"] == 0

    def test_valid_state_summary(self, test_client, monkeypatch, tmp_path):
        path = self._point_at(monkeypatch, tmp_path)
        _record(path, context=131072)
        _record(path, model="phi-4-mini", runtime="Phi-4-mini-Q4_K_M.gguf", context=131072)
        resp = test_client.get("/api/models/state", headers=test_client.auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["exists"] is True and body["valid"] is True
        assert body["routeSeq"] == 2
        assert body["active"]["catalogId"] == "phi-4-mini"
        assert body["historyCount"] == 1
        assert body["capabilityImpact"]["agentViable"] is True

    def test_malformed_state_is_diagnostic(self, test_client, monkeypatch, tmp_path):
        path = self._point_at(monkeypatch, tmp_path)
        path.write_text("][", encoding="utf-8")
        resp = test_client.get("/api/models/state", headers=test_client.auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["exists"] is True and body["valid"] is False and body["errors"]

    def test_schema_invalid_state_is_diagnostic(self, test_client, monkeypatch, tmp_path):
        path = self._point_at(monkeypatch, tmp_path)
        doc = _record(path)
        doc["active"]["routeSeq"] = -1
        doc["unexpected"] = "must be rejected"
        path.write_text(json.dumps(doc), encoding="utf-8")

        resp = test_client.get("/api/models/state", headers=test_client.auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["exists"] is True
        assert body["valid"] is False
        assert body["active"] is None
        assert any("routeSeq" in error for error in body["errors"])
        assert any("unexpected" in error for error in body["errors"])

    def test_requires_auth(self, test_client, monkeypatch, tmp_path):
        self._point_at(monkeypatch, tmp_path)
        resp = test_client.get("/api/models/state")
        assert resp.status_code in (401, 403)


class TestObserveHook:
    @pytest.fixture(autouse=True)
    def _isolation(self, monkeypatch, tmp_path):
        import test_model_activate as tma
        config_dir = tmp_path / "isolated-home" / ".config" / "opencode"
        monkeypatch.setattr(
            tma._mod,
            "_opencode_config_paths",
            lambda: (config_dir / "opencode.json", config_dir / "config.json"),
        )
        monkeypatch.setattr(tma._mod, "_chat_completion_ready", lambda *_a, **_k: True)
        monkeypatch.setattr(
            tma._mod, "_llama_runtime_context_length", lambda *_args: 65536
        )
        monkeypatch.setattr(tma._mod, "_container_exists", lambda _c: False)
        monkeypatch.setattr(tma._mod, "_container_running", lambda _c: False)
        monkeypatch.setattr(
            tma._mod,
            "_capture_container_state",
            lambda container: {"exists": False, "running": False},
        )
        monkeypatch.setattr(tma._mod, "_wait_for_container_health", lambda _c: None)
        monkeypatch.setattr(
            tma._mod,
            "_capture_managed_opencode_state",
            lambda: {"system": tma._mod.platform.system(), "active": False},
        )
        monkeypatch.setattr(tma._mod, "_opencode_installed", lambda: False)

    def test_activation_success_records_state(self, tmp_path, monkeypatch):
        import subprocess
        import test_model_activate as tma

        install_dir = tma._write_model_activation_fixture(tmp_path)[0]
        monkeypatch.setattr(tma._mod, "INSTALL_DIR", install_dir)
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(tma._mod.time, "sleep", lambda _s: None)
        monkeypatch.setattr(tma._mod, "_compose_restart_llama_server", lambda _env: None)

        def fake_run(cmd, **_kwargs):
            stdout = tma._llama_identity_response("new-model.gguf") if cmd and cmd[0] == "curl" else ""
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(tma._mod.subprocess, "run", fake_run)
        assert tma._mod._switchboard_state is not None

        handler = tma._ResponseHandler()
        tma._mod.AgentHandler._do_model_activate(handler, "target-model")
        assert handler.response_code == 200

        state_path = install_dir / "data" / "model-state.json"
        doc, errors = sb.read_state(state_path)
        assert errors == [] and doc is not None
        assert doc["routeSeq"] == 1
        assert doc["active"]["catalogId"] == "target-model"
        assert doc["active"]["runtimeModelId"] == "new-model.gguf"
        assert doc["active"]["proof"]["completion"] is True

    def test_activation_failure_leaves_state_untouched(self, tmp_path, monkeypatch):
        import subprocess
        import test_model_activate as tma

        install_dir = tma._write_model_activation_fixture(tmp_path)[0]
        state_path = install_dir / "data" / "model-state.json"
        _record(state_path, model="previous", runtime="Previous.gguf")
        before = state_path.read_text(encoding="utf-8")

        monkeypatch.setattr(tma._mod, "INSTALL_DIR", install_dir)
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(tma._mod.time, "sleep", lambda _s: None)
        monkeypatch.setattr(tma._mod, "_compose_restart_llama_server", lambda _env: None)

        def failing_run(cmd, **_kwargs):
            # Identity probe returns the wrong model so verification fails.
            stdout = tma._llama_identity_response("wrong-model.gguf") if cmd and cmd[0] == "curl" else ""
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(tma._mod.subprocess, "run", failing_run)
        handler = tma._ResponseHandler()
        tma._mod.AgentHandler._do_model_activate(handler, "target-model")
        assert handler.response_code != 200
        assert state_path.read_text(encoding="utf-8") == before
