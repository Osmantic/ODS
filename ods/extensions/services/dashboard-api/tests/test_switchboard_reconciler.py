"""Switchboard PR 2A: adapter contract + reconciler transaction boundaries."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BIN_DIR = Path(__file__).resolve().parents[4] / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

from model_switchboard import adapters as ad  # noqa: E402
from model_switchboard import reconciler as rc  # noqa: E402


class TestReconcilerBoundaries:
    def test_success_runs_phases_in_order(self):
        fake = ad.FakeAdapter({
            "verify_identity": [ad.result(True, identity="M.gguf")],
        })
        run = rc.run_runtime_activation(fake, {})
        assert run["ok"] is True
        assert run["identity"] == "M.gguf"
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
            "verify_identity": [ad.result(True, identity="M.gguf")],
            "verify_completion": [ad.result(False, "no tokens")],
        })
        run = rc.run_runtime_activation(fake, {})
        assert run["ok"] is False and run["phase"] == "verify_completion"
        assert run["identity"] == "M.gguf"

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
            return True

        adapter = ad.ContainerLlamaAdapter(
            restart=restart,
            wait_ready=wait_ready,
            expected_gguf="Model.gguf",
            context_length=4096,
        )
        env = {"GPU_BACKEND": "nvidia"}
        run = rc.run_runtime_activation(adapter, env)
        assert run["ok"] is True
        assert run["identity"] == "Model.gguf"
        assert seen["restart_env"] is env
        assert seen["wait"] == ("Model.gguf", 4096, "")

    def test_restart_exception_becomes_stage_failure(self):
        adapter = ad.ContainerLlamaAdapter(
            restart=lambda _env: (_ for _ in ()).throw(OSError("compose down")),
            wait_ready=lambda *a, **k: True,
            expected_gguf="M.gguf",
            context_length=1024,
        )
        run = rc.run_runtime_activation(adapter, {})
        assert run["ok"] is False and run["phase"] == "stage"
        assert "compose down" in run["detail"]

    def test_not_ready_is_identity_failure(self):
        adapter = ad.ContainerLlamaAdapter(
            restart=lambda _env: None,
            wait_ready=lambda *a, **k: False,
            expected_gguf="M.gguf",
            context_length=1024,
        )
        run = rc.run_runtime_activation(adapter, {})
        assert run["ok"] is False and run["phase"] == "verify_identity"


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
            stdout = tma._llama_identity_response("new-model.gguf") if cmd and cmd[0] == "curl" else ""
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(tma._mod.subprocess, "run", fake_run)
        assert tma._mod._switchboard_adapters is not None

        handler = tma._ResponseHandler()
        tma._mod.AgentHandler._do_model_activate(handler, "target-model")
        assert handler.response_code == 200
        assert order[:2] == ["restart", "wait"]

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
        # rollback restored the pre-activation env exactly as before PR 2A
        assert env_path.read_text(encoding="utf-8") == before_env


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
