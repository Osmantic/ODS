"""Provider Apply/Deactivate/Recover inside the existing root access service.

Root owns the original projection and service images; owner receipts can only
confirm this exact independently computed transaction. Registration verification
is deliberately separate from successful model transport. Recovery rolls back a
pending owner transaction, or reconciles its already-durable completion.
"""
import hashlib
import json
import os
import re
import uuid

from pixel_access_bridge import AccessError, atomic_json, digest
from pixel_access_protocol import HEX
from pixel_settings import coordinator as settings

from .activation_config import (
    _validate_plan,
    plan_activation,
    restore_activation,
    update_activation,
)
from .config import normalize_config
from .runtime_custody import RuntimeCustody
from .service_activation import activate, definition, stop_before_owner_change, verify
from .service_environment import ServiceEnvironment, _pair, _parents, _sync

PLAN = 'provider-root-plan.json'
MANAGED = 'provider-root-managed.json'
PROOF = 'provider-verified.json'
MAX_PLAN = 8 * 1024 * 1024


def _encoded(value):
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode('ascii')


def _binding(managed):
    return managed['plan']['fields']['binding']['after'] if managed else None


def _managed(value):
    if value is None:
        return None
    if type(value) is not dict or set(value) != {'plan', 'baseline', 'environment'}:
        raise AccessError('invalid-provider-root-state')
    _validate_plan(value['plan'])
    _pair(value['baseline'])
    _pair(value['environment'])
    return value


def _read_root(bridge, name):
    path = bridge.state / name
    _parents(path)
    return settings._read(path, 0, MAX_PLAN)[0] if os.path.lexists(path) else None


def _write(bridge, journal):
    _journal(journal)
    atomic_json(bridge.state / 'transition.json', journal)


def _journal(value):
    keys = {'kind', 'phase', 'token', 'transactionId', 'edge_revision', 'planHash',
            'providerRevision', 'runtimeCustody', 'boundary', 'mode', 'serviceDefinition'}
    optional = {'serviceEnvironment', 'restartIdentity', 'providerServiceVerified', 'error', 'outcome', 'noOwnerWrite'}
    if (type(value) is not dict or not keys <= set(value) or set(value) - keys - optional
            or value['kind'] != 'provider' or value['phase'] not in ('acquiring', 'invoking', 'restarting', 'releasing')
            or any(type(value[key]) is not str or not HEX.fullmatch(value[key])
                   for key in ('token', 'transactionId', 'edge_revision', 'planHash', 'runtimeCustody', 'serviceDefinition'))
            or type(value['providerRevision']) is not int or not 0 <= value['providerRevision'] < 2**53
            or value['mode'] not in ('sandboxed', 'full-access')
            or type(value['boundary']) is not str or not 1 <= len(value['boundary']) <= 4096
            or 'serviceEnvironment' in value and not HEX.fullmatch(str(value['serviceEnvironment']))
            or 'restartIdentity' in value and not settings._valid_identity(value['restartIdentity'])
            or 'outcome' in value and value['outcome'] not in ('applied', 'rolled-back')
            or 'noOwnerWrite' in value and value['noOwnerWrite'] is not True):
        raise AccessError('invalid-provider-transition')
    return value


def _record(bridge, journal):
    value = _read_root(bridge, PLAN)
    keys = {'schemaVersion', 'transactionId', 'beforeSha', 'afterSha', 'beforeConfig',
            'previous', 'nextPlan', 'sourceHash', 'deployment', 'policy'}
    if (type(value) is not dict or set(value) != keys or value['schemaVersion'] != 1
            or value['transactionId'] != journal['transactionId'] or digest(value) != journal['planHash']
            or any(type(value[key]) is not str or not HEX.fullmatch(value[key])
                   for key in ('beforeSha', 'afterSha', 'sourceHash'))):
        raise AccessError('invalid-provider-root-plan')
    _managed(value['previous'])
    if value['nextPlan'] is not None:
        _validate_plan(value['nextPlan'])
    return value


def _inputs(bridge, directory):
    bridge.settings_source()
    saved, source_hash = settings._read(directory / 'provider-config.json', bridge.owner.pw_uid, 256 * 1024)
    saved = normalize_config(saved)
    config, checksum = settings._read(bridge.home / '.openclaw/openclaw.json', bridge.owner.pw_uid)
    managed = _managed(_read_root(bridge, MANAGED))
    return saved, source_hash, config, checksum, managed


def _revision(snapshot, source_hash, managed, custody, journal):
    return digest([snapshot['revision'], journal if journal else [source_hash, managed, custody]])


def _acquire(bridge, journal):
    edge = bridge.edge()
    revision = edge['revision'] if edge.get('phase') == 'idle' else journal['edge_revision']
    edge = bridge.edge('recover' if edge.get('phase') == 'interrupted' else 'acquire', journal['token'], revision)
    journal['edge_revision'] = edge['revision']
    _write(bridge, journal)
    bridge.native('acquire', journal['token'])
    if settings._busy(bridge, journal):
        raise AccessError('runtime-busy')


def _prepare(bridge, journal, record, environment):
    previous = record['previous']
    before_binding = _binding(previous)
    after_binding = record['nextPlan']['fields']['binding']['after'] if record['nextPlan'] else None
    return environment.prepare(journal, before_sha=record['beforeSha'], after_sha=record['afterSha'],
        before_binding=before_binding, after_binding=after_binding, deployment_document=record['deployment'],
        policy=record['policy'], expected=previous['environment'] if previous else None,
        restore=previous['baseline'] if after_binding is None else None)


def _start(bridge, request, snapshot, directory, runtime, environment):
    saved, source_hash, config, checksum, managed = _inputs(bridge, directory)
    custody = runtime.qualify()
    if (request['providerRevision'] != saved['revision']
            or request['revision'] != _revision(snapshot, source_hash, managed, custody, None)):
        raise AccessError('provider-inspection-changed')
    if snapshot['busy'] or snapshot['configured_mode'] not in ('sandboxed', 'full-access'):
        raise AccessError('runtime-busy-or-unqualified')
    settings._identity(bridge)  # An unrelated stopped unit is not start authority.
    owner = bridge.worker('provider-status')
    if owner['pending'] or owner['configSha256'] != checksum or owner['binding'] != _binding(managed):
        raise AccessError('provider-owner-state-changed')
    if request['operation'] == 'deactivate':
        if managed is None:
            raise AccessError('provider-not-managed')
        after, next_plan = restore_activation(config, managed['plan']), None
        deployment_document, policy = None, None
    else:
        if not saved['enabled']:
            raise AccessError('provider-policy-disabled')
        # Bootstrap registration does not exercise the owner-derived route
        # worker. Refuse stale/missing artifacts before any journal or restart.
        runtime.require_worker(directory, custody)
        options = {'revision': saved['revision'], 'allow_cloud': saved['policy']['allowCloud'], 'activation_id': str(uuid.uuid4())}
        next_plan = (update_activation(config, managed['plan'], **options) if managed else plan_activation(config, **options))
        after = next_plan['document']
        deployment_document, policy, check = runtime.deployment(next_plan['fields']['binding']['after'], directory)
        if check != custody:
            raise AccessError('provider-runtime-custody-changed')
    expected = managed['environment'] if managed else {'environment': None, 'dropin': None}
    if environment.snapshot() != expected:
        raise AccessError('provider-service-baseline-conflict')
    transaction = os.urandom(32).hex()
    record = {'schemaVersion': 1, 'transactionId': transaction, 'beforeSha': checksum,
              'afterSha': hashlib.sha256(_encoded(after)).hexdigest(), 'beforeConfig': config,
              'previous': managed, 'nextPlan': next_plan, 'sourceHash': source_hash,
              'deployment': deployment_document, 'policy': policy}
    if len(_encoded(record)) > MAX_PLAN:
        raise AccessError('provider-root-plan-too-large')
    journal = {'kind': 'provider', 'phase': 'acquiring', 'token': os.urandom(32).hex(),
               'transactionId': transaction, 'edge_revision': snapshot['_edge']['revision'],
               'planHash': digest(record), 'providerRevision': saved['revision'], 'runtimeCustody': custody,
               'boundary': bridge.unit_boundary(), 'mode': snapshot['configured_mode'],
               'serviceDefinition': definition(bridge, environment.dropin)}
    # Neither root record grants mutation until the durable transition exists.
    atomic_json(bridge.state / PLAN, record)
    _write(bridge, journal)
    return journal, record


def _completion(bridge, journal, record, state, result):
    outcome = {'registration-verified': 'applied', 'rolled-back': 'rolled-back'}.get(result.get('status'))
    binding = (record['nextPlan']['fields']['binding']['after'] if record['nextPlan'] else None
               ) if outcome == 'applied' else _binding(record['previous'])
    checksum = record['afterSha' if outcome == 'applied' else 'beforeSha']
    if (outcome is None or state['pending'] or state['configSha256'] != checksum or state['binding'] != binding
            or result.get('configSha256') != checksum or result.get('binding') != binding
            or settings._read(bridge.home / '.openclaw/openclaw.json', bridge.owner.pw_uid)[1] != checksum):
        raise AccessError('provider-completion-mismatch')
    expected = {'transactionId': journal['transactionId'], 'binding': binding, 'outcome': outcome, 'configSha256': checksum}
    if journal.get('noOwnerWrite'):
        if outcome != 'rolled-back':
            raise AccessError('provider-completion-mismatch')
    elif state['completion'] != expected:
        raise AccessError('provider-completion-mismatch')
    return outcome


def _finish(bridge, journal, record, environment, runtime, outcome):
    images, side = environment.select(journal)
    if side != ('after' if outcome == 'applied' else 'before'):
        raise AccessError('provider-completion-mismatch')
    selection = {'side': side, 'binding': images[side + 'Binding'], 'environmentHash': digest(images[side])}
    proof = verify(bridge, journal, environment, selection, qualify_runtime=runtime.qualify)
    runtime.verify_process()
    next_state = record['previous']
    if outcome == 'applied':
        next_state = ({'plan': record['nextPlan'], 'environment': images['after'],
                       'baseline': record['previous']['baseline'] if record['previous'] else images['before']}
                      if record['nextPlan'] else None)
    journal.update(phase='releasing', outcome=outcome)
    _write(bridge, journal)
    atomic_json(bridge.state / MANAGED, next_state)
    atomic_json(bridge.state / PROOF, dict(proof, transactionId=journal['transactionId'], outcome=outcome,
                                         runtimeCustody=journal['runtimeCustody'], boundary=journal['boundary'],
                                         serviceDefinition=journal['serviceDefinition']))
    bridge.edge('release', journal['token'], journal['edge_revision'])
    bridge.native('release', journal['token'])
    (bridge.state / 'transition.json').unlink()
    try:
        _sync(bridge.state)
    except OSError:
        _write(bridge, journal)
        raise
    return {'outcome': outcome, 'binding': selection['binding'], 'registrationVerified': True, 'transportVerified': False}


def change(bridge, request):
    if (type(request) is not dict or set(request) != {'operation', 'revision', 'providerRevision'}
            or request['operation'] not in ('apply', 'deactivate', 'recover')
            or type(request['revision']) is not str or not HEX.fullmatch(request['revision'])
            or type(request['providerRevision']) is not int or not 0 <= request['providerRevision'] < 2**53):
        raise AccessError('invalid-provider-request')
    with bridge.locked():
        snapshot = bridge.inspect()
        with settings._store(bridge, exclusive=True) as directory:
            runtime, environment = RuntimeCustody(bridge), ServiceEnvironment(bridge)
            journal = bridge.pending()
            if request['operation'] != 'recover':
                if journal:
                    raise AccessError('transition-recovery-required')
                journal, record = _start(bridge, request, snapshot, directory, runtime, environment)
            else:
                if not journal or journal.get('kind') != 'provider':
                    raise AccessError('provider-recovery-unavailable')
                _journal(journal)
                if (request['revision'] != _revision(snapshot, None, None, None, journal)
                        or request['providerRevision'] != journal['providerRevision']):
                    raise AccessError('provider-inspection-changed')
                record = _record(bridge, journal)
            try:
                if runtime.qualify() != journal['runtimeCustody']:
                    raise AccessError('provider-runtime-custody-changed')
                _acquire(bridge, journal)
                if 'serviceEnvironment' not in journal:
                    _prepare(bridge, journal, record, environment)
                callback = lambda: activate(bridge, journal, environment, qualify_runtime=runtime.qualify)
                if request['operation'] != 'recover':
                    if _inputs(bridge, directory)[1] != record['sourceHash']:
                        raise AccessError('provider-source-changed')
                    journal['phase'] = 'invoking'
                    _write(bridge, journal)
                    stop_before_owner_change(bridge, journal, environment, qualify_runtime=runtime.qualify)
                    binding = record['nextPlan']['fields']['binding']['after'] if record['nextPlan'] else None
                    projection = {'afterSha': record['afterSha'], 'previousPlanSha':
                                  hashlib.sha256(_encoded(record['previous']['plan'])).hexdigest() if record['previous'] else None}
                    result = bridge.worker('provider-change', binding=binding, config_hash=record['beforeSha'],
                        transaction_id=journal['transactionId'], expected_projection=projection,
                        busy=lambda: settings._busy(bridge, journal), activate_provider=callback)
                else:
                    state = bridge.worker('provider-status')
                    if state['pending']:
                        result = bridge.worker('provider-recover', config_hash=state['configSha256'],
                            transaction_id=journal['transactionId'], busy=lambda: settings._busy(bridge, journal), activate_provider=callback)
                    elif state['completion'] and state['completion']['transactionId'] == journal['transactionId']:
                        done = state['completion']
                        result = {'status': 'registration-verified' if done['outcome'] == 'applied' else 'rolled-back',
                                  'binding': done['binding'], 'configSha256': done['configSha256']}
                    elif (state['configSha256'] == record['beforeSha'] and state['binding'] == _binding(record['previous'])
                          and (journal['phase'] in ('acquiring', 'invoking') or journal.get('noOwnerWrite') is True)):
                        journal['noOwnerWrite'] = True
                        _write(bridge, journal)
                        # A refused write can leave our deliberately stopped
                        # gateway on the original config. Restore its service
                        # before final verification, without restarting a live
                        # original process or replaying a durable completion.
                        try:
                            settings._identity(bridge)
                        except AccessError as error:
                            if error.code != 'settings-process-not-active':
                                raise
                            if callback() != 'verified':
                                raise AccessError('provider-original-runtime-unverified')
                        result = {'status': 'rolled-back', 'binding': _binding(record['previous']), 'configSha256': record['beforeSha']}
                    else:
                        raise AccessError('provider-recovery-conflict')
                outcome = _completion(bridge, journal, record, bridge.worker('provider-status'), result)
                return _finish(bridge, journal, record, environment, runtime, outcome)
            except Exception as error:
                code = str(error)
                journal['error'] = code if re.fullmatch(r'[a-z0-9-]{1,96}', code) else 'provider-transition-failed'
                _write(bridge, journal)  # Retain exact phase/authority, then propagate.
                raise


def status(bridge):
    snapshot, journal = bridge.inspect(), bridge.pending()
    if journal:
        if journal.get('kind') != 'provider':
            raise AccessError('transition-recovery-required')
        _journal(journal)
        return {'status': 'pending', 'revision': _revision(snapshot, None, None, None, journal),
                'providerRevision': journal['providerRevision'], 'binding': None, 'pending': True,
                'registrationVerified': False, 'transportVerified': False, 'lastVerifiedAt': None}
    with settings._store(bridge, exclusive=False) as directory:
        saved, source_hash, _config, checksum, managed = _inputs(bridge, directory)
        owner = bridge.worker('provider-status')
        if owner['pending']:
            raise AccessError('provider-recovery-journal-missing')
        if owner['configSha256'] != checksum or owner['binding'] != _binding(managed):
            raise AccessError('provider-owner-state-changed')
        runtime = RuntimeCustody(bridge)
        custody = runtime.qualify()
        proof = _read_root(bridge, PROOF) or {}
        verified = (snapshot['runtime_verified'] is True and proof.get('configSha256') == checksum
                    and proof.get('binding') == _binding(managed) and proof.get('runtimeCustody') == custody
                    and proof.get('boundary') == bridge.unit_boundary()
                    and proof.get('serviceDefinition') == definition(bridge, ServiceEnvironment(bridge).dropin)
                    and all(proof.get(k) == v for k, v in settings._identity(bridge).items()))
        if verified:
            runtime.verify_process()
            if ServiceEnvironment(bridge).snapshot() != (managed['environment'] if managed else {'environment': None, 'dropin': None}):
                verified = False
        state = 'not-applied'
        if verified:
            state = 'inactive' if not managed else ('applied' if _binding(managed)['revision'] == saved['revision'] else 'saved-changes')
        return {'status': state, 'revision': _revision(snapshot, source_hash, managed, custody, None),
                'providerRevision': saved['revision'], 'binding': _binding(managed) if verified else None,
                'pending': False, 'registrationVerified': verified, 'transportVerified': False,
                'lastVerifiedAt': proof.get('observedAt') if verified else None}
