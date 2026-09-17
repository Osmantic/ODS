"""Contract tests for dashboard-api/pixel_connection_public.py.

`normalize_connection_result` is the credential-free validator applied to the
host agent's metadata-probe result before the dashboard trusts it
(routers/pixel_providers.py). It enforces the exact envelope shape, device
identity, endpoint URL rules, and the expected-vs-metadata cross-checks that
stop a forged or stale probe from presenting a route as verified.
"""
import copy
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pixel_connection_public import normalize_connection_result


def valid(**overrides):
    expires = int(time.time()) + 600
    value = {
        'schemaVersion': 1,
        'endpoint': 'http://127.0.0.1:11434/v1',
        'deviceId': 'device-0123456789abcdef',
        'expiresAt': expires,
        'expected': {'catalogId': 'qwen3-4b', 'runtimeModelId': 'qwen3-4b.gguf'},
        'metadata': {
            'catalogId': 'qwen3-4b', 'routedModel': 'qwen3-4b.gguf',
            'identitySource': 'ods-verified-route', 'routeSeq': 3,
            'contextLength': 32768, 'maxOutputTokens': 4096,
            'expiresAt': expires, 'execution': 'client-owned',
            'capabilities': {'chat': True, 'tools': True, 'vision': False,
                             'agentViable': False},
        },
    }
    value.update(overrides)
    return value


def with_meta(**mutations):
    value = valid()
    value['metadata'].update(mutations)
    return value


class TestEnvelope:
    def test_valid_result(self):
        value = valid()
        result = normalize_connection_result(value)
        assert result == value and result is not value

    @pytest.mark.parametrize('doc', [
        None, [], 'x',
        dict(valid(), extra=1),
        {k: v for k, v in valid().items() if k != 'endpoint'},
        dict(valid(), schemaVersion=2),
        dict(valid(), schemaVersion='1'),
        dict(valid(), expiresAt='soon'),
        dict(valid(), expiresAt=int(time.time()) - 10),  # already expired
        dict(valid(), expiresAt=2**53),
        dict(valid(), deviceId='not-a-device'),
        dict(valid(), deviceId='device-0123456789ABCDEF'),  # uppercase
        dict(valid(), deviceId='device-0123'),
    ])
    def test_invalid_envelope(self, doc):
        with pytest.raises(ValueError, match='invalid-connection-result'):
            normalize_connection_result(doc)

    def test_expiry_boundary(self):
        # expiresAt == now is already expired.
        with pytest.raises(ValueError):
            normalize_connection_result(valid(expiresAt=int(time.time())))


class TestExpected:
    @pytest.mark.parametrize('expected', [
        {'catalogId': 'x'},
        {'catalogId': 'x', 'runtimeModelId': 'y', 'extra': 'z'},
        {'catalogId': '', 'runtimeModelId': 'y'},
        {'catalogId': ' x ', 'runtimeModelId': 'y'},   # outer whitespace
        {'catalogId': 'x\n', 'runtimeModelId': 'y'},  # control char
        {'catalogId': 'x' * 257, 'runtimeModelId': 'y'},
        {'catalogId': 'x' * 256, 'runtimeModelId': 'é'},  # non-ASCII
    ])
    def test_expected_fields_strict_text(self, expected):
        with pytest.raises(ValueError, match='invalid-connection-result'):
            normalize_connection_result(valid(expected=expected))


class TestEndpoint:
    @pytest.mark.parametrize('endpoint', [
        'http://127.0.0.1:11434/v1', 'https://models.example.com/v1',
        'http://[::1]:8080/v1',
    ])
    def test_valid_endpoints(self, endpoint):
        assert normalize_connection_result(valid(endpoint=endpoint))[
            'endpoint'] == endpoint

    @pytest.mark.parametrize('endpoint', [
        'ftp://host/v1', 'http://host/', 'http://host/v1/extra',
        'http://host:0/v1', 'http://host:65536/v1', 'http://user@host/v1',
        'http://host/v1?query=1', 'http://host/v1#frag', 'http://ho st/v1',
        'http://host\\path/v1', '', 'not-a-url', 'http:///v1',
    ])
    def test_invalid_endpoints(self, endpoint):
        # urlsplit raises its own ValueError ('Port out of range') for
        # :65536 before the validator sees it — match ValueError broadly.
        with pytest.raises(ValueError):
            normalize_connection_result(valid(endpoint=endpoint))


class TestMetadata:
    @pytest.mark.parametrize('mutation', [
        {'catalogId': 'other'},                  # != expected.catalogId
        {'routedModel': 'other.gguf'},           # != expected.runtimeModelId
        {'identitySource': 'unverified-route'},
        {'execution': 'server-owned'},
        {'routeSeq': -1}, {'routeSeq': '3'},
        {'contextLength': 4095}, {'contextLength': 10_000_001},
        {'contextLength': '32768'},
        {'maxOutputTokens': 255},                # below floor
        {'maxOutputTokens': 200000},             # above min(131072, ctx)
        {'maxOutputTokens': 40000},              # > contextLength=32768
    ])
    def test_metadata_invariants(self, mutation):
        with pytest.raises(ValueError, match='invalid-connection-result'):
            normalize_connection_result(with_meta(**mutation))

    def test_expires_must_match_envelope(self):
        doc = valid()
        doc['metadata']['expiresAt'] = doc['expiresAt'] + 1
        with pytest.raises(ValueError):
            normalize_connection_result(doc)

    def test_capabilities_all_bool_and_chat_required(self):
        assert normalize_connection_result(with_meta(
            capabilities={'chat': True, 'tools': False, 'vision': False,
                          'agentViable': False}))['metadata']['capabilities']
        for bad in ({'chat': False, 'tools': True, 'vision': False,
                     'agentViable': False},
                    {'chat': 1, 'tools': True, 'vision': False,
                     'agentViable': False},
                    {'chat': True, 'tools': True, 'vision': False},
                    {'chat': True, 'tools': True, 'vision': False,
                     'agentViable': False, 'extra': True}):
            with pytest.raises(ValueError):
                normalize_connection_result(with_meta(capabilities=bad))

    def test_metadata_extra_key_rejected(self):
        doc = with_meta(internalNote='secret')
        with pytest.raises(ValueError):
            normalize_connection_result(doc)


class TestIsolation:
    def test_result_is_deepcopy_not_shared(self):
        value = valid()
        result = normalize_connection_result(value)
        result['metadata']['capabilities']['tools'] = False
        result['expected']['catalogId'] = 'mutated'
        assert value['metadata']['capabilities']['tools'] is True
        assert value['expected']['catalogId'] == 'qwen3-4b'
