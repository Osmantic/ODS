#!/usr/bin/env python3
"""Validate generated runtime config contracts."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "config" / "generated-config-contracts.json"
VALID_INVARIANTS = {"file_contains", "yaml_text_contains", "json_path_enum_contains"}


class Issues:
    def __init__(self) -> None:
        self.items: list[str] = []

    def add(self, path: str, message: str) -> None:
        self.items.append(f"{path}: {message}")

    def require(self, condition: bool, path: str, message: str) -> None:
        if not condition:
            self.add(path, message)

    def exit_if_any(self) -> None:
        if not self.items:
            return
        print("[FAIL] generated config contract validation")
        for item in self.items:
            print(f"  - {item}")
        raise SystemExit(1)


def nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_json_path(data: Any, dotted_path: str) -> Any:
    current = data
    for part in dotted_path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            raise KeyError(dotted_path)
    return current


def validate_string_list(issues: Issues, value: Any, path: str) -> list[str]:
    if not isinstance(value, list):
        issues.add(path, "must be an array")
        return []
    result: list[str] = []
    for index, item in enumerate(value):
        if not nonempty_string(item):
            issues.add(f"{path}[{index}]", "must be a non-empty string")
            continue
        result.append(item)
    issues.require(len(result) == len(set(result)), path, "must not contain duplicates")
    return result


def validate_writers(issues: Issues, value: Any, path: str) -> None:
    if not isinstance(value, list) or not value:
        issues.add(path, "must be a non-empty array")
        return
    platforms: list[str] = []
    for index, writer in enumerate(value):
        writer_path = f"{path}[{index}]"
        if not isinstance(writer, dict):
            issues.add(writer_path, "must be an object")
            continue
        platform = writer.get("platform")
        target = writer.get("path")
        issues.require(nonempty_string(platform), f"{writer_path}.platform", "must be a non-empty string")
        issues.require(nonempty_string(target), f"{writer_path}.path", "must be a non-empty string")
        if nonempty_string(platform):
            platforms.append(platform)
        if nonempty_string(target):
            issues.require((ROOT / target).exists(), f"{writer_path}.path", f"file does not exist: {target}")


def validate_invariant(issues: Issues, invariant: Any, path: str) -> None:
    if not isinstance(invariant, dict):
        issues.add(path, "must be an object")
        return
    invariant_id = invariant.get("id")
    invariant_type = invariant.get("type")
    target = invariant.get("path")
    issues.require(nonempty_string(invariant_id), f"{path}.id", "must be a non-empty string")
    issues.require(invariant_type in VALID_INVARIANTS, f"{path}.type", f"must be one of {sorted(VALID_INVARIANTS)}")
    issues.require(nonempty_string(target), f"{path}.path", "must be a non-empty string")
    if not nonempty_string(target):
        return

    target_path = ROOT / str(target)
    issues.require(target_path.exists(), f"{path}.path", f"file does not exist: {target}")
    if not target_path.exists():
        return

    if invariant_type in {"file_contains", "yaml_text_contains"}:
        needle = invariant.get("needle")
        issues.require(nonempty_string(needle), f"{path}.needle", "must be a non-empty string")
        if nonempty_string(needle):
            text = target_path.read_text(encoding="utf-8", errors="replace")
            issues.require(str(needle) in text, path, f"{target} does not contain required text {needle!r}")
    elif invariant_type == "json_path_enum_contains":
        json_path = invariant.get("json_path")
        values = validate_string_list(issues, invariant.get("values"), f"{path}.values")
        issues.require(nonempty_string(json_path), f"{path}.json_path", "must be a non-empty string")
        if nonempty_string(json_path):
            try:
                enum_value = resolve_json_path(load_json(target_path), str(json_path))
            except Exception as exc:  # noqa: BLE001 - contract validator should report cleanly
                issues.add(f"{path}.json_path", f"could not resolve {json_path!r}: {exc}")
                return
            issues.require(isinstance(enum_value, list), f"{path}.json_path", "must resolve to an array")
            if isinstance(enum_value, list):
                for value in values:
                    issues.require(value in enum_value, path, f"{target} {json_path} missing {value!r}")


def validate_surface(issues: Issues, surface: Any, path: str) -> str | None:
    if not isinstance(surface, dict):
        issues.add(path, "must be an object")
        return None
    surface_id = surface.get("id")
    issues.require(nonempty_string(surface_id), f"{path}.id", "must be a non-empty string")
    issues.require(nonempty_string(surface.get("label")), f"{path}.label", "must be a non-empty string")
    validate_string_list(issues, surface.get("target_paths"), f"{path}.target_paths")
    validate_writers(issues, surface.get("writers"), f"{path}.writers")

    invariants = surface.get("invariants")
    if not isinstance(invariants, list) or not invariants:
        issues.add(f"{path}.invariants", "must be a non-empty array")
    else:
        invariant_ids: list[str] = []
        for index, invariant in enumerate(invariants):
            if isinstance(invariant, dict) and nonempty_string(invariant.get("id")):
                invariant_ids.append(str(invariant["id"]))
            validate_invariant(issues, invariant, f"{path}.invariants[{index}]")
        issues.require(len(invariant_ids) == len(set(invariant_ids)), f"{path}.invariants", "ids must be unique")

    return surface_id if isinstance(surface_id, str) else None


def main(argv: list[str]) -> int:
    path = Path(argv[0]) if argv else DEFAULT_PATH
    if not path.exists():
        print(f"[FAIL] generated config contract file not found: {path}")
        return 1
    try:
        data = load_json(path)
    except json.JSONDecodeError as exc:
        print(f"[FAIL] invalid JSON in {path}: {exc}")
        return 1

    issues = Issues()
    if not isinstance(data, dict):
        issues.add("$", "must be an object")
        issues.exit_if_any()
        return 1
    issues.require(data.get("version") == 1, "$.version", "must be 1")
    surfaces = data.get("surfaces")
    if not isinstance(surfaces, list):
        issues.add("$.surfaces", "must be an array")
    else:
        surface_ids: list[str] = []
        for index, surface in enumerate(surfaces):
            surface_id = validate_surface(issues, surface, f"$.surfaces[{index}]")
            if surface_id:
                surface_ids.append(surface_id)
        required = {"env", "opencode", "litellm-lemonade", "perplexica", "hermes"}
        issues.require(set(surface_ids) == required, "$.surfaces", f"must define exactly {sorted(required)}")

    issues.exit_if_any()
    print("[PASS] generated config contracts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
