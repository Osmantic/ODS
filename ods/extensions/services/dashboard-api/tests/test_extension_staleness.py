"""Unit tests for two pure helpers in routers/extensions.py that were not
directly covered:

  * _is_stale — drives TTL-based cleanup of extension progress files.
    Timezone handling matters: progress files are written with tz-aware
    UTC ISO timestamps, and a malformed/naive timestamp must be treated as
    stale so a corrupt file can never wedge the UI in a spinning state.

  * _is_one_shot_extension — decides whether a catalog entry is a one-shot
    CLI/setup tool, preferring the explicit ``startup_check`` flag and
    falling back to ``port == 0`` for older catalogs.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from routers.extensions import _is_stale, _is_one_shot_extension


def _iso(delta_seconds: int) -> str:
    """A tz-aware UTC ISO timestamp offset from now by *delta_seconds*."""
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_seconds)).isoformat()


# ---------------------------------------------------------------------------
# _is_stale
# ---------------------------------------------------------------------------


def test_old_timestamp_is_stale():
    assert _is_stale(_iso(-7200), max_age_seconds=3600) is True


def test_recent_timestamp_is_not_stale():
    assert _is_stale(_iso(-60), max_age_seconds=3600) is False


def test_future_timestamp_is_not_stale():
    # Negative age (clock skew) must not read as stale.
    assert _is_stale(_iso(120), max_age_seconds=3600) is False


def test_zulu_suffix_is_accepted():
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # A fresh 'Z'-suffixed timestamp parses and is not stale.
    assert _is_stale(ts, max_age_seconds=3600) is False


def test_naive_timestamp_is_treated_as_stale():
    # No offset → subtraction against an aware "now" raises TypeError, which
    # the helper maps to "stale" so a bad file is cleaned up rather than kept.
    assert _is_stale("2026-01-01T00:00:00", max_age_seconds=3600) is True


def test_malformed_timestamp_is_stale():
    assert _is_stale("not-a-timestamp", max_age_seconds=3600) is True


def test_empty_timestamp_is_stale():
    assert _is_stale("", max_age_seconds=3600) is True


# ---------------------------------------------------------------------------
# _is_one_shot_extension
# ---------------------------------------------------------------------------


def test_startup_check_false_marks_one_shot():
    assert _is_one_shot_extension({"startup_check": False, "port": 8080}) is True


def test_startup_check_true_is_not_one_shot():
    # Explicit flag wins even when port would otherwise suggest one-shot.
    assert _is_one_shot_extension({"startup_check": True, "port": 0}) is False


def test_port_zero_fallback_marks_one_shot():
    assert _is_one_shot_extension({"port": 0}) is True


def test_nonzero_port_without_flag_is_not_one_shot():
    assert _is_one_shot_extension({"port": 8080}) is False
