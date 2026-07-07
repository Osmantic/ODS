#!/usr/bin/env python3
"""ODS — installer simulation summary validator.

This script is a CI/automation gate for artifacts produced by:
  - scripts/simulate-installers.sh
  - scripts/ods-doctor.sh
  - scripts/preflight-engine.sh

Why this exists:
  The simulation step is meant to provide a single structured "snapshot" that
  can be validated without access to Docker, GPU drivers, or platform-specific
  tooling. Historically this validator was too shallow (only a few keys) and
  too brittle (failed fast without showing all issues).

Design goals (senior-grade guardrails):
  - Validate structure AND critical semantics (types, required subtrees)
  - Provide actionable error messages with JSON-path context
  - Aggregate errors (report all problems at once)
  - Work without third-party dependencies

Exit codes:
  0: PASS
  2: FAIL (validation errors)
  3: FAIL (unreadable/invalid JSON)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

Json = Union[None, bool, int, float, str, List["Json"], Dict[str, "Json"]]


# -----------------------------
# Error model / utilities
# -----------------------------


@dataclass(frozen=True)
class ValidationIssue:
    path: str
    message: str

    def format(self) -> str:
        return f"- {self.path}: {self.message}"


class Validator:
    def __init__(self, *, strict: bool = False) -> None:
        self.strict = strict
        self.issues: List[ValidationIssue] = []

    def add(self, path: str, message: str) -> None:
        self.issues.append(ValidationIssue(path=path, message=message))

    def fail_if_any(self) -> None:
        if not self.issues:
            return
        print("[FAIL] simulation summary validation")
        for issue in self.issues:
            print(issue.format())
        raise SystemExit(2)


def _type_name(v: Any) -> str:
    if v is None:
        return "null"
    return type(v).__name__


def _is_int(v: Any) -> bool:
    # bool is a subclass of int; exclude it explicitly.
    return isinstance(v, int) and not isinstance(v, bool)


def _as_mapping(v: Any) -> Optional[Mapping[str, Any]]:
    return v if isinstance(v, Mapping) else None


def _as_sequence(v: Any) -> Optional[Sequence[Any]]:
    return v if isinstance(v, Sequence) and not isinstance(v, (str, bytes, bytearray)) else None


def _require_key(v: Validator, obj: Mapping[str, Any], path: str, key: str) -> Any:
    if key not in obj:
        v.add(path, f"missing required key '{key}'")
        return None
    return obj[key]


def _require_type(v: Validator, value: Any, path: str, expected: str) -> None:
    ok = False
    if expected == "object":
        ok = isinstance(value, Mapping)
    elif expected == "array":
        ok = _as_sequence(value) is not None
    elif expected == "string":
        ok = isinstance(value, str)
    elif expected == "int":
        ok = _is_int(value)
    elif expected == "bool":
        ok = isinstance(value, bool)
    elif expected == "number":
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif expected == "null":
        ok = value is None

    if not ok:
        v.add(path, f"expected {expected}, got {_type_name(value)}")


def _optional_type(v: Validator, value: Any, path: str, expected: str) -> None:
    if value is None:
        return
    _require_type(v, value, path, expected)


def _require_one_of(v: Validator, value: Any, path: str, allowed: Sequence[str]) -> None:
    if not isinstance(value, str):
        v.add(path, f"expected string enum {list(allowed)}, got {_type_name(value)}")
        return
    if value not in allowed:
        v.add(path, f"invalid value '{value}', expected one of {list(allowed)}")


def _require_nonempty_string(v: Validator, value: Any, path: str) -> None:
    _require_type(v, value, path, "string")
    if isinstance(value, str) and not value.strip():
        v.add(path, "must be a non-empty string")


def _require_path_like(v: Validator, value: Any, path: str) -> None:
    # We accept absolute or relative paths, but we should not accept embedded NUL.
    _require_type(v, value, path, "string")
    if isinstance(value, str) and "\x00" in value:
        v.add(path, "path contains NUL byte")


def _require_iso8601ish(v: Validator, value: Any, path: str) -> None:
    # We intentionally avoid strict RFC3339 parsing to keep dependencies at 0.
    _require_type(v, value, path, "string")
    if isinstance(value, str):
        # Very lightweight check: 2026-03-15T12:34:56+00:00 / Z / with fractional seconds.
        if not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$", value):
            v.add(path, "expected ISO8601 timestamp (UTC or offset)")


# -----------------------------
# Schema validation
# -----------------------------


def validate_linux_dryrun(v: Validator, linux: Mapping[str, Any], path: str) -> None:
    exit_code = _require_key(v, linux, path, "exit_code")
    _require_type(v, exit_code, f"{path}.exit_code", "int")

    signals = _require_key(v, linux, path, "signals")
    _require_type(v, signals, f"{path}.signals", "object")

    log_path = _require_key(v, linux, path, "log")
    _require_path_like(v, log_path, f"{path}.log")

    install_summary = _require_key(v, linux, path, "install_summary")
    _require_type(v, install_summary, f"{path}.install_summary", "object")

    if isinstance(signals, Mapping):
        # Signals are boolean indicators from the linux dry-run log.
        required_signals = (
            "capability_loaded",
            "hardware_class_logged",
            "backend_contract_loaded",
            "preflight_report_logged",
            "compose_selection_logged",
        )
        for s in required_signals:
            if s not in signals:
                v.add(f"{path}.signals", f"missing required signal '{s}'")
            else:
                _require_type(v, signals.get(s), f"{path}.signals.{s}", "bool")


def validate_macos_installer(v: Validator, mac: Mapping[str, Any], path: str) -> None:
    exit_code = _require_key(v, mac, path, "exit_code")
    _require_type(v, exit_code, f"{path}.exit_code", "int")

    log_path = _require_key(v, mac, path, "log")
    _require_path_like(v, log_path, f"{path}.log")

    preflight = _require_key(v, mac, path, "preflight")
    _optional_type(v, preflight, f"{path}.preflight", "object")

    doctor = _require_key(v, mac, path, "doctor")
    _optional_type(v, doctor, f"{path}.doctor", "object")

    # If preflight exists, ensure it has a summary block.
    if isinstance(preflight, Mapping):
        summary = preflight.get("summary")
        _require_type(v, summary, f"{path}.preflight.summary", "object")
        if isinstance(summary, Mapping):
            blockers = summary.get("blockers")
            warnings = summary.get("warnings")
            _require_type(v, blockers, f"{path}.preflight.summary.blockers", "int")
            _require_type(v, warnings, f"{path}.preflight.summary.warnings", "int")


def validate_windows_scenario(v: Validator, win: Mapping[str, Any], path: str) -> None:
    report = _require_key(v, win, path, "report")
    _require_type(v, report, f"{path}.report", "object")
    if not isinstance(report, Mapping):
        return

    summary = report.get("summary")
    _require_type(v, summary, f"{path}.report.summary", "object")

    if isinstance(summary, Mapping):
        blockers = summary.get("blockers")
        warnings = summary.get("warnings")
        _require_type(v, blockers, f"{path}.report.summary.blockers", "int")
        _require_type(v, warnings, f"{path}.report.summary.warnings", "int")


def validate_doctor_snapshot(v: Validator, doctor: Mapping[str, Any], path: str) -> None:
    exit_code = _require_key(v, doctor, path, "exit_code")
    _require_type(v, exit_code, f"{path}.exit_code", "int")

    report = _require_key(v, doctor, path, "report")
    _require_type(v, report, f"{path}.report", "object")
    if not isinstance(report, Mapping):
        return

    # Historically we require autofix_hints (used by UX / troubleshooting flows)
    if "autofix_hints" not in report:
        v.add(f"{path}.report", "missing required key 'autofix_hints'")
    else:
        _require_type(v, report.get("autofix_hints"), f"{path}.report.autofix_hints", "array")

    # Newer doctor outputs include a summary block; validate if present.
    if "summary" in report:
        _require_type(v, report.get("summary"), f"{path}.report.summary", "object")
        summary = report.get("summary")
        if isinstance(summary, Mapping) and "runtime_ready" in summary:
            _require_type(v, summary.get("runtime_ready"), f"{path}.report.summary.runtime_ready", "bool")


def validate_golden_paths(v: Validator, golden: Mapping[str, Any], path: str) -> None:
    contract = _require_key(v, golden, path, "contract")
    _require_path_like(v, contract, f"{path}.contract")

    evidence = _require_key(v, golden, path, "evidence")
    _require_path_like(v, evidence, f"{path}.evidence")

    scenario_count = _require_key(v, golden, path, "scenario_count")
    _require_type(v, scenario_count, f"{path}.scenario_count", "int")

    pass_count = _require_key(v, golden, path, "pass_count")
    _require_type(v, pass_count, f"{path}.pass_count", "int")

    if _is_int(scenario_count) and scenario_count <= 0:
        v.add(f"{path}.scenario_count", "must be greater than zero")
    if _is_int(pass_count) and _is_int(scenario_count) and pass_count > scenario_count:
        v.add(f"{path}.pass_count", "must be less than or equal to scenario_count")


def validate_summary(v: Validator, data: Mapping[str, Any]) -> None:
    # version
    version = data.get("version")
    _require_nonempty_string(v, version, "$.version")
    if isinstance(version, str) and version != "1":
        v.add("$.version", "must be '1'")

    # generated_at
    if "generated_at" in data:
        _require_iso8601ish(v, data.get("generated_at"), "$.generated_at")
    elif v.strict:
        v.add("$", "missing required key 'generated_at' (strict mode)")

    # runs
    runs = data.get("runs")
    _require_type(v, runs, "$.runs", "object")
    if not isinstance(runs, Mapping):
        return

    required_runs = (
        "linux_dryrun",
        "macos_installer_mvp",
        "windows_scenario_preflight",
        "doctor_snapshot",
    )
    for key in required_runs:
        if key not in runs:
            v.add("$.runs", f"missing required run '{key}'")

    # Validate each known run (only if present)
    linux = runs.get("linux_dryrun")
    _require_type(v, linux, "$.runs.linux_dryrun", "object")
    if isinstance(linux, Mapping):
        validate_linux_dryrun(v, linux, "$.runs.linux_dryrun")

    mac = runs.get("macos_installer_mvp")
    _require_type(v, mac, "$.runs.macos_installer_mvp", "object")
    if isinstance(mac, Mapping):
        validate_macos_installer(v, mac, "$.runs.macos_installer_mvp")

    win = runs.get("windows_scenario_preflight")
    _require_type(v, win, "$.runs.windows_scenario_preflight", "object")
    if isinstance(win, Mapping):
        validate_windows_scenario(v, win, "$.runs.windows_scenario_preflight")

    doc = runs.get("doctor_snapshot")
    _require_type(v, doc, "$.runs.doctor_snapshot", "object")
    if isinstance(doc, Mapping):
        validate_doctor_snapshot(v, doc, "$.runs.doctor_snapshot")

    if "golden_paths" in data:
        _require_type(v, data.get("golden_paths"), "$.golden_paths", "object")
        golden = data.get("golden_paths")
        if isinstance(golden, Mapping):
            validate_golden_paths(v, golden, "$.golden_paths")

    # Strict mode: warn on unknown top-level keys
    if v.strict:
        allowed_top = {"version", "generated_at", "runs", "golden_paths"}
        for k in data.keys():
            if k not in allowed_top:
                v.add("$", f"unknown top-level key '{k}' (strict mode)")


# -----------------------------
# CLI
# -----------------------------


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="validate-sim-summary.py",
        description="Validate ODS installer simulation summary JSON.",
    )
    p.add_argument("summary_json", help="Path to summary.json")
    p.add_argument("--strict", action="store_true", help="Fail on unknown keys and require generated_at")
    return p.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = _parse_args(argv)

    path = Path(args.summary_json)
    if not path.exists():
        print(f"[FAIL] summary file not found: {path}")
        return 2

    try:
        raw = path.read_text(encoding="utf-8")
    except Exception as exc:
        print(f"[FAIL] cannot read summary file: {exc}")
        return 3

    try:
        data = json.loads(raw)
    except Exception as exc:
        print(f"[FAIL] invalid JSON: {exc}")
        return 3

    if not isinstance(data, Mapping):
        print(f"[FAIL] root must be an object, got {_type_name(data)}")
        return 2

    v = Validator(strict=bool(args.strict))
    validate_summary(v, data)
    v.fail_if_any()

    print("[PASS] simulation summary structure")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
