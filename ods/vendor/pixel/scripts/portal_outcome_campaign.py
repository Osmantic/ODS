#!/usr/bin/env python3
"""Run every declared Pixel/Codex outcome pair in a private campaign plan."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any

import portal_outcome_evaluation as evaluation
import portal_outcome_task as outcome_task


PLAN_SCHEMA = "https://osmantic.com/pixel/schemas/portal-outcome-campaign-plan-v1.schema.json"
PLAN_ID_RE = re.compile(r"^outcomeplan-[0-9]{13}-[a-f0-9]{12}$")
PLAN_BOUNDARY = (
    "Private path-bearing campaign plan only. It grants no model, tool, provider, external-effect, "
    "publication, deployment, acceptance, or promotion authority."
)
CAMPAIGN_BOUNDARY = (
    "Content-free complete-campaign index only; private plans, run evidence, and reviewer identity "
    "remain in separately protected owner custody. This record grants no capability, effect, "
    "acceptance, publication, deployment, or promotion authority."
)


def relative_file(parent: Path, value: Any, label: str) -> Path:
    relative = evaluation.relative_path(value, label)
    current = parent
    for part in relative.parts:
        current = current / part
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                raise evaluation.OutcomeError(f"{label} contains a link")
        except OSError as exc:
            raise evaluation.OutcomeError(f"{label} is unavailable") from exc
    evaluation.private_parent(current)
    return current


def corpus_scenarios(root: Path) -> tuple[dict[tuple[str, str, str, str | None], dict[str, Any]], str]:
    corpus_path = root / "security-evals" / "portal-user-journeys" / "corpus-v1.json"
    corpus, raw = evaluation.read_json(corpus_path, "portal journey corpus")
    journeys = corpus.get("journeys")
    if not isinstance(journeys, list) or not journeys:
        raise evaluation.OutcomeError("portal journey corpus is incomplete")
    expected: dict[tuple[str, str, str, str | None], dict[str, Any]] = {}
    for journey in journeys:
        if not isinstance(journey, dict) or not isinstance(journey.get("id"), str) or not isinstance(journey.get("faults"), list):
            raise evaluation.OutcomeError("portal journey corpus contains an invalid journey")
        keys = [
            (journey["id"], lane, kind, fault)
            for lane in evaluation.COMPARISON_LANES
            for kind, fault in [("baseline", None)] + [("fault-injection", item) for item in journey["faults"]]
        ]
        for key in keys:
            if key in expected:
                raise evaluation.OutcomeError("portal journey campaign scenarios are duplicated")
            expected[key] = journey
    return expected, evaluation.sha256(raw)


def validate_plan(root: Path, plan_path: Path) -> tuple[dict[str, Any], bytes, dict[tuple[str, str, str, str | None], dict[str, Any]]]:
    plan, raw = evaluation.read_json(plan_path, "private outcome campaign plan", private=True)
    evaluation.exact_fields(plan, {
        "$schema", "schemaVersion", "operation", "planId", "corpusSha256", "runtimeCondition", "pairs", "boundary",
    }, "private outcome campaign plan")
    if (
        plan["$schema"] != PLAN_SCHEMA or plan["schemaVersion"] != 1
        or plan["operation"] != "pixel-portal-outcome-campaign-plan"
        or not isinstance(plan["planId"], str)
        or PLAN_ID_RE.fullmatch(plan["planId"]) is None
        or plan["runtimeCondition"] not in {"cold-first-request", "warm-neutral-probe"}
        or plan["boundary"] != PLAN_BOUNDARY
    ):
        raise evaluation.OutcomeError("outcome campaign plan identity or authority boundary is invalid")
    expected, corpus_sha = corpus_scenarios(root)
    if plan["corpusSha256"] != corpus_sha:
        raise evaluation.OutcomeError("outcome campaign plan is not bound to the exact public corpus")
    pairs = plan["pairs"]
    if not isinstance(pairs, list) or not 1 <= len(pairs) <= 256:
        raise evaluation.OutcomeError("outcome campaign plan has no bounded run pairs")
    observed: dict[tuple[str, str, str, str | None], dict[str, Any]] = {}
    used_paths: set[Path] = set()
    for index, pair in enumerate(pairs):
        pair = evaluation.exact_fields(pair, {
            "journeyId", "comparisonLane", "scenarioKind", "scenarioFault", "taskPath", "pixelRunPath", "codexRunPath",
        }, f"campaign pair {index}")
        lane, kind, fault = pair["comparisonLane"], pair["scenarioKind"], pair["scenarioFault"]
        if not isinstance(pair["journeyId"], str) or not isinstance(lane, str) or not isinstance(kind, str):
            raise evaluation.OutcomeError("outcome campaign scenario identity is invalid")
        key = (pair["journeyId"], lane, kind, fault if isinstance(fault, str) else None)
        if (
            lane not in evaluation.COMPARISON_LANES
            or kind not in {"baseline", "fault-injection"}
            or kind == "baseline" and fault is not None
            or kind == "fault-injection" and not isinstance(fault, str)
            or key not in expected
            or key in observed
        ):
            raise evaluation.OutcomeError("outcome campaign scenario is unknown or duplicated")
        task_path = relative_file(plan_path.parent, pair["taskPath"], f"campaign pair {index} task")
        pixel_path = relative_file(plan_path.parent, pair["pixelRunPath"], f"campaign pair {index} Pixel run")
        codex_path = relative_file(plan_path.parent, pair["codexRunPath"], f"campaign pair {index} Codex run")
        if pixel_path == codex_path:
            raise evaluation.OutcomeError("outcome campaign pair reuses one run record")
        if task_path in used_paths or pixel_path in used_paths or codex_path in used_paths:
            raise evaluation.OutcomeError("outcome campaign reuses a task or run record across scenarios")
        used_paths.update((task_path, pixel_path, codex_path))
        observed[key] = {"task": task_path, "pixel": pixel_path, "codex": codex_path}
    return plan, raw, observed


def build_campaign(root: Path, plan_path: Path) -> dict[str, Any]:
    plan, plan_raw, pairs = validate_plan(root, plan_path)
    expected, corpus_sha = corpus_scenarios(root)
    entries = []
    cutoffs = []
    comparison_hashes = []
    lane_harnesses: dict[str, tuple[str, str]] = {}
    autonomy_totals = Counter()
    for key in sorted(pairs, key=lambda item: (item[0], item[1], item[2], item[3] or "")):
        paths = pairs[key]
        admission = outcome_task.admit_task(root, paths["task"])
        admission_sha = evaluation.sha256(evaluation.canonical(admission))
        expected_task_binding = {
            "comparisonLane": admission["comparisonLane"],
            "corpusSha256": admission["corpusSha256"],
            "journeySha256": admission["journeySha256"],
            "taskSpecificationSha256": admission["taskSpecificationSha256"],
            "taskAdmissionSha256": admission_sha,
            "userRequestSha256": admission["bindings"]["userRequestSha256"],
            "sourceSnapshotSha256": admission["bindings"]["sourceSnapshotSha256"],
            "environmentSha256": admission["bindings"]["environmentSha256"],
            "toolPolicySha256": admission["bindings"]["toolPolicySha256"],
            "verifierSha256": admission["bindings"]["verifierSha256"],
            "sharedModelContractSha256": admission["bindings"]["sharedModelContractSha256"],
            "sharedInferenceContractSha256": admission["bindings"]["sharedInferenceContractSha256"],
            "researchFixtureSha256": admission["bindings"]["researchFixtureSha256"],
            "capabilitiesSha256": evaluation.sha256(evaluation.canonical(admission["capabilities"])),
            "budgetsSha256": evaluation.sha256(evaluation.canonical(admission["budgets"])),
            "dataRoute": admission["dataRoute"],
            "effectBoundary": admission["effectBoundary"],
            "scenario": admission["scenario"],
        }
        result = evaluation.compare_runs(root, paths["pixel"], paths["codex"])
        if (result["journeyId"], result["comparisonLane"], result["scenarioKind"], result["scenarioFault"]) != key:
            raise evaluation.OutcomeError("outcome comparison does not match its declared campaign scenario")
        if result["taskBindingSha256"] != evaluation.sha256(evaluation.canonical(expected_task_binding)):
            raise evaluation.OutcomeError("outcome comparison is not bound to its exact admitted task")
        if result["runtimeCondition"] != plan["runtimeCondition"]:
            raise evaluation.OutcomeError("outcome comparison differs from its immutable campaign runtime condition")
        observed_harnesses = (result["pixelHarnessContractSha256"], result["codexHarnessContractSha256"])
        if lane_harnesses.setdefault(result["comparisonLane"], observed_harnesses) != observed_harnesses:
            raise evaluation.OutcomeError("outcome campaign mixes harness contracts within a lane")
        comparison_sha = evaluation.sha256(evaluation.canonical(result))
        autonomy_totals.update({
            "pixelToolCalls": result["metrics"]["pixelToolCalls"],
            "codexToolCalls": result["metrics"]["codexToolCalls"],
            "pixelOperatorInterventions": result["metrics"]["pixelOperatorInterventions"],
            "codexOperatorInterventions": result["metrics"]["codexOperatorInterventions"],
            "pixelExcessOperatorInterventions": result["metrics"]["pixelExcessOperatorInterventions"],
            "codexExcessOperatorInterventions": result["metrics"]["codexExcessOperatorInterventions"],
            "pixelOperatorAttentionRequests": result["metrics"]["pixelOperatorAttentionRequests"],
            "codexOperatorAttentionRequests": result["metrics"]["codexOperatorAttentionRequests"],
            "pixelExcessOperatorAttentionRequests": result["metrics"]["pixelExcessOperatorAttentionRequests"],
            "codexExcessOperatorAttentionRequests": result["metrics"]["codexExcessOperatorAttentionRequests"],
            "pixelApprovalRequests": result["metrics"]["pixelApprovalRequests"],
            "codexApprovalRequests": result["metrics"]["codexApprovalRequests"],
            "pixelScopeExpansionRequests": result["metrics"]["pixelScopeExpansionRequests"],
            "codexScopeExpansionRequests": result["metrics"]["codexScopeExpansionRequests"],
            "pixelUnnecessarySafetyBlocks": result["metrics"]["pixelUnnecessarySafetyBlocks"],
            "codexUnnecessarySafetyBlocks": result["metrics"]["codexUnnecessarySafetyBlocks"],
        })
        comparison_hashes.append(comparison_sha)
        cutoffs.append(evaluation.timestamp(result["evidenceCutoffAt"], "comparison evidence cutoff"))
        entries.append({
            "journeyId": result["journeyId"], "comparisonLane": result["comparisonLane"],
            "runtimeCondition": result["runtimeCondition"], "scenarioKind": result["scenarioKind"],
            "scenarioFault": result["scenarioFault"], "scenarioSeedSha256": result["scenarioSeedSha256"],
            "taskAdmissionSha256": admission_sha,
            "taskBindingSha256": result["taskBindingSha256"], "comparisonSha256": comparison_sha,
            "status": result["status"], "classification": result["classification"],
        })
    counts = Counter(item["classification"] for item in entries)
    status_counts = Counter(item["status"] for item in entries)
    comparator_sha = evaluation.sha256(evaluation.read_bytes(
        Path(evaluation.__file__).resolve(), limit=evaluation.MAX_JSON_BYTES,
    ))
    plan_sha = evaluation.sha256(plan_raw)
    complete = set(pairs) == set(expected)
    passing = complete and entries and all(item["status"] == "pass" for item in entries)
    seed = evaluation.canonical({
        "corpusSha256": corpus_sha, "comparatorSha256": comparator_sha,
        "planSha256": plan_sha, "comparisonSha256": comparison_hashes,
    })
    return {
        "schemaVersion": 1,
        "operation": "pixel-portal-outcome-campaign",
        "campaignId": f"outcomecampaign-{evaluation.sha256(seed)[:24]}",
        "corpusSha256": corpus_sha,
        "comparatorSha256": comparator_sha,
        "planSha256": plan_sha,
        "runtimeCondition": plan["runtimeCondition"],
        "evidenceCutoffAt": max(cutoffs).isoformat().replace("+00:00", "Z"),
        "status": "pass" if passing else "blocked",
        "requiredJourneys": len({key[0] for key in expected}),
        "coveredJourneys": len({key[0] for key in pairs}),
        "requiredScenarios": len(expected),
        "coveredScenarios": len(pairs),
        "laneHarnessContracts": [
            {
                "comparisonLane": lane,
                "pixelHarnessContractSha256": harnesses[0],
                "codexHarnessContractSha256": harnesses[1],
            }
            for lane, harnesses in sorted(lane_harnesses.items())
        ],
        "comparisons": entries,
        "summary": {
            "pass": status_counts["pass"], "blocked": status_counts["blocked"],
            "parity": counts["parity"], "pixelRegression": counts["pixel-regression"],
            "capabilityBlocking": counts["capability-blocking"],
            "referenceFailure": counts["reference-failure"], "unexplainedDelta": counts["unexplained-delta"],
            "safetyFailure": counts["safety-failure"],
        },
        "autonomy": {
            "singleAdmissionNoninteractive": True,
            **{key: autonomy_totals[key] for key in (
                "pixelToolCalls", "codexToolCalls",
                "pixelOperatorInterventions", "codexOperatorInterventions",
                "pixelExcessOperatorInterventions", "codexExcessOperatorInterventions",
                "pixelOperatorAttentionRequests", "codexOperatorAttentionRequests",
                "pixelExcessOperatorAttentionRequests", "codexExcessOperatorAttentionRequests",
                "pixelApprovalRequests", "codexApprovalRequests",
                "pixelScopeExpansionRequests", "codexScopeExpansionRequests",
                "pixelUnnecessarySafetyBlocks", "codexUnnecessarySafetyBlocks",
            )},
        },
        "privacy": {
            "pathsIncluded": False, "privateContentIncluded": False, "backendIdentityIncluded": False,
            "providerContentIncluded": False, "credentialsIncluded": False,
        },
        "boundary": CAMPAIGN_BOUNDARY,
    }


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a complete content-free Pixel/Codex outcome campaign")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = arguments()
    try:
        root = args.root.resolve()
        plan_path = Path(os.path.abspath(args.plan))
        output_path = Path(os.path.abspath(args.output)) if args.output else None
        for path, label in ((plan_path, "campaign plan"), (output_path, "campaign output")):
            if path is not None and (path == root or root in path.parents):
                raise evaluation.OutcomeError(f"{label} must remain outside the source repository")
        evaluation.private_parent(plan_path)
        try:
            if plan_path.resolve(strict=True) != plan_path:
                raise evaluation.OutcomeError("private campaign plan path contains a link")
        except OSError as exc:
            raise evaluation.OutcomeError("private campaign plan is unavailable") from exc
        result = build_campaign(root, plan_path)
        payload = json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"
        if output_path:
            evaluation.write_new_private(output_path, payload)
        print(payload.decode("utf-8"), end="")
        return 0 if result["status"] == "pass" else 3
    except (evaluation.OutcomeError, UnicodeError, OSError) as exc:
        print(f"[pixel] ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
