#!/usr/bin/env python3
"""Regression tests for universal healthcheck status parsing."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "healthcheck.py"


def load_module():
    spec = importlib.util.spec_from_file_location("ods_healthcheck", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    module = load_module()
    assert module._parse_expected_status("200,204,3xx,401-403") >= {200, 204, 399, 401, 403}

    for expression in ("99", "600", "0xx", "6xx", "1-999999999", "700-200"):
        try:
            module._parse_expected_status(expression)
        except ValueError as exc:
            assert "range" in str(exc)
        else:
            raise AssertionError(f"accepted invalid HTTP status expression: {expression}")

    assert module.main(
        ["http://127.0.0.1/health", "--expect-status", "1-999999999", "--json"]
    ) == 2
    print("[PASS] healthcheck status bounds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
