"""Real Root publication FDs/renames and owned child control on Linux.

Darwin account/ACL/process metadata and launchctl observations are simulated.
No service mutation, Source/provider/SDK/model execution or physical Mac claim.
"""
# ruff: noqa: E402,F401,F811
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

if sys.platform != 'linux' or not Path('/proc/self/fd').is_dir():
    pytest.skip('Linux/procfs metadata simulation, not physical Darwin', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_source_active_publication import active, journal, gate
from test_native_source_generation import generation, SPECS
from test_native_source_custody import native
from test_connection_deployment_paths import deployment
from test_native_source_runtime_publication import built, source, package
from test_native_source_publication import material, publication, helper
from pixel_connections import custody
import pixel_access_bridge as bridge


@pytest.fixture(autouse=True)
def qualification_operation(deployment):
    # The fixture stages and exercises one disposable Root deployment. Keep its
    # real typed operation scope across preparation too; every namespace, ACL,
    # inode and independent bundle check still runs. The separate publisher/cache
    # suite covers cold calls and scope revocation on each public entry point.
    with deployment.bridge._native_runtime_operation():
        yield


@pytest.fixture
def lifecycle(active, monkeypatch):
    n = active
    # Only the external test driver substitutes Darwin's raw clock. Actual
    # bounded scopes, sealed lease revocation and production readers remain.
    monkeypatch.setattr(bridge, '_native_source_clock_ns', lambda: n.now)
    # Qualifying the synthetic full bundle is deliberately slow. The separate
    # deadline suite covers both real scope clocks; this fixture advances raw
    # time explicitly and avoids treating test-machine speed as Mac evidence.
    monkeypatch.setattr(bridge, 'time', SimpleNamespace(monotonic=lambda: 1000.0, sleep=time.sleep))
    original_mkdir = os.mkdir
    def mkdir(path, *args, **kwargs):
        original_mkdir(path, *args, **kwargs)
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            **({'dir_fd': kwargs['dir_fd']} if 'dir_fd' in kwargs else {}))
        try:
            if Path(os.readlink('/proc/self/fd/' + str(fd))).is_relative_to(n.world):
                info = n.real_fstat(fd)
                # The kernel allocated a NEW directory inode. Discard only
                # metadata left in the test simulation by a removed old file.
                n.owners.pop((info.st_dev, info.st_ino), None)
                n.acl.acls.pop((info.st_dev, info.st_ino), None)
        finally:
            os.close(fd)
    monkeypatch.setattr(os, 'mkdir', mkdir)
    with n.f.bridge._native_runtime_operation():
        n.first = helper.publish_active_slot(n.selected, 'sync')
        n.nonce = n.first['invocation']['nonce']
        n.definition = n.units / (n.first['label'] + '.plist')
        n.definition_image = (n.definition.read_bytes(), n.definition.stat().st_ino)
        yield n


def advance(n, phase, *, nonce=None, role='sync', identifier=None):
    return helper.transition_active_slot(n.selected, role, identifier,
        expected_nonce=n.nonce if nonce is None else nonce, phase=phase)


def rearm(n, *, nonce=None, role='sync', identifier=None, seconds=120):
    with n.f.bridge.bounded(seconds):
        return helper.rearm_active_slot(n.selected, role, identifier,
            expected_nonce=n.nonce if nonce is None else nonce)


def private_files(n):
    return {str(p.relative_to(n.world)): p.read_bytes()
        for p in n.world.rglob('*') if p.is_file() and not p.is_symlink()}


def test_real_owned_child_quiesce_revokes_before_stop_and_rearm_keeps_definition(lifecycle, monkeypatch):
    n = lifecycle
    process = subprocess.Popen([sys.executable, '-I', '-S', '-c', 'import time; time.sleep(600)'],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    command = n.f.bridge.command
    def observed(argv, **kwargs):
        if process.poll() is None:
            if argv == ['/bin/ps', '-axo', 'pid=,uid=']:
                return f'{process.pid} {n.identity["id"]}\n'
            if argv == ['/bin/launchctl', 'print', 'system']:
                return 'system = {\n services = {\n ' + n.first['label'] + ' = {}\n }\n}\n'
        return command(argv, **kwargs)
    monkeypatch.setattr(n.f.bridge, 'command', observed)
    try:
        assert advance(n, 'running')['phase'] == 'running'
        old = custody._native_invocation(n.first['invocation'])
        assert custody._native_slot_matches(custody._native_broker_projection(gate()), old, running=True)
        n.now += 301 * 10**9  # Revocation must work after both original deadlines.
        assert advance(n, 'quiescing')['phase'] == 'quiescing'
        assert not custody._native_slot_matches(custody._native_broker_projection(gate()), old, running=True)
        assert process.poll() is None  # Publication did not signal or replay it.
        before = private_files(n)
        with pytest.raises(ValueError, match='(?:quiesce-required|loaded-job-preserved)'):
            advance(n, 'retired')
        assert private_files(n) == before
        process.terminate()
        process.wait(timeout=5)
        assert advance(n, 'retired')['phase'] == 'retired'
        next_run = rearm(n, seconds=7)
        assert next_run['phase'] == 'prepared' and next_run['invocation']['nonce'] != n.nonce
        assert 0 < int(next_run['invocation']['admissionDeadlineNs']) - n.now <= 7 * 10**9
        assert int(next_run['invocation']['workerDeadlineNs']) - n.now == 300 * 10**9
        assert (n.definition.read_bytes(), n.definition.stat().st_ino) == n.definition_image
        before = private_files(n)
        n.now += 9 * 10**9
        assert rearm(n)['invocation'] == next_run['invocation']  # Lost reply never renews.
        assert private_files(n) == before
        with pytest.raises(ValueError, match='nonce-changed'):
            advance(n, 'running')
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def test_repeated_sync_runs_archive_exact_evidence_without_growing_live_slot_inventory(lifecycle):
    n = lifecycle
    for _ in range(4):
        advance(n, 'running')
        advance(n, 'completed')
        result = rearm(n)
        n.nonce = result['invocation']['nonce']
        n.now += 10**9
    current = journal(n)
    assert len(current['current']['definitions']) == 2  # original sync/direct only
    assert current['retained'] == {} and current['history']['count'] == 12
    assert current['history']['pending'] is None
    history = n.metadata / 'native-source-jobs/history'
    receipts = list(history.glob('*/receipt.json'))
    assert len(receipts) == 12
    for path in receipts:
        receipt = json.loads(path.read_bytes())
        assert receipt['kind'] == 'ods-native-source-invocation-history'
        for name, image in receipt['files'].items():
            old = path.parent / name
            assert helper.sha(old.read_bytes()) == image['sha256']
            assert [old.stat().st_dev, old.stat().st_ino] == image['inode']
    assert (n.definition.read_bytes(), n.definition.stat().st_ino) == n.definition_image
    assert not any(argv[1] in ('bootstrap', 'kickstart', 'enable', 'bootout') for argv in n.commands)


def test_sibling_invocation_and_deadlines_survive_all_lifecycle_commits(lifecycle):
    n = lifecycle
    sibling = helper.publish_active_slot(n.selected, 'action', 'calendar-1893456789012-12abcdef')
    row = copy.deepcopy(gate()['launch']['invocations'][sibling['label']])
    definition = copy.deepcopy(gate()['launch']['definitions'][sibling['label']])
    advance(n, 'unknown')
    advance(n, 'quiescing')
    advance(n, 'retired')
    rearm(n)
    value = gate()
    assert value['launch']['invocations'][sibling['label']] == row
    assert value['launch']['definitions'][sibling['label']] == definition
    assert custody._native_slot_matches(custody._native_broker_projection(value), custody._native_invocation(sibling['invocation']))


@pytest.mark.parametrize('role,identifier', [
    ('action', 'calendar-1893456789012-12abcdef'),
    ('reconcile', 'calendar-1893456789012-12abcdef'),
    ('exchange', 'abcdef0123456789abcdef0123456789'), ('revoke', None)])
def test_effect_oneshots_cannot_be_rearmed_even_after_quiescence(lifecycle, role, identifier):
    n = lifecycle
    result = helper.publish_active_slot(n.selected, role, identifier)
    nonce = result['invocation']['nonce']
    advance(n, 'retired', nonce=nonce, role=role, identifier=identifier)
    before = private_files(n)
    with pytest.raises(ValueError, match='rearm-refused'):
        rearm(n, nonce=nonce, role=role, identifier=identifier)
    assert private_files(n) == before


def test_rearm_requires_actual_bounded_root_scope_and_retirement(lifecycle):
    n = lifecycle
    before = private_files(n)
    with pytest.raises(ValueError, match='rearm-refused'):
        rearm(n)
    assert private_files(n) == before
    advance(n, 'retired')
    before = private_files(n)
    with pytest.raises(bridge.AccessError, match='native-source-deadline-unavailable'):
        helper.rearm_active_slot(n.selected, 'sync', expected_nonce=n.nonce)
    # An absent clock/scope cannot mint even a durable new invocation intent.
    assert private_files(n) == before
    assert gate()['launch']['invocations'][n.first['label']]['invocation']['nonce'] == n.nonce


def test_direct_rearm_binds_both_deadlines_to_root_ceiling(lifecycle):
    n = lifecycle
    first = helper.publish_active_slot(n.selected, 'direct')
    nonce = first['invocation']['nonce']
    advance(n, 'retired', nonce=nonce, role='direct')
    result = rearm(n, nonce=nonce, role='direct', seconds=7)
    invocation = result['invocation']
    assert int(invocation['workerDeadlineNs']) == int(invocation['admissionDeadlineNs']) == n.now + 7 * 10**9
    assert invocation['nonce'] != nonce


@pytest.mark.parametrize('where', ['archive-prefix', 'archive-rename', 'archive-commit',
    'manifest-old', 'manifest-new', 'before-gate', 'after-gate', 'metadata-commit'])
def test_lost_reply_resumes_exact_lifecycle_images_nonce_and_archive(lifecycle, monkeypatch, where):
    n = lifecycle
    actual_save, actual_rename, actual_replace = custody._native_save_at, os.rename, os.replace
    hit = []
    def save(selection, directory, name, value, **kwargs):
        result = actual_save(selection, directory, name, value, **kwargs)
        if name == 'source-jobs-active.json' and not hit:
            history, txn = value.get('history', {}), value.get('transaction')
            selected = (where == 'archive-prefix' and history.get('pending') is not None
                and history['pending']['image']['inode'] is not None and not history['pending']['image']['ready']
                or where == 'archive-commit' and history.get('count') == 1 and txn is None
                or where == 'metadata-commit' and value['phase'] == 'committed' and txn and txn.get('change'))
            if selected:
                hit.append(True)
                raise OSError('invented lifecycle lost acknowledgement')
        return result
    def rename(first, second, **kwargs):
        result = actual_rename(first, second, **kwargs)
        selected = (where == 'archive-rename' and first == second and str(first).startswith('.active-')
            or where == 'manifest-old' and first != second and str(second).endswith('.manifest-old')
            or where == 'manifest-new' and first != second and str(first).endswith('.manifest'))
        if selected and not hit:
            hit.append(True)
            raise OSError('invented lifecycle rename acknowledgement')
        return result
    def replace(first, second, **kwargs):
        selected = second == 'custody.json' and where in ('before-gate', 'after-gate') and not hit
        if selected and where == 'before-gate':
            hit.append(True)
            raise OSError('invented lifecycle before Gate')
        result = actual_replace(first, second, **kwargs)
        if selected:
            hit.append(True)
            raise OSError('invented lifecycle Gate acknowledgement')
        return result
    monkeypatch.setattr(custody, '_native_save_at', save)
    monkeypatch.setattr(os, 'rename', rename)
    monkeypatch.setattr(os, 'replace', replace)
    with pytest.raises(OSError, match='invented lifecycle'):
        advance(n, 'quiescing')
    assert hit
    before = journal(n)
    n.now += 301 * 10**9
    monkeypatch.setattr(custody, '_native_save_at', actual_save)
    monkeypatch.setattr(os, 'rename', actual_rename)
    monkeypatch.setattr(os, 'replace', actual_replace)
    result = advance(n, 'quiescing')
    assert result['invocation'] == n.first['invocation'] and result['phase'] == 'quiescing'
    assert journal(n)['phase'] == 'committed' and journal(n)['history']['pending'] is None
    if before['transaction'] and before['transaction'].get('change'):
        assert journal(n)['transaction']['change'] == before['transaction']['change']
        for key, image in before['transaction']['images'].items():
            if image['inode'] is not None:
                assert journal(n)['transaction']['images'][key]['inode'] == image['inode']


def test_revocation_cannot_rollback_to_previous_dispatch_authority(lifecycle):
    n = lifecycle
    advance(n, 'quiescing')
    before = private_files(n)
    with pytest.raises(ValueError, match='lifecycle-rollback-refused'):
        helper.publish_active_slot(n.selected, 'sync', rollback=True)
    assert private_files(n) == before


@pytest.mark.parametrize('where', ['before-gate', 'after-gate'])
def test_rearm_recovery_keeps_original_deadline_and_explicit_abort_restores_only_revoked_gate(lifecycle, monkeypatch, where):
    n = lifecycle
    advance(n, 'retired')
    revoked = copy.deepcopy(gate())
    original = os.replace
    hit = []
    def replace(first, second, **kwargs):
        selected = second == 'custody.json' and not hit
        if selected and where == 'before-gate':
            hit.append(True)
            raise OSError('invented rearm before Gate')
        result = original(first, second, **kwargs)
        if selected:
            hit.append(True)
            raise OSError('invented rearm Gate acknowledgement')
        return result
    monkeypatch.setattr(os, 'replace', replace)
    with pytest.raises(OSError, match='invented rearm'):
        rearm(n, seconds=7)
    pending = journal(n)['transaction']['invocation']
    assert hit and pending['nonce'] != n.nonce
    n.now = int(pending['admissionDeadlineNs']) + 1
    monkeypatch.setattr(os, 'replace', original)
    if where == 'before-gate':
        with pytest.raises(ValueError, match='admission-expired'):
            rearm(n)
        assert gate() == revoked
    else:
        assert rearm(n)['invocation'] == pending  # Already committed Gate, no renewal.
    assert journal(n)['transaction']['invocation'] == pending
    before = private_files(n)
    with pytest.raises(ValueError, match='abort-mismatch'):
        helper.abort_rearm_active_slot(n.selected, 'sync', expected_nonce=n.nonce)
    assert private_files(n) == before
    result = helper.abort_rearm_active_slot(n.selected, 'sync', expected_nonce=pending['nonce'])
    assert result['status'] == 'restored' and gate() == revoked
    assert helper.abort_rearm_active_slot(n.selected, 'sync', expected_nonce=pending['nonce']) == result
    next_run = rearm(n)
    assert next_run['invocation']['nonce'] not in (n.nonce, pending['nonce'])
    assert custody._native_slot_matches(custody._native_broker_projection(gate()), custody._native_invocation(next_run['invocation']))


@pytest.mark.parametrize('phase', ['completed', 'quiescing', 'unknown', 'retired'])
def test_non_dispatch_phases_never_authorize_source_or_invalidate_sibling_shape(lifecycle, phase):
    n = lifecycle
    if phase == 'completed':
        advance(n, 'running')
    advance(n, phase)
    snapshot = custody._native_broker_projection(gate())
    old = custody._native_invocation(n.first['invocation'])
    assert not custody._native_slot_matches(snapshot, old)
    assert not custody._native_slot_matches(snapshot, old, running=True)
