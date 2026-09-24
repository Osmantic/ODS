from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "patch-hermes-config.py"


@pytest.mark.parametrize(
    "option,value",
    [
        ("--context-length", "0"),
        ("--context-length", "-1"),
        ("--request-timeout-seconds", "0"),
        ("--max-tokens", "-8"),
    ],
)
def test_cli_rejects_non_positive_runtime_limits(tmp_path, option, value):
    config = tmp_path / "config.yaml"
    config.write_text("model:\n  default: test\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(config), option, value],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert f"argument {option}: must be a positive integer" in result.stderr
    assert config.read_text(encoding="utf-8") == "model:\n  default: test\n"


def test_cli_accepts_positive_runtime_limits(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("model:\n  default: test\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(config),
            "--context-length",
            "4096",
            "--request-timeout-seconds",
            "300",
            "--max-tokens",
            "512",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    rendered = config.read_text(encoding="utf-8")
    assert "context_length: 4096" in rendered
    assert "request_timeout_seconds: 300" in rendered
    assert "max_tokens: 512" in rendered
