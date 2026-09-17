"""Real model transactions with fake installed services; not deployment proof."""
import sys
if sys.platform == "win32":
    from unittest import SkipTest
    raise SkipTest("POSIX owner coordination runs under Linux/WSL")
import json
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
    assert begin(adapter)['pending'];assert adapter.native_phase==adapter.edge_phase=='held'
    result=apply(adapter);assert result['status']=='applied' and result['contract']==NEW
    assert adapter.log.count('restart')==1
    assert adapter.native_phase==adapter.edge_phase=='held'
    done=finish(adapter);assert done['outcome']=='commit' and not done['pending']
    assert adapter.native_phase==adapter.edge_phase=='idle'
    assert finish(adapter)==done
    assert 'PRIVATE' not in json.dumps(done)

@pytest.mark.parametrize('failure',['preinvoke','lost-reply'])
def test_lost_reply_or_partial_begin_can_restore_exact_bytes(adapter,failure):
    before=adapter.path.read_bytes();adapter.failure=failure
    with pytest.raises(AccessError):
        begin(adapter)
        apply(adapter)
    assert adapter.pending()
    adapter.failure=None
    restored=finish(adapter,'rollback')
    assert restored['outcome']=='rollback' and adapter.path.read_bytes()==before
    assert adapter.native_phase==adapter.edge_phase=='idle'

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
