"""Explicit selection and stop ordering; no live Darwin services are exercised."""
from contextlib import contextmanager
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from test_native_source_installer_recovery import access, context_plan, source_images, binding
from test_native_installer_support import support, s  # noqa: F401 - shared explicit Root fixture


@pytest.mark.parametrize('explicit', [False, True])
def test_active_recovery_keeps_resume_default_and_requires_explicit_rollback(monkeypatch, explicit):
    import pixel_access_bridge as module
    adapter = object.__new__(module.LaunchdAccessBridge)
    adapter.state = access._launchd.ACCESS_STATE
    monkeypatch.setattr(access.sys, 'platform', 'darwin')
    monkeypatch.setattr(access.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(access.os.path, 'lexists', lambda _path: True)
    held, events = [], []
    @contextmanager
    def locked(self):
        held.append('lock')
        try: yield
        finally: held.pop()
    @contextmanager
    def bounded(self, seconds):
        assert held == ['lock'] and seconds == 900
        held.append('bounded')
        try: yield
        finally: held.pop()
    monkeypatch.setattr(module.LaunchdAccessBridge, 'recovery_locked', locked)
    monkeypatch.setattr(module.LaunchdAccessBridge, 'bounded', bounded)
    plan, records = {'source_lifecycle': {'mode': 'absent'}}, []
    journal = SimpleNamespace(value={'phase': 'active'})
    monkeypatch.setattr(access, '_load_upgrade_recovery', lambda **_kw: (plan, journal, records))
    monkeypatch.setattr(access, '_verify_recovery_bindings', lambda *_a: None)
    def invoked(name, p, r, j, *extra, verify_snapshots):
        assert held == ['lock', 'bounded'] and (p, r, j) == (plan, records, journal)
        assert extra == ((adapter,) if name == 'rollback' else ())
        verify_snapshots()
        events.append(name)
    monkeypatch.setattr(access, '_resume_source_upgrade_active', lambda *a, **kw: invoked('resume', *a, **kw))
    monkeypatch.setattr(access, '_recover_before_source_publication', lambda *a, **kw: invoked('rollback', *a, **kw))
    result = access.recover_install(adapter, current_digest='a' * 64, candidate_digest='b' * 64,
        owner_name='fixture', rollback_before_source_publication=explicit)
    assert events == ['rollback' if explicit else 'resume']
    assert result == ('restored' if explicit else 'active') and not held


@pytest.mark.parametrize('fault', ['schema', 'phase', 'withdrawal', 'kit', 'identity', 'withdrawal-receipt'])
def test_unqualified_active_origin_never_prepares_capsule_or_stops_services(monkeypatch, fault):
    records = source_images()
    plan = context_plan(records)
    addition = binding(records)
    addition['proof'].update(kit=None, identity={'kind': 'absent'})
    journal = SimpleNamespace(value=dict(schemaVersion=5, phase='active', sourceAddition=addition))
    if fault == 'schema': journal.value['schemaVersion'] = 4
    elif fault == 'phase': journal.value['phase'] = 'prepared'
    elif fault == 'withdrawal': plan['source_lifecycle']['mode'] = 'withdrawal'
    elif fault == 'kit': addition['proof']['kit'] = {}
    elif fault == 'identity': addition['proof']['identity'] = {'kind': 'retained'}
    else: addition['receipt'] = {}
    monkeypatch.setattr(access, '_bound_sidefile_archive', lambda *_a, **_kw: pytest.fail('must not prepare'))
    monkeypatch.setattr(access, '_recover_upgrade', lambda *_a, **_kw: pytest.fail('must not stop'))
    with pytest.raises(access.InstallError, match='origin-unqualified'):
        access._recover_before_source_publication(plan, records, journal, object(), verify_snapshots=lambda: None)


@pytest.mark.parametrize('explicit', [False, True])
def test_cli_passes_only_explicit_prepublication_selection_to_protected_recovery(monkeypatch, capsys, explicit):
    monkeypatch.setattr(access.sys, 'platform', 'darwin')
    monkeypatch.setattr(access.os, 'geteuid', lambda: 0)
    adapter = object()
    monkeypatch.setattr(access, '_recovery_bridge', lambda **_kw: adapter)
    calls = []
    def recover(bridge, **kwargs):
        assert bridge is adapter
        calls.append(kwargs)
        return 'restored'
    monkeypatch.setattr(access, 'recover_install', recover)
    monkeypatch.setattr(access, '_reprove_recovered_access', lambda owner: calls.append(owner))
    argv = ['--owner', 'fixture', '--current-bundle-digest', 'a' * 64, '--bundle-digest', 'b' * 64]
    if explicit: argv.append('--rollback-before-source-publication')
    assert access._recovery_main(argv) == 0
    assert calls[0].get('rollback_before_source_publication', False) is explicit and calls[1] == 'fixture'
    assert 'restored' in capsys.readouterr().out


@pytest.mark.skipif(sys.platform != 'linux' or os.geteuid() != 0, reason='Root-owned Linux support driver only')
def test_protected_entry_exposes_explicit_prepublication_option_from_complete_capsule(support):  # noqa: F811 - shared fixture
    source, python = support
    capsule = s.stage_support(source, python=python)
    driver = '''
import importlib.util,os,sys,urllib.request
from pathlib import Path
root=Path(__ROOT__)
spec=importlib.util.spec_from_file_location('pixel_native_installer_entry',root/'bin/pixel_native_installer_entry.py')
module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
module.SUPPORT_ROOT=root.parent;module._acl=lambda fd:os.fstat(fd);sys.platform='darwin'
module.main(['--peer','access-install','--','recover-runtime','--owner','fixture',
 '--current-bundle-digest','a'*64,'--bundle-digest','b'*64,'--rollback-before-source-publication','--help'])
'''.replace('__ROOT__', repr(capsule['root']))
    result = subprocess.run([str(python), '-I', '-B', '-c', driver], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert '--rollback-before-source-publication' in result.stdout


@pytest.mark.parametrize('stop_failure', [False, True])
def test_preservation_runs_only_after_all_services_are_proven_stopped(monkeypatch, stop_failure):
    """Real recovery sequencer, external launchd/readiness as explicit adapters."""
    roles = access._upgrade.CORE_ROLES
    events, states = [], {role: 'candidate' for role in roles}
    previous = {role: SimpleNamespace(target=role, version='previous') for role in roles}
    candidate = {role: SimpleNamespace(target=role, version='candidate') for role in roles}
    for role, item in candidate.items():
        item.assert_stopped = lambda role=role: events.append('absent-' + role) if states[role] == 'absent' else pytest.fail('still running')
    monkeypatch.setattr(access, '_upgrade_services', lambda *_a: (previous, candidate))
    monkeypatch.setattr(access, '_recover_unstarted_upgrade', lambda *_a, **_kw: False)
    monkeypatch.setattr(access, '_source_upgrade_file_options', lambda *_a: ({}, {}))
    monkeypatch.setattr(access, '_verify_recovery_runtime', lambda *_a: None)
    monkeypatch.setattr(access, '_resume_migration_hold', lambda *_a: {'held': True})
    monkeypatch.setattr(access, '_observe_upgrade_service', lambda before, after: states[before.target])
    def stop(service):
        if stop_failure and service.target == roles[0]: raise ValueError('fixture stop failed')
        states[service.target] = 'absent'
        events.append('stop-' + service.target)
    def absent(before, after):
        assert states[before.target] == 'absent'
    monkeypatch.setattr(access, '_stop_upgrade_service', stop)
    monkeypatch.setattr(access, '_assert_upgrade_absent', absent)
    monkeypatch.setattr(access, '_restore_upgrade_owner', lambda *_a: events.append('owner'))
    def start(service):
        assert 'preserve' in events
        states[service.target] = 'previous'
    monkeypatch.setattr(access, '_start_upgrade_service', start)
    for name in ('_upgrade_service_identity', '_upgrade_policy_mode', '_ready_gateway', '_ready_access',
            '_ready_access_relay', '_finish_source_upgrade', '_clear_candidate_stop_witnesses', '_finish_migration_hold'):
        monkeypatch.setattr(access, name, lambda *_a, **_kw: None)
    value = {'phase': 'active'}
    journal = SimpleNamespace(value=value, phase=lambda phase: value.update(phase=phase), finish=lambda verify: verify())
    def preserve():
        assert set(states.values()) == {'absent'} and events[-1] == 'owner'
        events.append('preserve')
    # Keep the real file restore codec; one already-before image needs no writer.
    records = [dict(path='/invented', before=b'same', after=b'same', mode=0o644, gid=0)]
    monkeypatch.setattr(access, '_source_snapshot_read', lambda *_a, **_kw: b'same')
    run = lambda: access._recover_upgrade({'access_settings': {'gateway_port': 18789}}, records, journal,
        verify_snapshots=lambda: None, preserve_private=preserve)
    if stop_failure:
        with pytest.raises(access._upgrade.UpgradeError): run()
        assert 'preserve' not in events and journal.value['phase'] == 'recovery-required'
    else:
        run()
        assert events.count('preserve') == 1 and journal.value['phase'] == 'restored'
