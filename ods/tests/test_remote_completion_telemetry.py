import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'bin'))
from remote_provider.telemetry import CompletionObservation, route_fingerprint
from remote_provider import telemetry


ROUTE = {'routeFingerprint': 'a' * 64, 'provider': {'model': 'remote-model'}}


@pytest.fixture
def telemetry_clock(monkeypatch):
    now = [1000.0]
    # Replace only the observer's clock, not Python's shared time module.
    monkeypatch.setattr(telemetry, 'time', SimpleNamespace(
        monotonic=lambda: now[0], time=lambda: 1700000000.0))
    return now


def test_usage_is_bounded_and_never_estimated_from_text():
    for data in [b'data: {"choices":[{"delta":{"content":"long reply"}}]}\n\ndata: [DONE]\n\n',
                 b'data: ' + b'x' * (64 * 1024 + 1) + b'\n\n',
                 b'data: {"usage":{"completion_tokens":true}}\n\ndata: [DONE]\n\n',
                 b'data: {"usage":{"completion_tokens":20}}\n\n']:
        sample = CompletionObservation(ROUTE)
        sample.feed(data)
        assert sample.result() is None
        assert len(sample.buffer) <= sample.LIMIT


def test_responses_completion_uses_actual_output_usage_and_error_invalidates_sample(telemetry_clock):
    sample = CompletionObservation(ROUTE)
    sample.feed(b'data: {"type":"response.completed","response":{"usage":{"output_tokens":12}}}\n\n')
    telemetry_clock[0] += 0.5
    result = sample.result()
    assert result['completionTokens'] == 12
    assert result['elapsedMs'] == pytest.approx(500.0)
    sample.feed(b'data: {"type":"error","error":{"message":"private"}}\n\n')
    assert sample.result() is None


def test_a_null_error_field_is_not_an_error(telemetry_clock):
    sample = CompletionObservation(ROUTE)
    sample.feed(b'data: {"error":null,"usage":{"completion_tokens":20}}\n\ndata: [DONE]\n\n')
    telemetry_clock[0] += 0.5
    assert sample.result()['completionTokens'] == 20
    failed = CompletionObservation(ROUTE)
    failed.feed(b'data: {"error":{"message":"x"},"usage":{"completion_tokens":20}}\n\ndata: [DONE]\n\n')
    assert failed.result() is None


@pytest.mark.parametrize('elapsed', [0.0, -0.001, 3600.001])
def test_completed_usage_with_invalid_elapsed_time_is_rejected(telemetry_clock, elapsed):
    sample = CompletionObservation(ROUTE)
    sample.feed(b'data: {"usage":{"completion_tokens":20}}\n\ndata: [DONE]\n\n')
    telemetry_clock[0] += elapsed
    assert sample.result() is None


def test_malformed_provider_frames_never_escape_the_telemetry_observer():
    sample = CompletionObservation(ROUTE)
    sample.feed(b'data: ' + b'[' * 1500 + b'0' + b']' * 1500 + b'\n\n')
    assert sample.result() is None
    other = CompletionObservation(ROUTE)
    other.payload({'type': [], 'usage': None})
    assert other.result() is None


def test_fingerprint_matches_host_route_identity_and_ignores_secrets():
    import ast
    path = Path(__file__).parents[1] / 'bin/ods-host-agent.py'
    tree = ast.parse(path.read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == '_remote_provider_route_fingerprint')
    import hashlib
    scope = {'json': json, 'hashlib': hashlib}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), scope)
    state = {'provider': {'transport': 'direct', 'baseUrl': 'https://provider.example/v1', 'model': 'model',
                          'contextLength': 131072, 'maxTokens': 8192, 'reasoning': False}}
    assert route_fingerprint(state) == scope['_remote_provider_route_fingerprint'](state)
    state['privateSecret'] = 'not-part-of-identity'
    assert route_fingerprint(state) == scope['_remote_provider_route_fingerprint'](state)
