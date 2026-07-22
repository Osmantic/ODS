"""Switchboard PR 2A: adapter contract + reconciler transaction boundaries."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_BIN_DIR = Path(__file__).resolve().parents[4] / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

from model_switchboard import adapters as ad  # noqa: E402
from model_switchboard import reconciler as rc  # noqa: E402


def _proof_result(identity="M.gguf", **overrides):
    payload = {
        "identity": identity,
        "contextLength": 65536,
        "contextVerified": True,
        "capabilities": {
            "chat": True,
            "tools": False,
            "vision": False,
            "agentViable": True,
        },
        "verifiedAt": "2026-07-20T00:00:00Z",
    }
    payload.update(overrides)
    return ad.result(True, **payload)


def _readiness_proof(identity="M.gguf", context_length=65536):
    return {
        "identity": identity,
        "contextLength": context_length,
        "contextVerified": True,
        "verifiedAt": "2026-07-20T00:00:00+00:00",
    }


class TestReconcilerBoundaries:
    def test_success_runs_phases_in_order(self):
        fake = ad.FakeAdapter({
            "verify_identity": [_proof_result()],
            "verify_completion": [_proof_result()],
        })
        run = rc.run_runtime_activation(fake, {})
        assert run["ok"] is True
        assert run["identity"] == "M.gguf"
        assert run["contextLength"] == 65536
        assert run["capabilities"]["agentViable"] is True
        assert run["verifiedAt"] == "2026-07-20T00:00:00Z"
        assert fake.calls == list(rc.PHASES)

    def test_stage_failure_stops_sequence(self):
        fake = ad.FakeAdapter({"stage": [ad.result(False, "boom")]})
        run = rc.run_runtime_activation(fake, {})
        assert run["ok"] is False and run["phase"] == "stage"
        assert "boom" in run["detail"]
        assert fake.calls == ["stage"]

    def test_identity_failure_stops_before_completion(self):
        fake = ad.FakeAdapter({
            "verify_identity": [ad.result(False, "wrong model")],
        })
        run = rc.run_runtime_activation(fake, {})
        assert run["ok"] is False and run["phase"] == "verify_identity"
        assert fake.calls == ["stage", "verify_identity"]

    def test_identity_ok_without_identity_cannot_pass(self):
        # Health-only success must not satisfy verification.
        fake = ad.FakeAdapter({"verify_identity": [ad.result(True)]})
        run = rc.run_runtime_activation(fake, {})
        assert run["ok"] is False and run["phase"] == "verify_identity"
        assert "no concrete identity" in run["detail"]

    def test_completion_failure_reports_boundary(self):
        fake = ad.FakeAdapter({
            "verify_identity": [_proof_result()],
            "verify_completion": [ad.result(False, "no tokens")],
        })
        run = rc.run_runtime_activation(fake, {})
        assert run["ok"] is False and run["phase"] == "verify_completion"
        assert run["identity"] == "M.gguf"

    @pytest.mark.parametrize(
        ("missing", "detail"),
        [
            ("identity", "identity"),
            ("contextLength", "context length"),
            ("contextVerified", "context-verification"),
            ("capabilities", "capability"),
            ("verifiedAt", "timestamp"),
        ],
    )
    def test_successful_verification_requires_full_proof_contract(
        self, missing, detail
    ):
        outcome = _proof_result()
        outcome.pop(missing)
        fake = ad.FakeAdapter({"verify_identity": [outcome]})
        run = rc.run_runtime_activation(fake, {})
        assert run["ok"] is False
        assert run["phase"] == "verify_identity"
        assert detail in run["detail"]

    def test_completion_identity_must_match_identity_proof(self):
        fake = ad.FakeAdapter({
            "verify_identity": [_proof_result("new.gguf")],
            "verify_completion": [_proof_result("old.gguf")],
        })
        run = rc.run_runtime_activation(fake, {})
        assert run["ok"] is False
        assert run["phase"] == "verify_completion"
        assert "does not match" in run["detail"]

    def test_completion_metadata_must_match_identity_proof(self):
        fake = ad.FakeAdapter({
            "verify_identity": [_proof_result()],
            "verify_completion": [_proof_result(contextLength=32768)],
        })
        run = rc.run_runtime_activation(fake, {})
        assert run["ok"] is False
        assert "metadata does not match" in run["detail"]

    def test_completion_can_carry_a_later_proof_timestamp(self):
        fake = ad.FakeAdapter({
            "verify_identity": [_proof_result()],
            "verify_completion": [
                _proof_result(verifiedAt="2026-07-20T00:00:01Z")
            ],
        })
        run = rc.run_runtime_activation(fake, {})
        assert run["ok"] is True
        assert run["verifiedAt"] == "2026-07-20T00:00:01Z"

    @pytest.mark.parametrize("verified_at", ["not-a-time", "2026-07-20T00:00:00"])
    def test_proof_timestamp_must_be_valid_utc(self, verified_at):
        fake = ad.FakeAdapter({
            "verify_identity": [_proof_result(verifiedAt=verified_at)],
        })
        run = rc.run_runtime_activation(fake, {})
        assert run["ok"] is False
        assert "timestamp" in run["detail"]

    def test_zero_context_cannot_pass_verification(self):
        fake = ad.FakeAdapter({
            "verify_identity": [_proof_result(contextLength=0)],
        })
        run = rc.run_runtime_activation(fake, {})
        assert run["ok"] is False
        assert "context length" in run["detail"]

    def test_adapter_exception_is_contract_violation(self):
        class Exploding(ad.FakeAdapter):
            def stage(self, env):
                raise RuntimeError("adapter bug")

        run = rc.run_runtime_activation(Exploding(), {})
        assert run["ok"] is False and run["phase"] == "stage"
        assert "adapter raised" in run["detail"]

    def test_non_contract_result_fails(self):
        class Weird(ad.FakeAdapter):
            def stage(self, env):
                return "ok"  # not a contract dict

        run = rc.run_runtime_activation(Weird(), {})
        assert run["ok"] is False and run["phase"] == "stage"
        assert "non-contract" in run["detail"]


class TestContainerLlamaAdapter:
    def test_delegates_with_expected_arguments(self):
        seen = {}

        def restart(env):
            seen["restart_env"] = env

        def wait_ready(env, gguf, ctx, lemonade_model_id=""):
            seen["wait"] = (gguf, ctx, lemonade_model_id)
            return _readiness_proof("runtime/Model.gguf", 4096)

        adapter = ad.ContainerLlamaAdapter(
            restart=restart,
            wait_ready=wait_ready,
            expected_gguf="Model.gguf",
            context_length=4096,
        )
        env = {"GPU_BACKEND": "nvidia"}
        run = rc.run_runtime_activation(adapter, env)
        assert run["ok"] is True
        assert run["identity"] == "runtime/Model.gguf"
        assert run["contextLength"] == 4096
        assert run["capabilities"] == {
            "chat": True,
            "tools": False,
            "vision": False,
            "agentViable": False,
        }
        assert run["verifiedAt"]
        assert seen["restart_env"] is env
        assert seen["wait"] == ("Model.gguf", 4096, "")

    def test_restart_exception_becomes_stage_failure(self):
        adapter = ad.ContainerLlamaAdapter(
            restart=lambda _env: (_ for _ in ()).throw(OSError("compose down")),
            wait_ready=lambda *a, **k: _readiness_proof("M.gguf", 1024),
            expected_gguf="M.gguf",
            context_length=1024,
        )
        run = rc.run_runtime_activation(adapter, {})
        assert run["ok"] is False and run["phase"] == "stage"
        assert "compose down" in run["detail"]

    def test_not_ready_is_identity_failure(self):
        adapter = ad.ContainerLlamaAdapter(
            restart=lambda _env: None,
            wait_ready=lambda *a, **k: {},
            expected_gguf="M.gguf",
            context_length=1024,
        )
        run = rc.run_runtime_activation(adapter, {})
        assert run["ok"] is False and run["phase"] == "verify_identity"


class TestNativeLlamaAdapter:
    def test_windows_native_delegation_and_kind(self):
        calls = []
        adapter = ad.NativeLlamaAdapter(
            restart=lambda _e: calls.append("restart"),
            wait_ready=lambda *a, **k: (
                calls.append("wait"),
                _readiness_proof("Win.gguf", 8192),
            )[1],
            expected_gguf="Win.gguf",
            context_length=8192,
        )
        run = rc.run_runtime_activation(adapter, {})
        assert run["ok"] is True and run["identity"] == "Win.gguf"
        assert adapter.kind == "llama-server"
        assert calls == ["restart", "wait"]

    def test_native_restart_failure_is_stage_boundary(self):
        adapter = ad.NativeLlamaAdapter(
            restart=lambda _e: (_ for _ in ()).throw(RuntimeError("launchd bootout failed")),
            wait_ready=lambda *a, **k: _readiness_proof("Mac.gguf", 8192),
            expected_gguf="Mac.gguf",
            context_length=8192,
        )
        run = rc.run_runtime_activation(adapter, {})
        assert run["ok"] is False and run["phase"] == "stage"
        assert "launchd bootout failed" in run["detail"]

    def test_boolean_readiness_cannot_masquerade_as_identity(self):
        adapter = ad.NativeLlamaAdapter(
            restart=lambda _e: None,
            wait_ready=lambda *a, **k: True,
            expected_gguf="Configured.gguf",
            context_length=8192,
        )
        run = rc.run_runtime_activation(adapter, {})
        assert run["ok"] is False
        assert run["phase"] == "verify_identity"

    def test_full_adapter_contract_is_explicit(self):
        required = {
            "stage",
            "verify_identity",
            "verify_completion",
            "publish_native_alias",
            "unload",
            "delete",
            "rollback",
        }
        assert required.issubset(set(dir(ad.RuntimeAdapter)))
        assert required.issubset(set(dir(ad.FakeAdapter)))


class TestLemonadeAdapter:
    def test_verify_uses_resolved_lemonade_id(self):
        seen = {}

        def wait_ready(env, gguf, ctx, lemonade_model_id=""):
            seen["args"] = (gguf, ctx, lemonade_model_id)
            return _readiness_proof("extra.Q.gguf", 65536)

        adapter = ad.LemonadeAdapter(
            wait_ready=wait_ready,
            expected_gguf="Q.gguf",
            context_length=65536,
            lemonade_model_id="extra.Q.gguf",
        )
        run = rc.run_runtime_activation(adapter, {})
        assert run["ok"] is True
        assert run["identity"] == "extra.Q.gguf"
        assert run["contextLength"] == 65536
        assert adapter.kind == "lemonade"
        assert seen["args"] == ("Q.gguf", 65536, "extra.Q.gguf")

    def test_missing_id_is_stage_failure(self):
        adapter = ad.LemonadeAdapter(
            wait_ready=lambda *a, **k: True,
            expected_gguf="Q.gguf",
            context_length=1024,
            lemonade_model_id="",
        )
        run = rc.run_runtime_activation(adapter, {})
        assert run["ok"] is False and run["phase"] == "stage"

    def test_not_ready_is_identity_failure(self):
        adapter = ad.LemonadeAdapter(
            wait_ready=lambda *a, **k: {},
            expected_gguf="Q.gguf",
            context_length=1024,
            lemonade_model_id="extra.Q.gguf",
        )
        run = rc.run_runtime_activation(adapter, {})
        assert run["ok"] is False and run["phase"] == "verify_identity"

    def test_boolean_readiness_cannot_echo_configured_lemonade_id(self):
        adapter = ad.LemonadeAdapter(
            wait_ready=lambda *a, **k: True,
            expected_gguf="Q.gguf",
            context_length=1024,
            lemonade_model_id="extra.Q.gguf",
        )
        run = rc.run_runtime_activation(adapter, {})
        assert run["ok"] is False
        assert run["identity"] is None


class TestHostAgentWiring:
    def test_compose_llama_path_flows_through_reconciler(self, tmp_path, monkeypatch):
        import subprocess
        import test_model_activate as tma

        install_dir = tma._write_model_activation_fixture(tmp_path)[0]
        monkeypatch.setattr(tma._mod, "INSTALL_DIR", install_dir)
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(tma._mod.time, "sleep", lambda _s: None)

        order: list[str] = []
        monkeypatch.setattr(
            tma._mod, "_compose_restart_llama_server",
            lambda _env: order.append("restart"),
        )
        real_wait = tma._mod._wait_for_model_readiness

        def spying_wait(env, **kwargs):
            order.append("wait")
            return real_wait(env, **kwargs)

        monkeypatch.setattr(tma._mod, "_wait_for_model_readiness", spying_wait)

        def fake_run(cmd, **_kwargs):
            url = next((str(part) for part in cmd if str(part).startswith("http")), "")
            if url.endswith("/v1/models"):
                stdout = tma._llama_identity_response("new-model.gguf")
            elif url.endswith("/props"):
                stdout = json.dumps({
                    "default_generation_settings": {"n_ctx": 65536}
                })
            else:
                stdout = ""
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(tma._mod.subprocess, "run", fake_run)
        assert tma._mod._switchboard_adapters is not None

        handler = tma._ResponseHandler()
        tma._mod.AgentHandler._do_model_activate(handler, "target-model")
        assert handler.response_code == 200
        assert order[:2] == ["restart", "wait"]
        state = json.loads(
            (install_dir / "data" / "model-state.json").read_text(encoding="utf-8")
        )
        assert state["active"]["runtimeModelId"] == "new-model.gguf"
        assert state["active"]["proof"]["identity"] == "new-model.gguf"

    def test_reconciler_failure_uses_existing_rollback(self, tmp_path, monkeypatch):
        import subprocess
        import test_model_activate as tma

        install_dir = tma._write_model_activation_fixture(tmp_path)[0]
        env_path = install_dir / ".env"
        before_env = env_path.read_text(encoding="utf-8")

        monkeypatch.setattr(tma._mod, "INSTALL_DIR", install_dir)
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(tma._mod.time, "sleep", lambda _s: None)
        monkeypatch.setattr(
            tma._mod, "_compose_restart_llama_server", lambda _env: None
        )

        def failing_run(cmd, **_kwargs):
            stdout = tma._llama_identity_response("wrong-model.gguf") if cmd and cmd[0] == "curl" else ""
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(tma._mod.subprocess, "run", failing_run)
        handler = tma._ResponseHandler()
        tma._mod.AgentHandler._do_model_activate(handler, "target-model")
        assert handler.response_code != 200
        payload = handler.parse_response()
        assert payload["error"].startswith("Health check failed;")
        assert "restoration could not be proved" in payload["error"]
        assert payload["failure_phase"] == "verify_identity"
        assert "runtime did not report the staged model" in payload["failure_detail"]
        # rollback restored the pre-activation env exactly as before PR 2A
        assert env_path.read_text(encoding="utf-8") == before_env

    def test_completion_failure_detail_survives_successful_rollback(
        self, tmp_path, monkeypatch
    ):
        import test_model_activate as tma

        install_dir, env_path = tma._write_model_activation_fixture(tmp_path)[:2]
        before_env = env_path.read_text(encoding="utf-8")
        monkeypatch.setattr(tma._mod, "INSTALL_DIR", install_dir)
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(
            tma._mod, "_compose_restart_llama_server", lambda _env: None
        )
        monkeypatch.setattr(
            tma._mod,
            "_wait_for_model_readiness",
            lambda *_args, **_kwargs: True,
        )
        monkeypatch.setattr(
            tma._mod._switchboard_reconciler,
            "run_runtime_activation",
            lambda *_args, **_kwargs: {
                "ok": False,
                "phase": "verify_completion",
                "detail": "completion response carried the previous model",
                "identity": "new-model.gguf",
            },
        )
        handler = tma._ResponseHandler()
        tma._mod.AgentHandler._do_model_activate(handler, "target-model")
        assert handler.response_code == 500
        payload = handler.parse_response()
        assert payload["rolled_back"] is True
        assert payload["error"] == (
            "Health check failed — rolled back to previous model"
        )
        assert payload["failure_phase"] == "verify_completion"
        assert payload["failure_detail"] == (
            "completion response carried the previous model"
        )
        assert env_path.read_text(encoding="utf-8") == before_env

    def test_stage_exception_keeps_existing_error_contract(self, tmp_path, monkeypatch):
        import subprocess
        import test_model_activate as tma

        install_dir = tma._write_model_activation_fixture(tmp_path)[0]
        monkeypatch.setattr(tma._mod, "INSTALL_DIR", install_dir)
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(tma._mod.time, "sleep", lambda _s: None)
        restart_calls = 0

        def restart(_env):
            nonlocal restart_calls
            restart_calls += 1
            if restart_calls == 1:
                raise OSError("compose down")

        monkeypatch.setattr(tma._mod, "_compose_restart_llama_server", restart)

        def fake_run(cmd, **_kwargs):
            stdout = (
                tma._llama_identity_response("old-model.gguf")
                if cmd and cmd[0] == "curl"
                else ""
            )
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(tma._mod.subprocess, "run", fake_run)
        handler = tma._ResponseHandler()
        tma._mod.AgentHandler._do_model_activate(handler, "target-model")
        assert handler.response_code == 500
        payload = handler.parse_response()
        assert payload["error"] == "Model activation failed: compose down"
        assert payload["failure_phase"] == "stage"
        assert payload["failure_detail"] == "compose down"
        assert payload["rolled_back"] is True


@pytest.fixture(autouse=True)
def _isolation(monkeypatch, tmp_path, request):
    if "TestHostAgentWiring" not in str(request.node.nodeid):
        yield
        return
    import test_model_activate as tma
    config_dir = tmp_path / "isolated-home" / ".config" / "opencode"
    monkeypatch.setattr(
        tma._mod, "_opencode_config_paths",
        lambda: (config_dir / "opencode.json", config_dir / "config.json"),
    )
    monkeypatch.setattr(tma._mod, "_chat_completion_ready", lambda *_a, **_k: True)
    monkeypatch.setattr(
        tma._mod, "_llama_runtime_context_length", lambda *_args: 65536
    )
    monkeypatch.setattr(tma._mod, "_container_exists", lambda _c: False)
    monkeypatch.setattr(tma._mod, "_container_running", lambda _c: False)
    monkeypatch.setattr(
        tma._mod, "_capture_container_state",
        lambda container: {"exists": False, "running": False},
    )
    monkeypatch.setattr(tma._mod, "_wait_for_container_health", lambda _c: None)
    monkeypatch.setattr(
        tma._mod, "_capture_managed_opencode_state",
        lambda: {"system": tma._mod.platform.system(), "active": False},
    )
    monkeypatch.setattr(tma._mod, "_opencode_installed", lambda: False)
    yield
