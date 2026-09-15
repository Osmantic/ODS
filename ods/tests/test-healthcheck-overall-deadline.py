import importlib.util
from pathlib import Path
import sys


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/healthcheck.py'
SPEC = importlib.util.spec_from_file_location('healthcheck', SCRIPT)
healthcheck = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = healthcheck
SPEC.loader.exec_module(healthcheck)


def test_retries_share_one_overall_timeout(monkeypatch):
    now = iter([0.0, 0.0, 0.4, 0.4, 1.1])
    calls = []
    monkeypatch.setattr(healthcheck.time, 'monotonic', lambda: next(now))
    monkeypatch.setattr(healthcheck.time, 'sleep', lambda _seconds: None)

    result = healthcheck.with_retries(
        lambda remaining: calls.append(remaining) or (False, 'unavailable', None),
        retries=50,
        timeout=1.0,
    )

    assert result == (False, 'unavailable', None)
    assert len(calls) == 2
    assert all(0 < remaining <= 1.0 for remaining in calls)
