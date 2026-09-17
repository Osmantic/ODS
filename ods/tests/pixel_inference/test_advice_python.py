"""Contract tests for pixel_provider.advice_python interpreter selection.

`advice_setup.py` uses these helpers to pick the interpreter that prepares the
optional advisory venv. An HTTP client never supplies executable paths — the
host scans fixed prefixes, probes each candidate in an isolated mode, verifies
the binary digest twice (TOCTOU), and only ever selects an interpreter that can
actually build a venv (venv + ensurepip).
"""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'bin'))
from pixel_provider import advice_python
from pixel_provider.advice_python import candidates, select_candidate
from pixel_provider.store import StoreError

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='POSIX interpreter scan')


def probe(version=(3, 12, 0), venv=True, ensurepip=True, returncode=0, raw=None):
    payload = json.dumps(dict(version=list(version), venv=venv, ensurepip=ensurepip)).encode()
    return SimpleNamespace(returncode=returncode, stdout=payload if raw is None else raw, stderr=b'')


def test_candidates_have_stable_identity_and_capability_shape():
    found = candidates()
    seen = {item['id'] for item in found}
    assert len(seen) == len(found)
    for item in found:
        assert set(item) == {'id', 'path', 'version', 'canPrepare'}
        assert len(item['id']) == 64 and all(c in '0123456789abcdef' for c in item['id'])
        assert os.path.isabs(item['path']) and Path(item['path']).is_file()
        assert [int(p) for p in item['version'].split('.')][:2] >= [3, 11]
        assert type(item['canPrepare']) is bool
    again = {item['path']: item['id'] for item in candidates()}
    assert all(again[item['path']] == item['id'] for item in found)


def test_select_candidate_round_trips_a_real_interpreter():
    ready = [item for item in candidates() if item['canPrepare']]
    if not ready:
        pytest.skip('no venv-capable interpreter on this host')
    chosen = select_candidate(ready[0]['id'])
    assert chosen == ready[0]


def test_select_candidate_rejects_unknown_or_unprepared_identity(monkeypatch):
    with pytest.raises(StoreError, match='advice-python-unavailable'):
        select_candidate('0' * 64)
    monkeypatch.setattr(advice_python.subprocess, 'run',
                        lambda *a, **k: probe(venv=False))
    for item in candidates():
        with pytest.raises(StoreError, match='advice-python-unavailable'):
            select_candidate(item['id'])


def test_non_posix_never_proposes_interpreters(monkeypatch):
    monkeypatch.setattr(advice_python.os, 'name', 'nt')
    assert candidates() == []


def test_candidates_preserve_scan_order(monkeypatch):
    calls = []
    real_run = advice_python.subprocess.run

    def spy(args, **kwargs):
        calls.append(args[0])
        return real_run(args, **kwargs)

    monkeypatch.setattr(advice_python.subprocess, 'run', spy)
    found = candidates()
    # Returned candidates keep the order their binaries were probed in —
    # the running interpreter is scanned first, then the fixed prefixes.
    paths = [item['path'] for item in found]
    assert paths == [path for path in calls if path in paths]
    resolved = str(Path(sys.executable).resolve())
    if resolved in calls:
        assert calls[0] == resolved


def test_duplicate_spellings_resolve_to_one_candidate():
    existing = next((p for p in ('/usr/bin/python3.14', '/usr/bin/python3.13',
                                 '/usr/bin/python3.12', '/usr/bin/python3.11',
                                 '/usr/local/bin/python3.12')
                     if Path(p).is_file()), None)
    if existing is None:
        pytest.skip('no prefixed interpreter to alias')
    original = sys.executable
    try:
        sys.executable = existing
        found = candidates()
    finally:
        sys.executable = original
    assert sum(1 for item in found if item['path'] == str(Path(existing).resolve())) == 1


@pytest.mark.parametrize('result', [
    SimpleNamespace(returncode=1, stdout=probe().stdout, stderr=b''),
    SimpleNamespace(returncode=0, stdout=b'not json', stderr=b''),
    SimpleNamespace(returncode=0, stdout=b' ' * 4097, stderr=b''),
    probe(version=(3, 10, 9)),
    probe(version=('3', '12', 'x')),
    probe(version=(3, 12)),
    probe(venv='yes'),
    probe(ensurepip=1),
])
def test_rejects_unhealthy_or_underqualified_probes(monkeypatch, result):
    monkeypatch.setattr(advice_python.subprocess, 'run', lambda *a, **k: result)
    assert candidates() == []


def test_probe_timeout_and_os_errors_skip_candidate(monkeypatch):
    def timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd='probe', timeout=3)
    monkeypatch.setattr(advice_python.subprocess, 'run', timeout)
    assert candidates() == []


def test_digest_drift_between_probes_drops_candidate(monkeypatch):
    counter = iter(range(1000))
    monkeypatch.setattr(advice_python, 'digest_file',
                        lambda path: format(next(counter), '064x'))
    monkeypatch.setattr(advice_python.subprocess, 'run', lambda *a, **k: probe())
    assert candidates() == []


def test_probe_runs_isolated_without_ambient_environment(monkeypatch):
    captured = []
    def record(command, **kwargs):
        captured.append((command, kwargs))
        return probe()
    monkeypatch.setattr(advice_python.subprocess, 'run', record)
    candidates()
    assert captured, 'at least one interpreter should be probed'
    for command, kwargs in captured:
        assert command[1:4] == ['-I', '-S', '-B']
        assert kwargs['env'] == {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8',
                                 'PYTHONDONTWRITEBYTECODE': '1'}
        assert kwargs['cwd'] == '/' and kwargs['timeout'] == 3
        assert kwargs['stdin'] is subprocess.DEVNULL and kwargs['capture_output'] is True
