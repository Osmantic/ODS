"""Native mutations cannot reopen an interrupted Google admission boundary.

The real Root/owner journals run in disposable fixtures. Provider operations,
services and UID custody are simulated; no installed account is contacted.
"""
import sys
if sys.platform == 'win32':
    from unittest import SkipTest
    raise SkipTest('POSIX Root/owner connection fixtures require Linux/WSL')

from unittest.mock import Mock

import pytest

import test_native_enrollment as fixture
from pixel_connections import coordinator as c, custody, native_mcp as native

world = fixture.world
owner = fixture.owner


def ready(owner):
    flow = fixture.authorize(owner)
    assert owner.controller.activate(flow_id=flow)['phase'] == 'ready'
    return flow


def snapshot(owner):
    root = owner.bridge.home.parent
    return {str(path.relative_to(root)): (path.stat().st_mode, path.read_bytes())
            for path in root.rglob('*') if path.is_file()}


def failed_google_before_root_cleanup(owner, monkeypatch):
    # Google may preserve the already admitted Native app without another
    # Native consent. Simulate only the pinned SDK/provider readiness boundary.
    monkeypatch.setattr(fixture.harness.tx, '_catalog_ready', lambda *args, **kwargs: True)
    monkeypatch.setattr(fixture.harness.tx.native_mcp_owner, 'check_staged_native_apps',
                        lambda *args, **kwargs: None)
    exchange = custody.exchange

    def lost_exchange(*args):
        exchange(*args)
        raise fixture.harness.Crash()

    with monkeypatch.context() as fault:
        fault.setattr(custody, 'exchange', lost_exchange)
        with pytest.raises(fixture.harness.Crash):
            fixture.harness.connect(owner)
    write = c._write_lifecycle

    def lost_cleanup(bridge, value):
        write(bridge, value)
        if value.get('admissionComplete'):
            raise fixture.harness.Crash()

    with monkeypatch.context() as fault:
        fault.setattr(c, '_write_lifecycle', lost_cleanup)
        with pytest.raises(fixture.harness.Crash):
            c.control(owner.bridge, 'connection-activate', {})
    assert owner.bridge.pending()['google']['stage'] == 'released'
    assert c.read_lifecycle(c._lifecycle_path(owner.bridge))['admissionComplete'] is True


def test_native_disconnect_cannot_publish_intent_before_retained_google_terminal_hold_is_cleaned(owner, monkeypatch):
    ready(owner)
    failed_google_before_root_cleanup(owner, monkeypatch)
    before, calls, commands = snapshot(owner), owner.native_calls[:], owner.commands[:]
    put = Mock(wraps=owner.controller._put)
    monkeypatch.setattr(owner.controller, '_put', put)
    for _ in range(2):
        with pytest.raises(native.NativeError, match='^connection-transition-in-progress$'):
            owner.controller.disconnect()
        assert snapshot(owner) == before
        assert owner.native_calls == calls and owner.commands == commands
        assert owner.controller._read()['phase'] == 'ready'
        put.assert_not_called()
    google_calls = len(owner.google.calls)
    assert c.control(owner.bridge, 'connection-activate', {})['outcome'] == 'applied'
    assert c.control(owner.bridge, 'connection-activate', {})['outcome'] == 'unchanged'
    assert owner.bridge.pending() is None and len(owner.google.calls) == google_calls
    assert owner.controller.disconnect()['phase'] == 'disconnected'
    assert fixture.choice(owner) == {'notion': False}
    assert owner.native_calls == calls + ['forget']


@pytest.mark.parametrize('operation', ['begin', 'disconnect'])
def test_legacy_unsettled_google_failure_blocks_native_before_any_write_but_completed_marker_allows_it(
        owner, monkeypatch, operation):
    ready(owner)
    c._write_lifecycle(owner.bridge, c._lifecycle('failed', error=c.EXCHANGE_INTERRUPTED))
    before, calls = snapshot(owner), owner.native_calls[:]
    put = Mock(wraps=owner.controller._put)
    monkeypatch.setattr(owner.controller, '_put', put)
    invoke = (lambda: owner.controller.begin(redirect_port=43123)) if operation == 'begin' else owner.controller.disconnect
    for _ in range(2):
        with pytest.raises(native.NativeError, match='^' + c.EXCHANGE_INTERRUPTED + '$'):
            invoke()
        assert snapshot(owner) == before and owner.native_calls == calls
        put.assert_not_called()
    # Only the protected post-release marker distinguishes a completed failure
    # from an old failed exchange whose credential custody is still uncertain.
    c._write_lifecycle(owner.bridge, dict(c._lifecycle('failed', error=c.EXCHANGE_INTERRUPTED), admissionComplete=True))
    assert owner.controller.disconnect()['phase'] == 'disconnected'
    if operation == 'begin':
        assert owner.controller.begin(redirect_port=43123)['phase'] == 'consent'
