"""Real model transactions with fake installed services; not deployment proof."""
import sys
if sys.platform == "win32":
    from unittest import SkipTest
    raise SkipTest("POSIX owner coordination runs under Linux/WSL")
import json
import hashlib
import os
from pathlib import Path
import pytest
from test_model_transaction import config, NEW, ID, sha
from test_access_bridge import FakeBridge
import pixel_access_bridge as access
from pixel_access_bridge import AccessError, atomic_json
import pixel_model_coordinator as c
import model_transaction as tx
from pixel_model_contract import projection


class ModelBridge(FakeBridge):
    def __init__(self, root):
        super().__init__(root)
        self.started=1000; self.stopped=False; self.failure=None
        self.discover()
        self.path=self.home / '.openclaw/openclaw.json'
        self.path.parent.mkdir(mode=0o700)
        atomic_json(self.path,config())
        config_directory = self.home / '.config'
        config_directory.mkdir(mode=0o700)
        marker_directory = config_directory / 'ods'
        marker_directory.mkdir(mode=0o700)
        self.marker_path = marker_directory / 'pixel-managed.json'
        atomic_json(self.marker_path, {'schema_version': 2, 'manager': 'ods', 'state': 'ready',
            'initial_active_state': 'absent',
            'install_dir': str(self.install), 'configuration_sha256': c._marker_digest(config())})
        self.loaded=json.loads(self.path.read_text())
        self.owner_state=self.path.parent / '.ods-access-mode'
    def command(self,args,timeout=20):
        if 'restart' in args:
            if self.failure=='restart':
                self.stopped=True;raise AccessError('host-command-failed')
            self.started+=1000;self.stopped=False
            self.loaded=json.loads(self.path.read_text())
            return super().command(args,timeout)
        if any('ExecMainStartTimestampMonotonic' in arg for arg in args):
            return f'MainPID={self.pid if not self.stopped else 0}\nActiveState={"active" if not self.stopped else "failed"}\nExecMainStartTimestampMonotonic={self.started}'
        return str(self.pid)
    def worker(self,operation='status',**kw):
        if operation=='status':return {'configured_status':self.mode,'config_sha256':sha(self.path),'managed':False}
        self.log.append(operation)
        if self.failure=='preinvoke' and operation=='model-begin':raise AccessError('owner-worker-failed')
        result=tx.operate(str(self.path),state_dir=str(self.owner_state),operation=operation,
            transaction_id=kw.get('transaction_id'),expected_config_sha256=kw.get('config_hash'),
            proposed=kw.get('model_target'),outcome=kw.get('model_outcome'),
            validate_config=lambda staged:True,check_no_active_run=kw.get('busy'))
        if self.failure=='lost-reply' and operation=='model-apply':raise AccessError('owner-worker-failed')
        return result
    def http(self,_origin,_path,_key,payload=None,**kw):
        if self.stopped:raise AccessError('model-runtime-unavailable')
        return {'schemaVersion':1,'source':'current-model-contract','pid':self.pid,'revision':self.nrev,
                'observedAt':'2026-09-16T00:00:00.000Z',**projection(self.loaded)}
    def stopped_native(self,token):
        if self.native_phase!='held' or token!=self.pending()['token']:raise AccessError('native-lease-unconfirmed')
        return {'stopped':True,'available':True,'phase':'held','pid':0,'active':0,'revision':self.nrev}
    def owns_native_hold(self,snapshot,token):
        return self.native_phase=='held' and not self.active and token==self.pending()['token']

@pytest.fixture
def adapter(tmp_path,monkeypatch):
    tmp_path.chmod(0o700)
    original=access.private_json
    read=lambda path,_uid,maximum=1048576:original(path,os.getuid(),maximum)
    monkeypatch.setattr(access,'private_json',read);monkeypatch.setattr(c,'private_json',read)
    return ModelBridge(tmp_path)

def begin(a):return c.control(a,'model-begin',{'revision':c.control(a,'model-status')['revision'],'transactionId':ID})
def apply(a):return c.control(a,'model-apply',{'transactionId':ID,'target':NEW})
def finish(a,outcome='commit'):return c.control(a,'model-finish',{'transactionId':ID,'outcome':outcome})

def test_64k_to_16k_holds_through_actual_runtime_readback(adapter):
    previous_marker = json.loads(adapter.marker_path.read_text())['configuration_sha256']
    assert begin(adapter)['pending'];assert adapter.native_phase==adapter.edge_phase=='held'
    result=apply(adapter);assert result['status']=='applied' and result['contract']==NEW
    assert adapter.log.count('restart')==1
    assert adapter.native_phase==adapter.edge_phase=='held'
    done=finish(adapter);assert done['outcome']=='commit' and not done['pending']
    assert json.loads(adapter.marker_path.read_text())['configuration_sha256'] == c._marker_digest(json.loads(adapter.path.read_text()))
    assert json.loads(adapter.marker_path.read_text())['configuration_sha256'] != previous_marker
    assert adapter.marker_path.stat().st_mode & 0o777 == 0o600
    assert adapter.marker_path.stat().st_uid == os.getuid()
    assert adapter.native_phase==adapter.edge_phase=='idle'
    assert finish(adapter)==done
    assert 'PRIVATE' not in json.dumps(done)

def test_bootstrap_completion_does_not_block_browser_model_switch(adapter):
    # Fresh installs leave the old shared receipt in this exact format.
    adapter.state.mkdir(mode=0o700)
    promotion = {"kind": "model-completion", "transaction_id": ID,
                 "outcome": "applied", "config_sha256": sha(adapter.path)}
    atomic_json(adapter.state / "model-completed.json", promotion)
    assert c.control(adapter, 'model-status')['status'] == 'ready'
    begin(adapter);apply(adapter)
    done=finish(adapter)
    assert done['status'] == 'completed' and done['outcome'] == 'commit'
    assert (adapter.state / "model-route-completed.json").exists()
    assert json.loads((adapter.state / "model-completed.json").read_text()) == promotion

def test_legacy_browser_completion_is_read_without_rewriting_it(adapter):
    adapter.state.mkdir(mode=0o700)
    receipt={"transactionId": ID, "outcome": "commit", "configSha256": sha(adapter.path)}
    atomic_json(adapter.state / "model-completed.json", receipt)
    status=c.control(adapter, 'model-status')
    assert status['status'] == 'completed' and status['transactionId'] == ID
    assert not (adapter.state / "model-route-completed.json").exists()

def test_malformed_legacy_promotion_receipt_fails_closed(adapter):
    adapter.state.mkdir(mode=0o700)
    atomic_json(adapter.state / "model-completed.json", {"kind": "model-completion",
                "transaction_id": "bad", "outcome": "applied", "config_sha256": sha(adapter.path)})
    with pytest.raises(AccessError,match='invalid-model-completion'):
        c.control(adapter,'model-status')

@pytest.mark.parametrize('failure',['preinvoke','lost-reply'])
def test_lost_reply_or_partial_begin_can_restore_exact_bytes(adapter,failure):
    before=adapter.path.read_bytes();adapter.failure=failure
    prior_marker = adapter.marker_path.read_bytes()
    with pytest.raises(AccessError):
        begin(adapter)
        apply(adapter)
    assert adapter.pending()
    adapter.failure=None
    restored=finish(adapter,'rollback')
    assert restored['outcome']=='rollback' and adapter.path.read_bytes()==before
    assert adapter.marker_path.read_bytes() == prior_marker
    assert adapter.native_phase==adapter.edge_phase=='idle'


def test_model_begin_refuses_unattested_marker_drift(adapter):
    marker = json.loads(adapter.marker_path.read_text())
    marker['configuration_sha256'] = 'f' * 64
    atomic_json(adapter.marker_path, marker)
    with pytest.raises(AccessError, match='model-marker-drifted'):
        begin(adapter)
    assert not adapter.pending()


@pytest.mark.parametrize('field,value', [
    ('manager', 'other'), ('state', 'deactivating'),
    ('initial_active_state', 'present'), ('install_dir', '/other/install'),
])
def test_model_begin_refuses_foreign_or_inactive_marker(adapter, field, value):
    marker = json.loads(adapter.marker_path.read_text())
    marker[field] = value
    atomic_json(adapter.marker_path, marker)
    with pytest.raises(AccessError, match='model-marker-invalid'):
        begin(adapter)
    assert not adapter.pending()


def test_model_begin_refuses_writable_marker_directory(adapter):
    adapter.marker_path.parent.chmod(0o777)
    with pytest.raises(AccessError, match='model-marker-unsafe'):
        begin(adapter)
    assert not adapter.pending()


def test_model_begin_refuses_symlinked_marker(adapter):
    alternate = adapter.marker_path.with_name('other.json')
    adapter.marker_path.rename(alternate)
    adapter.marker_path.symlink_to(alternate)
    with pytest.raises((AccessError, OSError)):
        begin(adapter)
    assert not adapter.pending()


def test_marker_digest_matches_independent_installer_contract():
    canonical = json.dumps(config(), sort_keys=True, separators=(',', ':')).encode()
    expected = hashlib.sha256(b'ods-pixel-openclaw-v1\0' + canonical).hexdigest()
    assert c._marker_digest(config()) == expected


def test_model_finish_preserves_hold_if_marker_changes_during_switch(adapter):
    original = json.loads(adapter.marker_path.read_text())
    begin(adapter); apply(adapter)
    changed = dict(original, configuration_sha256='f' * 64)
    atomic_json(adapter.marker_path, changed)
    with pytest.raises(AccessError, match='model-marker-drifted'):
        finish(adapter)
    assert adapter.pending() and adapter.native_phase == adapter.edge_phase == 'held'
    assert json.loads(adapter.marker_path.read_text()) == changed
    atomic_json(adapter.marker_path, original)
    assert finish(adapter)['outcome'] == 'commit'


def test_marker_rebind_is_idempotent_after_interrupted_release(adapter):
    begin(adapter); apply(adapter)
    adapter.fail = 'release'
    with pytest.raises(AccessError): finish(adapter)
    bound = adapter.marker_path.read_bytes()
    adapter.fail = None
    assert finish(adapter)['outcome'] == 'commit'
    assert adapter.marker_path.read_bytes() == bound


def test_prepatch_pending_journal_can_complete_without_adopting_drift(adapter):
    begin(adapter); apply(adapter)
    journal = adapter.pending()
    journal.pop('markerBeforeSha')
    atomic_json(adapter.state / 'transition.json', journal)
    assert finish(adapter)['outcome'] == 'commit'
    assert json.loads(adapter.marker_path.read_text())['configuration_sha256'] == c._marker_digest(json.loads(adapter.path.read_text()))


def test_model_finish_refuses_changed_before_snapshot_without_rebinding_marker(adapter):
    begin(adapter); apply(adapter)
    before_path = adapter.state / 'model-before.json'
    before = json.loads(before_path.read_text())
    marker = adapter.marker_path.read_bytes()
    atomic_json(before_path, {**before, 'tampered': True})

    with pytest.raises(AccessError, match='model-before-changed'):
        finish(adapter)

    assert adapter.pending()
    assert adapter.marker_path.read_bytes() == marker
    atomic_json(before_path, before)
    assert finish(adapter)['outcome'] == 'commit'


def test_six_switches_keep_installer_marker_bound(adapter):
    # Repeated browser cycles must not strand the uninstall/reinstall guard.
    for cycle in range(1, 7):
        transaction_id = f'{cycle:x}' * 64
        revision = c.control(adapter, 'model-status')['revision']
        c.control(adapter, 'model-begin', {'revision': revision, 'transactionId': transaction_id})
        c.control(adapter, 'model-apply', {'transactionId': transaction_id, 'target': NEW})
        c.control(adapter, 'model-finish', {'transactionId': transaction_id, 'outcome': 'commit'})
        marker = json.loads(adapter.marker_path.read_text())
        assert marker['configuration_sha256'] == c._marker_digest(json.loads(adapter.path.read_text()))


def test_normal_nonwritable_config_parent_is_accepted(adapter):
    (adapter.home / '.config').chmod(0o755)
    assert begin(adapter)['pending']
    assert finish(adapter, 'rollback')['outcome'] == 'rollback'

def test_partial_release_reacquires_owned_hold_and_finishes(adapter):
    begin(adapter);apply(adapter);adapter.fail='release'
    with pytest.raises(AccessError):finish(adapter)
    assert adapter.pending()['phase']=='releasing' and adapter.edge_phase=='idle'
    adapter.fail=None
    assert finish(adapter)['outcome']=='commit'
    assert adapter.log.count('restart')==1

def test_conflicting_request_active_run_and_config_drift_rejected(adapter):
    adapter.active=1
    with pytest.raises(AccessError,match='runtime-busy'):begin(adapter)
    assert not adapter.pending()
    adapter.active=0;begin(adapter)
    with pytest.raises(AccessError,match='transaction-conflict'):
        c.control(adapter,'model-apply',{'transactionId':'c'*64,'target':NEW})
    with pytest.raises(AccessError,match='apply-unverified'):finish(adapter)
    adapter.path.write_bytes(adapter.path.read_bytes()+b' ')
    with pytest.raises(Exception,match='model-config-changed|model-rollback-conflict'):finish(adapter,'rollback')
    assert adapter.pending() and adapter.native_phase==adapter.edge_phase=='held'

def test_status_never_claims_disk_changes_as_loaded_runtime(adapter):
    before=c.control(adapter,'model-status')['contract']
    begin(adapter);adapter.failure='lost-reply'
    with pytest.raises(AccessError):apply(adapter)
    status=c.control(adapter,'model-status')
    assert status['pending'] and status['status']=='held' and status['contract']==before
    adapter.failure=None
    assert apply(adapter)['contract']==NEW

def test_failed_restart_restores_previous_config_and_owned_stopped_unit(adapter):
    before=adapter.path.read_bytes();begin(adapter);adapter.failure='restart'
    with pytest.raises(AccessError):apply(adapter)
    assert adapter.stopped and adapter.pending() and adapter.path.read_bytes()!=before
    adapter.failure=None
    assert finish(adapter,'rollback')['outcome']=='rollback'
    assert not adapter.stopped and adapter.path.read_bytes()==before


def test_stopped_process_readback_never_uses_stale_http_origin(adapter,monkeypatch):
    begin(adapter)
    adapter.stopped=True
    monkeypatch.setattr(adapter,'native',lambda *args,**kw:adapter.stopped_native(adapter.pending()['token']))
    monkeypatch.setattr(adapter,'http',lambda *args,**kw:pytest.fail('stopped process has no live readback'))
    with pytest.raises(AccessError,match='model-runtime-unavailable'):c._readback(adapter)

def test_status_refuses_partial_hold_and_owner_snapshot_failure(adapter):
    adapter.fail='acquire'
    with pytest.raises(AccessError):begin(adapter)
    with pytest.raises(AccessError,match='hold-unconfirmed'):c.control(adapter,'model-status')
    adapter.fail=None
    finish(adapter,'rollback')

def test_status_recovers_applied_phase_only_with_live_contract_and_both_holds(adapter):
    begin(adapter);apply(adapter)
    journal=adapter.pending();journal['phase']='applying';atomic_json(adapter.state/'transition.json',journal)
    assert c.control(adapter,'model-status')['status']=='applied'
    assert finish(adapter)['outcome']=='commit'
    # A second private transaction tests the missing Edge proof separately.
    result=c.control(adapter,'model-status')
    c.control(adapter,'model-begin',{'revision':result['revision'],'transactionId':'c'*64})
    adapter.edge_phase='idle'
    with pytest.raises(AccessError,match='hold-unconfirmed'):c.control(adapter,'model-status')

def test_status_held_is_never_returned_without_owner_snapshot(adapter):
    adapter.failure='preinvoke'
    with pytest.raises(AccessError):begin(adapter)
    with pytest.raises(AccessError,match='begin-unconfirmed'):c.control(adapter,'model-status')
