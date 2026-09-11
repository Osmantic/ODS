"""Disposable POSIX owner transactions, NOT installed runtime acceptance."""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
import settings_transaction as tx
from pixel_settings.contract import SettingsError
TRANSACTIONS = {}


def sha(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def owner(tmp_path):
    tmp_path.chmod(0o700)
    path = tmp_path / "openclaw.json"
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    config = {
        "agents": {"list": [{"id": "pixel", "model": "local/pixel", "sandbox": {"mode": "all"},
                              "verboseDefault": "off", "contextTokens": 32768}]},
        "plugins": {"entries": {"pixel-ods": {"enabled": True, "config": {"ownerNote": "keep"}}}},
        "models": {"providers": {"local": {"baseUrl": "http://127.0.0.1:1/v1", "apiKey": "synthetic-secret"}}},
        "env": {"ODS_MODE": "remote"}, "tools": {"deny": ["dangerous"]},
    }
    original = (json.dumps(config, indent=4) + "\n\n").encode()
    path.write_bytes(original)
    path.chmod(0o600)
    caps = {"providerContextTokens": 131072, "providerMaxOutputTokens": 16384,
            "activeContextTokens": 32768, "activeMaxOutputTokens": 4096,
            "backendContextTokens": 65536, "capacitySource": "backend-observed",
            "supportedThinkingLevels": ["off", "high"], "samplingSupported": True,
            "pixelOnlyRuntime": True}
    return path, state, caps, original


def apply(owner, preferences=None, **kwargs):
    path, state, caps, _ = owner
    options = dict(state_dir=str(state), settings_revision=3, expected_config_sha256=sha(path.read_bytes()),
                   transaction_id=sha(uuid.uuid4().bytes),
                   validate_config=lambda staged: True, check_no_active_run=lambda: False,
                   activate=lambda: "verified")
    options.update(kwargs)
    if not (state / tx.JOURNAL).exists():
        TRANSACTIONS[str(path)] = options["transaction_id"]
    return tx.apply_settings(str(path), {"verbosity": "full"} if preferences is None else preferences, caps, **options)


def recover(owner, **kwargs):
    path, state, _, _ = owner
    options = dict(state_dir=str(state), expected_config_sha256=sha(path.read_bytes()),
                   transaction_id=TRANSACTIONS.get(str(path), "a" * 64),
                   validate_config=lambda staged: True, check_no_active_run=lambda: False,
                   activate=lambda: "verified")
    options.update(kwargs)
    return tx.recover_settings(str(path), **options)


def pending(owner):
    with pytest.raises(SettingsError, match="settings-activation-unavailable"):
        apply(owner, activate=lambda: "unavailable")
    return owner[1] / tx.JOURNAL


def private_json(path, value):
    path.write_text(json.dumps(value))
    path.chmod(0o600)


def test_apply_validates_actual_stage_and_preserves_unrelated_config(owner):
    path, state, _, original = owner
    calls = []

    def validate(staged):
        assert path.read_bytes() == original
        assert Path(staged).parent == path.parent and staged != str(path)
        assert stat.S_IMODE(Path(staged).stat().st_mode) == 0o600
        calls.append("validated")
        return json.loads(Path(staged).read_bytes())["agents"]["list"][0]["verboseDefault"] == "full"

    def activate():
        assert (state / tx.JOURNAL).exists()
        assert json.loads(path.read_bytes())["agents"]["list"][0]["verboseDefault"] == "full"
        calls.append("activated")
        return "verified"

    result = apply(owner, validate_config=validate, activate=activate)
    assert result == {"status": "runtime-verified", "settingsRevision": 3, "configSha256": sha(path.read_bytes())}
    before, after = json.loads(original), json.loads(path.read_bytes())
    before["agents"]["list"][0]["verboseDefault"] = "full"
    assert before == after
    assert calls == ["validated", "activated"]
    assert (state / tx.BACKUP).read_bytes() == original
    assert not (state / tx.JOURNAL).exists()
    assert json.loads((state / tx.MANAGED).read_bytes())["settingsRevision"] == 3
    for item in state.iterdir():
        assert stat.S_IMODE(item.stat().st_mode) == 0o600
    assert "synthetic-secret" not in json.dumps(result)


def test_second_rejected_apply_restores_immediate_preimage_and_previous_receipt(owner):
    apply(owner)
    path, state, _, original = owner
    previous = path.read_bytes()
    receipt = (state / tx.MANAGED).read_bytes()
    outcomes = iter(["rejected", "verified"])
    result = apply(owner, {"verbosity": "on"}, settings_revision=4, activate=lambda: next(outcomes))
    assert result == {"status": "rolled-back", "configSha256": sha(previous)}
    assert path.read_bytes() == previous != original
    assert (state / tx.MANAGED).read_bytes() == receipt
    assert not (state / tx.JOURNAL).exists()


def test_null_reset_returns_original_owner_value_without_erasing_other_preference(owner):
    apply(owner, {"verbosity": "full", "temperature": 0.5})
    apply(owner, {"verbosity": None, "temperature": 0.5}, settings_revision=4)
    pixel = json.loads(owner[0].read_bytes())["agents"]["list"][0]
    assert pixel["verboseDefault"] == "off" and pixel["params"]["temperature"] == 0.5


def test_interrupted_activation_requires_explicit_recovery_and_restores_exact_bytes(owner):
    path, state, _, original = owner
    journal = pending(owner)
    with pytest.raises(SettingsError, match="settings-recovery-required"):
        apply(owner)
    assert recover(owner)["status"] == "rolled-back"
    assert path.read_bytes() == original
    assert not journal.exists() and not (state / tx.MANAGED).exists()


@pytest.mark.parametrize("kwargs,code", [
    ({"expected_config_sha256": "0" * 64}, "settings-config-changed"),
    ({"expected_config_sha256": "bad"}, "settings-transaction-contract"),
    ({"settings_revision": True}, "invalid-settings-revision"),
    ({"settings_revision": -1}, "invalid-settings-revision"),
    ({"activate": None}, "settings-transaction-contract"),
    ({"validate_config": lambda p: False}, "validation-failed"),
    ({"check_no_active_run": lambda: True}, "active-run"),
    ({"check_no_active_run": lambda: "idle"}, "active-run-check-failed"),
])
def test_invalid_or_busy_apply_has_no_config_or_recovery_writes(owner, kwargs, code):
    with pytest.raises((SettingsError, tx.controller.AccessModeError)) as error:
        apply(owner, **kwargs)
    assert getattr(error.value, "code", str(error.value)) == code
    assert owner[0].read_bytes() == owner[3]
    assert not list(owner[0].parent.glob(".ods-access-stage-*"))
    assert not (owner[1] / tx.JOURNAL).exists() and not (owner[1] / tx.BACKUP).exists()


def test_validation_race_does_not_overwrite_owner_edit(owner):
    path, state, _, original = owner
    edited = original + b" "

    def validate(staged):
        path.write_bytes(edited)
        return True

    with pytest.raises(tx.controller.AccessModeRace):
        apply(owner, validate_config=validate)
    assert path.read_bytes() == edited and (state / tx.JOURNAL).exists()
    with pytest.raises(SettingsError, match="settings-rollback-conflict"):
        recover(owner)
    assert path.read_bytes() == edited


def test_postwrite_drift_refuses_rollback_without_overwriting(owner):
    pending(owner)
    owner[0].write_bytes(owner[0].read_bytes() + b" ")
    changed = owner[0].read_bytes()
    with pytest.raises(SettingsError, match="settings-rollback-conflict"):
        recover(owner)
    assert owner[0].read_bytes() == changed and (owner[1] / tx.JOURNAL).exists()


@pytest.mark.parametrize("operation", ["enable", "restore"])
def test_access_cannot_consume_settings_recovery(owner, operation):
    journal = pending(owner)
    method = tx.controller.enable_full_access if operation == "enable" else tx.controller.restore_sandbox
    kwargs = {"confirmed": True} if operation == "enable" else {}
    with pytest.raises(tx.controller.AccessModeError) as error:
        method(str(owner[0]), str(owner[1]), validate_config=lambda p: True,
               restart=lambda: True, check_no_active_run=lambda: False, **kwargs)
    assert error.value.code == "settings-recovery-required" and journal.exists()


@pytest.mark.parametrize("recovering", [False, True])
def test_settings_refuses_access_pending(owner, monkeypatch, recovering):
    if recovering:
        pending(owner)
    monkeypatch.setattr(tx.controller, "_load_receipt", lambda sd: {"status": "pending"})
    before = owner[0].read_bytes()
    with pytest.raises(SettingsError, match="access-recovery-required"):
        (recover if recovering else apply)(owner)
    assert owner[0].read_bytes() == before


@pytest.mark.parametrize("change", [
    lambda j: j.update(schemaVersion=True),
    lambda j: j.update(beforeSha="invalid"),
    lambda j: j.update(configMode=True),
    lambda j: j.update(previous={}),
    lambda j: j["next"].update(settingsRevision=True),
    lambda j: j["next"]["state"].update(identity="invalid"),
    lambda j: j.update(next=None),
])
def test_corrupt_journal_refuses_before_writing(owner, change):
    journal = pending(owner)
    value = json.loads(journal.read_bytes())
    change(value)
    private_json(journal, value)
    before = owner[0].read_bytes()
    with pytest.raises(SettingsError, match="invalid-settings-state"):
        recover(owner)
    assert owner[0].read_bytes() == before and journal.exists()


@pytest.mark.parametrize("target", [tx.JOURNAL, tx.BACKUP])
@pytest.mark.parametrize("kind", ["symlink", "hardlink", "wide-mode", "fifo"])
def test_unsafe_state_files_refuse_without_blocking(owner, target, kind):
    pending(owner)
    path = owner[1] / target
    raw = path.read_bytes()
    path.unlink()
    other = owner[1] / "other"
    other.write_bytes(raw)
    other.chmod(0o600)
    if kind == "symlink":
        path.symlink_to(other)
    elif kind == "hardlink":
        os.link(other, path)
    elif kind == "fifo":
        os.mkfifo(path, 0o600)
    else:
        path.write_bytes(raw)
        path.chmod(0o644)
    before = owner[0].read_bytes()
    with pytest.raises(SettingsError, match="unsafe-settings-state"):
        recover(owner)
    assert owner[0].read_bytes() == before


def test_duplicate_json_keys_in_owner_config_are_not_silently_dropped(owner):
    owner[0].write_bytes(owner[3].replace(b'"env": {', b'"env": {}, "env": {'))
    before = owner[0].read_bytes()
    with pytest.raises(SettingsError, match="invalid-settings-state"):
        apply(owner)
    assert owner[0].read_bytes() == before and not (owner[1] / tx.BACKUP).exists()


def test_journal_duplicate_keys_refuse(owner):
    journal = pending(owner)
    journal.write_bytes(journal.read_bytes().replace(b'"kind":"settings"', b'"kind":"access","kind":"settings"', 1))
    with pytest.raises(SettingsError, match="invalid-settings-state"):
        recover(owner)


def test_backup_hash_corruption_does_not_restore(owner):
    pending(owner)
    (owner[1] / tx.BACKUP).write_bytes(b"{}")
    before = owner[0].read_bytes()
    with pytest.raises(SettingsError, match="settings-backup-mismatch"):
        recover(owner)
    assert owner[0].read_bytes() == before


@pytest.mark.parametrize("outcome,code", [(True, "settings-activation-contract"), (None, "settings-activation-contract"),
                                          ("unavailable", "settings-activation-unavailable")])
def test_unknown_activation_retains_pending_state(owner, outcome, code):
    with pytest.raises(SettingsError, match=code):
        apply(owner, activate=lambda: outcome)
    assert (owner[1] / tx.JOURNAL).exists()


def test_shared_apply_lock_is_held_during_activation(owner):
    def activate():
        with pytest.raises(tx.controller.AccessModeRace):
            with tx.controller._Lock(str(owner[1]), timeout=0):
                pytest.fail("second writer acquired shared owner lock")
        return "verified"
    apply(owner, activate=activate)


@pytest.mark.parametrize("failed_dir_fsync", [1, 2, 3, 4])
def test_directory_durability_failure_never_claims_verified(owner, monkeypatch, failed_dir_fsync):
    original_fsync = os.fsync
    count = 0

    def fault(fd):
        nonlocal count
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            count += 1
            if count == failed_dir_fsync:
                raise OSError("injected directory failure")
        return original_fsync(fd)

    monkeypatch.setattr(os, "fsync", fault)
    with pytest.raises(tx.controller.AccessModeRollback) as error:
        apply(owner)
    assert error.value.code == "write-durability-unknown"
    monkeypatch.setattr(os, "fsync", original_fsync)
    if failed_dir_fsync == 1:
        assert owner[0].read_bytes() == owner[3] and not (owner[1] / tx.JOURNAL).exists()
    else:
        assert (owner[1] / tx.JOURNAL).exists()
        recover(owner)
        assert owner[0].read_bytes() == owner[3]


def test_stage_fsync_failure_does_not_leave_private_config_tempfile(owner, monkeypatch):
    monkeypatch.setattr(os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("injected")))
    with pytest.raises(OSError):
        apply(owner)
    assert owner[0].read_bytes() == owner[3]
    assert not list(owner[0].parent.glob(".ods-access-stage-*"))


def test_mode_change_during_validation_is_not_overwritten(owner):
    def validate(staged):
        owner[0].chmod(0o640)
        return True
    with pytest.raises(SettingsError, match="settings-config-changed"):
        apply(owner, validate_config=validate)
    assert stat.S_IMODE(owner[0].stat().st_mode) == 0o640
    assert owner[0].read_bytes() == owner[3]


def test_journal_removal_durability_failure_keeps_explicit_recovery_possible(owner, monkeypatch):
    original_fsync = os.fsync
    count = 0
    def fault(fd):
        nonlocal count
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            count += 1
            if count == 6:  # backup, journal, config, managed, completed, unlink
                raise OSError("injected journal unlink durability failure")
        return original_fsync(fd)
    monkeypatch.setattr(os, "fsync", fault)
    with pytest.raises(OSError):
        apply(owner)
    monkeypatch.setattr(os, "fsync", original_fsync)
    assert (owner[1] / tx.JOURNAL).exists()
    assert recover(owner)["status"] == "rolled-back"
    assert owner[0].read_bytes() == owner[3]


def test_activation_exception_keeps_journal_and_rollback_failure_keeps_it(owner):
    def unavailable():
        raise TimeoutError("synthetic transport failure")
    with pytest.raises(TimeoutError):
        apply(owner, activate=unavailable)
    with pytest.raises(SettingsError, match="settings-rollback-unverified"):
        recover(owner, activate=lambda: "rejected")
    assert (owner[1] / tx.JOURNAL).exists()
    assert recover(owner)["status"] == "rolled-back"


def test_settings_and_access_owned_fields_compose_without_erasing_each_other(owner):
    path, state, _, original = owner
    options = dict(validate_config=lambda staged: True, restart=lambda: True, check_no_active_run=lambda: False)
    tx.controller.enable_full_access(str(path), str(state), confirmed=True, **options)
    apply(owner)
    status = tx.controller.get_status(str(path), str(state))
    assert status["status"] == "full-access" and status["runtime_verified"] is False
    tx.controller.restore_sandbox(str(path), str(state), **options)
    pixel = json.loads(path.read_bytes())["agents"]["list"][0]
    assert pixel["sandbox"] == {"mode": "all"} and pixel["verboseDefault"] == "full"
    apply(owner, {"verbosity": None})
    assert json.loads(path.read_bytes()) == json.loads(original)


def test_lost_hold_after_activation_retains_recovery(owner):
    busy = False
    def activate():
        nonlocal busy
        busy = True
        return "verified"
    with pytest.raises(tx.controller.AccessModeRejected) as error:
        apply(owner, check_no_active_run=lambda: busy, activate=activate)
    assert error.value.code == "active-run"
    assert (owner[1] / tx.JOURNAL).exists()
    assert recover(owner)["status"] == "rolled-back"


def test_lost_success_reply_has_bounded_completion_receipt_and_cannot_reapply_id(owner):
    identifier = "b" * 64
    result = apply(owner, transaction_id=identifier)
    state = tx.settings_status(str(owner[0]), state_dir=str(owner[1]))
    assert state["runtimeVerified"] is False and state["pending"] is False
    assert state["completion"] == {"transactionId": identifier, "settingsRevision": 3,
                                   "configSha256": result["configSha256"], "outcome": "applied"}
    assert state["managedRevision"] == 3
    with pytest.raises(SettingsError, match="settings-transaction-already-completed"):
        apply(owner, transaction_id=identifier)
    assert "synthetic-secret" not in json.dumps(state)


def test_pending_journal_hides_earlier_completion_and_recovery_is_id_bound(owner):
    apply(owner, transaction_id="b" * 64)
    pending(owner)
    state = tx.settings_status(str(owner[0]), state_dir=str(owner[1]))
    assert state["pending"] is True and state["completion"] is None
    before = owner[0].read_bytes()
    with pytest.raises(SettingsError, match="invalid-settings-state"):
        recover(owner, transaction_id="b" * 64)
    assert owner[0].read_bytes() == before
    recover(owner)
    state = tx.settings_status(str(owner[0]), state_dir=str(owner[1]))
    assert state["completion"]["outcome"] == "rolled-back" and state["managedRevision"] == 3


def test_completion_write_durability_failure_stays_pending_until_recovery(owner, monkeypatch):
    original = os.fsync
    count = 0
    def fault(fd):
        nonlocal count
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            count += 1
            if count == 5:
                raise OSError("injected completion durability failure")
        return original(fd)
    monkeypatch.setattr(os, "fsync", fault)
    with pytest.raises(tx.controller.AccessModeRollback):
        apply(owner)
    monkeypatch.setattr(os, "fsync", original)
    state = tx.settings_status(str(owner[0]), state_dir=str(owner[1]))
    assert state["pending"] is True and state["completion"] is None
    recover(owner)
    assert tx.settings_status(str(owner[0]), state_dir=str(owner[1]))["completion"]["outcome"] == "rolled-back"
