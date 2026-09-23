"""Durable native proof under the original repaired deployment's edge hold.

No transport or filesystem effects live here. The privileged adapter keeps the
existing controller lock across every phase; this never adopts another lease.
"""
import base64
import copy
import json
import re

PHASES = ('prepared', 'acquiring-native', 'probing', 'verified', 'releasing-native',
          'native-released', 'releasing-edge', 'complete')
LIMIT = 256 * 1024


class ProofError(ValueError):
    pass


def fail():
    raise ProofError('controller-held-proof-authority-mismatch')


def make_intent(repair, *, current, candidate, owner, snapshots, repair_body,
                settings_hash, identities, mode):
    document = repair._object(repair_body, repair.LIMIT)
    repair.validate_intent(document, current=current, candidate=candidate, owner=owner,
                           snapshots=snapshots)
    if document['phase'] != 'repaired' or mode not in ('sandboxed', 'full-access'):
        fail()
    if type(settings_hash) is not str or not re.fullmatch('[a-f0-9]{64}', settings_hash):
        fail()
    # Reuse the strict six-role process-birth and owner contract, not the old
    # pre-repair births: a new proof binds the processes actually present now.
    repair.make_intent(current=current, candidate=candidate, owner=owner,
                       snapshots=snapshots, identities=identities)
    return dict(schemaVersion=1, kind='ods-pixel-controller-held-proof', phase='prepared',
        currentDigest=current, candidateDigest=candidate, owner=copy.deepcopy(owner),
        bindings={name: repair.sha(body) for name, body in snapshots.items()},
        repairSha256=repair.sha(repair_body), settingsSha256=settings_hash, mode=mode,
        heldRecord=base64.b64encode(snapshots['hold']).decode('ascii'),
        identities=copy.deepcopy(identities), leaseRevision=None, proof=None)


def validate(repair, value, **authority):
    try:
        if type(value) is not dict or value.get('phase') not in PHASES:
            fail()
        held = base64.b64decode(value['heldRecord'], validate=True)
        snapshots = authority['snapshots']
        initial = dict(authority, snapshots=dict(snapshots, hold=held), identities=value['identities'])
        expected = make_intent(repair, **initial)
        expected.update(phase=value['phase'], leaseRevision=value['leaseRevision'], proof=value['proof'])
        if json.dumps(expected, sort_keys=True) != json.dumps(value, sort_keys=True):
            fail()
        index = PHASES.index(value['phase'])
        if index < PHASES.index('probing'):
            if value['leaseRevision'] is not None: fail()
        elif type(value['leaseRevision']) is not str or not re.fullmatch('[a-f0-9]{64}', value['leaseRevision']):
            fail()
        if index < PHASES.index('verified'):
            if value['proof'] is not None: fail()
        else:
            check_proof(value['proof'], value)
        # This also enforces the exact original hold bytes and the ordinary
        # release writer's sole allowed phase changes, without archive edits.
        repair.validate_intent(repair._object(authority['repair_body'], repair.LIMIT),
            current=authority['current'], candidate=authority['candidate'], owner=authority['owner'],
            snapshots=snapshots, allow_released=index >= PHASES.index('releasing-edge'))
        if len(json.dumps(value).encode()) > LIMIT: fail()
    except (KeyError, TypeError, ValueError):
        fail()


def check_proof(proof, value):
    if (type(proof) is not dict or set(proof) != {'mode', 'pid', 'config_sha256', 'executed', 'at'}
            or proof['mode'] != value['mode'] or type(proof['pid']) is not int
            or proof['pid'] != value['identities']['gateway'][0] or proof['executed'] is not True
            or type(proof['config_sha256']) is not str or not re.fullmatch('[a-f0-9]{64}', proof['config_sha256'])
            or type(proof['at']) is not str or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z', proof['at'])):
        fail()


def check_native(snapshot, value, *, held, ready=False, exact=False):
    native = snapshot['native']
    if (native.get('runtime_version') != '2026.6.33'
            or native.get('available') is not True or native.get('phase') != ('held' if held else 'idle')
            or type(native.get('active')) is not int or native['active'] != 0
            or type(native.get('pid')) is not int or native['pid'] != value['identities']['gateway'][0]
            or type(native.get('revision')) is not str or not re.fullmatch('[a-f0-9]{64}', native['revision'])
            or snapshot.get('owned') is not held
            or held and value['leaseRevision'] is not None and native['revision'] != value['leaseRevision']):
        fail()
    if ready:
        if snapshot.get('ready') is not True: fail()
        check_proof(native.get('proof'), value)
    if exact and native.get('proof') != value['proof']:
        fail()
    return native


def execute(value, adapter):
    value = copy.deepcopy(value)
    def checkpoint(phase, **fields):
        nonlocal value
        updated = dict(value, phase=phase, **fields)
        adapter.persist(value, updated)
        value = updated
    while True:
        adapter.verify(value)
        phase = value['phase']
        if phase == 'prepared':
            check_native(adapter.snapshot(value), value, held=False)
            checkpoint('acquiring-native')
        elif phase == 'acquiring-native':
            observed = adapter.snapshot(value)
            if observed['native'].get('phase') == 'idle':
                check_native(observed, value, held=False)
                observed = adapter.native(value, 'acquire')
            native = check_native(observed, value, held=True)
            # Acquire resets old proof. A later proof in this earlier phase
            # cannot have come from this write-ahead transaction.
            if native.get('proof') is not None: fail()
            checkpoint('probing', leaseRevision=native['revision'])
        elif phase == 'probing':
            observed = adapter.snapshot(value)
            check_native(observed, value, held=True)
            if observed.get('ready') is not True:
                # Re-executes only the fixed bounded core-tool proof, never a
                # user request. An outstanding runtime probe refuses overlap.
                observed = adapter.native(value, 'probe')
            native = check_native(observed, value, held=True, ready=True)
            checkpoint('verified', proof=copy.deepcopy(native['proof']))
        elif phase == 'verified':
            check_native(adapter.snapshot(value), value, held=True, ready=True, exact=True)
            checkpoint('releasing-native')
        elif phase == 'releasing-native':
            observed = adapter.snapshot(value)
            if observed['native'].get('phase') == 'held':
                check_native(observed, value, held=True, ready=True, exact=True)
                observed = adapter.native(value, 'release')
            # A lost release reply is accepted only after directly observing
            # idle on the same process, with the same still-verified proof.
            check_native(observed, value, held=False, ready=True, exact=True)
            checkpoint('native-released')
        elif phase == 'native-released':
            check_native(adapter.snapshot(value), value, held=False, ready=True, exact=True)
            checkpoint('releasing-edge')
        elif phase == 'releasing-edge':
            check_native(adapter.snapshot(value), value, held=False, ready=True, exact=True)
            adapter.release_edge(value)
            checkpoint('complete')
        elif phase == 'complete':
            check_native(adapter.snapshot(value), value, held=False, ready=True, exact=True)
            adapter.verify_released(value)
            return value
        else:
            fail()
