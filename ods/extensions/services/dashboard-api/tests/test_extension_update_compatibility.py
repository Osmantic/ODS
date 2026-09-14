from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

import assistant_first_planner as planner
import extension_lockfile as lockfile
import extension_update_compatibility as update_compatibility
from extension_planning_contract import computed_catalog_revision


LOCKED_CATALOG_REVISION = "a" * 64


def locked_extension(
    service_id: str,
    *,
    desired_state: str = "enabled",
    version: str = "1.2.3",
    minimum: str = "2.0.0",
    maximum: str | None = "2.9.9",
    digest: str = "d",
) -> dict:
    return {
        "id": service_id,
        "desiredState": desired_state,
        "version": version,
        "manifestSchemaVersion": "ods.services.v2",
        "dataSchemaVersion": "1",
        "odsCompatibility": {"minimum": minimum, "maximum": maximum},
        "definitionHashes": {
            "manifestSha256": "sha256:" + digest * 64,
            "composeSha256": "sha256:" + "c" * 64,
        },
        "imageDigests": ["sha256:" + "e" * 64],
        "dependencyEdges": [],
        "configuration": {
            "schemaSha256": "f" * 64,
            "presentConfigKeys": [],
            "presentSecretKeys": [],
            "secretReference": None,
        },
    }


def source_lockfile(*extensions: dict, ods_version: str = "2.6.0") -> dict:
    document = {
        "schema": "ods.extensions.lockfile.v1",
        "odsVersion": ods_version,
        "catalogRevision": LOCKED_CATALOG_REVISION,
        "platform": "linux",
        "architecture": "amd64",
        "containerRuntime": "docker",
        "runtimeMode": "assistant-first",
        "postCommitObservedStateRevision": "1" * 64,
        "extensions": sorted(copy.deepcopy(extensions), key=lambda item: item["id"]),
        "lastCommittedTransaction": {
            "transactionId": "txn-" + "2" * 24,
            "planHash": "3" * 64,
            "sequence": 1,
            "plannedObservedStateRevision": "4" * 64,
        },
        "priorLockfileHash": None,
        "backupReference": "backup-v1-20260912T190000Z",
    }
    return lockfile.lockfile_envelope(document)


def catalog_entry(
    service_id: str,
    *,
    version: str = "1.2.3",
    minimum: str = "2.0.0",
    maximum: str | None = "2.9.9",
    digest: str = "d",
    schema: str = "ods.services.v2",
) -> dict:
    planning = {
        "serviceType": "docker",
        "version": version,
        "dataSchemaVersion": "1",
        "odsCompatibility": {"minimum": minimum, "maximum": maximum},
        "definitionSha256": "sha256:" + digest * 64,
        "composeSha256": "sha256:" + "c" * 64,
        "dependsOn": [],
        "provides": [],
        "requires": [],
        "optional": [],
        "conflicts": [],
        "providerPriority": 0,
        "requirements": {
            "platforms": ["linux"],
            "architectures": ["amd64"],
            "containerRuntimes": ["docker"],
            "gpuBackends": ["cpu"],
            "minDriverVersion": None,
        },
        "estimates": {
            "downloadBytes": 100,
            "diskBytes": 200,
            "cpuMillicores": 100,
            "ramBytes": 300,
            "vramBytes": 0,
            "gpuCount": 0,
        },
        "resources": {
            "hostPorts": [],
            "containerPorts": [],
            "networks": [],
            "volumes": [],
            "devices": [],
            "exclusive": [],
            "linuxCapabilities": [],
            "hostPermissions": ["network"],
        },
        "configuration": [],
        "artifacts": {
            "images": [
                {
                    "reference": f"example/{service_id}:{version}",
                    "digest": "sha256:" + "e" * 64,
                    "downloadBytes": 100,
                }
            ],
            "builds": [],
        },
        "lifecycle": {
            "healthChecks": ["http://127.0.0.1/health"],
            "readiness": ["healthy"],
            "setupHook": None,
            "migrationHook": None,
            "rollback": "definition",
            "timeoutSeconds": 120,
        },
        "data": [],
        "trust": {
            "tier": "bundled",
            "publisher": "ODS",
            "definitionSignature": None,
        },
        "support": {"status": "supported", "url": None},
        "legacy": False,
    }
    if schema == "ods.services.v1":
        planning = {
            key: planning[key]
            for key in (
                "serviceType",
                "version",
                "dataSchemaVersion",
                "odsCompatibility",
                "definitionSha256",
                "composeSha256",
                "dependsOn",
                "legacy",
            )
        }
        planning.update(
            {
                "version": "0.0.0-legacy",
                "dataSchemaVersion": "legacy",
                "odsCompatibility": {"minimum": "", "maximum": ""},
                "legacy": True,
            }
        )
    return {
        "id": service_id,
        "manifest_schema_version": schema,
        "planning": planning,
    }


def assess(source: dict, entries: list[dict], **overrides) -> dict:
    values = {
        "installed_ods_version": source["lockfile"]["odsVersion"],
        "candidate_ods_version": "3.0.0",
        "candidate_catalog": entries,
        "candidate_catalog_revision": computed_catalog_revision(entries),
    }
    values.update(overrides)
    return update_compatibility.assess_update_compatibility(source, **values)


def test_exact_compatible_definition_is_retained_and_hash_bound() -> None:
    source = source_lockfile(
        locked_extension("app", maximum="3.1.0"),
    )
    entries = [catalog_entry("app", maximum="3.1.0")]

    envelope = assess(source, entries)
    assessment = envelope["assessment"]

    assert envelope["schema"] == "ods.extensions.update-compatibility-envelope.v1"
    assert envelope["sourceLockfileHash"] == source["lockfileHash"]
    assert (
        envelope["assessmentHash"]
        == hashlib.sha256(planner.canonical_json_bytes(assessment)).hexdigest()
    )
    assert assessment["canUpdate"] is True
    assert assessment["requiresExtensionPlan"] is False
    assert assessment["blockers"] == []
    assert assessment["warnings"] == []
    assert assessment["decisions"][0]["disposition"] == "retain"


def test_incompatible_lock_requires_newer_compatible_extension_plan() -> None:
    source = source_lockfile(locked_extension("app"))
    entries = [catalog_entry("app", version="2.0.0", maximum="3.5.0", digest="b")]

    assessment = assess(source, entries)["assessment"]

    assert assessment["canUpdate"] is True
    assert assessment["requiresExtensionPlan"] is True
    assert assessment["decisions"][0]["disposition"] == "upgrade-required"
    assert assessment["requiredUpgrades"] == [
        {
            "serviceId": "app",
            "fromVersion": "1.2.3",
            "toVersion": "2.0.0",
            "definitionHashes": {
                "manifestSha256": "sha256:" + "b" * 64,
                "composeSha256": "sha256:" + "c" * 64,
            },
            "imageDigests": ["sha256:" + "e" * 64],
        }
    ]


def test_incompatible_enabled_extension_blocks_without_usable_upgrade() -> None:
    source = source_lockfile(locked_extension("app"))
    entries = [catalog_entry("app", version="2.0.0", maximum="2.9.9", digest="b")]

    assessment = assess(source, entries)["assessment"]

    assert assessment["canUpdate"] is False
    assert assessment["requiresExtensionPlan"] is False
    assert assessment["blockers"] == [
        {"code": "candidate-extension-incompatible", "serviceId": "app"}
    ]
    assert assessment["decisions"][0]["disposition"] == "blocked"


def test_missing_enabled_extension_fails_closed() -> None:
    source = source_lockfile(locked_extension("app", maximum="3.1.0"))

    assessment = assess(source, [catalog_entry("other")])["assessment"]

    assert assessment["canUpdate"] is False
    assert assessment["blockers"] == [
        {"code": "candidate-extension-missing", "serviceId": "app"}
    ]


def test_disabled_incompatible_extension_is_reported_without_being_enabled() -> None:
    source = source_lockfile(
        locked_extension("app", desired_state="disabled"),
    )

    assessment = assess(source, [catalog_entry("other")])["assessment"]

    assert assessment["canUpdate"] is True
    assert assessment["requiresExtensionPlan"] is False
    assert assessment["requiredUpgrades"] == []
    assert assessment["warnings"] == [
        {"code": "candidate-extension-missing", "serviceId": "app"}
    ]
    assert assessment["decisions"][0]["disposition"] == "disabled-incompatible"


def test_compatible_locked_extension_reports_optional_newer_candidate() -> None:
    source = source_lockfile(locked_extension("app", maximum="3.1.0"))
    entries = [catalog_entry("app", version="2.0.0", maximum="3.1.0", digest="b")]

    assessment = assess(source, entries)["assessment"]

    assert assessment["canUpdate"] is True
    assert assessment["requiresExtensionPlan"] is False
    assert assessment["requiredUpgrades"] == []
    assert assessment["warnings"] == [
        {"code": "extension-upgrade-available", "serviceId": "app"}
    ]
    assert assessment["decisions"][0]["disposition"] == "upgrade-available"


@pytest.mark.parametrize(
    ("entry", "code"),
    [
        (
            catalog_entry("app", version="1.2.2"),
            "candidate-extension-version-regression",
        ),
        (catalog_entry("app", digest="b"), "candidate-extension-definition-drift"),
        (
            catalog_entry("app", version="1.2.3+rebuilt"),
            "candidate-extension-version-ambiguous",
        ),
        (
            catalog_entry("app", schema="ods.services.v1"),
            "candidate-extension-legacy",
        ),
    ],
)
def test_enabled_extension_rejects_unsafe_candidate_identity(
    entry: dict, code: str
) -> None:
    source = source_lockfile(locked_extension("app", maximum="3.1.0"))

    assessment = assess(source, [entry])["assessment"]

    assert assessment["canUpdate"] is False
    assert assessment["blockers"] == [{"code": code, "serviceId": "app"}]


def test_same_version_metadata_change_is_definition_drift() -> None:
    source = source_lockfile(locked_extension("app", maximum="3.1.0"))
    entry = catalog_entry("app", maximum="3.5.0")

    assessment = assess(source, [entry])["assessment"]

    assert assessment["canUpdate"] is False
    assert assessment["blockers"] == [
        {"code": "candidate-extension-definition-drift", "serviceId": "app"}
    ]


def test_required_upgrade_must_have_an_immutable_artifact() -> None:
    source = source_lockfile(locked_extension("app"))
    entry = catalog_entry("app", version="2.0.0", maximum="3.5.0", digest="b")
    entry["planning"]["artifacts"] = {"images": [], "builds": []}
    entry["planning"]["estimates"]["downloadBytes"] = 0

    assessment = assess(source, [entry])["assessment"]

    assert assessment["canUpdate"] is False
    assert assessment["blockers"] == [
        {"code": "candidate-extension-artifact-missing", "serviceId": "app"}
    ]


def test_prerelease_precedence_requires_a_real_newer_candidate() -> None:
    source = source_lockfile(
        locked_extension("app", version="2.0.0-rc.1", maximum="2.9.9"),
        ods_version="2.6.0",
    )
    entries = [catalog_entry("app", version="2.0.0", maximum="3.1.0", digest="b")]

    assessment = assess(source, entries)["assessment"]

    assert assessment["requiredUpgrades"][0]["toVersion"] == "2.0.0"


def test_candidate_catalog_order_does_not_change_assessment_bytes() -> None:
    source = source_lockfile(
        locked_extension("alpha", maximum="3.1.0", digest="a"),
        locked_extension("beta", maximum="3.1.0", digest="b"),
    )
    entries = [
        catalog_entry("alpha", maximum="3.1.0", digest="a"),
        catalog_entry("beta", maximum="3.1.0", digest="b"),
    ]
    revision = computed_catalog_revision(entries)

    first = assess(source, entries, candidate_catalog_revision=revision)
    second = assess(
        source, list(reversed(entries)), candidate_catalog_revision=revision
    )

    assert planner.canonical_json_bytes(first) == planner.canonical_json_bytes(second)
    assert [item["serviceId"] for item in first["assessment"]["decisions"]] == [
        "alpha",
        "beta",
    ]


def test_assessment_does_not_mutate_inputs() -> None:
    source = source_lockfile(locked_extension("app", maximum="3.1.0"))
    entries = [catalog_entry("app", maximum="3.1.0")]
    source_before = copy.deepcopy(source)
    entries_before = copy.deepcopy(entries)

    assess(source, entries)

    assert source == source_before
    assert entries == entries_before


def test_generated_catalog_is_a_valid_candidate_for_empty_desired_state() -> None:
    catalog_path = (
        Path(__file__).resolve().parents[4] / "config" / "extensions-catalog.json"
    )
    document = json.loads(catalog_path.read_text(encoding="utf-8"))
    source = source_lockfile()

    assessment = assess(
        source,
        document["extensions"],
        candidate_ods_version="2.6.0",
        candidate_catalog_revision=document["catalog_revision"],
    )["assessment"]

    assert assessment["canUpdate"] is True
    assert assessment["decisions"] == []


def test_stale_installed_version_and_catalog_revision_are_rejected() -> None:
    source = source_lockfile(locked_extension("app"))
    entries = [catalog_entry("app")]

    with pytest.raises(
        update_compatibility.ExtensionUpdateCompatibilityError,
        match="stale-source-lockfile",
    ):
        assess(source, entries, installed_ods_version="2.6.1")

    with pytest.raises(
        update_compatibility.ExtensionUpdateCompatibilityError,
        match="stale-candidate-catalog",
    ):
        assess(source, entries, candidate_catalog_revision="9" * 64)


def test_candidate_ods_version_regression_is_rejected() -> None:
    source = source_lockfile(
        locked_extension("app", minimum="2.0.0", maximum="3.1.0"),
        ods_version="2.6.0",
    )
    entries = [catalog_entry("app", minimum="2.0.0", maximum="3.1.0")]

    with pytest.raises(
        update_compatibility.ExtensionUpdateCompatibilityError,
        match="candidate-ods-version-regression",
    ):
        assess(source, entries, candidate_ods_version="2.5.9")


def test_invalid_or_duplicate_candidate_catalog_is_rejected() -> None:
    source = source_lockfile(locked_extension("app"))
    entries = [catalog_entry("app"), catalog_entry("app")]

    with pytest.raises(
        update_compatibility.ExtensionUpdateCompatibilityError,
        match="duplicate-candidate-extension",
    ):
        assess(source, entries)

    malformed = [catalog_entry("app")]
    malformed[0]["planning"]["unknown"] = True
    with pytest.raises(
        update_compatibility.ExtensionUpdateCompatibilityError,
        match="invalid-candidate-catalog",
    ):
        assess(source, malformed)


def test_invalid_source_lockfile_is_rejected_without_projecting_values() -> None:
    source = source_lockfile(locked_extension("app"))
    source["lockfile"]["extensions"][0]["secretValue"] = "do-not-project"
    entries = [catalog_entry("app")]

    with pytest.raises(
        update_compatibility.ExtensionUpdateCompatibilityError,
        match="invalid-source-lockfile",
    ) as caught:
        assess(source, entries)

    assert caught.value.as_dict() == {
        "code": "invalid-source-lockfile",
        "details": {"lockfileCode": "extension-fields"},
    }
    assert "do-not-project" not in str(caught.value.as_dict())
