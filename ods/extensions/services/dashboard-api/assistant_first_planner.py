"""Pure deterministic planning for Assistant First extension requests.

The module deliberately has no host observers or lifecycle executors.  Callers
must supply parsed manifests, immutable revisions, and explicit preferences.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any


_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_CAPABILITY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}@[1-9][0-9]{0,8}$")
_RESOURCE_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,63}$")
_CONFIG_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_UTC_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
_SEMVER_RE = re.compile(
    r"^(0|[1-9][0-9]*)\."
    r"(0|[1-9][0-9]*)\."
    r"(0|[1-9][0-9]*)"
    r"(?:-((?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_DRIVER_VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$")

_PLANNING_FIELDS = frozenset(
    {
        "provides",
        "requires",
        "optional",
        "conflicts",
        "provider_priority",
        "requirements",
        "estimates",
        "resources",
        "configuration",
        "artifacts",
        "lifecycle",
        "data",
        "trust",
        "support",
    }
)

_REQUIREMENT_FIELDS = frozenset(
    {"platforms", "architectures", "container_runtimes", "gpu_backends", "min_driver_version"}
)
_ESTIMATE_FIELDS = frozenset(
    {"download_bytes", "disk_bytes", "cpu_millicores", "ram_bytes", "vram_bytes", "gpu_count"}
)
_RESOURCE_FIELDS = frozenset(
    {
        "host_ports",
        "container_ports",
        "networks",
        "volumes",
        "devices",
        "exclusive",
        "linux_capabilities",
        "host_permissions",
    }
)
_CONFIG_FIELDS = frozenset(
    {"key", "type", "required", "secret", "source", "restart_behavior", "validation", "default"}
)
_ARTIFACT_FIELDS = frozenset({"images", "builds"})
_IMAGE_FIELDS = frozenset({"reference", "digest", "download_bytes"})
_BUILD_FIELDS = frozenset({"source", "revision", "context_digest", "output", "download_bytes"})
_LIFECYCLE_FIELDS = frozenset(
    {"health_checks", "readiness", "setup_hook", "migration_hook", "rollback", "timeout_seconds"}
)
_DATA_FIELDS = frozenset({"path", "backup_class", "owner", "uninstall", "purge"})
_TRUST_FIELDS = frozenset({"tier", "publisher", "definition_signature"})
_SUPPORT_FIELDS = frozenset({"status", "url"})


class PlanningError(ValueError):
    """A stable, safe planning failure suitable for an API error response."""

    def __init__(self, code: str, **details: Any) -> None:
        super().__init__(code)
        self.code = code
        self.details = copy.deepcopy(details)

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "details": copy.deepcopy(self.details)}


def _fail(code: str, **details: Any) -> None:
    raise PlanningError(code, **details)


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail("invalid-field-type", field=field)
    if any(not isinstance(key, str) for key in value):
        _fail("invalid-object-key", field=field)
    return value


def _exact_mapping(value: Any, field: str, expected: frozenset[str]) -> Mapping[str, Any]:
    result = _mapping(value, field)
    if set(result) != expected:
        _fail("invalid-object-fields", field=field, fields=sorted(set(result)))
    return result


def _sequence(value: Any, field: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        _fail("invalid-field-type", field=field)
    return value


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        _fail("invalid-identifier", field=field)
    return value


def _capability(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _CAPABILITY_RE.fullmatch(value):
        _fail("invalid-capability", field=field)
    return value


def _resource(value: Any, field: str) -> str:
    if not isinstance(value, str) or ".." in value or not _RESOURCE_RE.fullmatch(value):
        _fail("invalid-resource", field=field)
    return value


def _config_key(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _CONFIG_KEY_RE.fullmatch(value):
        _fail("invalid-config-key", field=field)
    return value


def _unique_strings(
    value: Any,
    field: str,
    validator: Any,
) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(_sequence(value, field)):
        normalized = validator(item, f"{field}[{index}]")
        if normalized in seen:
            _fail("duplicate-value", field=field, value=normalized)
        seen.add(normalized)
        result.append(normalized)
    # These manifest/request arrays are semantic sets.  Lexicographic order is
    # the planner's documented normalization rule, not a JSON canonicalizer
    # rewriting arbitrary lists.
    return tuple(sorted(result))


def _integer(value: Any, field: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        _fail("invalid-integer", field=field)
    return value


def _boolean(value: Any, field: str) -> bool:
    if type(value) is not bool:
        _fail("invalid-boolean", field=field)
    return value


def _text(value: Any, field: str, *, maximum: int, allow_empty: bool = False) -> str:
    if (
        not isinstance(value, str)
        or (not allow_empty and not value)
        or len(value) > maximum
        or any(
            ord(character) < 32
            or ord(character) == 127
            or 0xD800 <= ord(character) <= 0xDFFF
            for character in value
        )
    ):
        _fail("invalid-text", field=field)
    return value


def _enum(value: Any, field: str, allowed: frozenset[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        _fail("invalid-enum", field=field)
    return value


def _nullable_text(value: Any, field: str, maximum: int) -> str | None:
    if value is None:
        return None
    return _text(value, field, maximum=maximum)


def _digest(value: Any, field: str, *, optional: bool = False) -> str:
    if optional and (value is None or value == ""):
        return ""
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        _fail("invalid-digest", field=field)
    return value


def _token_list(
    value: Any,
    field: str,
    allowed: frozenset[str],
    *,
    require_one: bool = False,
) -> tuple[str, ...]:
    result = _unique_strings(
        value,
        field,
        lambda item, item_field: _enum(item, item_field, allowed),
    )
    if require_one and not result:
        _fail("empty-required-list", field=field)
    return result


def _unique_integers(value: Any, field: str, minimum: int, maximum: int) -> tuple[int, ...]:
    result: list[int] = []
    seen: set[int] = set()
    for index, item in enumerate(_sequence(value, field)):
        normalized = _integer(item, f"{field}[{index}]", minimum, maximum)
        if normalized in seen:
            _fail("duplicate-value", field=field, value=normalized)
        seen.add(normalized)
        result.append(normalized)
    return tuple(sorted(result))


def _device(value: Any, field: str) -> str:
    result = _text(value, field, maximum=128)
    if not result.startswith("/") or ".." in result or re.fullmatch(r"/[A-Za-z0-9/_-]{1,127}", result) is None:
        _fail("invalid-device", field=field)
    return result


def _relative_path(value: Any, field: str, maximum: int) -> str:
    result = _text(value, field, maximum=maximum)
    if result.startswith("/") or ".." in result or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", result) is None:
        _fail("invalid-relative-path", field=field)
    return result


def _linux_capability(value: Any, field: str) -> str:
    result = _text(value, field, maximum=64)
    if re.fullmatch(r"CAP_[A-Z0-9_]{1,63}", result) is None:
        _fail("invalid-linux-capability", field=field)
    return result


def adapt_manifest(manifest: Any) -> dict[str, Any]:
    """Project a v1/v2 manifest into a strict immutable planning record.

    v1 capability-like fields are intentionally ignored.  Only its explicit
    hard ``depends_on`` edges survive the compatibility adapter.
    """

    root = _mapping(manifest, "manifest")
    schema_version = root.get("schema_version")
    if schema_version not in {"ods.services.v1", "ods.services.v2"}:
        _fail("unsupported-manifest-schema")
    service = _mapping(root.get("service"), "service")
    service_id = _identifier(service.get("id"), "service.id")
    depends_on = _unique_strings(
        service.get("depends_on", []), "service.depends_on", _identifier
    )
    catalog = root.get("_catalog", {})
    catalog = _mapping(catalog, "_catalog")
    unknown_catalog = sorted(set(catalog) - {"definition_sha256", "compose_sha256"})
    if unknown_catalog:
        _fail("invalid-object-fields", field="_catalog", fields=unknown_catalog)
    definition_sha = _digest(
        catalog.get("definition_sha256", ""), "_catalog.definition_sha256", optional=True
    )
    compose_sha = _digest(
        catalog.get("compose_sha256", ""), "_catalog.compose_sha256", optional=True
    )

    empty: tuple[str, ...] = ()
    empty_resources = {
        "hostPorts": (),
        "containerPorts": (),
        "networks": (),
        "volumes": (),
        "devices": (),
        "exclusive": (),
        "linuxCapabilities": (),
        "hostPermissions": (),
    }
    empty_estimates = {
        "downloadBytes": 0,
        "diskBytes": 0,
        "cpuMillicores": 0,
        "ramBytes": 0,
        "vramBytes": 0,
        "gpuCount": 0,
    }
    if schema_version == "ods.services.v1":
        compatibility = root.get("compatibility", {})
        compatibility = compatibility if isinstance(compatibility, Mapping) else {}
        return {
            "schemaVersion": schema_version,
            "id": service_id,
            "serviceType": str(service.get("type", "docker")),
            "version": "0.0.0-legacy",
            "dataSchemaVersion": "legacy",
            "odsCompatibility": {
                "minimum": str(compatibility.get("ods_min", "")),
                "maximum": str(compatibility.get("ods_max", "")),
            },
            "definitionSha256": definition_sha,
            "composeSha256": compose_sha,
            "dependsOn": depends_on,
            "provides": empty,
            "requires": empty,
            "optional": empty,
            "conflicts": empty,
            "providerPriority": 0,
            "requirements": {
                "platforms": (),
                "architectures": (),
                "containerRuntimes": (),
                "gpuBackends": (),
                "minDriverVersion": None,
            },
            "estimates": empty_estimates,
            "resources": empty_resources,
            "configuration": (),
            "artifacts": {"images": (), "builds": ()},
            "lifecycle": {
                "healthChecks": (),
                "readiness": (),
                "setupHook": None,
                "migrationHook": None,
                "rollback": "none",
                "timeoutSeconds": 1,
            },
            "data": (),
            "trust": {"tier": "bundled", "publisher": "ODS", "definitionSignature": None},
            "support": {"status": "experimental", "url": None},
            "legacy": True,
        }

    version = service.get("version")
    if not isinstance(version, str) or _SEMVER_RE.fullmatch(version) is None:
        _fail("invalid-version", field="service.version")
    data_schema_version = _text(
        service.get("data_schema_version"), "service.data_schema_version", maximum=64
    )
    service_type = _enum(
        service.get("type"), "service.type", frozenset({"docker", "host-systemd"})
    )
    compatibility = _mapping(root.get("compatibility"), "compatibility")
    minimum = _text(compatibility.get("ods_min"), "compatibility.ods_min", maximum=32)
    maximum = compatibility.get("ods_max")
    if maximum is not None:
        maximum = _text(maximum, "compatibility.ods_max", maximum=32)
    minimum_release = _semver(minimum, "compatibility.ods_min")
    if maximum is not None and _semver(maximum, "compatibility.ods_max") < minimum_release:
        _fail("invalid-version-range", field="compatibility")

    planning = _exact_mapping(service.get("planning"), "service.planning", _PLANNING_FIELDS)
    requirements = _exact_mapping(
        planning.get("requirements"), "service.planning.requirements", _REQUIREMENT_FIELDS
    )
    estimates = _exact_mapping(
        planning.get("estimates"), "service.planning.estimates", _ESTIMATE_FIELDS
    )
    resources = _exact_mapping(
        planning.get("resources"), "service.planning.resources", _RESOURCE_FIELDS
    )

    host_ports: list[dict[str, Any]] = []
    seen_host_ports: set[tuple[str, int]] = set()
    for index, item in enumerate(
        _sequence(resources.get("host_ports"), "service.planning.resources.host_ports")
    ):
        port = _exact_mapping(
            item,
            f"service.planning.resources.host_ports[{index}]",
            frozenset({"port", "protocol"}),
        )
        normalized = {
            "port": _integer(port.get("port"), f"host_ports[{index}].port", 1, 65535),
            "protocol": _enum(
                port.get("protocol"), f"host_ports[{index}].protocol", frozenset({"tcp", "udp"})
            ),
        }
        identity = (normalized["protocol"], normalized["port"])
        if identity in seen_host_ports:
            _fail("duplicate-value", field="service.planning.resources.host_ports")
        seen_host_ports.add(identity)
        host_ports.append(normalized)
    host_ports.sort(key=lambda item: (item["protocol"], item["port"]))

    configuration: list[dict[str, Any]] = []
    seen_config: set[str] = set()
    for index, item in enumerate(
        _sequence(planning.get("configuration"), "service.planning.configuration")
    ):
        field = f"service.planning.configuration[{index}]"
        spec = _mapping(item, field)
        if not set(spec) <= _CONFIG_FIELDS or not _CONFIG_FIELDS - {"validation", "default"} <= set(spec):
            _fail("invalid-object-fields", field=field, fields=sorted(set(spec)))
        key = _config_key(spec.get("key"), f"{field}.key")
        if key in seen_config:
            _fail("duplicate-value", field="service.planning.configuration", value=key)
        seen_config.add(key)
        secret = _boolean(spec.get("secret"), f"{field}.secret")
        if secret and "default" in spec:
            _fail("secret-default-forbidden", field=field)
        normalized: dict[str, Any] = {
            "key": key,
            "type": _enum(
                spec.get("type"), f"{field}.type", frozenset({"string", "integer", "boolean", "url", "enum"})
            ),
            "required": _boolean(spec.get("required"), f"{field}.required"),
            "secret": secret,
            "source": _enum(
                spec.get("source"), f"{field}.source", frozenset({"user", "generated", "system", "provider"})
            ),
            "restartBehavior": _enum(
                spec.get("restart_behavior"),
                f"{field}.restart_behavior",
                frozenset({"none", "service", "stack"}),
            ),
        }
        if "validation" in spec:
            normalized["validation"] = _text(
                spec.get("validation"), f"{field}.validation", maximum=256, allow_empty=True
            )
        if "default" in spec:
            default = spec.get("default")
            if type(default) not in {str, int, bool}:
                _fail("invalid-config-default", field=field)
            expected_type = normalized["type"]
            valid_default = (
                (expected_type == "integer" and type(default) is int)
                or (expected_type == "boolean" and type(default) is bool)
                or (expected_type in {"string", "url", "enum"} and type(default) is str)
            )
            if not valid_default:
                _fail("invalid-config-default", field=field)
            normalized["default"] = default
        configuration.append(normalized)
    configuration.sort(key=lambda item: item["key"])

    artifacts = _exact_mapping(
        planning.get("artifacts"), "service.planning.artifacts", _ARTIFACT_FIELDS
    )
    images: list[dict[str, Any]] = []
    seen_images: set[str] = set()
    for index, item in enumerate(_sequence(artifacts.get("images"), "artifacts.images")):
        image = _exact_mapping(item, f"artifacts.images[{index}]", _IMAGE_FIELDS)
        normalized_image = {
            "reference": _text(image.get("reference"), f"images[{index}].reference", maximum=512),
            "digest": _digest(image.get("digest"), f"images[{index}].digest"),
            "downloadBytes": _integer(
                image.get("download_bytes"), f"images[{index}].download_bytes", 0, 2**63 - 1
            ),
        }
        if normalized_image["reference"] in seen_images:
            _fail("duplicate-artifact", field="artifacts.images", reference=normalized_image["reference"])
        seen_images.add(normalized_image["reference"])
        images.append(normalized_image)
    images.sort(key=lambda item: (item["reference"], item["digest"]))
    builds: list[dict[str, Any]] = []
    seen_builds: set[str] = set()
    for index, item in enumerate(_sequence(artifacts.get("builds"), "artifacts.builds")):
        build = _exact_mapping(item, f"artifacts.builds[{index}]", _BUILD_FIELDS)
        normalized_build = {
            "source": _text(build.get("source"), f"builds[{index}].source", maximum=512),
            "revision": _text(build.get("revision"), f"builds[{index}].revision", maximum=128),
            "contextDigest": _digest(build.get("context_digest"), f"builds[{index}].context_digest"),
            "output": _text(build.get("output"), f"builds[{index}].output", maximum=512),
            "downloadBytes": _integer(
                build.get("download_bytes"), f"builds[{index}].download_bytes", 0, 2**63 - 1
            ),
        }
        if (
            normalized_build["output"] in seen_builds
            or normalized_build["output"] in seen_images
        ):
            _fail("duplicate-artifact", field="artifacts.builds", output=normalized_build["output"])
        seen_builds.add(normalized_build["output"])
        builds.append(normalized_build)
    builds.sort(key=lambda item: (item["output"], item["revision"]))

    lifecycle = _exact_mapping(
        planning.get("lifecycle"), "service.planning.lifecycle", _LIFECYCLE_FIELDS
    )
    data: list[dict[str, Any]] = []
    seen_data_paths: set[str] = set()
    for index, item in enumerate(_sequence(planning.get("data"), "service.planning.data")):
        field = f"service.planning.data[{index}]"
        value = _exact_mapping(item, field, _DATA_FIELDS)
        data_path = _relative_path(value.get("path"), f"{field}.path", 256)
        if data_path in seen_data_paths:
            _fail("duplicate-value", field="service.planning.data", value=data_path)
        seen_data_paths.add(data_path)
        data.append(
            {
                "path": data_path,
                "backupClass": _enum(
                    value.get("backup_class"), f"{field}.backup_class", frozenset({"required", "recommended", "ephemeral"})
                ),
                "owner": _enum(value.get("owner"), f"{field}.owner", frozenset({"ods", "extension", "user"})),
                "uninstall": _enum(value.get("uninstall"), f"{field}.uninstall", frozenset({"preserve", "archive"})),
                "purge": _enum(value.get("purge"), f"{field}.purge", frozenset({"separate-approval", "unsupported"})),
            }
        )
    data.sort(key=lambda item: item["path"])
    trust = _exact_mapping(planning.get("trust"), "service.planning.trust", _TRUST_FIELDS)
    support = _exact_mapping(planning.get("support"), "service.planning.support", _SUPPORT_FIELDS)

    record = {
        "schemaVersion": schema_version,
        "id": service_id,
        "serviceType": service_type,
        "version": version,
        "dataSchemaVersion": data_schema_version,
        "odsCompatibility": {"minimum": minimum, "maximum": maximum},
        "definitionSha256": definition_sha,
        "composeSha256": compose_sha,
        "dependsOn": depends_on,
        "provides": _unique_strings(planning.get("provides"), "service.planning.provides", _capability),
        "requires": _unique_strings(planning.get("requires"), "service.planning.requires", _capability),
        "optional": _unique_strings(planning.get("optional"), "service.planning.optional", _capability),
        "conflicts": _unique_strings(planning.get("conflicts"), "service.planning.conflicts", _identifier),
        "providerPriority": _integer(
            planning.get("provider_priority"), "service.planning.provider_priority", -1_000_000, 1_000_000
        ),
        "requirements": {
            "platforms": _token_list(
                requirements.get("platforms"), "requirements.platforms", frozenset({"linux", "darwin", "windows"}), require_one=True
            ),
            "architectures": _token_list(
                requirements.get("architectures"), "requirements.architectures", frozenset({"amd64", "arm64"}), require_one=True
            ),
            "containerRuntimes": _token_list(
                requirements.get("container_runtimes"), "requirements.container_runtimes", frozenset({"docker", "podman", "none"}), require_one=True
            ),
            "gpuBackends": _token_list(
                requirements.get("gpu_backends"), "requirements.gpu_backends", frozenset({"amd", "nvidia", "apple", "cpu", "none"}), require_one=True
            ),
            "minDriverVersion": _nullable_driver_version(
                requirements.get("min_driver_version"), "requirements.min_driver_version"
            ),
        },
        "estimates": {
            "downloadBytes": _integer(estimates.get("download_bytes"), "estimates.download_bytes", 0, 2**63 - 1),
            "diskBytes": _integer(estimates.get("disk_bytes"), "estimates.disk_bytes", 0, 2**63 - 1),
            "cpuMillicores": _integer(estimates.get("cpu_millicores"), "estimates.cpu_millicores", 0, 10**9),
            "ramBytes": _integer(estimates.get("ram_bytes"), "estimates.ram_bytes", 0, 2**63 - 1),
            "vramBytes": _integer(estimates.get("vram_bytes"), "estimates.vram_bytes", 0, 2**63 - 1),
            "gpuCount": _integer(estimates.get("gpu_count"), "estimates.gpu_count", 0, 1024),
        },
        "resources": {
            "hostPorts": tuple(host_ports),
            "containerPorts": tuple(
                _unique_integers(resources.get("container_ports"), "resources.container_ports", 1, 65535)
            ),
            "networks": _unique_strings(resources.get("networks"), "resources.networks", _resource),
            "volumes": _unique_strings(resources.get("volumes"), "resources.volumes", _resource),
            "devices": _unique_strings(resources.get("devices"), "resources.devices", _device),
            "exclusive": _unique_strings(resources.get("exclusive"), "resources.exclusive", _resource),
            "linuxCapabilities": _unique_strings(
                resources.get("linux_capabilities"), "resources.linux_capabilities", _linux_capability
            ),
            "hostPermissions": _token_list(
                resources.get("host_permissions"),
                "resources.host_permissions",
                frozenset({"network", "gpu", "audio", "camera", "usb", "host-mount", "docker-socket", "privileged"}),
            ),
        },
        "configuration": tuple(configuration),
        "artifacts": {"images": tuple(images), "builds": tuple(builds)},
        "lifecycle": {
            "healthChecks": tuple(
                sorted(_text(item, f"health_checks[{index}]", maximum=256) for index, item in enumerate(_sequence(lifecycle.get("health_checks"), "lifecycle.health_checks")))
            ),
            "readiness": tuple(
                sorted(_text(item, f"readiness[{index}]", maximum=256) for index, item in enumerate(_sequence(lifecycle.get("readiness"), "lifecycle.readiness")))
            ),
            "setupHook": None if lifecycle.get("setup_hook") is None else _relative_path(
                lifecycle.get("setup_hook"), "lifecycle.setup_hook", 128
            ),
            "migrationHook": None if lifecycle.get("migration_hook") is None else _relative_path(
                lifecycle.get("migration_hook"), "lifecycle.migration_hook", 128
            ),
            "rollback": _enum(
                lifecycle.get("rollback"), "lifecycle.rollback", frozenset({"definition", "snapshot", "compensating-action", "none"})
            ),
            "timeoutSeconds": _integer(lifecycle.get("timeout_seconds"), "lifecycle.timeout_seconds", 1, 3600),
        },
        "data": tuple(data),
        "trust": {
            "tier": _enum(trust.get("tier"), "trust.tier", frozenset({"bundled", "verified", "community", "local"})),
            "publisher": _text(trust.get("publisher"), "trust.publisher", maximum=128),
            "definitionSignature": _nullable_text(
                trust.get("definition_signature"), "trust.definition_signature", 1024
            ),
        },
        "support": {
            "status": _enum(
                support.get("status"), "support.status", frozenset({"supported", "experimental", "deprecated", "unsupported"})
            ),
            "url": _nullable_text(support.get("url"), "support.url", 512),
        },
        "legacy": False,
    }
    if service_id in record["conflicts"]:
        _fail("self-conflict", serviceId=service_id)
    return record


def _json_safe(value: Any, seen: set[int] | None = None) -> None:
    if seen is None:
        seen = set()
    if value is None or isinstance(value, bool) or type(value) is int:
        return
    if isinstance(value, str):
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            _fail("invalid-unicode")
        return
    if isinstance(value, float):
        _fail("float-not-canonical")
    if isinstance(value, list):
        identity = id(value)
        if identity in seen:
            _fail("cyclic-json-value")
        seen.add(identity)
        for item in value:
            _json_safe(item, seen)
        seen.remove(identity)
        return
    if isinstance(value, dict):
        identity = id(value)
        if identity in seen:
            _fail("cyclic-json-value")
        seen.add(identity)
        for key, item in value.items():
            if not isinstance(key, str):
                _fail("invalid-object-key", field="canonical-json")
            _json_safe(item, seen)
        seen.remove(identity)
        return
    _fail("unsupported-json-type")


def canonical_json_bytes(value: Any) -> bytes:
    """Return the one canonical byte representation accepted for plan hashes."""

    _json_safe(value)
    text = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (text + "\n").encode("utf-8", errors="strict")


def _provider_for(
    capability: str,
    records: Mapping[str, Mapping[str, Any]],
    preferences: Mapping[str, str],
    eligible_provider_ids: set[str] | None = None,
) -> str:
    all_candidates = [
        record for record in records.values() if capability in record["provides"]
    ]
    if not all_candidates:
        _fail("missing-capability", capability=capability)
    preferred = preferences.get(capability)
    if preferred is not None:
        if preferred not in {record["id"] for record in all_candidates}:
            _fail(
                "invalid-provider-preference",
                capability=capability,
                serviceId=preferred,
            )
        if eligible_provider_ids is not None and preferred not in eligible_provider_ids:
            _fail(
                "incompatible-provider-preference",
                capability=capability,
                serviceId=preferred,
            )
        return preferred
    candidates = [
        record
        for record in all_candidates
        if eligible_provider_ids is None or record["id"] in eligible_provider_ids
    ]
    if not candidates:
        _fail(
            "no-compatible-provider",
            capability=capability,
            candidates=sorted(record["id"] for record in all_candidates),
        )
    highest = max(record["providerPriority"] for record in candidates)
    winners = sorted(
        record["id"] for record in candidates if record["providerPriority"] == highest
    )
    if len(winners) != 1:
        _fail("ambiguous-provider", capability=capability, candidates=winners)
    return winners[0]


def _provider_is_eligible(
    record: Mapping[str, Any],
    state: Mapping[str, Any],
    policy: Mapping[str, Any],
    current_ods: tuple[int, int, int, int, tuple[tuple[int, int, str], ...]],
) -> bool:
    if record["legacy"]:
        return False
    minimum = record["odsCompatibility"]["minimum"]
    maximum = record["odsCompatibility"]["maximum"]
    if minimum and current_ods < _semver(minimum, f"{record['id']}.compatibility.minimum"):
        return False
    if maximum and current_ods > _semver(maximum, f"{record['id']}.compatibility.maximum"):
        return False
    requirements = record["requirements"]
    if (
        state["platform"] not in requirements["platforms"]
        or state["architecture"] not in requirements["architectures"]
        or state["containerRuntime"] not in requirements["containerRuntimes"]
        or state["gpuBackend"] not in requirements["gpuBackends"]
    ):
        return False
    minimum_driver = requirements["minDriverVersion"]
    if minimum_driver is not None:
        if state["driverVersion"] is None:
            return False
        if _driver_version(state["driverVersion"], "observed_state.driverVersion") < _driver_version(
            minimum_driver, f"{record['id']}.requirements.minDriverVersion"
        ):
            return False
    if record["trust"]["tier"] not in policy["allowedTrustTiers"]:
        return False
    if set(record["resources"]["hostPermissions"]) & set(policy["forbiddenHostPermissions"]):
        return False
    if record["support"]["status"] == "unsupported":
        return False
    if record["support"]["status"] == "experimental" and not policy["allowExperimental"]:
        return False
    if (
        record["serviceType"] == "docker"
        and not record["artifacts"]["images"]
        and not record["artifacts"]["builds"]
    ):
        return False
    return True


def _topological_order(
    selected: set[str], edges: set[tuple[str, str]]
) -> list[str]:
    incoming = {service_id: 0 for service_id in selected}
    outgoing = {service_id: set() for service_id in selected}
    for before, after in edges:
        if before == after:
            _fail("dependency-cycle", services=[before])
        if after not in outgoing[before]:
            outgoing[before].add(after)
            incoming[after] += 1
    ready = sorted(service_id for service_id, count in incoming.items() if count == 0)
    result: list[str] = []
    while ready:
        current = ready.pop(0)
        result.append(current)
        for after in sorted(outgoing[current]):
            incoming[after] -= 1
            if incoming[after] == 0:
                ready.append(after)
                ready.sort()
    if len(result) != len(selected):
        _fail(
            "dependency-cycle",
            services=sorted(service_id for service_id, count in incoming.items() if count),
        )
    return result


def _semver(
    value: Any, field: str
) -> tuple[int, int, int, int, tuple[tuple[int, int, str], ...]]:
    """Return a key with SemVer 2.0.0 precedence; build metadata is ignored."""

    if not isinstance(value, str):
        _fail("invalid-version", field=field)
    match = _SEMVER_RE.fullmatch(value)
    if match is None:
        _fail("invalid-version", field=field)
    prerelease = match.group(4)
    identifiers = () if prerelease is None else tuple(prerelease.split("."))
    precedence = tuple(
        (0, int(identifier), "")
        if identifier.isdigit()
        else (1, 0, identifier)
        for identifier in identifiers
    )
    return (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)),
        1 if prerelease is None else 0,
        precedence,
    )


def _driver_version(value: Any, field: str) -> tuple[tuple[int, int, str], ...]:
    """Return a stable natural-sort key for vendor-specific driver versions."""

    if not isinstance(value, str) or _DRIVER_VERSION_RE.fullmatch(value) is None:
        _fail("invalid-driver-version", field=field)
    return tuple(
        (0, int(token), "") if token.isdigit() else (1, 0, token.lower())
        for token in re.findall(r"[0-9]+|[A-Za-z]+", value)
    )


def _nullable_driver_version(value: Any, field: str) -> str | None:
    if value is None:
        return None
    _driver_version(value, field)
    return value


def normalize_host_state(value: Any) -> dict[str, Any]:
    fields = frozenset(
        {
            "odsVersion",
            "platform",
            "architecture",
            "containerRuntime",
            "gpuBackend",
            "driverVersion",
            "available",
            "occupiedPorts",
            "reservedResources",
            "installedServices",
        }
    )
    state = _exact_mapping(value, "observed_state", fields)
    available_fields = frozenset(
        {"diskBytes", "ramBytes", "vramBytes", "cpuMillicores", "gpuCount"}
    )
    available = _exact_mapping(state.get("available"), "observed_state.available", available_fields)
    occupied: list[dict[str, Any]] = []
    seen_ports: set[tuple[str, int]] = set()
    for index, item in enumerate(_sequence(state.get("occupiedPorts"), "observed_state.occupiedPorts")):
        field = f"observed_state.occupiedPorts[{index}]"
        port = _exact_mapping(item, field, frozenset({"port", "protocol", "owner"}))
        normalized = {
            "port": _integer(port.get("port"), f"{field}.port", 1, 65535),
            "protocol": _enum(port.get("protocol"), f"{field}.protocol", frozenset({"tcp", "udp"})),
            "owner": _text(port.get("owner"), f"{field}.owner", maximum=128),
        }
        identity = (normalized["protocol"], normalized["port"])
        if identity in seen_ports:
            _fail("duplicate-value", field="observed_state.occupiedPorts")
        seen_ports.add(identity)
        occupied.append(normalized)
    occupied.sort(key=lambda item: (item["protocol"], item["port"], item["owner"]))

    reserved: list[dict[str, Any]] = []
    seen_resources: set[str] = set()
    for index, item in enumerate(
        _sequence(state.get("reservedResources"), "observed_state.reservedResources")
    ):
        field = f"observed_state.reservedResources[{index}]"
        resource = _exact_mapping(item, field, frozenset({"name", "owner"}))
        name = _resource(resource.get("name"), f"{field}.name")
        if name in seen_resources:
            _fail("duplicate-value", field="observed_state.reservedResources", value=name)
        seen_resources.add(name)
        reserved.append(
            {"name": name, "owner": _text(resource.get("owner"), f"{field}.owner", maximum=128)}
        )
    reserved.sort(key=lambda item: (item["name"], item["owner"]))

    installed: list[dict[str, Any]] = []
    seen_installed: set[str] = set()
    for index, item in enumerate(
        _sequence(state.get("installedServices"), "observed_state.installedServices")
    ):
        field = f"observed_state.installedServices[{index}]"
        service = _exact_mapping(
            item, field, frozenset({"id", "version", "definitionSha256", "status"})
        )
        service_id = _identifier(service.get("id"), f"{field}.id")
        if service_id in seen_installed:
            _fail("duplicate-value", field="observed_state.installedServices", value=service_id)
        seen_installed.add(service_id)
        version = _text(service.get("version"), f"{field}.version", maximum=128)
        installed.append(
            {
                "id": service_id,
                "version": version,
                "definitionSha256": _digest(
                    service.get("definitionSha256"), f"{field}.definitionSha256"
                ),
                "status": _enum(
                    service.get("status"),
                    f"{field}.status",
                    frozenset({"enabled", "disabled", "stopped", "unhealthy", "error"}),
                ),
            }
        )
    installed.sort(key=lambda item: item["id"])

    driver = state.get("driverVersion")
    ods_version = _text(state.get("odsVersion"), "observed_state.odsVersion", maximum=64)
    _semver(ods_version, "observed_state.odsVersion")
    normalized_driver = _nullable_driver_version(driver, "observed_state.driverVersion")
    return {
        "odsVersion": ods_version,
        "platform": _enum(
            state.get("platform"), "observed_state.platform", frozenset({"linux", "darwin", "windows"})
        ),
        "architecture": _enum(
            state.get("architecture"), "observed_state.architecture", frozenset({"amd64", "arm64"})
        ),
        "containerRuntime": _enum(
            state.get("containerRuntime"),
            "observed_state.containerRuntime",
            frozenset({"docker", "podman", "none"}),
        ),
        "gpuBackend": _enum(
            state.get("gpuBackend"),
            "observed_state.gpuBackend",
            frozenset({"amd", "nvidia", "apple", "cpu", "none"}),
        ),
        "driverVersion": normalized_driver,
        "available": {
            name: _integer(available.get(name), f"observed_state.available.{name}", 0, 2**63 - 1)
            for name in sorted(available_fields)
        },
        "occupiedPorts": occupied,
        "reservedResources": reserved,
        "installedServices": installed,
    }


def normalize_policy(value: Any) -> dict[str, Any]:
    fields = frozenset(
        {"allowedTrustTiers", "forbiddenHostPermissions", "allowExperimental", "requireApproval"}
    )
    policy = _exact_mapping(value, "policy", fields)
    return {
        "allowedTrustTiers": list(
            _token_list(
                policy.get("allowedTrustTiers"),
                "policy.allowedTrustTiers",
                frozenset({"bundled", "verified", "community", "local"}),
                require_one=True,
            )
        ),
        "forbiddenHostPermissions": list(
            _token_list(
                policy.get("forbiddenHostPermissions"),
                "policy.forbiddenHostPermissions",
                frozenset({"network", "gpu", "audio", "camera", "usb", "host-mount", "docker-socket", "privileged"}),
            )
        ),
        "allowExperimental": _boolean(policy.get("allowExperimental"), "policy.allowExperimental"),
        "requireApproval": _boolean(policy.get("requireApproval"), "policy.requireApproval"),
    }


def public_json_value(value: Any) -> Any:
    if isinstance(value, tuple):
        return [public_json_value(item) for item in value]
    if isinstance(value, list):
        return [public_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: public_json_value(item) for key, item in value.items()}
    return value


def build_plan(
    manifests: Any,
    *,
    requested_action: Any = "ensure",
    requested_services: Any = (),
    requested_capabilities: Any = (),
    provider_preferences: Any = None,
    missing_config_keys: Any = (),
    missing_secret_keys: Any = (),
    catalog_revision: Any,
    observed_state_revision: Any,
    observed_state: Any,
    policy_revision: Any,
    policy: Any,
    valid_until: Any,
) -> dict[str, Any]:
    """Resolve a deterministic, non-executing plan from explicit inputs."""

    if not isinstance(catalog_revision, str) or not _SHA256_RE.fullmatch(catalog_revision):
        _fail("invalid-catalog-revision")
    if not isinstance(observed_state_revision, str) or not _SHA256_RE.fullmatch(observed_state_revision):
        _fail("invalid-state-revision")
    if not isinstance(policy_revision, str) or not _SHA256_RE.fullmatch(policy_revision):
        _fail("invalid-policy-revision")
    if not isinstance(valid_until, str) or _UTC_RE.fullmatch(valid_until) is None:
        _fail("invalid-plan-expiry")
    try:
        datetime.strptime(valid_until, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise PlanningError("invalid-plan-expiry") from exc
    action = _enum(requested_action, "requested_action", frozenset({"ensure"}))
    state = normalize_host_state(observed_state)
    normalized_policy = normalize_policy(policy)
    computed_state_revision = hashlib.sha256(canonical_json_bytes(state)).hexdigest()
    if computed_state_revision != observed_state_revision:
        _fail("stale-observed-state", currentRevision=computed_state_revision)
    computed_policy_revision = hashlib.sha256(canonical_json_bytes(normalized_policy)).hexdigest()
    if computed_policy_revision != policy_revision:
        _fail("stale-policy", currentRevision=computed_policy_revision)
    current_ods = _semver(state["odsVersion"], "observed_state.odsVersion")

    records: dict[str, dict[str, Any]] = {}
    for index, manifest in enumerate(_sequence(manifests, "manifests")):
        record = adapt_manifest(manifest)
        service_id = record["id"]
        if service_id in records:
            _fail("duplicate-service", serviceId=service_id)
        records[service_id] = record
    eligible_provider_ids = {
        service_id
        for service_id, record in records.items()
        if _provider_is_eligible(record, state, normalized_policy, current_ods)
    }

    services = _unique_strings(requested_services, "requested_services", _identifier)
    capabilities = _unique_strings(
        requested_capabilities, "requested_capabilities", _capability
    )
    observed_missing_config = set(
        _unique_strings(missing_config_keys, "missing_config_keys", _config_key)
    )
    observed_missing_secrets = set(
        _unique_strings(missing_secret_keys, "missing_secret_keys", _config_key)
    )
    raw_preferences = {} if provider_preferences is None else _mapping(
        provider_preferences, "provider_preferences"
    )
    preferences: dict[str, str] = {}
    for key, value in raw_preferences.items():
        capability = _capability(key, "provider_preferences.key")
        service_id = _identifier(value, f"provider_preferences.{key}")
        preferences[capability] = service_id
    unknown_preferences = sorted(set(preferences) - set(capabilities) - {
        capability
        for record in records.values()
        for capability in record["requires"]
    })
    if unknown_preferences:
        _fail("unused-provider-preference", capabilities=unknown_preferences)

    selected: set[str] = set()
    for service_id in services:
        if service_id not in records:
            _fail("unknown-service", serviceId=service_id)
        selected.add(service_id)

    provider_bindings: dict[str, str] = {}
    for capability in capabilities:
        provider = _provider_for(
            capability, records, preferences, eligible_provider_ids
        )
        provider_bindings[capability] = provider
        selected.add(provider)

    edges: set[tuple[str, str]] = set()
    processed: set[str] = set()
    while selected - processed:
        service_id = sorted(selected - processed)[0]
        record = records[service_id]
        processed.add(service_id)
        for dependency in record["dependsOn"]:
            if dependency not in records:
                _fail(
                    "missing-dependency",
                    serviceId=service_id,
                    dependencyId=dependency,
                )
            selected.add(dependency)
            edges.add((dependency, service_id))
        for capability in record["requires"]:
            provider = _provider_for(
                capability, records, preferences, eligible_provider_ids
            )
            existing = provider_bindings.get(capability)
            if existing is not None and existing != provider:
                _fail("inconsistent-provider", capability=capability)
            provider_bindings[capability] = provider
            selected.add(provider)
            edges.add((provider, service_id))

    for service_id in sorted(selected):
        for conflict in records[service_id]["conflicts"]:
            if conflict in selected:
                _fail("service-conflict", services=sorted({service_id, conflict}))

    ports: dict[tuple[str, int], str] = {}
    resources: dict[str, str] = {}
    for service_id in sorted(selected):
        for port in records[service_id]["resources"]["hostPorts"]:
            identity = (port["protocol"], port["port"])
            if identity in ports:
                _fail(
                    "port-conflict",
                    port=port["port"],
                    protocol=port["protocol"],
                    services=[ports[identity], service_id],
                )
            ports[identity] = service_id
        for resource in records[service_id]["resources"]["exclusive"]:
            if resource in resources:
                _fail(
                    "resource-conflict",
                    resource=resource,
                    services=[resources[resource], service_id],
                )
            resources[resource] = service_id

    order = _topological_order(selected, edges)
    installed = {item["id"]: item for item in state["installedServices"]}
    occupied_ports = {
        (item["protocol"], item["port"]): item["owner"] for item in state["occupiedPorts"]
    }
    reserved_resources = {item["name"]: item["owner"] for item in state["reservedResources"]}
    for identity, service_id in ports.items():
        owner = occupied_ports.get(identity)
        if owner is not None and owner != service_id:
            _fail(
                "host-port-conflict",
                port=identity[1],
                protocol=identity[0],
                serviceId=service_id,
                owner=owner,
            )
    for resource, service_id in resources.items():
        owner = reserved_resources.get(resource)
        if owner is not None and owner != service_id:
            _fail(
                "host-resource-conflict", resource=resource, serviceId=service_id, owner=owner
            )

    optional: list[dict[str, Any]] = []
    for service_id in order:
        for capability in records[service_id]["optional"]:
            providers = sorted(
                record["id"]
                for record in records.values()
                if capability in record["provides"]
            )
            optional.append(
                {
                    "serviceId": service_id,
                    "capability": capability,
                    "availableProviders": providers,
                }
            )

    definitions: list[dict[str, Any]] = []
    operations: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    configuration_contracts: dict[str, dict[str, Any]] = {}
    required_configuration: dict[str, dict[str, Any]] = {}
    totals = {
        "downloadBytes": 0,
        "diskBytes": 0,
        "cpuMillicores": 0,
        "ramBytes": 0,
        "vramBytes": 0,
        "gpuCount": 0,
    }
    for service_id in order:
        record = records[service_id]
        if not record["definitionSha256"]:
            _fail("missing-definition-digest", serviceId=service_id)
        if (
            not record["legacy"]
            and record["serviceType"] == "docker"
            and not record["artifacts"]["images"]
            and not record["artifacts"]["builds"]
        ):
            _fail("missing-immutable-artifact", serviceId=service_id)
        declared_download = sum(
            item["downloadBytes"]
            for kind in ("images", "builds")
            for item in record["artifacts"][kind]
        )
        if not record["legacy"] and declared_download != record["estimates"]["downloadBytes"]:
            _fail(
                "artifact-estimate-mismatch",
                serviceId=service_id,
                artifactBytes=declared_download,
                estimatedBytes=record["estimates"]["downloadBytes"],
            )
        minimum = record["odsCompatibility"]["minimum"]
        maximum = record["odsCompatibility"]["maximum"]
        if minimum and current_ods < _semver(minimum, f"{service_id}.compatibility.minimum"):
            _fail("incompatible-ods-version", serviceId=service_id)
        if maximum and current_ods > _semver(maximum, f"{service_id}.compatibility.maximum"):
            _fail("incompatible-ods-version", serviceId=service_id)
        if record["legacy"]:
            warnings.append({"code": "legacy-manifest", "serviceId": service_id})
        else:
            requirements = record["requirements"]
            checks = (
                (state["platform"], requirements["platforms"], "incompatible-platform"),
                (state["architecture"], requirements["architectures"], "incompatible-architecture"),
                (state["containerRuntime"], requirements["containerRuntimes"], "incompatible-container-runtime"),
                (state["gpuBackend"], requirements["gpuBackends"], "incompatible-gpu-backend"),
            )
            for actual, allowed, code in checks:
                if actual not in allowed:
                    _fail(code, serviceId=service_id, observed=actual)
            minimum_driver = requirements["minDriverVersion"]
            if minimum_driver is not None:
                if state["driverVersion"] is None:
                    _fail("missing-driver-version", serviceId=service_id)
                if _driver_version(state["driverVersion"], "observed_state.driverVersion") < _driver_version(
                    minimum_driver, f"{service_id}.requirements.minDriverVersion"
                ):
                    _fail("incompatible-driver-version", serviceId=service_id)
            if record["trust"]["tier"] not in normalized_policy["allowedTrustTiers"]:
                _fail("untrusted-definition", serviceId=service_id, tier=record["trust"]["tier"])
            forbidden = sorted(
                set(record["resources"]["hostPermissions"])
                & set(normalized_policy["forbiddenHostPermissions"])
            )
            if forbidden:
                _fail("forbidden-host-permission", serviceId=service_id, permissions=forbidden)
            status = record["support"]["status"]
            if status == "unsupported":
                _fail("unsupported-service", serviceId=service_id)
            if status in {"experimental", "deprecated"}:
                if status == "experimental" and not normalized_policy["allowExperimental"]:
                    _fail("experimental-service-blocked", serviceId=service_id)
                warnings.append({"code": f"{status}-service", "serviceId": service_id})
        for item in record["configuration"]:
            previous = configuration_contracts.get(item["key"])
            if previous is not None and previous != item:
                _fail("configuration-contract-conflict", key=item["key"])
            configuration_contracts[item["key"]] = item
            if item["required"]:
                required_configuration[item["key"]] = item
        observed = installed.get(service_id)
        if (
            observed is not None
            and observed["version"] == record["version"]
            and observed["definitionSha256"] == record["definitionSha256"]
        ):
            operation = {
                "enabled": "noop",
                "disabled": "enable",
                "stopped": "enable",
                "unhealthy": "repair",
                "error": "repair",
            }[observed["status"]]
        elif observed is None:
            operation = "install"
        else:
            operation = "update"
        if operation != "noop":
            for name in totals:
                totals[name] += record["estimates"][name]
        operations.append({"serviceId": service_id, "action": operation})
        definitions.append(
            {
                "id": service_id,
                "serviceType": record["serviceType"],
                "manifestSchemaVersion": record["schemaVersion"],
                "version": record["version"],
                "dataSchemaVersion": record["dataSchemaVersion"],
                "odsCompatibility": public_json_value(record["odsCompatibility"]),
                "definitionSha256": record["definitionSha256"],
                "composeSha256": record["composeSha256"] or None,
                "requirements": public_json_value(record["requirements"]),
                "estimates": public_json_value(record["estimates"]),
                "configuration": public_json_value(record["configuration"]),
                "artifacts": public_json_value(record["artifacts"]),
                "resources": public_json_value(record["resources"]),
                "lifecycle": public_json_value(record["lifecycle"]),
                "data": public_json_value(record["data"]),
                "trust": public_json_value(record["trust"]),
                "support": public_json_value(record["support"]),
            }
        )

    if totals["diskBytes"] > state["available"]["diskBytes"]:
        _fail("insufficient-disk", required=totals["diskBytes"], available=state["available"]["diskBytes"])
    for estimate, available in (
        ("ramBytes", "ramBytes"),
        ("vramBytes", "vramBytes"),
        ("cpuMillicores", "cpuMillicores"),
    ):
        if totals[estimate] > state["available"][available]:
            warnings.append(
                {
                    "code": f"estimated-{estimate}-exceeds-available",
                    "required": totals[estimate],
                    "available": state["available"][available],
                }
            )
    if totals["gpuCount"] > state["available"]["gpuCount"]:
        _fail(
            "insufficient-gpu-count",
            required=totals["gpuCount"],
            available=state["available"]["gpuCount"],
        )

    required_config = sorted(
        key for key, item in required_configuration.items() if not item["secret"]
    )
    required_secrets = sorted(
        key for key, item in required_configuration.items() if item["secret"]
    )
    missing_names = (observed_missing_config & set(required_config)) | (
        observed_missing_secrets & set(required_secrets)
    )
    missing_configuration = []
    for key in sorted(missing_names):
        item = public_json_value(required_configuration[key])
        item.pop("default", None)
        missing_configuration.append(item)
    warnings.sort(key=lambda item: canonical_json_bytes(item))
    plan = {
        "schema": "ods.assistant-first.plan.v1",
        "requestedAction": action,
        "catalogRevision": catalog_revision,
        "observedStateRevision": observed_state_revision,
        "policyRevision": policy_revision,
        "validUntil": valid_until,
        "requestedServices": list(services),
        "requestedCapabilities": list(capabilities),
        "selectedServices": order,
        "operations": operations,
        "providerBindings": [
            {"capability": capability, "serviceId": provider_bindings[capability]}
            for capability in sorted(provider_bindings)
        ],
        "optionalCapabilities": optional,
        "definitions": definitions,
        "resourceDelta": totals,
        "requiredConfigKeys": required_config,
        "requiredSecretKeys": required_secrets,
        "missingRequiredConfigKeys": sorted(
            observed_missing_config & set(required_config)
        ),
        "missingRequiredSecretKeys": sorted(
            observed_missing_secrets & set(required_secrets)
        ),
        "missingConfiguration": missing_configuration,
        "dataEffects": [
            {"serviceId": definition["id"], "paths": definition["data"]}
            for definition in definitions
            if definition["data"]
        ],
        "rollbackEffects": [
            {
                "serviceId": definition["id"],
                "contract": definition["lifecycle"]["rollback"],
            }
            for definition in definitions
        ],
        "warnings": warnings,
        "blockers": [],
        "approval": {
            "required": normalized_policy["requireApproval"],
            "scope": "exact-plan-hash",
        },
    }
    plan_hash = hashlib.sha256(canonical_json_bytes(plan)).hexdigest()
    return {
        "schema": "ods.assistant-first.plan-envelope.v1",
        "planId": f"plan-{plan_hash[:24]}",
        "catalogRevision": catalog_revision,
        "observedStateRevision": observed_state_revision,
        "policyRevision": policy_revision,
        "planHash": plan_hash,
        "plan": plan,
    }
