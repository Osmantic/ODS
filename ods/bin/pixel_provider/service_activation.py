"""Service restart callback for the root provider transaction, not a public API.

The enclosing coordinator supplies a protected-runtime custody check and owns
both admission holds, root journal, saved policy and owner transaction. This
callback is deliberately zero-argument when bound into the owner-worker pipe:
each invocation selects its environment using the actual configuration hash.
"""
import re
import time
from pathlib import Path

from pixel_access_bridge import AccessError, atomic_json, digest, remaining
from pixel_access_protocol import HEX
from pixel_settings import coordinator as settings
from pixel_settings.contract import SettingsError
from pixel_settings.runtime import _timestamp

from .service_environment import _snapshot


def definition(bridge, owned_dropin):
    service = bridge.gateway_service
    if hasattr(service, 'definition'):
        return service.definition(owned_dropin)
    if hasattr(service, 'unit'):
        return _systemd_definition(bridge, owned_dropin)
    raise AccessError('provider-service-adapter-missing')


def _systemd_definition(bridge, owned_dropin):
    # Provider environment changes must not replace a launcher, OS identity or
    # unrelated unit policy. The exact two managed files are checked separately.
    fields = bridge.command(['systemctl', 'show', bridge.gateway_service.unit,
        '--property=User,Group,DynamicUser,WorkingDirectory,RootDirectory,RootImage'])
    sources = bridge.command(['systemctl', 'show', bridge.gateway_service.unit, '--property=FragmentPath,DropInPaths'])
    values = dict(line.split('=', 1) for line in sources.splitlines() if '=' in line)
    fragment, dropins = values.get('FragmentPath', ''), values.get('DropInPaths', '')
    paths = [fragment, *dropins.split()]
    if (not fragment or len(paths) != len(set(paths))
            or any(not re.fullmatch(r'/[A-Za-z0-9_./-]+', path) for path in paths)):
        raise AccessError('provider-unit-source-unqualified')
    # Read the exact root-protected files, not presentation headers from `cat`.
    # A comment inside a unit cannot hide another section from the fingerprint.
    images = []
    for path in paths:
        if path == str(owned_dropin):
            continue
        image = _snapshot(Path(path))
        if image is None:
            raise AccessError('provider-unit-source-unavailable')
        images.append([path, image])
    return digest([fields, images])


def _custody(journal, qualify_runtime):
    expected = journal.get('runtimeCustody')
    if (type(expected) is not str or not HEX.fullmatch(expected)
            or not callable(qualify_runtime) or qualify_runtime() != expected):
        raise AccessError('provider-runtime-custody-unqualified')


def _unchanged(bridge, journal, environment, qualify_runtime):
    _custody(journal, qualify_runtime)
    if (bridge.unit_boundary() != journal['boundary']
            or definition(bridge, environment.dropin) != journal['serviceDefinition']):
        raise AccessError('provider-service-definition-changed')


def _registration(envelope, identity, revision, binding):
    keys = {'schemaVersion', 'source', 'pid', 'runtimeVersion', 'revision',
            'observedAt', 'registration', 'transportVerified'}
    if (type(envelope) is not dict or set(envelope) != keys
            or type(envelope['schemaVersion']) is not int or envelope['schemaVersion'] != 1
            or envelope['source'] != 'current-provider-registration'
            or type(envelope['pid']) is not int or envelope['pid'] != identity['pid']
            or envelope['revision'] != revision or envelope['transportVerified'] is not False
            or envelope['runtimeVersion'] != '2026.6.33'
            or type(envelope['observedAt']) is not str):
        return False
    try:
        _timestamp(envelope['observedAt'])
        from pixel_access_protocol import provider_binding
        provider_binding(envelope.get('registration', {}).get('binding'))
    except (ValueError, SettingsError, AttributeError):
        return False
    return envelope['registration'] == {'status': 'active' if binding else 'inactive', 'binding': binding}


def verify(bridge, journal, environment, selection, *, qualify_runtime):
    """Prove exact current registration and unchanged access, not model success."""
    _unchanged(bridge, journal, environment, qualify_runtime)
    environment.verify(journal, selection)
    # Existing effective tool execution + settings readback retain the access
    # boundary. Provider registration alone is not proof that tools stayed safe.
    proof = settings._verify(bridge, journal)
    identity = settings._identity(bridge)
    native = bridge.native()
    if native.get('pid') != identity['pid']:
        raise AccessError('provider-process-changed')
    envelope = bridge.http(bridge.native_origin, '/pixel-ods/access-runtime', bridge.native_key,
        {'operation': 'provider-readback', 'token': journal['token'], 'revision': native['revision']})
    if not _registration(envelope, identity, native['revision'], selection['binding']):
        raise AccessError('provider-registration-mismatch')
    if (settings._identity(bridge) != identity or settings._busy(bridge, journal)
            or any(proof[key] != identity[key] for key in ('pid', 'started', 'boot'))):
        raise AccessError('provider-process-changed')
    environment.verify(journal, selection)
    _unchanged(bridge, journal, environment, qualify_runtime)
    return dict(proof, binding=selection['binding'], environmentHash=selection['environmentHash'],
                registrationVerified=True, transportVerified=False)


def stop_before_owner_change(bridge, journal, environment, *, qualify_runtime):
    """Drain and stop before config replacement can trigger a partial hot reload.

    Persist restart authority before stopping. No owner bytes have changed yet;
    an interrupted stop or refused owner write remains explicitly recoverable.
    """
    _unchanged(bridge, journal, environment, qualify_runtime)
    if settings._busy(bridge, journal):
        raise AccessError('runtime-busy')
    before = settings._identity(bridge)
    journal.update(phase='invoking', restartIdentity=before)
    atomic_json(bridge.state / 'transition.json', journal)
    bridge.gateway_service.stop(timeout=60)
    if bridge.stopped_native(journal['token']).get('stopped') is not True:
        raise AccessError('provider-stop-unconfirmed')
    _unchanged(bridge, journal, environment, qualify_runtime)


def activate(bridge, journal, environment, *, qualify_runtime):
    """Durably arm restart, replace environment, restart fixed unit and verify.

    Failure leaves root/owner journals and holds intact. A known registration
    mismatch is the sole rejected outcome, letting the existing owner operation
    restore its exact before-image and invoke this SAME callback again.
    """
    environment.load(journal)
    _unchanged(bridge, journal, environment, qualify_runtime)
    if settings._busy(bridge, journal):
        return 'unavailable'
    try:
        before = settings._identity(bridge)
        running = True
    except AccessError:
        if (journal.get('phase') not in ('invoking', 'restarting') or not settings._valid_identity(journal.get('restartIdentity'))
                or bridge.stopped_native(journal['token']).get('stopped') is not True):
            return 'unavailable'
        before = journal['restartIdentity']
        running = False
    # A late pipe reply/recovery callback must not replace an already verified
    # current process. Rollback selects a different side, so it cannot reuse it.
    record, side = environment.select(journal)
    prior = journal.get('providerServiceVerified')
    if prior == {'identity': before, 'side': side, 'environmentHash': digest(record[side])}:
        selection = {'side': side, 'binding': record[side + 'Binding'], 'environmentHash': digest(record[side])}
        verify(bridge, journal, environment, selection, qualify_runtime=qualify_runtime)
        return 'verified'
    journal.pop('providerServiceVerified', None)
    journal['phase'] = 'restarting'
    journal['restartIdentity'] = before
    atomic_json(bridge.state / 'transition.json', journal)
    # A rollback callback may arrive after the replacement gateway started.
    # launchd must unload that held instance before loading a different plist.
    if running and getattr(bridge.gateway_service, 'is_launchd', False):
        bridge.gateway_service.stop(timeout=60)
        if bridge.stopped_native(journal['token']).get('stopped') is not True:
            raise AccessError('provider-stop-unconfirmed')
    selection = environment.apply(journal)
    _unchanged(bridge, journal, environment, qualify_runtime)
    bridge.gateway_service.reload()
    _unchanged(bridge, journal, environment, qualify_runtime)
    environment.verify(journal, selection)
    bridge.gateway_service.restart(timeout=60)
    deadline = time.monotonic() + remaining(120)
    while time.monotonic() < deadline:
        try:
            now = settings._identity(bridge)
            if (now['boot'] == before['boot'] and now['pid'] != before['pid']
                    and now['started'] > before['started']):
                bridge.native('acquire', journal['token'], timeout=3)
                if bridge.http(bridge.native_origin, '/health', bridge.native_key, timeout=3).get('ok') is True:
                    verify(bridge, journal, environment, selection, qualify_runtime=qualify_runtime)
                    journal['providerServiceVerified'] = {'identity': now, 'side': selection['side'],
                        'environmentHash': selection['environmentHash']}
                    atomic_json(bridge.state / 'transition.json', journal)
                    return 'verified'
        except AccessError as error:
            if error.code == 'provider-registration-mismatch':
                return 'rejected'
            if error.code not in ('settings-process-not-active', 'settings-process-unavailable',
                                  'runtime-unavailable-or-busy', 'runtime-operation-timeout',
                                  'native-idle-unconfirmed'):
                raise
            # Observe the SAME unit/restart. A timeout does not authorize another.
        except SettingsError:
            return 'unavailable'
        time.sleep(remaining(1))
    return 'unavailable'
