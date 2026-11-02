"""Durable Root intent/claim contracts, with real private journal writes."""
import contextlib
from datetime import datetime, timedelta, timezone
import hashlib
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


@pytest.fixture
def access_world(world, monkeypatch):
    bridge, _consent = world
    path = bridge.state / 'owner-config.json'
    server = {'enabled': False, 'auth': 'oauth', 'transport': 'streamable-http', 'url': 'https://example.test/mcp'}
    raw = native.encoded({'mcp': {'servers': {'qa-notes': server}}})
    path.write_bytes(raw)
    path.chmod(0o600)
    bridge.binding.update(ownerUid=os.geteuid(), configPath=str(path), configSha256=hashlib.sha256(raw).hexdigest())
    snapshot = {'runtime_verified': True, 'pending': False, 'configured_mode': 'full-access',
                'effective_mode': 'full-access', 'revision': 'c' * 64,
                '_config': {'config_sha256': bridge.binding['configSha256']}}
    monkeypatch.setattr(native, 'bridge_binding', lambda selected, name: dict(selected.binding))
    monkeypatch.setattr(bridge, 'inspect', lambda: snapshot, raising=False)
    results = {'probe': {'reachable': True, 'permitted': True, 'reason': 'ready', 'scope': 'pixel-owner-default'},
               'forget': {'forgotten': True}}

    def worker(operation, *, config_hash, native_oauth_request):
        assert operation == 'native-mcp-oauth'
        request = native.worker_request(native_oauth_request)
        assert config_hash == bridge.binding['configSha256']
        bridge.calls.append(request['action'])
        return results[request['action']]

    monkeypatch.setattr(bridge, 'worker', worker)
    return bridge, snapshot, results, server


def test_access_check_joins_bound_native_policy_to_fresh_runtime_proof(access_world):
    bridge, snapshot, _results, _server = access_world
    result = native.check_access(bridge, 'qa-notes')
    assert result == {'reachable': True, 'permitted': True, 'reason': 'ready', 'scope': 'pixel-owner-default',
                      'configSha256': bridge.binding['configSha256'], 'runtimeRevision': snapshot['revision']}
    assert bridge.calls == ['probe']


@pytest.mark.parametrize('change', [
    {'runtime_verified': False}, {'pending': True}, {'effective_mode': 'unknown'}, {'revision': 'not-a-proof'},
    {'_config': {'config_sha256': 'b' * 64}},
])
def test_unverified_or_old_runtime_cannot_probe_or_forget(access_world, change):
    bridge, snapshot, _results, _server = access_world
    snapshot.update(change)
    for operation in (native.check_access, native.forget_disabled):
        with pytest.raises(native.NativeError, match='native-app-runtime-unverified'):
            operation(bridge, 'qa-notes')
    assert bridge.calls == []


def test_forget_requires_disabled_exact_config_and_reports_no_provider_revocation(access_world):
    bridge, snapshot, _results, server = access_world
    assert native.forget_disabled(bridge, 'qa-notes') == {'forgotten': True, 'providerRevoked': False}
    path = Path(bridge.binding['configPath'])
    server['enabled'] = True
    raw = native.encoded({'mcp': {'servers': {'qa-notes': server}}})
    path.write_bytes(raw)
    bridge.binding['configSha256'] = snapshot['_config']['config_sha256'] = hashlib.sha256(raw).hexdigest()
    with pytest.raises(native.NativeError, match='native-app-withdrawal-required'):
        native.forget_disabled(bridge, 'qa-notes')
    assert bridge.calls == ['forget']


def test_runtime_or_binding_changed_during_check_is_not_ready(access_world, monkeypatch):
    bridge, snapshot, _results, _server = access_world
    original = bridge.worker

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        snapshot['revision'] = 'd' * 64
        return result

    monkeypatch.setattr(bridge, 'worker', changed)
    with pytest.raises(native.NativeError, match='native-mcp-binding-changed'):
        native.check_access(bridge, 'qa-notes')


@pytest.mark.parametrize('value', [
    {'reachable': True, 'permitted': True, 'reason': 'policy-blocked', 'scope': 'pixel-owner-default'},
    {'reachable': 1, 'permitted': True, 'reason': 'ready', 'scope': 'pixel-owner-default'},
    {'reachable': True, 'permitted': True, 'reason': 'ready', 'scope': 'another-agent'},
    {'reachable': True, 'permitted': True, 'reason': 'ready', 'scope': 'pixel-owner-default', 'token': 'PRIVATE'},
])
def test_native_probe_protocol_cannot_return_ambiguous_or_sensitive_results(value):
    with pytest.raises(protocol.ProtocolError, match='owner-protocol-failed'):
        protocol.result('native-mcp-oauth', value)


@pytest.fixture
def authorized_world(world):
    if os.geteuid() == 0:
        pytest.skip('Activation fixture binds an ordinary owner UID')
    bridge, consent = world
    path = bridge.state / 'prepared-config.json'
    raw = native.encoded({'mcp': {'servers': {'qa-notes': {
        'enabled': False, 'auth': 'oauth', 'transport': 'streamable-http', 'url': 'https://provider.example/mcp'}}}})
    path.write_bytes(raw)
    path.chmod(0o600)
    bridge.binding.update(ownerUid=os.geteuid(), configPath=str(path), configSha256=hashlib.sha256(raw).hexdigest())
    begin = consent.begin(redirect_port=43123)
    consent.complete(flow_id=begin['flowId'], callback_url=callback(bridge))
    return bridge, consent, begin['flowId']


def test_authorization_is_durably_reserved_once_without_certifying_portal_access(authorized_world):
    bridge, consent, flow = authorized_world
    # The production lock is not reentrant. Internal enrollment calls the
    # locked helper while owning it; public entry points take it exactly once.
    bridge.lock = threading.Lock()
    with bridge.locked():
        claim = consent._claim_authorization_locked(flow_id=flow, activation_id='d' * 64)
    assert claim == {'flowId': flow, 'activationId': 'd' * 64, 'phase': 'activating',
                     'bindingSha256': native.digest(bridge.binding)}
    assert consent.status()['portalAccessVerified'] is False
    assert consent.recover(flow_id=flow)['phase'] == 'activating'
    with pytest.raises(native.NativeError, match='native-authorization-already-claimed'):
        consent.claim_authorization(flow_id=flow, activation_id='e' * 64)
    with pytest.raises(native.NativeError, match='native-activation-settlement-required'):
        consent.cancel(flow_id=flow)
    with pytest.raises(native.NativeError, match='native-sign-in-in-progress'):
        consent.begin(redirect_port=43123)
    with pytest.raises(native.NativeError, match='native-callback-already-claimed'):
        consent.complete(flow_id=flow, callback_url=callback(bridge))
    assert bridge.calls == ['begin', 'complete']
    with pytest.raises(native.NativeError, match='native-activation-claim-mismatch'):
        consent.finish_authorization(flow_id=flow, activation_id='e' * 64)
    expected = {'phase': 'settled', 'portalAccessVerified': False}
    assert consent.finish_authorization(flow_id=flow, activation_id='d' * 64) == expected
    assert consent.finish_authorization(flow_id=flow, activation_id='d' * 64) == expected
    new = consent.begin(redirect_port=43123)
    assert new['flowId'] != flow
    assert 'activationId' not in json.loads(consent.path.read_bytes())


def test_concurrent_authorization_claims_cannot_reserve_two_activations(authorized_world):
    _bridge, consent, flow = authorized_world
    outcomes = []

    def claim(identity):
        try: outcomes.append(consent.claim_authorization(flow_id=flow, activation_id=identity)['phase'])
        except native.NativeError as error: outcomes.append(error.code)

    threads = [threading.Thread(target=claim, args=(char * 64,)) for char in ('d', 'e')]
    for thread in threads: thread.start()
    for thread in threads:
        thread.join(5)
        assert not thread.is_alive()
    assert sorted(outcomes) == ['activating', 'native-authorization-already-claimed']


def test_claim_fsync_failure_does_not_consume_or_grant_authorization(authorized_world):
    bridge, consent, flow = authorized_world
    before = consent.path.read_bytes()
    with patch.object(native.os, 'fsync', side_effect=OSError('failed private claim fsync')):
        with pytest.raises(OSError):
            consent.claim_authorization(flow_id=flow, activation_id='d' * 64)
    assert consent.path.read_bytes() == before
    assert consent.status()['phase'] == 'authorized'
    assert bridge.calls == ['begin', 'complete']


@pytest.mark.parametrize('claimed', [False, True])
def test_expiry_recovers_an_unclaimed_authorization_but_preserves_a_pending_root_reservation(authorized_world, claimed):
    _bridge, consent, flow = authorized_world
    if claimed:
        consent.claim_authorization(flow_id=flow, activation_id='d' * 64)
    now = consent.now
    consent.now = lambda: now() + timedelta(seconds=oauth.FLOW_SECONDS)
    recovered = consent.recover(flow_id=flow)
    assert recovered['phase'] == ('activating' if claimed else 'failed')
    assert recovered['error'] == (None if claimed else 'native-flow-expired')
    assert recovered['portalAccessVerified'] is False


def test_activation_rejects_binding_drift_expiry_and_an_already_enabled_server(authorized_world):
    bridge, consent, flow = authorized_world
    original = dict(bridge.binding)
    bridge.binding['nodeSha256'] = 'b' * 64
    with pytest.raises(native.NativeError, match='native-mcp-binding-changed'):
        consent.claim_authorization(flow_id=flow, activation_id='d' * 64)
    bridge.binding = original
    now = consent.now
    consent.now = lambda: now() + timedelta(seconds=oauth.FLOW_SECONDS)
    with pytest.raises(native.NativeError, match='native-flow-expired'):
        consent.claim_authorization(flow_id=flow, activation_id='d' * 64)
    consent.now = now
    path = Path(bridge.binding['configPath'])
    value = json.loads(path.read_bytes())
    value['mcp']['servers']['qa-notes']['enabled'] = True
    path.write_bytes(native.encoded(value))
    with pytest.raises(native.NativeError, match='native-mcp-binding-changed'):
        consent.claim_authorization(flow_id=flow, activation_id='d' * 64)
    # Even a fresh authenticated binding to an enabled entry is not preparation.
    bridge.binding['configSha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    consent._write(dict(consent._read(), bindingSha256=native.digest(bridge.binding)))
    with pytest.raises(native.NativeError, match='native-app-preparation-required'):
        consent.claim_authorization(flow_id=flow, activation_id='d' * 64)
    assert consent.status()['phase'] == 'authorized'
