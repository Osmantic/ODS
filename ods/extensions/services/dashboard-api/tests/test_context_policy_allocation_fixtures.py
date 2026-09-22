import pytest
from pathlib import Path
import sys

dashboard_api_dir = Path(__file__).resolve().parents[1]
if str(dashboard_api_dir) not in sys.path:
    sys.path.insert(0, str(dashboard_api_dir))

from context_policy import (
    HERMES_MIN_CONTEXT,
    HERMES_TARGET_CONTEXT,
    PIXEL_MIN_CONTEXT,
    clamp_context,
    is_context_sufficient,
)

def test_clamp_context_within_bounds():
    assert clamp_context(32768) == 32768

def test_clamp_context_below_minimum():
    assert clamp_context(1024) == PIXEL_MIN_CONTEXT
    assert clamp_context(-50) == PIXEL_MIN_CONTEXT

def test_clamp_context_above_maximum():
    assert clamp_context(200000) == HERMES_TARGET_CONTEXT

def test_is_context_sufficient():
    assert is_context_sufficient(HERMES_MIN_CONTEXT) is True
    assert is_context_sufficient(HERMES_MIN_CONTEXT + 1000) is True
    assert is_context_sufficient(HERMES_MIN_CONTEXT - 1) is False
    assert is_context_sufficient(0) is False
