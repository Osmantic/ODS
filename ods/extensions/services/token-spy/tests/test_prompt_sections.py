"""Prompt section filtering must distinguish headings from code examples."""

import importlib.util
import sys
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "prompt_section_filters", Path(__file__).resolve().parents[1] / "filters.py"
)
filters = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = filters
_spec.loader.exec_module(filters)


def trim_prompt(text, headings=("## Heartbeats",)):
    body = {"messages": [{"role": "system", "content": text}]}
    return filters.apply_filters(body, {
        "enabled": True,
        "system_prompt": {
            "enabled": True, "mode": "strip_sections", "strip_sections": list(headings),
        },
    })


@pytest.mark.parametrize("fence", ["```", "~~~~", "  ```"])
def test_keeps_matching_heading_inside_code_example(fence):
    text = f"Instructions\n{fence}\n## Heartbeats\nexample content\n{fence}\nKeep this.\n"
    body, result = trim_prompt(text)
    assert body["messages"][0]["content"] == text
    assert result.system_sections_stripped == []
    assert result.chars_saved == 0


@pytest.mark.parametrize("fence", ["```python", "~~~python"])
def test_code_heading_cannot_end_removed_section(fence):
    closing = fence[:3]
    text = f"Keep\n## Heartbeats\nDiscard\n{fence}\n# Example\nDiscard too\n{closing}\n## Tools\nKeep tools\n"
    body, result = trim_prompt(text)
    assert body["messages"][0]["content"] == "Keep\n## Tools\nKeep tools\n"
    assert result.system_sections_stripped == ["## Heartbeats"]


def test_shorter_or_different_fence_cannot_end_code_example():
    text = "Before\n````text\n```\n~~~\n## Heartbeats\nExample\n````\nAfter\n"
    body, _ = trim_prompt(text)
    assert body["messages"][0]["content"] == text


def test_removes_last_heading_without_final_newline():
    body, result = trim_prompt("Keep\n## Heartbeats")
    assert body["messages"][0]["content"] == "Keep\n"
    assert result.system_sections_stripped == ["## Heartbeats"]


def test_keeps_crlf_and_unselected_sections_verbatim():
    text = "# Root\r\nKeep\r\n## Heartbeats\r\nDrop\r\n### Nested\r\nDrop\r\n## Tools\r\nKeep\r\n"
    body, _ = trim_prompt(text)
    assert body["messages"][0]["content"] == "# Root\r\nKeep\r\n## Tools\r\nKeep\r\n"


def test_removes_repeated_sections_and_reports_each_selected_heading_once():
    body, result = trim_prompt(
        "## Heartbeats\nDrop\n## Tools\nKeep\n## Heartbeats\nDrop\n# End\nKeep",
    )
    assert body["messages"][0]["content"] == "## Tools\nKeep\n# End\nKeep"
    assert result.system_sections_stripped == ["## Heartbeats"]


def test_unicode_line_separator_does_not_start_a_markdown_heading():
    text = "Literal separator: \u2028## Heartbeats\nKeep this.\n"
    body, _ = trim_prompt(text)
    assert body["messages"][0]["content"] == text


def test_empty_heading_ends_removed_section():
    body, _ = trim_prompt("## Heartbeats\nDrop\n##\nKeep\n")
    assert body["messages"][0]["content"] == "##\nKeep\n"
