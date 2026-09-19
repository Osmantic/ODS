"""Contract tests for pixel_sharing_public.normalize_sharing_response.

This validator decides which host sharing responses the dashboard may trust:
the device list it renders, the route it advertises, and — on the issued
path — the credential a client will use. Every check runs before the response
reaches the UI, so a forged or drifted host reply must fail closed.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pixel_sharing_public import normalize_sharing_response

INT53 = 2**53 - 1


def device(**kw):
    doc = {
        'id': 'device-0123456789abcdef',
        'label': 'Pixel 9',
        'catalogId': 'qwen3-4b',
        'runtimeModelId': 'qwen3-4b.gguf',
        'createdAt': 1000,
        'expiresAt': 2000,
        'revoked': False,
        'maxConcurrent': 4,
        'maxOutputTokens': 4096,
        'deadlineSeconds': 60,
        'requestsPerMinute': 60,
    }
    doc.update(kw)
    return doc


def response(issued=False, **kw):
    doc = {
        'configuration': {
            'schemaVersion': 1,
            'revision': 7,
            'enabled': True,
            'devices': [device()],
        },
        'activeRoute': {
            'catalogId': 'qwen3-4b',
            'runtimeModelId': 'qwen3-4b.gguf',
            'routeSeq': 3,
            'contextLength': 32768,
            'capabilities': {'chat': True, 'tools': True,
                             'vision': False, 'agentViable': False},
        },
        'transport': {'mode': 'loopback-only',
                      'defaultPort': 4005, 'port': 4005},
        'runtime': {'status': 'ready'},
    }
    if issued:
        doc['model'] = 'ods/shared'
        doc['credential'] = {
            'id': 'device-0123456789abcdef',
            'key': 'ods_infer_' + 'a' * 64,
        }
    doc.update(kw)
    return doc


def invalid(doc, issued=False):
    with pytest.raises(ValueError, match='invalid-sharing-response'):
        normalize_sharing_response(doc, issued=issued)


class TestRootShape:
    def test_valid(self):
        doc = response()
        result = normalize_sharing_response(doc)
        assert result == doc and result is not doc

    def test_issued_valid(self):
        doc = response(issued=True)
        assert normalize_sharing_response(doc, issued=True) == doc

    @pytest.mark.parametrize('doc', [
        None, [], 'x',
        dict(response(), extra=1),
        {k: v for k, v in response().items() if k != 'runtime'},
        # credential/model must not appear when not issued
        dict(response(), credential={'id': 'x', 'key': 'y'}),
        dict(response(), model='ods/shared'),
    ])
    def test_root_keys_exact(self, doc):
        invalid(doc)

    def test_issued_requires_credential_and_model(self):
        with pytest.raises(ValueError):
            normalize_sharing_response(response(), issued=True)


class TestConfiguration:
    @pytest.mark.parametrize('mutation', [
        {'schemaVersion': 2}, {'schemaVersion': '1'}, {'schemaVersion': True},
        {'revision': -1}, {'revision': 2**53}, {'revision': '7'},
        {'revision': True}, {'enabled': 1}, {'devices': 'x'},
        {'devices': [{}] * 65},
    ])
    def test_config_fields(self, mutation):
        doc = response()
        doc['configuration'].update(mutation)
        invalid(doc)

    def test_devices_empty_ok(self):
        doc = response()
        doc['configuration']['devices'] = []
        assert normalize_sharing_response(doc)


class TestDevices:
    @pytest.mark.parametrize('mutation', [
        {'id': 'device-0123456789abcdeg'},   # non-hex
        {'id': 'DEVICE-0123456789abcdef'},
        {'id': 'device-0123'},
        {'label': ''}, {'label': ' pad '}, {'label': 'x' * 257},
        {'label': 'line\nbreak'}, {'label': 'émoji'},
        {'catalogId': ' x'}, {'runtimeModelId': 'x' * 257},
        {'createdAt': -1}, {'createdAt': True},
        {'expiresAt': 1000},                  # must be > createdAt
        {'expiresAt': 2**53},
        {'revoked': 0},
        {'maxConcurrent': 0}, {'maxConcurrent': 9},
        {'maxOutputTokens': 0}, {'maxOutputTokens': 131073},
        {'deadlineSeconds': 0}, {'deadlineSeconds': 3601},
        {'requestsPerMinute': 0}, {'requestsPerMinute': 601},
        {'tokenHash': 'a' * 64},              # secret never allowed
    ])
    def test_device_fields(self, mutation):
        doc = response()
        doc['configuration']['devices'] = [device(**mutation)]
        invalid(doc)

    def test_duplicate_device_id_rejected(self):
        doc = response()
        doc['configuration']['devices'] = [device(), device()]
        invalid(doc)

    def test_distinct_devices_ok(self):
        doc = response()
        doc['configuration']['devices'] = [
            device(), device(id='device-ffffffffffffffff')]
        assert len(normalize_sharing_response(
            doc)['configuration']['devices']) == 2


class TestActiveRoute:
    def test_none_allowed(self):
        doc = response(activeRoute=None)
        assert normalize_sharing_response(doc)['activeRoute'] is None

    @pytest.mark.parametrize('route', [
        'x', [],
        {'catalogId': 'q', 'runtimeModelId': 'm', 'routeSeq': 0,
         'contextLength': 1,
         'capabilities': {'chat': True, 'tools': False, 'vision': False,
                          'agentViable': False}, 'extra': 1},
        {'catalogId': ' ', 'runtimeModelId': 'm', 'routeSeq': 0,
         'contextLength': 1, 'capabilities': {'chat': True, 'tools': False,
                                            'vision': False,
                                            'agentViable': False}},
    ])
    def test_route_shape(self, route):
        invalid(response(activeRoute=route))

    @pytest.mark.parametrize('mutation', [
        {'routeSeq': -1}, {'routeSeq': '3'},
        {'contextLength': 0}, {'contextLength': 10_000_001},
        {'contextLength': True},
    ])
    def test_route_fields(self, mutation):
        route = dict(response()['activeRoute'], **mutation)
        invalid(response(activeRoute=route))

    def test_capabilities_exact_bool_set(self):
        route = response()['activeRoute']
        for bad in ({'chat': 1, 'tools': False, 'vision': False,
                     'agentViable': False},
                    {'chat': True, 'tools': False, 'vision': False},
                    {'chat': True, 'tools': False, 'vision': False,
                     'agentViable': False, 'extra': True}):
            invalid(response(activeRoute=dict(route, capabilities=bad)))


class TestTransport:
    @pytest.mark.parametrize('mutation', [
        {'mode': 'lan'}, {'defaultPort': 4006}, {'defaultPort': '4005'},
        {'port': 80}, {'port': 1023}, {'port': 65536}, {'port': '4005'},
    ])
    def test_transport_fields(self, mutation):
        doc = response()
        doc['transport'].update(mutation)
        invalid(doc)


class TestRuntime:
    @pytest.mark.parametrize('status', [
        'not-probed', 'starting', 'ready', 'stopped', 'error', 'unavailable',
    ])
    def test_valid_statuses(self, status):
        assert normalize_sharing_response(
            response(runtime={'status': status}))

    @pytest.mark.parametrize('runtime', [
        {'status': 'busy'}, {'status': 'ready', 'extra': 1}, 'ready',
    ])
    def test_invalid_runtime(self, runtime):
        invalid(response(runtime=runtime))


class TestIssuedFields:
    def test_model_must_be_shared_alias(self):
        invalid(response(issued=True, model='other'), issued=True)

    def test_credential_exact_keys(self):
        doc = response(issued=True)
        doc['credential']['extra'] = 'x'
        invalid(doc, issued=True)

    def test_credential_id_must_be_live_device(self):
        doc = response(issued=True)
        doc['credential']['id'] = 'device-ffffffffffffffff'
        invalid(doc, issued=True)

    def test_credential_revoked_device_rejected(self):
        doc = response(issued=True)
        doc['configuration']['devices'][0]['revoked'] = True
        invalid(doc, issued=True)

    @pytest.mark.parametrize('key', [
        'ods_infer_' + 'a' * 63, 'ods_infer_' + 'a' * 65,
        'ods_infer_' + 'A' * 64, 'sk-' + 'a' * 64, 'ods_infer_',
    ])
    def test_key_format(self, key):
        doc = response(issued=True)
        doc['credential']['key'] = key
        invalid(doc, issued=True)


class TestIsolation:
    def test_result_is_deep_copy(self):
        doc = response()
        result = normalize_sharing_response(doc)
        result['configuration']['devices'][0]['revoked'] = True
        assert doc['configuration']['devices'][0]['revoked'] is False
