"""Native app choices use the existing Root/owner update, restart and rollback.

This exercises already-authorized Root records with simulated services. It is
not the browser enrollment/consent path, nor proof of a live Notion account.
"""
import sys
if sys.platform == 'win32':
    from unittest import SkipTest
    raise SkipTest('POSIX owner coordination runs under Linux/WSL')

import json
from pathlib import Path

import pytest

import test_connection_coordinator as harness
import pixel_access_protocol as protocol
from pixel_access_bridge import AccessError
from pixel_connections import coordinator as c, native_apps as apps
from pixel_connections.native_mcp import NativeError

world = harness.world


@pytest.fixture
def owner(world, monkeypatch):
    monkeypatch.setattr(harness.tx, '_catalog_ready', lambda openclaw, chosen: not chosen)
    # Services and the OpenClaw launcher are simulated in this Root transaction
    # fixture. The actual version-bound preflight runs in test_native_mcp_oauth.
    world.native_preflights = []
    monkeypatch.setattr(harness.tx.native_mcp_owner, 'check_staged_native_apps',
        lambda path, *, openclaw, chosen: world.native_preflights.append((Path(path), dict(chosen))))
    return world


def save_choice(owner, enabled):
    record = harness.record(owner) if owner.record.exists() else {
        'schemaVersion': 1, 'kind': 'ods-portal-connections', 'updatedAt': '2026-10-28T12:00:00Z',
        'ownerTimeZone': None, 'connectors': {}}
    c._write_record(dict(record, nativeApps={'notion': enabled}))


def live(owner):
    return json.loads(owner.bridge.config_path.read_text())


def activate(owner):
    return c.control(owner.bridge, 'connection-activate', {})


def test_saved_native_choice_is_applied_idempotently_to_gateway_and_onboarding(owner):
    for enabled in (False, True, False):
        save_choice(owner, enabled)
        assert activate(owner) == {'outcome': 'applied'}
        assert apps.configured_choices(live(owner)) == {'notion': enabled}
        answers = json.loads(owner.bridge.answers_path.read_text())
        assert answers['nativeMcpApps'] == apps.answers({'notion': enabled})
        restarts = owner.bridge.log.count('restart')
        assert activate(owner) == {'outcome': 'unchanged'}
        assert owner.bridge.log.count('restart') == restarts
    assert owner.bridge.log.count('restart') == 3


def test_native_choices_cross_the_owner_status_protocol_and_legacy_frames_normalize(owner):
    save_choice(owner, False)
    activate(owner)
    reply = owner.bridge.worker('connection-owner-status')
    assert protocol.connection_result('connection-owner-status', reply) == reply
    assert reply['live']['nativeApps'] == {'notion': False}
    legacy = dict(reply, answers={key: value for key, value in reply['answers'].items() if key != 'nativeMcpApps'})
    normalized = protocol.connection_result('connection-owner-status', legacy)
    assert normalized['answers']['nativeMcpApps'] is None
    assert 'nativeMcpApps' not in legacy['answers'], 'normalizing a received frame must not mutate it'
    with pytest.raises(protocol.ProtocolError):
        protocol.connection_result('connection-owner-status', dict(reply, answers={'unknown': True}))


def test_rejected_native_activation_restores_config_environment_and_onboarding(owner, monkeypatch):
    save_choice(owner, False)
    activate(owner)
    paths = (owner.bridge.config_path, owner.bridge.env_path, owner.bridge.answers_path)
    before = {path: path.read_bytes() for path in paths}
    restarts = iter(['rejected', 'verified'])
    monkeypatch.setattr(c, '_restart', lambda bridge, journal: next(restarts))
    save_choice(owner, True)
    with pytest.raises(AccessError, match='gateway-change-rolled-back'):
        activate(owner)
    assert {path: path.read_bytes() for path in paths} == before
    assert apps.configured_choices(live(owner)) == {'notion': False}
    assert next(restarts, None) is None


def test_busy_portal_cannot_change_native_grants(owner):
    save_choice(owner, False)
    activate(owner)
    before = owner.bridge.config_path.read_bytes()
    save_choice(owner, True)
    owner.bridge.active = 1
    with pytest.raises(AccessError, match='runtime-busy'):
        activate(owner)
    assert owner.bridge.config_path.read_bytes() == before


@pytest.mark.parametrize('code', ['native-app-policy-blocked', 'native-app-namespace-conflict',
                                  'native-mcp-binding-changed', 'native-oauth-attempt-unconfirmed'])
def test_preflight_refusal_leaves_owner_files_and_completion_untouched(owner, monkeypatch, code):
    save_choice(owner, False)
    activate(owner)
    paths = (owner.bridge.config_path, owner.bridge.env_path, owner.bridge.answers_path)
    before = {path: path.read_bytes() for path in paths}
    sd = owner.bridge.owner_state
    saved = {path: path.read_bytes() for path in sd.iterdir() if path.is_file()}
    restarts = owner.bridge.log.count('restart')
    checked = []

    def refuse(path, *, openclaw, chosen):
        staged = Path(path)
        assert staged != owner.bridge.config_path
        assert apps.configured_choices(json.loads(staged.read_text())) == {'notion': True}
        assert chosen == {'notion': True}
        assert {item: item.read_bytes() for item in paths} == before
        checked.append(staged)
        raise NativeError(code)

    monkeypatch.setattr(harness.tx.native_mcp_owner, 'check_staged_native_apps', refuse)
    save_choice(owner, True)
    with pytest.raises(harness.tx.ConnectionTransactionError, match=code):
        activate(owner)
    assert len(checked) == 1 and not checked[0].exists()
    assert {path: path.read_bytes() for path in paths} == before
    assert {path: path.read_bytes() for path in sd.iterdir() if path.is_file()} == saved
    assert owner.bridge.log.count('restart') == restarts
    # Root may have held admissions before the owner refused. Settle that exact
    # attempt without applying the rejected candidate or creating a new receipt.
    with owner.bridge.locked():
        assert c._resume(owner.bridge, owner.bridge.pending()) == 'rolled-back'
    assert owner.bridge.pending() is None
    assert {path: path.read_bytes() for path in paths} == before
    assert {path: path.read_bytes() for path in sd.iterdir() if path.is_file()} == saved
    assert owner.bridge.log.count('restart') == restarts


@pytest.mark.parametrize('enabled', [False, True])
def test_disconnecting_google_keeps_native_choice_even_without_timezone_or_plugins(owner, enabled):
    harness.connect(owner)
    activate(owner)
    saved = harness.record(owner)
    c._write_record(dict(saved, ownerTimeZone=None, nativeApps={'notion': enabled}))
    activate(owner)
    assert c.control(owner.bridge, 'connection-disconnect', {'connector': 'google'})['outcome'] == 'disconnected'
    assert harness.record(owner)['nativeApps'] == {'notion': enabled}
    assert harness.record(owner)['connectors'] == {}
    activate(owner)
    assert apps.configured_choices(live(owner)) == {'notion': enabled}
    assert 'pixel-source-broker' not in live(owner)['plugins']['allow']
