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
        self.held_contract = None
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
            # The native hold saves the exact contract a rollback restores.
            self.held_contract = copy.deepcopy(state["contract"])
            state.update(status="held", pending=True, transactionId=request["transactionId"], outcome=None)
        elif operation == "model-apply":
            state.update(status="applied", contract=copy.deepcopy(request["target"]))
        elif operation == "model-finish":
            if request["outcome"] == "commit" and state["status"] != "applied":
                raise host._PixelModelTransactionRejected("model-apply-unverified")
            if request["outcome"] == "rollback":
                # A hold seeded without model-begin restores the module default.
                state["contract"] = copy.deepcopy(PREVIOUS if self.held_contract is None else self.held_contract)
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


def _recover_http():
    handler = fixtures._ResponseHandler(request_body={})
    host.AgentHandler._handle_model_recover(handler)
    return handler.response_code, handler.parse_response()


def _status_http():
    handler = fixtures._ResponseHandler(request_body={})
    host.AgentHandler._handle_model_recovery_status(handler)
    return handler.response_code, handler.parse_response()


def _offer():
    return host._public_pixel_model_restore_offer(host._read_pixel_model_journal())


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

    # Repair is idempotent: while the relay still cannot answer it loads
    # nothing and stays pending; the owner simply invokes it again later.
    # The rollback evidence is exact, but its finish gets no answer and
    # neither does the status read behind it: that is a transport outage,
    # reported as unavailable rather than as missing proof.
    unavailable_before = len(coordinator.unavailable)
    assert _repair() == {"pending": True, "phase": "rolling-back",
                         "transactionId": journal["transactionId"], "reason": "model-recovery-unavailable"}
    assert coordinator.unavailable[unavailable_before:] == ["model-status", "model-finish", "model-status"]
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
    assert _offer() == {"model": "old-model.gguf", "contextLength": 65536}

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
    code, body = _recover_http()
    assert code == 409 and body["reason"] == "model-recovery-proof-required"
    assert body["restore"] == {"model": "old-model.gguf", "contextLength": 65536}

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
    delivered = list(coordinator.delivered)

    code, payload = _restore(requested)

    assert payload["pending"] is True
    if problem == "unreadable":
        assert code == 503 and payload["reason"] == "model-recovery-unavailable"
        assert payload["restore"] == {"model": "old-model.gguf", "contextLength": 65536}
        # One status read answered nothing; it is not repeated as an
        # ownership check, and nothing else is attempted.
        assert coordinator.unavailable == ["model-status"]
    else:
        assert code == 409 and payload["reason"] == "model-restore-unavailable"
    if problem == "stale-id":
        # A transaction ID the journal does not hold never reaches recovery
        # or the coordinator at all.
        assert coordinator.delivered == delivered and coordinator.unavailable == []
    assert inference.restarts == restarts and inference.gguf == "new-model.gguf"
    assert "model-finish" not in coordinator.delivered


@pytest.mark.parametrize("phase", ["prepared", "committing"])
def test_restore_is_not_offered_where_it_could_contradict_the_native_outcome(world, phase):
    transaction = _legacy_stuck_state(world)
    transaction._save(phase)
    assert host._pixel_model_restore_offer(host._read_pixel_model_journal()) is None


def test_restore_is_not_offered_for_remote_or_missing_previous_model(world):
    transaction = _legacy_stuck_state(world)
    transaction.previous = {**PREVIOUS, "routeFingerprint": "d" * 64}
    transaction._save("held")
    assert host._pixel_model_restore_offer(host._read_pixel_model_journal()) is None
    transaction.previous = copy.deepcopy(PREVIOUS)
    transaction._save("held")
    (world["install"] / "data" / "models" / "old-model.gguf").unlink()
    assert host._pixel_model_restore_offer(host._read_pixel_model_journal()) is None


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


TARGET = {"model": "new-model.gguf", "contextLength": 65536, "maxTokens": 8192, "reasoning": False}
REMOTE_PREVIOUS = {"model": "remote-model", "contextLength": 131072, "maxTokens": 8192, "reasoning": False,
                   "routeFingerprint": "d" * 64}


@pytest.mark.parametrize("remote_route_restored", [True, False])
def test_repair_never_commits_a_local_target_over_an_exactly_restored_remote_route(
        world, monkeypatch, remote_route_restored):
    """Turning a remote provider off (remote -> local) must not end as a silent local commit.

    The deactivation applied the local contract natively, then failed late.
    Its host rollback restored every captured file (cloud mode again), and
    only the final remote proof or the LiteLLM restore failed. llama-server
    still serves the local model, so the target answers a live proof, but
    .env, cloud.yaml and LiteLLM route remote. Exact rollback evidence wins.
    """
    coordinator, inference = world["coordinator"], world["inference"]
    coordinator.state["contract"] = copy.deepcopy(REMOTE_PREVIOUS)
    transaction = host._begin_pixel_model_transaction(host.load_env(world["env"]))
    assert transaction.previous == REMOTE_PREVIOUS
    env = world["env"]
    cloud = env.read_bytes()
    env.write_text(env.read_text(encoding="utf-8") + "ODS_MODE=local\n", encoding="utf-8")
    local_target = copy.deepcopy(PREVIOUS)  # the local model llama-server serves throughout
    transaction.apply(local_target)
    env.write_bytes(cloud)  # the deactivation's rollback restored every captured file
    journal = host._read_pixel_model_journal()
    assert journal["phase"] == "applied" and host._pixel_model_config_digests() == journal["before"]
    assert host._prove_pixel_model_contract(host.load_env(env), local_target)  # the target still answers

    remote_route = {"route": "restored-remote-provider"}
    monkeypatch.setattr(host, "_read_remote_provider_route_state_for_update", lambda: remote_route)
    monkeypatch.setattr(host, "_remote_provider_runtime_contract",
                        lambda route: copy.deepcopy(REMOTE_PREVIOUS) if route is remote_route else {})
    litellm = []

    def verify_litellm_route(_config, *, model="default"):
        litellm.append(model)
        if not remote_route_restored:
            raise RuntimeError("LiteLLM did not serve a completion through the active ods/current route")

    monkeypatch.setattr(host, "_verify_litellm_route", verify_litellm_route)

    result = _repair()

    if remote_route_restored:
        assert result == {"pending": False, "phase": "completed",
                          "transactionId": transaction.id, "outcome": "rollback"}
        assert coordinator.state["contract"] == REMOTE_PREVIOUS
        assert coordinator.state["outcome"] == "rollback"
        assert host._read_pixel_model_journal()["outcome"] == "rollback"
    else:
        # Neither outcome is proved: stay pending; never publish the local contract.
        assert result == {"pending": True, "phase": "applied", "transactionId": transaction.id,
                          "reason": "model-recovery-proof-required"}
        assert "model-finish" not in coordinator.delivered
        assert coordinator.state["status"] == "applied"
    assert litellm == ["ods/current"]  # only the previous (remote) contract was proved
    assert inference.restarts == []


def test_relay_outage_is_unavailable_and_a_later_restore_reports_the_kept_target(world):
    """A transport failure is not missing proof, and a commit is never shown as a restore."""
    coordinator, inference = world["coordinator"], world["inference"]
    transaction = _legacy_stuck_state(world)
    transaction.apply(copy.deepcopy(TARGET))
    coordinator.down = True

    code, body = _recover_http()

    assert code == 503 and body["pending"] is True and body["phase"] == "applied"
    assert body["reason"] == "model-recovery-unavailable"
    assert coordinator.unavailable == ["model-status"]  # one read, never a retry loop
    assert "model-finish" not in coordinator.delivered

    # The relay answers again. Recovery inside the restore proves the switch
    # finished: the new model is kept and the answer says so.
    coordinator.down = False
    code, payload = _restore(transaction.id)

    assert code == 200, payload
    assert payload == {"pending": False, "phase": "completed", "transactionId": transaction.id,
                       "outcome": "commit", "reason": "model-restore-target-kept"}
    assert inference.gguf == "new-model.gguf" and inference.restarts == []
    assert coordinator.state["contract"] == TARGET and coordinator.state["outcome"] == "commit"


def test_restore_finishes_nothing_unless_the_reloaded_runtime_is_the_exact_previous_identity(
        world, monkeypatch):
    coordinator, inference = world["coordinator"], world["inference"]
    # The hold saved an identity that names the same file name from another
    # store; reloading the catalog model cannot prove that exact identity.
    elsewhere = {**PREVIOUS, "model": "other-store/old-model.gguf"}
    coordinator.state["contract"] = copy.deepcopy(elsewhere)
    transaction = _legacy_stuck_state(world)
    assert transaction.previous == elsewhere
    assert _offer() == {"model": "other-store/old-model.gguf", "contextLength": 65536}
    reconciled = []
    monkeypatch.setattr(host, "_reconcile_ods_managed_pixel_model",
                        lambda *args, **kwargs: reconciled.append(args) or "reconciled")

    code, payload = _restore(transaction.id)

    assert code == 409 and payload["reason"] == "model-restore-failed", payload
    assert "does not match the interrupted switch's previous model contract" in payload["detail"]
    # Loaded, refused before any finish, then rolled back to what it served.
    assert inference.restarts == ["old-model.gguf", "new-model.gguf"] and inference.gguf == "new-model.gguf"
    assert "model-finish" not in coordinator.delivered and coordinator.state["status"] == "held"
    # A restore never reconciles Pixel directly, not even in its own rollback:
    # only finishing the native hold may restore the saved contract.
    assert reconciled == []
    assert host._read_pixel_model_journal()["phase"] == "held"


def test_restore_rechecks_ownership_immediately_before_its_first_write(world, monkeypatch):
    coordinator, inference = world["coordinator"], world["inference"]
    transaction = _legacy_stuck_state(world)
    env_before = world["env"].read_text(encoding="utf-8")
    capture = host._capture_container_state

    def another_owner_takes_the_hold(name):
        # Runs inside the restore's activation, after the restore's own
        # ownership check and before the activation writes anything.
        coordinator.state["transactionId"] = "f" * 64
        return capture(name)

    monkeypatch.setattr(host, "_capture_container_state", another_owner_takes_the_hold)

    code, payload = _restore(transaction.id)

    assert code == 409 and payload["reason"] == "model-restore-failed", payload
    assert "ownership changed" in payload["detail"]
    assert inference.restarts == [] and inference.gguf == "new-model.gguf"
    assert world["env"].read_text(encoding="utf-8") == env_before
    assert "model-finish" not in coordinator.delivered


def test_restore_reloads_the_journal_context_not_the_catalog_default(world):
    library_path = world["install"] / "config" / "model-library.json"
    library = json.loads(library_path.read_text(encoding="utf-8"))
    for item in library["models"]:
        if item["id"] == "previous-model":
            item["context_length"] = 32768  # differs from the context the hold saved
    library_path.write_text(json.dumps(library), encoding="utf-8")
    coordinator, inference = world["coordinator"], world["inference"]
    transaction = _legacy_stuck_state(world)

    code, payload = _restore(transaction.id)

    assert code == 200 and payload["outcome"] == "rollback", payload
    assert inference.gguf == "old-model.gguf" and inference.context == 65536
    assert "CTX_SIZE=65536" in world["env"].read_text(encoding="utf-8")
    assert coordinator.state["contract"] == PREVIOUS


def test_no_restore_offer_is_always_logged_for_the_owner(world, caplog):
    _legacy_stuck_state(world)
    caplog.set_level("INFO")
    imports = world["install"] / "data" / "model-imports.json"
    imports.write_text("{not json", encoding="utf-8")

    assert _offer() is None
    assert "the model catalog is unreadable" in caplog.text

    imports.unlink()
    (world["install"] / "data" / "models" / "old-model.gguf").unlink()
    caplog.clear()
    assert _offer() is None
    assert "old-model.gguf is no longer installed" in caplog.text


@pytest.mark.parametrize("route", ["status", "recover", "restore"])
def test_unreadable_journal_is_a_logged_unavailable_answer(world, caplog, route):
    coordinator, inference = world["coordinator"], world["inference"]
    transaction = _legacy_stuck_state(world)
    host._pixel_model_journal_path().write_text("{", encoding="utf-8")
    delivered = list(coordinator.delivered)
    caplog.set_level("WARNING")

    code, body = {"status": _status_http, "recover": _recover_http,
                  "restore": lambda: _restore(transaction.id)}[route]()

    assert code == 503 and body["pending"] is True and body["phase"] == "unavailable"
    assert "restore" not in body
    assert any(record.levelname == "WARNING" and record.exc_info for record in caplog.records)
    assert coordinator.delivered == delivered and inference.restarts == []
    # The lifecycle lock was released.
    acquired, _active = host._begin_model_lifecycle("model_download", "probe")
    assert acquired
    host._end_model_lifecycle("model_download")
