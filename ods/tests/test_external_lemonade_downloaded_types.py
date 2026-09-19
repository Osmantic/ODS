#!/usr/bin/env python3
"""Regression test: external Lemonade selector handles string booleans and comma labels."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELECTOR = ROOT / "scripts" / "select-external-lemonade-model.py"


def run_selector(models: list[dict[str, object]]) -> subprocess.CompletedProcess[str]:
    payload = json.dumps({"object": "list", "data": models})
    return subprocess.run(
        [sys.executable, str(SELECTOR)],
        input=payload,
        text=True,
        capture_output=True,
        check=False,
    )


def test_string_boolean_and_numeric_downloaded_state():
    # External APIs or proxies can serialize boolean flags as string "true" or int 1
    models = [
        {
            "id": "qwen2.5-7b-instruct",
            "downloaded": "true",
            "labels": "tool-calling, reasoning",
            "size": 4.5,
        },
        {
            "id": "small-chat",
            "downloaded": 1,
            "labels": ["chat"],
            "size": 1.0,
        },
        {
            "id": "undownloaded-big",
            "downloaded": "false",
            "labels": ["tool-calling"],
            "size": 30.0,
        },
    ]
    res = run_selector(models)
    assert res.returncode == 0, f"selector failed: {res.stderr}"
    assert res.stdout.strip() == "qwen2.5-7b-instruct"


def test_comma_delimited_labels_enable_tool_calling_boost():
    # Comma-separated label strings must be parsed to detect tool-calling
    models = [
        {
            "id": "base-model",
            "downloaded": True,
            "labels": "chat",
            "size": 10.0,
        },
        {
            "id": "agentic-model",
            "downloaded": True,
            "labels": "tool-calling, reasoning",
            "size": 5.0,
        },
    ]
    res = run_selector(models)
    assert res.returncode == 0, f"selector failed: {res.stderr}"
    # Tool-calling model should win despite smaller parameter/size footprint
    assert res.stdout.strip() == "agentic-model"


if __name__ == "__main__":
    test_string_boolean_and_numeric_downloaded_state()
    test_comma_delimited_labels_enable_tool_calling_boost()
    print("test_external_lemonade_downloaded_types: OK")
