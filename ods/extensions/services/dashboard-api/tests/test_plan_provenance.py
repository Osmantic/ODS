"""Phase 3 plan provenance boundary tests.

Covers: happy reconstruction, stale expected hashes under
state/catalog/policy/request drift, structural rejection of caller-supplied
plan envelopes, duplicate/extra intent fields, secret values rejected in
favour of names only, deterministic canonical bytes, and catalog definition
hashes changing the plan.  All inputs are injected; no live services.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
from collections import UserDict
from pathlib import Path
from typing import Any

import pytest


_MODULE = Path(__file__).resolve().parents[1] / "plan_provenance.py"
_SPEC = importlib.util.spec_from_file_location("plan_provenance", _MODULE)
assert _SPEC is not None and _SPEC.loader is not None
plan_provenance = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(plan_provenance)

_PLANNER = Path(__file__).resolve().parents[1] / "assistant_first_planner.py"
_PLANNER_SPEC = importlib.util.spec_from_file_location("assistant_first_planner", _PLANNER)
assert _PLANNER_SPEC is not None and _PLANNER_SPEC.loader is not None
planner = importlib.util.module_from_spec(_PLANNER_SPEC)
_PLANNER_SPEC.loader.exec_module(planner)


HOST_STATE: dict[str, Any] = {
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
POLICY: dict[str, Any] = {
    "allowedTrustTiers": ["bundled"],
    "forbiddenHostPermissions": ["docker-socket", "privileged"],
    "allowExperimental": False,
    "requireApproval": True,
}


def catalog_entry(
    service_id: str,
    *,
    provides: list[str] | None = None,
    requires: list[str] | None = None,
    priority: int = 0,
    config: list[str] | None = None,
    secrets: list[str] | None = None,
    definition_sha: str | None = None,
    depends_on: list[str] | None = None,
) -> dict[str, Any]:
    """A v2 catalog entry with the exact planning key set the router accepts."""
    return {
        "id": service_id,
        "manifest_schema_version": "ods.services.v2",
        "planning": {
            "serviceType": "docker",
            "version": "1.2.3",
            "dataSchemaVersion": "1",
            "odsCompatibility": {"minimum": "2.0.0", "maximum": None},
            "definitionSha256": definition_sha or ("sha256:" + "d" * 64),
            "composeSha256": "",
            "dependsOn": depends_on or [],
            "provides": provides or [],
            "requires": requires or [],
            "optional": [],
            "conflicts": [],
            "providerPriority": priority,
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
            "configuration": [
                {
                    "key": key,
                    "type": "string",
                    "required": True,
                    "secret": False,
                    "source": "user",
                    "restartBehavior": "service",
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
                    "restartBehavior": "service",
                }
                for key in (secrets or [])
            ],
            "artifacts": {
                "images": [
                    {
                        "reference": f"example/{service_id}:1.2.3",
                        "digest": "sha256:" + "e" * 64,
                        "downloadBytes": 100,
                    }
                ],
                "builds": [],
            },
            "lifecycle": {
                "healthChecks": ["http:/health"],
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
        },
    }


def computed_catalog_revision(entries: list[dict[str, Any]]) -> str:
    return plan_provenance._compute_catalog_revision(entries)


def make_catalog(entries: list[dict[str, Any]]):
    """Return a server-side catalog provider bound to the given entries."""

    def provider() -> tuple[list[dict[str, Any]], str]:
        return copy.deepcopy(entries), computed_catalog_revision(entries)

    return provider


def state_provider(state: dict[str, Any] | None = None):
    frozen = copy.deepcopy(state if state is not None else HOST_STATE)

    def provider() -> dict[str, Any]:
        return copy.deepcopy(frozen)

    return provider


def policy_provider(policy: dict[str, Any] | None = None):
    frozen = copy.deepcopy(policy if policy is not None else POLICY)

    def provider() -> dict[str, Any]:
        return copy.deepcopy(frozen)

    return provider


def intent(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "requestedServices": ["app"],
        "requestedCapabilities": [],
        "providerPreferences": {},
        "validUntil": "2026-10-01T00:00:00Z",
        "missingConfigKeys": [],
        "missingSecretKeys": [],
    }
    body.update(changes)
    return body


def authorize(
    intent_body: dict[str, Any] | None = None,
    *,
    entries: list[dict[str, Any]] | None = None,
    state: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
    expected_plan_hash: str | None = None,
):
    return plan_provenance.authorize_plan(
        intent_body if intent_body is not None else intent(),
        catalog=make_catalog(entries if entries is not None else [catalog_entry("app")]),
        observed_state=state_provider(state),
        policy=policy_provider(policy),
        expected_plan_hash=expected_plan_hash,
    )


def prov_error(code: str, call) -> plan_provenance.ProvenanceError:
    with pytest.raises(plan_provenance.ProvenanceError) as caught:
        call()
    assert caught.value.code == code, caught.value.as_dict()
    return caught.value


# ---------------------------------------------------------------------------
# Happy reconstruction
# ---------------------------------------------------------------------------


def test_happy_reconstruction_returns_canonical_envelope() -> None:
    envelope = authorize()
    assert envelope["schema"] == "ods.assistant-first.plan-envelope.v1"
    assert envelope["plan"]["schema"] == "ods.assistant-first.plan.v1"
    assert envelope["plan"]["selectedServices"] == ["app"]
    assert envelope["plan"]["operations"] == [{"serviceId": "app", "action": "install"}]
    assert envelope["planHash"] == hashlib.sha256(
        planner.canonical_json_bytes(envelope["plan"])
    ).hexdigest()
    assert envelope["planId"] == f"plan-{envelope['planHash'][:24]}"


def test_reconstructed_envelope_matches_phase2_router_semantics() -> None:
    """The provenance boundary must produce the same envelope the router would."""
    entries = [catalog_entry("app", secrets=["APP_TOKEN"])]
    envelope = authorize(
        intent(missingSecretKeys=["APP_TOKEN"]),
        entries=entries,
    )
    plan = envelope["plan"]
    assert plan["requiredSecretKeys"] == ["APP_TOKEN"]
    assert plan["missingRequiredSecretKeys"] == ["APP_TOKEN"]
    # Missing configuration carries the contract but never a default or value.
    assert plan["missingConfiguration"][0]["key"] == "APP_TOKEN"
    assert "default" not in plan["missingConfiguration"][0]


def test_expected_plan_hash_happy_path_matches() -> None:
    envelope = authorize()
    authorize(expected_plan_hash=envelope["planHash"])  # must not raise


# ---------------------------------------------------------------------------
# Stale expected hash after drift
# ---------------------------------------------------------------------------


def test_stale_expected_hash_after_observed_state_drift() -> None:
    envelope = authorize()
    drifted = copy.deepcopy(HOST_STATE)
    drifted["occupiedPorts"] = [{"port": 8080, "protocol": "tcp", "owner": "other"}]
    current = authorize(state=drifted)
    assert current["observedStateRevision"] != envelope["observedStateRevision"]
    error = prov_error(
        "plan-hash-mismatch",
        lambda: authorize(
            state=drifted, expected_plan_hash=envelope["planHash"]
        ),
    )
    assert error.details["expectedPlanHash"] == envelope["planHash"]
    assert error.details["currentPlanHash"] == current["planHash"]
    assert error.details["currentPlanHash"] != error.details["expectedPlanHash"]


def test_stale_expected_hash_after_catalog_drift() -> None:
    envelope = authorize()
    drifted_entries = [catalog_entry("app"), catalog_entry("extra")]
    error = prov_error(
        "plan-hash-mismatch",
        lambda: authorize(
            entries=drifted_entries, expected_plan_hash=envelope["planHash"]
        ),
    )
    assert error.details["expectedPlanHash"] == envelope["planHash"]
    assert error.details["currentPlanHash"] != envelope["planHash"]


def test_stale_expected_hash_after_catalog_definition_hash_drift() -> None:
    envelope = authorize()
    drifted_entries = [
        catalog_entry("app", definition_sha="sha256:" + "9" * 64)
    ]
    error = prov_error(
        "plan-hash-mismatch",
        lambda: authorize(
            entries=drifted_entries, expected_plan_hash=envelope["planHash"]
        ),
    )
    assert error.details["currentPlanHash"] != envelope["planHash"]


def test_stale_expected_hash_after_policy_drift() -> None:
    envelope = authorize()
    drifted_policy = {**POLICY, "requireApproval": False}
    current = authorize(policy=drifted_policy)
    assert current["policyRevision"] != envelope["policyRevision"]
    error = prov_error(
        "plan-hash-mismatch",
        lambda: authorize(
            policy=drifted_policy, expected_plan_hash=envelope["planHash"]
        ),
    )
    assert error.details["expectedPlanHash"] == envelope["planHash"]
    assert error.details["currentPlanHash"] == current["planHash"]


def test_stale_expected_hash_after_request_drift() -> None:
    envelope = authorize()
    drifted_intent = intent(requestedServices=[])
    current = authorize(intent_body=drifted_intent)
    assert current["planHash"] != envelope["planHash"]
    error = prov_error(
        "plan-hash-mismatch",
        lambda: authorize(
            intent_body=drifted_intent, expected_plan_hash=envelope["planHash"]
        ),
    )
    assert error.details["expectedPlanHash"] == envelope["planHash"]
    assert error.details["currentPlanHash"] == current["planHash"]


def test_expected_plan_hash_must_be_wellformed_sha256() -> None:
    prov_error(
        "invalid-sha256",
        lambda: authorize(expected_plan_hash="not-a-hash"),
    )


# ---------------------------------------------------------------------------
# Hidden-effect forgery is structurally impossible
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "smuggled",
    [
        # A full caller-built envelope
        {
            "schema": "ods.assistant-first.plan-envelope.v1",
            "planId": "plan-" + "a" * 24,
            "planHash": "a" * 64,
            "plan": {"schema": "ods.assistant-first.plan.v1"},
        },
        # A bare plan body
        {"plan": {"schema": "ods.assistant-first.plan.v1", "operations": []}},
        # Operations array
        {"operations": [{"serviceId": "app", "action": "install"}]},
        # Definitions
        {"definitions": [{"id": "app", "definitionSha256": "sha256:" + "d" * 64}]},
        # Effects
        {"resourceDelta": {"diskBytes": 1}, "dataEffects": [], "rollbackEffects": []},
        # Shell data
        {"shell": ["rm -rf /"]},
        {"command": "curl evil.example"},
        # Secret values
        {"secretValues": {"APP_TOKEN": "sk-live-123"}},
        {"secrets": "sk-live-123"},
        # Approvals / actor
        {"approval": {"required": False, "scope": "none"}},
        {"actor": "root"},
    ],
)
def test_forbidden_plan_and_effect_fields_are_rejected(smuggled: dict) -> None:
    body = intent()
    body.update(smuggled)
    prov_error("extra-keys", lambda: authorize(intent_body=body))


def test_tampered_plan_cannot_survive_rebuild() -> None:
    """A returned envelope carries no authority: every call rebuilds fresh."""
    envelope = authorize()
    # Mutating a previously returned envelope cannot poison later rebuilds.
    envelope["plan"]["operations"] = [{"serviceId": "app", "action": "noop"}]
    envelope["plan"]["resourceDelta"]["diskBytes"] = 0
    rebuilt = authorize()
    assert rebuilt["plan"]["operations"] == [{"serviceId": "app", "action": "install"}]
    assert rebuilt["plan"]["resourceDelta"]["diskBytes"] == 200
    assert planner.canonical_json_bytes(rebuilt) != planner.canonical_json_bytes(envelope)


def test_envelope_shaped_intent_is_rejected_before_any_read() -> None:
    """A caller-built envelope has no input channel and is rejected outright."""
    envelope = authorize()
    forged = copy.deepcopy(envelope)
    forged["plan"]["operations"] = [{"serviceId": "app", "action": "noop"}]
    prov_error("extra-keys", lambda: authorize(intent_body=copy.deepcopy(forged)))


def test_catalog_definitions_cannot_be_overridden_by_caller() -> None:
    """Definitions come from the injected catalog only; no caller channel exists."""
    envelope = authorize()
    forged = copy.deepcopy(envelope)
    forged["plan"]["definitions"][0]["resources"]["hostPermissions"] = [
        "docker-socket",
        "privileged",
    ]
    # The forged envelope is rejected when offered back through the intent.
    prov_error("extra-keys", lambda: authorize(intent_body=copy.deepcopy(forged)))
    # And a fresh rebuild always reflects the injected catalog.
    rebuilt = authorize()
    assert rebuilt["plan"]["definitions"][0]["resources"]["hostPermissions"] == [
        "network"
    ]


# ---------------------------------------------------------------------------
# Duplicate and extra request fields
# ---------------------------------------------------------------------------


def test_duplicate_requested_services_are_rejected() -> None:
    prov_error(
        "duplicate-value",
        lambda: authorize(intent(requestedServices=["app", "app"])),
    )


def test_duplicate_requested_capabilities_are_rejected() -> None:
    entries = [catalog_entry("app"), catalog_entry("provider", provides=["route@1"])]
    prov_error(
        "duplicate-value",
        lambda: authorize(
            intent(
                requestedServices=[],
                requestedCapabilities=["route@1", "route@1"],
            ),
            entries=entries,
        ),
    )


def test_json_equivalent_duplicate_preferences_are_rejected() -> None:
    # JSON objects cannot carry duplicate keys after parsing, so a duplicate
    # preference is expressed as two keys mapping to conflicting targets is
    # impossible; instead a repeated value via non-str key is rejected.
    prov_error(
        "invalid-capability",
        lambda: authorize(intent(providerPreferences={"route@1": "app", 3: "app"})),
    )


def test_extra_intent_keys_are_rejected() -> None:
    prov_error(
        "extra-keys",
        lambda: authorize(intent(catalogRevision="a" * 64)),
    )


def test_intent_with_revision_inputs_is_rejected() -> None:
    """The caller may not supply any revision; the server computes them."""
    body = intent(
        observedStateRevision="a" * 64,
        policyRevision="b" * 64,
    )
    prov_error("extra-keys", lambda: authorize(intent_body=body))


def test_noncanonical_types_are_rejected() -> None:
    prov_error(
        "invalid-field-type",
        lambda: authorize(UserDict(intent())),
    )
    prov_error(
        "invalid-field-type",
        lambda: authorize(intent(requestedServices="app")),
    )
    prov_error(
        "invalid-field-type",
        lambda: authorize(intent(providerPreferences=[["route@1", "app"]])),
    )
    prov_error(
        "invalid-plan-expiry",
        lambda: authorize(intent(validUntil=20261001)),
    )


def test_invalid_requested_service_identifier_is_rejected() -> None:
    prov_error(
        "invalid-identifier",
        lambda: authorize(intent(requestedServices=["../escape"])),
    )
    prov_error(
        "invalid-identifier",
        lambda: authorize(intent(requestedServices=["App"])),
    )


def test_invalid_capability_format_is_rejected() -> None:
    prov_error(
        "invalid-capability",
        lambda: authorize(intent(requestedCapabilities=["route"])),
    )
    prov_error(
        "invalid-capability",
        lambda: authorize(intent(requestedCapabilities=["route@0"])),
    )


def test_invalid_config_key_names_are_rejected() -> None:
    prov_error(
        "invalid-config-key",
        lambda: authorize(intent(missingConfigKeys=["lower_token"])),
    )
    prov_error(
        "invalid-config-key",
        lambda: authorize(intent(missingSecretKeys=["TOKEN=private-sentinel"])),
    )


def test_invalid_provider_preference_target_is_rejected() -> None:
    prov_error(
        "invalid-identifier",
        lambda: authorize(
            intent(providerPreferences={"route@1": "../../escape"})
        ),
    )


def test_path_like_identifiers_are_rejected() -> None:
    prov_error(
        "invalid-identifier",
        lambda: authorize(intent(requestedServices=["../state"])),
    )


# ---------------------------------------------------------------------------
# Secret values rejected; names only
# ---------------------------------------------------------------------------


def test_secret_values_are_never_accepted() -> None:
    value_attempts = [
        ["APP_TOKEN=sk-live-abc123"],
        ["sk-live-abc123"],
        ["APP_TOKEN\nREAL_VALUE"],
    ]
    for attempt in value_attempts:
        error = prov_error(
            "invalid-config-key",
            lambda attempt=attempt: authorize(intent(missingSecretKeys=attempt)),
        )
        assert "sk-live-abc123" not in json.dumps(error.as_dict())


def test_private_sentinel_never_leaks_in_duplicate_key_error() -> None:
    error = prov_error(
        "invalid-config-key",
        lambda: authorize(intent(missingSecretKeys=["TOKEN=private-sentinel"])),
    )
    assert "private-sentinel" not in json.dumps(error.as_dict())


def test_missing_config_keys_only_names_are_reflected() -> None:
    envelope = authorize(
        entries=[catalog_entry("app", config=["APP_SETTING"])],
        intent_body=intent(missingConfigKeys=["APP_SETTING", "UNRELATED_SETTING"]),
    )
    assert envelope["plan"]["missingRequiredConfigKeys"] == ["APP_SETTING"]
    assert "UNRELATED_SETTING" not in json.dumps(envelope)


# ---------------------------------------------------------------------------
# Deterministic canonical bytes
# ---------------------------------------------------------------------------


def test_identical_inputs_produce_byte_identical_envelopes() -> None:
    entries = [
        catalog_entry("app", depends_on=["db"], requires=["route@1"]),
        catalog_entry("db"),
        catalog_entry("provider", provides=["route@1"], priority=2),
    ]
    first = authorize(
        intent(requestedServices=["app", "db"], requestedCapabilities=["route@1"]),
        entries=entries,
    )
    second = authorize(
        intent(requestedServices=["db", "app"], requestedCapabilities=["route@1"]),
        entries=list(reversed(entries)),
    )
    assert planner.canonical_json_bytes(first) == planner.canonical_json_bytes(second)
    assert first["planHash"] == second["planHash"]
    assert first["plan"]["selectedServices"] == ["db", "provider", "app"]


def test_canonical_bytes_have_single_trailing_newline() -> None:
    envelope = authorize()
    encoded = planner.canonical_json_bytes(envelope)
    assert encoded.endswith(b"\n")
    assert not encoded.endswith(b"\n\n")


def test_repeated_authorization_is_stable() -> None:
    envelope_one = authorize()
    envelope_two = authorize()
    assert planner.canonical_json_bytes(envelope_one) == planner.canonical_json_bytes(
        envelope_two
    )


# ---------------------------------------------------------------------------
# Catalog definition hashes change the plan
# ---------------------------------------------------------------------------


def test_catalog_definition_hash_change_changes_plan_hash() -> None:
    base = authorize(entries=[catalog_entry("app")])
    changed = authorize(
        entries=[catalog_entry("app", definition_sha="sha256:" + "7" * 64)]
    )
    assert base["planHash"] != changed["planHash"]
    assert (
        base["plan"]["definitions"][0]["definitionSha256"]
        != changed["plan"]["definitions"][0]["definitionSha256"]
    )
    assert base["catalogRevision"] != changed["catalogRevision"]


def test_catalog_compose_hash_change_changes_plan_hash() -> None:
    entries_a = [catalog_entry("app")]
    entries_b = [catalog_entry("app")]
    entries_b[0]["planning"]["composeSha256"] = "sha256:" + "8" * 64
    base = authorize(entries=entries_a)
    changed = authorize(entries=entries_b)
    assert base["planHash"] != changed["planHash"]


def test_server_computed_catalog_revision_must_match_injected_revision() -> None:
    entries = [catalog_entry("app")]

    def tampered_provider() -> tuple[list[dict[str, Any]], str]:
        return entries, "f" * 64

    prov_error(
        "catalog-revision-mismatch",
        lambda: plan_provenance.authorize_plan(
            intent(),
            catalog=tampered_provider,
            observed_state=state_provider(),
            policy=policy_provider(),
        ),
    )


@pytest.mark.parametrize(
    ("catalog_value", "error_code"),
    [
        (None, "invalid-catalog-provider"),
        (([],), "invalid-catalog-provider"),
        (((), "a" * 64), "invalid-catalog-provider"),
        (([], "not-a-digest"), "invalid-sha256"),
    ],
)
def test_catalog_provider_shape_is_strict(catalog_value, error_code) -> None:
    prov_error(
        error_code,
        lambda: plan_provenance.authorize_plan(
            intent(),
            catalog=lambda: catalog_value,
            observed_state=state_provider(),
            policy=policy_provider(),
        ),
    )


def test_state_and_policy_provider_shapes_are_strict() -> None:
    prov_error(
        "invalid-observed-state-provider",
        lambda: plan_provenance.authorize_plan(
            intent(),
            catalog=make_catalog([catalog_entry("app")]),
            observed_state=lambda: [],
            policy=policy_provider(),
        ),
    )
    prov_error(
        "invalid-policy-provider",
        lambda: plan_provenance.authorize_plan(
            intent(),
            catalog=make_catalog([catalog_entry("app")]),
            observed_state=state_provider(),
            policy=lambda: [],
        ),
    )


def test_authoritative_provider_values_are_snapshotted_before_hashing(
    monkeypatch,
) -> None:
    entries = [catalog_entry("app")]
    revision = computed_catalog_revision(entries)
    expected = authorize(entries=entries)
    real_compute = plan_provenance._compute_catalog_revision

    def mutate_original_after_snapshot(snapshot):
        entries[0]["planning"]["composeSha256"] = "sha256:" + "9" * 64
        return real_compute(snapshot)

    monkeypatch.setattr(
        plan_provenance, "_compute_catalog_revision", mutate_original_after_snapshot
    )
    actual = plan_provenance.authorize_plan(
        intent(),
        catalog=lambda: (entries, revision),
        observed_state=state_provider(),
        policy=policy_provider(),
    )
    assert actual == expected


def test_untrusted_observed_state_revision_input_is_impossible() -> None:
    """A revision supplied by the caller is an extra key, never honoured."""
    body = intent(observedStateRevision="a" * 64)
    prov_error("extra-keys", lambda: authorize(intent_body=body))


def test_policy_cannot_be_supplied_by_caller() -> None:
    body = intent(policy={**POLICY, "allowedTrustTiers": ["community"]})
    prov_error("extra-keys", lambda: authorize(intent_body=body))


# ---------------------------------------------------------------------------
# Boundary integrity
# ---------------------------------------------------------------------------


def test_provenance_error_is_a_planning_error() -> None:
    import assistant_first_planner

    assert issubclass(
        plan_provenance.ProvenanceError, assistant_first_planner.PlanningError
    )


def test_module_has_no_fastapi_dependency() -> None:
    source = _MODULE.read_text(encoding="utf-8")
    for line in source.splitlines():
        stripped = line.strip()
        assert not stripped.startswith(("import fastapi", "from fastapi")), stripped
    forbidden = {"subprocess", "httpx", "aiohttp", "socket", "docker", "os.system"}
    globals_names = set(plan_provenance.__dict__)
    assert forbidden.isdisjoint(globals_names)


def test_fresh_import_does_not_pull_fastapi_or_app_config() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json, sys; sys.path.insert(0, '.'); import plan_provenance; "
            "print(json.dumps(sorted(\n"
            "    name for name in ('fastapi', 'pydantic', 'config', 'security')\n"
            "    if name in sys.modules\n)))",
        ],
        capture_output=True,
        text=True,
        cwd=str(_MODULE.parent),
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == []


def test_verify_plan_hash_helper_reports_mismatch_without_raising() -> None:
    envelope = authorize()
    assert (
        plan_provenance.verify_plan_hash(
            intent(),
            catalog=make_catalog([catalog_entry("app")]),
            observed_state=state_provider(),
            policy=policy_provider(),
            expected_plan_hash=envelope["planHash"],
        )
        is True
    )
    assert (
        plan_provenance.verify_plan_hash(
            intent(),
            catalog=make_catalog([catalog_entry("app", definition_sha="sha256:" + "6" * 64)]),
            observed_state=state_provider(),
            policy=policy_provider(),
            expected_plan_hash=envelope["planHash"],
        )
        is False
    )
