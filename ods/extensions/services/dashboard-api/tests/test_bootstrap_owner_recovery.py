"""Interrupted legacy bootstrap: production host, bridge and shell orchestration.

Only installed source/service/native/inference boundaries are simulated. No
Docker socket, network, actual gateway or model is used by these controls.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest
import test_model_activate as fixtures

host = fixtures._mod
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / 'tests'))
from test_pixel_model_transition import FakeBridge
import pixel_access_bridge
from pixel_access_bridge import AccessError, atomic_json
import pixel_access_client
import pixel_model_coordinator
import pixel_model_transition
import pixel_bootstrap_recovery as snapshots
import model_lifecycle_lock as locks
from pixel_model_contract import plan, projection

TARGET = dict(gguf='full.gguf', model='full-model', context=65536)


def native_config(model='bootstrap'):
    return {'agents': {'list': [{'id': 'pixel', 'model': 'ods-gateway/ods/current'}], 'defaults': {}},
            'models': {'providers': {'ods-gateway': {'models': [dict(id='ods/current',
                name=f'ODS Current ({model})', contextWindow=65536, maxTokens=8192, reasoning=False)]}}},
            'plugins': {'entries': {'pixel-ods': {'enabled': True, 'config': {}}}}}


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    install = tmp_path / 'install'; install.mkdir(); (install / 'data').mkdir()
    home = tmp_path / 'home'; (home / '.openclaw').mkdir(parents=True)
    native = home / '.openclaw/openclaw.json'
    snapshots.write(native, json.dumps(native_config()).encode())
    env = dict(ODS_MODE='local', GGUF_FILE=TARGET['gguf'], LLM_MODEL=TARGET['model'],
               MAX_CONTEXT='65536', CTX_SIZE='65536', PIXEL_OPENWEBUI_KEY='fixture')
    snapshots.write(install / '.env', '\n'.join(f'{k}={v}' for k, v in env.items()).encode())

    class Bridge(FakeBridge):
        def worker(self, operation='status', **kwargs):
            return dict(configured_status='sandboxed', config_sha256=hashlib.sha256(native.read_bytes()).hexdigest())
        def inspect(self, **kwargs):
            self.config = self.worker()
            return super().inspect(**kwargs)
        def unit_boundary(self): return 'fixture-unit'
        def verify_held_mode(self, token, mode):
            super().verify_held_mode(token, mode)
            atomic_json(self.state / 'verified.json', dict(pid=self.native_state['pid'],
                proof={}, config_sha256=self.worker()['config_sha256'], boundary=self.unit_boundary()))

    bridge = Bridge(tmp_path / 'root-state')
    # The immutable API image runs unprivileged. Simulate root identity only
    # at the privileged adapter boundary; retain real file/mode validation.
    real_private = pixel_access_bridge.private_json
    actual_uid = os.geteuid()
    monkeypatch.setattr(pixel_access_bridge, 'os', SimpleNamespace(**{**vars(os), 'geteuid': lambda: 0}))
    monkeypatch.setattr(pixel_access_bridge, 'private_json', lambda path, uid, *a, **k:
        real_private(path, actual_uid if Path(path).is_relative_to(bridge.state) and uid == 0 else uid, *a, **k))
    bridge.install = install
    bridge.owner = SimpleNamespace(pw_uid=os.geteuid(), pw_dir=str(home))
    monkeypatch.setattr(pixel_model_coordinator, '_config', lambda _: (json.loads(native.read_bytes()),
                         hashlib.sha256(native.read_bytes()).hexdigest()))
    def request(operation, body=None):
        try:
            if operation == 'model-status': return 200, bridge.model_status()
            if operation == 'model-recovery-prepare': return 200, bridge.model_recovery_prepare(body)
            if operation == 'installer-model-verify': return 200, bridge.verify_installer_model_access(body['transaction_id'])
            if operation == 'model-finish': return 200, bridge.model_finish(body)
            if operation == 'model-bootstrap-begin': return 200, bridge.model_begin(bootstrap=body['transaction_id'])
            raise AssertionError(operation)
        except AccessError as exc:
            return 409, {'error': exc.code}
    monkeypatch.setattr(pixel_model_transition, '_load_request_access', lambda: request)
    monkeypatch.setattr(pixel_access_client, 'request_access', request)
    monkeypatch.setattr(host, 'INSTALL_DIR', install)
    monkeypatch.setattr(host, '_ods_managed_pixel_identity', lambda: ('owner', home))
    monkeypatch.setattr(host, '_pixel_model_config_paths', lambda: {'.env': install / '.env'})
    monkeypatch.setattr(host.platform, 'system', lambda: 'Linux')
    proofs = []
    monkeypatch.setattr(host, '_prove_pixel_model_contract', lambda _, c: proofs.append(c) is None
                        and c['model'] == TARGET['model'] and c['contextLength'] == TARGET['context'])
    monkeypatch.setattr(host, '_verify_litellm_route', lambda *a, **k: None)
    return SimpleNamespace(bridge=bridge, install=install, home=home, native=native,
                           env=env, request=request, proofs=proofs, monkeypatch=monkeypatch)


def shell_reconcile(r, model, context, **kwargs):
    """Execute production reconciliation; stub only physical runtime/source work."""
    tx = kwargs['borrowed_transaction']
    candidate = r.install / 'candidate.json'
    config = plan(json.loads(r.native.read_bytes()), dict(model=model, contextLength=context,
        maxTokens=kwargs['max_tokens'], reasoning=kwargs['reasoning'], imageInput='unknown'))
    snapshots.write(candidate, json.dumps(config).encode())
    source = (ROOT / 'installers/lib/pixel-host-install.sh').read_text()
    function = re.search(r'^ods_pixel_reconcile_promoted_model\(\) \{\n.*?^}', source, re.M | re.S).group()
    script = r'''
set -eu
INSTALL_DIR=$1; home=$2; native=$3; candidate_fixture=$4; transaction=$5; selected_model=$6; selected_context=$7
events="$INSTALL_DIR/events"
_ods_pixel_model_transition() { cat "$INSTALL_DIR/status.json"; }
_ods_pixel_managed_source_ref() { printf 'a%.0s' {1..40}; }
_ods_pixel_reconciliation_source_url() { echo bundled; }
_ods_pixel_source_checkout() { echo "$INSTALL_DIR/source"; }
_ods_pixel_openclaw_bin() { echo /bin/true; }
_ods_pixel_uses_stable_model_alias() { return 0; }
_ods_pixel_stable_alias_matches_promoted_model() { return 0; }
_ods_pixel_contract_sha256() { echo fixture; }
_ods_pixel_managed_contract_matches() { return 0; }
_ods_pixel_search_provider_matches_contract() { return 0; }
_ods_pixel_model_reconciliation_snapshot() { cp "$native" "$INSTALL_DIR/native-before"; echo "$INSTALL_DIR/backup"; }
_ods_pixel_update_onboarding_model() { echo onboarding >> "$events"; }
_ods_pixel_stage_stable_alias_candidate() { echo "$candidate_fixture"; }
_ods_pixel_apply_runtime_budget() { :; }
_ods_pixel_candidate_is_managed_runtime_update() { :; }
_ods_pixel_candidate_config_matches_live() { cmp -s "$candidate_fixture" "$native"; }
_ods_pixel_atomic_replace_managed_file() { cp "$3" "$4"; echo publish >> "$events"; }
_ods_pixel_install_onboarding_mirror() { :; }
_ods_pixel_recreate_agent_sandbox() { :; }
_ods_pixel_restart_gateway_and_verify() { echo restart-and-verify >> "$events"; test "$4" = "$transaction"; }
_ods_pixel_reverify_unless_model_held() { :; }
_ods_pixel_restart_ingress_and_verify() { :; }
_ods_pixel_mark_ready() { :; }
ods_pixel_run_as_owner() { shift 2; "$@"; }
'''
    (r.install / 'status.json').write_text(json.dumps(r.bridge.model_status()))
    script += function + '\nods_pixel_reconcile_promoted_model owner "$home" "$selected_model" ready "$selected_context" 8192 false "" "$transaction"\n'
    result = subprocess.run(['bash', '-c', script, 'fixture', str(r.install), str(r.home),
                             str(r.native), str(candidate), tx, model, str(context)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert 'restart-and-verify' in (r.install / 'events').read_text()
    return 'reconciled'


@pytest.mark.parametrize('point', ['before-rewrite', 'after-rewrite'])
def test_real_bridge_and_shell_repair_legacy_process_death(recovery, point):
    r = recovery
    tx = r.bridge.model_begin()['transaction_id']
    original_sha = r.bridge.pending()['start_config_sha256']
    if point == 'after-rewrite':
        # Reproduce death after config publication, before gateway restart.
        changed = plan(json.loads(r.native.read_bytes()), dict(model=TARGET['model'], contextLength=65536,
                         maxTokens=8192, reasoning=False, imageInput='unknown'))
        child = subprocess.Popen([sys.executable, '-c',
            'import os,signal,sys;from pathlib import Path;Path(sys.argv[1]).write_text(sys.argv[2]);os.kill(os.getpid(),signal.SIGKILL)',
            str(r.native), json.dumps(changed)])
        child.wait(timeout=5)
        assert child.returncode < 0
        assert hashlib.sha256(r.native.read_bytes()).hexdigest() != original_sha
    assert host._pixel_model_recovery_status()['pending'] is True
    assert host._recover_pixel_model_transaction(r.env)['pending'] is True
    r.monkeypatch.setattr(host, '_reconcile_ods_managed_pixel_model', lambda *a, **k: shell_reconcile(r, *a, **k))
    result = host._recover_bootstrap_pixel_transaction(tx, TARGET)
    assert result['pending'] is False
    assert projection(json.loads(r.native.read_bytes()))['contract']['model'] == TARGET['model']
    assert r.bridge.model_status() == {'pending': False}
    # The root completion keeps the original transaction, and retries use it.
    assert r.bridge.model_completion()['transaction_id'] == tx
    assert host._recover_bootstrap_pixel_transaction(tx, TARGET)['pending'] is False
    assert len(r.proofs) >= 2


@pytest.mark.parametrize('failure', ['inference', 'source', 'release', 'wrong-id', 'stale-target', 'gateway'])
def test_refusals_preserve_journal_and_admission(recovery, failure):
    r = recovery; tx = r.bridge.model_begin()['transaction_id']
    if failure == 'inference': r.monkeypatch.setattr(host, '_prove_pixel_model_contract', lambda *_: False)
    if failure == 'source': (r.bridge.state / 'source-upgrade').mkdir()
    if failure == 'release': atomic_json(r.bridge.state / 'release-intent.json',
                                        dict(transactionId=tx, candidateSha256='f' * 64))
    if failure == 'wrong-id': tx = '0' * 64
    chosen = {**TARGET, 'model': 'other'} if failure == 'stale-target' else TARGET
    if failure == 'gateway':
        r.bridge.fail_verify = True
        r.monkeypatch.setattr(host, '_reconcile_ods_managed_pixel_model', lambda *a, **k: shell_reconcile(r, *a, **k))
    else:
        r.monkeypatch.setattr(host, '_reconcile_ods_managed_pixel_model', lambda *a, **k: pytest.fail('unexpected native mutation'))
    with pytest.raises((RuntimeError, AccessError, ValueError)):
        host._recover_bootstrap_pixel_transaction(tx, chosen)
    assert r.bridge.pending() is not None
    assert r.bridge.edge_state['phase'] == 'held'


@pytest.mark.parametrize('point', ['native-release', 'edge-release', 'finish-reply'])
def test_release_interruptions_resume_original_transaction(recovery, point):
    r = recovery; tx = r.bridge.model_begin()['transaction_id']
    r.monkeypatch.setattr(host, '_reconcile_ods_managed_pixel_model', lambda *a, **k: shell_reconcile(r, *a, **k))
    if point == 'native-release': r.bridge.fail_native = 'release'
    if point == 'edge-release': r.bridge.fail_edge = 'release'
    if point == 'finish-reply':
        original = r.request
        def lost(operation, body=None):
            result = original(operation, body)
            if operation == 'model-finish': raise OSError('reply lost')
            return result
        r.monkeypatch.setattr(pixel_access_client, 'request_access', lost)
    with pytest.raises((RuntimeError, OSError)):
        host._recover_bootstrap_pixel_transaction(tx, TARGET)
    r.bridge.fail_native = r.bridge.fail_edge = None
    r.monkeypatch.setattr(pixel_access_client, 'request_access', r.request)
    assert host._recover_bootstrap_pixel_transaction(tx, TARGET)['pending'] is False


def test_bootstrap_intent_precedes_gates_and_survives_lost_begin(recovery):
    r = recovery
    tx = snapshots.prepare(r.install, TARGET)
    gate = r.bridge.edge
    def edge(*args, **kwargs):
        if args and args[0] == 'drain':
            assert r.bridge.bootstrap_intent(tx)['target'] == TARGET
            assert json.loads((r.bridge.state / 'bootstrap-native-before.json').read_bytes())['transactionId'] == tx
        return gate(*args, **kwargs)
    r.monkeypatch.setattr(r.bridge, 'edge', edge)
    r.bridge.model_begin(bootstrap=tx)  # caller loses reply here
    status = pixel_model_transition.execute('status')
    assert status['transaction_id'] == tx and status['bootstrap']['target'] == TARGET
    with pytest.raises(AccessError): r.bridge.model_begin(bootstrap=tx)
    assert r.bridge.pending()['transaction_id'] == tx


@pytest.mark.parametrize('change', ['snapshot', 'manifest', 'current', 'symlink'])
def test_stale_snapshots_never_become_recovery_authority(recovery, change):
    r = recovery; tx = snapshots.prepare(r.install, TARGET)
    folder = snapshots.directory(r.install, tx)
    if change == 'snapshot': (folder / 'env').write_bytes(b'stale')
    if change == 'manifest': (folder / 'manifest.json').write_bytes(b'{}')
    if change == 'current': (r.install / '.env').write_bytes(b'changed')
    if change == 'symlink':
        (folder / 'env').unlink(); (folder / 'env').symlink_to(r.install / '.env')
    with pytest.raises(AccessError): r.bridge.model_begin(bootstrap=tx)
    assert r.bridge.pending() is None
    assert r.bridge.edge_state['phase'] == 'idle'


@pytest.mark.parametrize('evidence', ['missing', 'completed', 'unavailable', 'malformed'])
def test_host_status_never_calls_legacy_hold_idle(recovery, evidence):
    r = recovery; r.bridge.model_begin()
    if evidence == 'completed':
        r.monkeypatch.setattr(host, '_read_pixel_model_journal', lambda: {'phase': 'completed', 'transactionId': '0' * 64})
    if evidence in ('unavailable', 'malformed'):
        r.monkeypatch.setattr(pixel_access_client, 'request_access',
            lambda *_: (503, {'error': 'unavailable'}) if evidence == 'unavailable' else (200, {'pending': 'false'}))
    result = host._pixel_model_recovery_status()
    assert result['pending'] is True
    assert result['phase'] == ('unavailable' if evidence in ('unavailable', 'malformed') else 'held')


def test_shared_shell_lock_refuses_host_and_dies_with_parent(tmp_path, monkeypatch):
    install = tmp_path / 'install'; install.mkdir()
    monkeypatch.setenv('ODS_MODEL_LIFECYCLE_LOCK_ROOT', str(tmp_path / 'locks'))
    monkeypatch.setattr(host, 'INSTALL_DIR', install)
    shell = ROOT / 'installers/lib/model-lifecycle-lock.sh'
    child = subprocess.Popen(['bash', '-c', 'set -e; . "$1"; ods_model_lifecycle_lock_acquire "$2" fixture; echo READY; read -r _',
                              'fixture', str(shell), str(install)], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'READY'  # diagnostics go to stderr
        acquired, _ = host._begin_model_lifecycle('model_recovery')
        assert acquired is False
        child.kill(); child.wait(timeout=3)
        deadline = time.monotonic() + 3
        while True:
            try: fd = locks.acquire(install); break
            except BlockingIOError:
                if time.monotonic() >= deadline: raise
                time.sleep(.05)
        locks.release(fd)
    finally:
        if child.poll() is None: child.kill(); child.wait(timeout=3)


@pytest.mark.parametrize('hostile', ['directory-symlink', 'file-symlink', 'hardlink', 'fifo', 'public-directory'])
def test_lifecycle_lock_refuses_hostile_precreation_without_chown(tmp_path, monkeypatch, hostile):
    install = tmp_path / 'install'; install.mkdir()
    root = tmp_path / 'locks'; other = tmp_path / 'other'; other.mkdir()
    monkeypatch.setenv('ODS_MODEL_LIFECYCLE_LOCK_ROOT', str(root))
    protected = other / 'sentinel'; protected.write_text('unchanged'); protected.chmod(0o600)
    before = protected.stat()
    if hostile == 'directory-symlink': root.symlink_to(other, target_is_directory=True)
    else:
        root.mkdir(mode=0o700)
        lock = root / f'ods-model-lifecycle-{locks.key_for(install)}.lock'
        if hostile == 'file-symlink': lock.symlink_to(protected)
        if hostile == 'hardlink': os.link(protected, lock)
        if hostile == 'fifo': os.mkfifo(lock, 0o600)
        if hostile == 'public-directory': root.chmod(0o777)
    with pytest.raises((OSError, RuntimeError)): locks.acquire(install)
    assert protected.read_text() == 'unchanged'
    assert (protected.stat().st_uid, protected.stat().st_mode) == (before.st_uid, before.st_mode)


def test_checksum_matches_existing_shell_lock_identity(tmp_path):
    result = subprocess.run(['cksum'], input=os.fsencode(tmp_path) + b'\0', capture_output=True, check=True)
    assert result.stdout.split()[0].decode() == locks.key_for(tmp_path)


def test_http_busy_and_invalid_target_dispatch_no_work(monkeypatch):
    monkeypatch.setattr(host, 'check_auth', lambda _: True)
    monkeypatch.setattr(host, '_recover_bootstrap_pixel_transaction', lambda *a: pytest.fail('recovery dispatched'))
    for target, expected in ((TARGET, 409), ({**TARGET, 'context': True}, 400)):
        monkeypatch.setattr(host, 'read_json_body', lambda _: {'bootstrapTransactionId': 'a' * 64, 'target': target})
        monkeypatch.setattr(host, '_begin_model_lifecycle', lambda _: (False, {}))
        handler = fixtures._ResponseHandler()
        host.AgentHandler._handle_model_recover(handler)
        assert handler.response_code == expected


def test_before_inference_death_can_complete_the_proven_bootstrap_route(recovery):
    r = recovery
    old = dict(gguf='bootstrap.gguf', model='bootstrap', context=65536)
    r.env.update(GGUF_FILE=old['gguf'], LLM_MODEL=old['model'])
    snapshots.write(r.install / '.env', '\n'.join(f'{k}={v}' for k,v in r.env.items()).encode())
    tx = snapshots.prepare(r.install, TARGET)
    r.bridge.model_begin(bootstrap=tx)
    # The intended target remains full, but the explicit current target is old.
    assert host._pixel_model_recovery_status()['currentTarget'] == old
    r.monkeypatch.setattr(host, '_prove_pixel_model_contract', lambda _, c: c['model'] == old['model'])
    r.monkeypatch.setattr(host, '_reconcile_ods_managed_pixel_model', lambda *a, **k: shell_reconcile(r, *a, **k))
    assert host._recover_bootstrap_pixel_transaction(tx, old)['pending'] is False
    assert r.bridge.model_completion()['transaction_id'] == tx


def test_changed_bound_snapshot_refuses_native_repair(recovery):
    r = recovery; tx = snapshots.prepare(r.install, TARGET)
    r.bridge.model_begin(bootstrap=tx)
    (snapshots.directory(r.install, tx) / 'env').write_bytes(b'stale')
    before = r.native.read_bytes()
    with pytest.raises(RuntimeError): host._recover_bootstrap_pixel_transaction(tx, TARGET)
    assert r.native.read_bytes() == before and r.bridge.pending() is not None


@pytest.mark.skipif(os.geteuid() != 0, reason='separate root isolated-container control')
def test_root_refuses_attacker_owned_directory_and_never_chowns_symlink_target(tmp_path, monkeypatch):
    install = tmp_path / 'install'; install.mkdir()
    root = tmp_path / 'locks'; root.mkdir(mode=0o700)
    os.chown(root, 12345, -1)
    monkeypatch.setenv('ODS_MODEL_LIFECYCLE_LOCK_ROOT', str(root))
    with pytest.raises(RuntimeError): locks.acquire(install)
    assert root.stat().st_uid == 12345
    root.rmdir()
    other = tmp_path / 'attacker-target'; other.mkdir(mode=0o700); os.chown(other, 12345, -1)
    root.symlink_to(other, target_is_directory=True)
    with pytest.raises(OSError): locks.acquire(install)
    assert other.stat().st_uid == 12345


def test_missing_install_is_unavailable_not_an_active_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setattr(host, 'INSTALL_DIR', tmp_path / 'absent')
    acquired, result = host._begin_model_lifecycle('model_recovery')
    assert acquired is False and result['operation'] is None
    assert host._model_lifecycle_conflict('recovery', result)['code'] == 'model_lifecycle_lock_unavailable'
    # The failed filesystem acquisition must not retain the threading lock.
    assert host._model_lifecycle_lock.acquire(blocking=False)
    host._model_lifecycle_lock.release()


def test_owner_host_uses_root_authenticated_transport_without_installed_cli_loader(recovery):
    r = recovery
    r.monkeypatch.setattr(pixel_model_transition, '_load_request_access',
                         lambda: pytest.fail('owner checkout cannot satisfy installed CLI custody'))
    tx = r.bridge.model_begin()['transaction_id']
    assert host._pixel_model_recovery_status()['transactionId'] == tx
    r.monkeypatch.setattr(host, '_reconcile_ods_managed_pixel_model', lambda *a, **k: shell_reconcile(r, *a, **k))
    assert host._recover_bootstrap_pixel_transaction(tx, TARGET)['pending'] is False
