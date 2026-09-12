from __future__ import annotations

import copy
import hashlib
import importlib.util
import math
from pathlib import Path

import pytest


MODULE = Path(__file__).resolve().parents[1] / "assistant_first_planner.py"
SPEC = importlib.util.spec_from_file_location("assistant_first_planner", MODULE)
assert SPEC is not None and SPEC.loader is not None
planner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(planner)

CATALOG = "a" * 64
HOST_STATE = {
    "odsVersion": "2.1.0",
    "platform": "linux",
    "architecture": "amd64",
    "containerRuntime": "docker",
    "gpuBackend": "cpu",
    "driverVersion": None,
    "available": {
        "diskBytes": 10_000_000_000,
        "ramBytes": 10_000_000_000,
        "vramBytes": 10_000_000_000,
        "cpuMillicores": 16_000,
        "gpuCount": 0,
    },
    "occupiedPorts": [],
    "reservedResources": [],
    "installedServices": [],
}
POLICY = {
    "allowedTrustTiers": ["bundled"],
    "forbiddenHostPermissions": ["docker-socket", "privileged"],
    "allowExperimental": False,
    "requireApproval": True,
}
STATE = hashlib.sha256(planner.canonical_json_bytes(HOST_STATE)).hexdigest()
POLICY_REVISION = hashlib.sha256(planner.canonical_json_bytes(POLICY)).hexdigest()


def manifest(
    service_id: str,
    *,
    depends_on: list[str] | None = None,
    provides: list[str] | None = None,
    requires: list[str] | None = None,
    optional: list[str] | None = None,
    conflicts: list[str] | None = None,
    priority: int = 0,
    ports: list[int] | None = None,
    resources: list[str] | None = None,
    config: list[str] | None = None,
    secrets: list[str] | None = None,
    schema: str = "ods.services.v2",
) -> dict:
    service = {
        "id": service_id,
        "version": "1.2.3",
        "data_schema_version": "1",
        "type": "docker",
        "depends_on": depends_on or [],
    }
    if schema == "ods.services.v2":
        service["planning"] = {
            "provides": provides or [],
            "requires": requires or [],
            "optional": optional or [],
            "conflicts": conflicts or [],
            "provider_priority": priority,
            "requirements": {
                "platforms": ["linux"],
                "architectures": ["amd64"],
                "container_runtimes": ["docker"],
                "gpu_backends": ["cpu"],
                "min_driver_version": None,
            },
            "estimates": {
                "download_bytes": 100,
                "disk_bytes": 200,
                "cpu_millicores": 100,
                "ram_bytes": 300,
                "vram_bytes": 0,
                "gpu_count": 0,
            },
            "resources": {
                "host_ports": [
                    {"port": port, "protocol": "tcp"} for port in (ports or [])
                ],
                "container_ports": [],
                "networks": [],
                "volumes": [],
                "devices": [],
                "exclusive": resources or [],
                "linux_capabilities": [],
                "host_permissions": ["network"],
            },
            "configuration": [
                {
                    "key": key,
                    "type": "string",
                    "required": True,
                    "secret": False,
                    "source": "user",
                    "restart_behavior": "service",
                }
                for key in (config or [])
            ]
            + [
                {
                    "key": key,
                    "type": "string",
                    "required": True,
                    "secret": True,
                    "source": "user",
                    "restart_behavior": "service",
                }
                for key in (secrets or [])
            ],
            "artifacts": {
                "images": [
                    {
                        "reference": f"example/{service_id}:1.2.3",
                        "digest": "sha256:" + "e" * 64,
                        "download_bytes": 100,
                    }
                ],
                "builds": [],
            },
            "lifecycle": {
                "health_checks": ["http:/health"],
                "readiness": ["healthy"],
                "setup_hook": None,
                "migration_hook": None,
                "rollback": "definition",
                "timeout_seconds": 120,
            },
            "data": [],
            "trust": {
                "tier": "bundled",
                "publisher": "ODS",
                "definition_signature": None,
            },
            "support": {"status": "supported", "url": None},
        }
    return {
        "schema_version": schema,
        "compatibility": {"ods_min": "2.0.0"},
        "service": service,
        "_catalog": {
            "definition_sha256": "sha256:" + "d" * 64,
            "compose_sha256": "",
        },
    }


def build(manifests: list[dict], **overrides):
    values = {
        "requested_services": [],
        "requested_capabilities": [],
        "provider_preferences": {},
        "missing_config_keys": [],
        "missing_secret_keys": [],
        "catalog_revision": CATALOG,
        "observed_state_revision": STATE,
        "observed_state": HOST_STATE,
        "policy_revision": POLICY_REVISION,
        "policy": POLICY,
        "valid_until": "2026-10-01T00:00:00Z",
    }
    values.update(overrides)
    return planner.build_plan(manifests, **values)


def error(code: str, call) -> planner.PlanningError:
    with pytest.raises(planner.PlanningError) as caught:
        call()
    assert caught.value.code == code
    assert caught.value.as_dict()["code"] == code
    return caught.value


def test_shuffled_inputs_have_byte_identical_plan_and_hash() -> None:
    records = [
        manifest("app", depends_on=["db"], requires=["route@1"]),
        manifest("db"),
        manifest("provider", provides=["route@1"], priority=2),
    ]
    first = build(
        records,
        requested_services=["app", "db"],
        requested_capabilities=["route@1"],
    )
    second = build(
        list(reversed(records)),
        requested_services=["db", "app"],
        requested_capabilities=["route@1"],
    )
    assert planner.canonical_json_bytes(first) == planner.canonical_json_bytes(second)
    assert first["planHash"] == second["planHash"]
    assert first["plan"]["selectedServices"] == ["db", "provider", "app"]


def test_utf8_is_unescaped_and_has_one_trailing_lf() -> None:
    encoded = planner.canonical_json_bytes({"label": "café"})
    assert b"caf\xc3\xa9" in encoded
    assert encoded.endswith(b"\n") and not encoded.endswith(b"\n\n")


def test_canonicalizer_rejects_lone_surrogates_as_a_domain_error() -> None:
    error("invalid-unicode", lambda: planner.canonical_json_bytes({"label": "\ud800"}))


def test_build_does_not_mutate_inputs() -> None:
    records = [manifest("app")]
    before = copy.deepcopy(records)
    build(records, requested_services=["app"])
    assert records == before


def test_v1_capabilities_are_non_operational_but_hard_dependencies_remain() -> None:
    legacy = manifest("legacy", depends_on=["dep"], schema="ods.services.v1")
    legacy["service"]["capabilities"] = {"provides": ["route@1"]}
    adapted = planner.adapt_manifest(legacy)
    assert adapted["provides"] == ()
    assert adapted["dependsOn"] == ("dep",)
    error(
        "missing-capability",
        lambda: build([legacy, manifest("dep")], requested_capabilities=["route@1"]),
    )


def test_provider_preference_beats_priority() -> None:
    result = build(
        [
            manifest("preferred", provides=["route@1"], priority=1),
            manifest("higher", provides=["route@1"], priority=9),
        ],
        requested_capabilities=["route@1"],
        provider_preferences={"route@1": "preferred"},
    )
    assert result["plan"]["selectedServices"] == ["preferred"]


def test_unique_highest_priority_provider_wins() -> None:
    result = build(
        [
            manifest("low", provides=["route@1"], priority=1),
            manifest("high", provides=["route@1"], priority=2),
        ],
        requested_capabilities=["route@1"],
    )
    assert result["plan"]["selectedServices"] == ["high"]


def test_semver_prerelease_does_not_satisfy_final_ods_minimum() -> None:
    state = copy.deepcopy(HOST_STATE)
    state["odsVersion"] = "2.0.0-rc.1"
    revision = hashlib.sha256(planner.canonical_json_bytes(state)).hexdigest()
    error(
        "incompatible-ods-version",
        lambda: build(
            [manifest("app")],
            requested_services=["app"],
            observed_state=state,
            observed_state_revision=revision,
        ),
    )


def test_driver_versions_use_deterministic_vendor_aware_natural_order() -> None:
    record = manifest("app")
    record["service"]["planning"]["requirements"]["gpu_backends"] = ["nvidia"]
    record["service"]["planning"]["requirements"]["min_driver_version"] = "550.54.14"
    state = copy.deepcopy(HOST_STATE)
    state["gpuBackend"] = "nvidia"
    state["driverVersion"] = "550.54.9"
    revision = hashlib.sha256(planner.canonical_json_bytes(state)).hexdigest()
    error(
        "incompatible-driver-version",
        lambda: build(
            [record],
            requested_services=["app"],
            observed_state=state,
            observed_state_revision=revision,
        ),
    )


def test_provider_selection_filters_incompatible_alternatives_before_priority() -> None:
    incompatible = manifest("high", provides=["route@1"], priority=9)
    incompatible["service"]["planning"]["requirements"]["platforms"] = ["darwin"]
    result = build(
        [incompatible, manifest("low", provides=["route@1"], priority=1)],
        requested_capabilities=["route@1"],
    )
    assert result["plan"]["selectedServices"] == ["low"]
    error(
        "incompatible-provider-preference",
        lambda: build(
            [incompatible, manifest("low", provides=["route@1"], priority=1)],
            requested_capabilities=["route@1"],
            provider_preferences={"route@1": "high"},
        ),
    )


def test_equal_priority_provider_is_ambiguous() -> None:
    caught = error(
        "ambiguous-provider",
        lambda: build(
            [manifest("alpha", provides=["route@1"]), manifest("beta", provides=["route@1"])],
            requested_capabilities=["route@1"],
        ),
    )
    assert caught.details["candidates"] == ["alpha", "beta"]


def test_missing_dependency_and_unknown_service_fail() -> None:
    error(
        "missing-dependency",
        lambda: build([manifest("app", depends_on=["missing"])], requested_services=["app"]),
    )
    error("unknown-service", lambda: build([], requested_services=["missing"]))


def test_dependency_cycle_fails() -> None:
    error(
        "dependency-cycle",
        lambda: build(
            [manifest("alpha", depends_on=["beta"]), manifest("beta", depends_on=["alpha"])],
            requested_services=["alpha"],
        ),
    )


def test_capability_cycle_fails() -> None:
    error(
        "dependency-cycle",
        lambda: build(
            [
                manifest("alpha", provides=["alpha-cap@1"], requires=["beta-cap@1"]),
                manifest("beta", provides=["beta-cap@1"], requires=["alpha-cap@1"]),
            ],
            requested_services=["alpha"],
        ),
    )


@pytest.mark.parametrize(
    ("records", "code"),
    [
        ([manifest("alpha", conflicts=["beta"]), manifest("beta")], "service-conflict"),
        ([manifest("alpha", ports=[8080]), manifest("beta", ports=[8080])], "port-conflict"),
        (
            [manifest("alpha", resources=["gpu:0"]), manifest("beta", resources=["gpu:0"])],
            "resource-conflict",
        ),
    ],
)
def test_selected_collisions_fail(records: list[dict], code: str) -> None:
    error(code, lambda: build(records, requested_services=["alpha", "beta"]))


def test_optional_capability_reports_availability_without_activation() -> None:
    result = build(
        [
            manifest("app", optional=["search@1"]),
            manifest("search", provides=["search@1"]),
        ],
        requested_services=["app"],
    )
    assert result["plan"]["selectedServices"] == ["app"]
    assert result["plan"]["optionalCapabilities"] == [
        {"serviceId": "app", "capability": "search@1", "availableProviders": ["search"]}
    ]


@pytest.mark.parametrize("service_id", ["../escape", "bad/name", "Upper", "two words", "-bad"])
def test_invalid_service_identifiers_fail(service_id: str) -> None:
    error("invalid-identifier", lambda: planner.adapt_manifest(manifest(service_id)))


def test_duplicate_values_and_services_fail() -> None:
    error(
        "duplicate-value",
        lambda: build([], requested_services=["same", "same"]),
    )
    error(
        "duplicate-service",
        lambda: build([manifest("same"), manifest("same")]),
    )


@pytest.mark.parametrize("revision", ["A" * 64, "a" * 63, "../state", "bad/state", ""])
def test_invalid_revisions_fail(revision: str) -> None:
    if len(revision) >= 63:
        error("invalid-catalog-revision", lambda: build([], catalog_revision=revision))
    else:
        error("invalid-state-revision", lambda: build([], observed_state_revision=revision))


@pytest.mark.parametrize("priority", [True, 1.5, math.nan, math.inf])
def test_bool_float_and_non_finite_integer_fields_fail(priority) -> None:
    error(
        "invalid-integer",
        lambda: planner.adapt_manifest(manifest("app", priority=priority)),
    )


def test_canonicalizer_rejects_float_and_unsupported_values() -> None:
    error("float-not-canonical", lambda: planner.canonical_json_bytes({"score": math.nan}))
    error("unsupported-json-type", lambda: planner.canonical_json_bytes({"items": (1, 2)}))


def test_unknown_and_secret_value_fields_fail_closed() -> None:
    unknown = manifest("app")
    unknown["service"]["planning"]["surprise"] = True
    error("invalid-object-fields", lambda: planner.adapt_manifest(unknown))
    secret = manifest("app")
    secret["service"]["planning"]["secret_values"] = {"TOKEN": "do-not-read"}
    caught = error("invalid-object-fields", lambda: planner.adapt_manifest(secret))
    assert "do-not-read" not in str(caught.as_dict())


def test_required_key_names_are_sorted_and_never_contain_values() -> None:
    result = build(
        [manifest("app", config=["Z_MODE", "A_MODE"], secrets=["Z_TOKEN", "A_TOKEN"])],
        requested_services=["app"],
        missing_config_keys=["A_MODE", "UNRELATED_MODE"],
        missing_secret_keys=["Z_TOKEN", "UNRELATED_TOKEN"],
    )
    assert result["plan"]["requiredConfigKeys"] == ["A_MODE", "Z_MODE"]
    assert result["plan"]["requiredSecretKeys"] == ["A_TOKEN", "Z_TOKEN"]
    assert result["plan"]["missingRequiredConfigKeys"] == ["A_MODE"]
    assert result["plan"]["missingRequiredSecretKeys"] == ["Z_TOKEN"]


def test_hash_covers_plan_only_and_excludes_itself() -> None:
    result = build([manifest("app")], requested_services=["app"])
    expected = hashlib.sha256(planner.canonical_json_bytes(result["plan"])).hexdigest()
    whole_envelope = hashlib.sha256(planner.canonical_json_bytes(result)).hexdigest()
    assert result["planHash"] == expected
    assert result["planHash"] != whole_envelope
    assert "planHash" not in result["plan"]


def test_state_and_policy_revisions_are_recomputed_not_trusted() -> None:
    changed_state = copy.deepcopy(HOST_STATE)
    changed_state["occupiedPorts"] = [{"port": 8080, "protocol": "tcp", "owner": "other"}]
    caught = error(
        "stale-observed-state",
        lambda: build([manifest("app")], observed_state=changed_state),
    )
    assert caught.details["currentRevision"] == hashlib.sha256(
        planner.canonical_json_bytes(planner.normalize_host_state(changed_state))
    ).hexdigest()
    changed_policy = copy.deepcopy(POLICY)
    changed_policy["allowExperimental"] = True
    error("stale-policy", lambda: build([manifest("app")], policy=changed_policy))


def test_exact_definition_resources_expiry_and_approval_are_in_hash() -> None:
    result = build([manifest("app", ports=[8080], resources=["gpu:0"])], requested_services=["app"])
    definition = result["plan"]["definitions"][0]
    assert result["planId"] == f"plan-{result['planHash'][:24]}"
    assert definition["definitionSha256"] == "sha256:" + "d" * 64
    assert definition["resources"]["hostPorts"] == [{"port": 8080, "protocol": "tcp"}]
    assert result["plan"]["resourceDelta"]["downloadBytes"] == 100
    assert result["plan"]["validUntil"] == "2026-10-01T00:00:00Z"
    assert result["plan"]["approval"] == {"required": True, "scope": "exact-plan-hash"}
    assert result["plan"]["operations"] == [{"serviceId": "app", "action": "install"}]


def test_host_collisions_and_hard_resource_limits_fail_before_execution() -> None:
    port_state = copy.deepcopy(HOST_STATE)
    port_state["occupiedPorts"] = [{"port": 8080, "protocol": "tcp", "owner": "other"}]
    port_revision = hashlib.sha256(planner.canonical_json_bytes(port_state)).hexdigest()
    error(
        "host-port-conflict",
        lambda: build(
            [manifest("app", ports=[8080])],
            requested_services=["app"],
            observed_state=port_state,
            observed_state_revision=port_revision,
        ),
    )
    disk_state = copy.deepcopy(HOST_STATE)
    disk_state["available"]["diskBytes"] = 1
    disk_revision = hashlib.sha256(planner.canonical_json_bytes(disk_state)).hexdigest()
    error(
        "insufficient-disk",
        lambda: build(
            [manifest("app")],
            requested_services=["app"],
            observed_state=disk_state,
            observed_state_revision=disk_revision,
        ),
    )


def test_platform_trust_permissions_and_support_fail_closed() -> None:
    incompatible = manifest("app")
    incompatible["service"]["planning"]["requirements"]["platforms"] = ["darwin"]
    error("incompatible-platform", lambda: build([incompatible], requested_services=["app"]))

    forbidden = manifest("app")
    forbidden["service"]["planning"]["resources"]["host_permissions"] = ["docker-socket"]
    error("forbidden-host-permission", lambda: build([forbidden], requested_services=["app"]))

    untrusted = manifest("app")
    untrusted["service"]["planning"]["trust"]["tier"] = "community"
    error("untrusted-definition", lambda: build([untrusted], requested_services=["app"]))

    unsupported = manifest("app")
    unsupported["service"]["planning"]["support"]["status"] = "unsupported"
    error("unsupported-service", lambda: build([unsupported], requested_services=["app"]))

    versioned = manifest("app")
    versioned["compatibility"]["ods_min"] = "3.0.0"
    error("incompatible-ods-version", lambda: build([versioned], requested_services=["app"]))


def test_soft_memory_estimate_is_a_stable_warning() -> None:
    state = copy.deepcopy(HOST_STATE)
    state["available"]["ramBytes"] = 1
    revision = hashlib.sha256(planner.canonical_json_bytes(state)).hexdigest()
    result = build(
        [manifest("app")],
        requested_services=["app"],
        observed_state=state,
        observed_state_revision=revision,
    )
    assert result["plan"]["warnings"] == [
        {"code": "estimated-ramBytes-exceeds-available", "required": 300, "available": 1}
    ]


def test_secret_configuration_has_metadata_but_never_a_value_or_default() -> None:
    record = manifest("app", secrets=["APP_TOKEN"])
    record["service"]["planning"]["configuration"][0]["default"] = "private-sentinel"
    caught = error("secret-default-forbidden", lambda: planner.adapt_manifest(record))
    assert "private-sentinel" not in str(caught.as_dict())
    result = build(
        [manifest("app", secrets=["APP_TOKEN"])],
        requested_services=["app"],
        missing_secret_keys=["APP_TOKEN"],
    )
    assert result["plan"]["missingConfiguration"] == [
        {
            "key": "APP_TOKEN",
            "type": "string",
            "required": True,
            "secret": True,
            "source": "user",
            "restartBehavior": "service",
        }
    ]
    assert "private-sentinel" not in planner.canonical_json_bytes(result).decode()


def test_docker_v2_requires_immutable_artifacts_and_consistent_download_estimate() -> None:
    missing = manifest("app")
    missing["service"]["planning"]["artifacts"]["images"] = []
    error("missing-immutable-artifact", lambda: build([missing], requested_services=["app"]))
    mismatched = manifest("app")
    mismatched["service"]["planning"]["estimates"]["download_bytes"] = 101
    error("artifact-estimate-mismatch", lambda: build([mismatched], requested_services=["app"]))


def test_manifest_rejects_wrong_typed_defaults_and_duplicate_artifact_outputs() -> None:
    wrong_default = manifest("app")
    wrong_default["service"]["planning"]["configuration"] = [
        {
            "key": "WORKERS",
            "type": "integer",
            "required": False,
            "secret": False,
            "source": "user",
            "restart_behavior": "service",
            "default": "four",
        }
    ]
    error("invalid-config-default", lambda: planner.adapt_manifest(wrong_default))

    duplicate_image = manifest("app")
    image = copy.deepcopy(duplicate_image["service"]["planning"]["artifacts"]["images"][0])
    image["digest"] = "sha256:" + "f" * 64
    duplicate_image["service"]["planning"]["artifacts"]["images"].append(image)
    error("duplicate-artifact", lambda: planner.adapt_manifest(duplicate_image))

    colliding_output = manifest("app")
    existing_image = colliding_output["service"]["planning"]["artifacts"]["images"][0]
    colliding_output["service"]["planning"]["artifacts"]["builds"] = [
        {
            "source": "https://example.invalid/source.git",
            "revision": "0123456789abcdef",
            "context_digest": "sha256:" + "a" * 64,
            "output": existing_image["reference"],
            "download_bytes": 0,
        }
    ]
    error("duplicate-artifact", lambda: planner.adapt_manifest(colliding_output))

    duplicate_data = manifest("app")
    entry = {
        "path": "data/app",
        "backup_class": "required",
        "owner": "extension",
        "uninstall": "preserve",
        "purge": "separate-approval",
    }
    duplicate_data["service"]["planning"]["data"] = [entry, copy.deepcopy(entry)]
    error("duplicate-value", lambda: planner.adapt_manifest(duplicate_data))


def test_all_shared_configuration_keys_must_have_identical_contracts() -> None:
    first = manifest("first", config=["MODE"])
    second = manifest("second", config=["MODE"])
    second["service"]["planning"]["configuration"][0]["required"] = False
    error(
        "configuration-contract-conflict",
        lambda: build([first, second], requested_services=["first", "second"]),
    )


@pytest.mark.parametrize(
    ("status", "action", "download"),
    [
        ("enabled", "noop", 0),
        ("disabled", "enable", 100),
        ("stopped", "enable", 100),
        ("unhealthy", "repair", 100),
        ("error", "repair", 100),
    ],
)
def test_installed_state_drives_exact_operation_and_resource_delta(
    status: str, action: str, download: int
) -> None:
    state = copy.deepcopy(HOST_STATE)
    state["installedServices"] = [
        {
            "id": "app",
            "version": "1.2.3",
            "definitionSha256": "sha256:" + "d" * 64,
            "status": status,
        }
    ]
    revision = hashlib.sha256(planner.canonical_json_bytes(state)).hexdigest()
    result = build(
        [manifest("app")],
        requested_services=["app"],
        observed_state=state,
        observed_state_revision=revision,
    )
    assert result["plan"]["operations"] == [{"serviceId": "app", "action": action}]
    assert result["plan"]["resourceDelta"]["downloadBytes"] == download


@pytest.mark.parametrize("expiry", ["2026-99-01T00:00:00Z", "2026-10-01 00:00:00Z", ""])
def test_expiry_must_be_a_real_canonical_utc_timestamp(expiry: str) -> None:
    error("invalid-plan-expiry", lambda: build([], valid_until=expiry))
