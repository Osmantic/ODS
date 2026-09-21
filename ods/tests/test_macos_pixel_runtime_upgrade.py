import importlib.util
import copy
import itertools
from contextlib import contextmanager
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

SPEC = importlib.util.spec_from_file_location('upgrade', Path(__file__).resolve().parents[1]
    / 'installers/macos/lib/pixel-runtime-upgrade.py')
upgrade = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(upgrade)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))


@pytest.mark.parametrize('fault', [None, 'save', 'verify', 'drift', 'unlink'])
def test_retire_rollback_preserves_authority_before_removal_and_resumes(fault):
    a, b = 'a' * 64, 'b' * 64
    archive = 'runtime-upgrade-' + b + '.completed.json'
    context = 'runtime-upgrade-context-' + b + '.json'
    edge = 'runtime-upgrade-edge-' + b + '.json'
    snapshots = {archive: json.dumps(dict(phase='restored', currentDigest=a, candidateDigest=b)).encode(),
                 context: b'{"fixture":"context"}', edge: b'{"phase":"released"}'}
    live = dict(snapshots)
    if fault == 'drift': live[context] = b'changed'
    history, events = [], []
    failed = [False]
    def verify():
        events.append('verify')
        if fault == 'verify': raise RuntimeError('verify')
    def read(name):
        if name not in live: raise FileNotFoundError(name)
        return live[name]
    def preserve(value):
        events.append('preserve')
        if fault == 'save': raise OSError('save')
        if history: assert history[0] == value
        else: history.append(value)
    def remove(name, expected):
        assert history and events.count('verify') >= 2
        if fault == 'unlink' and not failed[0] and len(live) == 2:
            failed[0] = True
            raise OSError('unlink')
        if name not in live: raise FileNotFoundError(name)
        assert live[name] == expected
        events.append(name)
        del live[name]
    def run():
        upgrade.retire_restored_attempt(current_digest=a, candidate_digest=b, snapshots=snapshots,
            verify_restored=verify, read=read, preserve=preserve, remove=remove)
    if fault:
        with pytest.raises((OSError, RuntimeError)): run()
        if fault != 'unlink':
            assert len(live) == 3
            return
        assert archive in live and len(live) == 2
    run()
    assert not live and events[-1] == archive
    import base64
    assert {name: base64.b64decode(body) for name, body in history[0]['files'].items()} == snapshots


@pytest.mark.parametrize('fault', ['active', 'held', 'foreign-file', 'missing-context', 'wrong-digest'])
def test_retirement_refuses_unqualified_history_before_callbacks(fault):
    a, b = 'a' * 64, 'b' * 64
    archive = 'runtime-upgrade-' + b + '.completed.json'
    context = 'runtime-upgrade-context-' + b + '.json'
    snapshots = {archive: json.dumps(dict(phase='active' if fault == 'active' else 'restored',
        currentDigest='c' * 64 if fault == 'wrong-digest' else a, candidateDigest=b)).encode(),
        context: b'{}'}
    if fault == 'held': snapshots['runtime-upgrade-edge-' + b + '.json'] = b'{"phase":"held"}'
    if fault == 'foreign-file': snapshots['verified.json'] = b'{}'
    if fault == 'missing-context': del snapshots[context]
    def forbidden(*args): pytest.fail('unqualified retirement reached an effect callback')
    with pytest.raises(upgrade.UpgradeError):
        upgrade.retire_restored_attempt(current_digest=a, candidate_digest=b, snapshots=snapshots,
            verify_restored=forbidden, read=forbidden, preserve=forbidden, remove=forbidden)


@pytest.mark.parametrize('fault', [None, 'late-verification', 'permissions', 'symlink'])
def test_retirement_filesystem_preserves_private_history_and_can_resume(tmp_path, monkeypatch, fault):
    import base64
    import stat
    import pixel_macos_custody as custody
    @contextmanager
    def directory(path):
        assert path == tmp_path
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try: yield fd
        finally: os.close(fd)
    def verify_fd(fd, *, directory):
        info = os.fstat(fd)
        assert not directory and stat.S_ISREG(info.st_mode) and info.st_nlink == 1
        return info
    # Real descriptor I/O; only root ownership/ancestor checks are substituted.
    monkeypatch.setattr(custody, 'protected_directory', directory)
    monkeypatch.setattr(custody, '_verify_fd', verify_fd)
    a, b = 'a' * 64, 'b' * 64
    archive = 'runtime-upgrade-' + b + '.completed.json'
    context = 'runtime-upgrade-context-' + b + '.json'
    snapshots = {archive: json.dumps(dict(phase='restored', currentDigest=a, candidateDigest=b)).encode(),
                 context: b'{"private":"context-data"}'}
    for name, body in snapshots.items():
        (tmp_path / name).write_bytes(body)
        (tmp_path / name).chmod(0o600)
    if fault == 'permissions': (tmp_path / context).chmod(0o644)
    if fault == 'symlink':
        (tmp_path / context).unlink()
        (tmp_path / context).symlink_to(tmp_path / archive)
    calls = []
    def verify():
        calls.append(True)
        if fault == 'late-verification' and len(calls) == 2: raise RuntimeError('late')
    def run():
        return upgrade.archive_restored_attempt(tmp_path, current_digest=a, candidate_digest=b,
            snapshots=snapshots, verify_restored=verify)
    if fault:
        with pytest.raises((OSError, RuntimeError)): run()
        assert all((tmp_path / name).exists() for name in snapshots)
        if fault != 'late-verification':
            assert not list(tmp_path.glob('runtime-upgrade-history-*'))
            return
    name = run()
    history = tmp_path / name
    assert list(tmp_path.iterdir()) == [history]
    assert stat.S_IMODE(history.stat().st_mode) == 0o600
    value = json.loads(history.read_bytes())
    assert {k: base64.b64decode(v) for k, v in value['files'].items()} == snapshots
    import hashlib
    snapshots = upgrade.decode_retirement_history(history.read_bytes(),
        history_digest=hashlib.sha256(history.read_bytes()).hexdigest(), current_digest=a, candidate_digest=b)
    assert run() == name


@pytest.mark.parametrize('fault', ['digest', 'selection', 'schema', 'encoding', 'names', 'duplicate'])
def test_retirement_history_decoder_rejects_untrusted_shape(fault):
    import hashlib
    import base64
    a, b = 'a' * 64, 'b' * 64
    snapshot = json.dumps(dict(phase='restored', currentDigest=a, candidateDigest=b)).encode()
    value = dict(schemaVersion=1, currentDigest=a, candidateDigest=b, files={
        'runtime-upgrade-' + b + '.completed.json': base64.b64encode(snapshot).decode(),
        'runtime-upgrade-context-' + b + '.json': 'e30='})
    if fault == 'selection': value['currentDigest'] = 'c' * 64
    if fault == 'schema': value['schemaVersion'] = True
    if fault == 'encoding': value['files']['runtime-upgrade-context-' + b + '.json'] = '!!!'
    if fault == 'names': value['files']['../outside'] = 'e30='
    body = (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()
    if fault == 'duplicate': body = body.replace(b'"schemaVersion":1', b'"schemaVersion":1,"schemaVersion":1')
    digest = 'd' * 64 if fault == 'digest' else hashlib.sha256(body).hexdigest()
    with pytest.raises(upgrade.UpgradeError):
        upgrade.decode_retirement_history(body, history_digest=digest, current_digest=a, candidate_digest=b)


def scenario(failure=None, recovery_failure=False, roles=upgrade.CORE_ROLES):
    events = []
    loaded = {role: 'old' for role in roles}
    current_files = ['old']
    failed = [False]

    def event(name):
        events.append(name)
        if name == failure and not failed[0]:
            failed[0] = True
            raise RuntimeError('injected-' + name)

    def service(role, version):
        def assert_stopped():
            event('stopped-' + version + '-' + role)
            assert role not in loaded
        return SimpleNamespace(target='system/' + role, role=role, version=version,
                               assert_stopped=assert_stopped)

    previous = {r: service(r, 'old') for r in loaded}
    candidate = {r: service(r, 'new') for r in loaded}

    def stop(s):
        event('before-stop-' + s.version + '-' + s.role)
        loaded.pop(s.role, None)
        event('stop-' + s.version + '-' + s.role)

    def start(s):
        assert current_files[0] == s.version
        assert s.role not in loaded
        loaded[s.role] = s.version
        event('start-' + s.version + '-' + s.role)

    def replace():
        assert not loaded
        current_files[0] = 'new'
        event('replace')

    def restore():
        assert not loaded
        if recovery_failure:
            raise RuntimeError('restore failed')
        current_files[0] = 'old'
        event('restore')

    def ready(services):
        version = services['gateway'].version
        assert loaded == dict.fromkeys(services, version)
        event('ready-' + version)

    return dict(previous=previous, candidate=candidate, phase=lambda p: event('phase-' + p),
        verify=lambda: event('verify'), replace_files=replace, restore_files=restore,
        start=start, stop=stop, ready=ready), events, loaded, current_files


def test_upgrade_stops_old_before_replacement_and_checks_new():
    args, events, loaded, files = scenario()
    upgrade.activate(**args)
    assert set(loaded.values()) == {'new'} and files == ['new']
    assert events.index('stop-old-relay') < events.index('stop-old-gateway') < events.index('replace')
    assert events[-2:] == ['ready-new', 'phase-active']


@pytest.mark.parametrize('failure', [None, 'replace', 'ready-new'] + [
    action + '-' + role for action in ('before-stop-old', 'stop-old', 'start-new')
    for role in upgrade.NATIVE_ROLES])
def test_complete_native_update_restores_all_six_services_on_failure(failure):
    args, events, loaded, files = scenario(failure=failure, roles=upgrade.NATIVE_ROLES)
    if failure:
        with pytest.raises(RuntimeError): upgrade.activate(**args)
        assert loaded == dict.fromkeys(upgrade.NATIVE_ROLES, 'old') and files == ['old']
        assert events[-1] == 'phase-restored'
    else:
        upgrade.activate(**args)
        assert loaded == dict.fromkeys(upgrade.NATIVE_ROLES, 'new') and files == ['new']
        stops = [event for event in events if event.startswith('stop-old-')]
        starts = [event for event in events if event.startswith('start-new-')]
        assert stops == ['stop-old-' + role for role in reversed(upgrade.NATIVE_ROLES)]
        assert starts == ['start-new-' + role for role in upgrade.NATIVE_ROLES]
        assert events.index(stops[-1]) < events.index('replace') < events.index(starts[0])


@pytest.mark.parametrize('missing', ['operations', 'promoter', 'manager'])
def test_partial_native_update_is_rejected_before_mutation(missing):
    args, events, _, _ = scenario(roles=upgrade.NATIVE_ROLES)
    del args['previous'][missing]
    del args['candidate'][missing]
    with pytest.raises(upgrade.UpgradeError, match='services-incomplete'):
        upgrade.activate(**args)
    assert not events


@pytest.mark.parametrize('versions', list(itertools.product(('old', 'new', None), repeat=6)))
def test_native_recovery_handles_every_mixture_of_six_service_versions(versions):
    args, events, loaded, files = scenario(roles=upgrade.NATIVE_ROLES)
    loaded.clear()
    loaded.update({role: version for role, version in zip(upgrade.NATIVE_ROLES, versions) if version})
    files[:] = ['new']
    def observe(old, new):
        return {'old': 'previous', 'new': 'candidate', None: 'absent'}[loaded.get(old.role)]
    def absent(old, new):
        assert old.role not in loaded
        events.append('proof-absent-' + old.role)
    upgrade.recover_previous(previous=args['previous'], candidate=args['candidate'],
        phase=args['phase'], verify_snapshots=args['verify'], observe=observe,
        assert_absent=absent, restore_files=args['restore_files'], start=args['start'],
        stop=args['stop'], ready=args['ready'])
    assert files == ['old'] and loaded == dict.fromkeys(upgrade.NATIVE_ROLES, 'old')
    assert events[-1] == 'phase-restored'


def test_group_readable_broker_policy_round_trips_through_recovery():
    records = [dict(path='/usr/local/libexec/ods-pixel-services/operations/policy.json',
        before=b'previous policy', after=b'candidate policy', mode=0o640, gid=61000)]
    options = dict(current_digest='a' * 64, candidate_digest='b' * 64,
        allowed_paths={item['path'] for item in records})
    value = upgrade.encode_recovery(records, **options)
    assert upgrade.decode_recovery(value, **options) == records


@pytest.mark.parametrize('fault', [None, 'partial-owner', 'ready-new'])
def test_owner_migration_and_rollback_only_run_with_all_services_stopped(fault):
    args, events, loaded, files = scenario(failure='ready-new' if fault == 'ready-new' else None)
    owner = ['old']
    def migrate():
        assert not loaded and files == ['new']
        owner[:] = ['new']
        events.append('migrate-owner')
        if fault == 'partial-owner':
            raise RuntimeError('owner-reply-lost')
    def restore():
        assert not loaded and files == ['new']
        owner[:] = ['old']
        events.append('restore-owner')
    if fault:
        with pytest.raises(RuntimeError):
            upgrade.activate(**args, migrate_owner=migrate, restore_owner=restore)
        assert owner == files == ['old']
        assert events.index('restore-owner') < events.index('restore') < events.index('start-old-gateway')
    else:
        upgrade.activate(**args, migrate_owner=migrate, restore_owner=restore)
        assert owner == files == ['new']
        assert events.index('replace') < events.index('migrate-owner') < events.index('start-new-gateway')


def test_failed_owner_rollback_keeps_services_stopped_and_requires_recovery():
    args, events, loaded, files = scenario(failure='ready-new')
    def restore():
        assert not loaded
        raise RuntimeError('owner-drift')
    with pytest.raises(upgrade.UpgradeError, match='recovery-required'):
        upgrade.activate(**args, migrate_owner=lambda: None, restore_owner=restore)
    assert not loaded and files == ['new']
    assert 'restore' not in events


def test_owner_hooks_must_be_paired_before_any_mutation():
    args, events, _, _ = scenario()
    with pytest.raises(upgrade.UpgradeError, match='owner-hooks-incomplete'):
        upgrade.activate(**args, migrate_owner=lambda: None)
    assert not events


@pytest.mark.parametrize('versions', list(itertools.product(('old', 'new', None), repeat=3)))
def test_interrupted_upgrade_recovers_mixed_jobs_only_after_stop_proof(versions):
    args, events, loaded, files = scenario()
    loaded.clear()
    loaded.update({r: v for r, v in zip(args['previous'], versions) if v})
    files[:] = ['new']
    def observe(old, new):
        return {'old': 'previous', 'new': 'candidate', None: 'absent'}[loaded.get(old.role)]
    def absent(old, new):
        assert old.role not in loaded
        events.append('proof-absent-' + old.role)
    upgrade.recover_previous(previous=args['previous'], candidate=args['candidate'],
        phase=args['phase'], verify_snapshots=args['verify'], observe=observe,
        assert_absent=absent, restore_files=args['restore_files'], start=args['start'],
        stop=args['stop'], ready=args['ready'])
    assert files == ['old'] and set(loaded.values()) == {'old'} and len(loaded) == 3
    assert events[-1] == 'phase-restored'


def test_recovery_refuses_unknown_live_job_before_stopping_anything():
    args, events, loaded, files = scenario()
    with pytest.raises(upgrade.UpgradeError, match='service-unqualified'):
        upgrade.recover_previous(previous=args['previous'], candidate=args['candidate'],
            phase=args['phase'], verify_snapshots=args['verify'], observe=lambda *args: 'unknown',
            assert_absent=lambda *args: None, restore_files=args['restore_files'],
            start=args['start'], stop=args['stop'], ready=args['ready'])
    assert events == ['verify']
    assert len(loaded) == 3


def test_recovery_does_not_restore_on_unproven_missing_process_tree():
    args, events, loaded, files = scenario()
    loaded.clear()
    files[:] = ['new']
    def absent(*args):
        raise RuntimeError('missing durable stop witness')
    with pytest.raises(upgrade.UpgradeError, match='recovery-required'):
        upgrade.recover_previous(previous=args['previous'], candidate=args['candidate'],
            phase=args['phase'], verify_snapshots=args['verify'], observe=lambda *args: 'absent',
            assert_absent=absent, restore_files=args['restore_files'],
            start=args['start'], stop=args['stop'], ready=args['ready'])
    assert files == ['new'] and not loaded
    assert 'restore' not in events
    assert events[-1] == 'phase-recovery-required'


@pytest.mark.parametrize('failure', ['before-stop-old-relay', 'before-stop-old-access',
    'before-stop-old-gateway', 'stop-old-relay', 'stop-old-access', 'stop-old-gateway',
    'replace', 'start-new-gateway', 'start-new-access', 'start-new-relay', 'ready-new', 'phase-active'])
def test_every_partial_transition_restores_previous(failure):
    args, events, loaded, files = scenario(failure)
    with pytest.raises(RuntimeError, match='injected-'):
        upgrade.activate(**args)
    assert set(loaded.values()) == {'old'} and len(loaded) == 3 and files == ['old']
    assert events[-1] == 'phase-restored'


def test_restore_failure_never_starts_old_against_new_files():
    args, events, loaded, files = scenario('ready-new', recovery_failure=True)
    with pytest.raises(upgrade.UpgradeError, match='recovery-required'):
        upgrade.activate(**args)
    assert not loaded and files == ['new']
    assert events[-1] == 'phase-recovery-required'


def test_changed_target_rejected_before_side_effects():
    args, events, _, _ = scenario()
    args['candidate']['gateway'].target = 'system/unrelated'
    with pytest.raises(upgrade.UpgradeError, match='target-changed'):
        upgrade.activate(**args)
    assert events == []


def test_unconfirmed_candidate_stop_prevents_restoring_executables():
    args, events, _, files = scenario('ready-new')
    original_stop = args['stop']
    def stop(service):
        if service.version == 'new':
            raise RuntimeError('cannot prove candidate stopped')
        original_stop(service)
    args['stop'] = stop
    with pytest.raises(upgrade.UpgradeError, match='recovery-required'):
        upgrade.activate(**args)
    assert 'restore' not in events and files == ['new']
    assert events[-1] == 'phase-recovery-required'


def test_preflight_failure_does_not_stop_services():
    args, events, loaded, files = scenario('verify')
    with pytest.raises(RuntimeError, match='injected-verify'):
        upgrade.activate(**args)
    assert events == ['verify']
    assert set(loaded.values()) == {'old'} and files == ['old']


def file_set():
    records = [dict(path=name, before=b'old', after=b'new', mode=0o600, gid=0)
               for name in ('launcher', 'plist', 'config')]
    values = {r['path']: r['before'] for r in records}
    writes = []
    def replace(path, *, expected, replacement, mode, gid):
        assert values[path] == expected
        assert mode == 0o600 and gid == 0
        values[path] = replacement
        writes.append(path)
    return records, values, writes, replace


def test_partial_file_write_restores_only_modified_files():
    records, values, writes, replace = file_set()
    def fail(path, **kwargs):
        if path == 'plist': raise OSError('injected-write-failure')
        replace(path, **kwargs)
    with pytest.raises(OSError):
        upgrade.replace_deployment_files(records, read=values.__getitem__, replace=fail)
    assert values == {'launcher': b'new', 'plist': b'old', 'config': b'old'}
    upgrade.restore_deployment_files(records, read=values.__getitem__, replace=replace)
    assert set(values.values()) == {b'old'}
    assert writes == ['launcher', 'launcher']


@pytest.mark.parametrize('restore', [False, True])
def test_file_drift_preflight_prevents_any_overwrite(restore):
    records, values, writes, replace = file_set()
    values['config'] = b'user change'
    operation = upgrade.restore_deployment_files if restore else upgrade.replace_deployment_files
    with pytest.raises(upgrade.UpgradeError, match='file-drift'):
        operation(records, read=values.__getitem__, replace=replace)
    assert not writes and values['config'] == b'user change'


def test_file_set_round_trip_restores_in_reverse_order():
    records, values, writes, replace = file_set()
    upgrade.replace_deployment_files(records, read=values.__getitem__, replace=replace)
    upgrade.restore_deployment_files(records, read=values.__getitem__, replace=replace)
    assert writes == ['launcher', 'plist', 'config', 'config', 'plist', 'launcher']
    assert set(values.values()) == {b'old'}


def recovery_fixture():
    records = [dict(path='/private/etc/ods/fixture', mode=0o600, gid=0,
                    before=b'private-before\x00', after=b'private-after')]
    selection = dict(current_digest='a' * 64, candidate_digest='b' * 64,
                     allowed_paths={'/private/etc/ods/fixture'})
    return records, selection, upgrade.encode_recovery(records, **selection)


def test_recovery_roundtrip_preserves_binary_snapshots():
    records, selection, value = recovery_fixture()
    assert upgrade.decode_recovery(json.loads(json.dumps(value)), **selection) == records


@pytest.mark.parametrize('mutation', ['path', 'bytes', 'hash', 'phase', 'digest', 'extra', 'mode', 'duplicate'])
def test_recovery_rejects_mismatched_or_corrupt_record(mutation):
    _, selection, value = recovery_fixture()
    if mutation == 'path': value['files'][0]['path'] = '/private/etc/unrelated'
    if mutation == 'bytes': value['files'][0]['before'] = 'invalid!'
    if mutation == 'hash': value['files'][0]['beforeSha256'] = '0' * 64
    if mutation == 'phase': value['phase'] = 'unknown'
    if mutation == 'digest': value['candidateDigest'] = 'c' * 64
    if mutation == 'extra': value['unexpected'] = True
    if mutation == 'mode': value['files'][0]['mode'] = 0o777
    if mutation == 'duplicate': value['files'].append(copy.deepcopy(value['files'][0]))
    with pytest.raises(upgrade.UpgradeError):
        upgrade.decode_recovery(value, **selection)


def test_journal_persists_phases_without_overwriting_drift(tmp_path, monkeypatch):
    import pixel_access_bridge as bridge
    real_read = bridge.private_json
    # Simulate the root boundary while exercising actual atomic writes/reads.
    monkeypatch.setattr(bridge, 'private_json', lambda path, uid, maximum:
                        real_read(path, os.getuid(), maximum))
    _, _, value = recovery_fixture()
    path = tmp_path / 'upgrade.json'
    journal = upgrade.RecoveryJournal.create(path, value)
    assert path.stat().st_mode & 0o077 == 0
    journal.phase('replacing-files')
    assert json.loads(path.read_text())['phase'] == 'replacing-files'
    with pytest.raises(upgrade.UpgradeError, match='journal-exists'):
        upgrade.RecoveryJournal.create(path, value)
    path.write_text('{}')
    with pytest.raises(upgrade.UpgradeError, match='journal-changed'):
        journal.phase('active')
    assert path.read_text() == '{}'


@pytest.mark.parametrize('mutation', [None, 'digest', 'path', 'race', 'permissions'])
def test_reopen_journal_validates_external_selection_without_writes(tmp_path, monkeypatch, mutation):
    import pixel_access_bridge as bridge
    real_read = bridge.private_json
    records, selection, value = recovery_fixture()
    path = tmp_path / 'upgrade.json'
    upgrade.RecoveryJournal.create(path, value)
    original = path.read_bytes()
    calls = []
    def read(target, uid, maximum):
        assert uid == 0 and maximum == upgrade.JOURNAL_LIMIT
        calls.append(target)
        result = real_read(target, os.getuid(), maximum)
        if mutation == 'race' and len(calls) == 2:
            result['phase'] = 'recovery-required'
        return result
    monkeypatch.setattr(bridge, 'private_json', read)
    if mutation == 'digest': selection['candidate_digest'] = 'c' * 64
    if mutation == 'path': selection['allowed_paths'] = {'/unrelated'}
    if mutation == 'permissions': path.chmod(0o644)
    if mutation:
        expected = bridge.AccessError if mutation == 'permissions' else upgrade.UpgradeError
        with pytest.raises(expected):
            upgrade.RecoveryJournal.load(path, **selection)
    else:
        journal, decoded = upgrade.RecoveryJournal.load(path, **selection)
        assert decoded == records and journal.value == value
        assert len(calls) == 2
    assert path.read_bytes() == original


@pytest.mark.parametrize('pending', [None, 'transition.json', 'policy-activation.json', 'runtime-upgrade.json'])
def test_prepare_holds_controller_lock_and_rejects_pending_work(tmp_path, pending):
    held = []
    @contextmanager
    def locked():
        held.append(True)
        try: yield
        finally: held.pop()
    bridge = SimpleNamespace(state=tmp_path, locked=locked)
    records, selection, _ = recovery_fixture()
    if pending:
        (tmp_path / pending).write_text('{}')
        with pytest.raises(upgrade.UpgradeError, match='pending-recovery'):
            with upgrade.prepared_upgrade(bridge, records, **selection):
                pytest.fail('must not activate')
        if pending != 'runtime-upgrade.json':
            assert not (tmp_path / 'runtime-upgrade.json').exists()
    else:
        with upgrade.prepared_upgrade(bridge, records, **selection) as journal:
            assert held and journal.path.exists()
            assert upgrade.decode_recovery(json.loads(journal.path.read_text()), **selection) == records
    assert not held


@pytest.mark.parametrize('failure', [None, 'verify', 'archive', 'drift'])
def test_finish_verifies_live_and_archives_before_releasing_guard(tmp_path, monkeypatch, failure):
    import pixel_access_bridge as bridge
    real_read = bridge.private_json
    monkeypatch.setattr(bridge, 'private_json', lambda path, uid, maximum:
                        real_read(path, os.getuid(), maximum))
    _, _, value = recovery_fixture()
    value['phase'] = 'active'
    path = tmp_path / 'runtime-upgrade.json'
    journal = upgrade.RecoveryJournal.create(path, value)
    verified = []
    def verify():
        verified.append(True)
        assert path.exists()
        if failure == 'verify': raise RuntimeError('verification failed')
        if failure == 'drift': path.write_text('{}')
    if failure == 'archive':
        def fail(*_): raise OSError('disk failed')
        monkeypatch.setattr(bridge, 'atomic_json', fail)
    if failure:
        with pytest.raises((RuntimeError, OSError)):
            journal.finish(verify)
        assert path.exists()
    else:
        journal.finish(verify)
        assert not path.exists()
        archive = tmp_path / ('runtime-upgrade-' + 'b' * 64 + '.completed.json')
        assert json.loads(archive.read_text()) == value
        assert archive.stat().st_mode & 0o077 == 0
    assert verified == [True]


@pytest.mark.parametrize('state', ['before', 'after', 'mixed', 'drift', 'pending', 'exception'])
def test_recovery_preflight_holds_lock_and_preserves_journal(tmp_path, monkeypatch, state):
    import pixel_access_bridge as bridge_module
    real_read = bridge_module.private_json
    monkeypatch.setattr(bridge_module, 'private_json', lambda path, uid, maximum:
                        real_read(path, os.getuid(), maximum))
    records, selection, _ = recovery_fixture()
    records.append(dict(records[0], path='/private/etc/ods/second'))
    selection['allowed_paths'] = {item['path'] for item in records}
    value = upgrade.encode_recovery(records, **selection)
    path = tmp_path / 'runtime-upgrade.json'
    upgrade.RecoveryJournal.create(path, value)
    original = path.read_bytes()
    held = []
    @contextmanager
    def locked():
        held.append(True)
        try: yield
        finally: held.pop()
    bridge = SimpleNamespace(state=tmp_path, recovery_locked=locked)
    if state == 'pending': (tmp_path / 'transition.json').write_text('{}')
    reads = []
    def read(target):
        assert held
        reads.append(target)
        if state == 'drift': return b'unknown'
        index = 0 if target == records[0]['path'] else 1
        key = 'after' if state == 'after' or state == 'mixed' and index else 'before'
        return records[index][key]
    def enter():
        with upgrade.recovery_upgrade(bridge, **selection, read=read) as (journal, decoded):
            assert held and journal.value == value and decoded == records
            if state == 'exception': raise RuntimeError('caller failed')
    if state in ('drift', 'pending'):
        with pytest.raises(upgrade.UpgradeError): enter()
    elif state == 'exception':
        with pytest.raises(RuntimeError, match='caller failed'): enter()
    else:
        enter()
    assert not held and path.read_bytes() == original
    if state == 'pending': assert not reads


def test_real_launchd_recovery_guard_accepts_journal_but_normal_lock_refuses(tmp_path, monkeypatch):
    import pixel_access_bridge as bridge_module
    real_read = bridge_module.private_json
    monkeypatch.setattr(bridge_module, 'private_json', lambda path, uid, maximum:
                        real_read(path, os.getuid(), maximum))
    records, selection, _ = recovery_fixture()
    value = upgrade.encode_recovery(records, **selection)
    path = tmp_path / 'runtime-upgrade.json'
    upgrade.RecoveryJournal.create(path, value)
    held = []
    @contextmanager
    def base_lock(self):
        held.append(True)
        try:
            yield
        finally:
            held.pop()
    monkeypatch.setattr(bridge_module.SystemdAccessBridge, 'locked', base_lock)
    adapter = object.__new__(bridge_module.LaunchdAccessBridge)
    adapter.state = tmp_path
    with pytest.raises(bridge_module.AccessError, match='runtime-upgrade-recovery-required'):
        with adapter.locked():
            pytest.fail('normal operation admitted')
    def read(target):
        assert held == [True]
        return next(item['before'] for item in records if item['path'] == target)
    with upgrade.recovery_upgrade(adapter, **selection, read=read) as (journal, decoded):
        assert held == [True]
        assert decoded == records and journal.value == value
    assert not held and path.exists()
