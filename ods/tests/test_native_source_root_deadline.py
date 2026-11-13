"""Deterministic clock/scope controls; no Source jobs or physical Darwin claim."""
# ruff: noqa: E402
import contextvars
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import pixel_access_bridge as bridge

NS = 10**9
ERROR = 'native-source-deadline-unavailable'


@pytest.fixture
def clocks(monkeypatch):
    state = SimpleNamespace(mono=100.0, raw=1000 * NS, events=[], first_mono_delay=0)

    def monotonic():
        state.events.append('monotonic')
        value = state.mono
        if state.first_mono_delay:
            delay, state.first_mono_delay = state.first_mono_delay, 0
            state.mono += delay
            state.raw += delay * NS
        return value

    def raw(clock):
        assert clock == 999
        state.events.append('raw')
        return state.raw

    state.api = SimpleNamespace(monotonic=monotonic, clock_gettime_ns=raw, CLOCK_MONOTONIC_RAW=999)
    monkeypatch.setattr(bridge, 'time', state.api)
    monkeypatch.setattr(bridge, 'sys', SimpleNamespace(platform='darwin'))
    assert bridge._DEADLINE.get() is None
    assert bridge._NATIVE_SOURCE_DEADLINE.get() is None
    return state


def adapter():
    return object.__new__(bridge.LaunchdAccessBridge)


def deadline(value):
    return bridge.LaunchdAccessBridge._native_source_deadline_ns(value)


def refused(value):
    with pytest.raises(bridge.AccessError, match='^' + ERROR + '$'):
        deadline(value)


def test_real_bounded_scope_is_required_and_cache_is_not_a_deadline(clocks):
    value = adapter()
    refused(value)
    with value._native_runtime_operation():
        refused(value)
    with value.bounded(30):
        scope = bridge._NATIVE_SOURCE_DEADLINE.get()
        cache = bridge.LaunchdAccessBridge._native_runtime_lease(value)
        assert scope is not cache and cache.active
        assert deadline(value) == 1030 * NS
    assert not scope.active and not cache.active
    refused(value)
    clocks.raw += 2 * NS
    clocks.mono += 2
    with value.bounded(30):
        assert deadline(value) == 1032 * NS


def test_same_bridge_nested_deadline_tightens_then_restores_without_changing_cache(clocks):
    value = adapter()
    with value.bounded(60):
        outer = bridge._NATIVE_SOURCE_DEADLINE.get()
        cache = bridge.LaunchdAccessBridge._native_runtime_lease(value)
        clocks.mono += 10
        clocks.raw += 10 * NS
        with value.bounded(5):
            inner = bridge._NATIVE_SOURCE_DEADLINE.get()
            assert inner is not outer
            assert deadline(value) == 1015 * NS
            assert bridge._DEADLINE.get() == 115
            assert bridge.LaunchdAccessBridge._native_runtime_lease(value) is cache
        assert not inner.active and outer.active
        assert deadline(value) == 1060 * NS
        assert bridge._DEADLINE.get() == 160


def test_other_bridge_inherits_remaining_limit_and_cannot_borrow_owner_scope(clocks):
    first, second = adapter(), adapter()
    with first.bounded(20):
        original = deadline(first)
        clocks.mono += 18
        clocks.raw += 18 * NS
        with second.bounded(120):
            assert deadline(second) == original == 1020 * NS
            assert bridge.remaining(120) == 2
            refused(first)
        assert deadline(first) == original
        refused(second)


def test_raw_entry_sample_precedes_super_and_uses_current_remaining_not_old_duration(clocks):
    value = adapter()
    clocks.first_mono_delay = 3
    with value.bounded(30):
        assert clocks.events[0:2] == ['raw', 'monotonic']
        assert bridge._DEADLINE.get() == 130
        assert bridge.remaining(30) == 27
        # raw0 was sampled before the three-second delay. Adding the old 30s
        # to a later raw sample would instead extend this to 1033s.
        assert deadline(value) == 1027 * NS
        assert clocks.raw == 1003 * NS


def test_earlier_enclosing_systemd_monotonic_deadline_is_not_extended(clocks):
    value = adapter()
    systemd = object.__new__(bridge.SystemdAccessBridge)
    with systemd.bounded(5):
        clocks.mono += 4
        clocks.raw += 4 * NS
        with value.bounded(120):
            assert bridge.remaining(120) == 1
            assert deadline(value) == 1005 * NS
    with value.bounded(30):
        original = deadline(value)
        with systemd.bounded(1):
            refused(value)  # No matching raw twin for this changed Mono scope.
        assert deadline(value) == original


@pytest.mark.parametrize('change', ['expired', 'backward'])
def test_raw_expiry_or_rollback_refuses_without_role_budget_fallback(clocks, change):
    value = adapter()
    with value.bounded(10):
        clocks.raw = 1010 * NS if change == 'expired' else 999 * NS
        refused(value)


def test_existing_monotonic_deadline_and_late_return_error_remain_binding(clocks):
    value = adapter()
    with pytest.raises(bridge.AccessError, match='^operation-deadline-exceeded$'):
        with value.bounded(10):
            scope = bridge._NATIVE_SOURCE_DEADLINE.get()
            clocks.mono = 111
            refused(value)  # Raw remains live; Mono alone is enough to refuse.
    assert not scope.active and bridge._DEADLINE.get() is None
    assert bridge._NATIVE_SOURCE_DEADLINE.get() is None


@pytest.mark.parametrize('change', ['platform', 'constant', 'api', 'oserror', 'bool', 'overflow'])
def test_missing_raw_clock_keeps_non_source_mac_bounded_usable(clocks, monkeypatch, change):
    if change == 'platform':
        monkeypatch.setattr(bridge.sys, 'platform', 'linux')
    elif change == 'constant':
        del clocks.api.CLOCK_MONOTONIC_RAW
    elif change == 'api':
        del clocks.api.clock_gettime_ns
    elif change == 'oserror':
        def unavailable(_clock):
            raise OSError('simulated unavailable raw clock')
        clocks.api.clock_gettime_ns = unavailable
    else:
        clocks.raw = True if change == 'bool' else 2**63
    value = adapter()
    reached = []
    with value.bounded(10):
        reached.append('non-source body')
        assert bridge.remaining(30) == 10
        refused(value)
    assert reached == ['non-source body']


def test_clock_loss_after_entry_refuses_and_exception_revokes_both_scopes(clocks):
    value = adapter()
    with pytest.raises(RuntimeError, match='fixture failure'):
        with value.bounded(10):
            source_scope = bridge._NATIVE_SOURCE_DEADLINE.get()
            cache = bridge.LaunchdAccessBridge._native_runtime_lease(value)
            del clocks.api.clock_gettime_ns
            refused(value)
            raise RuntimeError('fixture failure')
    assert not source_scope.active and not cache.active and cache.custody._cache == {}
    assert bridge._NATIVE_SOURCE_DEADLINE.get() is None and bridge._DEADLINE.get() is None


def test_copied_context_is_revoked_after_exit_even_inside_another_bounded(clocks):
    value = adapter()
    with value.bounded(30):
        copied = contextvars.copy_context()
        assert copied.run(deadline, value) == 1030 * NS
    copied.run(refused, value)

    def nested():
        with value.bounded(5):
            refused(value)
    copied.run(nested)
    refused(value)


def test_live_copied_context_cannot_transfer_deadline_to_another_thread(clocks):
    value = adapter()
    with value.bounded(30):
        copied = contextvars.copy_context()
        results = []

        def check():
            refused(value)
            with value.bounded(5):
                refused(value)
            results.append('both refused')
        thread = threading.Thread(target=lambda: copied.run(check))
        thread.start()
        thread.join(timeout=2)
        assert not thread.is_alive() and results == ['both refused']
        assert deadline(value) == 1030 * NS


def test_copied_inner_scope_cannot_survive_revocation_of_its_enclosing_operation(clocks):
    value = adapter()
    with value.bounded(30):
        copied = contextvars.copy_context()
        inner = copied.run(value.bounded, 5)
        copied.run(inner.__enter__)
        assert copied.run(deadline, value) == 1005 * NS
    try:
        copied.run(refused, value)
    finally:
        copied.run(inner.__exit__, None, None, None)
    assert bridge._NATIVE_SOURCE_DEADLINE.get() is None


def test_plain_context_data_subclass_and_systemd_do_not_qualify(clocks):
    value = adapter()
    token = bridge._NATIVE_SOURCE_DEADLINE.set({'ceiling': 1030 * NS, 'bridge': value})
    try:
        refused(value)
        with value.bounded(5):
            refused(value)
    finally:
        bridge._NATIVE_SOURCE_DEADLINE.reset(token)

    class Subclass(bridge.LaunchdAccessBridge):
        pass
    for other in (object.__new__(Subclass), object.__new__(bridge.SystemdAccessBridge)):
        with other.bounded(5):
            refused(other)
            assert bridge._NATIVE_SOURCE_DEADLINE.get() is None


def test_invalid_seal_and_infinite_budget_refuse_fixed_source_error(clocks):
    value = adapter()
    with value.bounded(5):
        scope = bridge._NATIVE_SOURCE_DEADLINE.get()
        scope.seal = object()
        refused(value)
    with value.bounded(float('inf')):
        refused(value)
