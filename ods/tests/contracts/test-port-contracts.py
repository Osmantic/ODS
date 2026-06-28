#!/usr/bin/env python3
"""Canonical port contract parity checks.

Validates config/ports.json against .env schema/example values, bundled
service manifests, and compose port mappings. This Python version is the
canonical implementation so Windows auditors do not need Bash to run the check.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

try:
    import yaml
except ModuleNotFoundError as exc:
    print(f"[FAIL] Missing Python dependency: {exc}")
    raise SystemExit(1)


ROOT_DIR = Path(__file__).resolve().parents[2]
PORTS_FILE = ROOT_DIR / "config" / "ports.json"
SCHEMA_FILE = ROOT_DIR / ".env.schema.json"
ENV_EXAMPLE_FILE = ROOT_DIR / ".env.example"
SERVICES_DIR = ROOT_DIR / "extensions" / "services"


def fail(message: str) -> None:
    print(f"[FAIL] {message}")
    raise SystemExit(1)


def load_manifest_map() -> dict[str, dict]:
    manifest_map: dict[str, dict] = {}
    for manifest_path in sorted(SERVICES_DIR.glob("*/manifest.yaml")):
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        service = manifest.get("service") or {}
        service_id = service.get("id")
        if service_id:
            manifest_map[service_id] = service
    return manifest_map


def parse_env_example_defaults() -> dict[str, int]:
    defaults: dict[str, int] = {}
    pattern = re.compile(r"^\s*#\s*([A-Z0-9_]+)=([0-9]+)\b")
    for line in ENV_EXAMPLE_FILE.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if match:
            defaults[match.group(1)] = int(match.group(2))
    return defaults


def collect_compose_port_map() -> dict[str, tuple[int, int, str]]:
    compose_pattern = re.compile(r"\$\{([A-Z0-9_]+):-([0-9]+)\}:([0-9]+)")
    compose_files = [
        ROOT_DIR / "docker-compose.base.yml",
        *sorted(SERVICES_DIR.glob("*/compose*.yaml")),
        *sorted(SERVICES_DIR.glob("*/compose*.yml")),
    ]
    compose_map: dict[str, tuple[int, int, str]] = {}
    for compose_path in compose_files:
        if not compose_path.exists():
            continue
        doc = yaml.safe_load(compose_path.read_text(encoding="utf-8")) or {}
        services = doc.get("services") or {}
        for service_cfg in services.values():
            ports = service_cfg.get("ports") or []
            for port_expr in ports:
                if not isinstance(port_expr, str):
                    continue
                match = compose_pattern.search(port_expr)
                if not match:
                    continue
                env_var = match.group(1)
                external_default = int(match.group(2))
                internal_port = int(match.group(3))
                current = compose_map.get(env_var)
                if current and (
                    current[0] != external_default or current[1] != internal_port
                ):
                    fail(
                        f"Inconsistent compose defaults for {env_var}: "
                        f"{current[0]}:{current[1]} vs "
                        f"{external_default}:{internal_port} ({compose_path})"
                    )
                compose_map[env_var] = (
                    external_default,
                    internal_port,
                    str(compose_path.relative_to(ROOT_DIR)),
                )
    return compose_map


def main() -> int:
    if not PORTS_FILE.exists():
        fail(f"Missing canonical port contract: {PORTS_FILE}")
    if not SCHEMA_FILE.exists():
        fail(f"Missing env schema: {SCHEMA_FILE}")
    if not ENV_EXAMPLE_FILE.exists():
        fail(f"Missing env example: {ENV_EXAMPLE_FILE}")
    if not SERVICES_DIR.exists():
        fail(f"Missing services directory: {SERVICES_DIR}")

    ports_contract = json.loads(PORTS_FILE.read_text(encoding="utf-8"))
    entries = ports_contract.get("ports")
    if not isinstance(entries, list) or not entries:
        fail("config/ports.json must define a non-empty 'ports' array")

    schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    schema_props = schema.get("properties", {})
    if not isinstance(schema_props, dict):
        fail(".env.schema.json properties must be an object")

    example_defaults = parse_env_example_defaults()
    manifest_map = load_manifest_map()
    compose_map = collect_compose_port_map()

    seen_env_vars: set[str] = set()
    for entry in entries:
        service_id = entry.get("service_id")
        env_var = entry.get("env_var")
        external_default = entry.get("external_default")
        internal_port = entry.get("internal_port")
        manifest_service = entry.get("manifest_service")
        compose_managed = entry.get("compose_managed", True)
        include_in_example = entry.get("include_in_example", True)

        if not isinstance(service_id, str) or not service_id:
            fail(f"Invalid service_id in ports contract entry: {entry}")
        if not isinstance(env_var, str) or not re.fullmatch(r"[A-Z0-9_]+", env_var):
            fail(f"Invalid env_var for {service_id}: {env_var}")
        if env_var in seen_env_vars:
            fail(f"Duplicate env_var in config/ports.json: {env_var}")
        seen_env_vars.add(env_var)
        if not isinstance(external_default, int) or external_default <= 0:
            fail(f"Invalid external_default for {service_id}: {external_default}")
        if not isinstance(internal_port, int) or internal_port <= 0:
            fail(f"Invalid internal_port for {service_id}: {internal_port}")
        if not isinstance(compose_managed, bool):
            fail(f"compose_managed must be boolean for {service_id}")
        if not isinstance(include_in_example, bool):
            fail(f"include_in_example must be boolean for {service_id}")

        schema_prop = schema_props.get(env_var)
        if not isinstance(schema_prop, dict):
            fail(f"{env_var} missing in .env.schema.json properties")
        schema_default = schema_prop.get("default")
        if schema_default != external_default:
            fail(
                f".env.schema.json default mismatch for {env_var}: "
                f"expected {external_default}, found {schema_default}"
            )

        if include_in_example:
            example_default = example_defaults.get(env_var)
            if example_default != external_default:
                fail(
                    f".env.example default mismatch for {env_var}: "
                    f"expected {external_default}, found {example_default}"
                )

        if manifest_service is not None:
            if not isinstance(manifest_service, str) or not manifest_service:
                fail(f"manifest_service must be string/null for {service_id}")
            manifest_service_obj = manifest_map.get(manifest_service)
            if not isinstance(manifest_service_obj, dict):
                fail(f"Missing manifest for service '{manifest_service}'")
            manifest_env = manifest_service_obj.get("external_port_env")
            manifest_default = manifest_service_obj.get("external_port_default")
            if manifest_env != env_var:
                fail(
                    f"Manifest env var mismatch for {manifest_service}: "
                    f"expected {env_var}, found {manifest_env}"
                )
            if manifest_default != external_default:
                fail(
                    f"Manifest default mismatch for {manifest_service}: "
                    f"expected {external_default}, found {manifest_default}"
                )

        if compose_managed:
            compose_values = compose_map.get(env_var)
            if not compose_values:
                fail(f"Compose mapping missing for {env_var}")
            compose_default, compose_internal, compose_source = compose_values
            if compose_default != external_default or compose_internal != internal_port:
                fail(
                    f"Compose mismatch for {env_var} in {compose_source}: "
                    f"expected {external_default}:{internal_port}, "
                    f"found {compose_default}:{compose_internal}"
                )

    print("[PASS] canonical port contract parity")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
