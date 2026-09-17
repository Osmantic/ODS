"""Contract tests for bin/pixel_access_protocol.py.

These are the fixed owner-worker pipe frames between the host agent and the
access controller: bounded newline frames with duplicate-key and non-finite
rejection, per-operation request/result schemas, hook replies, and the
provider-binding shape. `test_pixel_model_transition.py` exercises the
coordinator; this file pins the wire contract itself.
"""
import io
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'bin'))
import pixel_access_protocol as protocol
from pixel_access_protocol import ProtocolError

HEX = 'd' * 64
DIR = 'e' * 64
BASE = {'openclaw': '/abs/openclaw.json', 'config_sha256': HEX, 'confirmed': True}


def frame(value):
    import json
    return json.dumps(value, separators=(',', ':')) + '\n'


class TestDecodeFrame:
    def test_roundtrip(self):
        assert protocol.decode_frame(frame({'a': 1}), 16384) == {'a': 1}

    def test_requires_trailing_newline(self):
        with pytest.raises(ProtocolError, match='owner-protocol-failed'):
            protocol.decode_frame('{"a":1}', 16384)

    def test_byte_bound(self):
        with pytest.raises(ProtocolError):
            protocol.decode_frame(frame({'a': 'x' * 100}), 50)

    def test_rejects_non_str(self):
        with pytest.raises(ProtocolError):
            protocol.decode_frame(b'{"a":1}\n', 16384)

    def test_rejects_duplicate_keys(self):
        with pytest.raises(ProtocolError, match='owner-protocol-failed'):
            protocol.decode_frame('{"a":1,"a":2}\n', 16384)

    @pytest.mark.parametrize('raw', ['{"a":NaN}\n', '{"a":Infinity}\n', '{"a":-Infinity}\n',
                                     '{"a":1e999}\n', 'not json\n'])
    def test_rejects_non_finite_and_malformed(self, raw):
        with pytest.raises(ProtocolError):
            protocol.decode_frame(raw, 16384)

    def test_read_frame_bounds_line(self):
        with pytest.raises(ProtocolError):
            protocol.read_frame(io.StringIO('x' * 100 + '\n'), 50)


class TestControlRequest:
    @pytest.mark.parametrize('operation,keys', [
        ('status', set()), ('model-status', set()),
        ('change', {'request'}), ('model-begin', set()),
        ('model-finish', {'request'}),
        ('model-route-status', set()), ('model-route-begin', {'request'}),
        ('model-route-apply', {'request'}), ('model-route-finish', {'request'}),
        ('settings-status', {'data_dir_id'}), ('settings-change', {'data_dir_id', 'request'}),
        ('provider-status', {'data_dir_id'}), ('provider-change', {'data_dir_id', 'request'}),
    ])
    def test_exact_key_sets(self, operation, keys):
        value = {'operation': operation}
        if 'request' in keys:
            if operation == 'model-finish':
                value['request'] = {'transaction_id': HEX, 'outcome': 'applied'}
            elif operation.startswith('model-route-'):
                value['request'] = {
                    'model-route-begin': {'transactionId': HEX, 'revision': HEX},
                    'model-route-apply': {'transactionId': HEX, 'target': {}},
                    'model-route-finish': {'transactionId': HEX, 'outcome': 'commit'},
                }[operation]
            else:
                value['request'] = {}
        if 'data_dir_id' in keys:
            value['data_dir_id'] = DIR
        assert protocol.control_request(value) is value

    def test_unknown_operation_and_extra_keys(self):
        for value in ({'operation': 'exec'}, {'operation': 'status', 'x': 1},
                      {'operation': 'change'}, {'operation': 'status', 'request': {}}):
            with pytest.raises(ProtocolError, match='invalid-request'):
                protocol.control_request(value)

    def test_data_dir_id_must_be_hex(self):
        for bad in ('short', 'g' * 64, 7):
            with pytest.raises(ProtocolError, match='invalid-request'):
                protocol.control_request({'operation': 'settings-status', 'data_dir_id': bad})

    def test_model_finish_outcome(self):
        for outcome, ok in (('applied', True), ('rolled-back', True),
                            ('commit', False), ('x', False)):
            value = {'operation': 'model-finish',
                     'request': {'transaction_id': HEX, 'outcome': outcome}}
            if ok:
                assert protocol.control_request(value)
            else:
                with pytest.raises(ProtocolError):
                    protocol.control_request(value)

    def test_model_route_requests(self):
        for op, request, ok in (
            ('model-route-begin', {'transactionId': HEX, 'revision': HEX}, True),
            ('model-route-begin', {'transactionId': HEX, 'revision': 'x'}, False),
            ('model-route-apply', {'transactionId': HEX, 'target': {}}, True),
            ('model-route-apply', {'transactionId': HEX, 'target': 'x'}, False),
            ('model-route-finish', {'transactionId': HEX, 'outcome': 'rollback'}, True),
            ('model-route-finish', {'transactionId': HEX, 'outcome': 'applied'}, False),
        ):
            value = {'operation': op, 'request': request}
            if ok:
                assert protocol.control_request(value)
            else:
                with pytest.raises(ProtocolError, match='invalid-request'):
                    protocol.control_request(value)


def worker(operation, **extra):
    value = dict(BASE, operation=operation)
    value.update(extra)
    return value


class TestWorkerRequest:
    @pytest.mark.parametrize('operation', [
        'status', 'settings-status', 'provider-status', 'provider-worker-status',
        'model-status',
    ])
    def test_status_family_requires_null_sha(self, operation):
        value = worker(operation, config_sha256=None)
        if operation == 'provider-worker-status':
            value['provider_probe'] = {'python': '/usr/bin/python3',
                                       'launcher': '/x/ods-pixel-route-lease',
                                       'providerDirectory': '/x/providers', 'receipt': {}}
        assert protocol.request(value)
        with pytest.raises(ProtocolError):
            protocol.request(worker(operation))

    @pytest.mark.parametrize('operation', ['full-access', 'sandboxed'])
    def test_mode_changes_require_sha_but_no_transaction(self, operation):
        assert protocol.request(worker(operation))
        with pytest.raises(ProtocolError):
            protocol.request(worker(operation, config_sha256=None))
        with pytest.raises(ProtocolError):
            protocol.request(worker(operation, config_sha256='x'))

    @pytest.mark.parametrize('operation', [
        'settings-apply', 'settings-recover', 'provider-change', 'provider-recover',
        'model-begin', 'model-apply', 'model-rollback', 'model-finish',
    ])
    def test_mutations_require_transaction_and_sha(self, operation):
        extra = {'transaction_id': HEX}
        if operation == 'settings-apply':
            extra.update(settings_revision=1, preferences={}, capabilities={})
        if operation == 'provider-change':
            extra['binding'] = None
        if operation == 'model-apply':
            extra['model_target'] = {'model': 'm', 'contextLength': 4096,
                                     'maxTokens': 100, 'reasoning': False}
        if operation == 'model-finish':
            extra['model_outcome'] = 'commit'
        assert protocol.request(worker(operation, **extra))
        with pytest.raises(ProtocolError):
            protocol.request(worker(operation, config_sha256=None, **extra))
        with pytest.raises(ProtocolError):
            protocol.request(worker(operation, transaction_id='x', **{k: v for k, v in extra.items() if k != 'transaction_id'}))

    def test_openclaw_must_be_absolute_posix(self):
        for bad in ('relative/path', 'has\x00null', 7):
            with pytest.raises(ProtocolError):
                protocol.request(worker('status', config_sha256=None, openclaw=bad))
        with pytest.raises(ProtocolError):
            protocol.request(worker('status', config_sha256=None, confirmed='yes'))

    def test_model_apply_validates_target(self):
        target = {'model': 'm', 'contextLength': 4096, 'maxTokens': 100, 'reasoning': False}
        assert protocol.request(worker('model-apply', transaction_id=HEX, model_target=target))
        with pytest.raises(ProtocolError):
            protocol.request(worker('model-apply', transaction_id=HEX,
                                    model_target=dict(target, contextLength=100)))

    def test_model_finish_outcome(self):
        assert protocol.request(worker('model-finish', transaction_id=HEX,
                                       model_outcome='rollback'))
        with pytest.raises(ProtocolError):
            protocol.request(worker('model-finish', transaction_id=HEX,
                                    model_outcome='applied'))

    def test_provider_change_binding_and_projection(self):
        binding = {'schemaVersion': 1, 'activationId': str(uuid.uuid4()),
                   'revision': 0, 'allowCloud': False}
        assert protocol.request(worker('provider-change', transaction_id=HEX,
                                       binding=binding))
        assert protocol.request(worker('provider-change', transaction_id=HEX,
                                       binding=binding,
                                       expected_projection={'afterSha': HEX,
                                                            'previousPlanSha': None}))
        for bad in ({'afterSha': 'x', 'previousPlanSha': None},
                    {'afterSha': HEX}, {'afterSha': HEX, 'previousPlanSha': 'x'}):
            with pytest.raises(ProtocolError):
                protocol.request(worker('provider-change', transaction_id=HEX,
                                        binding=binding, expected_projection=bad))
        for bad_binding in ({'schemaVersion': 2, 'activationId': str(uuid.uuid4()),
                             'revision': 0, 'allowCloud': False},
                            dict(binding, activationId='not-uuid'),
                            dict(binding, revision=-1)):
            with pytest.raises(ProtocolError):
                protocol.request(worker('provider-change', transaction_id=HEX,
                                        binding=bad_binding))

    def test_worker_probe_shape(self):
        probe = {'python': '/usr/bin/python3', 'launcher': '/x/ods-pixel-route-lease',
                 'providerDirectory': '/x/providers', 'receipt': {}}
        base = worker('provider-worker-status', config_sha256=None)
        assert protocol.request(dict(base, provider_probe=probe))
        for patch in ({'launcher': '/x/other'}, {'python': 'relative'},
                      {'providerDirectory': '/x/\x00'}, {'receipt': 'x'}):
            bad = dict(probe, **patch)
            with pytest.raises(ProtocolError):
                protocol.request(dict(base, provider_probe=bad))

    def test_settings_apply_fields(self):
        base = dict(transaction_id=HEX, preferences={}, capabilities={})
        assert protocol.request(worker('settings-apply', settings_revision=0, **base))
        for bad in (dict(base, settings_revision=-1), dict(base, settings_revision='1'),
                    dict(base, preferences='x'), dict(base, capabilities='x')):
            with pytest.raises(ProtocolError):
                protocol.request(worker('settings-apply', **bad))


class TestHookReply:
    @pytest.mark.parametrize('operation,name,ok', [
        ('status', 'busy', False), ('full-access', 'busy', True),
        ('sandboxed', 'restart', True), ('settings-apply', 'busy', True),
        ('settings-apply', 'settings-activate', True),
        ('provider-change', 'provider-activate', True),
        ('model-begin', 'busy', True), ('model-status', 'busy', False),
    ])
    def test_hook_allowlist(self, operation, name, ok):
        value = 'verified' if name.endswith('activate') else True
        if ok:
            assert protocol.hook_reply(operation, name, value) == value
        else:
            with pytest.raises(ProtocolError, match='owner-protocol-failed'):
                protocol.hook_reply(operation, name, value)

    def test_activate_values(self):
        for value, ok in (('verified', True), ('rejected', True), ('unavailable', True),
                          ('yes', False), (True, False)):
            if ok:
                assert protocol.hook_reply('settings-apply', 'settings-activate', value) == value
            else:
                with pytest.raises(ProtocolError, match='host-hook-failed'):
                    protocol.hook_reply('settings-apply', 'settings-activate', value)

    def test_busy_hooks_are_bool(self):
        with pytest.raises(ProtocolError, match='host-hook-failed'):
            protocol.hook_reply('full-access', 'busy', 'yes')


def settings_status(**changes):
    value = {'configSha256': HEX, 'managedRevision': 3, 'pending': False,
             'completion': None, 'runtimeVerified': False}
    value.update(changes)
    return value


class TestResult:
    def test_model_result_requires_sha(self):
        with pytest.raises(ProtocolError):
            protocol.result('model-begin', {})
        assert protocol.result('model-apply', {'configSha256': HEX})

    def test_model_status_result(self):
        value = {'configSha256': HEX, 'contract': {'model': 'm', 'contextLength': 4096,
                 'maxTokens': 1, 'reasoning': False}, 'limits': {}, 'pending': False,
                 'transactionId': None, 'completion': None}
        assert protocol.result('model-status', value)
        done = {'transactionId': HEX, 'outcome': 'commit', 'configSha256': HEX}
        assert protocol.result('model-status', dict(value, completion=done))
        with pytest.raises(ProtocolError):
            protocol.result('model-status', dict(value, completion=dict(done, outcome='x')))

    def test_worker_status_ready_bool(self):
        assert protocol.result('provider-worker-status', {'ready': True})
        with pytest.raises(ProtocolError):
            protocol.result('provider-worker-status', {'ready': 'yes'})

    def test_settings_status_result(self):
        assert protocol.result('settings-status', settings_status())
        with pytest.raises(ProtocolError):
            protocol.result('settings-status', settings_status(runtimeVerified=True))
        done = {'transactionId': HEX, 'settingsRevision': 3, 'outcome': 'applied',
                'configSha256': HEX}
        assert protocol.result('settings-status', settings_status(completion=done))
        with pytest.raises(ProtocolError):
            protocol.result('settings-status', settings_status(completion=done, pending=True))

    def test_settings_apply_result(self):
        assert protocol.result('settings-apply', {'status': 'runtime-verified',
                                                  'settingsRevision': 2,
                                                  'configSha256': HEX})
        assert protocol.result('settings-apply', {'status': 'rolled-back',
                                                  'configSha256': HEX})
        with pytest.raises(ProtocolError):
            protocol.result('settings-apply', {'status': 'applied', 'configSha256': HEX})
        with pytest.raises(ProtocolError):
            protocol.result('settings-recover', {'status': 'runtime-verified',
                                                 'settingsRevision': 2, 'configSha256': HEX})

    def test_provider_status_result(self):
        binding = {'schemaVersion': 1, 'activationId': str(uuid.uuid4()), 'revision': 0,
                   'allowCloud': False}
        value = {'configSha256': HEX, 'binding': binding, 'pending': False,
                 'completion': None, 'runtimeVerified': False}
        assert protocol.result('provider-status', value)
        done = {'transactionId': HEX, 'binding': binding, 'outcome': 'applied',
                'configSha256': HEX}
        assert protocol.result('provider-status', dict(value, completion=done))
        with pytest.raises(ProtocolError):
            protocol.result('provider-status', dict(value, binding='x'))

    def test_provider_change_result(self):
        binding = {'schemaVersion': 1, 'activationId': str(uuid.uuid4()), 'revision': 0,
                   'allowCloud': False}
        assert protocol.result('provider-change', {'status': 'registration-verified',
                                                   'binding': binding, 'configSha256': HEX})
        assert protocol.result('provider-change', {'status': 'rolled-back', 'binding': None,
                                                   'configSha256': HEX})
        with pytest.raises(ProtocolError):
            protocol.result('provider-recover', {'status': 'registration-verified',
                                                 'binding': binding, 'configSha256': HEX})

    def test_access_operations_pass_through(self):
        assert protocol.result('status', {'anything': 'goes'})
        assert protocol.result('full-access', {'custom': 1})
