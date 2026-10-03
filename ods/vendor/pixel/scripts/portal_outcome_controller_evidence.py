#!/usr/bin/env python3
"""Validate durable Pixel Controller custody and emit content-free evidence."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Iterable

import portal_outcome_evaluation as evaluation
import portal_outcome_orchestrate as common


CONTROLLER_EVIDENCE_BOUNDARY = (
    "Content-free exact Pixel Controller evidence for one admitted long-horizon comparison objective. It binds the "
    "immutable goal, DSV4-backed Builder child, durable run custody, parent and child checkpoint lineage, independent "
    "verification, usage, and cleanup disposition; it grants no execution, replay, lease, scope expansion, external "
    "effect, completion, publication, deployment, acceptance, or promotion authority."
)
SUPPORTED_CONTROLLER_EVIDENCE_TYPES = frozenset({
    "exact-source", "runtime-environment", "artifact-digest", "independent-verifier", "typed-call",
    "checkpoint-lineage", "cleanup-proof", "privacy-route",
})
GOAL_RE = re.compile(r"^workgoal-[0-9]{13}-[a-f0-9]{12}$")
JOB_RE = re.compile(r"^work-[0-9]{13}-[a-f0-9]{12}$")


def _requested(values: Iterable[str]) -> frozenset[str]:
    observed = list(values)
    if (
        len(observed) != len(set(observed))
        or any(not isinstance(item, str) or item not in SUPPORTED_CONTROLLER_EVIDENCE_TYPES for item in observed)
    ):
        raise evaluation.OutcomeError("Controller evidence request contains an unsupported or duplicate type")
    return frozenset(observed)


def _hashes(value: dict[str, Any], fields: Iterable[str], label: str) -> None:
    for field in fields:
        evaluation.valid_hash(value[field], f"{label} {field}")


def _lineage(value: Any, *, label: str, digest_field: str, previous_field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not 1 <= len(value) <= 100_000:
        raise evaluation.OutcomeError(f"{label} is empty or unbounded")
    previous = None
    for sequence, item in enumerate(value):
        if not isinstance(item, dict) or item.get("sequence") != sequence:
            raise evaluation.OutcomeError(f"{label} sequence is invalid")
        evaluation.valid_hash(item.get(digest_field), f"{label} digest")
        prior = item.get(previous_field)
        if sequence == 0:
            if prior is not None:
                raise evaluation.OutcomeError(f"{label} genesis points to a predecessor")
        elif prior != previous:
            raise evaluation.OutcomeError(f"{label} chain is broken")
        previous = item[digest_field]
    return value


def _artifact_payload(run_dir: Path, artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    selected = [item for item in artifacts if item.get("relativePath") == "pixel-controller-evidence.json"]
    if len(selected) != 1 or selected[0].get("kind") != "test-evidence":
        raise evaluation.OutcomeError("Pixel Controller retained custody artifact is missing or substituted")
    item = selected[0]
    payload = evaluation.evidence_bytes(
        run_dir / "run.json", item["relativePath"], item["sha256"], item["bytes"],
        "Pixel Controller retained custody artifact",
    )
    return evaluation.parse_json(payload, "Pixel Controller retained custody artifact")


def _validate(
    value: Any, *, source_sha256: str, model_sha256: str, inference_sha256: str,
) -> dict[str, Any]:
    value = evaluation.exact_fields(value, {
        "schemaVersion", "format", "status", "goalId", "goalSha256", "objectiveSha256",
        "sourceSnapshotSha256", "modelContractSha256", "inferenceContractSha256", "parent", "child",
        "custody", "usage", "independentVerification", "cleanup", "privacy", "authority", "boundary",
    }, "Pixel Controller evidence")
    if (
        value["schemaVersion"] != 1 or value["format"] != "pixel-controller-evidence-v1"
        or value["status"] != "pass" or GOAL_RE.fullmatch(value["goalId"] or "") is None
        or value["sourceSnapshotSha256"] != source_sha256
        or value["modelContractSha256"] != model_sha256
        or value["inferenceContractSha256"] != inference_sha256
        or value["boundary"] != CONTROLLER_EVIDENCE_BOUNDARY
    ):
        raise evaluation.OutcomeError("Pixel Controller identity, result, or immutable binding is invalid")
    _hashes(value, ("goalSha256", "objectiveSha256", "sourceSnapshotSha256", "modelContractSha256", "inferenceContractSha256"), "Pixel Controller")

    parent = evaluation.exact_fields(value["parent"], {
        "state", "checkpointCount", "headCheckpointSha256", "milestonesTotal", "milestonesCompleted",
        "jobsStarted", "lineage",
    }, "Pixel Controller parent")
    parent_lineage = _lineage(
        parent["lineage"], label="Pixel Controller parent lineage",
        digest_field="checkpointSha256", previous_field="previousCheckpointSha256",
    )
    for item in parent_lineage:
        evaluation.exact_fields(item, {"sequence", "state", "checkpointSha256", "previousCheckpointSha256"}, "Pixel Controller parent checkpoint")
    if (
        parent["state"] != "completed" or parent["checkpointCount"] != len(parent_lineage)
        or parent["headCheckpointSha256"] != parent_lineage[-1]["checkpointSha256"]
        or parent["milestonesTotal"] != 1 or parent["milestonesCompleted"] != 1 or parent["jobsStarted"] != 1
    ):
        raise evaluation.OutcomeError("Pixel Controller parent did not durably complete its one admitted milestone")

    child = evaluation.exact_fields(value["child"], {
        "profile", "jobId", "jobSha256", "planSha256", "state", "checkpointCount",
        "headCheckpointSha256", "iterations", "verificationEvidenceSha256",
    }, "Pixel Controller child")
    _hashes(child, ("jobSha256", "planSha256", "headCheckpointSha256", "verificationEvidenceSha256"), "Pixel Controller child")
    if (
        child["profile"] != "builder" or JOB_RE.fullmatch(child["jobId"] or "") is None
        or child["state"] != "completed" or type(child["checkpointCount"]) is not int
        or type(child["iterations"]) is not int or child["checkpointCount"] < 2 or child["iterations"] < 1
    ):
        raise evaluation.OutcomeError("Pixel Controller child is not a completed real Builder run")

    custody = evaluation.exact_fields(value["custody"], {
        "recordCount", "headBundleSha256", "exactLeaseOnly", "lineage",
    }, "Pixel Controller custody")
    custody_lineage = _lineage(
        custody["lineage"], label="Pixel Controller custody lineage",
        digest_field="bundleSha256", previous_field="previousBundleSha256",
    )
    for item in custody_lineage:
        evaluation.exact_fields(item, {"sequence", "purpose", "bundleSha256", "previousBundleSha256", "leaseIteration"}, "Pixel Controller custody record")
        expected_purpose = "initial" if item["sequence"] == 0 else "admission" if item["sequence"] == 1 else "continuation"
        expected_iteration = 1 if item["sequence"] <= 1 else item["sequence"]
        if item["purpose"] != expected_purpose or item["leaseIteration"] != expected_iteration:
            raise evaluation.OutcomeError("Pixel Controller custody purpose or lease sequence is invalid")
    if (
        custody["recordCount"] != len(custody_lineage) or custody["headBundleSha256"] != custody_lineage[-1]["bundleSha256"]
        or custody["exactLeaseOnly"] is not True or custody["recordCount"] != child["iterations"] + 1
    ):
        raise evaluation.OutcomeError("Pixel Controller custody does not exactly bind every Builder iteration")

    usage = evaluation.exact_fields(value["usage"], {
        "runtimeSeconds", "modelRequests", "inputTokens", "outputTokens", "networkBytes", "artifactBytes",
        "failures", "toolCalls", "latencyMs",
    }, "Pixel Controller usage")
    for field in usage:
        evaluation.integer(usage[field], 0, 2_000_000_000, f"Pixel Controller usage {field}")
    independent = evaluation.exact_fields(value["independentVerification"], {
        "status", "evidenceSha256", "workerSelectedChecks", "externalEffects",
    }, "Pixel Controller independent verification")
    evaluation.valid_hash(independent["evidenceSha256"], "Pixel Controller independent verification")
    if independent["status"] != "pass" or independent["workerSelectedChecks"] is not False or independent["externalEffects"] is not False:
        raise evaluation.OutcomeError("Pixel Controller completion was not independently and safely verified")

    cleanup = evaluation.exact_fields(value["cleanup"], {
        "preparedRunsDiscarded", "expectedPreparedRuns", "activeChild", "ephemeralPreparationComplete",
    }, "Pixel Controller cleanup")
    if (
        cleanup["preparedRunsDiscarded"] != child["iterations"]
        or cleanup["expectedPreparedRuns"] != child["iterations"] or cleanup["activeChild"] is not False
        or cleanup["ephemeralPreparationComplete"] is not True
    ):
        raise evaluation.OutcomeError("Pixel Controller left an active or undiscarded child preparation")
    privacy = evaluation.exact_fields(value["privacy"], {
        "directNetwork", "credentials", "privateDataSentRemote", "externalEffects",
    }, "Pixel Controller privacy")
    authority = evaluation.exact_fields(value["authority"], {
        "grantsExecution", "grantsReplay", "grantsLease", "grantsScopeExpansion", "grantsExternalEffects",
        "grantsCompletion", "grantsPublication", "grantsDeployment", "grantsAcceptance", "grantsPromotion",
    }, "Pixel Controller authority")
    if any(type(item) is not bool or item for item in privacy.values()) or any(type(item) is not bool or item for item in authority.values()):
        raise evaluation.OutcomeError("Pixel Controller evidence overclaims privacy or authority")
    return value


def validate_and_emit(
    *, run_dir: Path, outcome: dict[str, Any], artifacts: list[dict[str, Any]], required_evidence: Iterable[str],
    runtime_evidence: dict[str, Any], independent_verification: dict[str, Any], source_sha256: str,
    model_sha256: str, inference_sha256: str, admission: dict[str, Any], environment: dict[str, Any],
    tool_policy: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    required = _requested(required_evidence)
    retained = _artifact_payload(run_dir, artifacts)
    controller = _validate(
        outcome.get("controllerEvidence"), source_sha256=source_sha256,
        model_sha256=model_sha256, inference_sha256=inference_sha256,
    )
    if retained != controller:
        raise evaluation.OutcomeError("Pixel Controller retained custody differs from the system result")
    if (
        controller["independentVerification"]["evidenceSha256"] != evaluation.sha256(evaluation.canonical(independent_verification))
        or admission.get("profile") != "controller" or admission.get("dataRoute") != "local-only"
        or environment.get("isolation", {}).get("directNetwork") is not False
        or tool_policy.get("brokeredServices") != ["local-model"]
        or any(tool_policy.get(field) is not False for field in (
            "hostAccess", "ambientCredentials", "externalEffects", "mergeAuthority", "deployAuthority", "policyMutation",
        ))
    ):
        raise evaluation.OutcomeError("Pixel Controller evidence differs from its independent result or local-only authority")

    descriptors: dict[str, dict[str, Any]] = {"runtime-environment": runtime_evidence}
    payloads = {
        "exact-source": ("controller-source.json", {"sourceSnapshotSha256": source_sha256}),
        "artifact-digest": ("controller-artifacts.json", {"artifacts": len(artifacts)}),
        "independent-verifier": ("controller-independent-verification.json", independent_verification),
        "typed-call": ("controller-dispatch.json", {
            "goalId": controller["goalId"], "goalSha256": controller["goalSha256"],
            "childJobId": controller["child"]["jobId"], "childJobSha256": controller["child"]["jobSha256"],
            "childPlanSha256": controller["child"]["planSha256"], "exactLeaseOnly": True,
        }),
        "checkpoint-lineage": ("controller-checkpoint-lineage.json", {
            "goalId": controller["goalId"], "parent": controller["parent"], "child": controller["child"],
            "custody": controller["custody"],
        }),
        "cleanup-proof": ("controller-cleanup.json", controller["cleanup"]),
        "privacy-route": ("controller-privacy-route.json", {
            "sourceSnapshotSha256": source_sha256, "modelContractSha256": model_sha256,
            "inferenceContractSha256": inference_sha256, **controller["privacy"],
        }),
    }
    for kind, (relative, payload) in payloads.items():
        if kind in required:
            descriptors[kind] = common.write_evidence(
                run_dir, relative, json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"), kind,
            )
    if "runtime-environment" not in required:
        descriptors.pop("runtime-environment", None)
    return [descriptors[kind] for kind in sorted(required)], descriptors.get("independent-verifier", next(iter(descriptors.values())))
