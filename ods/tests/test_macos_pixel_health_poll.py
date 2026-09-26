import importlib.util
import json
from pathlib import Path
import subprocess
import time
import pytest

PATH = Path(__file__).resolve().parents[1] / 'installers/macos/lib/pixel-native-compose.py'
spec = importlib.util.spec_from_file_location('pixel_native_compose', PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class Clock:
    def __init__(self):
        self.now = 0.0
    def monotonic(self):
        return self.now
    def sleep(self, s):
        assert 0 <= s <= max(0, 90 - self.now)
        self.now += s


def make_run(clock, script):
    calls = []
    def run(*args, **kwargs):
        calls.append((args, kwargs, clock.now))
        timeout = kwargs.get('timeout', 0)
        idx = len(calls) - 1
        action = script[min(idx, len(script) - 1)]
        kind, payload = action
        if kind == 'timeout':
            clock.now += timeout
            raise subprocess.TimeoutExpired(' '.join(args), timeout)
        clock.now += payload.get('cost', timeout)
        return _Result(payload['returncode'], payload['stdout'])
    run.calls = calls
    return run


class _Result:
    def __init__(self, returncode, stdout):
        self.returncode = returncode
        self.stdout = stdout


def healthy_rows(services=mod.SERVICES):
    return json.dumps([{'Service': s, 'State': 'running', 'Health': 'healthy'} for s in services])


def test_transient_timeout_then_healthy(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(time, 'monotonic', clock.monotonic)
    monkeypatch.setattr(time, 'sleep', clock.sleep)
    run = make_run(clock, [('timeout', None), ('ok', {'returncode': 0, 'stdout': healthy_rows(), 'cost': 2})])
    assert mod.wait_ready(run) == {'phase': 'docker-ready'}
    assert clock.now < 90


def test_persistent_timeout_stops_at_90(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(time, 'monotonic', clock.monotonic)
    monkeypatch.setattr(time, 'sleep', clock.sleep)
    run = make_run(clock, [('timeout', None)])
    with pytest.raises(ValueError, match='native-compose-health-timeout'):
        mod.wait_ready(run)
    assert clock.now == 90
    for args, kwargs, started in run.calls:
        assert kwargs['timeout'] <= 10
        assert started < 90
        assert kwargs['timeout'] <= 90 - started


def test_late_healthy_response_rejected(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(time, 'monotonic', clock.monotonic)
    monkeypatch.setattr(time, 'sleep', clock.sleep)
    run = make_run(clock, [('ok', {'returncode': 0, 'stdout': healthy_rows(), 'cost': 90.25})])
    with pytest.raises(ValueError, match='native-compose-health-timeout'):
        mod.wait_ready(run)


def test_valid_ndjson(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(time, 'monotonic', clock.monotonic)
    monkeypatch.setattr(time, 'sleep', clock.sleep)
    ndjson = '\n'.join(json.dumps({'Service': s, 'State': 'running', 'Health': 'healthy'}) for s in mod.SERVICES)
    run = make_run(clock, [('ok', {'returncode': 0, 'stdout': ndjson, 'cost': 1})])
    assert mod.wait_ready(run) == {'phase': 'docker-ready'}


@pytest.mark.parametrize('stdout', [
    json.dumps([{'Service': s, 'State': 'running', 'Health': 'unhealthy'} for s in mod.SERVICES]),
    json.dumps([{'Service': s, 'State': 'running', 'Health': 'healthy'} for s in mod.SERVICES[:2]]),
    json.dumps([{'Service': s, 'State': 'running', 'Health': 'healthy'} for s in mod.SERVICES] +
               [{'Service': mod.SERVICES[0], 'State': 'running', 'Health': 'healthy'}]),
    'x' * 65537,
    'not json',
])
def test_rejected_outputs(monkeypatch, stdout):
    clock = Clock()
    monkeypatch.setattr(time, 'monotonic', clock.monotonic)
    monkeypatch.setattr(time, 'sleep', clock.sleep)
    run = make_run(clock, [('ok', {'returncode': 0, 'stdout': stdout, 'cost': 1})])
    with pytest.raises(ValueError, match='native-compose-health-timeout'):
        mod.wait_ready(run)


def test_nonzero_returncode_rejected(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(time, 'monotonic', clock.monotonic)
    monkeypatch.setattr(time, 'sleep', clock.sleep)
    run = make_run(clock, [('ok', {'returncode': 1, 'stdout': healthy_rows(), 'cost': 1})])
    with pytest.raises(ValueError, match='native-compose-health-timeout'):
        mod.wait_ready(run)