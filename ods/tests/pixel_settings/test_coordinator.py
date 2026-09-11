"""Real temporary file transactions with simulated services; NOT installed proof."""
import hashlib
import json
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(ROOT / "extensions/services/pixel-agent/host"))
sys.path.insert(0, str(ROOT / "extensions/services/pixel-agent/tests"))
from pixel_settings import coordinator as c
from pixel_access_bridge import AccessError, atomic_json
import pixel_access_bridge as access
import settings_transaction as tx
from test_access_bridge import FakeBridge
from test_runtime import config, envelope


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SettingsBridge(FakeBridge):
    def __init__(self, root):
        super().__init__(root)
        self.settings_data_dir = root / "data"
        self.started = 1000
        self.stopped = False
        self.owner_state = root / ".openclaw/.ods-access-mode"
        self.failure = None
        self.discover()
        self.path = self.home / ".openclaw/openclaw.json"
        self.path.parent.mkdir(mode=0o700)
        atomic_json(self.path, config())
        self.directory = self.settings_data_dir / "pixel-providers"
        self.directory.mkdir(mode=0o700, parents=True)
        (self.directory / ".provider-config.lock").touch(mode=0o600)
        self.save(3, {"verbosity": "full"})

    def save(self, revision, preferences):
        atomic_json(self.directory / "pixel-settings.json", {"schemaVersion": 1, "revision": revision, "preferences": preferences})

    def command(self, args, timeout=20):
        if "restart" in args:
            if self.failure == "restart":
                self.stopped = True
                raise AccessError("host-command-failed")
            self.stopped = False
            self.started += 1000
            return super().command(args, timeout)
        if any("ExecMainStartTimestampMonotonic" in arg for arg in args):
            return (f"MainPID={0 if self.stopped else self.pid}\nActiveState={'failed' if self.stopped else 'active'}\n"
                    f"ExecMainStartTimestampMonotonic={self.started}")
        return "0" if self.stopped else str(self.pid)

    def native(self, operation=None, token=None, *, timeout=60):
        if self.stopped:
            if operation not in (None, "acquire"): raise AccessError("gateway-restart-required")
            return self.stopped_native(self.pending()["token"] if operation is None and self.pending() else token)
        return super().native(operation, token, timeout=timeout)

    def stopped_native(self, token):
        if self.native_phase != "held" or not self.pending() or token != self.pending()["token"]:
            raise AccessError("native-lease-unconfirmed")
        return {"stopped": True, "available": True, "phase": "held", "pid": 0, "active": 0, "revision": self.nrev}

    def worker(self, operation="status", **kwargs):
        if operation == "status":
            return {"configured_status": self.mode, "config_sha256": checksum(self.path), "managed": False}
        if operation == "settings-status":
            value = tx.settings_status(str(self.path), state_dir=str(self.owner_state))
            if self.failure == "receipt" and value["completion"]:
                value["completion"]["transactionId"] = "f" * 64
            return value
        self.log.append(operation)
        if self.failure == "preinvoke": raise AccessError("owner-worker-failed")
        options = dict(state_dir=str(self.owner_state), transaction_id=kwargs["transaction_id"],
                       expected_config_sha256=kwargs["config_hash"], validate_config=lambda staged: bool(json.loads(Path(staged).read_text())),
                       check_no_active_run=kwargs["busy"], activate=kwargs["activate_settings"])
        if operation == "settings-apply":
            result = tx.apply_settings(str(self.path), kwargs["preferences"], kwargs["capabilities"],
                                       settings_revision=kwargs["settings_revision"], **options)
        else:
            result = tx.recover_settings(str(self.path), **options)
        if self.failure == "lost-reply": raise AccessError("owner-worker-failed")
        return result

    def http(self, _origin, path, _key, payload=None, **_kwargs):
        if path == "/health": return {"ok": True}
        value = envelope(json.loads(self.path.read_text()))
        value.update(pid=self.pid, revision=self.nrev)
        if self.failure == "mismatch" and json.loads(self.path.read_text())["agents"]["list"][0]["verboseDefault"] == "full":
            value["fields"]["verbosity"]["value"] = "off"
        return value


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    original = access.private_json
    monkeypatch.setattr(access, "private_json", lambda path, _uid, maximum=1048576: original(path, os.getuid(), maximum))
    read = c._read
    monkeypatch.setattr(c, "_read", lambda path, uid, maximum=1048576: read(path, os.getuid() if uid == 0 else uid, maximum))
    return SettingsBridge(tmp_path)


def request(adapter, operation="apply"):
    value = c.status(adapter)
    return {"operation": operation, "revision": value["revision"], "settingsRevision": value["settingsRevision"]}


def test_apply_real_owner_transaction_and_preserves_mode(adapter):
    result = c.change(adapter, request(adapter))
    assert result == {"outcome": "applied", "appliedRevision": 3}
    assert not adapter.pending() and not adapter.dropin.exists()
    assert adapter.log.count("restart") == 1
    assert adapter.native_phase == adapter.edge_phase == "idle"
    assert c.status(adapter)["status"] == "applied"
    assert c.status(adapter)["appliedRevision"] == 3


def test_fresh_store_status_is_actionable_and_never_creates_owner_files(adapter, tmp_path):
    adapter.settings_data_dir = str(tmp_path / "not-initialized")
    with pytest.raises(AccessError, match="settings-store-not-initialized"):
        c.status(adapter)
    assert not Path(adapter.settings_data_dir).exists()
    assert not adapter.pending()
    assert "restart" not in adapter.log


def test_mismatch_rolls_back_exact_bytes_without_claiming_applied(adapter):
    before = adapter.path.read_bytes()
    adapter.failure = "mismatch"
    result = c.change(adapter, request(adapter))
    assert result == {"outcome": "rolled-back", "appliedRevision": None}
    assert adapter.path.read_bytes() == before and adapter.log.count("restart") == 2
    status = c.status(adapter)
    assert status["status"] == "restored" and status["appliedRevision"] is None
    assert status["lastVerifiedAt"] is not None
    adapter.started += 1
    stale = c.status(adapter)
    assert stale["status"] == "not-applied" and stale["lastVerifiedAt"] is None


@pytest.mark.parametrize("failure", ["preinvoke", "lost-reply"])
def test_crash_then_explicit_recovery_without_duplicate_restart(adapter, failure):
    before = adapter.path.read_bytes()
    adapter.failure = failure
    with pytest.raises(AccessError): c.change(adapter, request(adapter))
    assert adapter.pending() and adapter.native_phase == adapter.edge_phase == "held"
    restarts = adapter.log.count("restart")
    adapter.failure = None
    result = c.change(adapter, request(adapter, "recover"))
    assert result["outcome"] == ("applied" if failure == "lost-reply" else "rolled-back")
    assert adapter.log.count("restart") == restarts and not adapter.pending()
    if failure == "preinvoke": assert adapter.path.read_bytes() == before


def test_partial_release_recovery_reproves_and_releases_both(adapter):
    adapter.fail = "release"
    with pytest.raises(AccessError): c.change(adapter, request(adapter))
    assert adapter.pending()["phase"] == "releasing" and adapter.edge_phase == "idle"
    adapter.fail = None
    assert c.change(adapter, request(adapter, "recover"))["outcome"] == "applied"
    assert adapter.log.count("restart") == 1 and adapter.native_phase == adapter.edge_phase == "idle"


def test_browser_transport_replay_after_lost_reply_cannot_apply_twice(adapter):
    original = request(adapter)
    adapter.failure = "lost-reply"
    with pytest.raises(AccessError): c.change(adapter, original)
    committed = adapter.path.read_bytes()
    log = list(adapter.log)
    with pytest.raises(AccessError, match="settings-inspection-changed"):
        c.change(adapter, original)
    assert adapter.path.read_bytes() == committed and adapter.log == log
    adapter.failure = None
    assert c.change(adapter, request(adapter, "recover"))["outcome"] == "applied"
    assert adapter.path.read_bytes() == committed and adapter.log.count("restart") == 1


def test_orphan_owner_pending_does_not_advertise_unauthorized_recovery(adapter, monkeypatch):
    original = adapter.worker
    monkeypatch.setattr(adapter, "worker", lambda operation="status", **kwargs:
                        dict(original(operation, **kwargs), pending=True) if operation == "settings-status"
                        else original(operation, **kwargs))
    before = adapter.path.read_bytes()
    with pytest.raises(AccessError, match="settings-recovery-journal-missing"):
        c.status(adapter)
    assert not adapter.pending() and not adapter.log and adapter.path.read_bytes() == before


def test_no_owner_write_partial_release_is_also_recoverable(adapter):
    adapter.failure = "preinvoke"
    with pytest.raises(AccessError): c.change(adapter, request(adapter))
    adapter.failure = None
    adapter.fail = "release"
    with pytest.raises(AccessError): c.change(adapter, request(adapter, "recover"))
    adapter.fail = None
    assert c.change(adapter, request(adapter, "recover"))["outcome"] == "rolled-back"
    assert adapter.log.count("restart") == 0


def test_stopped_owned_restart_recovers_but_pristine_stopped_apply_does_not(adapter):
    before = adapter.path.read_bytes()
    adapter.failure = "restart"
    with pytest.raises(AccessError): c.change(adapter, request(adapter))
    assert adapter.pending()["phase"] == "restarting" and adapter.stopped
    adapter.failure = None
    assert c.change(adapter, request(adapter, "recover"))["outcome"] == "rolled-back"
    assert adapter.path.read_bytes() == before and not adapter.stopped
    stale = request(adapter)
    adapter.stopped = True
    with pytest.raises(AccessError): c.change(adapter, stale)
    assert not adapter.pending()


def test_stale_recovery_and_wrong_settings_revision_never_acquire(adapter):
    adapter.failure = "preinvoke"
    with pytest.raises(AccessError): c.change(adapter, request(adapter))
    recovery = request(adapter, "recover")
    log = list(adapter.log)
    for field, value in (("revision", "0" * 64), ("settingsRevision", 4)):
        with pytest.raises(AccessError, match="inspection-changed"):
            c.change(adapter, dict(recovery, **{field: value}))
    assert adapter.log == log


def test_corrupt_saved_preferences_do_not_prevent_id_bound_recovery(adapter):
    adapter.failure = "preinvoke"
    with pytest.raises(AccessError): c.change(adapter, request(adapter))
    (adapter.directory / "pixel-settings.json").write_text("not json")
    adapter.failure = None
    assert c.change(adapter, request(adapter, "recover"))["outcome"] == "rolled-back"


def test_owner_receipt_mismatch_retains_hold(adapter):
    adapter.failure = "receipt"
    with pytest.raises(AccessError, match="completion-mismatch"): c.change(adapter, request(adapter))
    assert adapter.native_phase == adapter.edge_phase == "held" and adapter.pending()


def test_unjournaled_config_edit_is_never_overwritten(adapter):
    adapter.failure = "preinvoke"
    with pytest.raises(AccessError): c.change(adapter, request(adapter))
    value = json.loads(adapter.path.read_text())
    value["ownerNote"] = "concurrent edit"
    atomic_json(adapter.path, value)
    before = adapter.path.read_bytes()
    adapter.failure = None
    with pytest.raises(AccessError, match="recovery-conflict"): c.change(adapter, request(adapter, "recover"))
    assert adapter.path.read_bytes() == before and adapter.pending()


def test_pid_reuse_with_new_start_identity_invalidates_settings_proof(adapter):
    c.change(adapter, request(adapter))
    adapter.started += 1
    assert c.status(adapter)["status"] == "not-applied"


def test_new_saved_revision_is_not_mistaken_for_current_applied_revision(adapter):
    c.change(adapter, request(adapter))
    adapter.save(4, {"verbosity": "on"})
    status = c.status(adapter)
    assert status["status"] == "saved-changes"
    assert status["settingsRevision"] == 4 and status["appliedRevision"] == 3


@pytest.mark.parametrize("key,value", [("phase", "error"), ("transactionId", "bad"), ("token", False),
    ("kind", "access"), ("beforeIdentity", {"pid": 123}), ("extra", True), ("settingsRevision", True)])
def test_corrupt_root_recovery_journal_cannot_acquire_or_invoke(adapter, key, value):
    adapter.failure = "preinvoke"
    with pytest.raises(AccessError): c.change(adapter, request(adapter))
    journal = adapter.pending()
    journal[key] = value
    atomic_json(adapter.state / "transition.json", journal)
    log = list(adapter.log)
    with pytest.raises(AccessError): c.status(adapter)
    assert adapter.log == log


def test_stale_saved_revision_and_busy_apply_leave_no_root_journal(adapter):
    stale = request(adapter)
    adapter.save(4, {"verbosity": "on"})
    with pytest.raises(AccessError, match="inspection-changed"): c.change(adapter, stale)
    adapter.active = 1
    with pytest.raises(AccessError, match="runtime-busy"): c.change(adapter, request(adapter))
    assert not adapter.pending() and "settings-apply" not in adapter.log


def test_host_data_directory_identity_must_match_installer_pin(adapter):
    fingerprint = adapter.settings_source()
    assert adapter.settings_status(data_dir_id=fingerprint)["settingsRevision"] == 3
    before = adapter.path.read_bytes()
    with pytest.raises(AccessError, match="data-directory-changed"):
        adapter.change_settings(request(adapter), data_dir_id="0" * 64)
    assert adapter.path.read_bytes() == before and not adapter.pending()


def test_env_retargeting_cannot_apply_old_store_with_coincidentally_same_revision(adapter):
    stale = request(adapter)
    fingerprint = adapter.settings_source()
    (adapter.install / ".env").write_text("ODS_DATA_DIR=/other/private/data\n")
    (adapter.install / ".env").chmod(0o600)
    with pytest.raises(AccessError, match="data-directory-changed"):
        adapter.change_settings(stale, data_dir_id=fingerprint)
    assert not adapter.pending()


def test_existing_store_lock_is_exclusive_and_never_created_as_root(adapter):
    with c._store(adapter, exclusive=True):
        with pytest.raises(AccessError, match="store-busy"):
            with c._store(adapter, exclusive=False): pass
    lock = adapter.directory / ".provider-config.lock"
    lock.unlink()
    with pytest.raises(FileNotFoundError):
        with c._store(adapter, exclusive=True): pass
    assert not lock.exists()


@pytest.mark.parametrize("mutation", ["symlink", "hardlink", "permissions", "duplicate"])
def test_private_source_refuses_unsafe_files(adapter, mutation):
    path = adapter.directory / "pixel-settings.json"
    if mutation == "symlink":
        target = adapter.directory / "target.json"
        path.rename(target)
        path.symlink_to(target)
    if mutation == "hardlink": os.link(path, adapter.directory / "other.json")
    if mutation == "permissions": path.chmod(0o644)
    if mutation == "duplicate": path.write_text('{"revision":3,"revision":4}')
    with pytest.raises((AccessError, ValueError)): c.status(adapter)
