"""Public publisher and real qualification; explicit Linux Darwin metadata simulation.

No Source/provider/SDK/model jobs are exercised.
The existing cold _material.verify remains real and is measured separately.
"""
# ruff: noqa: E402
import copy
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

if sys.platform != 'linux' or not Path('/proc/self/fd').is_dir():
    pytest.skip('Linux/procfs custody fixture, not physical Darwin', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_source_active_publication import active, journal, gate  # noqa: F401
from test_native_source_generation import generation, SPECS  # noqa: F401
from test_native_source_custody import native  # noqa: F401
from test_connection_deployment_paths import deployment  # noqa: F401
from test_native_source_runtime_publication import built, source, package  # noqa: F401
from test_native_source_publication import material, publication, helper  # noqa: F401
from pixel_connections import custody
from pixel_provider import runtime_custody
import pixel_access_bridge as bridge


@pytest.fixture
def observed(active, monkeypatch):  # noqa: F811
    n = active
    target = n.f.root / 'runtime/openclaw.mjs'
    trace = SimpleNamespace(frames=[], leases=[], current=None, outsideBytes=0, aclChecks=0)
    actual_inspect, actual_fdopen = runtime_custody.RuntimeCustody.inspect, os.fdopen
    actual_stat, actual_acl = os.stat, custody._native_no_acl

    def inspect(qualifier):
        lease = bridge.LaunchdAccessBridge._native_runtime_lease(n.f.bridge)
        assert lease is not None and lease.active and lease.custody is qualifier
        assert qualifier.bridge is n.f.bridge
        frame = {'qualifierId':id(qualifier),'runtimeBytes':0,'targetMetadataChecks':0,
                 'cacheEntriesBefore':len(qualifier._cache)}
        trace.frames.append(frame)
        trace.leases.append(lease)
        trace.current = frame
        try:
            return actual_inspect(qualifier)
        finally:
            trace.current = None

    class ReadTrace:
        def __init__(self, stream, name):
            self.stream, self.name = stream, name

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def __getattr__(self, key):
            return getattr(self.stream, key)

        def read(self, *args):
            body = self.stream.read(*args)
            if self.name == target:
                if trace.current is not None:
                    trace.current['runtimeBytes'] += len(body)
                else:
                    trace.outsideBytes += len(body)
            return body

    def fdopen(fd, *args, **kwargs):
        return ReadTrace(actual_fdopen(fd, *args, **kwargs), Path(os.readlink('/proc/self/fd/' + str(fd))))

    def stat(path, *args, **kwargs):
        if (trace.current is not None and kwargs.get('follow_symlinks') is False
                and not isinstance(path, int) and Path(path) == target):
            trace.current['targetMetadataChecks'] += 1
        return actual_stat(path, *args, **kwargs)

    def acl(fd):
        trace.aclChecks += 1
        return actual_acl(fd)

    monkeypatch.setattr(runtime_custody.RuntimeCustody, 'inspect', inspect)
    monkeypatch.setattr(os, 'fdopen', fdopen)
    monkeypatch.setattr(os, 'stat', stat)
    monkeypatch.setattr(custody, '_native_no_acl', acl)
    n.operation_trace = trace
    return n


def assert_shared_operation(n, frames, leases):
    assert len(frames) > 1
    assert all(item is leases[0] for item in leases)
    assert len({item['qualifierId'] for item in frames}) == 1
    assert frames[0]['runtimeBytes'] > 0
    assert all(item['runtimeBytes'] == 0 for item in frames[1:])
    assert all(item['targetMetadataChecks'] > 0 for item in frames)
    assert n.operation_trace.aclChecks > 1


def test_public_publisher_reuses_one_qualifier_then_next_call_is_cold(observed):
    n, trace = observed, observed.operation_trace
    deadline = bridge._DEADLINE.get()
    first = helper.publish_active_slot(n.selected, 'sync')
    assert first['status'] == 'prepared'
    assert_shared_operation(n, trace.frames, trace.leases)
    lease = trace.leases[0]
    assert not lease.active and lease.custody._cache == {}
    assert bridge._NATIVE_RUNTIME_OPERATION.get() is None
    assert bridge._DEADLINE.get() == deadline
    assert trace.outsideBytes > 0  # Cold verification and fixture metadata walks remain separate.
    assert int(first['invocation']['admissionDeadlineNs']) - n.now == 30 * 10**9
    assert int(first['invocation']['workerDeadlineNs']) - n.now == 300 * 10**9
    before = (custody.NATIVE_STATE / 'custody.json').read_bytes()
    record = (n.metadata / 'source-jobs-active.json').read_bytes()
    split = len(trace.frames)
    again = helper.publish_active_slot(n.selected, 'sync')
    assert again['status'] == 'observed' and again['invocation'] == first['invocation']
    assert_shared_operation(n, trace.frames[split:], trace.leases[split:])
    assert trace.leases[split] is not lease and not trace.leases[split].active
    assert (custody.NATIVE_STATE / 'custody.json').read_bytes() == before
    assert (n.metadata / 'source-jobs-active.json').read_bytes() == record
    assert bridge._DEADLINE.get() == deadline
    assert journal(n)['phase'] == 'committed'
    assert gate()['launch']['invocations'][first['label']]['invocation'] == first['invocation']
    (n.world / 'publisher-operation-cache-observations.json').write_text(json.dumps({
        'frames':trace.frames,'sourceAclChecks':trace.aclChecks,'outsideInspectBytes':trace.outsideBytes,
        'separateOperations':True,'leasesRevoked':True,'sameGateAndJournalAfterObservation':True,
        'scope':'actual RuntimeCustody bytes/FD metadata; Darwin identity and ACL metadata explicitly simulated',
    }, sort_keys=True) + '\n')


def test_public_publisher_error_revokes_lease_and_preserves_before_gate(observed, monkeypatch):
    n, trace = observed, observed.operation_trace
    actual_binding = custody._native_environment_binding

    def binding(*args, **kwargs):
        actual_binding(*args, **kwargs)
        raise OSError('invented publisher binding failure')

    monkeypatch.setattr(custody, '_native_environment_binding', binding)
    with pytest.raises(OSError, match='invented publisher binding failure'):
        helper.publish_active_slot(n.selected, 'sync')
    assert trace.frames and all(not lease.active and lease.custody._cache == {} for lease in trace.leases)
    assert bridge._NATIVE_RUNTIME_OPERATION.get() is None
    assert (custody.NATIVE_STATE / 'custody.json').read_bytes() == n.initial_gate
    assert not (n.metadata / 'source-jobs-active.json').exists()


def test_public_wrapper_preserves_an_existing_outer_operation(observed):
    n = observed
    with bridge.LaunchdAccessBridge._native_runtime_operation(n.f.bridge):
        outer = bridge.LaunchdAccessBridge._native_runtime_lease(n.f.bridge)
        result = helper.publish_active_slot(n.selected, 'sync')
        assert result['status'] == 'prepared'
        assert all(lease is outer for lease in n.operation_trace.leases)
        assert outer.active and outer.custody._cache
    assert not outer.active and outer.custody._cache == {}


@pytest.mark.parametrize('invalid', ['duck', 'seal', 'bridge', 'deployment'])
def test_public_wrapper_refuses_invalid_selection_before_lease_access(active, monkeypatch, invalid):  # noqa: F811
    n = active

    class Duck:
        @property
        def bridge(self):
            pytest.fail('untyped caller bridge attribute was accessed')

    selected = Duck() if invalid == 'duck' else copy.copy(n.selected)
    if invalid != 'duck':
        object.__setattr__(selected, {'seal':'seal','bridge':'bridge','deployment':'deployment'}[invalid],
                           object() if invalid != 'bridge' else SimpleNamespace())
    def forbidden(_self):
        pytest.fail('invalid selection reached operation scope')

    monkeypatch.setattr(bridge.LaunchdAccessBridge, '_native_runtime_operation', forbidden)
    with pytest.raises(custody.CustodyError, match='deployment-required'):
        helper.publish_active_slot(selected, 'sync')
    assert bridge._NATIVE_RUNTIME_OPERATION.get() is None
    assert not (n.metadata / 'source-jobs-active.json').exists()
    assert (custody.NATIVE_STATE / 'custody.json').read_bytes() == n.initial_gate
