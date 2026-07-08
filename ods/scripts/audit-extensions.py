#!/usr/bin/env python3
"""
Audit ODS extensions for manifest, compose, overlay, and feature
contract consistency.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml


VALID_CATEGORIES = {"core", "recommended", "optional"}
VALID_TYPES = {"docker", "host-systemd"}
VALID_GPU_BACKENDS = {"amd", "nvidia", "apple", "all", "none"}
MANIFEST_NAMES = ("manifest.yaml", "manifest.yml", "manifest.json")
OVERLAY_SUFFIXES = {
    "amd": ("compose.amd.yaml", "compose.amd.yml"),
    "nvidia": ("compose.nvidia.yaml", "compose.nvidia.yml"),
    "apple": ("compose.apple.yaml", "compose.apple.yml"),
}
FEATURE_SERVICE_KEYS = (
    ("requirements", "services"),
    ("requirements", "services_all"),
    ("requirements", "services_any"),
    ("enabled_services_all",),
    ("enabled_services_any",),
)


@dataclass
class Issue:
    severity: str
    code: str
    message: str
    service: str | None = None
    path: str | None = None


@dataclass
class ServiceRecord:
    service_id: str
    directory_name: str
    directory: Path
    manifest_path: Path
    manifest: dict[str, Any]
    service: dict[str, Any]
    features: list[dict[str, Any]]
    compose_path: Path | None
    compose_enabled: bool
    overlay_paths: dict[str, Path]
    category: str
    service_type: str
    issues: list[Issue] = field(default_factory=list)

    def add_issue(
        self,
        severity: str,
        code: str,
        message: str,
        *,
        path: Path | None = None,
    ) -> None:
        self.issues.append(
            Issue(
                severity=severity,
                code=code,
                message=message,
                service=self.service_id,
                path=str(path) if path else None,
            )
        )

    @property
    def status(self) -> str:
        if any(issue.severity == "error" for issue in self.issues):
            return "fail"
        if any(issue.severity == "warning" for issue in self.issues):
            return "warn"
        return "pass"


def parse_args() -> argparse.Namespace:
    default_project = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(
        description="Audit ODS extension manifests and compose fragments."
    )
    parser.add_argument(
        "--project-dir",
        type=Path,
        default=default_project,
        help="ODS project directory (defaults to the repo root).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of the human-readable report.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings as failures.",
    )
    parser.add_argument(
        "services",
        nargs="*",
        help="Optional service IDs to audit. Defaults to all discovered services.",
    )
    return parser.parse_args()


def load_document(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        if path.suffix == ".json":
            return json.load(handle)
        return yaml.safe_load(handle)


def find_manifest(service_dir: Path) -> Path | None:
    for name in MANIFEST_NAMES:
        candidate = service_dir / name
        if candidate.exists():
            return candidate
    return None


def resolve_compose_path(service_dir: Path, compose_file: str) -> tuple[Path | None, bool]:
    if not compose_file:
        return None, False

    enabled = service_dir / compose_file
    if enabled.exists():
        return enabled, True

    disabled = service_dir / f"{compose_file}.disabled"
    if disabled.exists():
        return disabled, False

    return enabled, False


def discover_services(project_dir: Path) -> tuple[list[ServiceRecord], list[Issue]]:
    ext_dir = project_dir / "extensions" / "services"
    records: list[ServiceRecord] = []
    global_issues: list[Issue] = []

    if not ext_dir.exists():
        global_issues.append(
            Issue(
                severity="error",
                code="extensions-dir-missing",
                message="extensions/services directory not found",
                path=str(ext_dir),
            )
        )
        return records, global_issues

    for service_dir in sorted(ext_dir.iterdir()):
        if not service_dir.is_dir():
            continue

        manifest_path = find_manifest(service_dir)
        if manifest_path is None:
            global_issues.append(
                Issue(
                    severity="warning",
                    code="manifest-missing",
                    message="service directory has no manifest",
                    service=service_dir.name,
                    path=str(service_dir),
                )
            )
            continue

        try:
            manifest = load_document(manifest_path)
        except Exception as exc:
            global_issues.append(
                Issue(
                    severity="error",
                    code="manifest-invalid",
                    message=f"failed to parse manifest: {exc}",
                    service=service_dir.name,
                    path=str(manifest_path),
                )
            )
            continue

        if not isinstance(manifest, dict):
            global_issues.append(
                Issue(
                    severity="error",
                    code="manifest-shape-invalid",
                    message="manifest root must be a mapping",
                    service=service_dir.name,
                    path=str(manifest_path),
                )
            )
            continue

        service = manifest.get("service")
        if not isinstance(service, dict):
            global_issues.append(
                Issue(
                    severity="error",
                    code="service-section-missing",
                    message="manifest must contain a service mapping",
                    service=service_dir.name,
                    path=str(manifest_path),
                )
            )
            continue

        service_id = str(service.get("id") or service_dir.name)
        features = manifest.get("features") or []
        if not isinstance(features, list):
            features = []

        compose_path, compose_enabled = resolve_compose_path(
            service_dir, str(service.get("compose_file") or "")
        )
        overlay_paths: dict[str, Path] = {}
        for backend, names in OVERLAY_SUFFIXES.items():
            for name in names:
                candidate = service_dir / name
                if candidate.exists():
                    overlay_paths[backend] = candidate
                    break

        records.append(
            ServiceRecord(
                service_id=service_id,
                directory_name=service_dir.name,
                directory=service_dir,
                manifest_path=manifest_path,
                manifest=manifest,
                service=service,
                features=features,
                compose_path=compose_path,
                compose_enabled=compose_enabled,
                overlay_paths=overlay_paths,
                category=str(service.get("category") or "optional"),
                service_type=str(service.get("type") or "docker"),
            )
        )

    return records, global_issues


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def as_string_list(value: Any) -> list[str]:
    return [str(item) for item in as_list(value) if str(item)]


def parse_positive_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    try:
        integer = int(value)
    except (TypeError, ValueError):
        return None
    return integer if integer > 0 else None


def parse_non_negative_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    try:
        integer = int(value)
    except (TypeError, ValueError):
        return None
    return integer if integer >= 0 else None


def collect_service_references(feature: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for path in FEATURE_SERVICE_KEYS:
        target: Any = feature
        for key in path:
            if not isinstance(target, dict):
                target = None
                break
            target = target.get(key)
        refs.extend(as_string_list(target))
    return refs


def load_compose_definitions(record: ServiceRecord) -> tuple[dict[str, Any], dict[str, Path]]:
    definitions: dict[str, Any] = {}
    source_paths: dict[str, Path] = {}

    def capture(label: str, path: Path) -> None:
        try:
            doc = load_document(path)
        except Exception as exc:
            record.add_issue("error", "compose-invalid", f"failed to parse compose file: {exc}", path=path)
            return

        if doc is None:
            doc = {}
        if not isinstance(doc, dict):
            record.add_issue("error", "compose-shape-invalid", "compose root must be a mapping", path=path)
            return

        services = doc.get("services", {})
        if not isinstance(services, dict):
            record.add_issue("error", "compose-services-invalid", "compose services block must be a mapping", path=path)
            return

        definitions[label] = services.get(record.service_id)
        source_paths[label] = path

    if record.compose_path and record.compose_path.exists():
        capture("base", record.compose_path)

    for backend, path in record.overlay_paths.items():
        capture(backend, path)

    return definitions, source_paths


def extract_target_ports(service_def: Any) -> list[int]:
    """Return every container-internal port a service makes available.

    Looks at BOTH `ports:` (host-bound) and `expose:` (internal-only)
    declarations so internal-only services like ods-hermes (fronted by
    hermes-proxy) still register their port. Without this, a service
    that switches from `ports:` to `expose:` would fail
    compose-port-mismatch even though its container is fully reachable
    by other containers on the bridge network.
    """
    if not isinstance(service_def, dict):
        return []

    results: list[int] = []
    for port in as_list(service_def.get("ports")):
        if isinstance(port, int):
            if port > 0:
                results.append(port)
            continue

        if isinstance(port, str):
            tail = port.rsplit(":", 1)[-1]
            tail = tail.split("/", 1)[0]
            try:
                results.append(int(tail))
            except ValueError:
                continue
            continue

        if isinstance(port, dict):
            target = port.get("target")
            target_int = parse_positive_int(target)
            if target_int:
                results.append(target_int)

    # `expose:` is a list of "<port>" or "<port>/<proto>" strings/ints —
    # internal-network-only, never bound to the host.
    for entry in as_list(service_def.get("expose")):
        if isinstance(entry, int):
            if entry > 0:
                results.append(entry)
            continue
        if isinstance(entry, str):
            head = entry.split("/", 1)[0]
            try:
                results.append(int(head))
            except ValueError:
                continue
    return results


def ports_reference_env(service_def: Any, env_name: str) -> bool:
    if not isinstance(service_def, dict) or not env_name:
        return False

    needle = f"${{{env_name}"
    for port in as_list(service_def.get("ports")):
        if isinstance(port, str) and needle in port:
            return True
        if isinstance(port, dict):
            published = port.get("published")
            if isinstance(published, str) and env_name in published:
                return True
    return False


def service_has_runtime_definition(definitions: dict[str, Any]) -> bool:
    for definition in definitions.values():
        if isinstance(definition, dict):
            return True
    return False


def base_is_stub(definitions: dict[str, Any]) -> bool:
    base = definitions.get("base")
    return base == {} or base is None


def validate_records(
    records: list[ServiceRecord],
    global_issues: list[Issue],
    *,
    reference_records: list[ServiceRecord] | None = None,
) -> None:
    reference_records = reference_records or records
    known_services = {record.service_id: record for record in reference_records}
    selected_ids = set(known_services)
    alias_owners: dict[str, set[str]] = {}
    feature_owners: dict[str, set[str]] = {}
    id_owners: dict[str, set[str]] = {}

    for ref in reference_records:
        id_owners.setdefault(ref.service_id, set()).add(ref.directory_name)
        for alias in as_string_list(ref.service.get("aliases")):
            alias_owners.setdefault(alias, set()).add(ref.service_id)
        for feature in ref.features:
            if isinstance(feature, dict):
                feature_id = str(feature.get("id") or "")
                if feature_id:
                    feature_owners.setdefault(feature_id, set()).add(ref.service_id)

    for service_id, directories in sorted(id_owners.items()):
        if len(directories) > 1:
            global_issues.append(
                Issue(
                    severity="error",
                    code="service-id-collision",
                    message=f"service.id '{service_id}' is declared in multiple directories",
                    service=service_id,
                )
            )

    for record in records:
        manifest = record.manifest
        service = record.service

        if manifest.get("schema_version") != "ods.services.v1":
            record.add_issue(
                "error",
                "schema-version-invalid",
                "schema_version must be ods.services.v1",
                path=record.manifest_path,
            )

        if record.directory_name != record.service_id:
            record.add_issue(
                "error",
                "service-id-directory-mismatch",
                f"directory '{record.directory_name}' does not match service.id '{record.service_id}'",
                path=record.manifest_path,
            )

        name = str(service.get("name") or "").strip()
        if not name:
            record.add_issue("error", "service-name-missing", "service.name is required", path=record.manifest_path)

        if record.category not in VALID_CATEGORIES:
            record.add_issue(
                "error",
                "service-category-invalid",
                f"service.category must be one of {sorted(VALID_CATEGORIES)}",
                path=record.manifest_path,
            )

        if record.service_type not in VALID_TYPES:
            record.add_issue(
                "error",
                "service-type-invalid",
                f"service.type must be one of {sorted(VALID_TYPES)}",
                path=record.manifest_path,
            )

        # `host_network: true` marks services that use Docker's
        # `network_mode: host` (Tailscale being the canonical example) — they
        # don't have a Docker-mapped port or an HTTP health endpoint, so the
        # port/health requirements don't apply. The flag also flips off the
        # compose-port-mismatch check further down.
        host_network = bool(service.get("host_network"))

        health_type = str(service.get("health_type") or "http")

        # CLI/one-shot tools with health_type=none have no server port
        port = parse_positive_int(service.get("port"))
        if port is None and not host_network and health_type != "none":
            record.add_issue("error", "service-port-invalid", "service.port must be a positive integer", path=record.manifest_path)
        if health_type not in ("http", "tcp", "none"):
            record.add_issue(
                "error",
                "service-health-type-invalid",
                f"service.health_type must be one of http, tcp, none (got '{health_type}')",
                path=record.manifest_path,
            )

        health = str(service.get("health") or "")
        if health_type == "http" and not health.startswith("/") and not host_network:
            record.add_issue(
                "error",
                "service-health-invalid",
                "service.health must start with '/' when health_type is http",
                path=record.manifest_path,
            )

        ext_port_default = service.get("external_port_default")
        if ext_port_default not in (None, "") and parse_non_negative_int(ext_port_default) is None:
            record.add_issue(
                "error",
                "service-external-port-invalid",
                "service.external_port_default must be a non-negative integer when set",
                path=record.manifest_path,
            )

        external_port_env = str(service.get("external_port_env") or "")
        if external_port_env and not external_port_env.replace("_", "").isalnum():
            record.add_issue(
                "error",
                "service-port-env-invalid",
                "service.external_port_env must be shell-friendly",
                path=record.manifest_path,
            )

        gpu_backends = as_string_list(service.get("gpu_backends") or ["amd", "nvidia"])
        invalid_backends = [backend for backend in gpu_backends if backend not in VALID_GPU_BACKENDS]
        if invalid_backends:
            record.add_issue(
                "error",
                "service-gpu-backends-invalid",
                f"unknown gpu_backends values: {', '.join(sorted(invalid_backends))}",
                path=record.manifest_path,
            )

        alias_list = as_string_list(service.get("aliases"))
        seen_local_aliases: set[str] = set()
        for alias in alias_list:
            if alias in seen_local_aliases:
                record.add_issue(
                    "error",
                    "alias-duplicate-local",
                    f"alias '{alias}' is listed more than once",
                    path=record.manifest_path,
                )
                continue
            seen_local_aliases.add(alias)
            owners = alias_owners.get(alias, set())
            if owners - {record.service_id}:
                record.add_issue(
                    "error",
                    "alias-collision",
                    f"alias '{alias}' already belongs to service '{sorted(owners - {record.service_id})[0]}'",
                    path=record.manifest_path,
                )

        for dep in as_string_list(service.get("depends_on")):
            if dep not in selected_ids:
                record.add_issue(
                    "error",
                    "dependency-missing",
                    f"depends_on references unknown service '{dep}'",
                    path=record.manifest_path,
                )

        env_vars = service.get("env_vars")
        if env_vars is not None and not isinstance(env_vars, list):
            record.add_issue(
                "error",
                "env-vars-invalid",
                "service.env_vars must be a list when present",
                path=record.manifest_path,
            )
        elif isinstance(env_vars, list):
            for item in env_vars:
                if not isinstance(item, dict) or not str(item.get("key") or "").strip():
                    record.add_issue(
                        "error",
                        "env-var-entry-invalid",
                        "each service.env_vars entry must contain a non-empty key",
                        path=record.manifest_path,
                    )

        for feature in record.features:
            if not isinstance(feature, dict):
                record.add_issue(
                    "error",
                    "feature-invalid",
                    "each feature entry must be a mapping",
                    path=record.manifest_path,
                )
                continue

            for required in ("id", "name", "description", "category", "priority"):
                if feature.get(required) in (None, ""):
                    record.add_issue(
                        "error",
                        "feature-field-missing",
                        f"feature is missing required field '{required}'",
                        path=record.manifest_path,
                    )

            feature_id = str(feature.get("id") or "")
            if feature_id:
                owners = feature_owners.get(feature_id, set())
                if owners - {record.service_id}:
                    record.add_issue(
                        "error",
                        "feature-id-collision",
                        f"feature id '{feature_id}' already belongs to service '{sorted(owners - {record.service_id})[0]}'",
                        path=record.manifest_path,
                    )

            for ref in collect_service_references(feature):
                if ref not in selected_ids:
                    record.add_issue(
                        "error",
                        "feature-service-reference-invalid",
                        f"feature references unknown service '{ref}'",
                        path=record.manifest_path,
                    )

        if record.service_type == "host-systemd" and service.get("compose_file"):
            record.add_issue(
                "warning",
                "compose-file-unexpected",
                "host-systemd service usually should not declare compose_file",
                path=record.manifest_path,
            )

        if record.service_type != "docker":
            continue

        compose_file = str(service.get("compose_file") or "")
        if record.category != "core" and not compose_file:
            record.add_issue(
                "error",
                "compose-file-missing",
                "non-core docker services must declare service.compose_file",
                path=record.manifest_path,
            )
            continue

        if compose_file and (record.compose_path is None or not record.compose_path.exists()):
            record.add_issue(
                "error",
                "compose-file-missing",
                f"compose file '{compose_file}' was not found (enabled or disabled)",
                path=record.manifest_path,
            )
            continue

        definitions, source_paths = load_compose_definitions(record)
        if not service_has_runtime_definition(definitions):
            if record.category != "core" and record.compose_enabled:
                record.add_issue(
                    "error",
                    "compose-service-missing",
                    f"no compose definition found for service '{record.service_id}'",
                    path=record.compose_path or record.manifest_path,
                )
            continue

        if base_is_stub(definitions):
            for backend in gpu_backends:
                if backend in {"all", "none", "apple"}:
                    continue
                if backend not in record.overlay_paths:
                    record.add_issue(
                        "error",
                        "overlay-required",
                        f"stub compose requires compose.{backend}.yaml because gpu_backends includes '{backend}'",
                        path=record.compose_path or record.manifest_path,
                    )

        for backend, overlay_path in record.overlay_paths.items():
            if gpu_backends and "all" not in gpu_backends and backend not in gpu_backends:
                record.add_issue(
                    "warning",
                    "overlay-backend-extra",
                    f"{overlay_path.name} exists but service.gpu_backends does not include '{backend}'",
                    path=overlay_path,
                )

        container_name = str(service.get("container_name") or "")
        if container_name:
            matched = False
            for label, definition in definitions.items():
                if isinstance(definition, dict) and definition.get("container_name") == container_name:
                    matched = True
                    break
            if not matched and record.category != "core":
                record.add_issue(
                    "error",
                    "container-name-mismatch",
                    f"container_name '{container_name}' was not found in compose definitions",
                    path=source_paths.get("base", record.manifest_path),
                )

        if port is not None and not host_network:
            # host-network services share the host's network namespace; their
            # listen ports come from whatever process binds inside the host,
            # not from Docker's port mapping. Skipping this check is what
            # makes host_network: true viable in the first place.
            port_matches = False
            for definition in definitions.values():
                if port in extract_target_ports(definition):
                    port_matches = True
                    break
            if not port_matches and record.category != "core":
                record.add_issue(
                    "error",
                    "compose-port-mismatch",
                    f"no compose port mapping targets manifest service.port {port}",
                    path=source_paths.get("base", record.manifest_path),
                )

        if external_port_env:
            env_ref_found = False
            for definition in definitions.values():
                if ports_reference_env(definition, external_port_env):
                    env_ref_found = True
                    break
            if not env_ref_found and record.category != "core":
                record.add_issue(
                    "warning",
                    "compose-port-env-unused",
                    f"compose ports do not reference service.external_port_env '{external_port_env}'",
                    path=source_paths.get("base", record.manifest_path),
                )

        healthcheck_found = False
        for definition in definitions.values():
            if isinstance(definition, dict) and "healthcheck" in definition:
                healthcheck_found = True
                break
        if not healthcheck_found and record.category != "core":
            record.add_issue(
                "warning",
                "healthcheck-missing",
                "docker service has no healthcheck stanza in its compose definitions",
                path=source_paths.get("base", record.manifest_path),
            )

def filter_records(records: list[ServiceRecord], requested_services: list[str]) -> tuple[list[ServiceRecord], list[Issue]]:
    if not requested_services:
        return records, []

    requested = set(requested_services)
    available = {record.service_id for record in records}
    missing = sorted(requested - available)
    filtered = [record for record in records if record.service_id in requested]
    issues = [
        Issue(
            severity="error",
            code="service-not-found",
            message=f"requested service '{service_id}' was not found",
            service=service_id,
        )
        for service_id in missing
    ]
    return filtered, issues


def build_payload(
    project_dir: Path,
    records: list[ServiceRecord],
    global_issues: list[Issue],
    strict: bool,
    requested_services: list[str],
) -> dict[str, Any]:
    error_count = sum(1 for issue in global_issues if issue.severity == "error")
    warning_count = sum(1 for issue in global_issues if issue.severity == "warning")

    service_items = []
    for record in records:
        error_count += sum(1 for issue in record.issues if issue.severity == "error")
        warning_count += sum(1 for issue in record.issues if issue.severity == "warning")
        service_items.append(
            {
                "service_id": record.service_id,
                "directory": str(record.directory),
                "category": record.category,
                "type": record.service_type,
                "compose_enabled": record.compose_enabled,
                "status": record.status,
                "issues": [asdict(issue) for issue in record.issues],
            }
        )

    failed = error_count > 0 or (strict and warning_count > 0)
    return {
        "project_dir": str(project_dir),
        "requested_services": requested_services,
        "summary": {
            "services_audited": len(records),
            "errors": error_count,
            "warnings": warning_count,
            "strict": strict,
            "result": "fail" if failed else "pass",
        },
        "global_issues": [asdict(issue) for issue in global_issues],
        "services": service_items,
    }


def print_human_report(payload: dict[str, Any]) -> None:
    summary = payload["summary"]
    requested = payload["requested_services"]

    print("ODS Extension Audit")
    print(f"Project: {payload['project_dir']}")
    print(
        f"Scope: {', '.join(requested)}"
        if requested
        else f"Scope: all extensions ({summary['services_audited']})"
    )
    print("")

    for issue in payload["global_issues"]:
        prefix = "ERROR" if issue["severity"] == "error" else "WARN"
        location = f" [{issue['path']}]" if issue.get("path") else ""
        print(f"{prefix} global {issue['code']}: {issue['message']}{location}")

    if payload["global_issues"]:
        print("")

    for item in payload["services"]:
        label = item["status"].upper()
        print(f"{label:4} {item['service_id']} ({item['category']}, {item['type']})")
        for issue in item["issues"]:
            prefix = "ERROR" if issue["severity"] == "error" else "WARN"
            location = f" [{issue['path']}]" if issue.get("path") else ""
            print(f"     {prefix} {issue['code']}: {issue['message']}{location}")

    if not payload["services"] and not payload["global_issues"]:
        print("No services matched the requested scope.")

    print("")
    print(
        "Summary: "
        f"{summary['services_audited']} services, "
        f"{summary['errors']} errors, "
        f"{summary['warnings']} warnings, "
        f"result={summary['result']}"
    )


def main() -> int:
    args = parse_args()
    project_dir = args.project_dir.resolve()

    records, global_issues = discover_services(project_dir)
    filtered_records, filter_issues = filter_records(records, args.services)
    global_issues.extend(filter_issues)
    validate_records(filtered_records, global_issues, reference_records=records)

    payload = build_payload(
        project_dir=project_dir,
        records=filtered_records,
        global_issues=global_issues,
        strict=args.strict,
        requested_services=args.services,
    )

    if args.json:
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print_human_report(payload)

    summary = payload["summary"]
    if summary["errors"] > 0:
        return 1
    if args.strict and summary["warnings"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
