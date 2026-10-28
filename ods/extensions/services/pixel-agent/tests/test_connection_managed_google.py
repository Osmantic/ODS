"""Managed Google app selection through real Root/broker logic, fake provider.

No Google registration, personal client, real account or installed service is
used. Host/API/Settings transport remains a separate public integration block.
"""
import sys
if sys.platform == 'win32':
    from unittest import SkipTest
    raise SkipTest('POSIX Root custody fixtures run under Linux/WSL')

import contextlib
import json
import os
from urllib.parse import parse_qs, urlsplit

import pytest

import test_connection_coordinator as harness
from pixel_access_bridge import AccessError
from pixel_connections import coordinator as c, google_app, lifecycle, oauth, state, status

world = harness.world
SYNTHETIC_SECRET = 'synthetic-managed-application-only'


@pytest.fixture
def managed(world, monkeypatch):
    path = world.record.parent / 'portal-google-app.json'
    client = {'installed': {'client_id': harness.CLIENT['clientId'], 'client_secret': SYNTHETIC_SECRET,
        'project_id': 'ods-fixture-managed', 'auth_uri': oauth.GOOGLE_AUTH_URI, 'token_uri': oauth.GOOGLE_TOKEN_URI}}
    path.write_text(json.dumps({'kind': google_app.KIND, 'schemaVersion': 1, 'client': client}))
    path.chmod(0o600)
    monkeypatch.setattr(google_app, 'PATH', path)
    monkeypatch.setattr(google_app, 'OWNERS', (os.getuid(),))
    world.managed_app_path = path
    return world


def begin_request(**changes):
    return {'connector': 'google', 'authMode': 'managed', 'capabilities': harness.READ_BOTH,
            'cloudModels': False, 'ownerTimeZone': 'America/Chicago', 'loginHint': None,
            'redirectPort': 53682, **changes}


def start(world, **changes):
    return c.control(world.bridge, 'connection-begin', begin_request(**changes))


def test_managed_sign_in_uses_protected_client_pkce_and_account_selection_then_activates(managed):
    begin = start(managed)
    query = parse_qs(urlsplit(begin['authUrl']).query, strict_parsing=True)
    assert query['client_id'] == [harness.CLIENT['clientId']]
    assert query['prompt'] == ['select_account consent']
    assert query['code_challenge_method'] == ['S256']
    handoff_path = managed.paths.oauth / (begin['flowId'] + '.flow.json')
    handoff = json.loads(handoff_path.read_text())
    assert handoff['client']['installed']['client_secret'] == SYNTHETIC_SECRET
    assert handoff_path.stat().st_mode & 0o077 == 0
    assert SYNTHETIC_SECRET not in json.dumps(begin)
    assert SYNTHETIC_SECRET not in json.dumps(harness.journal(managed))
    assert harness.journal(managed)['request']['authMode'] == 'managed'
    completed = c.control(managed.bridge, 'connection-complete', {
        'flowId': begin['flowId'], 'state': handoff['state'], 'code': '4/synthetic-managed-code'})
    assert completed['outcome'] == 'connected'
    assert c.control(managed.bridge, 'connection-activate', {}) == {'outcome': 'applied'}
    assert managed.google.calls[0][1]['client_secret'] == [SYNTHETIC_SECRET]
    assert harness.record(managed)['connectors']['google']['account'] == 'owner@example.com'
    assert harness.record(managed)['connectors']['google']['authMode'] == 'managed'


def test_bound_managed_mode_survives_callback_even_when_deployment_availability_changes(managed):
    begin = start(managed)
    saved = harness.journal(managed)
    managed.managed_app_path.unlink()
    assert google_app.public_status()['state'] == 'not-configured'
    # A fresh callback uses the durable consent provenance, not the current
    # global deployment availability or an in-memory browser request.
    assert c.control(managed.bridge, 'connection-complete', {'flowId': begin['flowId'],
        'state': harness.consent_state(managed, begin['flowId']), 'code': '4/synthetic-bound-code'})['outcome'] == 'connected'
    assert saved['request']['authMode'] == 'managed'
    assert harness.record(managed)['connectors']['google']['authMode'] == 'managed'
    assert c.control(managed.bridge, 'connection-activate', {}) == {'outcome': 'applied'}
    assert harness.record(managed)['connectors']['google']['authMode'] == 'managed'


def test_legacy_pending_consent_defaults_to_advanced_even_when_managed_app_is_available(managed):
    begin = harness.begin(managed)
    journal = harness.journal(managed)
    journal['request'].pop('authMode')
    c._write_lifecycle(managed.bridge, journal)
    assert google_app.public_status()['state'] == 'configured'
    assert c.control(managed.bridge, 'connection-complete', {'flowId': begin['flowId'],
        'state': harness.consent_state(managed, begin['flowId']), 'code': '4/synthetic-legacy-code'})['outcome'] == 'connected'
    assert harness.record(managed)['connectors']['google']['authMode'] == 'advanced'


@pytest.mark.parametrize('bad', [None, True, 'official', 'owner', {}])
def test_invalid_auth_mode_in_root_record_or_consent_is_refused(managed, bad):
    start(managed)
    journal = harness.journal(managed)
    journal['request']['authMode'] = bad
    with pytest.raises(lifecycle.LifecycleError):
        lifecycle.validate(journal)
    record = {'schemaVersion': 1, 'kind': state.KIND, 'updatedAt': '2026-10-28T12:00:00Z',
        'ownerTimeZone': None, 'connectors': {'google': {'status': 'connected', 'account': 'owner@example.com',
        'calendarId': 'primary', 'gmailWindowDays': 180, 'capabilities': harness.READ_BOTH, 'cloudModels': False,
        'connectedAt': '2026-10-28T12:00:00Z', 'authMode': bad}}}
    with pytest.raises(state.StateError):
        state.validate(record)


def test_legacy_record_projection_defaults_advanced_without_rewriting_record(managed):
    # Connect using the existing compatible path, then model an old saved record.
    harness.connect(managed)
    record = harness.record(managed)
    record['connectors']['google'].pop('authMode')
    before = json.dumps(record, sort_keys=True)
    assert state.validate(record) is record
    projected = status.google_view(record, custody=True, broker=None, broker_error=False, now=c._now())
    assert projected['authMode'] == 'advanced'
    assert json.dumps(record, sort_keys=True) == before


@pytest.mark.parametrize('change,code', [('missing', 'google-app-not-configured'),
    ('unsafe', 'google-app-config-unsafe'), ('invalid', 'google-app-config-invalid')])
def test_unconfigured_or_unsafe_managed_app_does_not_mutate_services_or_open_consent(managed, change, code):
    if change == 'missing':
        managed.managed_app_path.unlink()
    elif change == 'unsafe':
        managed.managed_app_path.chmod(0o644)
    else:
        managed.managed_app_path.write_text(json.dumps({'private-invalid-client': SYNTHETIC_SECRET}))
    with pytest.raises(AccessError, match='^' + code + '$'):
        start(managed)
    assert managed.commands == []
    assert not managed.paths.state.exists()
    assert harness.journal(managed) is None


def test_missing_managed_app_preserves_an_already_open_advanced_consent(managed):
    prior = harness.begin(managed)
    path = managed.paths.oauth / (prior['flowId'] + '.flow.json')
    before = path.read_bytes()
    journal = harness.journal(managed)
    commands = managed.commands[:]
    managed.managed_app_path.unlink()
    with pytest.raises(AccessError, match='google-app-not-configured'):
        start(managed)
    assert path.read_bytes() == before and harness.journal(managed) == journal
    assert managed.commands == commands


def test_deployment_client_is_resolved_inside_the_existing_root_lock(managed, monkeypatch):
    original_lock, original_load = managed.bridge.locked, google_app.load
    held = []

    @contextlib.contextmanager
    def locked():
        assert not held, 'A managed sign-in must not acquire a nested Root lock'
        with original_lock():
            held.append(True)
            try:
                yield
            finally:
                held.pop()

    def load():
        assert held == [True]
        return original_load()

    monkeypatch.setattr(managed.bridge, 'locked', locked)
    monkeypatch.setattr(google_app, 'load', load)
    assert start(managed)['authUrl'].startswith(oauth.GOOGLE_AUTH_URI)
    assert not held


@pytest.mark.parametrize('extra', [{'client': harness.CLIENT}, {'client': None},
    {'clientPath': '/home/owner/personal-client.json'}, {'appPath': '/tmp/client.json'}])
def test_managed_request_cannot_supply_or_select_a_browser_client(managed, extra):
    with pytest.raises(AccessError, match='invalid-connection-request'):
        start(managed, **extra)
    assert not managed.paths.state.exists() and managed.commands == []


def test_explicit_advanced_client_and_exact_legacy_client_remain_compatible(managed):
    for request in (begin_request(authMode='advanced', client=harness.CLIENT),
                    {key: value for key, value in begin_request(client=harness.CLIENT).items() if key != 'authMode'}):
        reply = c.control(managed.bridge, 'connection-begin', request)
        handoff = json.loads((managed.paths.oauth / (reply['flowId'] + '.flow.json')).read_text())
        assert handoff['client']['installed']['client_secret'] == harness.CLIENT['clientSecret']
        assert SYNTHETIC_SECRET not in json.dumps(handoff)
        assert parse_qs(urlsplit(reply['authUrl']).query)['prompt'] == ['consent']
