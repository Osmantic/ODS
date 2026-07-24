#!/usr/bin/env python3
"""Unit tests for check-version-consistency.py error reporting.

The release gate must fail *cleanly* (collect an error string) when a version
authority is missing or lives outside ROOT, not crash with a raw traceback.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check-version-consistency.py"


def load_module():
    spec = importlib.util.spec_from_file_location("check_version_consistency", SCRIPT)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_first_match_missing_file_raises_valueerror() -> None:
    """A missing file must surface as ValueError (caught by add_regex_check),
    not FileNotFoundError, which would escape and crash the gate."""
    module = load_module()
    missing = module.ROOT / "does-not-exist-xyz.sh"
    try:
        module.first_match(missing, r'^VERSION="([^"]+)"', "missing file")
    except ValueError as exc:
        assert "missing file" in str(exc)
    except OSError as exc:  # pragma: no cover - this is the bug being guarded
        raise AssertionError(f"first_match leaked OSError instead of ValueError: {exc}")
    else:
        raise AssertionError("first_match did not raise for a missing file")


def test_add_regex_check_records_error_for_missing_file() -> None:
    """add_regex_check must append a clean error string, never propagate."""
    module = load_module()
    errors: list[str] = []
    checks: list[tuple[str, str]] = []
    module.add_regex_check(
        checks,
        errors,
        "missing constants",
        module.ROOT / "does-not-exist-xyz.sh",
        r'^VERSION="([^"]+)"',
    )
    assert checks == []
    assert len(errors) == 1
    assert "missing constants" in errors[0]


def test_display_path_tolerates_paths_outside_root() -> None:
    """ARCHITECTURE.md lives at ROOT.parent; the message helper must not raise
    when a checked file is outside ROOT."""
    module = load_module()
    outside = module.ROOT.parent / "ARCHITECTURE.md"
    # Must return a string rather than raising ValueError from relative_to.
    assert isinstance(module._display_path(outside), str)


def test_first_match_unmatched_pattern_outside_root_reports_cleanly() -> None:
    """A file outside ROOT whose pattern does not match must still yield a
    clean ValueError, not a relative_to() ValueError with a confusing message."""
    module = load_module()
    outside = module.ROOT.parent / "ARCHITECTURE.md"
    if not outside.exists():
        return  # nothing to assert if the outer file isn't checked out
    try:
        module.first_match(outside, r"^THIS_PATTERN_WILL_NOT_MATCH_ANYTHING=(x)$", "arch")
    except ValueError as exc:
        assert "could not find version" in str(exc)
        assert "arch" in str(exc)
    else:
        raise AssertionError("expected ValueError for an unmatched pattern")


def main() -> int:
    tests = [
        test_first_match_missing_file_raises_valueerror,
        test_add_regex_check_records_error_for_missing_file,
        test_display_path_tolerates_paths_outside_root,
        test_first_match_unmatched_pattern_outside_root_reports_cleanly,
    ]
    for test in tests:
        test()
    print("[PASS] version consistency error-reporting tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
