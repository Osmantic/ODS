"""Token Spy system-prompt breakdown tests.

analyze_system_prompt measures each workspace file from its own marker up to
the next KNOWN marker, so the marker table is a section-boundary table, not
just a list of metrics. A file missing from it silently folds its whole body
into the preceding file's bucket.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from uuid import uuid4

import pytest

TOKEN_SPY_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def token_spy_main():
    sys.path.insert(0, str(TOKEN_SPY_DIR))
    spec = importlib.util.spec_from_file_location(
        f"token_spy_main_{uuid4().hex}", TOKEN_SPY_DIR / "main.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _project_context(*sections: tuple[str, str]) -> list[dict]:
    """Render the workspace injection shape: '## FILE.md\\n\\n<body>\\n\\n'."""
    text = "# Project Context\n\n"
    for name, body in sections:
        text += f"## {name}\n{body}\n"
    text += "## Runtime\n\nruntime notes\n"
    return [{"type": "text", "text": text}]


def test_every_marker_gets_its_own_bucket(token_spy_main):
    soul = "s" * 400
    memory = "m" * 900
    heartbeat = "h" * 200

    result = token_spy_main.analyze_system_prompt(_project_context(
        ("SOUL.md", soul),
        ("MEMORY.md", memory),
        ("HEARTBEAT.md", heartbeat),
    ))

    # A missing marker is not a missing metric — it is a missing boundary, so
    # SOUL.md would otherwise absorb the whole MEMORY.md body as well.
    assert result["workspace_soul_chars"] == len(soul) + 1
    assert result["workspace_memory_chars"] == len(memory) + 1
    assert result["workspace_heartbeat_chars"] == len(heartbeat) + 1


def test_marker_table_matches_the_provider_breakdown(token_spy_main):
    """The proxy route and the provider abstraction must agree on the set.

    They feed the same usage columns, so a file counted by one and not the
    other reports different numbers for the same prompt depending on which
    path served the request.
    """
    # Imported through the package: anthropic.py uses relative imports.
    sys.path.insert(0, str(TOKEN_SPY_DIR))
    module = importlib.import_module("providers.anthropic")

    assert (token_spy_main.WORKSPACE_FILE_MAP
            == module.AnthropicProvider.WORKSPACE_FILE_MAP)


def test_base_prompt_excludes_every_workspace_bucket(token_spy_main):
    """Whatever the markers account for must come off the base prompt."""
    blocks = _project_context(("MEMORY.md", "m" * 500))
    result = token_spy_main.analyze_system_prompt(blocks)

    accounted = sum(
        value for key, value in result.items()
        if key.startswith("workspace_") or key == "skill_injection_chars"
    )
    assert result["base_prompt_chars"] == (
        result["system_prompt_total_chars"] - accounted
    )
    assert result["workspace_memory_chars"] > 0
