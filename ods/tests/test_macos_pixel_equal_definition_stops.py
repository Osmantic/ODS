"""Real service factory/lifecycle/FD receipts; only OS observations are invented."""
import base64
import errno
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bin'))
import pixel_access_bridge as bridge  # noqa: E402
import pixel_macos_custody as custody  # noqa: E402
import pixel_macos_process as process  # noqa: E402

SPEC = importlib.util.spec_from_file_location('equal_definition_installer', ROOT / 'installers/macos/lib/pixel-macos-access-install.py')
access = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(access)


@pytest.fixture
def world(tmp_path, monkeypatch):
    owner = SimpleNamespace(pw_name='fixture', pw_uid=os.getuid(), pw_gid=os.getgid())
    plan = dict(owner=owner, upgrade_qualification={'candidateDigest': 'b' * 64}, runtime_bundle={'digest': 'b' * 64},
        source_process=dict(uid=owner.pw_uid, gid=owner.pw_gid, executable='/invented/node'),
        gateway_binding={'process': dict(uid=owner.pw_uid, gid=owner.pw_gid, executable='/invented/node')})
    monkeypatch.setattr(access._launchd, 'ACCESS_STATE', tmp_path)
    roles = access._upgrade.NATIVE_ROLES
    targets = {role: (getattr(access._launchd, role.upper() + '_TARGET') if role in access._upgrade.CORE_ROLES
        else 'system/com.ods.pixel-native-' + role) for role in roles}
    paths = {role: (getattr(access._launchd, role.upper() + '_PLIST') if role in access._upgrade.CORE_ROLES
        else Path('/Library/LaunchDaemons') / (targets[role].split('/')[-1] + '.plist')) for role in roles}
    records, definitions, copies = [], {}, {}
    for role in roles:
        user = {'promoter': 'root', 'operations': '_ods_pixel_ops'}.get(role, owner.pw_name)
        arguments = (['/usr/bin/python3', '-I', '/usr/local/libexec/ods-pixel-access/access_mode_server.py']
            if role == 'access' else ['/usr/bin/env', '-i', '/usr/bin/python3', '-I', '-B', '/invented/' + role + '.py'])
        raw = plistlib.dumps(dict(Label=targets[role].split('/')[-1], UserName=user, ProgramArguments=arguments, RunAtLoad=True))
        records.append(dict(path=str(paths[role]), before=raw, after=raw, mode=0o644, gid=0))
        definitions[role] = dict(path=str(paths[role]), body=base64.b64encode(raw).decode())
        copy = tmp_path / (role + '.plist')
        copy.write_bytes(raw)
        copies[str(paths[role])] = copy
    managed = dict(owner=owner.pw_uid, recovery=dict(owner=owner.pw_name, python='/usr/bin/python3',
        identity=dict(name='_ods_pixel_ops', uid=61000, gid=61000),
        definitions={role: definitions[role] for role in access._native_services.INTEGRATIONS_ORDER}))
    raw = json.dumps(managed).encode()
    records.append(dict(path=str(tmp_path / 'service-installation.json'), before=raw, after=raw, mode=0o600, gid=0))
    actual_pwd = access.pwd.getpwnam
    monkeypatch.setattr(access.pwd, 'getpwnam', lambda name: SimpleNamespace(pw_uid=61000, pw_gid=61000)
        if name == '_ods_pixel_ops' else actual_pwd(name))
    # Only the fixture's protected plist/Python metadata is adapted; witness
    # stores still use real atomic_json/private_json against this owner UID.
    monkeypatch.setattr(custody, 'protected_bytes', lambda path, **_kw:
        b'invented isolated Python' if str(path) == '/usr/bin/python3' else copies[str(path)].read_bytes())
    monkeypatch.setattr(custody, 'verify_loaded_launchd_definition', lambda raw, target, path, document:
        None if raw.startswith(target + ' = {') else pytest.fail('unqualified loaded target'))
    private_json = bridge.private_json
    monkeypatch.setattr(bridge, 'private_json', lambda path, _uid, maximum: private_json(path, os.getuid(), maximum))
    monkeypatch.setattr(access.sys, 'platform', 'darwin')
    live = {target: dict(loaded=True, pid=200 + i, birth=(2000 + i, 2)) for i, target in enumerate(targets.values())}
    events, boot = [], ['11111111-1111-1111-1111-111111111111']
    def command(arguments, **_kwargs):
        if arguments == ['/usr/sbin/sysctl', '-n', 'kern.bootsessionuuid']: return boot[0]
        action = arguments[1]
        if action == 'print':
            item = live[arguments[2]]
            if not item['loaded']: raise access.InstallError('host-command-failed', returncode=113)
            return arguments[2] + ' = {\n\tstate = running\n\tpid = ' + str(item['pid']) + '\n}\n'
        if action == 'bootout':
            live[arguments[2]]['loaded'] = False
        elif action == 'bootstrap':
            role = next(role for role, path in paths.items() if str(path) == arguments[3])
            item = live[targets[role]]
            item.update(loaded=True, pid=item['pid'] + 100, birth=(item['birth'][0] + 100, 2))
        else: raise AssertionError(arguments)
        events.append(tuple(arguments))
        return ''
    monkeypatch.setattr(access, '_command', command)
    monkeypatch.setattr(access, '_job_disabled', lambda _target: False)
    def birth(pid):
        for item in live.values():
            if item['loaded'] and item['pid'] == pid: return item['birth']
        raise process.ProcessIdentityError('gateway-process-unavailable', errno=errno.ESRCH)
    monkeypatch.setattr(process, 'process_birth', birth)
    monkeypatch.setattr(process, 'process_tree_snapshot', lambda pid: ((pid, *birth(pid)),))
    monkeypatch.setattr(process, 'process_identity', lambda pid, **_kw: (pid, *birth(pid)))
    return SimpleNamespace(plan=plan, records=records, paths=paths, copies=copies, live=live, events=events, boot=boot,
        build=lambda: access._upgrade_services(plan, records), state=tmp_path)


def test_actual_factory_selects_fresh_canonical_stop_and_replays_same_receipt(world):
    previous, candidate = world.build()
    old, new = previous['access'], candidate['access']
    assert old._upgrade_stop_peer is new and new._upgrade_stop_peer is old
    assert access._observe_upgrade_service(old, new) == 'candidate'
    access._stop_upgrade_service(new)
    receipt = new.load_stop()
    with pytest.raises(FileNotFoundError): old.load_stop()  # No nonnull propagation.
    access._assert_upgrade_absent(old, new)
    previous, candidate = world.build()
    access._assert_upgrade_absent(previous['access'], candidate['access'])
    assert candidate['access'].load_stop() == receipt


@pytest.mark.parametrize('fault', [None, 'absent', 'unpaired-candidate', 'file-drift', 'loaded-definition'])
def test_unstarted_recovery_accepts_only_original_live_equivalent_contracts(world, monkeypatch, fault):
    previous, candidate = world.build()
    assert all(access._observe_upgrade_service(previous[role], candidate[role]) == 'candidate'
        for role in previous)
    records = world.records[:len(previous)]  # Actual factory's protected plist images.
    monkeypatch.setattr(access, '_edge_hold_journal', lambda _plan: world.state / 'edge-hold.json')
    monkeypatch.setattr(access, '_verify_recovery_runtime', lambda _plan: None)
    monkeypatch.setattr(access, '_source_snapshot_read', lambda path, **_kwargs:
        world.copies[str(path)].read_bytes())
    monkeypatch.setattr(access, '_upgrade_policy_mode', lambda _plan: 'sandboxed')
    for name in ('_ready_gateway', '_ready_access', '_ready_access_relay',
            '_finish_source_upgrade', '_clear_candidate_stop_witnesses'):
        monkeypatch.setattr(access, name, lambda *_args, **_kwargs: None)
    phases = []
    journal = SimpleNamespace(value={'phase': 'prepared'}, phase=phases.append,
        finish=lambda verify: verify())
    if fault == 'absent':
        world.live[previous['access'].target]['loaded'] = False
    elif fault == 'unpaired-candidate':
        del candidate['access']._upgrade_stop_peer
        monkeypatch.setattr(access, '_observe_upgrade_service', lambda *_args: 'candidate')
    elif fault == 'file-drift':
        world.copies[str(world.paths['access'])].write_bytes(b'changed')
    elif fault == 'loaded-definition':
        def changed(*_args, **_kwargs):
            raise access.InstallError('launchd-loaded-definition-mismatch')
        monkeypatch.setattr(custody, 'verify_loaded_launchd_definition', changed)
        previous, candidate = world.build()
    before = {path: path.read_bytes() for path in world.copies.values()}
    def run():
        return access._recover_unstarted_upgrade(dict(world.plan, access_settings={'gateway_port': 18789}),
            records, journal, previous, candidate, verify_snapshots=lambda: None, previous_guard=True)
    if fault:
        with pytest.raises(access.InstallError): run()
        assert phases == []
    else:
        assert run() is True
        assert phases == ['restored']
        assert all(item['loaded'] for item in world.live.values())
    assert world.events == []  # No bootout/bootstrap, including on refusal.
    assert {path: path.read_bytes() for path in world.copies.values()} == before


@pytest.mark.parametrize('version', ['previous', 'candidate'])
@pytest.mark.parametrize('method', ['upgrade', 'reload'])
def test_either_bootstrap_invalidates_both_witnesses_before_later_instance(world, version, method):
    previous, candidate = world.build()
    old, new = previous['access'], candidate['access']
    access._stop_upgrade_service(new)
    old.save_stop(new.load_stop())
    selected = {'previous': old, 'candidate': new}[version]
    if method == 'upgrade': access._start_upgrade_service(selected)
    else: selected.reload()
    assert old.load_stop() is None and new.load_stop() is None
    world.live[new.target]['loaded'] = False  # Later instance exited with unknown descendants.
    previous, candidate = world.build()
    with pytest.raises(access.InstallError, match='native-stop-witness-unavailable'):
        access._assert_upgrade_absent(previous['access'], candidate['access'])


@pytest.mark.parametrize('version', ['previous', 'candidate'])
@pytest.mark.parametrize('cut', [1, 2])
@pytest.mark.parametrize('after', [False, True])
def test_crash_at_either_invalidation_never_bootstraps_or_reuses_later_authority(world, monkeypatch, version, cut, after):
    previous, candidate = world.build()
    access._stop_upgrade_service(candidate['access'])
    previous['access'].save_stop(candidate['access'].load_stop())
    save, count = bridge.atomic_json, []
    def interrupted(path, value):
        if value is None:
            count.append(path)
            if len(count) == cut:
                if after: save(path, value)
                raise OSError('invented invalidation interruption')
        save(path, value)
    monkeypatch.setattr(bridge, 'atomic_json', interrupted)
    previous, candidate = world.build()  # Actual factory captures the durable writer.
    world.events.clear()
    with pytest.raises(OSError): access._start_upgrade_service({'previous': previous, 'candidate': candidate}[version]['access'])
    assert world.events == [] and not world.live[candidate['access'].target]['loaded']
    # No bootstrap happened. Either the still-bound old proof remains usable,
    # or its cleared canonical slot conservatively refuses; neither is renewed.
    if candidate['access'].load_stop() is None:
        with pytest.raises(access.InstallError, match='native-stop-witness-unavailable'):
            access._assert_upgrade_absent(previous['access'], candidate['access'])
    else:
        access._assert_upgrade_absent(previous['access'], candidate['access'])


@pytest.mark.parametrize('fault', ['definition', 'process'])
def test_different_snapshot_contracts_keep_distinct_stores_and_original_selection(world, fault):
    role = 'access' if fault == 'definition' else 'gateway'
    if fault == 'definition':
        row = next(row for row in world.records if row['path'] == str(world.paths[role]))
        row['after'] = plistlib.dumps(dict(plistlib.loads(row['before']), KeepAlive=True))
    else:
        world.plan['gateway_binding']['process']['executable'] = '/invented/other-node'
    previous, candidate = world.build()
    old, new = previous[role], candidate[role]
    assert not hasattr(old, '_upgrade_stop_peer') and not hasattr(new, '_upgrade_stop_peer')
    assert access._observe_upgrade_service(old, new) == 'previous'
    old.save_stop({'invented': 'retained'})
    new.save_stop(None)
    assert old.load_stop() == {'invented': 'retained'} and new.load_stop() is None


def test_legacy_equal_definition_prefix_does_not_adopt_previous_witness(world):
    previous, candidate = world.build()
    old = previous['access']
    access._stop_upgrade_service(old)  # Reproduce the earlier installer's choice.
    bridge.atomic_json(access._runtime_upgrade_stop_witness('b' * 64, 'candidate', 'access'), None)
    previous, candidate = world.build()
    previous['access'].assert_stopped()
    with pytest.raises(access.InstallError, match='native-stop-witness-unavailable'):
        access._assert_upgrade_absent(previous['access'], candidate['access'])


def test_seven_role_stop_carry_forward_start_and_rollback_roundtrip(world):
    previous, candidate = world.build()
    assert set(previous) == set(candidate) == set(access._upgrade.NATIVE_ROLES)
    for role in reversed(access._upgrade.NATIVE_ROLES): access._stop_upgrade_service(previous[role])
    access._prepare_candidate_stop_witnesses(previous, candidate, world.records)
    for role in access._upgrade.NATIVE_ROLES:
        access._assert_upgrade_absent(previous[role], candidate[role])
        access._start_upgrade_service(candidate[role])
        assert previous[role].load_stop() is None and candidate[role].load_stop() is None
    # Recreate every adapter as a process restart would; no in-memory stop tree.
    previous, candidate = world.build()
    phases, restores = [], []
    def restore():
        for role in access._upgrade.NATIVE_ROLES: access._assert_upgrade_absent(previous[role], candidate[role])
        restores.append(True)
    def ready(services):
        for service in services.values(): access._upgrade_service_identity(service)
    access._upgrade.recover_previous(previous=previous, candidate=candidate, phase=phases.append,
        verify_snapshots=lambda: [service.verify_definition() for service in candidate.values()],
        observe=access._observe_upgrade_service, assert_absent=access._assert_upgrade_absent,
        restore_files=restore, start=access._start_upgrade_service, stop=access._stop_upgrade_service, ready=ready)
    assert phases == ['rolling-back', 'restored'] and restores == [True]
    assert all(item['loaded'] for item in world.live.values())
    for role in access._upgrade.NATIVE_ROLES:
        assert previous[role].load_stop() is None and candidate[role].load_stop() is None


def test_reboot_still_invalidates_fresh_equivalent_stop(world):
    previous, candidate = world.build()
    access._stop_upgrade_service(candidate['access'])
    world.boot[0] = '22222222-2222-2222-2222-222222222222'
    with pytest.raises(access.InstallError, match='native-stop-witness-unavailable'):
        access._assert_upgrade_absent(previous['access'], candidate['access'])


def test_natural_new_boot_recovers_legacy_prefix_only_by_stopping_seven_new_instances(world):
    previous, candidate = world.build()
    legacy = {}
    frozen_records = tuple(tuple(row[key] for key in ('path', 'before', 'after', 'mode', 'gid')) for row in world.records)
    frozen_files = {path: copy.read_bytes() for path, copy in world.copies.items()}
    for role in access._upgrade.NATIVE_ROLES:
        access._stop_upgrade_service(previous[role])
        legacy[role] = previous[role].load_stop()
        # This is the already-stopped prefix made by the earlier installer,
        # not a call to the new paired invalidator or an adopted receipt.
        bridge.atomic_json(access._runtime_upgrade_stop_witness('b' * 64, 'candidate', role), None)
    with pytest.raises(access.InstallError, match='native-stop-witness-unavailable'):
        access._assert_upgrade_absent(previous['access'], candidate['access'])
    world.events.clear()
    world.boot[0] = '22222222-2222-2222-2222-222222222222'
    fresh = {}
    for role in access._upgrade.NATIVE_ROLES:
        item = world.live[candidate[role].target]
        # OS adapter only: RunAtLoad has naturally created new real identities.
        # No tested bootstrap method is called to manufacture missing jobs.
        item.update(loaded=True, pid=item['pid'] + 1000, birth=(item['birth'][0] + 100000, 3))
        fresh[role] = [[item['pid'], *item['birth']]]
    previous, candidate = world.build()
    phases, restored = [], []
    def verify():
        assert frozen_records == tuple(tuple(row[key] for key in ('path', 'before', 'after', 'mode', 'gid')) for row in world.records)
        assert frozen_files == {path: copy.read_bytes() for path, copy in world.copies.items()}
    def restore():
        verify()
        assert not any(item['loaded'] for item in world.live.values())
        assert len(world.events) == 7 and all(event[1] == 'bootout' for event in world.events)
        for role in access._upgrade.NATIVE_ROLES:
            assert previous[role].load_stop() == legacy[role]
            receipt = candidate[role].load_stop()
            assert receipt['boot'] == world.boot[0] and receipt['processes'] == fresh[role]
            assert receipt['boot'] != legacy[role]['boot'] and receipt['processes'] != legacy[role]['processes']
            access._assert_upgrade_absent(previous[role], candidate[role])
        restored.append(True)
    def ready(services):
        for service in services.values(): access._upgrade_service_identity(service)
    access._upgrade.recover_previous(previous=previous, candidate=candidate, phase=phases.append,
        verify_snapshots=verify, observe=access._observe_upgrade_service, assert_absent=access._assert_upgrade_absent,
        restore_files=restore, start=access._start_upgrade_service, stop=access._stop_upgrade_service, ready=ready)
    assert phases == ['rolling-back', 'restored'] and restored == [True]
    verify()
    assert all(item['loaded'] for item in world.live.values())
