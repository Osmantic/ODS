"""Remote-route transactions with real temporary receipts and inert runtimes."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

import pytest

from test_host_agent import _mod, TestRemoteProviderLifecycle as _LifecycleFixtures


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    root=tmp_path/'ods'
    root.mkdir()
    monkeypatch.setattr(_mod,'INSTALL_DIR',root)
    data=root/'data'
    data.mkdir()
    (data/'.extensions-lock').write_bytes(b'\0')
    scripts=root/'scripts'
    scripts.mkdir()
    shutil.copyfile(Path(__file__).resolve().parents[4]/'scripts/extension-selection.py',
                    scripts/'extension-selection.py')
    for service_id in ('remote-provider-egress', 'remote-provider-ssh-tunnel'):
        service=root/'extensions/services'/service_id
        service.mkdir(parents=True)
        (service/'compose.yaml').write_text(
            f'services:\n  {service_id}:\n    image: example:latest\n')
    if os.name == 'nt':
        # Dashboard conftest's partial POSIX stub cannot replace msvcrt locks.
        monkeypatch.delitem(sys.modules, 'fcntl', raising=False)
    # Dynamic import may create bytecode in the installed script directory;
    # include that stable artifact in the rollback snapshot.
    _mod._load_extension_selector()
    cloud=root/'config/litellm/cloud.yaml'
    cloud.parent.mkdir(parents=True)
    cloud.write_text('previous cloud\n')
    env=root/'.env'
    env.write_text('ODS_MODE=local\nLLM_API_URL=http://llama-server:8080\nPIXEL_OPENWEBUI_KEY=fixture-chat-key\nGGUF_FILE=local.gguf\n')
    env.chmod(0o600)
    monkeypatch.setattr(_mod,'DATA_DIR',data)
    helpers=_LifecycleFixtures()
    helpers._patch_successful_probe(monkeypatch)
    state=SimpleNamespace(events=[],transactions=[],held=False,failure=None,
        native={'model':'local','contextLength':65536,'maxTokens':4096,'reasoning':False},
        root=root,data=data,cloud=cloud,env=env,helpers=helpers)

    class Transaction:
        def __init__(self):
            self.previous=deepcopy(state.native)
            self.target=None
            self.completed=False
        def verify_held(self):
            state.events.append('verify-held')
            if state.failure=='ownership':
                raise _mod._PixelModelTransactionUncertain('ownership changed')
            assert state.held
        def apply(self,target):
            assert state.held
            self.target=deepcopy(target)
            state.native=deepcopy(target)
            state.events.append('apply')
            if state.failure=='apply':raise _mod._PixelModelTransactionUncertain('apply unknown')
            return 'reconciled'
        def finish(self,outcome):
            assert state.held
            state.events.append('finish-'+outcome)
            if outcome=='commit':
                assert self.target
                if 'routeFingerprint' in self.target:
                    receipt=json.loads(_mod._remote_provider_activation_public_path().read_text())
                    assert receipt['proven'] and receipt['routeFingerprint']==self.target['routeFingerprint']
            if state.failure=='finish':raise _mod._PixelModelTransactionUncertain('finish unknown')
            state.native=deepcopy(self.target if outcome=='commit' else self.previous)
            self.completed=True
            state.held=False
            if state.failure=='final-journal':raise OSError('final journal write failed')
    def begin(_env):
        state.events.append('begin')
        if state.failure=='begin':raise _mod._PixelModelTransactionRejected('busy')
        assert not state.held
        state.held=True
        tx=Transaction()
        state.transactions.append(tx)
        return tx
    monkeypatch.setattr(_mod,'_begin_pixel_model_transaction',begin)
    def inspect():
        assert not state.held, 'pending native status must use transaction.previous'
        return deepcopy(state.native)
    monkeypatch.setattr(_mod,'_managed_pixel_runtime_contract',inspect)
    monkeypatch.setattr(_mod,'_active_remote_provider_pixel_runtime',lambda:None)
    monkeypatch.setattr(_mod,'_capture_container_state',lambda *_:{'exists':True,'running':True})
    def restart(*_args,**_kwargs):
        assert state.held
        state.events.append('restart')
        return True
    monkeypatch.setattr(_mod,'_restart_existing_container',restart)
    def restore(*_args,**_kwargs):
        assert state.held
        state.events.append('restore-container')
        return True
    monkeypatch.setattr(_mod,'_restore_container_state',restore)
    monkeypatch.setattr(_mod,'_wait_for_container_health',lambda *_:None)
    def render(_route,_env):
        assert state.held
        state.events.append('render')
        cloud.write_text('remote cloud\n')
    monkeypatch.setattr(_mod,'_render_remote_provider_cloud_config',render)
    def verify(_env,**_kwargs):
        state.events.append('route-proof')
        if state.failure in ('route','ownership'):raise RuntimeError('route unavailable')
    monkeypatch.setattr(_mod,'_verify_litellm_route',verify)
    def prove(_env,contract):
        state.events.append('prove-previous')
        if 'routeFingerprint' in contract:
            saved=json.loads(_mod._remote_provider_route_state_path().read_text())
            return _mod._remote_provider_runtime_contract(saved)==contract
        return _env['ODS_MODE']=='local' and cloud.read_text()=='previous cloud\n'
    monkeypatch.setattr(_mod,'_prove_pixel_model_contract',prove)
    for name in ('_write_remote_provider_route_state','_write_remote_provider_secret','_write_remote_provider_profile'):
        original=getattr(_mod,name)
        def mutate(*args,_name=name,_original=original,**kwargs):
            assert state.held
            state.events.append(_name)
            return _original(*args,**kwargs)
        monkeypatch.setattr(_mod,name,mutate)
    def forbidden(*_args,**_kwargs):pytest.fail('transaction path must not invoke legacy reconciliation')
    monkeypatch.setattr(_mod,'_reconcile_managed_pixel_contract',forbidden)
    return state


def configure(state, *, ssh=False, endpoint=None):
    payload=state.helpers._ssh_configure_payload() if ssh else state.helpers._configure_payload()
    if endpoint:payload['provider']['baseUrl']=endpoint
    return _mod._apply_remote_provider_lifecycle_operation(payload,_mod._plan_remote_provider_lifecycle_operation(payload))


def files(root):
    return {str(path.relative_to(root)):path.read_bytes() for path in root.rglob('*') if path.is_file()}


def test_configure_holds_admission_before_egress_and_commits_after_receipts(runtime):
    result=configure(runtime)
    events=runtime.events
    assert events.index('begin')<events.index('_write_remote_provider_secret')<events.index('_write_remote_provider_route_state')
    assert events.index('route-proof')<events.index('apply')<events.index('finish-commit')
    assert events.count('begin')==1 and events.count('finish-commit')==1
    assert result['activation']['routeFingerprint']==runtime.native['routeFingerprint']
    assert not runtime.held


def test_two_remote_providers_with_same_model_use_distinct_transactions_and_fingerprints(runtime):
    configure(runtime)
    first=deepcopy(runtime.native)
    result=configure(runtime,endpoint='https://second.example.test/v1')
    assert runtime.transactions[1].previous==first
    assert runtime.native['model']==first['model']
    assert runtime.native['routeFingerprint']!=first['routeFingerprint']
    assert result['applied'] and len(runtime.transactions)==2 and not runtime.held


def test_refused_begin_changes_no_provider_files(runtime):
    before=files(runtime.root)
    runtime.failure='begin'
    with pytest.raises(_mod._RemoteProviderApplyError,match='busy'):configure(runtime)
    assert files(runtime.root)==before
    assert runtime.events==['begin']


def test_route_failure_restores_egress_and_consumers_before_proven_rollback(runtime):
    before=files(runtime.root)
    runtime.failure='route'
    with pytest.raises(_mod._RemoteProviderApplyError,match='route unavailable'):configure(runtime)
    assert files(runtime.root)==before
    assert runtime.events.index('verify-held')<runtime.events.index('restore-container')
    assert runtime.events[-2:]==['prove-previous','finish-rollback']
    assert runtime.native==runtime.transactions[0].previous and not runtime.held


@pytest.mark.parametrize('refresh_fails', [False, True])
def test_rejected_replacement_refreshes_consumer_after_restoring_provider_files(
    runtime, monkeypatch, refresh_fails,
):
    configure(runtime)
    before = files(runtime.root)
    previous = deepcopy(runtime.native)
    secret = _mod._remote_provider_secret_path('REMOTE_LLM_API_KEY')
    previous_secret = secret.read_bytes()
    loaded = {'secret': previous_secret}
    runtime.events.clear()

    def restart(*args, **kwargs):
        loaded['secret'] = secret.read_bytes()
        runtime.events.append('load-candidate')
        return True

    def restore(*args, **kwargs):
        assert runtime.held
        # A startup consumer sees the provider files at recreation time.
        assert secret.read_bytes() == previous_secret
        assert files(runtime.root) == before
        runtime.events.append('refresh-restored')
        if refresh_fails:
            raise RuntimeError('consumer recreation failed')
        loaded['secret'] = secret.read_bytes()
        return True

    def verify(*args, **kwargs):
        if loaded['secret'] != previous_secret:
            raise RuntimeError('candidate chat denied')

    def prove(_env, contract):
        runtime.events.append('prove-restored')
        return loaded['secret'] == previous_secret and contract == previous

    monkeypatch.setattr(_mod, '_restart_existing_container', restart)
    monkeypatch.setattr(_mod, '_restore_container_state', restore)
    monkeypatch.setattr(_mod, '_verify_litellm_route', verify)
    monkeypatch.setattr(_mod, '_prove_pixel_model_contract', prove)
    payload = runtime.helpers._configure_payload()
    payload['secrets']['apiKey'] = 'rejected-fixture-key'
    plan = _mod._plan_remote_provider_lifecycle_operation(payload)
    if refresh_fails:
        with pytest.raises(_mod._PixelModelTransactionUncertain, match='consumer could not be refreshed'):
            _mod._apply_remote_provider_lifecycle_operation(payload, plan)
        assert runtime.held and 'finish-rollback' not in runtime.events
    else:
        with pytest.raises(_mod._RemoteProviderApplyError, match='candidate chat denied') as caught:
            _mod._apply_remote_provider_lifecycle_operation(payload, plan)
        assert caught.value.rollback == {'attempted': True, 'ok': True}
        assert runtime.events[-3:] == ['refresh-restored', 'prove-restored', 'finish-rollback']
        assert runtime.native == previous and not runtime.held
    assert files(runtime.root) == before
    assert runtime.events.count('refresh-restored') == 1


@pytest.mark.parametrize('failure',['apply','finish','ownership'])
def test_unconfirmed_mutation_or_lost_ownership_never_restores_or_releases(runtime,failure):
    runtime.failure=failure
    with pytest.raises(_mod._PixelModelTransactionUncertain):configure(runtime)
    assert runtime.env.read_text().startswith('ODS_MODE=cloud\n')
    assert _mod._remote_provider_route_state_path().exists()
    assert 'restore-container' not in runtime.events
    assert 'finish-rollback' not in runtime.events
    assert runtime.held


def test_disable_proves_local_runtime_and_removes_receipts_before_commit(runtime):
    configure(runtime)
    payload={'action':'disable'}
    result=_mod._apply_remote_provider_lifecycle_operation(payload,_mod._plan_remote_provider_lifecycle_operation(payload))
    assert result['activation']['restored'] and result['activation']['proven']
    assert runtime.native['model']=='local' and 'routeFingerprint' not in runtime.native
    assert not _mod._remote_provider_activation_state_path().exists()
    assert not _mod._remote_provider_activation_public_path().exists()
    assert runtime.events[-1]=='finish-commit' and not runtime.held


def test_ssh_replacement_of_active_remote_is_refused_before_staging(runtime):
    configure(runtime)
    before=files(runtime.root)
    runtime.events.clear()
    with pytest.raises(_mod._RemoteProviderApplyError,match='Disable the active remote provider'):
        configure(runtime,ssh=True)
    assert files(runtime.root)==before
    assert runtime.events==[] and not runtime.held


def test_ssh_stages_only_beside_verified_local_model_and_proof_callback_acquires_fresh_transaction(runtime):
    previous=deepcopy(runtime.native)
    result=configure(runtime,ssh=True)
    assert result['staged'] and not result['applied']
    assert runtime.events[-2:]==['prove-previous','finish-rollback']
    assert 'apply' not in runtime.events and runtime.native==previous and not runtime.held
    result=_mod._record_remote_provider_egress_probe(runtime.helpers._egress_probe_response())
    assert result['recorded'] and result['activation']['proven']
    assert len(runtime.transactions)==2 and runtime.events[-1]=='finish-commit'
    assert 'routeFingerprint' in runtime.native and not runtime.held


def test_ssh_proof_callback_retains_admitted_state_on_ambiguous_finish(runtime):
    configure(runtime,ssh=True)
    runtime.failure='finish'
    with pytest.raises(_mod._PixelModelTransactionUncertain):
        _mod._record_remote_provider_egress_probe(runtime.helpers._egress_probe_response())
    saved=json.loads(_mod._remote_provider_route_state_path().read_text())
    assert saved['status']['proven'] and runtime.held
    assert _mod._remote_provider_activation_public_path().exists()


def test_ssh_callback_preserves_committed_route_if_final_host_journal_write_fails(runtime):
    configure(runtime,ssh=True)
    runtime.failure='final-journal'
    with pytest.raises(_mod._PixelModelTransactionUncertain,match='final recovery receipt'):
        _mod._record_remote_provider_egress_probe(runtime.helpers._egress_probe_response())
    saved=json.loads(_mod._remote_provider_route_state_path().read_text())
    assert saved['status']['proven'] and not runtime.held
    assert runtime.transactions[-1].completed
    assert _mod._remote_provider_activation_public_path().exists()
    assert runtime.native['routeFingerprint']==_mod._remote_provider_route_fingerprint(saved)
    assert 'restore-container' not in runtime.events


def test_ssh_staging_requires_fresh_local_proof_before_any_mutation(runtime,monkeypatch):
    before=files(runtime.root)
    monkeypatch.setattr(_mod,'_prove_pixel_model_contract',lambda *_:False)
    with pytest.raises(_mod._RemoteProviderApplyError,match='current local model must be verified'):
        configure(runtime,ssh=True)
    assert files(runtime.root)==before and not runtime.held
    assert runtime.events==[]


def test_partial_container_restart_failure_restores_only_under_owned_hold(runtime,monkeypatch):
    before=files(runtime.root)
    def partial_restart(*_args,**_kwargs):
        assert runtime.held
        runtime.events.append('partial-restart')
        raise RuntimeError('restart failed after container changed')
    monkeypatch.setattr(_mod,'_restart_existing_container',partial_restart)
    with pytest.raises(_mod._RemoteProviderApplyError,match='restart failed'):
        configure(runtime)
    assert files(runtime.root)==before and not runtime.held
    assert runtime.events.index('verify-held')<runtime.events.index('restore-container')
    assert runtime.events[-2:]==['prove-previous','finish-rollback']


def test_failed_previous_route_proof_does_not_release_maintenance(runtime,monkeypatch):
    runtime.failure='route'
    monkeypatch.setattr(_mod,'_prove_pixel_model_contract',lambda *_:False)
    with pytest.raises(_mod._PixelModelTransactionUncertain,match='Previous provider route could not be proved'):
        configure(runtime)
    assert runtime.held
    assert 'restore-container' in runtime.events and 'finish-rollback' not in runtime.events


def test_failed_egress_restore_does_not_release_maintenance(runtime,monkeypatch):
    runtime.failure='route'
    def fail_restore(*_):raise OSError('restore failed')
    monkeypatch.setattr(_mod,'_restore_remote_provider_snapshots',fail_restore)
    with pytest.raises(_mod._PixelModelTransactionUncertain,match='Previous provider files could not be restored'):
        configure(runtime)
    assert runtime.held and 'finish-rollback' not in runtime.events
