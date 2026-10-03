"""Deterministic coverage for the observed CI exit-during-procfs-read race."""
import errno
from pathlib import Path

import pytest

from test_advice_setup_process import live
from test_route_worker import process_live

pytestmark = pytest.mark.skipif(__import__("sys").platform != "linux", reason="Linux procfs")


@pytest.mark.parametrize("probe", [live, process_live])
@pytest.mark.parametrize("error", [
    FileNotFoundError(errno.ENOENT, "No such file"),
    ProcessLookupError(errno.ESRCH, "No such process"),
])
def test_process_disappearing_during_stat_read_is_not_live(monkeypatch, probe, error):
    def disappeared(path, *args, **kwargs):
        assert path == Path("/proc/123456/stat")
        raise error
    monkeypatch.setattr(Path, "read_text", disappeared)
    assert probe(123456) is False


@pytest.mark.parametrize("probe", [live, process_live])
def test_unexpected_procfs_access_error_still_fails_the_test(monkeypatch, probe):
    def denied(*args, **kwargs):
        raise PermissionError(errno.EACCES, "Permission denied")
    monkeypatch.setattr(Path, "read_text", denied)
    with pytest.raises(PermissionError):
        probe(123456)


@pytest.mark.parametrize("probe", [live, process_live])
@pytest.mark.parametrize("state,expected", [("S", True), ("R", True), ("Z", False)])
def test_live_and_zombie_classification_is_unchanged(monkeypatch, probe, state, expected):
    monkeypatch.setattr(Path, "read_text", lambda *_: f"123456 (python) {state} 1 2 3")
    assert probe(123456) is expected
