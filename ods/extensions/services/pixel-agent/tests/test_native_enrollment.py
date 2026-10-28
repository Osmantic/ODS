"""Managed native lifecycle with the real Root/owner transactions and receipts.

Only services and the native OAuth/provider operation are simulated. No real
account, installed ODS state or public host/API enrollment route is exercised.
The independently pinned native SDK lane qualifies OAuth/policy operations.
"""
import sys
if sys.platform == 'win32':
    from unittest import SkipTest
    raise SkipTest('POSIX Root/owner connection transactions run under Linux/WSL')

import contextlib
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import threading
from urllib.parse import urlencode

import pytest

import test_connection_coordinator as harness
from pixel_access_bridge import AccessError
from pixel_connections import coordinator as c, native_apps as apps, native_enrollment as enrollment, native_mcp as native

world = harness.world


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def owner(world, monkeypatch):
    bridge = world.bridge
    original = bridge.worker
    now = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
    controller = enrollment.Controller(bridge, now=lambda: now)
    world.controller = controller
    world.native_calls = []
    world.authorized = False
    world.readiness = 'ready'
    world.auth_state = secrets.token_urlsafe(32)
    world.preflights = []
    world.lock = threading.Lock()

    @contextlib.contextmanager
    def locked():
        bridge.state.mkdir(mode=0o700, exist_ok=True)
        assert world.lock.acquire(blocking=False), 'Root access lock must never be nested'
        try:
            yield
        finally:
            world.lock.release()

    def binding(_bridge, server_name):
        assert _bridge is bridge and server_name == 'ods-notion'
        assert bridge.pending() is None
        config = json.loads(bridge.config_path.read_bytes())
        value = {key: 'a' * 64 for key in native.BINDING_KEYS if key.endswith('Sha256')}
        value.update(ownerUid=os.getuid(), home=str(bridge.home), configPath=str(bridge.config_path),
            stateDir=str(bridge.config_path.parent), packageDir='/qa/reviewed-native',
            nodePath='/qa/reviewed-node', binaryPath='/qa/reviewed-native/openclaw.mjs', serverName=server_name,
            configSha256=sha(bridge.config_path.read_bytes()), serverSha256=native.digest(config['mcp']['servers'][server_name]))
        return native.validate_binding(value)

    def preflight(path, *, openclaw, chosen):
        assert world.lock.locked()
        config = json.loads(Path(path).read_bytes())
        assert apps.configured_choices(config) == chosen
        world.preflights.append(chosen.copy())
        if chosen.get('notion'):
            held = controller.consent._read()
            saved = controller._read()
            assert held['phase'] == 'activating' and held['activationId'] == saved['activationId']
            assert saved['step']['beforeConfigSha256'] == sha(bridge.config_path.read_bytes())

    def worker(operation='status', **kwargs):
        if operation != 'native-mcp-oauth':
            try:
                reply = original(operation, **kwargs)
            except harness.tx.ConnectionTransactionError as error:
                raise AccessError(str(error)) from None
            if operation == 'status':
                return dict(reply, config_sha256=sha(bridge.config_path.read_bytes()))
            return reply
        assert world.lock.locked()
        request = native.worker_request(kwargs['native_oauth_request'])
        assert kwargs['config_hash'] == request['binding']['configSha256']
        assert request['binding'] == binding(bridge, request['serverName'])
        world.native_calls.append(request['action'])
        enabled = apps.configured_choices(json.loads(bridge.config_path.read_bytes()))['notion']
        if request['action'] == 'begin':
            assert enabled is False
            if world.authorized:
                return {'outcome': 'authorized', 'authUrl': None}
            return {'outcome': 'redirect', 'authUrl': 'https://provider.example/authorize?' + urlencode({
                'state': world.auth_state, 'redirect_uri': request['redirectUri'], 'response_type': 'code',
                'code_challenge_method': 'S256', 'code_challenge': 'native-private-pkce'})}
        if request['action'] == 'complete':
            assert enabled is False
            assert controller.consent._read()['phase'] == 'exchanging'
            world.authorized = True
            return {'outcome': 'authorized', 'authUrl': None}
        if request['action'] == 'probe':
            assert world.authorized and enabled
            reason = world.readiness
            return {'reachable': reason not in ('unreachable', 'sign-in-required'),
                'permitted': reason == 'ready', 'reason': reason, 'scope': 'pixel-owner-default'}
        assert request['action'] == 'forget' and enabled is False
        # Native forgetting is allowed only after live Root proof for this
        # disabled configuration, including an idempotent retry after lost reply.
        native._verified_runtime(bridge, request['binding'])
        world.authorized = False
        return {'forgotten': True}

    monkeypatch.setattr(bridge, 'locked', locked)
    monkeypatch.setattr(bridge, 'worker', worker)
    monkeypatch.setattr(native, 'bridge_binding', binding)
    monkeypatch.setattr(harness.tx.native_mcp_owner, 'check_staged_native_apps', preflight)
    monkeypatch.setattr(harness.tx, '_catalog_ready', lambda openclaw, chosen: not chosen)
    return world


def callback(owner):
    return 'http://127.0.0.1:43123/?' + urlencode({'state': owner.auth_state, 'code': 'PRIVATE-native-code'})


def authorize(owner):
    begin = owner.controller.begin(redirect_port=43123)
    assert begin['phase'] == 'consent' and begin['portalAccessVerified'] is False
    assert owner.controller.complete(flow_id=begin['flowId'], callback_url=callback(owner))['phase'] == 'authorized'
    return begin['flowId']


def choice(owner):
    return apps.configured_choices(json.loads(owner.bridge.config_path.read_bytes()))


def test_account_connects_only_after_native_authorization_gated_activation_and_scoped_readiness(owner):
    flow = authorize(owner)
    assert choice(owner) == {'notion': False}
    result = owner.controller.activate(flow_id=flow)
    assert result['phase'] == 'ready' and result['portalAccessVerified'] is True
    assert result['scope'] == 'pixel-owner-default'
    assert choice(owner) == {'notion': True}
    assert owner.native_calls == ['begin', 'complete', 'probe']
    assert owner.controller.consent.status()['phase'] == 'settled'
    assert owner.bridge.pending() is None
    assert owner.bridge.native_phase == owner.bridge.edge_phase == 'idle'
    assert owner.preflights == [{'notion': False}, {'notion': True}]
    assert owner.bridge.log.count('restart') == 2
    raw = owner.controller.path.read_text()
    for secret in ('PRIVATE-native-code', owner.auth_state, 'native-private-pkce', 'https://provider.example/authorize'):
        assert secret not in raw
    assert owner.controller.path.stat().st_mode & 0o777 == 0o600
    before = owner.native_calls[:]
    assert owner.controller.status()['portalAccessVerified'] is True
    assert owner.controller.recover(flow_id=flow)['portalAccessVerified'] is True
    assert owner.native_calls == before, 'Status/recovery of a fresh ready result must not contact native/provider again'


def test_lost_callback_reply_joins_authorized_consent_without_exchanging_the_code_twice(owner, monkeypatch):
    begin = owner.controller.begin(redirect_port=43123)
    put = owner.controller._put

    def lose(journal, **changes):
        if changes.get('phase') == 'authorized':
            monkeypatch.setattr(owner.controller, '_put', put)
            raise harness.Crash()
        return put(journal, **changes)

    monkeypatch.setattr(owner.controller, '_put', lose)
    with pytest.raises(harness.Crash):
        owner.controller.complete(flow_id=begin['flowId'], callback_url=callback(owner))
    assert owner.controller.status()['phase'] == 'consent'
    assert owner.controller.recover(flow_id=begin['flowId'])['phase'] == 'authorized'
    assert owner.controller.activate(flow_id=begin['flowId'])['portalAccessVerified'] is True
    assert owner.native_calls.count('complete') == 1


@pytest.mark.parametrize('boundary', ['held', 'released'])
def test_interrupted_activation_settles_the_exact_owner_write_without_reapplying(owner, monkeypatch, boundary):
    flow = authorize(owner)
    if boundary == 'held':
        bind = c._bind_marker

        def lose(*args):
            monkeypatch.setattr(c, '_bind_marker', bind)
            raise harness.Crash()
        monkeypatch.setattr(c, '_bind_marker', lose)
    else:
        settled = owner.controller._settled

        def lose(*args):
            monkeypatch.setattr(owner.controller, '_settled', settled)
            raise harness.Crash()
        monkeypatch.setattr(owner.controller, '_settled', lose)
    with pytest.raises(harness.Crash):
        owner.controller.activate(flow_id=flow)
    assert owner.bridge.log.count('connection-apply') == 2  # preparation + one activation
    assert owner.controller.recover(flow_id=flow)['portalAccessVerified'] is True
    assert owner.bridge.log.count('connection-apply') == 2
    assert owner.bridge.log.count('restart') == 2
    assert owner.controller.consent.status()['phase'] == 'settled'


def test_disconnect_with_claimed_but_not_started_activation_never_freshly_grants_the_old_intent(owner, monkeypatch):
    flow = authorize(owner)
    run = owner.controller._run_step

    def lose(journal):
        assert journal['step']['kind'] == 'activate'
        monkeypatch.setattr(owner.controller, '_run_step', run)
        raise harness.Crash()

    monkeypatch.setattr(owner.controller, '_run_step', lose)
    with pytest.raises(harness.Crash):
        owner.controller.activate(flow_id=flow)
    assert owner.controller.consent.status()['phase'] == 'activating'
    assert choice(owner) == {'notion': False}
    result = owner.controller.disconnect()
    assert result['phase'] == 'disconnected' and result['providerRevoked'] is False
    assert owner.authorized is False
    assert owner.preflights == [{'notion': False}]
    assert owner.bridge.log.count('restart') == 1
    assert owner.controller.consent.status()['phase'] == 'settled'


def test_ready_disconnect_withdraws_before_forgetting_and_preserves_independent_record_data(owner):
    flow = authorize(owner)
    owner.controller.activate(flow_id=flow)
    record = c._record()
    c._write_record(dict(record, plugins={'memory-wiki': False, 'future-plugin': False}))
    result = owner.controller.disconnect()
    assert result['phase'] == 'disconnected' and result['providerRevoked'] is False
    assert choice(owner) == {'notion': False}
    assert owner.native_calls == ['begin', 'complete', 'probe', 'forget']
    assert c._record()['plugins'] == {'memory-wiki': False, 'future-plugin': False}
    assert owner.bridge.log.count('restart') == 3
    assert owner.controller.status()['portalAccessVerified'] is False


@pytest.mark.parametrize('mode', ['sandboxed', 'full-access'])
def test_native_connect_and_disconnect_preserve_existing_google_account_and_custody(owner, mode):
    harness.connect(owner)
    c.control(owner.bridge, 'connection-activate', {})
    google = json.loads(owner.record.read_bytes())['connectors']['google']
    token = owner.paths.token.read_bytes()
    owner.bridge.mode = mode
    flow = authorize(owner)
    assert owner.controller.activate(flow_id=flow)['portalAccessVerified'] is True
    assert c._record()['connectors']['google'] == google
    assert owner.controller.disconnect()['phase'] == 'disconnected'
    assert c._record()['connectors']['google'] == google
    assert owner.paths.token.read_bytes() == token
    assert all('pixel-source-revoke.service' not in command for command in owner.commands)


def test_cancel_keeps_valid_native_credentials_and_never_exposes_tools(owner):
    owner.authorized = True
    begin = owner.controller.begin(redirect_port=43123)
    assert begin['phase'] == 'authorized'
    assert owner.controller.cancel(flow_id=begin['flowId'])['phase'] == 'cancelled'
    assert owner.authorized is True and choice(owner) == {'notion': False}
    assert owner.native_calls == ['begin']


def test_expired_unclaimed_authorization_settles_without_grants_and_can_sign_in_again(owner):
    flow = authorize(owner)
    advanced = lambda: datetime(2026, 10, 28, 12, tzinfo=timezone.utc) + timedelta(seconds=901)  # noqa: E731
    owner.controller.now = owner.controller.consent.now = advanced
    result = owner.controller.activate(flow_id=flow)
    assert result['phase'] == 'failed' and result['error'] == 'native-flow-expired'
    assert owner.controller.consent.status()['phase'] == 'failed'
    assert choice(owner) == {'notion': False} and owner.authorized is True
    assert owner.bridge.log.count('connection-apply') == 1
    begin = owner.controller.begin(redirect_port=43123)
    assert begin['phase'] == 'authorized' and begin['flowId'] != flow
    assert owner.controller.activate(flow_id=begin['flowId'])['portalAccessVerified'] is True
    assert owner.native_calls.count('complete') == 1 and 'forget' not in owner.native_calls


def test_expiry_between_plan_and_claim_also_settles_the_known_unclaimed_plan(owner, monkeypatch):
    flow = authorize(owner)
    claim = owner.controller.consent._claim_authorization_locked

    def expire(**kwargs):
        owner.controller.consent.now = lambda: datetime(2026, 10, 28, 12, tzinfo=timezone.utc) + timedelta(seconds=901)
        return claim(**kwargs)

    monkeypatch.setattr(owner.controller.consent, '_claim_authorization_locked', expire)
    assert owner.controller.activate(flow_id=flow)['phase'] == 'failed'
    assert choice(owner) == {'notion': False} and owner.authorized is True
    assert owner.controller._read()['step']['outcome'] == 'not-applied'


def test_crash_before_consuming_claim_can_cancel_without_forgetting_valid_credentials(owner, monkeypatch):
    flow = authorize(owner)
    monkeypatch.setattr(owner.controller.consent, '_claim_authorization_locked',
                        lambda **kwargs: (_ for _ in ()).throw(harness.Crash()))
    with pytest.raises(harness.Crash):
        owner.controller.activate(flow_id=flow)
    assert owner.controller._read()['phase'] == 'claiming'
    assert owner.controller.consent.status()['phase'] == 'authorized'
    assert owner.controller.cancel(flow_id=flow)['phase'] == 'cancelled'
    assert choice(owner) == {'notion': False} and owner.authorized is True
    assert owner.native_calls == ['begin', 'complete']


def test_lost_real_claim_reply_cannot_cancel_as_unclaimed_but_withdrawal_never_grants_it(owner, monkeypatch):
    flow = authorize(owner)
    claim = owner.controller.consent._claim_authorization_locked

    def lose(**kwargs):
        claim(**kwargs)
        raise harness.Crash()

    monkeypatch.setattr(owner.controller.consent, '_claim_authorization_locked', lose)
    with pytest.raises(harness.Crash):
        owner.controller.activate(flow_id=flow)
    assert owner.controller._read()['phase'] == 'claiming'
    assert owner.controller.consent.status()['phase'] == 'activating'
    with pytest.raises(native.NativeError, match='native-activation-settlement-required'):
        owner.controller.cancel(flow_id=flow)
    assert owner.controller.disconnect()['phase'] == 'disconnected'
    assert owner.preflights == [{'notion': False}] and owner.authorized is False


def test_blocked_readiness_is_not_connected_and_can_recover_without_reapplying_grants(owner):
    flow = authorize(owner)
    owner.readiness = 'policy-blocked'
    result = owner.controller.activate(flow_id=flow)
    assert result['phase'] == 'verifying' and result['portalAccessVerified'] is False
    assert owner.controller.consent.status()['phase'] == 'activating'
    assert choice(owner) == {'notion': True}
    owner.readiness = 'ready'
    assert owner.controller.recover(flow_id=flow)['portalAccessVerified'] is True
    assert owner.bridge.log.count('connection-apply') == 2


def test_status_invalidates_cached_readiness_when_runtime_revision_changes_without_network(owner):
    flow = authorize(owner)
    owner.controller.activate(flow_id=flow)
    calls = owner.native_calls[:]
    owner.bridge.nrev = 'c' * 64
    result = owner.controller.status()
    assert result['portalAccessVerified'] is False and result['error'] == 'native-app-verification-required'
    assert owner.native_calls == calls


def test_expired_cache_refreshes_readiness_without_reapplying_the_settled_configuration(owner):
    flow = authorize(owner)
    owner.controller.activate(flow_id=flow)
    owner.controller.now = lambda: datetime(2026, 10, 28, 12, tzinfo=timezone.utc) + timedelta(seconds=301)
    assert owner.controller.status()['portalAccessVerified'] is False
    assert owner.native_calls.count('probe') == 1
    assert owner.controller.recover(flow_id=flow)['portalAccessVerified'] is True
    assert owner.native_calls.count('probe') == 2 and owner.bridge.log.count('connection-apply') == 2


def test_settled_choice_can_be_reverified_after_unrelated_record_changes_without_overwriting_them(owner):
    flow = authorize(owner)
    owner.controller.activate(flow_id=flow)
    record = dict(c._record(), plugins={'memory-wiki': False})
    c._write_record(record)
    before = owner.record.read_bytes()
    assert owner.controller.status()['portalAccessVerified'] is False
    assert owner.controller.recover(flow_id=flow)['portalAccessVerified'] is True
    assert owner.record.read_bytes() == before
    assert owner.native_calls.count('probe') == 2 and owner.bridge.log.count('connection-apply') == 2


def test_unconfirmed_exchange_fails_closed_then_explicit_sign_in_reuses_valid_native_tokens(owner, monkeypatch):
    begin = owner.controller.begin(redirect_port=43123)
    worker = owner.bridge.worker

    def lose(operation='status', **kwargs):
        reply = worker(operation, **kwargs)
        if operation == 'native-mcp-oauth' and kwargs['native_oauth_request']['action'] == 'complete':
            monkeypatch.setattr(owner.bridge, 'worker', worker)
            raise harness.Crash()
        return reply

    monkeypatch.setattr(owner.bridge, 'worker', lose)
    with pytest.raises(harness.Crash):
        owner.controller.complete(flow_id=begin['flowId'], callback_url=callback(owner))
    assert owner.authorized is True
    recovered = owner.controller.recover(flow_id=begin['flowId'])
    assert recovered['phase'] == 'failed' and recovered['error'] == 'native-oauth-attempt-unconfirmed'
    new = owner.controller.begin(redirect_port=43123)
    assert new['flowId'] != begin['flowId'] and new['phase'] == 'authorized' and new['authUrl'] is None
    assert owner.controller.activate(flow_id=new['flowId'])['portalAccessVerified'] is True
    assert owner.native_calls.count('complete') == 1 and 'forget' not in owner.native_calls


def test_native_preflight_refusal_recovers_no_write_and_restores_the_protected_record(owner, monkeypatch):
    flow = authorize(owner)
    before = {path: path.read_bytes() for path in (owner.bridge.config_path, owner.bridge.env_path, owner.bridge.answers_path)}
    record_before = owner.record.read_bytes()
    check = harness.tx.native_mcp_owner.check_staged_native_apps

    def refuse(path, *, openclaw, chosen):
        check(path, openclaw=openclaw, chosen=chosen)
        if chosen.get('notion'):
            raise native.NativeError('native-app-policy-blocked')

    monkeypatch.setattr(harness.tx.native_mcp_owner, 'check_staged_native_apps', refuse)
    with pytest.raises(AccessError, match='native-app-policy-blocked'):
        owner.controller.activate(flow_id=flow)
    assert {path: path.read_bytes() for path in before} == before
    assert owner.bridge.pending() is not None
    result = owner.controller.recover(flow_id=flow)
    assert result['phase'] == 'failed' and result['portalAccessVerified'] is False
    assert owner.record.read_bytes() == record_before
    assert owner.bridge.pending() is None and owner.authorized is True
    assert owner.controller.consent.status()['phase'] == 'settled'
    assert owner.native_calls == ['begin', 'complete']


def test_lost_ack_of_known_rollback_never_restores_the_rejected_enable_choice(owner, monkeypatch):
    flow = authorize(owner)
    check = harness.tx.native_mcp_owner.check_staged_native_apps

    def refuse(path, *, openclaw, chosen):
        check(path, openclaw=openclaw, chosen=chosen)
        if chosen.get('notion'):
            raise native.NativeError('native-app-policy-blocked')

    monkeypatch.setattr(harness.tx.native_mcp_owner, 'check_staged_native_apps', refuse)
    with pytest.raises(AccessError):
        owner.controller.activate(flow_id=flow)
    put = owner.controller._put

    def lose(journal, **changes):
        if changes.get('phase') == 'failed':
            monkeypatch.setattr(owner.controller, '_put', put)
            raise harness.Crash()
        return put(journal, **changes)

    monkeypatch.setattr(owner.controller, '_put', lose)
    with pytest.raises(harness.Crash):
        owner.controller.recover(flow_id=flow)
    assert owner.controller._read()['step']['outcome'] == 'rolled-back'
    assert c._record()['nativeApps'] == {'notion': False}
    monkeypatch.setattr(owner.controller, '_record_write',
                        lambda *args: (_ for _ in ()).throw(AssertionError('A known rollback must not restore enable intent')))
    assert owner.controller.recover(flow_id=flow)['phase'] == 'failed'
    assert owner.bridge.log.count('connection-apply') == 2  # preparation + refused candidate, no replay


def test_busy_then_disconnect_persists_withdrawal_and_never_retries_the_old_enable(owner):
    flow = authorize(owner)
    owner.bridge.active = 1
    with pytest.raises(AccessError, match='runtime-busy'):
        owner.controller.activate(flow_id=flow)
    with pytest.raises(AccessError, match='runtime-busy'):
        owner.controller.disconnect()
    assert owner.controller._read()['intent'] == 'withdraw'
    assert 'forget' not in owner.native_calls and choice(owner) == {'notion': False}
    owner.bridge.active = 0
    assert owner.controller.recover(flow_id=flow)['phase'] == 'disconnected'
    assert owner.preflights == [{'notion': False}]
    assert owner.authorized is False and owner.bridge.log.count('restart') == 1


def test_lost_forget_reply_can_retry_only_after_verified_withdrawal_without_restarting_again(owner, monkeypatch):
    flow = authorize(owner)
    owner.controller.activate(flow_id=flow)
    worker = owner.bridge.worker

    def lose(operation='status', **kwargs):
        reply = worker(operation, **kwargs)
        if operation == 'native-mcp-oauth' and kwargs['native_oauth_request']['action'] == 'forget':
            monkeypatch.setattr(owner.bridge, 'worker', worker)
            raise harness.Crash()
        return reply

    monkeypatch.setattr(owner.bridge, 'worker', lose)
    with pytest.raises(harness.Crash):
        owner.controller.disconnect()
    assert owner.controller.status()['phase'] == 'forgetting'
    assert owner.authorized is False and choice(owner) == {'notion': False}
    assert owner.controller.recover(flow_id=flow)['phase'] == 'disconnected'
    assert owner.bridge.log.count('restart') == 3 and owner.native_calls.count('forget') == 2


def test_durable_intent_failure_never_starts_an_owner_write_or_native_authorization(owner, monkeypatch):
    before = owner.bridge.config_path.read_bytes()
    with monkeypatch.context() as selected:
        selected.setattr(enrollment.os, 'fsync', lambda fd: (_ for _ in ()).throw(OSError('failed fixture fsync')))
        with pytest.raises(OSError):
            owner.controller.begin(redirect_port=43123)
    assert owner.bridge.config_path.read_bytes() == before
    assert owner.native_calls == [] and owner.bridge.log.count('connection-apply') == 0
    assert not owner.controller.path.exists() and not owner.record.exists()


@pytest.mark.parametrize('side', ['env', 'answers'])
def test_untracked_sidefile_drift_prevents_pending_activation_settlement_without_overwriting(owner, monkeypatch, side):
    flow = authorize(owner)
    settled = owner.controller._settled

    def lose(*args):
        monkeypatch.setattr(owner.controller, '_settled', settled)
        raise harness.Crash()

    monkeypatch.setattr(owner.controller, '_settled', lose)
    with pytest.raises(harness.Crash):
        owner.controller.activate(flow_id=flow)
    path = owner.bridge.env_path if side == 'env' else owner.bridge.answers_path
    path.write_bytes(path.read_bytes() + b'\n')
    before = path.read_bytes()
    with pytest.raises(native.NativeError, match='native-app-state-changed'):
        owner.controller.recover(flow_id=flow)
    assert path.read_bytes() == before
    assert owner.bridge.log.count('connection-apply') == 2 and owner.native_calls.count('probe') == 0


def _disconnected(owner):
    flow = authorize(owner)
    owner.controller.activate(flow_id=flow)
    assert owner.controller.disconnect()['phase'] == 'disconnected'
    return flow


def _accepted_config_update(owner, change):
    """Simulate a completed independent writer, not that writer's admission path."""
    config = json.loads(owner.bridge.config_path.read_bytes())
    if change == 'model':
        config['agents']['defaults']['model'] = {'primary': 'fixture-model'}
    elif change == 'provider':
        config['models'] = {'providers': {'fixture': {'baseUrl': 'https://provider.example/v1'}}}
    elif change == 'source':
        config['plugins']['load']['paths'].append('/qa/accepted-source/plugin')
    elif change == 'regrant':
        apps.load_native_apps(config, apps.answers({'notion': True}))
    else:
        raise AssertionError(change)
    harness.atomic_json(owner.bridge.config_path, config)
    marker = json.loads(owner.bridge.marker_path.read_bytes())
    marker['configuration_sha256'] = harness.model_coordinator._marker_digest(config)
    harness.atomic_json(owner.bridge.marker_path, marker)
    owner.bridge.gateway_service.restart(timeout=5)
    proof = owner.bridge.native('probe', 'f' * 64)
    harness.atomic_json(owner.bridge.state / 'verified.json', {
        'pid': proof['pid'], 'proof': proof['proof'],
        'config_sha256': sha(owner.bridge.config_path.read_bytes()), 'boundary': owner.bridge.unit_boundary()})


@pytest.mark.parametrize('change', ['google', 'plugins', 'model', 'provider', 'source'])
def test_terminal_disconnect_reproves_current_disabled_choice_without_reopening_old_plan(owner, change):
    flow = _disconnected(owner)
    if change == 'google':
        harness.connect(owner)
        c.control(owner.bridge, 'connection-activate', {})
    elif change == 'plugins':
        c.control(owner.bridge, 'connection-plugin', {'plugin': 'memory-wiki', 'enabled': False})
    else:
        _accepted_config_update(owner, change)
    paths = (owner.controller.path, owner.controller.consent.path, owner.record, owner.bridge.config_path,
             owner.bridge.env_path, owner.bridge.answers_path, owner.bridge.state / 'verified.json')
    before = {path: path.read_bytes() for path in paths}
    calls, log = owner.native_calls[:], owner.bridge.log[:]
    result = owner.controller.disconnect()
    assert result['phase'] == 'disconnected' and result['providerRevoked'] is False
    assert {path: path.read_bytes() for path in paths} == before
    assert owner.native_calls == calls and owner.bridge.log == log
    enrollment.guard_idle(owner.bridge)
    assert owner.controller.recover(flow_id=flow)['phase'] == 'disconnected'
    new = owner.controller.begin(redirect_port=43123)
    assert new['phase'] == 'consent' and new['flowId'] != flow


@pytest.mark.parametrize('problem', ['root-choice', 'config-choice', 'unknown-config', 'runtime-proof'])
def test_terminal_disconnect_refuses_unknown_or_unverified_current_state_without_reopening(owner, monkeypatch, problem):
    _disconnected(owner)
    if problem == 'root-choice':
        c._write_record(dict(c._record(), nativeApps={'notion': True}))
    elif problem in ('config-choice', 'unknown-config'):
        config = json.loads(owner.bridge.config_path.read_bytes())
        if problem == 'config-choice':
            apps.load_native_apps(config, apps.answers({'notion': True}))
        else:
            config['mcp']['servers']['ods-notion']['url'] = 'https://other.example/mcp'
        harness.atomic_json(owner.bridge.config_path, config)
    else:
        inspect = owner.bridge.inspect
        monkeypatch.setattr(owner.bridge, 'inspect', lambda: dict(inspect(), runtime_verified=False))
    before = owner.controller.path.read_bytes()
    calls, log = owner.native_calls[:], owner.bridge.log[:]
    with pytest.raises(native.NativeError, match='native-app-(state-changed|runtime-unverified)'):
        owner.controller.disconnect()
    assert owner.controller.path.read_bytes() == before
    assert owner.native_calls == calls and owner.bridge.log == log
    enrollment.guard_idle(owner.bridge)


def test_terminal_disconnect_after_a_new_verified_grant_plans_fresh_exact_withdrawal(owner):
    _disconnected(owner)
    previous = owner.controller._read()['step']['transactionId']
    # A distinct admitted writer completed this grant. Its provider operation
    # is simulated; this test does not perform or replay another OAuth consent.
    c._write_record(dict(c._record(), nativeApps={'notion': True}, plugins={'memory-wiki': False}))
    _accepted_config_update(owner, 'regrant')
    owner.authorized = True
    calls = owner.native_calls[:]
    result = owner.controller.disconnect()
    current = owner.controller._read()
    assert result['phase'] == 'disconnected' and current['step']['transactionId'] != previous
    assert current['step']['kind'] == 'withdraw' and current['step']['outcome'] == 'applied'
    assert current['step']['beforeRecord']['nativeApps'] == {'notion': True}
    assert c._record()['plugins'] == {'memory-wiki': False} and choice(owner) == {'notion': False}
    assert owner.native_calls == calls + ['forget']
    assert owner.bridge.pending() is None
    enrollment.guard_idle(owner.bridge)


def _reserved_source(owner):
    import pixel_source_upgrade as source
    child = owner.bridge.state / 'source-upgrade'
    child.mkdir(mode=0o700)
    manager = source.SourceUpgrade(child, owner.bridge.install, owner.bridge.owner.pw_uid, state_uid=os.geteuid())
    manager._save({'version': 1, 'install': str(owner.bridge.install), 'uid': owner.bridge.owner.pw_uid,
        'identity': {'beforeRef': 'a' * 40, 'afterRef': 'b' * 40, 'markerSha256': 'c' * 64,
                     'configSha256': 'd' * 64, 'receiptSha256': None},
        'before': {}, 'after': {}, 'candidate': {}, 'hold': 'e' * 64, 'phase': 'held', 'outcome': None})
    return source, manager


@pytest.mark.parametrize('phase', ['absent', 'ready', 'disconnected'])
@pytest.mark.parametrize('source_state', ['held', 'invalid', 'unavailable'])
def test_fresh_disconnect_respects_source_reservation_before_any_journal_or_owner_effect(
        owner, monkeypatch, phase, source_state):
    if phase != 'absent':
        flow = authorize(owner)
        owner.controller.activate(flow_id=flow)
        if phase == 'disconnected':
            owner.controller.disconnect()
    else:
        owner.bridge.state.mkdir(mode=0o700, exist_ok=True)
    source, manager = _reserved_source(owner)
    if source_state == 'invalid':
        (manager.state / 'source-upgrade.json').write_text('{broken')
    elif source_state == 'unavailable':
        monkeypatch.setattr(source.SourceUpgrade, 'journal',
            lambda self: (_ for _ in ()).throw(PermissionError('fixture unavailable')))
    assert owner.bridge.pending() is None
    paths = (owner.controller.path, owner.record, owner.bridge.config_path, owner.bridge.env_path, owner.bridge.answers_path)
    before = {path: path.read_bytes() if path.exists() else None for path in paths}
    calls, log = owner.native_calls[:], owner.bridge.log[:]
    with pytest.raises(native.NativeError, match='^source-transition-(in-progress|unavailable)$'):
        owner.controller.disconnect()
    assert {path: path.read_bytes() if path.exists() else None for path in paths} == before
    assert owner.native_calls == calls and owner.bridge.log == log


@pytest.mark.parametrize('operation', ['recover', 'disconnect'])
def test_already_admitted_forget_recovery_remains_available_with_an_unreadable_source_journal(
        owner, monkeypatch, operation):
    flow = authorize(owner)
    owner.controller.activate(flow_id=flow)
    worker = owner.bridge.worker

    def lose(operation='status', **kwargs):
        result = worker(operation, **kwargs)
        if operation == 'native-mcp-oauth' and kwargs['native_oauth_request']['action'] == 'forget':
            monkeypatch.setattr(owner.bridge, 'worker', worker)
            raise harness.Crash()
        return result

    monkeypatch.setattr(owner.bridge, 'worker', lose)
    with pytest.raises(harness.Crash):
        owner.controller.disconnect()
    source, _manager = _reserved_source(owner)
    monkeypatch.setattr(source.SourceUpgrade, 'journal',
        lambda self: (_ for _ in ()).throw(PermissionError('fixture unavailable')))
    result = owner.controller.recover(flow_id=flow) if operation == 'recover' else owner.controller.disconnect()
    assert result['phase'] == 'disconnected'
    assert owner.native_calls.count('forget') == 2 and choice(owner) == {'notion': False}


def test_record_drift_during_consent_is_refused_without_overwriting_other_choices(owner):
    begin = owner.controller.begin(redirect_port=43123)
    record = c._record()
    c._write_record(dict(record, plugins={'memory-wiki': False}))
    before = owner.record.read_bytes()
    with pytest.raises(native.NativeError, match='native-app-state-changed'):
        owner.controller.complete(flow_id=begin['flowId'], callback_url=callback(owner))
    assert owner.record.read_bytes() == before and owner.native_calls == ['begin']


def test_other_mutating_connection_routes_can_guard_the_same_nonreentrant_root_lock(owner):
    begin = owner.controller.begin(redirect_port=43123)
    with owner.bridge.locked():
        with pytest.raises(native.NativeError, match='native-app-transition-in-progress'):
            enrollment.guard_idle(owner.bridge)
    owner.controller.cancel(flow_id=begin['flowId'])
    with owner.bridge.locked():
        enrollment.guard_idle(owner.bridge)


def test_journal_refuses_unknown_fields_and_unsafe_symlink_without_touching_its_target(owner):
    owner.controller.begin(redirect_port=43123)
    journal = owner.controller._read()
    for changes in ({'accessToken': 'PRIVATE'}, {'phase': 'connected'}, {'schemaVersion': True}):
        with pytest.raises(native.NativeError, match='invalid-native-enrollment-journal'):
            enrollment.validate_journal(dict(journal, **changes))
    owner.controller.path.unlink()
    outside = owner.controller.path.parent / 'preserve'
    outside.write_text('preserved')
    owner.controller.path.symlink_to(outside)
    with pytest.raises((native.NativeError, OSError)):
        owner.controller.status()
    assert outside.read_text() == 'preserved'
