"""Phase 3 authoritative plan provenance boundary.

The server-side component that rebuilds the exact plan envelope from trusted
inputs only.  An assistant or API-key caller supplies a minimal *request intent*
containing nothing more than the services, capabilities, provider preferences,
expiry, and missing configuration/secret key names they need.  The server owns
the current catalog, the policy, and a callable returning the live observed
host state.  From those three sources plus the intent, this module calls the
existing Phase 2 manifest adapter and deterministic planner, computes the
catalog, policy, and observed-state revisions itself, and returns the
canonical envelope.

The caller cannot:
  * Invent a self-hashed plan (envelopes, operations, definitions, effects,
    shell data, secret values, approvals, and actor fields are not accepted;
    the plan is always rebuilt server-side).
  * Hide resource, data, or permission effects (computed from the current
    catalog definitions).
  * Change catalog definitions (the catalog is a server-owned injection and
    its injected revision must match the recomputed one).
  * Execute against stale observed state (the revision is computed from the
    state the server observed, never read from the caller; an expected
    planHash must match the rebuilt envelope exactly or the call fails).
  * Inject secret values (only configuration/secret key names pass the
    strict key-name pattern).

The module deliberately has no FastAPI dependency: Phase 3 HTTP and
Unix-socket routes will wrap :func:`authorize_plan` later.  Catalog conversion
and revision material come from the same pure shared contract used by the
Phase 2 route, so the approval boundary cannot drift from the plan a user saw.
The solver itself remains in ``assistant_first_planner``.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
from collections.abc import Callable, Mapping
from typing import Any

from assistant_first_planner import (
    PlanningError,
    adapt_manifest,
    build_plan,
    canonical_json_bytes,
    normalize_host_state,
)
from extension_planning_contract import (
    computed_catalog_revision as _compute_catalog_revision,
    computed_policy_revision as _compute_policy_revision,
    manifest_from_catalog_entry as _manifest_from_catalog_entry,
)

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

RequestIntent = dict[str, Any]
"""Minimal intent the caller may supply.  Only the six allowed keys are read."""

CatalogProvider = Callable[[], tuple[list[dict[str, Any]], str]]
"""Returns (current catalog entries, catalog revision sha256 hex digest)."""

ObservedStateProvider = Callable[[], dict[str, Any]]
"""Returns the current full observed host-state object."""

PolicyProvider = Callable[[], dict[str, Any]]
"""Returns the current planning policy object."""

PlanEnvelope = dict[str, Any]
"""The canonical envelope returned by :func:`authorize_plan`."""


# ---------------------------------------------------------------------------
# Allowed request-intent keys and limits (mirroring the Phase 2 HTTP model)
# ---------------------------------------------------------------------------

_INTENT_KEYS = frozenset(
    {
        "requestedServices",
        "requestedCapabilities",
        "providerPreferences",
        "validUntil",
        "missingConfigKeys",
        "missingSecretKeys",
    }
)
_MAX_REQUESTED_SERVICES = 128
_MAX_REQUESTED_CAPABILITIES = 128
_MAX_PROVIDER_PREFERENCES = 128
_MAX_MISSING_KEYS = 256

_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_CAPABILITY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}@[1-9][0-9]{0,8}$")
_CONFIG_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_UTC_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ProvenanceError(PlanningError):
    """A plan-provenance violation at the Phase 3 boundary.

    Subclasses the Phase 2 :class:`PlanningError` so route wrappers can catch
    one exception type and reuse its stable ``code``/``details`` error shape.
    """


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _check_identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise ProvenanceError("invalid-identifier", field=field)
    return value


def _check_capability(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _CAPABILITY_RE.fullmatch(value):
        raise ProvenanceError("invalid-capability", field=field)
    return value


def _check_config_key(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _CONFIG_KEY_RE.fullmatch(value):
        raise ProvenanceError("invalid-config-key", field=field)
    return value


def _check_sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ProvenanceError("invalid-sha256", field=field)
    return value


def _check_unique_string_list(
    value: Any,
    field: str,
    validator: Callable[[Any, str], str],
    maximum: int,
) -> tuple[str, ...]:
    """Return a sorted deduplicated tuple, failing on duplicates or excess."""
    if not isinstance(value, list):
        raise ProvenanceError("invalid-field-type", field=field)
    if len(value) > maximum:
        raise ProvenanceError("list-too-long", field=field, maximum=maximum)
    result: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        normalized = validator(item, f"{field}[{index}]")
        if normalized in seen:
            raise ProvenanceError("duplicate-value", field=field, value=normalized)
        seen.add(normalized)
        result.append(normalized)
    return tuple(sorted(result))


def _check_provider_preferences(value: Any, field: str) -> dict[str, str]:
    """Validate the preference map: capability -> service id, no duplicates."""
    if not isinstance(value, dict):
        raise ProvenanceError("invalid-field-type", field=field)
    if len(value) > _MAX_PROVIDER_PREFERENCES:
        raise ProvenanceError(
            "list-too-long", field=field, maximum=_MAX_PROVIDER_PREFERENCES
        )
    result: dict[str, str] = {}
    for key, target in value.items():
        capability = _check_capability(key, f"{field}.key")
        if capability in result:
            raise ProvenanceError("duplicate-value", field=field, value=capability)
        result[capability] = _check_identifier(target, f"{field}.{key}")
    return result


def _check_valid_until(value: Any) -> str:
    """Validate the ISO-8601 UTC expiry string (format only, not freshness)."""
    if not isinstance(value, str) or _UTC_RE.fullmatch(value) is None:
        raise ProvenanceError("invalid-plan-expiry", field="validUntil")
    try:
        datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ProvenanceError("invalid-plan-expiry", field="validUntil") from exc
    return value


# ---------------------------------------------------------------------------
# Request-intent validation
# ---------------------------------------------------------------------------


def validate_request_intent(intent: RequestIntent) -> dict[str, Any]:
    """Validate and normalize the caller's request intent.

    The intent is the caller's *only* input channel.  It may contain exactly
    the six allowed keys; everything else -- plan envelopes, operations,
    definitions, effects, shell data, secret values, approvals, actors, and
    any revision input -- is rejected as an extra key.  Configuration and
    secret entries are names only: the strict ``[A-Z][A-Z0-9_]`` key pattern
    rejects anything value-shaped.  Duplicate IDs, capabilities, and
    preference keys are rejected.
    """
    if not isinstance(intent, dict):
        raise ProvenanceError("invalid-field-type", field="intent")
    if any(not isinstance(key, str) for key in intent):
        raise ProvenanceError("invalid-object-key", field="intent")
    extra = set(intent) - _INTENT_KEYS
    if extra:
        raise ProvenanceError("extra-keys", keys=sorted(extra))

    services = _check_unique_string_list(
        intent.get("requestedServices", []),
        "requestedServices",
        _check_identifier,
        _MAX_REQUESTED_SERVICES,
    )
    capabilities = _check_unique_string_list(
        intent.get("requestedCapabilities", []),
        "requestedCapabilities",
        _check_capability,
        _MAX_REQUESTED_CAPABILITIES,
    )
    preferences = _check_provider_preferences(
        intent.get("providerPreferences", {}), "providerPreferences"
    )
    valid_until = _check_valid_until(intent.get("validUntil", ""))
    config_keys = _check_unique_string_list(
        intent.get("missingConfigKeys", []),
        "missingConfigKeys",
        _check_config_key,
        _MAX_MISSING_KEYS,
    )
    secret_keys = _check_unique_string_list(
        intent.get("missingSecretKeys", []),
        "missingSecretKeys",
        _check_config_key,
        _MAX_MISSING_KEYS,
    )

    return {
        "requested_services": services,
        "requested_capabilities": capabilities,
        "provider_preferences": preferences,
        "valid_until": valid_until,
        "missing_config_keys": config_keys,
        "missing_secret_keys": secret_keys,
    }


# ---------------------------------------------------------------------------
# Server-side observed-state revision
# ---------------------------------------------------------------------------


def _compute_observed_state_revision(state: Mapping[str, Any]) -> str:
    """Compute the observed-state revision of the current state object."""
    normalized = normalize_host_state(state)
    return hashlib.sha256(canonical_json_bytes(normalized)).hexdigest()


# ---------------------------------------------------------------------------
# Authoritative reconstruction
# ---------------------------------------------------------------------------


def authorize_plan(
    intent: RequestIntent,
    *,
    catalog: CatalogProvider,
    observed_state: ObservedStateProvider,
    policy: PolicyProvider,
    expected_plan_hash: str | None = None,
) -> PlanEnvelope:
    """Rebuild and authorize a plan envelope from server-side truth only.

    Parameters
    ----------
    intent:
        The caller's minimal request intent (see :func:`validate_request_intent`).
    catalog:
        Server-side callable returning the current catalog entries and their
        revision hash.  The hash is verified against a recomputation, so a
        stale or tampered injection fails closed.
    observed_state:
        Server-side callable returning the current full observed host state.
    policy:
        Server-side callable returning the current planning policy.
    expected_plan_hash:
        When supplied, the rebuilt envelope's ``planHash`` must match exactly;
        any drift in state, catalog, policy, or intent since the hash was
        issued fails with ``plan-hash-mismatch``.

    Returns
    -------
    PlanEnvelope
        The canonical envelope (schema ``ods.assistant-first.plan-envelope.v1``)
        with ``planHash``, ``planId``, and the full rebuilt plan body.
    """
    if expected_plan_hash is not None:
        _check_sha256(expected_plan_hash, "expected_plan_hash")

    normalized = validate_request_intent(intent)

    catalog_result = catalog()
    if not isinstance(catalog_result, tuple) or len(catalog_result) != 2:
        raise ProvenanceError("invalid-catalog-provider")
    catalog_entries, catalog_revision = catalog_result
    current_state = observed_state()
    current_policy = policy()
    if not isinstance(catalog_entries, list) or any(
        not isinstance(entry, dict) for entry in catalog_entries
    ):
        raise ProvenanceError("invalid-catalog-provider")
    _check_sha256(catalog_revision, "catalog_revision")
    if not isinstance(current_state, dict):
        raise ProvenanceError("invalid-observed-state-provider")
    if not isinstance(current_policy, dict):
        raise ProvenanceError("invalid-policy-provider")

    # Freeze all three authoritative inputs before hashing or planning. This
    # prevents a mutable provider object from changing between revision
    # computation and plan construction.
    catalog_entries = json.loads(canonical_json_bytes(catalog_entries))
    current_state = json.loads(canonical_json_bytes(current_state))
    current_policy = json.loads(canonical_json_bytes(current_policy))

    computed_catalog_revision = _compute_catalog_revision(catalog_entries)
    if computed_catalog_revision != catalog_revision:
        raise ProvenanceError(
            "catalog-revision-mismatch",
            currentRevision=computed_catalog_revision,
            suppliedRevision=catalog_revision,
        )

    state_revision = _compute_observed_state_revision(current_state)
    policy_revision = _compute_policy_revision(current_policy)

    manifests: list[dict[str, Any]] = []
    for entry in catalog_entries:
        manifest = _manifest_from_catalog_entry(entry)
        adapt_manifest(manifest)
        manifests.append(manifest)

    envelope = build_plan(
        manifests,
        requested_services=normalized["requested_services"],
        requested_capabilities=normalized["requested_capabilities"],
        provider_preferences=normalized["provider_preferences"],
        missing_config_keys=normalized["missing_config_keys"],
        missing_secret_keys=normalized["missing_secret_keys"],
        catalog_revision=catalog_revision,
        observed_state_revision=state_revision,
        observed_state=current_state,
        policy_revision=policy_revision,
        policy=current_policy,
        valid_until=normalized["valid_until"],
    )

    if expected_plan_hash is not None and envelope["planHash"] != expected_plan_hash:
        raise ProvenanceError(
            "plan-hash-mismatch",
            currentPlanHash=envelope["planHash"],
            expectedPlanHash=expected_plan_hash,
        )
    return envelope


def verify_plan_hash(
    intent: RequestIntent,
    *,
    catalog: CatalogProvider,
    observed_state: ObservedStateProvider,
    policy: PolicyProvider,
    expected_plan_hash: str,
) -> bool:
    """Return whether the current inputs still produce *expected_plan_hash*.

    Approval-gate helper: ``False`` means the world drifted since the hash was
    issued and the approval must not be honored.  Domain errors other than the
    hash mismatch still raise.
    """
    try:
        authorize_plan(
            intent,
            catalog=catalog,
            observed_state=observed_state,
            policy=policy,
            expected_plan_hash=expected_plan_hash,
        )
    except ProvenanceError as exc:
        if exc.code == "plan-hash-mismatch":
            return False
        raise
    return True
