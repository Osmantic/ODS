"""A model switch interrupted by docker CLI timeouts must stay owner-recoverable.

Reproduces the fleet incident on an overloaded host (load ~198): a switch
loaded the new model, a post-check `docker inspect` timed out, and the
rollback could not read native status because the relay's own docker calls
timed out too. The previous product aborted the rollback before restoring
anything, leaving the new model loaded under a native hold on the previous
contract; Repair then reported model-recovery-proof-required forever.
"""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

import test_model_activate as fixtures

host = fixtures._mod
_relay_spec = importlib.util.spec_from_file_location(
    "pixel_access_relay_held_recovery",
    Path(fixtures._agent_path).parent / "pixel_access_relay.py",
)
relay = importlib.util.module_from_spec(_relay_spec)
_relay_spec.loader.exec_module(relay)

PREVIOUS = {"model": "old-model.gguf", "contextLength": 65536, "maxTokens": 8192, "reasoning": False}
DOCKER_TIMEOUT = (
    "Could not inspect health for ods-hermes: Command '['docker', 'inspect', '--type', "
    "'container', '--format', '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}', "
    "'ods-hermes']' timed out after 15 seconds"
)


class Coordinator:
    """Native model coordinator fake with the public model-status contract."""

    def __init__(self):
        self.state = dict(schemaVersion=1, status="ready", revision="a" * 64,
                          contract=copy.deepcopy(PREVIOUS), pending=False,
                          transactionId=None, outcome=None)
        self.delivered = []
        self.unavailable = []
        self.down = False

    def __call__(self, operation, request=None, *, config):
        if self.down:
            self.unavailable.append(operation)
            # What the relay raises when its docker inspect/exec time out.
            raise relay.AccessRelayError("agent-access-runtime-unavailable")
        self.delivered.append(operation)
        state = self.state
        if operation == "model-status":
            return dict(state)
        if request["transactionId"] != state["transactionId"] and operation != "model-begin":
            raise host._PixelModelTransactionRejected("model-transaction-conflict")
        if operation == "model-begin":
            assert not state["pending"]
            state.update(status="held", pending=True, transactionId=request["transactionId"], outcome=None)
        elif operation == "model-apply":
            state.update(status="applied", contract=copy.deepcopy(request["target"]))
        elif operation == "model-finish":
            if request["outcome"] == "commit" and state["status"] != "applied":
                raise host._PixelModelTransactionRejected("model-apply-unverified")
            if request["outcome"] == "rollback":
                state["contract"] = copy.deepcopy(PREVIOUS)
            state.update(status="completed", pending=False, outcome=request["outcome"])
        return dict(state)


class Inference:
    """llama-server whose loaded model changes only through a runtime restart."""

    def __init__(self):
        self.gguf = "old-model.gguf"
        self.context = 65536
        self.restarts = []
        self.fail_restart_to = None

    def restart(self, env):
        self.restarts.append(env["GGUF_FILE"])
        if env["GGUF_FILE"] == self.fail_restart_to:
            self.fail_restart_to = None
            raise RuntimeError("docker compose up llama-server timed out after 120 seconds")
        self.gguf = env["GGUF_FILE"]
        self.context = int(env["CTX_SIZE"])

    def readiness(self, *_args, **kwargs):
        if kwargs.get("gguf_file") != self.gguf:
            return False
        if kwargs.get("return_proof"):
            return {"identity": self.gguf, "contextLength": self.context,
                    "contextVerified": True, "verifiedAt": "2026-09-25T20:17:40Z"}
        if kwargs.get("return_identity"):
            return self.gguf
        return True


@pytest.fixture(autouse=True)
def _isolated_activation(monkeypatch, tmp_path):
    config_dir = tmp_path / "isolated-home" / ".config" / "opencode"
    monkeypatch.setattr(host, "_opencode_config_paths",
                        lambda: (config_dir / "opencode.json", config_dir / "config.json"))
    monkeypatch.setattr(host, "_lemonade_recipe_options_path", lambda: tmp_path / "lemonade-recipe.json")
    monkeypatch.setattr(host, "_capture_container_state", lambda _name: {"exists": False, "running": False})
    monkeypatch.setattr(host, "_wait_for_container_health", lambda _name: None)
    monkeypatch.setattr(host, "_capture_managed_opencode_state", lambda: {"system": "Linux", "active": False})
    monkeypatch.setattr(host, "_opencode_installed", lambda: False)
    monkeypatch.setattr(host, "_chat_completion_ready", lambda *_a, **_k: True)
    monkeypatch.setattr(host, "_llama_runtime_context_length", lambda *_a: 131072)
    monkeypatch.setattr(host.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(host, "_container_exists", lambda _name: False)
    monkeypatch.setattr(host, "_container_running", lambda _name: False)
    monkeypatch.setattr(host, "_verify_hermes_dashboard_ready", lambda: None)
    monkeypatch.setattr(host, "_reconcile_ods_managed_pixel_model",
                        lambda *_a, **_k: pytest.fail("a managed switch must use the native transaction"))
    monkeypatch.setattr(host, "AGENT_API_KEY", "test-agent-key")


@pytest.fixture
def world(tmp_path, monkeypatch):
    install, env_path, text, *_ = fixtures._write_model_activation_fixture(tmp_path)
    original = text.replace("CTX_SIZE=2048", "CTX_SIZE=65536") + "PIXEL_OPENWEBUI_KEY=configured\n"
    env_path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(host, "DATA_DIR", install / "data")
    (install / "data" / "models" / "old-model.gguf").write_text("old", encoding="utf-8")
    library = json.loads((install / "config" / "model-library.json").read_text(encoding="utf-8"))
    library["models"].append({
        "id": "previous-model", "gguf_file": "old-model.gguf",
        "gguf_url": "https://example.test/old-model.gguf",
        "gguf_sha256": hashlib.sha256(b"old").hexdigest(),
        "llm_model_name": "old-model", "context_length": 65536,
    })
    (install / "config" / "model-library.json").write_text(json.dumps(library), encoding="utf-8")
    coordinator, inference = Coordinator(), Inference()
    monkeypatch.setattr(host, "INSTALL_DIR", install)
    monkeypatch.setattr(host, "_runtime_model_control", coordinator)
    monkeypatch.setattr(host, "_compose_restart_llama_server", inference.restart)
    monkeypatch.setattr(host, "_wait_for_model_readiness", inference.readiness)
    post_check = {"fail": None}

    def openclaw_post_check(_state):
        failure, post_check["fail"] = post_check["fail"], None
        if failure is not None:
            failure()
            raise RuntimeError(DOCKER_TIMEOUT)
        return False

    monkeypatch.setattr(host, "_recreate_openclaw_if_present", openclaw_post_check)
    return {"install": install, "env": env_path, "original": original, "coordinator": coordinator,
            "inference": inference, "post_check": post_check}


def _activate(model_id, context):
    handler = fixtures._ResponseHandler()
    host.AgentHandler._do_model_activate(handler, model_id, requested_context_length=context)
    return handler.response_code, handler.parse_response()


def _repair():
    return host._recover_pixel_model_transaction(host.load_env(host.INSTALL_DIR / ".env"))


def _restore(transaction_id):
    handler = fixtures._ResponseHandler(request_body={"transactionId": transaction_id})
    host.AgentHandler._handle_model_restore_previous(handler)
    return handler.response_code, handler.parse_response()


def _stuck_after_post_check_and_rollback_timeouts(world):
    """Activation post-check timeout, then the rollback's runtime restart times out."""
    world["inference"].fail_restart_to = "old-model.gguf"
    world["post_check"]["fail"] = lambda: None
    code, payload = _activate("target-model", 65536)
    assert code == 500 and payload["pending"] is True, payload
    assert payload["code"] == "managed_model_recovery_required"
    journal = host._read_pixel_model_journal()
    assert journal["phase"] == "held" and journal["target"] is None
    assert world["inference"].gguf == "new-model.gguf"
    return journal


def test_rollback_with_unreadable_native_status_restores_host_and_repair_finishes(world):
    coordinator, inference = world["coordinator"], world["inference"]

    def overload():
        # From the post-check onwards every relay call times out.
        coordinator.down = True

    world["post_check"]["fail"] = overload
    code, payload = _activate("target-model", 65536)

    assert code == 500 and payload["pending"] is True, payload
    # The rollback no longer aborts on the unreadable ownership proof: the
    # previous files and runtime are restored and proved first.
    assert world["env"].read_text(encoding="utf-8") == world["original"]
    assert inference.restarts == ["new-model.gguf", "old-model.gguf"]
    assert inference.gguf == "old-model.gguf"
    journal = host._read_pixel_model_journal()
    assert journal["phase"] == "rolling-back" and journal["after"] == host._pixel_model_config_digests()
    assert "model-finish" not in coordinator.delivered
    assert coordinator.state["status"] == "held"

    # Repair is idempotent: while the relay still cannot answer it changes
    # nothing and stays pending; the owner simply invokes it again later.
    assert _repair()["pending"] is True
    assert "model-finish" not in coordinator.delivered
    coordinator.down = False
    result = _repair()

    assert result == {"pending": False, "phase": "completed",
                      "transactionId": journal["transactionId"], "outcome": "rollback"}
    assert coordinator.state["status"] == "completed" and coordinator.state["outcome"] == "rollback"
    assert coordinator.delivered.count("model-begin") == 1 and "model-apply" not in coordinator.delivered
    assert inference.restarts == ["new-model.gguf", "old-model.gguf"]  # Repair never loads a model.


def test_rollback_still_refuses_host_restore_when_another_owner_holds_the_gate(world):
    coordinator, inference = world["coordinator"], world["inference"]

    def foreign_owner():
        coordinator.state["transactionId"] = "f" * 64

    world["post_check"]["fail"] = foreign_owner
    code, payload = _activate("target-model", 65536)

    assert code == 500 and payload["pending"] is True and payload["rolled_back"] is False, payload
    assert "ownership changed" in payload["error"]
    assert inference.restarts == ["new-model.gguf"]
    assert "GGUF_FILE=new-model.gguf" in world["env"].read_text(encoding="utf-8")
    assert host._read_pixel_model_journal()["phase"] == "held"


def test_ownership_read_failures_keep_distinct_meanings(world, monkeypatch):
    coordinator = world["coordinator"]
    transaction = host._begin_pixel_model_transaction(host.load_env(world["env"]))
    coordinator.down = True
    with pytest.raises(host._PixelModelOwnershipUnavailable):
        transaction.verify_held()
    coordinator.down = False

    def refused(operation, request=None, *, config):
        raise host._PixelModelTransactionRejected("refused")

    monkeypatch.setattr(host, "_runtime_model_control", refused)
    with pytest.raises(host._PixelModelTransactionUncertain) as refused_error:
        transaction.verify_held()
    assert not isinstance(refused_error.value, host._PixelModelOwnershipUnavailable)
    assert coordinator.unavailable == ["model-status"]  # one read per check, never a loop


def test_held_after_rollback_timeouts_repair_offers_and_restore_releases_previous(world):
    coordinator, inference = world["coordinator"], world["inference"]
    journal = _stuck_after_post_check_and_rollback_timeouts(world)
    # Host files are back to the previous bytes, but the previous model never
    # loaded, so neither outcome is provable by Repair.
    assert host._pixel_model_config_digests() == journal["before"]
    result = _repair()
    assert result["pending"] is True and result["reason"] == "model-recovery-proof-required"
    assert host._public_pixel_model_restore_offer() == {"model": "old-model.gguf", "contextLength": 65536}

    code, payload = _restore(journal["transactionId"])

    assert code == 200, payload
    assert payload == {"pending": False, "phase": "completed",
                       "transactionId": journal["transactionId"], "outcome": "rollback"}
    assert inference.gguf == "old-model.gguf" and inference.context == 65536
    assert coordinator.state["status"] == "completed" and coordinator.state["contract"] == PREVIOUS
    assert coordinator.delivered.count("model-begin") == 1
    assert "model-apply" not in coordinator.delivered
    assert coordinator.delivered.count("model-finish") == 1
    receipt = json.loads((world["install"] / "data" / "model-activation-receipt.json").read_text())
    assert receipt["ggufFile"] == "old-model.gguf" and receipt["modelTransactionId"] == journal["transactionId"]
    # The owner can switch models normally again.
    code, payload = _activate("target-model", 65536)
    assert code == 200, payload
    assert coordinator.state["contract"]["model"] == "new-model.gguf"


def _legacy_stuck_state(world):
    """The exact fleet state: the target stays loaded under a hold on the previous contract."""
    transaction = host._begin_pixel_model_transaction(host.load_env(world["env"]))
    world["env"].write_text(world["original"].replace("old-model", "new-model"), encoding="utf-8")
    (world["install"] / "config" / "llama-server" / "models.ini").write_text(
        "[new-model]\nfilename = new-model.gguf\n", encoding="utf-8")
    world["inference"].gguf = "new-model.gguf"
    return transaction


def test_exact_fleet_state_is_restored_by_the_owner_action(world):
    coordinator, inference = world["coordinator"], world["inference"]
    transaction = _legacy_stuck_state(world)
    assert _repair()["pending"] is True
    handler = fixtures._ResponseHandler(request_body={})
    host.AgentHandler._handle_model_recover(handler)
    assert handler.response_code == 409
    assert handler.parse_response()["restore"] == {"model": "old-model.gguf", "contextLength": 65536}

    code, payload = _restore(transaction.id)

    assert code == 200 and payload["outcome"] == "rollback", payload
    assert inference.gguf == "old-model.gguf"
    assert "GGUF_FILE=old-model.gguf" in world["env"].read_text(encoding="utf-8")
    assert coordinator.delivered.count("model-begin") == 1 and "model-apply" not in coordinator.delivered
    assert host._read_pixel_model_journal()["outcome"] == "rollback"


def test_failed_restore_keeps_the_same_switch_pending_and_offers_retry(world):
    coordinator, inference = world["coordinator"], world["inference"]
    transaction = _legacy_stuck_state(world)
    inference.fail_restart_to = "old-model.gguf"

    code, payload = _restore(transaction.id)

    assert code == 409 and payload["pending"] is True and payload["reason"] == "model-restore-failed", payload
    assert payload["restore"] == {"model": "old-model.gguf", "contextLength": 65536}
    assert "timed out" in payload["detail"]
    # The failed restore rolled the host back to what it served before it.
    assert inference.gguf == "new-model.gguf"
    assert "model-finish" not in coordinator.delivered and coordinator.state["status"] == "held"
    journal = host._read_pixel_model_journal()
    assert journal["transactionId"] == transaction.id and journal["phase"] == "held"

    code, payload = _restore(transaction.id)
    assert code == 200 and payload["outcome"] == "rollback", payload


@pytest.mark.parametrize("problem", ["other-transaction", "stale-id", "changed-contract", "unreadable"])
def test_restore_never_loads_without_proved_ownership(world, problem):
    coordinator, inference = world["coordinator"], world["inference"]
    transaction = _legacy_stuck_state(world)
    requested = transaction.id
    if problem == "other-transaction":
        coordinator.state["transactionId"] = "f" * 64
    elif problem == "stale-id":
        requested = "e" * 64
    elif problem == "changed-contract":
        coordinator.state["contract"] = {**PREVIOUS, "contextLength": 32768}
    else:
        coordinator.down = True
    restarts = list(inference.restarts)

    code, payload = _restore(requested)

    assert payload["pending"] is True
    if problem == "unreadable":
        assert code == 503 and payload["reason"] == "model-recovery-unavailable"
        assert payload["restore"] == {"model": "old-model.gguf", "contextLength": 65536}
    else:
        assert code == 409 and payload["reason"] == "model-restore-unavailable"
    assert inference.restarts == restarts and inference.gguf == "new-model.gguf"
    assert "model-finish" not in coordinator.delivered


@pytest.mark.parametrize("phase", ["prepared", "committing"])
def test_restore_is_not_offered_where_it_could_contradict_the_native_outcome(world, phase):
    transaction = _legacy_stuck_state(world)
    transaction._save(phase)
    assert host._pixel_model_restore_offer() is None


def test_restore_is_not_offered_for_remote_or_missing_previous_model(world):
    transaction = _legacy_stuck_state(world)
    transaction.previous = {**PREVIOUS, "routeFingerprint": "d" * 64}
    transaction._save("held")
    assert host._pixel_model_restore_offer() is None
    transaction.previous = copy.deepcopy(PREVIOUS)
    transaction._save("held")
    (world["install"] / "data" / "models" / "old-model.gguf").unlink()
    assert host._pixel_model_restore_offer() is None


def test_restore_endpoint_requires_owner_auth_and_exact_body(world):
    transaction = _legacy_stuck_state(world)
    denied = fixtures._ResponseHandler(request_body={"transactionId": transaction.id}, api_key="wrong-key")
    host.AgentHandler._handle_model_restore_previous(denied)
    assert denied.response_code == 403
    for body in ({}, {"transactionId": transaction.id, "model": "other"}, {"transactionId": "A" * 64},
                 {"model_id": "target-model"}):
        handler = fixtures._ResponseHandler(request_body=body)
        host.AgentHandler._handle_model_restore_previous(handler)
        assert handler.response_code == 400, body
    assert world["inference"].gguf == "new-model.gguf"


def test_status_route_projects_restore_offer_only_while_pending(world):
    handler = fixtures._ResponseHandler(request_body={})
    host.AgentHandler._handle_model_recovery_status(handler)
    assert handler.parse_response() == {"pending": False, "phase": "idle", "transactionId": None}
    transaction = _legacy_stuck_state(world)
    handler = fixtures._ResponseHandler(request_body={})
    host.AgentHandler._handle_model_recovery_status(handler)
    assert handler.parse_response() == {"pending": True, "phase": "held", "transactionId": transaction.id,
                                        "restore": {"model": "old-model.gguf", "contextLength": 65536}}


def test_repair_commits_an_applied_target_when_every_state_agrees(world):
    coordinator, inference = world["coordinator"], world["inference"]
    target = {"model": "new-model.gguf", "contextLength": 65536, "maxTokens": 8192, "reasoning": False}
    transaction = _legacy_stuck_state(world)
    transaction.apply(target)
    assert host._read_pixel_model_journal()["phase"] == "applied"

    result = _repair()

    assert result["pending"] is False and result["outcome"] == "commit"
    assert coordinator.state["contract"] == target and "model-apply" in coordinator.delivered
    assert coordinator.delivered.count("model-apply") == 1
    assert inference.gguf == "new-model.gguf"


def test_repair_rolls_back_an_applied_target_when_host_restored_previous(world):
    coordinator = world["coordinator"]
    transaction = host._begin_pixel_model_transaction(host.load_env(world["env"]))
    transaction.apply({"model": "new-model.gguf", "contextLength": 65536, "maxTokens": 8192,
                       "reasoning": False})
    # Host files and runtime never left the previous model.
    result = _repair()
    assert result["pending"] is False and result["outcome"] == "rollback"
    assert coordinator.state["contract"] == PREVIOUS


def test_applied_target_without_live_proof_stays_pending(world):
    coordinator, inference = world["coordinator"], world["inference"]
    transaction = _legacy_stuck_state(world)
    transaction.apply({"model": "new-model.gguf", "contextLength": 65536, "maxTokens": 8192,
                       "reasoning": False})
    inference.gguf = "crashed.gguf"
    assert _repair()["pending"] is True
    assert "model-finish" not in coordinator.delivered
