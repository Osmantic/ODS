"""System-prompt breakdown contracts for analyze_system_prompt."""

from __future__ import annotations

import importlib.util
import os
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
    os.environ.setdefault("TOKEN_SPY_API_KEY", "test-key")
    os.environ.setdefault("DB_PATH", str(data_dir / "usage.db"))
    sys.path.insert(0, str(TOKEN_SPY_DIR))
    try:
        spec = importlib.util.spec_from_file_location(
            f"token_spy_main_prompt_{uuid4().hex}", TOKEN_SPY_DIR / "main.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(TOKEN_SPY_DIR))


SOUL_BODY = "Soul body B."
SKILLS_BODY = "- skill one\n- skill two"

PROMPT = f"""You are an agent.

# Project Context

## AGENTS.md

Agent rules body A.

## SOUL.md

{SOUL_BODY}

## Skills (mandatory)

{SKILLS_BODY}

## Heartbeats

heartbeat config
"""


def test_skills_block_is_not_folded_into_the_preceding_file(token_spy_main):
    # The skills block is measured on its own, so the workspace file above it
    # must stop at its heading. Running through it counted those characters in
    # both buckets and subtracted them twice from the base prompt.
    result = token_spy_main.analyze_system_prompt([{"text": PROMPT}])

    assert result["workspace_soul_chars"] < len(SOUL_BODY) + 5
    assert result["skill_injection_chars"] > len(SKILLS_BODY)
    assert result["workspace_soul_chars"] < result["skill_injection_chars"]


def test_breakdown_reconciles_with_the_total(token_spy_main):
    result = token_spy_main.analyze_system_prompt([{"text": PROMPT}])

    accounted = sum(
        value for key, value in result.items()
        if key.startswith("workspace_") or key == "skill_injection_chars"
    )
    assert accounted + result["base_prompt_chars"] == result["system_prompt_total_chars"]
    # A file's size must not depend on whether a skills block follows it.
    # It did while the file's span ran through the block.
    without_skills = token_spy_main.analyze_system_prompt([{
        "text": PROMPT.replace(f"## Skills (mandatory)\n\n{SKILLS_BODY}\n\n", ""),
    }])
    assert without_skills["workspace_soul_chars"] == result["workspace_soul_chars"]


def test_prompt_without_skills_section_is_unchanged(token_spy_main):
    prompt = PROMPT.replace(f"## Skills (mandatory)\n\n{SKILLS_BODY}\n\n", "")
    result = token_spy_main.analyze_system_prompt([{"text": prompt}])

    assert result["skill_injection_chars"] == 0
    accounted = sum(
        value for key, value in result.items()
        if key.startswith("workspace_") or key == "skill_injection_chars"
    )
    assert accounted + result["base_prompt_chars"] == result["system_prompt_total_chars"]


def test_empty_system_prompt(token_spy_main):
    assert token_spy_main.analyze_system_prompt([]) == {
        "system_prompt_total_chars": 0,
        "base_prompt_chars": 0,
    }
