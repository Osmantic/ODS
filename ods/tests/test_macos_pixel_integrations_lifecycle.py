"""Offline exact-capability and rollback tests; no launchd/provider qualification."""
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


LIB = Path(__file__).resolve().parents[1] / 'installers/macos/lib'


def load(name):
    spec = importlib.util.spec_from_file_location('integration_lifecycle_' + name.replace('-', '_'), LIB / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bundle = load('pixel-runtime-bundle')
upgrade = load('pixel-runtime-upgrade')
services = load('pixel-native-services')


@pytest.mark.parametrize('inspection', [False, True])
@pytest.mark.parametrize('fault', [None, 'partial', 'mode', 'gid', 'foreign', 'after-null', 'schema'])
def test_integrations_additions_require_complete_private_rollback_contract(inspection, fault):
    paths = upgrade.INTEGRATION_ADDITIONS | (upgrade.INSPECTION_ADDITIONS if inspection else set())
    assert len(upgrade.INTEGRATION_ADDITIONS) == 9
    records = [dict(path=path, before=None, after=b'new', mode=0o644, gid=0) for path in sorted(paths)]
    if fault == 'partial': records.pop()
    if fault == 'mode': records[0]['mode'] = 0o600
    if fault == 'gid': records[0]['gid'] = 20
    if fault == 'foreign': records[0]['path'] = '/private/foreign.py'
    if fault == 'after-null': records[0]['after'] = None
    options = dict(current_digest='a' * 64, candidate_digest='b' * 64,
                   allowed_paths={item['path'] for item in records})
    if fault and fault != 'schema':
        with pytest.raises(upgrade.UpgradeError): upgrade.encode_recovery(records, **options)
        return
    encoded = upgrade.encode_recovery(records, **options)
    assert encoded['schemaVersion'] == 3
    if fault == 'schema':
        encoded['schemaVersion'] = 2
        with pytest.raises(upgrade.UpgradeError): upgrade.decode_recovery(encoded, **options)
    else:
        assert upgrade.decode_recovery(json.loads(json.dumps(encoded)), **options) == records


@pytest.mark.parametrize('inspection', [False, True])
def test_new_capability_source_manifest_does_not_accept_partial_or_inspectionless_sets(inspection):
    all_names = set(bundle.ODS_SERVICE_SOURCES) | bundle.GENERATED_SERVICE_ARTIFACTS | {'operations/broker.py'}
    def manifest(names):
        return dict(schemaVersion=1, status='staged', requiresServiceQualification=True,
            pixelSourceRef='a' * 40, candidateConfigSha256='b' * 64,
            files={name: {'sha256': hashlib.sha256(name.encode()).hexdigest(), 'bytes': len(name)} for name in names})
    for legacy in (all_names - bundle.INTEGRATION_SERVICE_ARTIFACTS,
                   all_names - bundle.INTEGRATION_SERVICE_ARTIFACTS - bundle.INSPECTION_SERVICE_ARTIFACTS):
        bundle.validate_service_manifest_provenance(manifest(legacy))
    bundle.validate_service_manifest_provenance(manifest(all_names))
    assert len(bundle.INTEGRATION_SERVICE_ARTIFACTS) == 8
    for missing in bundle.INTEGRATION_SERVICE_ARTIFACTS:
        with pytest.raises(bundle.BundleError):
            bundle.validate_service_manifest_provenance(manifest(all_names - {missing}))
    if not inspection:
        with pytest.raises(bundle.BundleError):
            bundle.validate_service_manifest_provenance(manifest(all_names - bundle.INSPECTION_SERVICE_ARTIFACTS))


@pytest.mark.parametrize('failure_at', range(1, 15))
def test_new_capability_partial_file_publication_restores_all_prior_absences(failure_at):
    paths = upgrade.INTEGRATION_ADDITIONS | upgrade.INSPECTION_ADDITIONS
    records = [dict(path=p, before=None, after=p.encode(), mode=0o644, gid=0) for p in sorted(paths)]
    live, writes = {}, []
    def replace(path, *, expected, replacement, **kwargs):
        assert live.get(path) == expected
        if replacement is None: live.pop(path, None)
        else: live[path] = replacement
        writes.append(path)
        if len(writes) == failure_at: raise OSError('after replacement')
    with pytest.raises(OSError): upgrade.replace_deployment_files(records, read=live.get, replace=replace)
    upgrade.restore_deployment_files(records, read=live.get, replace=replace)
    assert not live
    upgrade.restore_deployment_files(records, read=live.get, replace=replace)


@pytest.mark.parametrize('role', services.INTEGRATIONS_ORDER)
def test_partial_native_service_contract_is_never_accepted(role):
    with pytest.raises(ValueError): services.service_order(set(services.INTEGRATIONS_ORDER) - {role, 'manager'})
    assert services.service_order(services.LEGACY_ORDER) == services.LEGACY_ORDER
    assert services.service_order(services.INTEGRATIONS_ORDER) == services.INTEGRATIONS_ORDER


def test_legacy_and_new_joint_upgrade_sets_remain_exact():
    for roles in (upgrade.CORE_ROLES, upgrade.LEGACY_NATIVE_ROLES, upgrade.NATIVE_ROLES):
        assert upgrade.service_roles(dict.fromkeys(roles), dict.fromkeys(roles)) == roles
    with pytest.raises(upgrade.UpgradeError):
        upgrade.service_roles(dict.fromkeys(upgrade.LEGACY_NATIVE_ROLES), dict.fromkeys(upgrade.NATIVE_ROLES))


@pytest.mark.parametrize('partial', [False, True])
def test_joint_publication_includes_exact_eight_sources_and_fourth_daemon_before_writes(monkeypatch, partial):
    monkeypatch.setattr(services.sys, 'platform', 'darwin')
    monkeypatch.setattr(services.os, 'geteuid', lambda: 0)
    snapshots = {name: b'VALUE = 1\n' for name in bundle.ODS_SERVICE_SOURCES}
    snapshots.update({'operations/broker.py': b'VALUE = 1\n', 'operations/policy.json': b'{}'})
    if partial: snapshots.pop(next(iter(bundle.INTEGRATION_SERVICE_ARTIFACTS)))
    events = []
    def module(name):
        if name == 'config': return SimpleNamespace(verified_services=lambda *a, **k: snapshots)
        def publication_files(**kwargs):
            events.append(('plan', name))
            result = [(kwargs['definition'], b'plist', 0o644, 0)]
            if name == 'integrations-service':
                assert set(kwargs['sources']) == set(bundle.INTEGRATION_PROGRAMS)
                assert kwargs['expected_sha256'] == {p: hashlib.sha256(v).hexdigest() for p, v in kwargs['sources'].items()}
                result += [(Path(kwargs['program_root']) / p, v, 0o644, 0) for p, v in kwargs['sources'].items()]
            return result
        def publish(**kwargs):
            events.append(('publish', name))
            return kwargs['definition']
        return SimpleNamespace(SOURCES=set(bundle.INTEGRATION_PROGRAMS), render=lambda **k: None,
            publication_files=publication_files, publish=publish,
            installer_helpers=lambda: SimpleNamespace(_preflight_file=lambda *a, **k: events.append(('preflight', a[0])),
                _write_exact=lambda *a, **k: events.append(('helper', a[0]))))
    monkeypatch.setattr(services, 'helper', module)
    options = dict(bundle='/approved', expected_digest='a' * 64, expected_ref='b' * 40,
        expected_config_digest='c' * 64, identity={'gid': 700}, owner='fixture', environment='/owner/.env',
        workspace='/workspace', port=3002, python='/usr/bin/python3')
    if partial:
        with pytest.raises(ValueError, match='integration-sources'): services.publish(**options)
        assert not events
    else:
        result = services.publish(**options)
        assert set(result) == set(services.INTEGRATIONS_ORDER)
        first_publish = next(i for i, e in enumerate(events) if e[0] == 'publish')
        assert all(i < first_publish for i, e in enumerate(events) if e[0] in ('plan', 'preflight'))


@pytest.mark.parametrize('failure', [None, 'bootstrap', 'readiness'])
def test_new_fourth_service_initial_activation_preserves_complete_checkpoint_and_rollback(monkeypatch, failure):
    monkeypatch.setattr(services.sys, 'platform', 'darwin')
    monkeypatch.setattr(services.os, 'geteuid', lambda: 0)
    events, checkpoints = [], []
    class InstallError(ValueError):
        returncode = 113
    def command(arguments, **kwargs):
        if arguments[1] == 'print': raise InstallError('absent')
        events.append(tuple(arguments[1:]))
        if failure == 'bootstrap' and arguments[1] == 'bootstrap' and 'integrations' in arguments[-1]:
            raise ValueError('bootstrap')
    installer = SimpleNamespace(InstallError=InstallError, _command=command, _job_disabled=lambda _: False)
    monkeypatch.setattr(services, 'helper', lambda _: SimpleNamespace(installer_helpers=lambda: installer))
    controls = {}
    for role in services.INTEGRATIONS_ORDER:
        controls[role] = SimpleNamespace(target='system/com.ods.pixel-native-' + role,
            plist=Path('/Library/LaunchDaemons/com.ods.pixel-native-' + role + '.plist'),
            verify_definition=lambda: None, process_identity=lambda: True,
            stop=lambda r=role, **k: events.append(('stop', r)),
            assert_stopped=lambda r=role: events.append(('stopped', r)))
    checks = {role: lambda r=role: not (failure == 'readiness' and r == 'integrations') for role in controls}
    if failure:
        with pytest.raises(ValueError):
            services.activate_new(services=controls, readiness=checks, checkpoint=checkpoints.append)
        assert [e[1] for e in events if e[0] == 'stop'] == list(reversed(services.INTEGRATIONS_ORDER))
        assert checkpoints[-1]['requiresRecovery'] is True
    else:
        services.activate_new(services=controls, readiness=checks, checkpoint=checkpoints.append)
        assert checkpoints[-1] == dict(phase='services-active', attempted=list(services.INTEGRATIONS_ORDER), requiresGatewayProof=True)


@pytest.mark.parametrize('failure', [None, *upgrade.NATIVE_ROLES, 'readiness'])
def test_joint_upgrade_restores_legacy_six_jobs_and_all_nine_absences(failure):
    """A failed new-capability bootstrap must never invent a previous daemon."""
    loaded = dict.fromkeys(upgrade.LEGACY_NATIVE_ROLES, 'previous')
    live = {}
    records = [dict(path=p, before=None, after=p.encode(), mode=0o644, gid=0)
               for p in sorted(upgrade.INTEGRATION_ADDITIONS)]
    phases, old_starts = [], []
    class Service:
        def __init__(self, role, version, absent=False):
            self.role, self.version, self.prior_absent = role, version, absent
            self.target = 'system/com.ods.pixel-' + role
        def assert_stopped(self):
            assert self.role not in loaded
    previous = {r: Service(r, 'previous', r == 'integrations') for r in upgrade.NATIVE_ROLES}
    candidate = {r: Service(r, 'candidate') for r in upgrade.NATIVE_ROLES}
    def stop(service):
        if service.prior_absent:
            assert service.role not in loaded
        else:
            loaded.pop(service.role, None)
    def start(service):
        if service.prior_absent:
            assert service.role not in loaded
            return
        loaded[service.role] = service.version
        if service.version == 'previous': old_starts.append(service.role)
        if service.version == 'candidate' and failure == service.role: raise RuntimeError('bootstrap')
    def replace(path, *, expected, replacement, **kwargs):
        assert live.get(path) == expected
        if replacement is None: live.pop(path, None)
        else: live[path] = replacement
    def ready(selection):
        expected = dict.fromkeys(upgrade.LEGACY_NATIVE_ROLES if selection is previous else upgrade.NATIVE_ROLES,
                                 'previous' if selection is previous else 'candidate')
        assert loaded == expected
        if selection is candidate and failure == 'readiness': raise RuntimeError('readiness')
    options = dict(previous=previous, candidate=candidate, phase=phases.append,
        verify=lambda: None, ready=ready, start=start, stop=stop,
        replace_files=lambda: upgrade.replace_deployment_files(records, read=live.get, replace=replace),
        restore_files=lambda: upgrade.restore_deployment_files(records, read=live.get, replace=replace))
    if failure:
        with pytest.raises(RuntimeError): upgrade.activate(**options)
        assert phases[-1] == 'restored'
        assert not live and set(old_starts) == set(upgrade.LEGACY_NATIVE_ROLES)
        assert loaded == dict.fromkeys(upgrade.LEGACY_NATIVE_ROLES, 'previous')
    else:
        upgrade.activate(**options)
        assert phases[-1] == 'active'
        assert loaded == dict.fromkeys(upgrade.NATIVE_ROLES, 'candidate')
        assert set(live) == upgrade.INTEGRATION_ADDITIONS
