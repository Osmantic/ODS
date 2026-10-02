"""A host selection receipt must agree with the API's shared-mount view."""
import os
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from routers import extensions


@pytest.fixture
def roots(tmp_path, monkeypatch):
    user, builtin = tmp_path / "user", tmp_path / "builtin"
    user.mkdir()
    builtin.mkdir()
    monkeypatch.setattr(extensions, "USER_EXTENSIONS_DIR", user)
    monkeypatch.setattr(extensions, "EXTENSIONS_DIR", builtin)
    monkeypatch.setattr(extensions, "request_agent_json",
                        lambda *a, **k: pytest.fail("unexpected live transport"))
    return user, builtin


@pytest.fixture
def clock(monkeypatch):
    value = SimpleNamespace(now=0.0, sleeps=[], after_sleep=lambda: None)
    def sleep(seconds):
        value.sleeps.append(seconds)
        value.now += seconds
        value.after_sleep()
    monkeypatch.setattr(extensions.time, "monotonic", lambda: value.now)
    monkeypatch.setattr(extensions.time, "sleep", sleep)
    return value


def markers(root, name, action):
    directory = root / name
    directory.mkdir()
    selected = directory / ("compose.yaml" if action == "enable" else "compose.yaml.disabled")
    opposite = directory / ("compose.yaml.disabled" if action == "enable" else "compose.yaml")
    selected.write_text("services: {}\n")
    return selected, opposite


def transport(monkeypatch, action, ids, result_action=None):
    calls = []
    result = {"status": "ok", "action": result_action or
              ("enabled" if action == "enable" else "disabled"), "service_ids": ids}
    def request(method, path, *, payload, timeout):
        assert (method, path) == ("POST", "/v1/extension/select")
        calls.append(payload)
        return result
    monkeypatch.setattr(extensions, "request_agent_json", request)
    return calls, result


@pytest.mark.parametrize("index", [0, 1])
@pytest.mark.parametrize("action,result_action", [("enable", "enabled"),
                         ("enable", "already_enabled"), ("disable", "disabled")])
def test_visible_selection_has_no_sleep(roots, clock, monkeypatch, index, action, result_action):
    markers(roots[index], "cyberchef", action)
    calls, result = transport(monkeypatch, action, ["cyberchef"], result_action)
    assert extensions._select_extensions_on_host(action, ["cyberchef"]) == result
    assert len(calls) == 1
    assert clock.sleeps == []


@pytest.mark.parametrize("action", ["enable", "disable"])
@pytest.mark.parametrize("invalid", ["both", "symlink", "opposite_symlink", "directory", "missing"])
def test_invalid_marker_pair_is_not_visible(roots, action, invalid):
    selected, opposite = markers(roots[0], "cyberchef", action)
    if invalid == "both":
        os.link(selected, opposite)
        assert selected.stat().st_ino == opposite.stat().st_ino
    elif invalid == "opposite_symlink":
        opposite.symlink_to(selected.parent / "missing")
    else:
        selected.unlink()
        if invalid == "symlink":
            selected.symlink_to(selected.parent / "missing")
        elif invalid == "directory":
            selected.mkdir()
    assert not extensions._selection_markers_visible(action, ["cyberchef"])


def test_real_resolver_rejects_escape(roots):
    outside = roots[0].parent / "outside"
    outside.mkdir()
    markers(outside, "cyberchef", "enable")
    (roots[0] / "cyberchef").symlink_to(outside / "cyberchef")
    assert not extensions._selection_markers_visible("enable", ["cyberchef"])


def test_host_receipt_waits_for_disable_visibility(roots, clock, monkeypatch):
    selected, opposite = markers(roots[0], "cyberchef", "disable")
    os.link(selected, opposite)
    def settle():
        if clock.now == 6.0:
            opposite.unlink()
    clock.after_sleep = settle
    calls, result = transport(monkeypatch, "disable", ["cyberchef"])
    assert extensions._select_extensions_on_host("disable", ["cyberchef"]) == result
    assert not opposite.exists()
    assert clock.now == 6.0
    assert len(calls) == 1


def test_all_dependencies_must_be_visible_together(roots, clock, monkeypatch):
    first, first_off = markers(roots[0], "first", "enable")
    second, second_off = markers(roots[1], "second", "enable")
    second.rename(second_off)
    def settle():
        if clock.now == 0.25:
            first.rename(first_off)
            second_off.rename(second)
        elif clock.now == 0.5:
            first_off.rename(first)
    clock.after_sleep = settle
    calls, result = transport(monkeypatch, "enable", ["first", "second"])
    assert extensions._select_extensions_on_host("enable", ["first", "second"]) == result
    assert clock.now == 0.5
    assert first.exists() and second.exists()
    assert len(calls) == 1


def test_timeout_reports_commit_without_replay_or_file_writes(roots, clock, monkeypatch):
    selected, opposite = markers(roots[0], "cyberchef", "disable")
    os.link(selected, opposite)
    before = {p.name: p.read_bytes() for p in selected.parent.iterdir()}
    calls, _ = transport(monkeypatch, "disable", ["cyberchef"])
    with pytest.raises(HTTPException) as exc:
        extensions._select_extensions_on_host("disable", ["cyberchef"])
    assert exc.value.status_code == 503
    assert "was committed" in exc.value.detail
    assert "Refresh status before retrying" in exc.value.detail
    assert clock.now == 10.0
    assert all(0 < delay <= 0.25 for delay in clock.sleeps)
    assert len(calls) == 1
    assert before == {p.name: p.read_bytes() for p in selected.parent.iterdir()}


@pytest.mark.parametrize("bad_result", [
    {"action": "disabled", "service_ids": ["different"]},
    {"action": "enabled", "service_ids": ["cyberchef"]},
])
def test_invalid_host_receipt_does_not_observe_or_replay(roots, monkeypatch, bad_result):
    calls, result = transport(monkeypatch, "disable", ["cyberchef"])
    result.update(bad_result)
    monkeypatch.setattr(extensions, "_selection_markers_visible",
                        lambda *a: pytest.fail("unverified host receipt observed"))
    with pytest.raises(HTTPException) as exc:
        extensions._select_extensions_on_host("disable", ["cyberchef"])
    assert exc.value.status_code == 502
    assert len(calls) == 1
