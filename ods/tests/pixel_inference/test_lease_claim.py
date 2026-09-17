"""Contract tests for pixel_provider.lease_claim.LeaseClaim.

Route workers take a persistent no-replay lease per run before any model call.
`test_route_worker.py` covers the happy path, replay refusal, and one busy
slot; this file pins the remaining public contract: identifier validation, the
hashed session id in claim.json, the third-entrant busy refusal, the finish
statuses, and the event-metadata allowlist that keeps upstream response fields
(for example a provider-reported model label) out of persisted results.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'bin'))
from pixel_provider.lease_claim import LeaseClaim
from pixel_provider.store import StoreError

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='POSIX lease ownership')


def run_id(prefix=False):
    value = str(uuid.uuid4())
    return 'chatcmpl_' + value if prefix else value


def test_run_id_shape_and_case_normalization(tmp_path):
    upper = run_id().upper()
    with LeaseClaim(tmp_path, upper, 'session', 0) as claim:
        assert claim.run_id == upper.lower()
        assert claim.path.name == upper.lower()
    with LeaseClaim(tmp_path, run_id(prefix=True), 'session', 0):
        pass


@pytest.mark.parametrize('bad', ['', 'not-a-uuid', 'chatcmpl_xyz', 42, None,
                                 'chatcmpl_' + str(uuid.uuid4())[:-1] + 'g'])
def test_rejects_invalid_run_ids(tmp_path, bad):
    with pytest.raises(StoreError, match='provider-run-invalid'):
        LeaseClaim(tmp_path, bad, 'session', 0)


@pytest.mark.parametrize('session', ['', 'x' * 257, None, 9])
def test_rejects_invalid_session_ids(tmp_path, session):
    with pytest.raises(StoreError, match='provider-session-invalid'):
        LeaseClaim(tmp_path, run_id(), session, 0)


@pytest.mark.parametrize('revision', [-1, 2**53, '1', 1.0, None])
def test_rejects_invalid_revisions(tmp_path, revision):
    with pytest.raises(StoreError, match='provider-revision-invalid'):
        LeaseClaim(tmp_path, run_id(), 'session', revision)


def test_claim_json_publishes_only_a_session_hash(tmp_path):
    session = 'conversation-' + uuid.uuid4().hex
    rid = run_id()
    with LeaseClaim(tmp_path, rid, session, 7):
        pass
    document = json.loads((tmp_path / 'route-leases' / rid / 'claim.json').read_text())
    assert document == {'schemaVersion': 1, 'runId': rid,
                        'sessionIdHash': hashlib.sha256(session.encode()).hexdigest(),
                        'revision': 7}
    assert session not in document.values()


def test_third_concurrent_claim_is_busy(tmp_path):
    blocked = run_id()
    with LeaseClaim(tmp_path, run_id(), 'a', 0):
        with LeaseClaim(tmp_path, run_id(), 'b', 0):
            with pytest.raises(StoreError, match='provider-lease-busy'):
                with LeaseClaim(tmp_path, blocked, 'c', 0):
                    pass
        # The refused entrant still consumes its identity: a late retry cannot
        # turn that earlier terminal refusal into new work.
        with pytest.raises(StoreError, match='provider-run-replayed'):
            with LeaseClaim(tmp_path, blocked, 'c', 0):
                pass


def test_reentering_a_held_claim_is_rejected(tmp_path):
    with LeaseClaim(tmp_path, run_id(), 'a', 0) as claim:
        with pytest.raises(StoreError, match='provider-lease-already-held'):
            claim.__enter__()


@pytest.mark.parametrize('status', ['closed', 'failed', 'deadline'])
def test_finish_persists_terminal_status(tmp_path, status):
    rid = run_id()
    with LeaseClaim(tmp_path, rid, 'a', 0) as claim:
        claim.finish(status)
    result = json.loads((tmp_path / 'route-leases' / rid / 'result.json').read_text())
    assert result['status'] == status and result['events'] == []


@pytest.mark.parametrize('status', ['completed', 'ok', '', None, 200])
def test_finish_rejects_nonterminal_statuses(tmp_path, status):
    with LeaseClaim(tmp_path, run_id(), 'a', 0) as claim:
        with pytest.raises(StoreError, match='provider-lease-status-invalid'):
            claim.finish(status)


def test_finish_requires_a_held_slot(tmp_path):
    claim = LeaseClaim.__new__(LeaseClaim)
    claim._fd = None
    with pytest.raises(StoreError, match='provider-lease-status-invalid'):
        claim.finish('closed')


def test_finish_filters_event_metadata_to_the_allowlist(tmp_path):
    rid = run_id()
    events = [
        {'requestId': 'r1', 'revision': 4, 'providerId': 'primary', 'result': 'attempt',
         'attempt': 1, 'upstreamStatus': 503},
        {'requestId': 'r1', 'revision': 4, 'providerId': 'primary', 'result': 'completed',
         'reportedModel': 'upstream-model-name', 'error': 'secrets', 'extra': 'dropped'},
    ]
    with LeaseClaim(tmp_path, rid, 'a', 0) as claim:
        claim.finish('closed', events)
    result = json.loads((tmp_path / 'route-leases' / rid / 'result.json').read_text())
    allowed = {'requestId', 'revision', 'providerId', 'result', 'attempt', 'upstreamStatus'}
    for entry in result['events']:
        assert set(entry) <= allowed
    assert result['events'][1] == {'requestId': 'r1', 'revision': 4,
                                   'providerId': 'primary', 'result': 'completed'}
    assert not any('reportedModel' in entry or 'error' in entry for entry in result['events'])


def test_result_survives_release_and_directory_modes(tmp_path):
    rid = run_id()
    with LeaseClaim(tmp_path, rid, 'a', 0) as claim:
        claim.finish('deadline', [{'result': 'deadline'}])
    leaf = tmp_path / 'route-leases' / rid
    assert leaf.stat().st_mode & 0o777 == 0o700
    assert (tmp_path / 'route-leases').stat().st_mode & 0o777 == 0o700
