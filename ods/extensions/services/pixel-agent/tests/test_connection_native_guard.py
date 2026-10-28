"""Old connection mutators respect native enrollment inside the same Root lock."""
import sys
if sys.platform == 'win32':
    from unittest import SkipTest
    raise SkipTest('POSIX connection coordination runs under Linux/WSL')

import contextlib
import copy
from pathlib import Path

import pytest

import test_connection_coordinator as harness
from pixel_access_bridge import AccessError, atomic_json
from pixel_connections import coordinator as c, native_enrollment as enrollment, state

world = harness.world
MUTATORS = ('begin', 'complete', 'activate', 'disconnect', 'plugin')


@pytest.fixture
def owner(world, monkeypatch):
    original_lock, original_guard = world.bridge.locked, enrollment.guard_idle
    observed = {'locked': False, 'guards': 0}

    @contextlib.contextmanager
    def single_lock():
        assert not observed['locked'], 'The mutator must not acquire another Root lock'
        with original_lock():
            observed['locked'] = True
            try:
                yield
            finally:
                observed['locked'] = False

    def guarded(bridge):
        assert observed['locked'], 'Native intent must be checked inside the existing Root lock'
        observed['guards'] += 1
        return original_guard(bridge)

    monkeypatch.setattr(world.bridge, 'locked', single_lock)
    monkeypatch.setattr(enrollment, 'guard_idle', guarded)
    world.guard_observed = observed
    return world


def journal(owner, phase):
    value = {'kind': 'native-app-enrollment', 'schemaVersion': 1, 'app': 'notion', 'flowId': 'b' * 32,
             'phase': phase, 'intent': 'withdraw' if phase in ('forgetting', 'disconnected') else 'connect',
             'createdAt': '2026-10-28T12:00:00Z', 'updatedAt': '2026-10-28T12:00:00Z',
             'activationId': None, 'step': None, 'stable': None, 'proof': None, 'error': None}
    if phase == 'ready':
        value.update(activationId='c' * 64, stable={'recordSha256': 'd' * 64, 'configSha256': 'e' * 64},
                     proof={'reachable': True, 'permitted': True, 'reason': 'ready', 'scope': 'pixel-owner-default',
                            'configSha256': 'e' * 64, 'runtimeRevision': 'f' * 64,
                            'checkedAt': '2026-10-28T12:00:00Z'})
    with owner.bridge.locked():
        owner.bridge.discover()
        atomic_json(Path(owner.bridge.state) / enrollment.JOURNAL, enrollment.validate_journal(value))


def operation(owner, name):
    """Seed real disposable Google custody before introducing native intent."""
    if name == 'complete':
        flow = harness.begin(owner)
        request = {'flowId': flow['flowId'], 'state': harness.consent_state(owner, flow['flowId']),
                   'code': '4/fixture-code'}
        return lambda: c.control(owner.bridge, 'connection-complete', request)
    harness.connect(owner)
    if name != 'activate':
        c.control(owner.bridge, 'connection-activate', {})
    if name == 'begin':
        harness.begin(owner)  # A pending flow must survive the refused replacement too.
        return lambda: harness.begin(owner)
    if name == 'activate':
        return lambda: c.control(owner.bridge, 'connection-activate', {})
    if name == 'disconnect':
        return lambda: c.control(owner.bridge, 'connection-disconnect', {'connector': 'google'})
    return lambda: c.control(owner.bridge, 'connection-plugin', {'plugin': 'memory-wiki', 'enabled': False})


def files(owner):
    root = owner.bridge.home.parent
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*') if path.is_file()}


@pytest.mark.parametrize('name', MUTATORS)
@pytest.mark.parametrize('phase', ['prepared', 'beginning', 'consent', 'authorized', 'forgetting'])
def test_pending_native_enrollment_refuses_old_mutators_before_any_side_effect(owner, name, phase):
    invoke = operation(owner, name)
    journal(owner, phase)
    before, commands, calls = files(owner), owner.commands[:], copy.deepcopy(owner.google.calls)
    guards = owner.guard_observed['guards']
    with pytest.raises(AccessError, match='^native-app-transition-in-progress$'):
        invoke()
    assert owner.guard_observed['guards'] == guards + 1
    assert files(owner) == before
    assert owner.commands == commands and owner.google.calls == calls
    assert not owner.guard_observed['locked']


@pytest.mark.parametrize('name', MUTATORS)
@pytest.mark.parametrize('phase', ['ready', 'cancelled', 'failed', 'disconnected'])
def test_terminal_native_enrollment_allows_old_mutators_without_a_nested_lock(owner, name, phase):
    invoke = operation(owner, name)
    journal(owner, phase)
    guards = owner.guard_observed['guards']
    assert isinstance(invoke(), dict)
    assert owner.guard_observed['guards'] == guards + 1
    assert not owner.guard_observed['locked']


@pytest.mark.parametrize('name', MUTATORS)
def test_unreadable_native_intent_is_not_silently_ignored_by_a_mutator(owner, name):
    invoke = operation(owner, name)
    journal(owner, 'prepared')
    path = Path(owner.bridge.state) / enrollment.JOURNAL
    path.write_text('{broken')
    before, commands = files(owner), owner.commands[:]
    with pytest.raises(AccessError, match='^invalid-native-mcp-state$'):
        invoke()
    assert files(owner) == before and owner.commands == commands


def test_read_and_approval_surfaces_keep_their_ordinary_behavior_during_enrollment(owner):
    harness.connect(owner)
    c.control(owner.bridge, 'connection-activate', {})
    journal(owner, 'prepared')
    guards = owner.guard_observed['guards']
    for operation in ('connection-proposals', 'connection-drafts', 'connection-activity'):
        assert isinstance(c.control(owner.bridge, operation, {}), dict)
    request = {'proposalId': 'calendar-1793190000000-abcdef01', 'sha256': 'a' * 64}
    with pytest.raises(AccessError, match='^calendar-changes-off$'):
        c.control(owner.bridge, 'connection-approve', request)
    assert owner.guard_observed['guards'] == guards

def test_native_controller_private_gateway_path_does_not_reenter_the_public_mutator_guard(owner):
    harness.connect(owner)
    c.control(owner.bridge, 'connection-activate', {})
    journal(owner, 'prepared')
    guards = owner.guard_observed['guards']
    record = harness.record(owner)
    with owner.bridge.locked():
        owner.bridge.discover()
        assert c._change_gateway(owner.bridge, c.gateway_target(record, custody_held=True),
            state.connection_answers(record, google_custody=True)) == 'unchanged'
    assert owner.guard_observed['guards'] == guards
