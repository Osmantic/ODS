#!/usr/bin/env python3
"""Read-only eligibility inspection for interrupted initial activation.

Default inspection establishes custody for a possible recovery. It does not
publish readiness, change services or files, or acquire or release admission.
Internal completion=True additionally permits the durable phases that a
retryable completion may observe, but still performs no mutation.
"""
import json
import os
import re
import sys
from pathlib import Path


class RecoveryError(Exception):
    pass


_HEX64 = re.compile(r'[a-f0-9]{64}')
_PENDING = ('runtime-upgrade.json', 'transition.json', 'policy-activation.json',
            'retirement.json')
_EXPECTED_ATTEMPTED = ['manager', 'promoter', 'operations']
_MANAGED_KEYS = ('manager', 'promoter', 'operations')
_READY_ACCESS_TIMEOUT = 15


def _fail(code):
    raise RecoveryError(code)


def _require(condition, code):
    if not condition:
        _fail(code)


def _private_json(api, path, maximum):
    from pixel_access_bridge import private_json
    return private_json(path, 0, maximum)


def _protected_bytes(api, path):
    from pixel_macos_custody import protected_bytes
    return protected_bytes(path)


def _state(api):
    return Path(api._launchd.ACCESS_STATE)


def _edge_path(api):
    return _state(api) / 'installation-edge.json'


def _is_exact_int(value, expected):
    return type(value) is int and value == expected


def _is_hex64(value):
    return isinstance(value, str) and _HEX64.fullmatch(value) is not None


def _check_plan(plan):
    _require(type(plan) is dict, 'complete-initial-service-selection-required')
    _require(plan.get('initial_install') is True,
             'complete-initial-service-selection-required')
    _require('upgrade_qualification' not in plan,
             'runtime-upgrade-plan-required')
    _require('migration' not in plan and 'migration_qualification' not in plan,
             'runtime-upgrade-plan-required')
    owner = plan.get('owner')
    _require(owner is not None, 'complete-initial-service-selection-required')
    uid = getattr(owner, 'pw_uid', None)
    _require(type(uid) is int and uid > 0,
             'complete-initial-service-selection-required')
    _require(isinstance(getattr(owner, 'pw_name', None), str),
             'complete-initial-service-selection-required')
    _require(isinstance(plan.get('source'), (str, Path)),
             'complete-initial-service-selection-required')
    bundle = plan.get('runtime_bundle')
    _require(type(bundle) is dict, 'complete-initial-service-selection-required')
    _require(_is_hex64(bundle.get('digest')),
             'complete-initial-service-selection-required')
    services = plan.get('native_services')
    _require(type(services) is dict, 'complete-initial-service-selection-required')
    _require(_is_hex64(services.get('expected_digest')),
             'complete-initial-service-selection-required')
    settings = plan.get('access_settings')
    _require(type(settings) is dict, 'complete-initial-service-selection-required')
    _require(type(settings.get('gateway_port')) is int,
             'complete-initial-service-selection-required')
    _require(type(settings.get('gateway_policy')) is dict,
             'complete-initial-service-selection-required')


def _check_installation(plan, record, *, completion):
    _require(type(record) is dict, 'native-initial-recovery-record-invalid')
    _require(_is_exact_int(record.get('schemaVersion'), 1),
             'native-initial-recovery-record-invalid')
    phase = record.get('phase')
    if completion:
        _require(phase in ('starting-access-relay', 'active'),
                 'native-initial-recovery-phase-not-interrupted')
    else:
        _require(phase == 'starting-access-relay',
                 'native-initial-recovery-phase-not-interrupted')
    _require(record.get('operation') == 'initial-install',
             'native-initial-recovery-operation-invalid')
    _require(_is_exact_int(record.get('owner'), plan['owner'].pw_uid),
             'native-initial-recovery-owner-invalid')
    _require(record.get('source') == str(plan['source']),
             'native-initial-recovery-source-invalid')
    _require(set(record) == {'schemaVersion', 'phase', 'operation', 'owner', 'source'},
             'native-initial-recovery-record-invalid')


def _check_edge(edge, *, completion):
    _require(type(edge) is dict, 'native-initial-recovery-edge-invalid')
    _require(_is_exact_int(edge.get('schemaVersion'), 1),
             'native-initial-recovery-edge-invalid')
    phase = edge.get('phase')
    if completion:
        _require(phase in ('held', 'releasing', 'released'),
                 'native-initial-recovery-edge-not-held')
    else:
        _require(phase == 'held', 'native-initial-recovery-edge-not-held')
    _require(_is_hex64(edge.get('container')),
             'native-initial-recovery-edge-invalid')
    binding = edge.get('binding')
    _require(type(binding) is dict and set(binding) == {'token', 'revision'},
             'native-initial-recovery-edge-invalid')
    _require(_is_hex64(binding.get('token')) and _is_hex64(binding.get('revision')),
             'native-initial-recovery-edge-invalid')
    allowed = {'schemaVersion', 'container', 'binding', 'phase', 'status'}
    _require(set(edge) == allowed, 'native-initial-recovery-edge-invalid')
    if 'status' in edge:
        status = edge['status']
        _require(type(status) is dict, 'native-initial-recovery-edge-invalid')
        _require(status.get('capability') == 'available',
                 'native-initial-recovery-edge-invalid')
        _require(status.get('phase') == 'held', 'native-initial-recovery-edge-invalid')
        _require(status.get('admission_blocked') is True,
                 'native-initial-recovery-edge-invalid')
        _require(_is_exact_int(status.get('streams'), 0),
                 'native-initial-recovery-edge-invalid')
        _require(status.get('revision') == binding['revision'],
                 'native-initial-recovery-edge-invalid')
    return binding['revision']


def _check_service_record(plan, record):
    _require(type(record) is dict, 'native-initial-recovery-services-invalid')
    _require(_is_exact_int(record.get('schemaVersion'), 1),
             'native-initial-recovery-services-invalid')
    _require(record.get('attempted') == _EXPECTED_ATTEMPTED,
             'native-initial-recovery-services-invalid')
    _require(not record.get('stopWitnesses'), 'native-initial-recovery-services-invalid')
    _require(not record.get('requiresRecovery'), 'native-initial-recovery-services-invalid')
    progress = record.get('progress')
    _require(type(progress) is dict and progress.get('phase') == 'services-active',
             'native-initial-recovery-services-invalid')
    _require(_is_exact_int(record.get('owner'), plan['owner'].pw_uid),
             'native-initial-recovery-services-invalid')
    _require(record.get('selection') == plan.get('native_services'),
             'native-initial-recovery-services-invalid')


def _check_settings(plan, settings_bytes):
    try:
        parsed = json.loads(settings_bytes)
    except (ValueError, UnicodeError):
        _fail('native-initial-recovery-configuration-invalid')
    _require(parsed == plan['access_settings'],
             'native-initial-recovery-configuration-changed')


def _check_bundle(api, plan):
    api._verify_bundle_selection(plan)


def _check_services(api, plan):
    services = api._activation_services(plan)[1:]
    _require(len(services) == 3, 'native-initial-recovery-services-invalid')
    for service in services:
        service.verify()
        _require(not api._job_disabled(service.target),
                 'native-initial-recovery-disabled-job')
    return services


def _check_access(api, plan, services):
    value = api._ready_access(services[1], timeout=_READY_ACCESS_TIMEOUT)
    _require(type(value) is dict, 'native-initial-recovery-access-invalid')
    _require(value.get('surface') == 'darwin', 'native-initial-recovery-access-invalid')
    _require(value.get('pending') is False, 'native-initial-recovery-access-invalid')
    _require(value.get('available') is True, 'native-initial-recovery-access-invalid')
    runtime_verified = value.get('runtime_verified')
    if runtime_verified is False:
        _require(value.get('effective_mode') == 'unknown',
                 'native-initial-recovery-access-invalid')
        _require(value.get('reason') == 'runtime-proof-required',
                 'native-initial-recovery-access-invalid')
    elif runtime_verified is True:
        _require(api._repair.access_ready(value),
                 'native-initial-recovery-access-invalid')
        expected = api._policy.policy_state(
            plan['access_settings']['gateway_policy'])['activeMode']
        _require(value.get('effective_mode') == expected,
                 'native-initial-recovery-access-invalid')
    else:
        _fail('native-initial-recovery-access-invalid')
    return value


def _check_relay(api, plan, services):
    api._ready_access_relay(services[2], plan)


def _check_gateway(api, plan, services):
    api._ready_gateway(services[0], plan['access_settings']['gateway_port'])


def _check_edge_live(api, plan, edge, revision):
    """Validate the live edge state against the durable edge record.

    The stored edge status remains the original held status; only the live
    edge request may report releasing/released. Idle is never inferred as
    release success; it is only accepted when the durable record already
    says releasing or released.
    """
    phase = edge['phase']
    value = api._migration_edge_request(plan, edge['container'])
    _require(type(value) is dict, 'native-initial-recovery-hold-invalid')
    _require(value.get('capability') == 'available', 'native-initial-recovery-hold-invalid')
    if phase == 'held':
        _require(value.get('phase') == 'held', 'native-initial-recovery-hold-invalid')
        _require(value.get('admission_blocked') is True,
                 'native-initial-recovery-hold-invalid')
        _require(_is_exact_int(value.get('streams'), 0),
                 'native-initial-recovery-hold-invalid')
        _require(value.get('revision') == revision,
                 'native-initial-recovery-hold-invalid')
    elif phase == 'releasing':
        live_phase = value.get('phase')
        if live_phase == 'held':
            _require(value.get('admission_blocked') is True,
                     'native-initial-recovery-hold-invalid')
            _require(_is_exact_int(value.get('streams'), 0),
                     'native-initial-recovery-hold-invalid')
            _require(value.get('revision') == revision,
                     'native-initial-recovery-hold-invalid')
        elif live_phase == 'idle':
            _require(value.get('admission_blocked') is False,
                     'native-initial-recovery-hold-invalid')
            _require(_is_exact_int(value.get('streams'), 0),
                     'native-initial-recovery-hold-invalid')
        else:
            _fail('native-initial-recovery-hold-invalid')
    elif phase == 'released':
        _require(value.get('phase') == 'idle', 'native-initial-recovery-hold-invalid')
        _require(value.get('admission_blocked') is False,
                 'native-initial-recovery-hold-invalid')
        _require(_is_exact_int(value.get('streams'), 0),
                 'native-initial-recovery-hold-invalid')
    else:
        _fail('native-initial-recovery-hold-invalid')


def _check_verify_new_services(api, plan):
    api._verify_new_services(plan)


def _check_no_pending(api):
    state = _state(api)
    for name in _PENDING:
        _require(not os.path.lexists(state / name),
                 'native-initial-recovery-transition-pending')


def _identities(api, services):
    return [api._upgrade_service_identity(service) for service in services]


def _managed_adapters(api, plan, service_record):
    recovery = service_record.get('recovery')
    _require(type(recovery) is dict, 'native-initial-recovery-services-invalid')
    adapters = api._native_services.recovery_adapters(
        recovery, owner=plan['owner'].pw_name, save_stop=None, load_stop=None)
    _require(type(adapters) is dict and set(adapters) == set(_MANAGED_KEYS),
             'native-initial-recovery-services-invalid')
    return adapters


def _managed_identities(adapters):
    return {key: adapters[key].process_identity() for key in sorted(_MANAGED_KEYS)}


def _recheck_records(api, plan, journal_path, record, edge_path, edge,
                     service_path, service_record, settings_path, settings_bytes,
                     *, completion):
    _check_no_pending(api)
    _require(_private_json(api, journal_path, 65536) == record,
             'native-initial-recovery-record-changed')
    _require(_private_json(api, edge_path, 65536) == edge,
             'native-initial-recovery-edge-changed')
    _require(_private_json(api, service_path, 2 * 1024 * 1024) == service_record,
             'native-initial-recovery-services-changed')
    _require(_protected_bytes(api, settings_path) == settings_bytes,
             'native-initial-recovery-configuration-changed')
    _check_installation(plan, record, completion=completion)
    _check_edge(edge, completion=completion)
    _check_service_record(plan, service_record)
    _check_settings(plan, settings_bytes)


def inspect(plan, api, *, completion=False):
    """Read-only eligibility inspection for interrupted initial activation.

    Default (completion=False) permits only starting-access-relay with a held
    edge and remains strictly read-only. Internal completion=True additionally
    permits active with held/releasing/released edge, still without mutation.

    Returns a public dict with status, runtimeDigest, serviceDigest and
    installationPhase. Never returns tokens, configs, or readiness claims.
    """
    _require(sys.platform == 'darwin', 'macos-root-install-required')
    _require(os.geteuid() == 0, 'macos-root-install-required')
    _check_plan(plan)

    _check_no_pending(api)
    state = _state(api)
    journal_path = state / 'installation.json'
    edge_path = _edge_path(api)
    service_path = state / 'service-installation.json'
    settings_path = api._destination(api._launchd.ACCESS_CONFIG)

    record = _private_json(api, journal_path, 65536)
    _check_installation(plan, record, completion=completion)
    edge = _private_json(api, edge_path, 65536)
    revision = _check_edge(edge, completion=completion)
    _require(record['phase'] == 'active' or edge['phase'] == 'held',
             'native-initial-recovery-phase-pair-invalid')
    service_record = _private_json(api, service_path, 2 * 1024 * 1024)
    _check_service_record(plan, service_record)
    settings_bytes = _protected_bytes(api, settings_path)
    _check_settings(plan, settings_bytes)

    _check_bundle(api, plan)
    services = _check_services(api, plan)
    identities = _identities(api, services)
    adapters = _managed_adapters(api, plan, service_record)
    managed_identities = _managed_identities(adapters)

    _check_gateway(api, plan, services)
    access_value = _check_access(api, plan, services)
    _check_relay(api, plan, services)
    _check_edge_live(api, plan, edge, revision)
    _check_verify_new_services(api, plan)

    # Re-sample everything that could have changed during readiness checks.
    _recheck_records(api, plan, journal_path, record, edge_path, edge,
                     service_path, service_record, settings_path, settings_bytes,
                     completion=completion)
    _require(_identities(api, services) == identities,
             'native-initial-recovery-process-changed')
    _require(_managed_identities(adapters) == managed_identities,
             'native-initial-recovery-process-changed')
    _check_bundle(api, plan)
    for service in services:
        service.verify()
        _require(not api._job_disabled(service.target),
                 'native-initial-recovery-disabled-job')
    _check_edge_live(api, plan, edge, revision)
    access_value = _check_access(api, plan, services)
    _check_verify_new_services(api, plan)

    # Final proof: records, config, PIDs, and managed identities unchanged
    # after ALL slow checks (including the second bundle and services proof).
    _recheck_records(api, plan, journal_path, record, edge_path, edge,
                     service_path, service_record, settings_path, settings_bytes,
                     completion=completion)
    _require(_identities(api, services) == identities,
             'native-initial-recovery-process-changed')
    _require(_managed_identities(adapters) == managed_identities,
             'native-initial-recovery-process-changed')
    _check_edge_live(api, plan, edge, revision)
    _check_no_pending(api)

    return {
        'status': 'interrupted-initial-inspection-passed',
        'runtimeDigest': plan['runtime_bundle']['digest'],
        'serviceDigest': plan['native_services']['expected_digest'],
        'installationPhase': record['phase'],
        'runtimeVerified': access_value.get('runtime_verified') is True,
    }


def complete(plan, api):
    """Finish only the already running, fully proved initial service set.

    Existing installation and edge journals make the activation/release
    sequence retryable. Controller reconciliation runs after releasing its
    lock, and final verification remains the existing readiness authority.
    """
    _require(sys.platform == 'darwin' and os.geteuid() == 0,
             'macos-root-install-required')
    _check_plan(plan)
    owner = api.pwd.getpwnam(plan['owner'].pw_name)
    _require(owner.pw_uid > 0 and (owner.pw_uid, owner.pw_gid) ==
             (plan['owner'].pw_uid, plan['owner'].pw_gid),
             'native-migration-owner-changed')
    bridge = api._bridge_from_recovery_plan(plan, owner_name=owner.pw_name)
    journal = _state(api) / 'installation.json'
    with bridge.locked():
        inspect(plan, api, completion=True)
        record = _private_json(api, journal, 65536)
        if record['phase'] == 'starting-access-relay':
            api._migration_phase(plan, journal, 'active')
        edge = _private_json(api, _edge_path(api), 65536)
        if edge['phase'] in ('held', 'releasing'):
            api._finish_migration_hold(plan, edge,
                verify_before_release=lambda: inspect(plan, api, completion=True))
        else:
            _require(edge['phase'] == 'released', 'native-initial-recovery-edge-not-held')
    api._reprove_installed_access(plan)
    return api.verify_initial_install(plan)
