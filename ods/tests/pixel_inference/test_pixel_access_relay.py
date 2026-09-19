"""Contract tests for bin/pixel_access_relay.py.

The relay is how a Windows/macOS dashboard host reaches the managed agent's
access controller through the inspected Edge container — model lifecycle
control (`model-begin`/`model-apply`/`model-finish`) and access-mode changes.
Credentials travel on child stdin only; response allocation is bounded and the
status allowlist is fixed before any bytes reach the host.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'bin'))
import pixel_access_relay as relay
from pixel_access_relay import (
    AccessRelayError, public_model_control, request_runtime_access,
    request_runtime_model_control, valid_change, valid_model_contract,
    valid_model_control,
)

HEX = 'c' * 64


def model_contract(**changes):
    value = {'model': 'qwen3-4b', 'contextLength': 32768, 'maxTokens': 4096,
             'reasoning': False}
    value.update(changes)
    return value


class TestValidators:
    @pytest.mark.parametrize('mode,confirmed,ok', [
        ('sandboxed', False, True), ('sandboxed', True, True),
        ('full-access', True, True), ('full-access', False, False),
    ])
    def test_valid_change(self, mode, confirmed, ok):
        value = {'mode': mode, 'revision': HEX, 'confirmed': confirmed}
        assert bool(valid_change(value)) is ok

    @pytest.mark.parametrize('patch', [
        {'mode': 'open'}, {'revision': 'short'}, {'confirmed': 'yes'}, {'x': 1},
    ])
    def test_invalid_change(self, patch):
        value = {'mode': 'sandboxed', 'revision': HEX, 'confirmed': True}
        value.update(patch)
        if 'x' in patch:
            value['x'] = 1
        assert not valid_change(value)

    def test_model_contract_parity_with_pixel_model_contract(self):
        assert valid_model_contract(model_contract()) is not None
        assert valid_model_contract(model_contract(routeFingerprint=HEX)) is not None
        for patch in ({'model': 'x' * 257}, {'contextLength': 4095},
                      {'maxTokens': 40000}, {'reasoning': 'y'},
                      {'routeFingerprint': 'nope'}, {'extra': 1}):
            assert not valid_model_contract(model_contract(**patch))

    @pytest.mark.parametrize('payload,ok', [
        ({'operation': 'model-status'}, True),
        ({'operation': 'model-status', 'request': {}}, False),
        ({'operation': 'model-begin',
          'request': {'transactionId': HEX, 'revision': HEX}}, True),
        ({'operation': 'model-begin', 'request': {'transactionId': HEX}}, False),
        ({'operation': 'model-apply',
          'request': {'transactionId': HEX, 'target': model_contract()}}, True),
        ({'operation': 'model-apply',
          'request': {'transactionId': HEX, 'target': {'model': 1}}}, False),
        ({'operation': 'model-finish',
          'request': {'transactionId': HEX, 'outcome': 'commit'}}, True),
        ({'operation': 'model-finish',
          'request': {'transactionId': HEX, 'outcome': 'maybe'}}, False),
        ({'operation': 'model-abort', 'request': {'transactionId': HEX}}, False),
    ])
    def test_valid_model_control(self, payload, ok):
        assert bool(valid_model_control(payload)) is ok


def control(status='ready', **changes):
    value = {'schemaVersion': 1, 'status': status, 'revision': HEX,
             'contract': model_contract(), 'pending': status in ('held', 'applied'),
             'transactionId': None if status == 'ready' else HEX, 'outcome': None}
    value.update(changes)
    return value


class TestPublicModelControl:
    @pytest.mark.parametrize('status', ['ready', 'held', 'applied'])
    def test_states(self, status):
        result = public_model_control(control(status))
        assert result['status'] == status
        assert result['pending'] == (status != 'ready')
        assert 'credential' not in json.dumps(result)

    def test_completed_requires_outcome(self):
        result = public_model_control(control('completed', transactionId=HEX,
                                              outcome='rollback'))
        assert result['outcome'] == 'rollback'
        with pytest.raises(ValueError, match='invalid-model-control-response'):
            public_model_control(control('completed', transactionId=HEX))
        with pytest.raises(ValueError):
            public_model_control(control('held', outcome='commit'))

    def test_ready_requires_no_transaction(self):
        with pytest.raises(ValueError):
            public_model_control(control('ready', transactionId=HEX))
        with pytest.raises(ValueError):
            public_model_control(control('held', transactionId=None))

    @pytest.mark.parametrize('patch', [
        {'status': 'unknown'}, {'revision': 'x'}, {'pending': 'yes'},
        {'transactionId': 'not-hex'},
    ])
    def test_rejects_invalid(self, patch):
        value = control('held')
        value.update(patch)
        with pytest.raises(ValueError, match='invalid-model-control-response'):
            public_model_control(value)


class _Proc:
    def __init__(self, stdout=b'', returncode=0):
        self.stdout, self.returncode = stdout, returncode


def config(**overrides):
    value = {'DASHBOARD_API_KEY': 'k' * 40, 'PIXEL_OPENWEBUI_KEY': 'p' * 40}
    value.update(overrides)
    return value


INSPECT = ('a' * 64 + ' true pixel-edge').encode()


class TestRuntimeModelControl:
    def _relay(self, monkeypatch, inspect_out=INSPECT, exec_out=b'{"status": 200, "body": {}}',
               exec_error=None):
        calls = []
        def fake_run(args, **kwargs):
            calls.append((args, kwargs))
            if args[1] == 'inspect':
                return _Proc(inspect_out)
            if exec_error is not None:
                raise exec_error
            return _Proc(exec_out)
        monkeypatch.setattr(subprocess, 'run', fake_run)
        return calls

    def test_invalid_operation_never_reaches_docker(self, monkeypatch):
        calls = self._relay(monkeypatch)
        status, body = request_runtime_model_control('model-abort', config=config())
        assert (status, body) == (400, {'error': 'invalid-request'})
        assert calls == []

    def test_missing_edge_key_fails_before_docker(self, monkeypatch):
        calls = self._relay(monkeypatch)
        with pytest.raises(AccessRelayError, match='managed-model-controller-unavailable'):
            request_runtime_model_control('model-status', config={'DASHBOARD_API_KEY': 'k' * 40})
        assert calls == []

    def test_happy_path_execs_inside_inspected_container(self, monkeypatch):
        payload = {'status': 200, 'body': control('held')}
        calls = self._relay(monkeypatch, exec_out=json.dumps(payload).encode())
        status, body = request_runtime_model_control(
            'model-begin', {'transactionId': HEX, 'revision': HEX}, config=config())
        assert status == 200 and body['status'] == 'held'
        inspect_args, exec_args = calls[0][0], calls[1][0]
        assert inspect_args[:2] == ['docker', 'inspect']
        assert exec_args[:2] == ['docker', 'exec'] and 'a' * 64 in exec_args
        stdin = json.loads(calls[1][1]['input'])
        assert stdin['key'] == 'k' * 40 and stdin['path'] == '/v1/model-control'
        assert stdin['request']['operation'] == 'model-begin'

    def test_malformed_200_body_is_unconfirmed(self, monkeypatch):
        payload = {'status': 200, 'body': {'schemaVersion': 1, 'status': 'bogus'}}
        self._relay(monkeypatch, exec_out=json.dumps(payload).encode())
        with pytest.raises(AccessRelayError, match='model-change-unconfirmed'):
            request_runtime_model_control('model-status', config=config())

    def test_non_200_statuses_pass_through(self, monkeypatch):
        payload = {'status': 409, 'body': {'error': 'runtime-busy'}}
        self._relay(monkeypatch, exec_out=json.dumps(payload).encode())
        status, body = request_runtime_model_control('model-status', config=config())
        assert status == 409 and body == {'error': 'runtime-busy'}

    def test_inspect_variants(self, monkeypatch):
        for bad in (b'short true pixel-edge', b'x' * 64 + b' false pixel-edge',
                    b'a' * 64 + b' true other-service', b'a' * 64 + b' true pixel-edge extra'):
            self._relay(monkeypatch, inspect_out=bad)
            with pytest.raises(AccessRelayError, match='agent-access-runtime-unavailable'):
                request_runtime_model_control('model-status', config=config())

    def test_oversized_child_output(self, monkeypatch):
        self._relay(monkeypatch, exec_out=b' ' * 65537)
        with pytest.raises(AccessRelayError, match='agent-access-runtime-unavailable'):
            request_runtime_model_control('model-status', config=config())

    def test_foreign_child_status_rejected(self, monkeypatch):
        self._relay(monkeypatch, exec_out=b'{"status": 500, "body": {}}')
        with pytest.raises(AccessRelayError, match='agent-access-runtime-unavailable'):
            request_runtime_model_control('model-status', config=config())

    def test_exec_timeout_maps_unavailable(self, monkeypatch):
        self._relay(monkeypatch, exec_error=subprocess.TimeoutExpired('docker', 25))
        with pytest.raises(AccessRelayError, match='agent-access-runtime-unavailable'):
            request_runtime_model_control('model-status', config=config())

    @pytest.mark.parametrize('key', ['short', 'has space ' + 'x' * 40, 'p' * 40])
    def test_owner_key_must_be_distinct_and_printable(self, monkeypatch, key):
        self._relay(monkeypatch)
        with pytest.raises(AccessRelayError, match='access-owner-auth-unavailable'):
            request_runtime_model_control('model-status',
                                          config=config(DASHBOARD_API_KEY=key))


class TestRuntimeAccess:
    def _relay(self, monkeypatch, exec_out=b'{"status": 200, "body": {"available": true}}'):
        def fake_run(args, **kwargs):
            if args[1] == 'inspect':
                return _Proc(INSPECT)
            return _Proc(exec_out)
        monkeypatch.setattr(subprocess, 'run', fake_run)

    def test_invalid_operation(self):
        status, body = request_runtime_access('mutate', config=config())
        assert status == 400

    def test_change_requires_valid_change(self):
        status, body = request_runtime_access(
            'change', {'mode': 'full-access', 'revision': HEX, 'confirmed': False},
            config=config())
        assert status == 400

    def test_non_linux_without_edge_key_returns_unavailable(self, monkeypatch):
        monkeypatch.setattr(relay.platform, 'system', lambda: 'Windows')
        status, body = request_runtime_access('status', config={'DASHBOARD_API_KEY': 'k' * 40})
        assert status == 200 and body['available'] is False
        assert body['reason'] == 'managed-runtime-unavailable'
        assert 'key' not in json.dumps(body).lower()

    def test_linux_without_edge_key_delegates(self, monkeypatch):
        monkeypatch.setattr(relay.platform, 'system', lambda: 'Linux')
        import pixel_access_client
        seen = []
        monkeypatch.setattr(pixel_access_client, 'request_access',
                            lambda op, req=None: seen.append((op, req)) or (200, {'ok': True}))
        status, body = request_runtime_access('status', config={'DASHBOARD_API_KEY': 'k' * 40})
        assert status == 200 and seen == [('status', None)]

    def test_change_uses_access_mode_path(self, monkeypatch):
        calls = []
        def fake_run(args, **kwargs):
            calls.append((args, kwargs))
            if args[1] == 'inspect':
                return _Proc(INSPECT)
            return _Proc(b'{"status": 200, "body": {"ok": true}}')
        monkeypatch.setattr(subprocess, 'run', fake_run)
        change = {'mode': 'sandboxed', 'revision': HEX, 'confirmed': True}
        status, _ = request_runtime_access('change', change, config=config())
        assert status == 200
        stdin = json.loads(calls[1][1]['input'])
        assert stdin['path'] == '/v1/access-mode' and stdin['request'] == change
