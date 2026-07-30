"""Fallback pricing lookup contracts for estimate_cost."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from uuid import uuid4

import pytest


TOKEN_SPY_DIR = Path(__file__).resolve().parent.parent

pytest.importorskip("fastapi")


@pytest.fixture(scope="module")
def token_spy_main(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("token-spy-data")
    # main.py mints and writes an API key at import when none is configured.
    import os

    os.environ.setdefault("TOKEN_SPY_API_KEY", "test-key")
    os.environ.setdefault("DB_PATH", str(data_dir / "usage.db"))
    sys.path.insert(0, str(TOKEN_SPY_DIR))
    try:
        spec = importlib.util.spec_from_file_location(
            f"token_spy_main_{uuid4().hex}", TOKEN_SPY_DIR / "main.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(TOKEN_SPY_DIR))


def _input_rate(module, model):
    """USD for one million input tokens on the hardcoded fallback table."""
    return module.estimate_cost(model, 1_000_000, 0, 0, 0, "no-such-provider")


def test_fallback_prefers_the_longest_matching_prefix(token_spy_main):
    # gpt-4o is a substring of gpt-4o-mini and is declared first; matching on
    # declaration order would bill mini traffic at the full gpt-4o rate.
    assert _input_rate(token_spy_main, "gpt-4o-mini") == pytest.approx(0.15)
    assert _input_rate(token_spy_main, "gpt-4o") == pytest.approx(2.50)


def test_fallback_table_has_no_shadowed_entries(token_spy_main):
    """Every prefix must be reachable, whatever order the table is written in."""
    table = token_spy_main.COST_PER_MILLION
    for prefix in table:
        cost = _input_rate(token_spy_main, prefix)
        assert cost == pytest.approx(table[prefix]["input"]), prefix


def test_unknown_model_is_free(token_spy_main):
    assert _input_rate(token_spy_main, "some-unlisted-model") == 0.0
    assert _input_rate(token_spy_main, "") == 0.0
