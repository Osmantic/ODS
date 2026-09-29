import pytest
import httpx
import time

from cloud_telemetry import get_cloud_throughput, project_completion


RUNTIME = {'source': 'remote-provider', 'model': 'model', 'routeFingerprint': 'a' * 64}
SAMPLE = {'schemaVersion': 1, 'model': 'model', 'routeFingerprint': 'a' * 64,
          'completionTokens': 100, 'elapsedMs': 2000, 'sampledAt': 1000000}


def test_completion_rate_is_measured_not_local_and_expires():
    value = project_completion({'sample': SAMPLE}, RUNTIME, now=1000001)
    assert value['tokens_per_second'] == 50
    assert value['throughput_mode'] == 'cloud_request_average'
    assert value['throughput_state'] == 'retained'
    # Dashboard's public inference timestamps use Unix seconds, including local metrics.
    assert value['throughput_sampled_at'] == SAMPLE['sampledAt'] / 1000
    assert project_completion({'sample': SAMPLE}, RUNTIME, now=1300001) == {}
    assert project_completion({'sample': SAMPLE}, RUNTIME, now=999999) == {}


@pytest.mark.parametrize('changes', [
    {'model': 'other'}, {'routeFingerprint': 'b' * 64}, {'elapsedMs': 0},
    {'elapsedMs': float('nan')}, {'completionTokens': True}, {'completionTokens': -1},
    {'sampledAt': 'yesterday'}, {'extra': 'private'}, {'schemaVersion': True},
])
def test_rejects_stale_identity_or_malformed_measurements(changes):
    assert project_completion({'sample': {**SAMPLE, **changes}}, RUNTIME, now=1000001) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['measured', 'oversized', 'redirect', 'offline'])
async def test_fetch_is_bounded_and_remote_failures_leave_status_available(case):
    def handler(request):
        assert str(request.url) == 'http://egress.internal:8091/telemetry'
        if case == 'offline':
            raise httpx.ConnectError('offline')
        if case == 'oversized':
            return httpx.Response(200, content=b'x' * 4097)
        if case == 'redirect':
            return httpx.Response(302, headers={'location': 'http://other.internal/'})
        return httpx.Response(200, json={'sample': {**SAMPLE, 'sampledAt': time.time() * 1000}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        result = await get_cloud_throughput(RUNTIME, 'http://egress.internal:8091', client)
    assert result.get('tokens_per_second') == (50 if case == 'measured' else None)


@pytest.mark.asyncio
async def test_status_polls_reuse_client_without_closing_it():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={'sample': {**SAMPLE, 'sampledAt': time.time() * 1000}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        for _ in range(2):
            result = await get_cloud_throughput(RUNTIME, 'http://egress.internal:8091', client)
            assert result['tokens_per_second'] == 50
            assert not client.is_closed
    assert calls == ['http://egress.internal:8091/telemetry'] * 2
