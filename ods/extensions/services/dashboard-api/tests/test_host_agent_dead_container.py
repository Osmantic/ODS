"""Re-enabling an extension removes only a dead container before Compose up."""

import subprocess
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from test_host_agent import _mod


@pytest.fixture
def selected(tmp_path, monkeypatch):
    ext_dir = tmp_path / "my-ext"
    ext_dir.mkdir()
    (ext_dir / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    selector = SimpleNamespace(
        _selection_lock=lambda *_args: nullcontext(), SelectionError=RuntimeError,
    )
    monkeypatch.setattr(_mod, "INSTALL_DIR", tmp_path)
    monkeypatch.setattr(_mod, "_load_extension_selector", lambda: selector)
    monkeypatch.setattr(_mod, "_find_ext_dir", lambda _sid: ext_dir)


def _fake_docker(monkeypatch, state, rm_code=0):
    calls = []

    def run(cmd, **_kwargs):
        calls.append(cmd)
        if cmd[-2:] == ["-aq", "my-ext"]:
            return subprocess.CompletedProcess(cmd, 0, "abc123\n", "")
        if cmd[:2] == ["docker", "inspect"]:
            return subprocess.CompletedProcess(cmd, 0, f"{state}\n", "")
        if cmd[:2] == ["docker", "rm"]:
            return subprocess.CompletedProcess(cmd, rm_code, "", "rm refused")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(_mod.subprocess, "run", run)
    return calls


@pytest.mark.parametrize("state", ["dead", "removing"])
def test_dead_container_is_removed_before_compose_up(selected, monkeypatch, state):
    calls = _fake_docker(monkeypatch, state)

    result = _mod._run_selected_extension_up("my-ext", ["-f", "base.yml"])

    assert result.returncode == 0
    assert calls.index(["docker", "rm", "-f", "abc123"]) < calls.index(
        ["docker", "compose", "-f", "base.yml", "up", "-d", "my-ext"])


@pytest.mark.parametrize("state", ["running", "exited", "created"])
def test_live_or_stopped_container_is_kept(selected, monkeypatch, state):
    calls = _fake_docker(monkeypatch, state)

    _mod._run_selected_extension_up("my-ext", ["-f", "base.yml"])

    assert not any(cmd[:2] == ["docker", "rm"] for cmd in calls)


def test_failed_removal_stops_before_compose_up(selected, monkeypatch):
    calls = _fake_docker(monkeypatch, "dead", rm_code=1)

    result = _mod._run_selected_extension_up("my-ext", ["-f", "base.yml"])

    assert result.returncode == 1
    assert result.stderr == "rm refused"
    assert not any("up" in cmd for cmd in calls)
