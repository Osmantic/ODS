#!/usr/bin/env python3
"""Opt-in live test: metrics must not keep native inference awake.

Uses an already-running loopback server. It neither installs nor restarts it.
Run with no other inference clients; --wake sends one short test completion.
"""
import argparse
import json
import math
import time
import urllib.parse
import urllib.request


def metrics_values(text):
    values = {}
    for line in text.splitlines():
        if not line or line.startswith('#'):
            continue
        parts = line.split()
        if len(parts) == 2 and parts[0].startswith('llamacpp:'):
            number = float(parts[1])
            if not math.isfinite(number) or number < 0:
                raise ValueError('Invalid inference metric')
            values[parts[0].removeprefix('llamacpp:')] = number
    for name in ('tokens_predicted_total', 'requests_processing', 'requests_deferred'):
        if name not in values:
            raise ValueError('Missing inference metric: ' + name)
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--idle-seconds', required=True, type=int)
    parser.add_argument('--wake', action='store_true')
    args = parser.parse_args()
    url = urllib.parse.urlsplit(args.url)
    if (url.scheme != 'http' or url.hostname not in ('127.0.0.1', 'localhost', '::1')
            or url.username or url.password or url.path not in ('', '/')
            or url.query or url.fragment or not 1 <= args.idle_seconds <= 86400):
        parser.error('Provide a loopback HTTP origin and a positive idle interval')
    base = args.url.rstrip('/')
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(path, payload=None, timeout=10):
        req = urllib.request.Request(base + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={'Content-Type': 'application/json'})
        with client.open(req, timeout=timeout) as response:
            if response.status != 200:
                raise RuntimeError('Unexpected HTTP status')
            return response.read().decode()

    def observe():
        props = json.loads(request('/props'))
        if type(props.get('is_sleeping')) is not bool:
            raise ValueError('Runtime does not expose its sleeping state')
        sample = metrics_values(request('/metrics'))
        if sample['requests_processing'] or sample['requests_deferred']:
            raise RuntimeError('Another inference is active; test did not interrupt it')
        return props['is_sleeping'], sample

    sleeping, first = observe()
    if sleeping:
        raise RuntimeError('Start with an awake, idle model to prove the sleep transition')
    started = time.monotonic()
    deadline = started + args.idle_seconds + 45
    samples = 1
    while time.monotonic() < deadline:
        time.sleep(min(2, args.idle_seconds / 2))
        sleeping, sample = observe()
        samples += 1
        if sample['tokens_predicted_total'] != first['tokens_predicted_total']:
            raise RuntimeError('Inference counters changed during the idle-only test')
        if sleeping:
            break
    else:
        raise RuntimeError('Metrics polling prevented sleep or the runtime did not sleep')
    asleep_at = round(time.monotonic() - started, 2)
    for _ in range(5):
        time.sleep(2)
        sleeping, sample = observe()
        samples += 1
        if not sleeping or sample['tokens_predicted_total'] != first['tokens_predicted_total']:
            raise RuntimeError('Monitoring woke the model or lost its counters')
    report = {'sleepObservedAfterSeconds': asleep_at, 'monitoringSamples': samples,
              'metricsDidNotWake': True, 'counterRetained': first['tokens_predicted_total']}
    print(json.dumps(report), flush=True)
    if args.wake:
        wake_started = time.monotonic()
        completion = json.loads(request('/v1/chat/completions', {
            'model': 'default', 'messages': [{'role': 'user', 'content': 'Responda apenas OK.'}],
            'max_tokens': 16, 'temperature': 0, 'stream': False,
            'chat_template_kwargs': {'enable_thinking': False}}, timeout=180))
        reply = completion['choices'][0]['message'].get('content')
        if not isinstance(reply, str) or not reply.strip():
            raise RuntimeError('No text returned after waking')
        sleeping, sample = observe()
        if sleeping or sample['tokens_predicted_total'] <= first['tokens_predicted_total']:
            raise RuntimeError('Wake failed or cumulative counters reset')
        print(json.dumps({'wakeSeconds': round(time.monotonic() - wake_started, 2),
                          'reply': reply, 'counterAfterWake': sample['tokens_predicted_total']}), flush=True)


if __name__ == '__main__':
    main()
