"""Unit tests for the shared context-window policy helpers."""

import pytest

from context_policy import (
    HERMES_MIN_CONTEXT,
    HERMES_TARGET_CONTEXT,
    PIXEL_MIN_CONTEXT,
    clamp_context,
    is_context_sufficient,
)


class TestClampContext:
    def test_returns_value_within_range(self):
        assert clamp_context(HERMES_MIN_CONTEXT) == HERMES_MIN_CONTEXT

    def test_clamps_below_minimum(self):
        assert clamp_context(1024) == PIXEL_MIN_CONTEXT

    def test_clamps_above_maximum(self):
        assert clamp_context(1_000_000) == HERMES_TARGET_CONTEXT

    def test_custom_bounds(self):
        assert clamp_context(100, min_context=10, max_context=50) == 50
        assert clamp_context(5, min_context=10, max_context=50) == 10

    def test_rejects_non_positive_context(self):
        with pytest.raises(ValueError):
            clamp_context(0)
        with pytest.raises(ValueError):
            clamp_context(-8192)

    def test_rejects_non_positive_bounds(self):
        with pytest.raises(ValueError):
            clamp_context(8192, min_context=0)
        with pytest.raises(ValueError):
            clamp_context(8192, max_context=-1)

    def test_rejects_inverted_bounds(self):
        with pytest.raises(ValueError):
            clamp_context(8192, min_context=HERMES_TARGET_CONTEXT, max_context=PIXEL_MIN_CONTEXT)

    def test_rejects_non_integer_types(self):
        with pytest.raises(TypeError):
            clamp_context("65536")  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            clamp_context(65536.5)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            clamp_context(True)  # type: ignore[arg-type]


class TestIsContextSufficient:
    def test_meets_default_hermes_floor(self):
        assert is_context_sufficient(HERMES_MIN_CONTEXT) is True
        assert is_context_sufficient(HERMES_MIN_CONTEXT + 1) is True

    def test_below_default_hermes_floor(self):
        assert is_context_sufficient(HERMES_MIN_CONTEXT - 1) is False

    def test_custom_requirement(self):
        assert is_context_sufficient(100, required_tokens=50) is True
        assert is_context_sufficient(49, required_tokens=50) is False

    def test_missing_or_zero_context_is_insufficient(self):
        # Callers pass `effective_context or 0` when the runtime/context
        # value may be absent — the result must be a plain False.
        assert is_context_sufficient(0) is False
        assert is_context_sufficient(-1) is False

    def test_rejects_non_positive_requirement(self):
        with pytest.raises(ValueError):
            is_context_sufficient(65536, required_tokens=0)

    def test_rejects_non_integer_types(self):
        with pytest.raises(TypeError):
            is_context_sufficient("65536")  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            is_context_sufficient(None)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            is_context_sufficient(True)  # type: ignore[arg-type]
