"""Cached-token accounting contracts.

``LLMProvider.calculate_cost`` bills every ``*_tokens`` field independently:

    input_tokens * input + output_tokens * output
  + cache_read_tokens * cache_read + cache_write_tokens * cache_write

so the fields a provider reports must be disjoint. Anthropic's API already
returns them that way; OpenAI's ``prompt_tokens`` is a total that *includes*
``prompt_tokens_details.cached_tokens``, and passing it through unchanged bills
the cached half twice.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


TOKEN_SPY_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOKEN_SPY_DIR))


@pytest.fixture(scope="module")
def openai():
    from providers.openai import OpenAICompatibleProvider

    return OpenAICompatibleProvider()


@pytest.fixture(scope="module")
def anthropic():
    from providers.anthropic import AnthropicProvider

    return AnthropicProvider()


def _response(prompt_tokens, cached, completion=0):
    usage = {"prompt_tokens": prompt_tokens, "completion_tokens": completion}
    if cached is not None:
        usage["prompt_tokens_details"] = {"cached_tokens": cached}
    return {"usage": usage, "choices": [{"finish_reason": "stop"}]}


# --- the fields must not overlap ------------------------------------------


def test_cached_tokens_are_excluded_from_input(openai):
    usage = openai.extract_usage_from_response(_response(1000, 800))
    assert usage["input_tokens"] == 200
    assert usage["cache_read_tokens"] == 800


def test_input_plus_cache_reconstructs_the_reported_total(openai):
    usage = openai.extract_usage_from_response(_response(1234, 567))
    assert usage["input_tokens"] + usage["cache_read_tokens"] == 1234


def test_cost_matches_a_hand_computed_bill(openai):
    """1M prompt tokens, 90% cached, on gpt-4o."""
    usage = openai.extract_usage_from_response(_response(1_000_000, 900_000))
    rates = openai.get_model_pricing("gpt-4o")
    expected = (100_000 * rates["input"] + 900_000 * rates["cache_read"]) / 1_000_000
    assert openai.calculate_cost(usage, "gpt-4o") == pytest.approx(expected)


def test_stream_usage_excludes_cached_from_input(openai):
    line = "data: " + json.dumps(
        {
            "choices": [],
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 50,
                "prompt_tokens_details": {"cached_tokens": 800},
            },
        }
    )
    result = openai.extract_usage_from_stream(line)
    assert result["input_tokens"] == 200
    assert result["cache_read_tokens"] == 800
    assert result["output_tokens"] == 50


# --- the no-cache and malformed paths are unchanged ------------------------


def test_no_cache_details_reports_the_full_prompt_as_input(openai):
    usage = openai.extract_usage_from_response(_response(500, None))
    assert usage["input_tokens"] == 500
    assert usage["cache_read_tokens"] == 0


def test_zero_cached_tokens_reports_the_full_prompt_as_input(openai):
    usage = openai.extract_usage_from_response(_response(500, 0))
    assert usage["input_tokens"] == 500
    assert usage["cache_read_tokens"] == 0


def test_null_prompt_tokens_details_is_tolerated(openai):
    response = {"usage": {"prompt_tokens": 400, "prompt_tokens_details": None}, "choices": []}
    usage = openai.extract_usage_from_response(response)
    assert usage["input_tokens"] == 400
    assert usage["cache_read_tokens"] == 0


@pytest.mark.parametrize("cached", [1500, -5, "many", None])
def test_impossible_cached_counts_never_produce_negative_input(openai, cached):
    """A cache count larger than the total, or non-numeric, must not make the
    bill negative or crash the recorder."""
    usage = openai.extract_usage_from_response(_response(1000, cached))
    assert usage["input_tokens"] >= 0
    assert usage["cache_read_tokens"] >= 0
    assert usage["input_tokens"] + usage["cache_read_tokens"] <= 1000
    assert openai.calculate_cost(usage, "gpt-4o") >= 0


def test_missing_usage_block_is_tolerated(openai):
    usage = openai.extract_usage_from_response({"choices": []})
    assert usage["input_tokens"] == 0
    assert usage["cache_read_tokens"] == 0


# --- the two providers agree on the convention -----------------------------


def test_both_providers_report_disjoint_fields(openai, anthropic):
    """Same conversation shape through each provider's response format."""
    oai = openai.extract_usage_from_response(_response(1000, 900))
    ant = anthropic.extract_usage_from_response(
        {
            "usage": {
                "input_tokens": 100,
                "output_tokens": 0,
                "cache_read_input_tokens": 900,
                "cache_creation_input_tokens": 0,
            }
        }
    )
    assert oai["input_tokens"] == ant["input_tokens"] == 100
    assert oai["cache_read_tokens"] == ant["cache_read_tokens"] == 900
