"""Dependency-injected runtime for Assistant First extension transactions."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from assistant_first_planner import (
    PlanningError,
    adapt_manifest,
    canonical_json_bytes,
    normalize_host_state,
)
from extension_planning_contract import (
    computed_catalog_revision,
    computed_policy_revision,
    manifest_from_catalog_entry,
)


CatalogProvider = Callable[[], tuple[list[dict[str, Any]], str]]
ObservedStateProvider = Callable[[], dict[str, Any]]
PolicyProvider = Callable[[], dict[str, Any]]
Clock = Callable[[], str]


@dataclass(frozen=True)
class TransactionRuntime:
    """All authority used by a transaction request, supplied as one object."""

    store: Any
    catalog: CatalogProvider
    observed_state: ObservedStateProvider
    policy: PolicyProvider
    clock: Clock
    executor: Any | None = None


class CurrentInputsProvenanceVerifier:
    """Require the stored plan to match all current authoritative inputs."""

    def __init__(
        self,
        *,
        catalog: CatalogProvider,
        observed_state: ObservedStateProvider,
        policy: PolicyProvider,
    ) -> None:
        self._catalog = catalog
        self._observed_state = observed_state
        self._policy = policy

    def verify(self, plan_hash: str, envelope: dict[str, Any]) -> bool:
        try:
            if not isinstance(plan_hash, str) or len(plan_hash) != 64:
                return False
            if not isinstance(envelope, dict) or envelope.get("planHash") != plan_hash:
                return False
            plan = envelope.get("plan")
            if not isinstance(plan, dict):
                return False
            if hashlib.sha256(canonical_json_bytes(plan)).hexdigest() != plan_hash:
                return False

            catalog_result = self._catalog()
            if not isinstance(catalog_result, tuple) or len(catalog_result) != 2:
                return False
            catalog, supplied_catalog_revision = catalog_result
            if not isinstance(catalog, list) or any(
                not isinstance(entry, dict) for entry in catalog
            ):
                return False
            if computed_catalog_revision(catalog) != supplied_catalog_revision:
                return False
            if supplied_catalog_revision != envelope.get("catalogRevision"):
                return False

            policy = self._policy()
            if not isinstance(policy, dict):
                return False
            if computed_policy_revision(policy) != envelope.get("policyRevision"):
                return False

            observed_state = self._observed_state()
            if not isinstance(observed_state, dict):
                return False
            normalized_state = normalize_host_state(observed_state)
            observed_revision = hashlib.sha256(
                canonical_json_bytes(normalized_state)
            ).hexdigest()
            if observed_revision != envelope.get("observedStateRevision"):
                return False

            current_definitions = {}
            for entry in catalog:
                record = adapt_manifest(manifest_from_catalog_entry(entry))
                current_definitions[record["id"]] = record["definitionSha256"]
            stored_definitions = plan.get("definitions")
            selected_services = plan.get("selectedServices")
            if not isinstance(stored_definitions, list) or not isinstance(
                selected_services, list
            ):
                return False
            stored_digests = {
                item.get("id"): item.get("definitionSha256")
                for item in stored_definitions
                if isinstance(item, dict)
            }
            if set(stored_digests) != set(selected_services):
                return False
            if any(
                current_definitions.get(service_id)
                != stored_digests.get(service_id)
                for service_id in selected_services
            ):
                return False
        except (KeyError, TypeError, ValueError, PlanningError):
            return False
        return True
