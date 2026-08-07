"""Pricing lookup contracts for the Token Spy providers.

Token Spy exists to tell an operator what their agents cost. A model id that
matches no pricing row is silently billed at zero, which looks exactly like
"this was free" — the one failure mode the service must not have.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from uuid import uuid4

import pytest


TOKEN_SPY_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOKEN_SPY_DIR))


def _load(relative: str):
    path = TOKEN_SPY_DIR / relative
    spec = importlib.util.spec_from_file_location(f"ts_{uuid4().hex}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def anthropic():
    from providers.anthropic import AnthropicProvider

    return AnthropicProvider()


# Every Claude model id Anthropic has shipped, in the spelling the API uses.
# Generation-first for 3.x, family-first from 4 on.
CLAUDE_IDS = [
    "claude-opus-4-6-20260115",
    "claude-opus-4-5-20251101",
    "claude-opus-4-1-20250805",
    "claude-opus-4-20250514",
    "claude-sonnet-4-5-20250929",
    "claude-sonnet-4-20250514",
    "claude-haiku-4-5-20251001",
    "claude-3-7-sonnet-20250219",
    "claude-3-5-sonnet-20241022",
    "claude-3-5-sonnet-20240620",
    "claude-3-5-haiku-20241022",
    "claude-3-opus-20240229",
    "claude-3-haiku-20240307",
]


@pytest.mark.parametrize("model", CLAUDE_IDS)
def test_every_claude_model_has_a_price(anthropic, model):
    """A priced model billed at zero is indistinguishable from a free one."""
    pricing = anthropic.get_model_pricing(model)
    assert pricing["input"] > 0, f"{model} priced at zero input"
    assert pricing["output"] > 0, f"{model} priced at zero output"


@pytest.mark.parametrize("model", CLAUDE_IDS)
def test_output_costs_at_least_as_much_as_input(anthropic, model):
    """Sanity check against a transposed row."""
    pricing = anthropic.get_model_pricing(model)
    assert pricing["output"] >= pricing["input"], model


@pytest.mark.parametrize("model", CLAUDE_IDS)
def test_cache_read_is_cheaper_than_input(anthropic, model):
    """Cache reads are discounted; a row where they are not is a typo."""
    pricing = anthropic.get_model_pricing(model)
    assert pricing["cache_read"] < pricing["input"], model


# --- Both spellings must resolve to the same tier -------------------------


@pytest.mark.parametrize(
    "generation_first,family_first",
    [
        ("claude-3-5-haiku-20241022", "claude-haiku-3-5"),
        ("claude-3-5-sonnet-20241022", "claude-sonnet-3-5"),
        ("claude-3-opus-20240229", "claude-opus-3"),
        ("claude-3-haiku-20240307", "claude-haiku-3"),
    ],
)
def test_generation_first_ids_resolve(anthropic, generation_first, family_first):
    assert anthropic.normalize_model(generation_first).startswith(family_first)
    assert (
        anthropic.get_model_pricing(generation_first)
        == anthropic.COST_TABLE[family_first]
    )


def test_family_first_ids_are_untouched(anthropic):
    """Claude 4+ ids already match; normalisation must not disturb them."""
    for model in ("claude-opus-4-5-20251101", "claude-haiku-4-5-20251001"):
        assert anthropic.normalize_model(model) == model


def test_haiku_3_is_not_priced_as_haiku_3_5(anthropic):
    """The bare `claude-haiku` catch-all must not swallow Haiku 3."""
    haiku_3 = anthropic.get_model_pricing("claude-3-haiku-20240307")
    haiku_35 = anthropic.get_model_pricing("claude-3-5-haiku-20241022")
    assert haiku_3["input"] < haiku_35["input"]


# --- Misses stay misses ----------------------------------------------------


@pytest.mark.parametrize("model", ["gpt-4o", "llama-3.1-70b", "", "claude"])
def test_non_claude_models_are_unpriced(anthropic, model):
    """Normalisation must not start matching things it should not."""
    assert anthropic.get_model_pricing(model)["input"] == 0.0


def test_cost_is_computed_from_the_matched_row(anthropic):
    """calculate_cost must use the row the lookup returned."""
    usage = {
        "input_tokens": 1_000_000,
        "output_tokens": 1_000_000,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }
    pricing = anthropic.get_model_pricing("claude-3-5-sonnet-20241022")
    cost = anthropic.calculate_cost(usage, "claude-3-5-sonnet-20241022")
    assert cost == pytest.approx(pricing["input"] + pricing["output"])
    assert cost > 0


def test_unpriced_model_costs_zero(anthropic):
    usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
    assert anthropic.calculate_cost(usage, "some-unknown-model") == 0.0


# --- The OpenAI-compatible table is unaffected -----------------------------


def test_openai_table_still_resolves():
    from providers.openai import OpenAICompatibleProvider

    provider = OpenAICompatibleProvider()
    for model in ("gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "o1", "kimi-k2"):
        assert provider.get_model_pricing(model)["input"] > 0, model
