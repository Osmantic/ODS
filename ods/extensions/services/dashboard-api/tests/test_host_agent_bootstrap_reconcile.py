"""The boot hook rechecks an interrupted bootstrap swap it had to defer.

reconcile-bootstrap-swap.sh exits 75 while the swap status is younger than its
freshness window or another lifecycle operation holds the lock. A reboot that
finishes soon after the last status update must still be recovered without
another agent restart or a manual `ods start`.
"""

import subprocess
from types import SimpleNamespace

import pytest

from test_host_agent import _mod


@pytest.fixture
def agent(tmp_path, monkeypatch):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "reconcile-bootstrap-swap.sh").write_text("", encoding="utf-8")
    monkeypatch.setattr(_mod, "INSTALL_DIR", tmp_path)
    sleeps: list[float] = []
    monkeypatch.setattr(_mod.time, "sleep", sleeps.append)
    return sleeps


def _script_codes(monkeypatch, codes):
    calls = []

    def run(argv, **_kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=codes[len(calls) - 1], stdout="", stderr="")

    monkeypatch.setattr(_mod.subprocess, "run", run)
    return calls


def test_fresh_status_then_lock_busy_is_rechecked_until_reconciled(agent, monkeypatch):
    calls = _script_codes(monkeypatch, [75, 75, 0])

    _mod._reconcile_interrupted_bootstrap_swap()

    assert len(calls) == 3
    assert agent == [_mod._BOOTSTRAP_RECONCILE_RECHECK_SECONDS] * 2


def test_rechecks_are_bounded_and_outlast_the_freshness_window(agent, monkeypatch):
    calls = _script_codes(monkeypatch, [75] * 10)

    _mod._reconcile_interrupted_bootstrap_swap()

    assert len(calls) == _mod._BOOTSTRAP_RECONCILE_MAX_RECHECKS + 1
    assert sum(agent) > 900


@pytest.mark.parametrize("code", [0, 1])
def test_settled_or_failed_runs_are_not_retried(agent, monkeypatch, code):
    calls = _script_codes(monkeypatch, [code, 0])

    _mod._reconcile_interrupted_bootstrap_swap()

    assert len(calls) == 1 and agent == []


def test_script_timeout_is_not_swallowed(agent, monkeypatch):
    def run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(_mod.subprocess, "run", run)
    with pytest.raises(subprocess.TimeoutExpired):
        _mod._reconcile_interrupted_bootstrap_swap()
