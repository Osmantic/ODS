"""Review-only recovery reduces permission even when ordinary policy is Allowed."""
import copy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import threading

import pytest

if sys.platform == "win32":
    pytest.skip("Unix owner service; run in WSL", allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
import integrations_apps as apps
import integrations_notes as notes
import integrations_service as service_module
from integrations_common import IntegrationError, Store, default_config, digest, now
from integrations_service import Integrations, server_context


def save(service, store, **updates):
    config = store.config()
    settings = {key: config[key] for key in ("notes", "notifications", "mcp", "apps")}
    for section, values in updates.items():
        settings[section].update(values)
    service.save(config["revision"], settings, {})


@pytest.fixture
def world(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Example.md").write_text("Original owned fixture.\n")
    store = Store(tmp_path / "state")
    config = default_config()
    config["notes"].update(enabled=True, vaultPath=str(vault), write="ask")
    config["notifications"].update(enabled=True, url="https://ntfy.example", topic="owned",
                                   notifyApprovals=True)
    for app in config["apps"]:
        config["apps"][app].update(enabled=True, url=f"https://{app}.example")
    config["apps"]["news"]["username"] = "fixture-owner"
    # A saved discovered catalog is data. The fixture never starts that server.
    server = {"id": "opaque", "name": "Opaque", "kind": "stdio", "preset": "", "enabled": True,
              "url": "", "command": "false", "workingFolder": "", "envNames": [],
              "tools": [{"name": "read_hint", "description": "Untrusted read-only hint",
                         "annotations": {"readOnlyHint": True}, "inputSchema": {"type": "object"}, "policy": "ask"}],
              "discoveredAt": now()}
    secrets = {"ntfyPassword": "fixture-only", "mcp": {}, "mcpEnv": {},
               "apps": {app: f"{app}-fixture-key" for app in config["apps"]}}
    server["checkedBinding"] = digest(server_context(server, secrets))
    config["mcp"]["servers"] = [server]
    store.save_config(config)
    store.save_secrets(secrets)
    service = Integrations(store)
    save(service, store)
    effects = []

    def forbidden(name):
        def fail(*_args, **_kwargs):
            effects.append(name)
            raise AssertionError(f"Review-only reached actuator {name}")
        return fail

    for target, attr in ((notes, "write"), (apps, "add_watch"), (apps, "remove_watch"),
                         (service_module, "send_notification"), (service, "_session"),
                         (service, "_notify_approval"), (service, "_watch_change"),
                         (store, "save_watch_state"), (service, "task_finished"),
                         (service, "digest_control")):
        monkeypatch.setattr(target, attr, forbidden(attr))
    return service, store, vault, effects


def call(service, action="notes-write", **fields):
    return service.handle("agent", {"schemaVersion": 1, "action": action,
        "recoveryReviewOnly": True, "path": "Example.md", "text": "Current owned text.", **fields})


def mcp(service, server="opaque", tool="read_hint", **arguments):
    return call(service, "mcp-call", server=server, tool=tool, arguments=arguments)


def decide(service, approval, choice="approve"):
    service.handle("owner", {"schemaVersion": 1, "action": "decide",
                            "approvalId": approval["approvalId"], "decision": choice})


def assert_no_effect(world):
    _service, _store, vault, effects = world
    assert effects == []
    assert (vault / "Example.md").read_text() == "Original owned fixture.\n"


def test_ask_proposal_reuses_pending_and_never_pings_or_changes_config(world):
    service, store, _vault, _effects = world
    before = (store.root / "config.json").read_bytes()
    first = call(service)
    assert first["status"] == "approval-required"
    assert first["recoveryReviewOnly"] is True
    assert first["actionExecuted"] is first["approvalConsumed"] is False
    assert call(service)["approvalId"] == first["approvalId"]
    assert len(store.approvals()) == 1
    assert (store.root / "config.json").read_bytes() == before
    assert_no_effect(world)


@pytest.mark.parametrize("policy", ["allow", "off"])
def test_allowed_and_off_refuse_before_any_note_mutator_or_new_request(world, policy):
    service, store, _vault, _effects = world
    save(service, store, notes={"write": policy})
    with pytest.raises(IntegrationError) as refused:
        call(service)
    assert refused.value.code == "review-only-policy"
    assert store.approvals() == []
    assert_no_effect(world)


def test_approved_is_truthful_ready_without_consumption_then_normal_owner_call_can_use_it(world, monkeypatch):
    service, store, vault, effects = world
    waiting = call(service)
    decide(service, waiting)
    before = (store.root / "approvals.json").read_bytes()
    ready = call(service)
    assert ready == {**waiting, "status": "review-only-ready"}
    assert (store.root / "approvals.json").read_bytes() == before
    assert store.approvals()[0]["state"] == "approved"
    assert_no_effect(world)
    # The floor only reduces a tagged correction; normal owner turns keep the
    # existing single-use permission semantics. Count the actual mutator entry.
    def normal_write(*args, **kwargs):
        effects.append("normal-write")
        # Only this explicit unflagged normal call is allowed in this test.
        return "Example.md"
    monkeypatch.setattr(notes, "write", normal_write)
    reply = service.handle("agent", {"schemaVersion": 1, "action": "notes-write", "path": "Example.md",
                                      "text": "Current owned text."})
    assert reply["written"] == "Example.md" and effects == ["normal-write"]
    assert store.approvals()[0]["state"] == "used"
    assert (vault / "Example.md").read_text() == "Original owned fixture.\n"


def test_declined_is_not_reopened_and_changed_arguments_need_their_own_request(world):
    service, store, *_ = world
    first = call(service)
    decide(service, first, "decline")
    before = (store.root / "approvals.json").read_bytes()
    with pytest.raises(IntegrationError) as refused:
        call(service)
    assert refused.value.code == "review-only-declined"
    assert (store.root / "approvals.json").read_bytes() == before
    fresh = call(service, text="Different current text.")
    assert fresh["approvalId"] != first["approvalId"]
    assert [entry["state"] for entry in store.approvals()] == ["declined", "pending"]
    assert_no_effect(world)


def test_old_used_and_expired_grants_never_substitute_for_current_fresh_pending(world):
    service, store, *_ = world
    first = call(service)
    entry = store.approvals()[0]
    entry.update(state="used", usedAt=now())
    expired = {**entry, "id": "expired", "state": "approved", "expiresAt": now() - 1}
    store.save_approvals([entry, expired])
    fresh = call(service)
    assert fresh["approvalId"] != first["approvalId"]
    assert store.approvals()[0] == entry
    assert store.approvals()[1]["state"] == "pending"
    assert_no_effect(world)


def test_missing_generation_fails_closed_without_creating_settings(world, monkeypatch):
    service, store, *_ = world
    config = store.config()
    del config["approvalBindings"]["notes"]
    store.save_config(config)
    before = (store.root / "config.json").read_bytes()
    monkeypatch.setattr(service, "_binding", lambda *_: pytest.fail("Review created a generation"))
    with pytest.raises(IntegrationError) as refused:
        call(service)
    assert refused.value.code == "review-only-context-unavailable"
    assert (store.root / "config.json").read_bytes() == before
    assert store.approvals() == []
    assert_no_effect(world)


@pytest.mark.parametrize("flag", [False, None, 1, "true", {}])
def test_present_nontrue_private_control_cannot_downgrade_to_ordinary_allowed(world, flag):
    service, store, *_ = world
    save(service, store, notes={"write": "allow"})
    with pytest.raises(IntegrationError) as refused:
        call(service, recoveryReviewOnly=flag)
    assert refused.value.code == "invalid-review-control"
    assert store.approvals() == []
    assert_no_effect(world)


def test_only_literal_known_reads_are_available_and_do_not_arm_a_digest(world, monkeypatch):
    service, store, *_ = world
    assert call(service, "notes-search", query="Original")["results"]
    assert "Original" in call(service, "notes-read")["text"]
    assert any(server["id"] == "app:documents" for server in call(service, "mcp-tools")["servers"])
    monkeypatch.setattr(apps, "unread_news", lambda *_args, **_kw: [{"id": "1", "title": "Owned story", "link": "https://story.example", "feed": "Owned news"}])
    # Use the actual app branch and run-context suppressor, not a fake use_app.
    call(service, "mcp-call", server="app:news", tool="digest", arguments={},
         runContext={"sessionId": "fixture-session", "sessionKey": "agent:pixel:fixture-run"})
    assert service.digest_snapshots == {}
    assert not (store.root / "digest-job.json").exists()
    assert_no_effect(world)


@pytest.mark.parametrize("action", ["notify", "task-finished", "digest-plan", "digest-job-record", "digest-job-clear", "made-up"])
def test_internal_or_notification_actions_cannot_escape_via_tagged_agent_requests(world, action):
    service, store, *_ = world
    with pytest.raises(IntegrationError) as refused:
        call(service, action)
    assert refused.value.code == "review-only-refused"
    assert store.approvals() == []
    assert_no_effect(world)


def test_opaque_readonly_hint_is_proposal_only_and_ready_grant_is_not_consumed(world):
    service, store, *_ = world
    pending = mcp(service, item="owned")
    assert pending["status"] == "approval-required"
    assert mcp(service, item="owned")["approvalId"] == pending["approvalId"]
    decide(service, pending)
    ready = mcp(service, item="owned")
    assert ready["status"] == "review-only-ready" and ready["actionExecuted"] is False
    assert store.approvals()[0]["state"] == "approved"
    assert mcp(service, item="different")["approvalId"] != pending["approvalId"]
    assert_no_effect(world)


@pytest.mark.parametrize("policy", ["allow", "off"])
def test_opaque_allow_or_off_never_launches_even_with_readonly_hint(world, policy):
    service, store, *_ = world
    config = store.config()
    # Same saved catalog, changed owner policy through normal Save.
    server = config["mcp"]["servers"][0]
    server["policies"] = {"read_hint": policy}
    save(service, store, mcp={"servers": [server]})
    with pytest.raises(IntegrationError) as refused:
        mcp(service)
    assert refused.value.code == "review-only-policy"
    assert store.approvals() == []
    assert_no_effect(world)


def test_opaque_context_mismatch_and_custom_builtin_name_do_not_get_a_read_bypass(world):
    service, store, *_ = world
    config = store.config()
    config["mcp"]["servers"][0]["command"] = "another-command"
    store.save_config(config)
    with pytest.raises(IntegrationError) as refused:
        mcp(service)
    assert refused.value.code == "mcp-check-required"
    with pytest.raises(IntegrationError):
        mcp(service, "documents", "search", query="owned")
    assert store.approvals() == []
    assert_no_effect(world)


@pytest.mark.parametrize("tool,args", [("add", {"url": "https://page.example", "title": "Owned", "below": 70}),
                                       ("remove", {"id": "owned"})])
def test_watch_prepares_exact_normal_digest_and_never_enters_actuator(world, monkeypatch, tool, args):
    service, store, *_ = world
    listing = {"owned": {"url": "https://old.example", "title": "Old"}} if tool == "remove" else {}
    monkeypatch.setattr(apps, "watches", lambda *_: copy.deepcopy(listing))
    before = (store.root / "config.json").read_bytes()
    first = mcp(service, "app:watch", tool, **args)
    assert first["status"] == "approval-required"
    assert mcp(service, "app:watch", tool, **args)["approvalId"] == first["approvalId"]
    decide(service, first)
    assert mcp(service, "app:watch", tool, **args)["status"] == "review-only-ready"
    assert store.approvals()[0]["state"] == "approved"
    assert (store.root / "config.json").read_bytes() == before
    assert not (store.root / "watch-state.json").exists()
    assert_no_effect(world)


def test_watch_policy_is_rechecked_after_provider_read_without_holding_settings_lock(world, monkeypatch):
    service, store, *_ = world
    entered, release = threading.Event(), threading.Event()
    def provider_read(*_):
        entered.set()
        assert release.wait(5)
        return {}
    monkeypatch.setattr(apps, "watches", provider_read)
    with ThreadPoolExecutor(2) as pool:
        pending = pool.submit(mcp, service, "app:watch", "add", url="https://page.example")
        assert entered.wait(5)
        config = store.config()
        config["apps"]["watch"]["changes"] = "allow"
        # This owner Save completes while provider IO is still blocked.
        pool.submit(save, service, store, apps=config["apps"]).result(timeout=2)
        release.set()
        with pytest.raises(IntegrationError) as refused:
            pending.result(timeout=5)
    assert refused.value.code == "review-only-policy"
    assert store.approvals() == []
    assert_no_effect(world)


def test_watch_allowed_refuses_before_even_provider_read(world, monkeypatch):
    service, store, *_ = world
    config = store.config()
    config["apps"]["watch"]["changes"] = "allow"
    save(service, store, apps=config["apps"])
    monkeypatch.setattr(apps, "watches", lambda *_: pytest.fail("Allowed recovery performed a provider read for mutation"))
    with pytest.raises(IntegrationError) as refused:
        mcp(service, "app:watch", "add", url="https://page.example")
    assert refused.value.code == "review-only-policy"
    assert store.approvals() == []
    assert_no_effect(world)
