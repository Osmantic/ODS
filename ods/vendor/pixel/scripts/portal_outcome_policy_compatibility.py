#!/usr/bin/env python3
"""Audit every materialized task against Pixel's exact non-authorizing policy envelope."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Callable

import portal_outcome_battery_campaign as battery_campaign
import portal_outcome_evaluation as evaluation
import portal_outcome_pair as pair_runner
import portal_outcome_pair_preflight as pair_preflight


COMPATIBILITY_BOUNDARY = (
    "Content-free read-only structural-compatibility evidence for every task in one exact private DSV4 battery "
    "materialization. It binds immutable task and configuration hashes and proves only model identity, inference, "
    "profile, runner, tools, services, budgets, verifier, input, and output-envelope fit. It does not assert model "
    "qualification, launch readiness, execution quality, completion, or release; creates no plan or lease; starts "
    "no model, container, network, task, or tool; includes no private task content or path; and grants no execution, "
    "credential, provider, external-effect, publication, deployment, acceptance, or promotion authority."
)
REPORT_AUTHORITY = dict(pair_preflight.PLANNED_REVIEW_AUTHORITY)
REVIEW_FIELDS = {
    "schemaVersion", "operation", "runId", "profile", "status", "readiness", "modelContractSha256",
    "inferenceContractSha256", "taskCompatibilitySha256", "harnessContractSha256", "workPolicySha256", "environmentSha256",
    "backendConfigSha256", "runnerImageDigest", "backendImageDigest", "changes", "authority", "boundary",
}


def _canonical_payload(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    ).encode("utf-8") + b"\n"


def audit_planned_compatibility(
    *, root: Path, materialization_root: Path, pair_configuration_path: Path,
    temporary_parent: Path, output_path: Path,
    materialization_loader: Callable[[Path, Path], tuple[dict[str, Any], str]] = battery_campaign.load_materialization,
    configuration_loader: Callable[[Path], tuple[dict[str, Any], bytes]] = pair_runner.load_configuration_record,
    reviewer: Callable[..., dict[str, Any]] = pair_preflight.review_materialized_pixel_task_planned_compatibility,
) -> dict[str, Any]:
    root = Path(root).resolve(strict=True)
    materialization_root = pair_runner._private_existing(
        pair_runner._absolute(str(materialization_root), "private policy-compatibility materialization path"),
        "private policy-compatibility materialization", directory=True,
    )
    pair_configuration_path = pair_runner._private_existing(
        pair_runner._absolute(str(pair_configuration_path), "private policy-compatibility configuration path"),
        "private policy-compatibility configuration",
    )
    temporary_parent = pair_runner._private_existing(
        pair_runner._absolute(str(temporary_parent), "private policy-compatibility temporary parent"),
        "private policy-compatibility temporary parent", directory=True,
    )
    output_path = pair_runner._absolute(str(output_path), "private policy-compatibility output path")
    evaluation.private_parent(output_path)
    if output_path.exists():
        raise evaluation.OutcomeError("policy-compatibility output already exists")
    if any(path == root or root in path.parents for path in (temporary_parent, output_path)):
        raise evaluation.OutcomeError("policy-compatibility state and evidence must remain outside the source repository")

    materialization, materialization_sha256 = materialization_loader(materialization_root, root)
    _configuration, configuration_raw = configuration_loader(pair_configuration_path)
    pair_config_sha256 = evaluation.sha256(configuration_raw)
    materialization_path = materialization_root / "materialization.json"
    materialization_raw = materialization_path.read_bytes()
    if evaluation.sha256(materialization_raw) != materialization_sha256:
        raise evaluation.OutcomeError("policy-compatibility materialization identity differs from its loader")

    common: dict[str, Any] | None = None
    reviews: list[dict[str, Any]] = []
    counts = {
        "tasks": 0, "tuning": 0, "heldOut": 0, "exactProfile": 0, "surrogateRehearsal": 0,
        "qualificationRequired": 0, "policyReady": 0,
    }
    for item in materialization["tasks"]:
        relative = evaluation.relative_path(item["taskRelativePath"], "policy-compatibility task path")
        task_path = materialization_root.joinpath(*relative.parts)
        task_raw = task_path.read_bytes()
        if evaluation.sha256(task_raw) != item["taskSha256"]:
            raise evaluation.OutcomeError("policy-compatibility task differs from its immutable inventory")
        result = reviewer(
            root=root, task_path=task_path, configuration_path=pair_configuration_path,
            temporary_parent=temporary_parent,
        )
        result = evaluation.exact_fields(result, REVIEW_FIELDS, "planned policy-compatibility task review")
        for field in (
            "modelContractSha256", "inferenceContractSha256", "taskCompatibilitySha256", "workPolicySha256",
            "harnessContractSha256", "environmentSha256", "backendConfigSha256",
        ):
            evaluation.valid_hash(result[field], f"planned policy-compatibility {field}")
        observed_common = {
            "profile": result["profile"], "modelContractSha256": result["modelContractSha256"],
            "inferenceContractSha256": result["inferenceContractSha256"],
            "harnessContractSha256": result["harnessContractSha256"],
            "workPolicySha256": result["workPolicySha256"], "environmentSha256": result["environmentSha256"],
            "backendConfigSha256": result["backendConfigSha256"], "runnerImageDigest": result["runnerImageDigest"],
            "backendImageDigest": result["backendImageDigest"],
        }
        if common is None:
            common = observed_common
        elif observed_common != common:
            raise evaluation.OutcomeError("policy-compatibility tasks do not share one exact Pixel envelope")
        if (
            result["schemaVersion"] != 1
            or result["operation"] != "pixel-portal-outcome-planned-system-review"
            or result["status"] != "structurally-compatible"
            or result["readiness"] not in {"qualification-required", "policy-ready"}
            or result["profile"] != materialization["profile"]
            or result["modelContractSha256"] != materialization["modelContractSha256"]
            or result["inferenceContractSha256"] != materialization["inferenceContractSha256"]
            or result["changes"] != {
                "policyMutated": False, "qualificationFabricated": False, "modelStarted": False,
                "containerCreated": False, "networkCreated": False, "taskExecuted": False,
                "externalEffects": False,
            }
            or result["authority"] != REPORT_AUTHORITY
            or result["boundary"] != pair_preflight.PIXEL_PLANNED_REVIEW_BOUNDARY
            or pair_runner.DIGEST_IMAGE_RE.fullmatch(result["runnerImageDigest"] or "") is None
            or pair_runner.DIGEST_IMAGE_RE.fullmatch(result["backendImageDigest"] or "") is None
        ):
            raise evaluation.OutcomeError("planned policy-compatibility task review is unsafe or mismatched")
        if task_path.read_bytes() != task_raw:
            raise evaluation.OutcomeError("policy-compatibility task changed during read-only review")
        review_sha256 = evaluation.sha256(evaluation.canonical(result))
        reviews.append({
            "taskSha256": item["taskSha256"], "reviewSha256": review_sha256,
            "partition": item["partition"], "profileFidelity": item["profileFidelity"],
            "proofClass": item["proofClass"], "readiness": result["readiness"],
        })
        counts["tasks"] += 1
        counts["tuning" if item["partition"] == "tuning" else "heldOut"] += 1
        counts["exactProfile" if item["profileFidelity"] == "exact" else "surrogateRehearsal"] += 1
        counts["qualificationRequired" if result["readiness"] == "qualification-required" else "policyReady"] += 1

    if common is None or counts["tasks"] != len(materialization["tasks"]):
        raise evaluation.OutcomeError("policy-compatibility review is incomplete")
    current_materialization = materialization_path.read_bytes()
    _current_configuration, current_configuration_raw = configuration_loader(pair_configuration_path)
    if current_materialization != materialization_raw or current_configuration_raw != configuration_raw:
        raise evaluation.OutcomeError("policy-compatibility materialization or configuration changed during review")

    report = {
        "schemaVersion": 1, "operation": "pixel-portal-outcome-policy-compatibility",
        "status": "structurally-compatible", "profile": materialization["profile"],
        "evaluationRegime": materialization["evaluationRegime"],
        "materializationSha256": materialization_sha256, "pairConfigSha256": pair_config_sha256,
        "batterySha256": materialization["batterySha256"],
        "modelContractSha256": common["modelContractSha256"],
        "inferenceContractSha256": common["inferenceContractSha256"],
        "harnessContractSha256": common["harnessContractSha256"],
        "auditorSha256": evaluation.sha256(Path(__file__).resolve(strict=True).read_bytes()),
        "workPolicySha256": common["workPolicySha256"], "environmentSha256": common["environmentSha256"],
        "backendConfigSha256": common["backendConfigSha256"],
        "images": {"runner": common["runnerImageDigest"], "modelBackend": common["backendImageDigest"]},
        "counts": counts,
        "checks": {
            "allTasksReviewed": True, "oneExactEnvelope": True, "immutableInputs": True,
            "modelAndInferenceExact": True, "harnessAndAuditorExact": True,
            "noPlanOrLease": True, "noQualificationClaim": True,
            "noModelOrTaskStarted": True, "noExternalEffects": True, "noAuthorityGranted": True,
            "noPrivateContentIncluded": True, "noPrivatePathIncluded": True,
        },
        "taskReviews": reviews,
        "authority": dict(REPORT_AUTHORITY), "boundary": COMPATIBILITY_BOUNDARY,
    }
    evaluation.write_new_private(output_path, _canonical_payload(report))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--materialization", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--temporary-parent", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = audit_planned_compatibility(
            root=args.root, materialization_root=Path(os.path.abspath(args.materialization)),
            pair_configuration_path=Path(os.path.abspath(args.config)),
            temporary_parent=Path(os.path.abspath(args.temporary_parent)),
            output_path=Path(os.path.abspath(args.output)),
        )
    except (OSError, ValueError, evaluation.OutcomeError) as exc:
        parser.error(str(exc))
    print(json.dumps({
        "status": report["status"], "profile": report["profile"],
        "tasks": report["counts"]["tasks"], "qualificationRequired": report["counts"]["qualificationRequired"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
