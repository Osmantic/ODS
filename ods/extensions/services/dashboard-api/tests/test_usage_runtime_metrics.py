"""Unit tests for the pure Prometheus/runtime-counter helpers in
routers/usage.py.

These functions parse llama.cpp's Prometheus text exposition and derive a
best-effort request count from token-counter deltas when the runtime does
not expose ``*_requests_total``. They are pure (aside from one module-level
observation cache) and previously had no direct coverage.
"""

from __future__ import annotations

import pytest

from routers import usage


# ---------------------------------------------------------------------------
# _metric_value
# ---------------------------------------------------------------------------


def test_metric_value_reads_matching_line():
    text = "# HELP foo\nfoo_total 42\nbar_total 7\n"
    assert usage._metric_value(text, "foo_total") == 42.0


def test_metric_value_handles_float_and_scientific_notation():
    assert usage._metric_value("x 1.5e3\n", "x") == 1500.0


def test_metric_value_missing_metric_returns_zero():
    assert usage._metric_value("foo_total 1\n", "absent_total") == 0


def test_metric_value_does_not_match_prefix_only():
    # "foo" must not match "foo_total" — the regex anchors on whitespace.
    assert usage._metric_value("foo_total 5\n", "foo") == 0


def test_metric_value_malformed_number_returns_zero():
    assert usage._metric_value("x notanumber\n", "x") == 0


# ---------------------------------------------------------------------------
# _has_metric
# ---------------------------------------------------------------------------


def test_has_metric_true_and_false():
    text = "requests_total 3\n"
    assert usage._has_metric(text, "requests_total") is True
    assert usage._has_metric(text, "missing_total") is False


# ---------------------------------------------------------------------------
# _observe_runtime_request_delta — the token-counter delta observer.
#
# It keeps a per-key cache of the last-seen token counters and infers one
# completed request each time the cumulative token count grows.
# ---------------------------------------------------------------------------


@pytest.fixture()
def clean_state(monkeypatch):
    """Isolate the module-level observation cache for each test."""
    monkeypatch.setattr(usage, "_LOCAL_RUNTIME_REQUEST_STATE", {})


def test_first_observation_initializes_baseline(clean_state):
    result = usage._observe_runtime_request_delta("k", input_tokens=10, output_tokens=5)
    assert result["requests"] == 0
    assert result["source"] == "unavailable"
    assert "baseline" in result["note"]


def test_token_growth_counts_a_request(clean_state):
    usage._observe_runtime_request_delta("k", input_tokens=10, output_tokens=5)
    result = usage._observe_runtime_request_delta("k", input_tokens=20, output_tokens=8)
    assert result["requests"] == 1
    assert result["source"] == "observed_counter_delta"


def test_successive_growth_accumulates(clean_state):
    usage._observe_runtime_request_delta("k", input_tokens=10, output_tokens=5)
    usage._observe_runtime_request_delta("k", input_tokens=20, output_tokens=8)
    result = usage._observe_runtime_request_delta("k", input_tokens=30, output_tokens=12)
    assert result["requests"] == 2


def test_no_token_change_keeps_cumulative_count(clean_state):
    usage._observe_runtime_request_delta("k", input_tokens=10, output_tokens=5)
    usage._observe_runtime_request_delta("k", input_tokens=20, output_tokens=8)
    # A repeated scrape with identical counters must not invent a new request
    # nor lose the previously observed one.
    result = usage._observe_runtime_request_delta("k", input_tokens=20, output_tokens=8)
    assert result["requests"] == 1


def test_counter_reset_rebaselines_to_zero(clean_state):
    usage._observe_runtime_request_delta("k", input_tokens=20, output_tokens=8)
    usage._observe_runtime_request_delta("k", input_tokens=30, output_tokens=10)
    # Runtime restart → counters drop below the last-seen totals → reset.
    result = usage._observe_runtime_request_delta("k", input_tokens=1, output_tokens=1)
    assert result["requests"] == 0
    assert result["source"] == "unavailable"


def test_distinct_keys_are_tracked_independently(clean_state):
    usage._observe_runtime_request_delta("a", input_tokens=10, output_tokens=0)
    usage._observe_runtime_request_delta("b", input_tokens=100, output_tokens=0)
    a = usage._observe_runtime_request_delta("a", input_tokens=11, output_tokens=0)
    b = usage._observe_runtime_request_delta("b", input_tokens=100, output_tokens=0)
    assert a["requests"] == 1  # key "a" grew
    assert b["requests"] == 0  # key "b" unchanged, still at baseline
