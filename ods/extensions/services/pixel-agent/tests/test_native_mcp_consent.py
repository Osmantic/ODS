"""Durable Root intent/claim contracts, with real private journal writes."""
import contextlib
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import sys
import threading
from unittest.mock import patch
from urllib.parse import urlencode

import pytest

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='POSIX Root consent custody and locks')

ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(ROOT / 'bin')]
from pixel_connections import native_mcp as native, oauth  # noqa: E402
import pixel_access_protocol as protocol  # noqa: E402


class Bridge:
    def __init__(self, state):
        self.state = state
        self.lock = threading.RLock()
        self.calls = []
        self.claims = []
        self.auth_state = secrets.token_urlsafe(32)
        self.result_lost = False
        self.binding = {key: 'a' * 64 for key in native.BINDING_KEYS if key.endswith('Sha256')}
        self.binding.update(ownerUid=1000, home='/qa/owner', configPath='/qa/owner/state/openclaw.json',
                            stateDir='/qa/owner/state', packageDir='/qa/runtime',
                            binaryPath='/qa/runtime/openclaw.mjs', nodePath='/usr/bin/node', serverName='qa-notes')

    def bounded(self, _seconds):
        return contextlib.nullcontext()

    def locked(self):
        return self.lock

    def worker(self, operation, *, config_hash, native_oauth_request):
        assert operation == 'native-mcp-oauth'
        assert config_hash == self.binding['configSha256']
        request = native.worker_request(native_oauth_request)
        self.calls.append(request['action'])
        journal = native.validate_journal(json.loads((self.state / 'native-mcp-consent.json').read_bytes()))
        self.claims.append(journal['phase'])
        if self.result_lost:
            raise RuntimeError('lost private worker result')
        if request['action'] == 'complete':
            return {'outcome': 'authorized', 'authUrl': None}
        return {'outcome': 'redirect', 'authUrl': 'https://provider.example/authorize?' + urlencode({
            'state': self.auth_state, 'redirect_uri': request['redirectUri'], 'response_type': 'code',
            'code_challenge_method': 'S256', 'code_challenge': 'native-pkce-challenge'})}


@pytest.fixture
def world(tmp_path):
    tmp_path.chmod(0o700)
    bridge = Bridge(tmp_path)
    now = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
    consent = native.Consent(bridge, app='notes', server_name='qa-notes', now=lambda: now)
    with patch.object(consent, '_binding', side_effect=lambda: dict(bridge.binding)):
        yield bridge, consent


def callback(bridge, *, port=43123, **values):
    return f'http://127.0.0.1:{port}/?' + urlencode({'state': bridge.auth_state, 'code': 'PRIVATE-code', **values})


def test_callback_claim_precedes_exchange_and_replay_is_refused(world):
    bridge, consent = world
    begin = consent.begin(redirect_port=43123)
    journal = (bridge.state / 'native-mcp-consent.json').read_text()
    assert bridge.auth_state not in journal and 'code_challenge' not in journal
    assert json.loads(journal)['stateHash'] == oauth.state_hash(bridge.auth_state)
    assert consent.complete(flow_id=begin['flowId'], callback_url=callback(bridge)) == {
        'phase': 'authorized', 'error': None, 'portalAccessVerified': False}
    assert bridge.claims == ['beginning', 'exchanging']
    assert 'PRIVATE-code' not in (bridge.state / 'native-mcp-consent.json').read_text()
    with pytest.raises(native.NativeError, match='native-callback-already-claimed'):
        consent.complete(flow_id=begin['flowId'], callback_url=callback(bridge))
    assert bridge.calls == ['begin', 'complete']
    public = consent.status()
    assert set(public) == {'app', 'phase', 'expiresAt', 'error', 'portalAccessVerified'}


@pytest.mark.parametrize('url', [
    'http://localhost:43123/?state=x&code=x', 'http://127.0.0.1:43124/?state=x&code=x',
    'http://127.0.0.1:43123/other?state=x&code=x', 'http://user@127.0.0.1:43123/?state=x&code=x',
    'http://127.0.0.1:43123/?state=x&code=x#fragment',
])
def test_full_redirect_is_bound_before_any_exchange(world, url):
    bridge, consent = world
    begin = consent.begin(redirect_port=43123)
    with pytest.raises(native.NativeError, match='native-callback-redirect-mismatch'):
        consent.complete(flow_id=begin['flowId'], callback_url=url)
    assert bridge.calls == ['begin']


def test_wrong_state_or_duplicate_query_keeps_rightful_flow_usable(world):
    bridge, consent = world
    begin = consent.begin(redirect_port=43123)
    for url in (callback(bridge, state=secrets.token_urlsafe(32)), callback(bridge) + '&code=another'):
        with pytest.raises(native.NativeError):
            consent.complete(flow_id=begin['flowId'], callback_url=url)
    assert consent.status()['phase'] == 'consent'
    consent.complete(flow_id=begin['flowId'], callback_url=callback(bridge))
    assert bridge.calls == ['begin', 'complete']


@pytest.mark.parametrize('field', sorted(native.BINDING_KEYS))
def test_every_installed_identity_component_is_bound_before_exchange(world, field):
    bridge, consent = world
    begin = consent.begin(redirect_port=43123)
    bridge.binding[field] = (1001 if field == 'ownerUid' else 'b' * 64 if field.endswith('Sha256') else '/another/path')
    with pytest.raises(native.NativeError, match='native-mcp-binding-changed'):
        consent.complete(flow_id=begin['flowId'], callback_url=callback(bridge))
    assert bridge.calls == ['begin']


def test_lost_exchange_result_recovers_without_replaying_or_forgetting(world):
    bridge, consent = world
    begin = consent.begin(redirect_port=43123)
    bridge.result_lost = True
    with pytest.raises(RuntimeError):
        consent.complete(flow_id=begin['flowId'], callback_url=callback(bridge))
    assert consent.status()['phase'] == 'exchanging'
    recovered = consent.recover(flow_id=begin['flowId'])
    assert recovered['phase'] == 'failed' and recovered['error'] == 'native-oauth-attempt-unconfirmed'
    with pytest.raises(native.NativeError, match='native-callback-already-claimed'):
        consent.complete(flow_id=begin['flowId'], callback_url=callback(bridge))
    assert bridge.calls == ['begin', 'complete']


def test_crash_before_begin_reply_and_expired_consent_can_be_explicitly_recovered(world):
    bridge, consent = world
    bridge.result_lost = True
    with pytest.raises(RuntimeError):
        consent.begin(redirect_port=43123)
    journal = json.loads((bridge.state / 'native-mcp-consent.json').read_bytes())
    assert journal['phase'] == 'beginning'
    assert consent.recover(flow_id=journal['flowId'])['phase'] == 'failed'
    bridge.result_lost = False
    begin = consent.begin(redirect_port=43123)
    consent.now = lambda: datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc) + timedelta(seconds=oauth.FLOW_SECONDS)
    with pytest.raises(native.NativeError, match='native-flow-expired'):
        consent.complete(flow_id=begin['flowId'], callback_url=callback(bridge))
    assert consent.recover(flow_id=begin['flowId'])['error'] == 'native-flow-expired'
    assert bridge.calls == ['begin', 'begin']


def test_concurrent_callbacks_exchange_once(world):
    bridge, consent = world
    begin = consent.begin(redirect_port=43123)
    outcomes = []

    def run():
        try:
            outcomes.append(consent.complete(flow_id=begin['flowId'], callback_url=callback(bridge))['phase'])
        except native.NativeError as error:
            outcomes.append(error.code)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
        assert not thread.is_alive()
    assert sorted(outcomes) == ['authorized', 'native-callback-already-claimed']
    assert bridge.calls == ['begin', 'complete']


def test_cancel_does_not_call_forget_and_denial_does_not_exchange(world):
    bridge, consent = world
    begin = consent.begin(redirect_port=43123)
    denied = 'http://127.0.0.1:43123/?' + urlencode({'state': bridge.auth_state, 'error': 'access_denied'})
    assert consent.complete(flow_id=begin['flowId'], callback_url=denied)['error'] == 'access-denied'
    begin = consent.begin(redirect_port=43123)
    assert consent.cancel(flow_id=begin['flowId'])['phase'] == 'cancelled'
    assert bridge.calls == ['begin', 'begin']


def test_fsync_failure_does_not_start_exchange(world):
    bridge, consent = world
    begin = consent.begin(redirect_port=43123)
    with patch.object(native.os, 'fsync', side_effect=OSError('fixture failed fsync')):
        with pytest.raises(OSError):
            consent.complete(flow_id=begin['flowId'], callback_url=callback(bridge))
    assert bridge.calls == ['begin']


@pytest.mark.skipif(os.name != 'posix', reason='POSIX private journal custody')
def test_unsafe_journal_is_refused_without_replacing_target(world):
    bridge, consent = world
    outside = bridge.state / 'outside'
    outside.write_text('preserve')
    path = bridge.state / 'native-mcp-consent.json'
    path.symlink_to(outside)
    with pytest.raises((native.NativeError, OSError)):
        consent.begin(redirect_port=43123)
    assert outside.read_text() == 'preserve' and bridge.calls == []


def test_owner_pipe_is_fixed_and_not_a_public_socket_operation(world):
    bridge, _consent = world
    inner = {'action': 'begin', 'binding': bridge.binding, 'serverName': 'qa-notes',
             'redirectUri': 'http://127.0.0.1:43123/'}
    frame = {'operation': 'native-mcp-oauth', 'openclaw': '/qa/runtime/openclaw.mjs',
             'config_sha256': 'a' * 64, 'confirmed': False, 'native_oauth_request': inner}
    protocol.request(frame)
    for bad in (dict(inner, action='forget'), dict(inner, command='unreviewed'), dict(inner, packageDir='/other')):
        with pytest.raises(protocol.ProtocolError):
            protocol.request(dict(frame, native_oauth_request=bad))
    with pytest.raises(protocol.ProtocolError):
        protocol.control_request({'operation': 'native-mcp-oauth', 'request': inner})
    with pytest.raises(protocol.ProtocolError):
        protocol.result('native-mcp-oauth', {'outcome': 'authorized', 'authUrl': None, 'token': 'PRIVATE'})


def test_writable_ancestor_is_not_a_bound_runtime_or_state_directory(tmp_path):
    directory = tmp_path / 'replaceable'
    directory.mkdir(mode=0o755)
    native._parents(directory / 'node', os.geteuid())
    directory.chmod(0o777)
    with pytest.raises(native.NativeError, match='unsafe-native-mcp-state'):
        native._parents(directory / 'node', os.geteuid())
