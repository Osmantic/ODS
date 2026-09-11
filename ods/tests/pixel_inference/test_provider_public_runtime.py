"""Public protocol/storage mocks, not installed provider acceptance."""
import copy
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'bin'))
import pixel_access_client
from pixel_provider import host_api, public
from pixel_provider.store import StoreError

BINDING = {'schemaVersion': 1, 'activationId': '123e4567-e89b-12d3-a456-426614174000',
           'revision': 3, 'allowCloud': False}
CHANGE = {'operation': 'apply', 'revision': 'a' * 64, 'providerRevision': 3}
TIME = '2026-09-09T18:01:25.584Z'


def runtime(state):
    if state == 'unavailable':
        return public.unavailable('provider-controller-unavailable')
    verified = state in ('applied', 'saved-changes', 'inactive')
    return {'schemaVersion': 1, 'status': state, 'revision': 'a' * 64,
            'providerRevision': 4 if state == 'saved-changes' else 3,
            'binding': copy.deepcopy(BINDING) if state in ('applied', 'saved-changes') else None,
            'pending': state == 'pending', 'registrationVerified': verified,
            'transportVerified': False, 'lastVerifiedAt': TIME if verified else None, 'reason': None}


def outcome(binding=BINDING, result='applied'):
    return {'outcome': result, 'binding': copy.deepcopy(binding),
            'registrationVerified': True, 'transportVerified': False}


@pytest.mark.parametrize('state', sorted(public.STATES))
def test_exact_status_shape_and_detached_binding(state):
    value = runtime(state)
    result = public.normalize_runtime(value)
    assert result == value and result is not value
    if value['binding']:
        result['binding']['revision'] = 999
        assert value['binding']['revision'] == 3


@pytest.mark.parametrize('state,key,value', [
    ('applied', 'providerRevision', 4), ('saved-changes', 'providerRevision', 3),
    ('inactive', 'binding', BINDING), ('pending', 'binding', BINDING),
    ('pending', 'pending', False), ('pending', 'registrationVerified', True),
    ('not-applied', 'lastVerifiedAt', TIME), ('not-applied', 'registrationVerified', True),
    ('applied', 'lastVerifiedAt', '2026-02-31T18:00:00Z'),
    ('applied', 'lastVerifiedAt', '2026-09-09T18:00:00+00:00'),
    ('applied', 'transportVerified', True), ('applied', 'transportVerified', 0),
    ('applied', 'registrationVerified', 1), ('applied', 'pending', True),
    ('unavailable', 'pending', False), ('unavailable', 'reason', 'private-sentinel'),
    ('applied', 'schemaVersion', True), ('applied', 'providerRevision', True),
    ('applied', 'revision', 'A' * 64), ('applied', 'binding', dict(BINDING, extra='secret')),
])
def test_contradictory_status_is_not_renderable(state, key, value):
    data = runtime(state)
    data[key] = value
    with pytest.raises(ValueError): public.normalize_runtime(data)


@pytest.mark.parametrize('operation', ['apply', 'deactivate', 'recover'])
@pytest.mark.parametrize('revision', [0, 2**53 - 1])
def test_change_accepts_only_existing_root_contract(operation, revision):
    value = dict(CHANGE, operation=operation, providerRevision=revision)
    assert public.normalize_change(value) == value


@pytest.mark.parametrize('value', [None, [], {}, dict(CHANGE, operation=[]), dict(CHANGE, operation='provider-recover'),
    dict(CHANGE, providerRevision=True), dict(CHANGE, providerRevision=2**53), dict(CHANGE, revision='A' * 64),
    dict(CHANGE, binding=BINDING), dict(CHANGE, data_dir_id='a' * 64), dict(CHANGE, expected_projection={})])
def test_change_rejects_caller_authority(value):
    with pytest.raises(ValueError): public.normalize_change(value)


def test_outcome_checks_operation_but_recovery_can_restore_an_older_revision():
    assert public.normalize_outcome(outcome(), CHANGE) == outcome()
    assert public.normalize_outcome(outcome(None), dict(CHANGE, operation='deactivate')) == outcome(None)
    old = outcome(dict(BINDING, revision=1), 'rolled-back')
    assert public.normalize_outcome(old, dict(CHANGE, operation='recover')) == old
    for bad in (outcome(None), outcome(dict(BINDING, revision=4)), outcome(result='rolled-back')):
        with pytest.raises(ValueError): public.normalize_outcome(bad, CHANGE)
    with pytest.raises(ValueError): public.normalize_outcome(outcome(), dict(CHANGE, operation='deactivate'))


def test_controller_raw_shape_cannot_override_public_envelope():
    value = runtime('pending')
    raw = {key: item for key, item in value.items() if key not in ('schemaVersion', 'reason')}
    assert public.from_controller(raw) == value
    for key in ('schemaVersion', 'reason', 'secret'):
        with pytest.raises(ValueError): public.from_controller(dict(raw, **{key: None}))


def test_dashboard_public_schema_is_byte_identical_and_agrees():
    path = ROOT / 'extensions/services/dashboard-api/pixel_provider_runtime_public.py'
    assert path.read_bytes() == (ROOT / 'bin/pixel_provider/public.py').read_bytes()
    spec = importlib.util.spec_from_file_location('isolated_provider_public', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for state in public.STATES:
        assert module.normalize_runtime(runtime(state)) == public.normalize_runtime(runtime(state))


def test_host_status_preserves_pending_and_fixed_codes(monkeypatch, tmp_path):
    monkeypatch.setattr(host_api.platform, 'system', lambda: 'Linux')
    value = runtime('pending')
    raw = {key: item for key, item in value.items() if key not in ('schemaVersion', 'reason')}
    assert host_api.runtime_status(tmp_path, request=lambda *a, **kw: (200, raw)) == value
    for error, expected in [('settings-store-not-initialized', 'settings-store-not-initialized'),
                            ('private-sentinel', 'provider-controller-unavailable')]:
        result = host_api.runtime_status(tmp_path, request=lambda *a, error=error, **kw: (503, {'error': error}))
        assert result == public.unavailable(expected)


@pytest.mark.parametrize('failure', ['timeout', 'bad-outcome', 'private-error', 'nonobject-error'])
def test_host_change_never_retries_unknown_outcome(monkeypatch, tmp_path, failure):
    monkeypatch.setattr(host_api.platform, 'system', lambda: 'Linux')
    calls = []
    def request(*args, **kwargs):
        calls.append((args, kwargs))
        if failure == 'timeout': raise TimeoutError('private-sentinel')
        if failure == 'bad-outcome': return 200, outcome(dict(BINDING, revision=99))
        if failure == 'nonobject-error': return 503, None
        return 503, {'error': 'private-sentinel'}
    with pytest.raises(StoreError) as error: host_api.runtime_change(tmp_path, CHANGE, request=request)
    assert error.value.code in ('provider-transition-uncertain', 'provider-transition-unavailable')
    assert calls == [(('provider-change', CHANGE), {'settings_data_dir': tmp_path})]


@pytest.mark.parametrize('body', [None, [], 'private-sentinel'])
def test_host_status_maps_nonobject_controller_errors(monkeypatch, tmp_path, body):
    monkeypatch.setattr(host_api.platform, 'system', lambda: 'Linux')
    assert host_api.runtime_status(tmp_path, request=lambda *a, **kw: (503, body)) == public.unavailable('provider-controller-unavailable')


def test_native_platform_does_not_call_linux_controller(monkeypatch, tmp_path):
    monkeypatch.setattr(host_api.platform, 'system', lambda: 'Windows')
    def forbidden(*_args, **_kwargs): raise AssertionError('must not call controller')
    assert host_api.runtime_status(tmp_path, request=forbidden)['reason'] == 'native-windows-adapter-missing'
    with pytest.raises(StoreError): host_api.runtime_change(tmp_path, CHANGE, request=forbidden)


@pytest.mark.parametrize('operation', ['provider-status', 'provider-change'])
def test_client_emits_only_fixed_root_operation_and_host_data_hash(monkeypatch, tmp_path, operation):
    import hashlib
    sent = []
    class Socket:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def settimeout(self, value): assert value == 335
        def connect(self, path): assert path == '/run/ods-pixel-access/control.sock'
        def sendall(self, raw): sent.append(json.loads(raw))
        def makefile(self, mode): return io.BytesIO(b'{"status":200,"body":{}}\n')
    monkeypatch.setattr(pixel_access_client.socket, 'socket', lambda *_args: Socket())
    assert pixel_access_client.request_access(operation, CHANGE, settings_data_dir=tmp_path) == (200, {})
    expected = {'operation': operation, 'data_dir_id': hashlib.sha256(str(tmp_path).encode()).hexdigest()}
    if operation == 'provider-change': expected['request'] = CHANGE
    assert sent == [expected]
    with pytest.raises(ValueError): pixel_access_client.request_access('provider-recover', CHANGE, settings_data_dir=tmp_path)
