"""Read only measurements belonging to the host-confirmed active cloud route."""
import asyncio
import json
import math
import time

import httpx


def project_completion(value, runtime, now=None):
    if not isinstance(value, dict) or set(value) != {'sample'}:
        return {}
    sample = value['sample']
    if not isinstance(sample, dict) or set(sample) != {
            'schemaVersion', 'routeFingerprint', 'model', 'completionTokens', 'elapsedMs', 'sampledAt'}:
        return {}
    if (type(sample['schemaVersion']) is not int or sample['schemaVersion'] != 1 or not runtime.get('routeFingerprint')
            or sample['routeFingerprint'] != runtime['routeFingerprint'] or sample['model'] != runtime['model']):
        return {}
    tokens, duration, stamp = sample['completionTokens'], sample['elapsedMs'], sample['sampledAt']
    if type(tokens) is not int or not 0 < tokens <= 100_000_000:
        return {}
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in (duration, stamp)) or not 0 < duration <= 3_600_000:
        return {}
    age = (time.time() * 1000 if now is None else now) - stamp
    if not 0 <= age <= 300_000:
        return {}
    rate = tokens * 1000 / duration
    if not math.isfinite(rate):
        return {}
    return {'tokens_per_second': round(rate, 2), 'throughput_mode': 'cloud_request_average',
            'throughput_state': 'retained', 'throughput_sampled_at': stamp,
            'throughput_model': sample['model']}


async def get_cloud_throughput(runtime, base_url):
    if runtime.get('source') != 'remote-provider' or not runtime.get('routeFingerprint'):
        return {}
    try:
        async with asyncio.timeout(1):
            async with httpx.AsyncClient(timeout=1, follow_redirects=False, trust_env=False) as client:
                async with client.stream('GET', base_url.rstrip('/') + '/telemetry') as response:
                    if response.status_code != 200:
                        return {}
                    body = b''
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > 4096:
                            return {}
                        body += chunk
            return project_completion(json.loads(body), runtime)
    except (httpx.HTTPError, TimeoutError, ValueError):
        return {}
