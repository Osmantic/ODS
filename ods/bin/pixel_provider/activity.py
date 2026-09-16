"""Shared fail-closed admission contract for activity assessment.

Pure advisory gate only.  A snapshot returned by this module is NOT an
authorization token.  Any caller that mutates state MUST collect a fresh
snapshot under the same admission lock before applying the mutation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Fullmatch — ID must span the entire string (no trailing newline, etc.)
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
VALID_KINDS = frozenset({"chat", "api", "cron", "background"})
VALID_STATES = frozenset({"active", "terminal"})
VALID_MODES = frozenset({"sandboxed", "full-access"})

_MAX_RUNS = 4096

_UNKNOWN_STATUS = "unknown"
_IDLE_STATUS = "idle"
_BUSY_STATUS = "busy"

# Content-free rejection reasons used by assess_activity
REASON_MALFORMED_SOURCE = "malformed-source"
REASON_INVALID_CLOCK = "invalid-clock"
REASON_SOURCE_FUTURE = "source-future"
REASON_SOURCE_STALE = "source-stale"
REASON_EPOCH_MISMATCH = "epoch-mismatch"


@dataclass(frozen=True)
class ActivitySnapshot:
    status: str
    active_runs: tuple[str, ...]
    reason: str


def _bad(reason: str) -> ActivitySnapshot:
    return ActivitySnapshot(_UNKNOWN_STATUS, (), reason)


def assess_activity(
    value: Any,
    *,
    now_ms: int,
    max_age_ms: int,
    expected_epoch: str,
) -> ActivitySnapshot:
    # --- strict clock parameter gates (content-free) ---
    if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0:
        return _bad(REASON_INVALID_CLOCK)
    if isinstance(max_age_ms, bool) or not isinstance(max_age_ms, int) or max_age_ms <= 0:
        return _bad(REASON_INVALID_CLOCK)
    if not isinstance(expected_epoch, str) or not ID_RE.fullmatch(expected_epoch):
        return _bad(REASON_MALFORMED_SOURCE)

    if not isinstance(value, dict):
        return _bad(REASON_MALFORMED_SOURCE)

    expected_keys = {"schemaVersion", "observedAt", "sourceEpoch", "runs"}
    if set(value.keys()) != expected_keys:
        return _bad(REASON_MALFORMED_SOURCE)

    # schemaVersion must be int == 1 (reject bool True and float 1.0)
    sv = value.get("schemaVersion")
    if isinstance(sv, bool) or not isinstance(sv, int) or sv != 1:
        return _bad(REASON_MALFORMED_SOURCE)

    observed_at = value.get("observedAt")
    if isinstance(observed_at, bool) or not isinstance(observed_at, int) or observed_at < 0:
        return _bad(REASON_MALFORMED_SOURCE)

    source_epoch = value.get("sourceEpoch")
    if not isinstance(source_epoch, str) or not ID_RE.fullmatch(source_epoch):
        return _bad(REASON_MALFORMED_SOURCE)

    runs = value.get("runs")
    if not isinstance(runs, list):
        return _bad(REASON_MALFORMED_SOURCE)

    if len(runs) > _MAX_RUNS:
        return _bad(REASON_MALFORMED_SOURCE)

    # --- temporal validation (distinct reasons) ---
    age = now_ms - observed_at
    if observed_at > now_ms:
        return _bad(REASON_SOURCE_FUTURE)
    if age > max_age_ms:
        return _bad(REASON_SOURCE_STALE)

    # --- epoch mismatch ---
    if source_epoch != expected_epoch:
        return _bad(REASON_EPOCH_MISMATCH)

    # --- validate runs ---
    seen_ids: set[str] = set()
    active_ids: list[str] = []
    for run in runs:
        if not isinstance(run, dict):
            return _bad(REASON_MALFORMED_SOURCE)
        if set(run.keys()) != {"id", "kind", "state"}:
            return _bad(REASON_MALFORMED_SOURCE)

        rid = run.get("id")
        if not isinstance(rid, str) or not ID_RE.fullmatch(rid):
            return _bad(REASON_MALFORMED_SOURCE)
        if rid in seen_ids:
            return _bad(REASON_MALFORMED_SOURCE)
        seen_ids.add(rid)

        kind = run.get("kind")
        if not isinstance(kind, str) or kind not in VALID_KINDS:
            return _bad(REASON_MALFORMED_SOURCE)

        state = run.get("state")
        if not isinstance(state, str) or state not in VALID_STATES:
            return _bad(REASON_MALFORMED_SOURCE)

        if state == "active":
            active_ids.append(rid)

    if active_ids:
        active_ids.sort()
        return ActivitySnapshot(_BUSY_STATUS, tuple(active_ids), "")
    return ActivitySnapshot(_IDLE_STATUS, (), "")


def transition_decision(
    snapshot: ActivitySnapshot,
    *,
    expected_revision: int,
    current_revision: int,
    requested_mode: str,
    confirmed: bool,
) -> dict[str, object]:
    """Fail-closed access transition gate.

    Priority order: invalid-request → stale-revision → confirmation-required
    → activity-unknown → run-active → ready.

    Rejects malformed snapshots: active_runs must be an exact tuple of
    unique valid IDs.  Idle/unknown require empty active_runs; busy
    requires non-empty.  Status must be one of the known enum values.
    """
    # --- invalid-request (parameter types) ---
    if not isinstance(snapshot, ActivitySnapshot):
        return {"allowed": False, "reason": "invalid-request"}
    if not isinstance(requested_mode, str) or requested_mode not in VALID_MODES:
        return {"allowed": False, "reason": "invalid-request"}
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
        return {"allowed": False, "reason": "invalid-request"}
    if isinstance(current_revision, bool) or not isinstance(current_revision, int) or current_revision < 0:
        return {"allowed": False, "reason": "invalid-request"}
    if not isinstance(confirmed, bool):
        return {"allowed": False, "reason": "invalid-request"}

    # --- malformed snapshot rejection ---
    if not isinstance(snapshot.status, str) or snapshot.status not in (_IDLE_STATUS, _BUSY_STATUS, _UNKNOWN_STATUS):
        return {"allowed": False, "reason": "invalid-request"}
    if not isinstance(snapshot.reason, str):
        return {"allowed": False, "reason": "invalid-request"}

    runs = snapshot.active_runs
    if type(runs) is not tuple or len(runs) > _MAX_RUNS:
        return {"allowed": False, "reason": "invalid-request"}

    # All IDs in active_runs must be valid
    for rid in runs:
        if not isinstance(rid, str) or not ID_RE.fullmatch(rid):
            return {"allowed": False, "reason": "invalid-request"}

    # Uniqueness
    if len(runs) != len(set(runs)):
        return {"allowed": False, "reason": "invalid-request"}

    # Idle/unknown must have empty active_runs
    if snapshot.status in (_IDLE_STATUS, _UNKNOWN_STATUS) and runs:
        return {"allowed": False, "reason": "invalid-request"}

    # Busy must have non-empty active_runs
    if snapshot.status == _BUSY_STATUS and not runs:
        return {"allowed": False, "reason": "invalid-request"}

    # --- stale-revision ---
    if expected_revision != current_revision:
        return {"allowed": False, "reason": "stale-revision"}

    # --- confirmation-required ---
    if requested_mode == "full-access" and not confirmed:
        return {"allowed": False, "reason": "confirmation-required"}

    # --- activity checks ---
    if snapshot.status == _UNKNOWN_STATUS:
        return {"allowed": False, "reason": "activity-unknown"}

    if snapshot.status == _BUSY_STATUS:
        return {"allowed": False, "reason": "run-active"}

    # snapshot.status == idle, revision matched, confirmation satisfied
    return {"allowed": True, "reason": "ready"}
