"""Exact Root enrollment steps settle without replaying their owner writes.

The installed services are simulated; the real gated coordinator, owner-file
transaction, completion receipt and Root access proof run in disposable state.
"""
import sys
if sys.platform == 'win32':
    from unittest import SkipTest
    raise SkipTest('POSIX connection coordination runs under Linux/WSL')

import copy
import hashlib
import json
from pathlib import Path
import secrets

import pytest

import test_connection_coordinator as harness
from pixel_access_bridge import AccessError
from pixel_connections import coordinator as c, planner, state

world = harness.world


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def owner(world, monkeypatch):
    worker = world.bridge.worker

    def actual_config_status(operation='status', **kwargs):
        reply = worker(operation, **kwargs)
        if operation == 'status':
            # The base fake uses a mode-only hash. Exact settlement must use the
            # actual bytes, as the production access owner worker does.
            return dict(reply, config_sha256=sha(world.bridge.config_path.read_bytes()))
        return reply

    monkeypatch.setattr(world.bridge, 'worker', actual_config_status)
    return world


def plan(owner):
    record = {'schemaVersion': 1, 'kind': 'ods-portal-connections',
              'updatedAt': '2026-10-28T12:00:00Z', 'ownerTimeZone': 'America/Chicago', 'connectors': {}}
    target = c.gateway_target(record, custody_held=False)
    answers = state.connection_answers(record, google_custody=False)
    before = owner.bridge.config_path.read_bytes()
    after = harness.tx._encoded(planner.plan_config(json.loads(before), target,
                                                    install_dir=str(owner.bridge.install_dir)))
    return {'target': target, 'answers': answers, 'transaction_id': secrets.token_hex(32),
            'before_config_sha256': sha(before), 'after_config_sha256': sha(after)}


def apply(owner, step):
    with owner.bridge.bounded(300), owner.bridge.locked():
        owner.bridge.discover()
        return c._change_gateway(owner.bridge, **step)


def crash_at_root_finish(monkeypatch):
    original = c._bind_marker

    def crash(bridge, journal):
        monkeypatch.setattr(c, '_bind_marker', original)
        raise harness.Crash()

    monkeypatch.setattr(c, '_bind_marker', crash)


def test_lost_root_reply_returns_closed_applied_receipt_without_another_write_or_restart(owner):
    step = plan(owner)
    assert apply(owner, step) == 'applied'
    files = (owner.bridge.config_path, owner.bridge.env_path, owner.bridge.answers_path)
    before = {path: path.read_bytes() for path in files}
    assert apply(owner, step) == 'applied'
    assert {path: path.read_bytes() for path in files} == before
    assert owner.bridge.log.count('connection-apply') == owner.bridge.log.count('restart') == 1
    assert owner.bridge.pending() is None


def test_pending_same_id_settles_and_returns_without_reapplying(owner, monkeypatch):
    step = plan(owner)
    crash_at_root_finish(monkeypatch)
    with pytest.raises(harness.Crash):
        apply(owner, step)
    pending = owner.bridge.pending()
    assert pending['transactionId'] == step['transaction_id']
    assert pending['beforeSha'] == step['before_config_sha256']
    assert pending['afterSha'] == step['after_config_sha256']
    assert apply(owner, step) == 'applied'
    assert owner.bridge.log.count('connection-apply') == owner.bridge.log.count('restart') == 1
    assert owner.bridge.native_phase == owner.bridge.edge_phase == 'idle'
    assert owner.bridge.pending() is None


@pytest.mark.parametrize('pending', [False, True], ids=['closed', 'held'])
def test_a_known_rollback_is_not_retried_toward_the_rejected_target(owner, monkeypatch, pending):
    step = plan(owner)
    files = (owner.bridge.config_path, owner.bridge.env_path, owner.bridge.answers_path)
    before = {path: path.read_bytes() for path in files}
    restarts = iter(['rejected', 'verified'])
    monkeypatch.setattr(c, '_restart', lambda bridge, journal: next(restarts))
    if pending:
        crash_at_root_finish(monkeypatch)
        with pytest.raises(harness.Crash):
            apply(owner, step)
    else:
        assert apply(owner, step) == 'rolled-back'
    assert apply(owner, step) == 'rolled-back'
    assert {path: path.read_bytes() for path in files} == before
    assert owner.bridge.log.count('connection-apply') == 1
    assert next(restarts, None) is None
    assert owner.bridge.pending() is None


@pytest.mark.parametrize('changed', ['transaction_id', 'before_config_sha256', 'after_config_sha256', 'target', 'answers'])
def test_pending_exact_step_rejects_a_different_plan_before_touching_admission(owner, monkeypatch, changed):
    step = plan(owner)
    crash_at_root_finish(monkeypatch)
    with pytest.raises(harness.Crash):
        apply(owner, step)
    journal = Path(owner.bridge.state) / 'transition.json'
    saved, log = journal.read_bytes(), owner.bridge.log[:]
    changed_step = copy.deepcopy(step)
    if changed in ('transaction_id', 'before_config_sha256', 'after_config_sha256'):
        changed_step[changed] = 'f' * 64
    elif changed == 'target':
        changed_step['target']['userTimezone'] = 'Europe/London'
    else:
        changed_step['answers']['userTimezone'] = 'Europe/London'
    with pytest.raises(AccessError, match='connection-transaction-conflict'):
        apply(owner, changed_step)
    assert journal.read_bytes() == saved and owner.bridge.log == log


@pytest.mark.parametrize('changes', [
    {'transaction_id': 'bad'}, {'transaction_id': True}, {'before_config_sha256': None},
    {'after_config_sha256': None}, {'before_config_sha256': 'F' * 64},
    {'transaction_id': None},
])
def test_exact_step_requires_a_valid_id_and_both_hashes_before_mutation(owner, changes):
    step = dict(plan(owner), **changes)
    before = owner.bridge.config_path.read_bytes()
    with pytest.raises(AccessError, match='invalid-connection-transaction'):
        apply(owner, step)
    assert owner.bridge.config_path.read_bytes() == before
    assert 'connection-apply' not in owner.bridge.log
    assert owner.bridge.pending() is None


@pytest.mark.parametrize('changed', ['after_hash', 'target', 'answers', 'config', 'environment', 'proof', 'revision'])
def test_closed_receipt_is_not_enough_without_matching_files_and_live_root_proof(owner, monkeypatch, changed):
    step = plan(owner)
    assert apply(owner, step) == 'applied'
    if changed == 'after_hash':
        step['after_config_sha256'] = 'f' * 64
    elif changed == 'target':
        step['target']['userTimezone'] = 'Europe/London'
    elif changed == 'answers':
        step['answers']['userTimezone'] = 'Europe/London'
    elif changed == 'config':
        owner.bridge.config_path.write_bytes(owner.bridge.config_path.read_bytes() + b'\n')
    elif changed == 'environment':
        owner.bridge.env_path.write_text(owner.bridge.env_path.read_text().replace(
            "PIXEL_LIMB_EMAIL_ENABLED='0'", "PIXEL_LIMB_EMAIL_ENABLED='1'"))
    else:
        inspect = owner.bridge.inspect

        def stale():
            snapshot = inspect()
            if changed == 'proof': snapshot['runtime_verified'] = False
            else: snapshot['revision'] = None
            return snapshot

        monkeypatch.setattr(owner.bridge, 'inspect', stale)
    with pytest.raises(AccessError, match='connection-(completion-mismatch|runtime-proof-failed)'):
        apply(owner, step)
    assert owner.bridge.log.count('connection-apply') == owner.bridge.log.count('restart') == 1
    assert owner.bridge.pending() is None


def test_no_write_resume_releases_existing_hold_without_creating_an_owner_receipt(owner, monkeypatch):
    step = plan(owner)
    worker = owner.bridge.worker

    def crash(operation='status', **kwargs):
        if operation == 'connection-apply':
            raise harness.Crash()
        return worker(operation, **kwargs)

    monkeypatch.setattr(owner.bridge, 'worker', crash)
    with pytest.raises(harness.Crash):
        apply(owner, step)
    assert owner.bridge.pending()['phase'] == 'invoking'
    monkeypatch.setattr(owner.bridge, 'worker', worker)
    assert apply(owner, step) == 'rolled-back'
    assert not (owner.bridge.owner_state / harness.tx.COMPLETED).exists()
    assert 'connection-apply' not in owner.bridge.log and 'restart' not in owner.bridge.log
    assert owner.bridge.pending() is None


@pytest.mark.parametrize('changed', ['answers', 'marker', 'process', 'revision'])
def test_closed_result_refuses_drift_during_its_fresh_runtime_observation(owner, monkeypatch, changed):
    step = plan(owner)
    assert apply(owner, step) == 'applied'
    inspect, calls = owner.bridge.inspect, []

    def racing():
        snapshot = inspect()
        calls.append(True)
        # The first inspection belongs to _change_gateway. The second is the
        # first closed-receipt proof; make the next observation disagree.
        if len(calls) == 2:
            if changed == 'answers':
                value = json.loads(owner.bridge.answers_path.read_text())
                owner.bridge.answers_path.write_text(json.dumps(dict(value, userTimezone='Europe/London')))
            elif changed == 'marker':
                value = json.loads(owner.bridge.marker_path.read_text())
                owner.bridge.marker_path.write_text(json.dumps(dict(value, configuration_sha256='f' * 64)))
            elif changed == 'process':
                owner.bridge.pid += 1
        if changed == 'revision' and len(calls) == 3:
            snapshot['revision'] = 'f' * 64
        return snapshot

    monkeypatch.setattr(owner.bridge, 'inspect', racing)
    with pytest.raises(AccessError, match='connection-completion-mismatch'):
        apply(owner, step)
    assert owner.bridge.log.count('connection-apply') == owner.bridge.log.count('restart') == 1
    assert owner.bridge.pending() is None


def test_wrong_expected_after_hash_never_claims_a_written_result_succeeded(owner):
    step = dict(plan(owner), after_config_sha256='f' * 64)
    with pytest.raises(AccessError, match='connection-completion-mismatch'):
        apply(owner, step)
    assert owner.bridge.pending()['phase'] == 'restarting'
    assert owner.bridge.native_phase == owner.bridge.edge_phase == 'held'
