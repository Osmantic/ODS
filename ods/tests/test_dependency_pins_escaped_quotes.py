#!/usr/bin/env python3
"""Regression test: check-dependency-pins preserves escaped quotes and inline comments."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check-dependency-pins.py"


def load_module():
    spec = importlib.util.spec_from_file_location("check_dependency_pins", SCRIPT)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_strip_inline_comment_with_escaped_quotes():
    module = load_module()
    strip_comment = module._strip_inline_comment

    # 1. Escaped double quote inside double-quoted string containing a '#' character
    line_with_escaped = r'ARG IMAGE="quay.io/vendor/image:tag\"#build-arg"'
    stripped = strip_comment(line_with_escaped)
    assert stripped == line_with_escaped, f"Expected {line_with_escaped!r}, got {stripped!r}"

    # 2. Escaped single quote inside single-quoted string containing a '#' character
    line_single_escaped = r"ARG IMAGE='quay.io/vendor/image:tag\'#build-arg'"
    stripped_single = strip_comment(line_single_escaped)
    assert stripped_single == line_single_escaped, f"Expected {line_single_escaped!r}, got {stripped_single!r}"

    # 3. Real unquoted trailing comment must still be stripped cleanly
    line_with_real_comment = 'image: "quay.io/vendor/image:1.0" # production pin'
    stripped_comment = strip_comment(line_with_real_comment)
    assert stripped_comment.rstrip() == 'image: "quay.io/vendor/image:1.0"'


if __name__ == "__main__":
    test_strip_inline_comment_with_escaped_quotes()
    print("test_dependency_pins_escaped_quotes: OK")
