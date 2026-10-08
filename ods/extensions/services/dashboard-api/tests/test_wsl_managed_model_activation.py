"""Managed Windows model changes use the existing Portal transaction and CAS.

All OS/runtime boundaries are fixture-owned; no interop, tasks or services run.
"""
import copy
import hashlib
import json
import threading

import pytest
import test_model_activate as fixtures

host = fixtures._mod


@pytest.fixture
def managed(tmp_path, monkeypatch):
    install, env_path, _, *_ = fixtures._write_model_activation_fixture(tmp_path, gpu_backend="cpu")
    models = tmp_path / "windows-models"
    models.mkdir()
    (install / "data/models/new-model.gguf").rename(models / "new-model.gguf")
    (models / "old-model.gguf").write_bytes(b"previous model")
    env_path.write_text(
        env_path.read_text().replace("CTX_SIZE=2048", "CTX_SIZE=65536")
        + "MAX_CONTEXT=65536\nPIXEL_OPENWEBUI_KEY=configured\n"
        "ODS_MODE=local\nLLM_BACKEND=llama-server\n"
        "AMD_INFERENCE_RUNTIME=llama-server\nAMD_INFERENCE_LOCATION=host\n"
        "AMD_INFERENCE_RUNTIME_MODE=windows-portal-llama-server\n"
        "ODS_HOST_LLM_TRANSPORT=model-router\nNATIVE_LLM_BASE_URL=http://localhost:13305\n"
        "NATIVE_LLM_CONTAINER_BASE_URL=http://host.docker.internal:13305\n"
        "ODS_ACTIVE_MODEL_STORE=windows-lemonade\n",
        encoding="utf-8",
    )
    registry = {"schemaVersion": 1, "stores": [{
        "id": "windows-lemonade", "hostPath": str(models),
        "containerPath": "/model-stores/windows-lemonade",
    }]}
    (install / "data/model-stores.json").write_text(json.dumps(registry))
    plan_path = tmp_path / "windows-runtime/portal-runtime/runtime.json"
    plan_path.parent.mkdir(parents=True)
    plan = {
        "ExecutablePath": r"C:\fixture\llama.cpp\b9014-win-vulkan-x64\llama-server.exe", "Port": 13305,
        "ModelsDir": r"C:\fixture\models", "ContextSize": 65536,
        "GgufFile": "old-model.gguf", "WslDistro": "Ubuntu-24.04", "WslInstallDir": str(install),
    }
    registration = install / "data/wsl-lemonade-runtime.json"
    registration.write_text(json.dumps({"schemaVersion": 1, "planPath": str(plan_path),
                                        "modelStoreId": "windows-lemonade"}))
    registration.chmod(0o600)
    previous = {"model": "old-model.gguf", "contextLength": 65536,
                "maxTokens": 3072, "reasoning": True, "routeFingerprint": "d" * 64}
    pixel = dict(schemaVersion=1, status="ready", revision="a" * 64,
                 contract=copy.deepcopy(previous), pending=False, transactionId=None, outcome=None)
    runtime = {"plan": copy.deepcopy(plan), "running": True, "managed": True, "failure": None}
    events = []

    def persist():
        plan_path.write_text(json.dumps(runtime["plan"], separators=(",", ":")), encoding="utf-8")

    def status(*_args):
        return {
            "ok": True, "managed": runtime["managed"], "running": runtime["running"],
            "plan": copy.deepcopy(runtime["plan"]), "planDigest": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
            "modelStoreWindowsPath": plan["ModelsDir"], "planPathWindows": r"C:\fixture\portal-runtime\runtime.json",
            "observation": {"status": "verified", "modelId": runtime["plan"]["GgufFile"],
                            "contextLength": runtime["plan"]["ContextSize"]} if runtime["running"] else None,
        }

    persist()

    def activate(_install, _env, gguf, context, digest):
        assert pixel["pending"] is True, "Portal must hold before Windows process mutation"
        assert digest == status()["planDigest"]
        events.append("windows-activate")
        if runtime["failure"] == "stopped-before-write":
            runtime["running"] = False
            raise host._wsl_runtime.BridgeError("fixture stopped before plan publication")
        runtime["plan"].update(GgufFile=gguf, ContextSize=context)
        persist()
        if runtime["failure"] in {"load", "unknown"}:
            runtime["running"] = False
            response = {"newPlanDigest": status()["planDigest"]} if runtime["failure"] == "load" else {}
            raise host._wsl_runtime.BridgeError("fixture model load failed", response=response)
        runtime["running"] = True
        return status()

    def restore(_install, _env, snapshot, digest):
        assert pixel["pending"] is True
        assert digest == status()["planDigest"]
        events.append("windows-restore")
        runtime["plan"] = copy.deepcopy(snapshot)
        runtime["running"] = True
        persist()
        return status()

    def stop(_install, _env, digest):
        assert pixel["pending"] is True
        assert digest == status()["planDigest"]
        events.append("windows-stop")
        runtime["running"] = False
        return status()

    def start(_install, _env, digest):
        assert digest == status()["planDigest"]
        events.append("windows-start")
        runtime["running"] = True
        return status()

    def control(operation, request=None, *, config):
        events.append(operation)
        if operation == "model-begin":
            pixel.update(status="held", pending=True, transactionId=request["transactionId"])
        elif operation == "model-apply":
            assert runtime["running"] is True
            pixel.update(status="applied", contract=copy.deepcopy(request["target"]))
        elif operation == "model-finish":
            assert runtime["running"] is True
            pixel.update(status="completed", pending=False, outcome=request["outcome"])
            if request["outcome"] == "rollback":
                pixel["contract"] = copy.deepcopy(previous)
        return copy.deepcopy(pixel)

    def readiness(_env, *args, **kwargs):
        if not runtime["running"] or kwargs.get("gguf_file") != runtime["plan"]["GgufFile"]:
            return False
        if kwargs.get("return_proof"):
            return {"identity": runtime["plan"]["GgufFile"], "contextLength": 65536,
                    "contextVerified": True, "verifiedAt": "2026-09-26T00:00:00+00:00"}
        if kwargs.get("return_identity"):
            return runtime["plan"]["GgufFile"]
        return True

    monkeypatch.setattr(host, "INSTALL_DIR", install)
    monkeypatch.setattr(host, "AGENT_API_KEY", "test-agent-key")
    monkeypatch.setattr(host, "_switchboard_initial_verify_cancel", threading.Event())
    monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
    # The real transport-key read (with its one-release legacy name); only
    # the WSL kernel check is the fixture's.
    monkeypatch.setattr(host._wsl_runtime, "candidate", lambda env: host._wsl_runtime.env_value(
        env, host._wsl_runtime.TRANSPORT_KEY)[1] == "model-router")
    for name, function in {"status": status, "activate": activate, "restore": restore, "stop": stop, "start": start}.items():
        monkeypatch.setattr(host._wsl_runtime, name, function)
    monkeypatch.setattr(host._wsl_runtime, "model_store", lambda *_args: models)
    monkeypatch.setattr(host._wsl_runtime, "plan_path", lambda *_args: plan_path)
    monkeypatch.setattr(host, "_runtime_model_control", control)
    monkeypatch.setattr(host, "_wait_for_model_readiness", readiness)
    monkeypatch.setattr(host, "_prove_pixel_model_contract", lambda _env, contract:
                        runtime["running"] and contract["model"] == runtime["plan"]["GgufFile"])
    monkeypatch.setattr(host, "_container_exists", lambda _name: False)
    monkeypatch.setattr(host, "_container_running", lambda _name: False)
    monkeypatch.setattr(host, "_capture_container_state", lambda _name: {"exists": False, "running": False})
    monkeypatch.setattr(host, "_capture_managed_opencode_state", lambda: {"system": "Linux", "active": False})
    monkeypatch.setattr(host, "_opencode_installed", lambda: False)
    monkeypatch.setattr(host, "_opencode_config_paths", lambda: (tmp_path / "opencode.json", tmp_path / "config.json"))
    monkeypatch.setattr(host, "_ods_managed_pixel_identity", lambda: None)
    monkeypatch.setattr(host, "_verify_hermes_dashboard_ready", lambda: None)
    monkeypatch.setattr(host.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(host, "_compose_restart_llama_server", lambda *_args: pytest.fail("WSL Windows runtime cannot restart a Linux inference service"))
    monkeypatch.setattr(host, "_recreate_llama_server", lambda *_args, **_kwargs: pytest.fail("WSL Windows runtime cannot recreate Linux inference"))
    monkeypatch.setattr(host, "_restart_windows_native_llama_server", lambda *_args: pytest.fail("WSL cannot use native Windows host dispatch"))
    monkeypatch.setattr(host, "_reconcile_ods_managed_pixel_model", lambda *_args, **_kwargs: pytest.fail("must use held Pixel transaction"))
    return dict(install=install, env=env_path, original=env_path.read_bytes(), plan_path=plan_path,
                runtime=runtime, pixel=pixel, events=events, status=status, models=models, registration=registration)


def test_managed_activation_persists_windows_plan_without_linux_runtime_restart(managed):
    handler = fixtures._ResponseHandler()
    host.AgentHandler._do_model_activate(handler, "target-model", requested_context_length=65536)
    assert handler.response_code == 200, handler.parse_response()
    assert managed["runtime"]["plan"]["GgufFile"] == "new-model.gguf"
    assert managed["pixel"]["outcome"] == "commit"
    assert managed["events"].index("model-begin") < managed["events"].index("windows-activate") < managed["events"].index("model-apply")
    persisted = host.load_env(managed["env"])
    assert persisted["ODS_HOST_LLM_TRANSPORT"] == "model-router"
    assert persisted["GGUF_FILE"] == "new-model.gguf"
    assert "LEMONADE_MODEL" not in persisted
    assert managed["pixel"]["contract"]["model"] == "new-model.gguf"
    journal = host._read_pixel_model_journal()
    assert journal["phase"] == "completed"
    assert journal["after"]["windows-runtime-plan"] == managed["status"]()["planDigest"]


def test_unmigrated_portal_env_keeps_driving_the_bridge_for_one_release(managed):
    # Before the installer's .env migration runs, the Portal names its
    # transport and origins with the pre-round-F keys (compatibility reads).
    text = managed["env"].read_text()
    for current, legacy in (("ODS_HOST_LLM_TRANSPORT", "LEMONADE_HOST_TRANSPORT"),
                            ("NATIVE_LLM_BASE_URL", "LEMONADE_BASE_URL"),
                            ("NATIVE_LLM_CONTAINER_BASE_URL", "LEMONADE_CONTAINER_BASE_URL")):
        text = text.replace(current + "=", legacy + "=")
    managed["env"].write_text(text.replace("ODS_MODE=local", "ODS_MODE=lemonade"))
    assert host._model_download_directory() == managed["models"]
    handler = fixtures._ResponseHandler()
    host.AgentHandler._do_model_activate(handler, "target-model", requested_context_length=65536)
    assert handler.response_code == 200, handler.parse_response()
    assert managed["runtime"]["plan"]["GgufFile"] == "new-model.gguf"
    assert "windows-activate" in managed["events"]
    persisted = host.load_env(managed["env"])
    # The agent does not rename keys; the installer migration owns .env.
    assert persisted["LEMONADE_HOST_TRANSPORT"] == "model-router"
    assert persisted["GGUF_FILE"] == "new-model.gguf"


def test_failed_windows_load_restores_previous_plan_with_returned_cas(managed):
    managed["runtime"]["failure"] = "load"
    handler = fixtures._ResponseHandler()
    host.AgentHandler._do_model_activate(handler, "target-model", requested_context_length=65536)
    assert handler.response_code == 500, handler.parse_response()
    assert handler.parse_response()["rolled_back"] is True
    assert managed["events"].count("windows-activate") == 1
    assert managed["events"].count("windows-restore") == 1
    assert managed["env"].read_bytes() == managed["original"]
    assert managed["runtime"]["plan"]["GgufFile"] == "old-model.gguf"
    assert managed["pixel"]["outcome"] == "rollback"


def test_unknown_windows_outcome_keeps_gate_held_without_adopting_fresh_digest(managed):
    managed["runtime"]["failure"] = "unknown"
    handler = fixtures._ResponseHandler()
    host.AgentHandler._do_model_activate(handler, "target-model", requested_context_length=65536)
    assert handler.response_code == 500, handler.parse_response()
    assert handler.parse_response()["pending"] is True
    assert managed["pixel"]["pending"] is True
    assert managed["runtime"]["plan"]["GgufFile"] == "new-model.gguf"
    assert "windows-restore" not in managed["events"]
    assert "model-finish" not in managed["events"]


def test_failure_after_stop_before_publication_restarts_only_original_cas_plan(managed):
    original_digest = managed["status"]()["planDigest"]
    managed["runtime"]["failure"] = "stopped-before-write"
    handler = fixtures._ResponseHandler()
    host.AgentHandler._do_model_activate(handler, "target-model", requested_context_length=65536)
    assert handler.response_code == 500, handler.parse_response()
    assert handler.parse_response()["rolled_back"] is True
    assert managed["status"]()["planDigest"] == original_digest
    assert managed["events"].count("windows-start") == 1
    assert "windows-restore" not in managed["events"]
    assert managed["pixel"]["outcome"] == "rollback"


def test_stop_holds_portal_until_start_proves_and_recovers_same_plan(managed):
    digest = managed["status"]()["planDigest"]
    stopped = fixtures._ResponseHandler(request_body={})
    host.AgentHandler._handle_model_runtime(stopped, "stop")
    assert stopped.response_code == 200, stopped.parse_response()
    assert managed["pixel"]["pending"] is True
    assert managed["runtime"]["running"] is False
    assert host._read_pixel_model_journal()["before"]["windows-runtime-plan"] == digest
    started = fixtures._ResponseHandler(request_body={})
    host.AgentHandler._handle_model_runtime(started, "start")
    assert started.response_code == 200, started.parse_response()
    assert managed["pixel"]["pending"] is False
    assert managed["runtime"]["running"] is True
    assert managed["status"]()["planDigest"] == digest


@pytest.mark.parametrize("operation", ["stop", "start"])
def test_runtime_control_rejects_lost_windows_ownership_before_any_gate_or_task_change(managed, operation):
    managed["runtime"]["managed"] = False
    handler = fixtures._ResponseHandler(request_body={})
    host.AgentHandler._handle_model_runtime(handler, operation)
    assert handler.response_code == 409
    assert managed["events"] == []
    assert managed["pixel"]["pending"] is False


def test_activation_rejects_lost_windows_ownership_before_mutation(managed):
    managed["runtime"]["managed"] = False
    handler = fixtures._ResponseHandler()
    host.AgentHandler._do_model_activate(handler, "target-model", requested_context_length=65536)
    assert handler.response_code == 409
    assert managed["events"] == []
    assert managed["env"].read_bytes() == managed["original"]


def test_start_refuses_plan_drift_while_intentionally_stopped(managed):
    stopped = fixtures._ResponseHandler(request_body={})
    host.AgentHandler._handle_model_runtime(stopped, "stop")
    assert stopped.response_code == 200
    managed["plan_path"].write_text(managed["plan_path"].read_text() + " ")
    started = fixtures._ResponseHandler(request_body={})
    host.AgentHandler._handle_model_runtime(started, "start")
    assert started.response_code == 409
    assert managed["pixel"]["pending"] is True
    assert "windows-start" not in managed["events"]


@pytest.mark.parametrize("field,value", [("GgufFile", "new-model.gguf"), ("ContextSize", 8192)])
def test_start_without_pending_journal_cannot_claim_success_for_another_startup_model(managed, field, value):
    managed["runtime"]["plan"][field] = value
    managed["runtime"]["running"] = False
    managed["plan_path"].write_text(json.dumps(managed["runtime"]["plan"]))
    handler = fixtures._ResponseHandler(request_body={})
    host.AgentHandler._handle_model_runtime(handler, "start")
    assert handler.response_code == 409, handler.parse_response()
    assert "windows-start" not in managed["events"] or managed["pixel"]["pending"] is True


def test_start_after_external_exit_holds_and_recovers_portal_before_success(managed):
    managed["runtime"]["running"] = False
    handler = fixtures._ResponseHandler(request_body={})
    host.AgentHandler._handle_model_runtime(handler, "start")
    assert handler.response_code == 200, handler.parse_response()
    events = managed["events"]
    assert events.index("model-begin") < events.index("windows-start") < events.index("model-finish")
    assert managed["pixel"]["pending"] is False


@pytest.mark.parametrize("proved", [False, True])
def test_start_without_pixel_still_requires_inference_route_proof(managed, monkeypatch, proved):
    managed["env"].write_text(managed["env"].read_text().replace("PIXEL_OPENWEBUI_KEY=configured\n", ""))
    managed["runtime"]["running"] = False
    proofs = []

    def prove(_env, contract):
        proofs.append(contract)
        return proved

    monkeypatch.setattr(host, "_prove_pixel_model_contract", prove)
    handler = fixtures._ResponseHandler(request_body={})
    host.AgentHandler._handle_model_runtime(handler, "start")
    assert handler.response_code == (200 if proved else 409), handler.parse_response()
    assert proofs == [{"model": "old-model.gguf", "contextLength": 65536}]
    assert "model-begin" not in managed["events"]


def test_activation_rejects_model_outside_bound_windows_store(managed):
    (managed["models"] / "new-model.gguf").rename(managed["install"] / "data/models/new-model.gguf")
    handler = fixtures._ResponseHandler()
    host.AgentHandler._do_model_activate(handler, "target-model", requested_context_length=65536)
    assert handler.response_code == 409, handler.parse_response()
    assert managed["events"] == []
    assert managed["env"].read_bytes() == managed["original"]


@pytest.mark.parametrize("ambiguous", [False, True])
def test_delete_does_not_fall_back_to_default_for_missing_or_ambiguous_model(managed, ambiguous):
    default = managed["install"] / "data/models/new-model.gguf"
    default.write_bytes(b"different default model")
    requested = "new-model.gguf" if ambiguous else "missing-model.gguf"
    assert host._installed_model_file(requested) is None
    windows = managed["models"] / "new-model.gguf"
    before = windows.read_bytes()
    handler = fixtures._ResponseHandler(request_body={"gguf_file": requested})

    host.AgentHandler._handle_model_delete(handler)

    assert handler.response_code == 409, handler.parse_response()
    assert handler.parse_response()["code"] == "model_artifact_unavailable"
    assert default.read_bytes() == b"different default model"
    assert windows.read_bytes() == before
    assert managed["events"] == []


def test_delete_allows_only_inactive_model_in_proven_windows_store(managed, monkeypatch):
    target = managed['models'] / 'new-model.gguf'
    monkeypatch.setattr(host, '_live_runtime_has_model', lambda *_args: False)
    handler = fixtures._ResponseHandler(request_body={'gguf_file': target.name})
    host.AgentHandler._handle_model_delete(handler)
    assert handler.response_code == 200, handler.parse_response()
    assert not target.exists()
    assert (managed['models'] / 'old-model.gguf').exists()


def test_delete_preserves_windows_model_when_runtime_ownership_is_lost(managed, monkeypatch):
    target = managed['models'] / 'new-model.gguf'
    managed['runtime']['managed'] = False
    monkeypatch.setattr(host, '_live_runtime_has_model', lambda *_args: pytest.fail('ownership denied first'))
    handler = fixtures._ResponseHandler(request_body={'gguf_file': target.name})
    host.AgentHandler._handle_model_delete(handler)
    assert handler.response_code != 200
    assert target.exists()


def test_model_store_and_plan_journal_are_bound_to_private_registration(managed):
    assert host._model_download_directory() == managed["models"]
    assert host._pixel_model_config_paths()["windows-runtime-plan"] == managed["plan_path"]
    assert host._pixel_model_config_digests()["windows-runtime-plan"] == managed["status"]()["planDigest"]
    if host.os.name != "nt":
        managed["registration"].chmod(0o644)
        with pytest.raises(RuntimeError, match="Unsafe Windows runtime registration"):
            host._model_download_directory()


def test_missing_runtime_registration_never_implicitly_adopts_model_store(managed):
    managed["registration"].unlink()
    with pytest.raises(RuntimeError, match="register its managed model store"):
        host._model_download_directory()
    assert managed["events"] == []
