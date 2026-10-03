"""One reviewed controller correction under an existing native admission hold.

Pure intent/projection/state-machine code. The privileged installer supplies
custody and launchd adapters; no arbitrary file, patch or digest is admitted.
The original runtime archive is never modified, and this repair never releases
admission or claims that the gateway's tool proof is ready.
"""
import base64
import copy
import hashlib
import json
import re

REPAIR = 'darwin-owner-groups-v1'
KIND = 'ods-pixel-controller-repair'
TARGET = '/usr/local/libexec/ods-pixel-access/pixel_access_bridge.py'
BEFORE = '03782ca98b4caeb6a86dcbde1ef240335f49e5384035832566e1c5334e26e73e'
AFTER = '90daa9ec87c571ed9f979a243df18c739a9708b7283f058be44b94af109cb29d'
LIMIT = 256 * 1024
PHASES = {'prepared', 'stopping', 'replacing', 'starting', 'verifying', 'repaired',
          'rollback-stopping', 'rollback-replacing', 'rollback-starting', 'rolled-back'}
OLD_HUNK = b"""        return {'user': owner.pw_uid, 'group': owner.pw_gid,
                'extra_groups': os.getgrouplist(owner.pw_name, owner.pw_gid)}
"""
NEW_HUNK = b"""        # The native worker only needs the owner's UID and primary GID for its
        # owner-owned files and Docker socket. Do not carry root's groups into
        # the child: macOS users can exceed subprocess's setgroups limit.
        return {'user': owner.pw_uid, 'group': owner.pw_gid,
                'extra_groups': []}
"""


class RepairError(ValueError):
    pass


def sha(body):
    return hashlib.sha256(body).hexdigest()


def _fail():
    raise RepairError('controller-repair-authority-mismatch')


def replacement(before):
    if type(before) is not bytes or sha(before) != BEFORE or before.count(OLD_HUNK) != 1:
        _fail()
    after = before.replace(OLD_HUNK, NEW_HUNK, 1)
    if sha(after) != AFTER:
        _fail()
    return after


def _object(raw, limit):
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                _fail()
            result[key] = value
        return result
    if type(raw) is not bytes or len(raw) > limit:
        _fail()
    try:
        value = json.loads(raw, object_pairs_hook=unique)
    except (ValueError, TypeError):
        _fail()
    if type(value) is not dict:
        _fail()
    return value


def _hold(raw):
    value = _object(raw, 65536)
    if (set(value) != {'schemaVersion', 'container', 'binding', 'phase', 'status'}
            or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
            or type(value['container']) is not str or not re.fullmatch('[a-f0-9]{64}', value['container'])
            or type(value['binding']) is not dict or set(value['binding']) != {'token', 'revision'}
            or any(type(item) is not str or not re.fullmatch('[a-f0-9]{64}', item) for item in value['binding'].values())
            or value['phase'] not in ('held', 'releasing', 'released')):
        _fail()
    status = value['status']
    if (type(status) is not dict or status.get('capability') != 'available'
            or status.get('phase') != 'held' or type(status.get('streams')) is not int
            or status['streams'] != 0 or status.get('admission_blocked') is not True
            or status.get('revision') != value['binding']['revision']):
        _fail()
    return value


def _bridge_record(archive, current, candidate):
    value = _object(archive, 32 * 1024 * 1024)
    if (value.get('phase') != 'active' or value.get('currentDigest') != current
            or value.get('candidateDigest') != candidate or type(value.get('files')) is not list):
        _fail()
    matches = [item for item in value['files'] if type(item) is dict and item.get('path') == TARGET]
    if len(matches) != 1:
        _fail()
    item = matches[0]
    if (type(item.get('mode')) is not int or item['mode'] != 0o644
            or type(item.get('gid')) is not int or item['gid'] != 0 or item.get('afterSha256') != BEFORE):
        _fail()
    try:
        before = base64.b64decode(item['after'], validate=True)
    except (ValueError, KeyError, TypeError):
        _fail()
    replacement(before)
    return before


def make_intent(*, current, candidate, owner, snapshots, identities):
    if (any(type(value) is not str or not re.fullmatch('[a-f0-9]{64}', value)
            for value in (current, candidate)) or current == candidate
            or type(owner) is not dict or set(owner) != {'name', 'uid', 'gid'}
            or type(owner['name']) is not str or not re.fullmatch('[A-Za-z_][A-Za-z0-9_-]{0,127}', owner['name'])
            or type(owner['uid']) is not int or owner['uid'] <= 0
            or type(owner['gid']) is not int or owner['gid'] < 0
            or type(snapshots) is not dict or set(snapshots) != {'archive', 'context', 'service', 'hold'}
            or any(type(body) is not bytes for body in snapshots.values())):
        _fail()
    _bridge_record(snapshots['archive'], current, candidate)
    if _hold(snapshots['hold'])['phase'] != 'held':
        _fail()
    if (type(identities) is not dict or set(identities) != {'gateway', 'access', 'relay', 'operations', 'manager', 'promoter'}
            or any(type(value) is not list or len(value) != (3 if role == 'access' else 10)
                   or any(type(item) is not int or item < 0 for item in value[:9])
                   or len(value) == 10 and (type(value[-1]) is not str or not value[-1].startswith('/'))
                   or value[0] <= 0 or value[1] <= 0 or value[2] >= 1000000
                   for role, value in identities.items())):
        _fail()
    return {'schemaVersion': 1, 'kind': KIND, 'repair': REPAIR, 'currentDigest': current,
            'candidateDigest': candidate, 'owner': dict(owner), 'phase': 'prepared',
            'target': {'path': TARGET, 'mode': 0o644, 'uid': 0, 'gid': 0,
                       'beforeSha256': BEFORE, 'afterSha256': AFTER},
            'bindings': {key: sha(body) for key, body in snapshots.items()},
            'heldRecord': base64.b64encode(snapshots['hold']).decode('ascii'),
            'identities': copy.deepcopy(identities)}


def validate_intent(value, *, current, candidate, owner, snapshots, allow_released=False):
    try:
        if type(value) is not dict or value.get('phase') not in PHASES:
            _fail()
        held = base64.b64decode(value['heldRecord'], validate=True)
        original = dict(snapshots, hold=held)
        expected = make_intent(current=current, candidate=candidate, owner=owner,
                               snapshots=original, identities=value['identities'])
        expected['phase'] = value['phase']
        if (json.dumps(value, sort_keys=True) != json.dumps(expected, sort_keys=True)
                or len(json.dumps(value).encode()) > LIMIT):
            _fail()
        actual_hold, initial_hold = _hold(snapshots['hold']), _hold(held)
        if (actual_hold['phase'] != 'held' and not allow_released
                or dict(actual_hold, phase='held') != initial_hold):
            _fail()
        # The ordinary hold releaser serializes the same object with only its
        # top-level phase advanced. No other raw-byte drift is adopted.
        expected_hold = (held if actual_hold['phase'] == 'held' else
                         json.dumps(dict(initial_hold, phase=actual_hold['phase'])).encode())
        if snapshots['hold'] != expected_hold:
            _fail()
    except (KeyError, TypeError, ValueError):
        _fail()
    return value


def effective_records(records, intent, *, current, candidate, owner, snapshots):
    """One exact, terminal repair exception; every other archive byte is binding."""
    if intent is None:
        return records
    if type(intent) is not dict:
        _fail()
    validate_intent(intent, current=current, candidate=candidate, owner=owner,
                    snapshots=snapshots, allow_released=intent.get('phase') == 'repaired')
    if intent['phase'] == 'rolled-back':
        return records
    if intent['phase'] != 'repaired':
        raise RepairError('controller-repair-incomplete')
    before = _bridge_record(snapshots['archive'], current, candidate)
    result, changed = [], 0
    for item in records:
        if item['path'] == TARGET:
            if item['after'] != before or item['mode'] != 0o644 or item['gid'] != 0:
                _fail()
            item = dict(item, after=replacement(before))
            changed += 1
        result.append(item)
    if changed != 1:
        _fail()
    return result


def require_finalizable(intent, snapshots):
    """Additional condition after full intent/projection validation by caller.

    A repaired controller is not a ready runtime. Ordinary recovery must first
    release this same hold; the caller must also verify fresh access readiness.
    """
    if intent['phase'] != 'repaired' or _hold(snapshots['hold'])['phase'] != 'released':
        raise RepairError('controller-repair-runtime-recovery-required')


def access_ready(value):
    # Keep the startup-reproof ready() contract without importing its privileged
    # CLI, which checks installed-program custody at import time. A selected
    # source checkout is not the root-owned installed reconcile program.
    mode = value.get('configured_mode')
    return (value.get('available') is True and value.get('scope') == 'owner-host'
            and mode in ('sandboxed', 'full-access') and value.get('effective_mode') == mode
            and value.get('runtime_verified') is True and value.get('busy') is False
            and value.get('pending') is False and value.get('reason') is None)


def execute(intent, adapter, *, rollback=False):
    """Persist intent before each effect. Adapter verifies custody on every step.

    No automatic rollback: uncertain process/file outcomes retain the admission
    hold and the exact phase until an operator explicitly resumes or rolls back.
    """
    value = copy.deepcopy(intent)
    def phase(name):
        nonlocal value
        updated = dict(value, phase=name)
        adapter.persist(value, updated)
        value = updated
    if rollback:
        if value['phase'] == 'rolled-back':
            adapter.verify(value, {BEFORE}, terminal=True)
            return value
        if not value['phase'].startswith('rollback-'):
            adapter.verify(value, phase_hashes(value['phase']))
            phase('rollback-stopping')
    elif value['phase'].startswith('rollback-') or value['phase'] == 'rolled-back':
        raise RepairError('controller-repair-rollback-selected')
    while True:
        current = value['phase']
        allowed = phase_hashes(current)
        adapter.verify(value, allowed, terminal=current in ('repaired', 'rolled-back'))
        if current in ('repaired', 'rolled-back'):
            return value
        if current == 'prepared':
            phase('stopping')
        elif current in ('stopping', 'rollback-stopping'):
            adapter.stop(value)
            phase('rollback-replacing' if rollback else 'replacing')
        elif current in ('replacing', 'rollback-replacing'):
            adapter.replace(value, rollback=rollback)
            phase('rollback-starting' if rollback else 'starting')
        elif current in ('starting', 'rollback-starting'):
            adapter.start(value)
            if rollback:
                adapter.verify(value, {BEFORE}, terminal=True)
                phase('rolled-back')
            else:
                phase('verifying')
        elif current == 'verifying':
            adapter.verify(value, {AFTER}, terminal=True)
            phase('repaired')
        else:
            _fail()


def phase_hashes(phase):
    if phase not in PHASES:
        _fail()
    return ({BEFORE} if phase in ('prepared', 'stopping', 'rollback-starting', 'rolled-back')
            else {BEFORE, AFTER} if phase in ('replacing', 'rollback-stopping', 'rollback-replacing')
            else {AFTER})
