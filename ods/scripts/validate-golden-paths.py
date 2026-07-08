#!/usr/bin/env python3
"""Validate ODS golden-path release contracts."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "config" / "golden-paths.json"
VALID_STATUSES = {"golden"}
VALID_PLATFORMS = {"linux", "windows-wsl2", "macos"}


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
        print("[FAIL] golden path validation")
        for item in self.items:
            print(f"  - {item}")
        raise SystemExit(1)


def as_dict(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def as_list(value: Any) -> list[Any] | None:
    return value if isinstance(value, list) else None


def nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_string_list(issues: Issues, value: Any, path: str) -> list[str]:
    if not isinstance(value, list):
        issues.add(path, "must be an array")
        return []
    result: list[str] = []
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if not nonempty_string(item):
            issues.add(item_path, "must be a non-empty string")
            continue
        result.append(item)
    issues.require(len(result) == len(set(result)), path, "must not contain duplicates")
    return result


def validate_service_ids(issues: Issues, service_ids: list[str], path: str) -> None:
    services_root = ROOT / "extensions" / "services"
    for service_id in service_ids:
        manifest = services_root / service_id / "manifest.yaml"
        issues.require(manifest.exists(), path, f"unknown service id '{service_id}'")


def validate_health_checks(issues: Issues, value: Any, path: str) -> None:
    checks = as_list(value)
    if checks is None:
        issues.add(path, "must be an array")
        return
    issues.require(bool(checks), path, "must include at least one health check")
    for index, check_value in enumerate(checks):
        check_path = f"{path}[{index}]"
        check = as_dict(check_value)
        if check is None:
            issues.add(check_path, "must be an object")
            continue
        service = check.get("service")
        url = check.get("url")
        issues.require(nonempty_string(service), f"{check_path}.service", "must be a non-empty string")
        issues.require(nonempty_string(url), f"{check_path}.url", "must be a non-empty string")
        if nonempty_string(service):
            validate_service_ids(issues, [service], f"{check_path}.service")
        if nonempty_string(url):
            issues.require(url.startswith("http://127.0.0.1:"), f"{check_path}.url", "must be localhost HTTP")


def validate_generated_configs(issues: Issues, value: Any, path: str) -> None:
    configs = as_list(value)
    if configs is None:
        issues.add(path, "must be an array")
        return
    surfaces: list[str] = []
    for index, config_value in enumerate(configs):
        config_path = f"{path}[{index}]"
        config = as_dict(config_value)
        if config is None:
            issues.add(config_path, "must be an object")
            continue
        surface = config.get("surface")
        target = config.get("path")
        issues.require(nonempty_string(surface), f"{config_path}.surface", "must be a non-empty string")
        issues.require(nonempty_string(target), f"{config_path}.path", "must be a non-empty string")
        if nonempty_string(surface):
            surfaces.append(surface)
    for required in ("env", "opencode", "hermes", "perplexica"):
        issues.require(required in surfaces, path, f"missing generated config surface '{required}'")


def validate_scenario(issues: Issues, scenario: Any, path: str) -> str | None:
    obj = as_dict(scenario)
    if obj is None:
        issues.add(path, "must be an object")
        return None

    scenario_id = obj.get("id")
    issues.require(nonempty_string(scenario_id), f"{path}.id", "must be a non-empty string")
    issues.require(nonempty_string(obj.get("label")), f"{path}.label", "must be a non-empty string")
    issues.require(obj.get("status") in VALID_STATUSES, f"{path}.status", f"must be one of {sorted(VALID_STATUSES)}")
    issues.require(obj.get("platform") in VALID_PLATFORMS, f"{path}.platform", f"must be one of {sorted(VALID_PLATFORMS)}")
    issues.require(nonempty_string(obj.get("architecture")), f"{path}.architecture", "must be a non-empty string")

    hardware = as_dict(obj.get("hardware"))
    if hardware is None:
        issues.add(f"{path}.hardware", "must be an object")
    else:
        issues.require(nonempty_string(hardware.get("gpu_backend")), f"{path}.hardware.gpu_backend", "must be a non-empty string")
        issues.require(nonempty_string(hardware.get("accelerator")), f"{path}.hardware.accelerator", "must be a non-empty string")

    installer = as_dict(obj.get("installer"))
    if installer is None:
        issues.add(f"{path}.installer", "must be an object")
    else:
        entrypoint = installer.get("entrypoint")
        issues.require(nonempty_string(entrypoint), f"{path}.installer.entrypoint", "must be a non-empty string")
        if nonempty_string(entrypoint):
            issues.require((ROOT / entrypoint).exists(), f"{path}.installer.entrypoint", f"file does not exist: {entrypoint}")
        issues.require(nonempty_string(installer.get("mode")), f"{path}.installer.mode", "must be a non-empty string")
        issues.require(nonempty_string(installer.get("ci_simulation")), f"{path}.installer.ci_simulation", "must be a non-empty string")
        validate_string_list(issues, installer.get("dry_run_args"), f"{path}.installer.dry_run_args")

    expected = as_dict(obj.get("expected"))
    if expected is None:
        issues.add(f"{path}.expected", "must be an object")
        return scenario_id if isinstance(scenario_id, str) else None

    issues.require(nonempty_string(expected.get("ods_mode")), f"{path}.expected.ods_mode", "must be a non-empty string")
    issues.require(nonempty_string(expected.get("llm_backend")), f"{path}.expected.llm_backend", "must be a non-empty string")
    for port_key in ("llm_host_port", "llm_container_port"):
        port = expected.get(port_key)
        issues.require(isinstance(port, int) and not isinstance(port, bool) and 0 < port < 65536, f"{path}.expected.{port_key}", "must be a TCP port integer")

    compose_files = validate_string_list(issues, expected.get("compose_files"), f"{path}.expected.compose_files")
    for compose_file in compose_files:
        issues.require((ROOT / compose_file).exists(), f"{path}.expected.compose_files", f"compose file does not exist: {compose_file}")

    core_services = validate_string_list(issues, expected.get("core_services"), f"{path}.expected.core_services")
    recommended_services = validate_string_list(issues, expected.get("recommended_services"), f"{path}.expected.recommended_services")
    validate_service_ids(issues, core_services, f"{path}.expected.core_services")
    validate_service_ids(issues, recommended_services, f"{path}.expected.recommended_services")

    model_route = as_dict(expected.get("model_route"))
    if model_route is None:
        issues.add(f"{path}.expected.model_route", "must be an object")
    else:
        for key in ("host_base_url", "container_base_url"):
            issues.require(nonempty_string(model_route.get(key)), f"{path}.expected.model_route.{key}", "must be a non-empty string")

    validate_generated_configs(issues, expected.get("generated_configs"), f"{path}.expected.generated_configs")
    validate_health_checks(issues, expected.get("health_checks"), f"{path}.expected.health_checks")

    return scenario_id if isinstance(scenario_id, str) else None


def main(argv: list[str]) -> int:
    path = Path(argv[0]) if argv else DEFAULT_PATH
    if not path.exists():
        print(f"[FAIL] golden path file not found: {path}")
        return 1
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"[FAIL] invalid JSON in {path}: {exc}")
        return 1

    issues = Issues()
    root = as_dict(data)
    if root is None:
        issues.add("$", "must be an object")
        issues.exit_if_any()
        return 1

    issues.require(root.get("version") == 1, "$.version", "must be 1")
    scenarios = as_list(root.get("scenarios"))
    if scenarios is None:
        issues.add("$.scenarios", "must be an array")
    else:
        issues.require(len(scenarios) == 4, "$.scenarios", "must contain exactly the four golden paths")
        seen: list[str] = []
        for index, scenario in enumerate(scenarios):
            scenario_id = validate_scenario(issues, scenario, f"$.scenarios[{index}]")
            if scenario_id:
                seen.append(scenario_id)
        issues.require(len(seen) == len(set(seen)), "$.scenarios", "scenario ids must be unique")
        expected_ids = {"linux-nvidia", "windows-wsl2-nvidia", "windows-wsl2-amd-lemonade", "apple-silicon"}
        issues.require(set(seen) == expected_ids, "$.scenarios", f"must define exactly {sorted(expected_ids)}")

    issues.exit_if_any()
    print("[PASS] golden path contracts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
