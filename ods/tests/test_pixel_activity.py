"""Tests for ods/bin/pixel_provider/activity — stdlib only, no external deps."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "bin")
)
from pixel_provider.activity import (  # noqa: E402
    ActivitySnapshot,
    assess_activity,
    transition_decision,
    REASON_MALFORMED_SOURCE,
    REASON_INVALID_CLOCK,
    REASON_SOURCE_FUTURE,
    REASON_SOURCE_STALE,
    REASON_EPOCH_MISMATCH,
)

_NOW = 1000000
_MAX_AGE = 5000
_EPOCH = "epoch-1"


def _valid_observation(runs: list[dict] | None = None) -> dict:
    return {
        "schemaVersion": 1,
        "observedAt": _NOW,
        "sourceEpoch": _EPOCH,
        "runs": runs or [],
    }


class TestAssessActivity(unittest.TestCase):
    def _s(self, **kw):
        return assess_activity(
            kw.get("value", _valid_observation()),
            now_ms=kw.get("now_ms", _NOW),
            max_age_ms=kw.get("max_age_ms", _MAX_AGE),
            expected_epoch=kw.get("expected_epoch", _EPOCH),
        )

    # --- valid inputs ---
    def test_idle_empty_runs(self):
        s = self._s()
        self.assertEqual(s.status, "idle")
        self.assertEqual(s.active_runs, ())
        self.assertEqual(s.reason, "")

    def test_busy_chat(self):
        s = self._s(value=_valid_observation([{"id": "r1", "kind": "chat", "state": "active"}]))
        self.assertEqual(s.status, "busy")
        self.assertEqual(s.active_runs, ("r1",))
        self.assertEqual(s.reason, "")

    def test_busy_all_kinds(self):
        runs = [
            {"id": "a", "kind": "chat", "state": "active"},
            {"id": "b", "kind": "api", "state": "active"},
            {"id": "c", "kind": "cron", "state": "active"},
            {"id": "d", "kind": "background", "state": "active"},
        ]
        s = self._s(value=_valid_observation(runs))
        self.assertEqual(s.status, "busy")
        self.assertEqual(s.active_runs, ("a", "b", "c", "d"))

    def test_terminal_only_idle(self):
        s = self._s(value=_valid_observation([{"id": "t1", "kind": "chat", "state": "terminal"}]))
        self.assertEqual(s.status, "idle")

    def test_sorted_active_ids(self):
        runs = [
            {"id": "z1", "kind": "chat", "state": "active"},
            {"id": "a1", "kind": "api", "state": "active"},
        ]
        s = self._s(value=_valid_observation(runs))
        self.assertEqual(s.active_runs, ("a1", "z1"))

    # --- unknown on malformed ---
    def _unknown_if(self, expected_reason=None, **kw):
        s = self._s(**kw)
        self.assertEqual(s.status, "unknown")
        if expected_reason is not None:
            self.assertEqual(s.reason, expected_reason)

    def test_non_dict(self):
        self._unknown_if(expected_reason=REASON_MALFORMED_SOURCE, value="not a dict")

    def test_missing_key(self):
        self._unknown_if(expected_reason=REASON_MALFORMED_SOURCE, value={"schemaVersion": 1, "observedAt": 0})

    def test_unknown_key(self):
        v = _valid_observation()
        v["extra"] = True
        self._unknown_if(expected_reason=REASON_MALFORMED_SOURCE, value=v)

    def test_duplicate_id(self):
        self._unknown_if(expected_reason=REASON_MALFORMED_SOURCE, value=_valid_observation([
            {"id": "x", "kind": "chat", "state": "active"},
            {"id": "x", "kind": "api", "state": "active"},
        ]))

    def test_bool_as_timestamp(self):
        self._unknown_if(expected_reason=REASON_INVALID_CLOCK, value=_valid_observation(), now_ms=True)

    def test_negative_now_ms(self):
        self._unknown_if(expected_reason=REASON_INVALID_CLOCK, value=_valid_observation(), now_ms=-1)

    def test_bool_max_age(self):
        self._unknown_if(expected_reason=REASON_INVALID_CLOCK, value=_valid_observation(), max_age_ms=True)

    def test_stale_age(self):
        self._unknown_if(expected_reason=REASON_SOURCE_STALE, value=_valid_observation(), now_ms=_NOW + _MAX_AGE + 1)

    def test_boundary_age_accepted(self):
        s = self._s(value=_valid_observation(), now_ms=_NOW + _MAX_AGE)
        self.assertNotEqual(s.status, "unknown")

    def test_future_timestamp(self):
        self._unknown_if(expected_reason=REASON_SOURCE_FUTURE, value=_valid_observation(), now_ms=_NOW - 1)

    def test_bad_epoch(self):
        self._unknown_if(expected_reason=REASON_EPOCH_MISMATCH, value=_valid_observation(), expected_epoch="different")

    def test_input_not_mutated(self):
        v = _valid_observation([{"id": "r1", "kind": "chat", "state": "active"}])
        orig = copy.deepcopy(v)
        self._s(value=v)
        self.assertEqual(v, orig)

    # --- schemaVersion strictness: bool True and float 1.0 rejected ---
    def test_schema_version_bool_true_rejected(self):
        v = _valid_observation()
        v["schemaVersion"] = True
        self._unknown_if(expected_reason=REASON_MALFORMED_SOURCE, value=v)

    def test_schema_version_float_rejected(self):
        v = _valid_observation()
        v["schemaVersion"] = 1.0
        self._unknown_if(expected_reason=REASON_MALFORMED_SOURCE, value=v)

    # --- newline in ID rejected (fullmatch) ---
    def test_newline_id_rejected(self):
        self._unknown_if(expected_reason=REASON_MALFORMED_SOURCE, value=_valid_observation([
            {"id": "r1\n", "kind": "chat", "state": "active"},
        ]))

    def test_newline_epoch_rejected(self):
        self._unknown_if(expected_reason=REASON_MALFORMED_SOURCE, expected_epoch="epoch-1\n")

    # --- observation bool rejected ---
    def test_observed_at_bool_rejected(self):
        v = _valid_observation()
        v["observedAt"] = True
        self._unknown_if(expected_reason=REASON_MALFORMED_SOURCE, value=v)

    # --- giant list exceeds bound ---
    def test_giant_list_rejected(self):
        runs = [{"id": f"r{i:04d}", "kind": "chat", "state": "terminal"} for i in range(4100)]
        self._unknown_if(expected_reason=REASON_MALFORMED_SOURCE, value=_valid_observation(runs))

    # --- malformed run fields ---
    def test_run_missing_field(self):
        self._unknown_if(expected_reason=REASON_MALFORMED_SOURCE, value=_valid_observation([
            {"id": "r1", "kind": "chat"},
        ]))

    def test_run_extra_field(self):
        self._unknown_if(expected_reason=REASON_MALFORMED_SOURCE, value=_valid_observation([
            {"id": "r1", "kind": "chat", "state": "active", "extra": 1},
        ]))

    def test_run_invalid_kind(self):
        self._unknown_if(expected_reason=REASON_MALFORMED_SOURCE, value=_valid_observation([
            {"id": "r1", "kind": "unknown_kind", "state": "active"},
        ]))

    def test_run_invalid_state(self):
        self._unknown_if(expected_reason=REASON_MALFORMED_SOURCE, value=_valid_observation([
            {"id": "r1", "kind": "chat", "state": "pending"},
        ]))

    def test_run_non_dict(self):
        self._unknown_if(expected_reason=REASON_MALFORMED_SOURCE, value=_valid_observation(["not_a_dict"]))


class TestTransitionDecision(unittest.TestCase):
    def test_every_run_kind_blocks_each_transition(self):
        for kind in ("chat", "api", "cron", "background"):
            for mode in ("sandboxed", "full-access"):
                with self.subTest(kind=kind, mode=mode):
                    snapshot = assess_activity(
                        _valid_observation([{"id": "r1", "kind": kind, "state": "active"}]),
                        now_ms=_NOW, max_age_ms=_MAX_AGE, expected_epoch=_EPOCH,
                    )
                    result = transition_decision(snapshot, expected_revision=1,
                        current_revision=1, requested_mode=mode, confirmed=True)
                    self.assertEqual(result, {"allowed": False, "reason": "run-active"})

    def test_malformed_snapshot_reason_denied(self):
        result = transition_decision(ActivitySnapshot("idle", (), None),
            expected_revision=1, current_revision=1, requested_mode="sandboxed", confirmed=True)
        self.assertEqual(result, {"allowed": False, "reason": "invalid-request"})

    def test_idle_full_access_confirmed(self):
        s = ActivitySnapshot("idle", (), "")
        r = transition_decision(s, expected_revision=1, current_revision=1, requested_mode="full-access", confirmed=True)
        self.assertEqual(r, {"allowed": True, "reason": "ready"})

    def test_idle_sandboxed_no_confirm(self):
        r = transition_decision(
            ActivitySnapshot("idle", (), ""),
            expected_revision=0, current_revision=0, requested_mode="sandboxed", confirmed=False,
        )
        self.assertEqual(r, {"allowed": True, "reason": "ready"})

    def test_full_access_no_confirm_denied(self):
        r = transition_decision(
            ActivitySnapshot("idle", (), ""),
            expected_revision=1, current_revision=1, requested_mode="full-access", confirmed=False,
        )
        self.assertEqual(r["reason"], "confirmation-required")

    def test_busy_denied(self):
        r = transition_decision(
            ActivitySnapshot("busy", ("r1",), ""),
            expected_revision=1, current_revision=1, requested_mode="sandboxed", confirmed=True,
        )
        self.assertEqual(r["reason"], "run-active")

    def test_unknown_denied(self):
        r = transition_decision(
            ActivitySnapshot("unknown", (), ""),
            expected_revision=1, current_revision=1, requested_mode="sandboxed", confirmed=True,
        )
        self.assertEqual(r["reason"], "activity-unknown")

    def test_stale_revision_denied(self):
        r = transition_decision(
            ActivitySnapshot("idle", (), ""),
            expected_revision=2, current_revision=1, requested_mode="sandboxed", confirmed=False,
        )
        self.assertEqual(r["reason"], "stale-revision")

    def test_invalid_mode_denied(self):
        r = transition_decision(
            ActivitySnapshot("idle", (), ""),
            expected_revision=1, current_revision=1, requested_mode="admin", confirmed=False,
        )
        self.assertEqual(r["reason"], "invalid-request")

    def test_bool_revision_denied(self):
        r = transition_decision(
            ActivitySnapshot("idle", (), ""),
            expected_revision=True, current_revision=1, requested_mode="sandboxed", confirmed=False,
        )
        self.assertEqual(r["reason"], "invalid-request")

    def test_invalid_snapshot_status_denied(self):
        r = transition_decision(
            ActivitySnapshot("bogus", (), ""),
            expected_revision=1, current_revision=1, requested_mode="sandboxed", confirmed=False,
        )
        self.assertEqual(r["reason"], "invalid-request")

    def test_priority_invalid_before_revision(self):
        r = transition_decision(
            ActivitySnapshot("idle", (), ""),
            expected_revision=2, current_revision=1, requested_mode="admin", confirmed=False,
        )
        self.assertEqual(r["reason"], "invalid-request")

    # --- malformed snapshot rejection ---
    def test_forged_idle_with_active_runs_rejected(self):
        """Idle snapshot with non-empty active_runs is malformed."""
        r = transition_decision(
            ActivitySnapshot("idle", ("r1",), ""),
            expected_revision=1, current_revision=1, requested_mode="sandboxed", confirmed=True,
        )
        self.assertEqual(r["reason"], "invalid-request")

    def test_forged_unknown_with_active_runs_rejected(self):
        """Unknown snapshot with non-empty active_runs is malformed."""
        r = transition_decision(
            ActivitySnapshot("unknown", ("r1",), ""),
            expected_revision=1, current_revision=1, requested_mode="sandboxed", confirmed=True,
        )
        self.assertEqual(r["reason"], "invalid-request")

    def test_forged_busy_empty_runs_rejected(self):
        """Busy snapshot with empty active_runs is malformed."""
        r = transition_decision(
            ActivitySnapshot("busy", (), ""),
            expected_revision=1, current_revision=1, requested_mode="sandboxed", confirmed=True,
        )
        self.assertEqual(r["reason"], "invalid-request")

    def test_malformed_id_in_active_runs_rejected(self):
        """Active runs containing invalid IDs are rejected."""
        r = transition_decision(
            ActivitySnapshot("busy", ("bad id!",), ""),
            expected_revision=1, current_revision=1, requested_mode="sandboxed", confirmed=True,
        )
        self.assertEqual(r["reason"], "invalid-request")

    def test_duplicate_ids_in_active_runs_rejected(self):
        r = transition_decision(
            ActivitySnapshot("busy", ("r1", "r1"), ""),
            expected_revision=1, current_revision=1, requested_mode="sandboxed", confirmed=True,
        )
        self.assertEqual(r["reason"], "invalid-request")

    def test_non_snapshot_object_rejected(self):
        r = transition_decision(
            {"status": "idle"},
            expected_revision=1, current_revision=1, requested_mode="sandboxed", confirmed=True,
        )
        self.assertEqual(r["reason"], "invalid-request")

    def test_non_tuple_active_runs_rejected(self):
        """active_runs must be a tuple, not a list."""
        s = ActivitySnapshot("busy", ["r1"], "")  # type: ignore
        # This should still be rejected because tuple check
        r = transition_decision(
            s,
            expected_revision=1, current_revision=1, requested_mode="sandboxed", confirmed=True,
        )
        self.assertEqual(r["reason"], "invalid-request")

    # --- four kinds alone block BOTH directions with confirmation ---
    def test_busy_blocks_sandboxed(self):
        r = transition_decision(
            ActivitySnapshot("busy", ("r1",), ""),
            expected_revision=1, current_revision=1, requested_mode="sandboxed", confirmed=True,
        )
        self.assertFalse(r["allowed"])

    def test_busy_blocks_full_access(self):
        r = transition_decision(
            ActivitySnapshot("busy", ("r1",), ""),
            expected_revision=1, current_revision=1, requested_mode="full-access", confirmed=True,
        )
        self.assertFalse(r["allowed"])

    def test_unknown_blocks_sandboxed(self):
        r = transition_decision(
            ActivitySnapshot("unknown", (), REASON_MALFORMED_SOURCE),
            expected_revision=1, current_revision=1, requested_mode="sandboxed", confirmed=True,
        )
        self.assertFalse(r["allowed"])
        self.assertEqual(r["reason"], "activity-unknown")

    def test_unknown_blocks_full_access(self):
        r = transition_decision(
            ActivitySnapshot("unknown", (), REASON_EPOCH_MISMATCH),
            expected_revision=1, current_revision=1, requested_mode="full-access", confirmed=True,
        )
        self.assertFalse(r["allowed"])
        self.assertEqual(r["reason"], "activity-unknown")

    # --- invalid snapshot types ---
    def test_invalid_snapshot_type_bool(self):
        r = transition_decision(
            True,
            expected_revision=1, current_revision=1, requested_mode="sandboxed", confirmed=True,
        )
        self.assertEqual(r["reason"], "invalid-request")

    def test_invalid_snapshot_type_none(self):
        r = transition_decision(
            None,
            expected_revision=1, current_revision=1, requested_mode="sandboxed", confirmed=True,
        )
        self.assertEqual(r["reason"], "invalid-request")


class TestTemporalRetention(unittest.TestCase):
    """Temporal validation tests retained from original."""

    def test_age_exactly_max_accepted(self):
        s = assess_activity(
            _valid_observation(),
            now_ms=_NOW + _MAX_AGE,
            max_age_ms=_MAX_AGE,
            expected_epoch=_EPOCH,
        )
        self.assertNotEqual(s.status, "unknown")

    def test_age_exceeds_max_rejected(self):
        s = assess_activity(
            _valid_observation(),
            now_ms=_NOW + _MAX_AGE + 1,
            max_age_ms=_MAX_AGE,
            expected_epoch=_EPOCH,
        )
        self.assertEqual(s.status, "unknown")
        self.assertEqual(s.reason, REASON_SOURCE_STALE)

    def test_observed_at_future_rejected(self):
        s = assess_activity(
            _valid_observation(),
            now_ms=_NOW - 1,
            max_age_ms=_MAX_AGE,
            expected_epoch=_EPOCH,
        )
        self.assertEqual(s.status, "unknown")
        self.assertEqual(s.reason, REASON_SOURCE_FUTURE)

    def test_observed_at_zero_valid(self):
        """observedAt=0 is valid when now_ms is large enough."""
        s = assess_activity(
            {"schemaVersion": 1, "observedAt": 0, "sourceEpoch": _EPOCH, "runs": []},
            now_ms=_NOW,
            max_age_ms=_NOW + 1,
            expected_epoch=_EPOCH,
        )
        self.assertEqual(s.status, "idle")


if __name__ == "__main__":
    unittest.main()
