#!/usr/bin/env python3
"""Fail-closed routing contract for full Pixel product-path outcome runs.

Portal outcome profiles are product roles, not interchangeable benchmark labels.
This module binds each role to the real Pixel execution surface that must be used
and to the evidence that surface is allowed to claim.  It grants no execution;
orchestrators must still supply an implemented adapter and exact evidence.
"""

from __future__ import annotations

from typing import Any, Iterable

import portal_outcome_evaluation as evaluation


PRODUCT_PATH_BOUNDARY = (
    "Content-free exact product-path routing only. A route binds one admitted journey to its real Pixel surface; "
    "it grants no execution, surrogate substitution, model start, tool use, provider call, credential, external "
    "effect, completion, acceptance, publication, deployment, or promotion authority."
)

KNOWN_EVIDENCE_TYPES = frozenset({
    "exact-source", "runtime-environment", "command-exit", "artifact-digest", "independent-verifier",
    "source-provenance", "observation-time", "citation-coverage", "typed-call", "provider-identifier",
    "action-journal", "checkpoint-lineage", "cleanup-proof", "privacy-route",
})

_ROUTES = {
    "assistant": {
        "engine": "portal-assistant",
        "components": ["portal-chat", "policy", "dlp", "inline-approvals", "typed-brokers"],
        "evidence": {
            "exact-source", "runtime-environment", "command-exit", "source-provenance", "observation-time", "privacy-route", "typed-call",
            "provider-identifier", "action-journal", "checkpoint-lineage", "independent-verifier",
        },
    },
    "builder": {
        "engine": "work-builder",
        "components": ["work-broker", "builder-runner", "model-proxy", "independent-verifier"],
        "evidence": {
            "exact-source", "runtime-environment", "command-exit", "artifact-digest", "independent-verifier",
        },
    },
    "researcher": {
        "engine": "work-researcher",
        "components": ["work-broker", "researcher-runner", "research-broker", "citation-verifier"],
        "evidence": {"source-provenance", "observation-time", "citation-coverage", "independent-verifier"},
    },
    "controller": {
        "engine": "deep-work-controller",
        "components": ["portal-chat", "goal-controller", "work-broker", "checkpoint-ledger", "cleanup-verifier"],
        "evidence": {
            "exact-source", "runtime-environment", "artifact-digest", "independent-verifier", "typed-call",
            "checkpoint-lineage", "cleanup-proof", "privacy-route",
        },
    },
}


def _exact_string_set(value: Any, label: str) -> frozenset[str]:
    if not isinstance(value, list) or len(value) != len(set(value)) or any(
        not isinstance(item, str) or item not in KNOWN_EVIDENCE_TYPES for item in value
    ):
        raise evaluation.OutcomeError(f"{label} is not a unique known evidence set")
    return frozenset(value)


def resolve_product_path(admission: dict[str, Any], journey: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(admission, dict) or not isinstance(journey, dict):
        raise evaluation.OutcomeError("product-path admission or journey is invalid")
    profile = admission.get("profile")
    route = _ROUTES.get(profile)
    if route is None or journey.get("profile") != profile or admission.get("journeyId") != journey.get("id"):
        raise evaluation.OutcomeError("product-path route differs from the admitted journey profile")
    required = _exact_string_set(journey.get("requiredEvidence"), "product-path required evidence")
    supported = frozenset(route["evidence"])
    missing = required - supported
    if missing:
        raise evaluation.OutcomeError(
            f"{profile} product path cannot produce required evidence: {','.join(sorted(missing))}"
        )
    return {
        "schemaVersion": 1,
        "operation": "pixel-portal-outcome-product-path",
        "journeyId": journey["id"],
        "profile": profile,
        "engine": route["engine"],
        "components": list(route["components"]),
        "requiredEvidence": sorted(required),
        "surrogateProfileAllowed": False,
        "boundary": PRODUCT_PATH_BOUNDARY,
    }


def require_engine(route: dict[str, Any], implemented_engines: Iterable[str]) -> None:
    engines = list(implemented_engines)
    if len(engines) != len(set(engines)) or any(
        not isinstance(item, str) or item not in {value["engine"] for value in _ROUTES.values()} for item in engines
    ):
        raise evaluation.OutcomeError("product-path engine registry is invalid")
    if route.get("engine") not in engines:
        raise evaluation.OutcomeError(
            f"Pixel product-path adapter is not implemented for {route.get('profile')} ({route.get('engine')})"
        )


def require_evidence_emitter(route: dict[str, Any], emitted_types: Iterable[str]) -> None:
    emitted = list(emitted_types)
    if len(emitted) != len(set(emitted)) or any(
        not isinstance(item, str) or item not in KNOWN_EVIDENCE_TYPES for item in emitted
    ):
        raise evaluation.OutcomeError("product-path evidence emitter registry is invalid")
    missing = set(route.get("requiredEvidence", [])) - set(emitted)
    if missing:
        raise evaluation.OutcomeError(
            f"Pixel product-path evidence emitter is incomplete for {route.get('profile')}: {','.join(sorted(missing))}"
        )


def require_exact_evidence(route: dict[str, Any], evidence: list[dict[str, Any]]) -> None:
    if not isinstance(evidence, list) or any(not isinstance(item, dict) for item in evidence):
        raise evaluation.OutcomeError("product-path evidence inventory is invalid")
    observed = [item.get("type") for item in evidence]
    if len(observed) != len(set(observed)) or set(observed) != set(route.get("requiredEvidence", [])):
        raise evaluation.OutcomeError("product-path evidence differs from the exact journey requirement")


PRODUCT_PATH_ENGINES = frozenset(value["engine"] for value in _ROUTES.values())


def product_engine(profile: str) -> str:
    route = _ROUTES.get(profile)
    if route is None:
        raise evaluation.OutcomeError("product-path profile is unknown")
    return route["engine"]


def profiles_for_engines(engines: Iterable[str]) -> frozenset[str]:
    observed = list(engines)
    if len(observed) != len(set(observed)) or any(engine not in PRODUCT_PATH_ENGINES for engine in observed):
        raise evaluation.OutcomeError("product-path engine registry is invalid")
    return frozenset(profile for profile, route in _ROUTES.items() if route["engine"] in observed)
