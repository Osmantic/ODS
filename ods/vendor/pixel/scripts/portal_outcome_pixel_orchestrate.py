#!/usr/bin/env python3
"""Compose one full-Pixel arm outcome run from exact private task bindings.

The supplied system adapter owns the real Work Broker/Builder/backend lifecycle.
This module owns fail-closed composition into the common comparison record so
Pixel and Codex are scored from identical task and evidence contracts.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Any, Callable

import portal_outcome_evaluation as evaluation
import portal_outcome_orchestrate as common
import portal_outcome_assistant_evidence as assistant_evidence
import portal_outcome_controller_evidence as controller_evidence
import portal_outcome_product_path as product_path
import portal_outcome_research_evidence as research_evidence
import portal_outcome_runner as runner
import portal_outcome_task as outcome_task
import portal_outcome_verifier as verifier_engine


PIXEL_RUNTIME_BOUNDARY = (
    "Content-free exact Pixel runtime identity only. It proves the admitted DSV4, current model qualification, Work policy, runner, and backend "
    "bindings for one fresh run and grants no execution, model start, provider, credential, network, external-effect, "
    "completion, publication, deployment, acceptance, or promotion authority."
)
WORK_VERIFICATION_BOUNDARY = (
    "Content-free independent verification evidence only. Worker output cannot select checks or grant merge, "
    "deployment, publication, external-effect, or policy authority."
)
INTERACTION_BOUNDARY = (
    "Content-free mechanically observed interaction telemetry for one single-admission noninteractive comparison run. "
    "It records only event classes, counts, and an observation digest; it contains no prompt, response, tool argument, "
    "result, path, credential, provider content, approval authority, or completion authority."
)
PIXEL_INTERACTION_SOURCES = frozenset({
    "pixel-assistant-conversation-v1", "pixel-builder-checkpoint-v1",
    "pixel-controller-ledger-v1", "pixel-researcher-lifecycle-v1",
})
WORK_JOB_RE = re.compile(r"^work-[0-9]{13}-[a-f0-9]{12}$")
WORK_CLAIM_RE = re.compile(r"^workclaim-[0-9]{13}-[a-f0-9]{12}$")
SIGNAL_RE = re.compile(r"^SIG[A-Z0-9]{1,16}$")
IMPLEMENTED_PRODUCT_ENGINES = frozenset({"portal-assistant", "work-builder", "deep-work-controller", "work-researcher"})
IMPLEMENTED_PRODUCT_PROFILES = product_path.profiles_for_engines(IMPLEMENTED_PRODUCT_ENGINES)


def _pixel_runtime_receipt(
    value: Any, *, model_contract: dict[str, Any], model_sha256: str, inference_sha256: str,
    run_id: str, profile: str,
) -> dict[str, Any]:
    value = evaluation.exact_fields(value, {
        "schemaVersion", "operation", "runId", "profile", "modelId", "modelContractSha256", "inferenceContractSha256",
        "workPolicySha256", "environmentSha256", "runtimeEnvironmentSha256", "harnessContractSha256", "runnerImageDigest", "verifierImageDigest",
        "backendImageDigest", "modelArtifactSha256", "launchBundleSha256", "qualificationReceiptSha256",
        "backendFresh", "runnerFresh", "realBackend", "realTools",
        "crossRunStateObserved", "qwenProductModel", "boundary",
    }, "Pixel runtime receipt")
    for field in ("modelContractSha256", "inferenceContractSha256", "workPolicySha256", "environmentSha256", "runtimeEnvironmentSha256", "harnessContractSha256", "modelArtifactSha256", "launchBundleSha256", "qualificationReceiptSha256"):
        evaluation.valid_hash(value[field], f"Pixel runtime {field}")
    for field in ("runnerImageDigest", "verifierImageDigest", "backendImageDigest"):
        if not isinstance(value[field], str) or not value[field].startswith("sha256:"):
            raise evaluation.OutcomeError(f"Pixel runtime {field} is invalid")
        evaluation.valid_hash(value[field][7:], f"Pixel runtime {field}")
    if (
        value["schemaVersion"] != 1 or value["operation"] != "pixel-portal-outcome-runtime" or value["runId"] != run_id
        or profile not in {"assistant", "builder", "controller", "researcher"} or value["profile"] != profile
        or value["modelId"] != "DeepSeek-V4-Flash-0731" or value["modelId"] != model_contract["modelId"]
        or value["modelContractSha256"] != model_sha256 or value["inferenceContractSha256"] != inference_sha256
        or value["backendImageDigest"] != model_contract["runtime"]["imageDigest"]
        or value["modelArtifactSha256"] != model_contract["artifact"]["sha256"]
        or value["backendFresh"] is not True or value["runnerFresh"] is not True
        or value["realBackend"] is not True or value["realTools"] is not True
        or value["crossRunStateObserved"] is not False or value["qwenProductModel"] is not False
        or value["boundary"] != PIXEL_RUNTIME_BOUNDARY
    ):
        raise evaluation.OutcomeError("Pixel runtime is not the exact fresh DSV4 product path")
    return value


def _pixel_interaction(value: Any, admission: dict[str, Any]) -> dict[str, Any]:
    value = evaluation.exact_fields(value, {
        "schemaVersion", "mode", "source", "observationSha256", "operatorInputsAfterAdmission",
        "operatorAttentionRequests", "approvalRequests", "scopeExpansionRequests", "safetyBlocks",
        "interruptions", "complete", "workerSelfReported", "boundary",
    }, "Pixel interaction telemetry")
    expected_source = {
        "assistant": "pixel-assistant-conversation-v1", "builder": "pixel-builder-checkpoint-v1",
        "controller": "pixel-controller-ledger-v1", "researcher": "pixel-researcher-lifecycle-v1",
    }[admission["profile"]]
    if (
        value["schemaVersion"] != 1 or value["mode"] != "single-admission-noninteractive"
        or value["source"] not in PIXEL_INTERACTION_SOURCES or value["source"] != expected_source
        or value["complete"] is not True or value["workerSelfReported"] is not False
        or value["boundary"] != INTERACTION_BOUNDARY
    ):
        raise evaluation.OutcomeError("Pixel interaction telemetry is not an exact complete product observation")
    evaluation.valid_hash(value["observationSha256"], "Pixel interaction observation")
    for field, maximum in (
        ("operatorInputsAfterAdmission", 1000000), ("operatorAttentionRequests", 1000000),
        ("approvalRequests", 1000000), ("scopeExpansionRequests", 1000000),
        ("safetyBlocks", 1), ("interruptions", 1000000),
    ):
        evaluation.integer(value[field], 0, maximum, f"Pixel interaction {field}")
    if (
        value["operatorInputsAfterAdmission"] > admission["budgets"]["operatorInterventions"]
        or value["approvalRequests"] > value["operatorAttentionRequests"]
        or value["scopeExpansionRequests"] > value["operatorAttentionRequests"]
    ):
        raise evaluation.OutcomeError("Pixel interaction telemetry exceeds admission or has inconsistent sub-counts")
    return value


def _pixel_execution(outcome: dict[str, Any], admission: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    usage = evaluation.exact_fields(
        outcome.get("usage"), {"modelRequests", "inputTokens", "outputTokens", "networkBytes", "toolCalls"},
        "Pixel arm usage",
    )
    budgets = admission["budgets"]
    for field, budget in (("modelRequests", "modelRequests"), ("inputTokens", "inputTokens"), ("outputTokens", "outputTokens")):
        evaluation.integer(usage[field], 0, budgets[budget], f"Pixel arm {field}")
    evaluation.integer(usage["networkBytes"], 0, 10737418240, "Pixel arm network bytes")
    evaluation.integer(usage["toolCalls"], 0, 1000000, "Pixel arm tool calls")
    exit_code = outcome.get("exitCode")
    if type(exit_code) is not int or not 0 <= exit_code <= 255:
        raise evaluation.OutcomeError("Pixel arm exit code is invalid")
    latency = evaluation.integer(outcome.get("latencyMs"), 0, 31536000000, "Pixel arm latency")
    if latency > (budgets["wallTimeSeconds"] + 120) * 1000:
        raise evaluation.OutcomeError("Pixel arm exceeded its wall-time receipt ceiling")
    authority = evaluation.exact_fields(
        outcome.get("authority"), {"sourceMutation", "merge", "deploy", "externalEffects"}, "Pixel arm authority",
    )
    if any(type(value) is not bool for value in authority.values()) or any(authority.values()):
        raise evaluation.OutcomeError("Pixel arm observed forbidden authority or effects")
    interaction = _pixel_interaction(outcome.get("interaction"), admission)
    status = "safety-blocked" if interaction["safetyBlocks"] else "completed" if exit_code == 0 else "failed"
    execution = {
        "status": status, "realBackend": True, "realTools": True,
        "exitCode": exit_code, "latencyMs": latency,
        "operatorInterventions": interaction["operatorInputsAfterAdmission"],
        "operatorAttentionRequests": interaction["operatorAttentionRequests"],
        "approvalRequests": interaction["approvalRequests"],
        "scopeExpansionRequests": interaction["scopeExpansionRequests"],
        "interruptions": interaction["interruptions"],
        "toolCalls": usage["toolCalls"],
        "modelRequests": usage["modelRequests"], "inputTokens": usage["inputTokens"],
        "outputTokens": usage["outputTokens"], "externalWrites": 0,
    }
    return execution, interaction


def _independent_verification(value: Any, definition: dict[str, Any]) -> dict[str, Any]:
    value = evaluation.exact_fields(value, {
        "schemaVersion", "format", "jobId", "claimId", "planSha256", "patchSha256", "candidateSha256",
        "status", "checks", "criteria", "network", "workerSelectedChecks", "externalEffects", "boundary",
    }, "Pixel independent verification")
    if (
        value["schemaVersion"] != 1 or value["format"] != "pixel-independent-verification-v1"
        or not isinstance(value["jobId"], str) or WORK_JOB_RE.fullmatch(value["jobId"]) is None
        or not isinstance(value["claimId"], str) or WORK_CLAIM_RE.fullmatch(value["claimId"]) is None
        or value["status"] not in {"pass", "fail"} or value["network"] != "none"
        or value["workerSelectedChecks"] is not False or value["externalEffects"] is not False
        or value["boundary"] != WORK_VERIFICATION_BOUNDARY
    ):
        raise evaluation.OutcomeError("Pixel independent verification identity or boundary is invalid")
    for field in ("planSha256", "patchSha256", "candidateSha256"):
        evaluation.valid_hash(value[field], f"Pixel independent verification {field}")
    planned = definition.get("workspaceVerification", {}).get("checks", [])
    observed = value["checks"]
    if not isinstance(observed, list) or len(observed) != len(planned):
        raise evaluation.OutcomeError("Pixel independent verification check set is incomplete")
    planned_by_id = {item["id"]: item for item in planned}
    seen: set[str] = set()
    for check in observed:
        if not isinstance(check, dict) or check.get("kind") not in {"patch-integrity", "command"}:
            raise evaluation.OutcomeError("Pixel independent verification check is invalid")
        fields = {"id", "kind", "criterionIndexes", "status", "candidateSha256", "evidenceSha256"}
        if check["kind"] == "patch-integrity":
            fields |= {"changes", "files", "bytes"}
        else:
            fields |= {"runtimeMilliseconds", "exitCode", "signal", "timedOut", "outputLimitExceeded", "spawnFailed", "stdoutBytes", "stdoutSha256", "stderrBytes", "stderrSha256"}
        check = evaluation.exact_fields(check, fields, "Pixel independent verification check")
        planned_check = planned_by_id.get(check["id"])
        if (
            check["id"] in seen or planned_check is None or check["kind"] != planned_check["kind"]
            or check["criterionIndexes"] != planned_check["criterionIndexes"]
            or check["candidateSha256"] != value["candidateSha256"] or check["status"] not in {"pass", "fail"}
        ):
            raise evaluation.OutcomeError("Pixel independent verification check differs from the immutable recipe")
        seen.add(check["id"])
        evaluation.valid_hash(check["evidenceSha256"], "Pixel independent verification check evidence")
        if check["kind"] == "patch-integrity":
            evaluation.integer(check["changes"], 0, 100000, "Pixel patch changes")
            evaluation.integer(check["files"], 0, 100000, "Pixel patch files")
            evaluation.integer(check["bytes"], 0, 1099511627776, "Pixel patch bytes")
        else:
            evaluation.integer(check["runtimeMilliseconds"], 0, 3630000, "Pixel verifier runtime")
            if check["exitCode"] is not None:
                evaluation.integer(check["exitCode"], 0, 255, "Pixel verifier exit code")
            if check["signal"] is not None and (not isinstance(check["signal"], str) or SIGNAL_RE.fullmatch(check["signal"]) is None):
                raise evaluation.OutcomeError("Pixel verifier signal is invalid")
            for field in ("timedOut", "outputLimitExceeded", "spawnFailed"):
                if type(check[field]) is not bool:
                    raise evaluation.OutcomeError("Pixel verifier failure flags are invalid")
            for field in ("stdoutBytes", "stderrBytes"):
                evaluation.integer(check[field], 0, 17825792, f"Pixel verifier {field}")
            for field in ("stdoutSha256", "stderrSha256"):
                evaluation.valid_hash(check[field], f"Pixel verifier {field}")
            passed = check["exitCode"] == 0 and check["signal"] is None and not check["timedOut"] and not check["outputLimitExceeded"] and not check["spawnFailed"]
            if (check["status"] == "pass") is not passed:
                raise evaluation.OutcomeError("Pixel verifier command status is inconsistent")
    if seen != set(planned_by_id):
        raise evaluation.OutcomeError("Pixel independent verification check set is incomplete")
    criteria = value["criteria"]
    acceptance = definition["acceptanceCriteria"]
    if not isinstance(criteria, list) or len(criteria) != len(acceptance):
        raise evaluation.OutcomeError("Pixel independent verification criterion set is incomplete")
    for index, criterion in enumerate(criteria):
        criterion = evaluation.exact_fields(criterion, {"index", "status", "checkIds"}, "Pixel verification criterion")
        relevant = [check for check in observed if index in check["criterionIndexes"]]
        expected_ids = [check["id"] for check in relevant]
        expected_status = "pass" if relevant and all(check["status"] == "pass" for check in relevant) else "fail"
        if criterion["index"] != index or criterion["status"] != expected_status or criterion["checkIds"] != expected_ids:
            raise evaluation.OutcomeError("Pixel independent verification criterion is inconsistent")
    expected_status = "pass" if all(item["status"] == "pass" for item in criteria) else "fail"
    if value["status"] != expected_status:
        raise evaluation.OutcomeError("Pixel independent verification aggregate status is inconsistent")
    return value


def _write_artifacts(run_dir: Path, items: Any, byte_ceiling: int) -> list[dict[str, Any]]:
    if not isinstance(items, list) or len(items) > 256:
        raise evaluation.OutcomeError("Pixel arm artifact inventory is invalid")
    retained = []
    observed: set[str] = set()
    total = 0
    for item in items:
        item = evaluation.exact_fields(item, {"kind", "relativePath", "payload"}, "Pixel arm artifact")
        relative = item["relativePath"]
        payload = item["payload"]
        if not isinstance(relative, str) or relative in observed or not isinstance(payload, bytes) or not payload:
            raise evaluation.OutcomeError("Pixel arm artifact is empty, duplicated, or invalid")
        observed.add(relative)
        total += len(payload)
        if total > byte_ceiling:
            raise evaluation.OutcomeError("Pixel arm artifacts exceed their admitted byte ceiling")
        relative_path = evaluation.relative_path(relative, "Pixel arm artifact path")
        if relative_path.parts[0] == "evidence" or relative_path.as_posix() == "run.json":
            raise evaluation.OutcomeError("Pixel arm artifact collides with controller evidence")
        target = run_dir.joinpath(*relative_path.parts)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
        retained.append({"kind": item["kind"], "relativePath": relative, "sha256": evaluation.sha256(payload), "bytes": len(payload)})
    return retained


def orchestrate_pixel_run(
    system: Any, *, root: Path, task_path: Path, run_dir: Path, run_id: str,
    runtime_condition: str,
    dimensions: Callable[[list[dict[str, Any]], list[dict[str, Any]]], Any], clock: Callable[[], str],
) -> dict[str, Any]:
    admission = outcome_task.admit_task(root, task_path)
    if admission["comparisonLane"] != "same-model-harness":
        raise evaluation.OutcomeError("Pixel outcome orchestration requires the same-model lane")
    if not callable(dimensions):
        raise evaluation.OutcomeError("Pixel dimension scores require an independent scorer callback")
    if not isinstance(run_dir, Path) or not run_dir.is_absolute() or not run_dir.is_dir():
        raise evaluation.OutcomeError("Pixel private run directory is unavailable")
    task, _raw = evaluation.read_json(task_path, "private outcome task", private=True)
    journey, _corpus_sha, _journey_sha = evaluation.corpus_contract(root, admission["journeyId"])
    route = product_path.resolve_product_path(admission, journey)
    product_path.require_engine(route, IMPLEMENTED_PRODUCT_ENGINES)
    product_path.require_evidence_emitter(
        route, common.SUPPORTED_EVIDENCE_TYPES | assistant_evidence.SUPPORTED_ASSISTANT_EVIDENCE_TYPES
        | controller_evidence.SUPPORTED_CONTROLLER_EVIDENCE_TYPES,
    )

    resolved: dict[str, tuple[dict[str, Any], bytes]] = {}
    for key, label in (
        ("userRequest", "user request"), ("sourceSnapshot", "source snapshot"),
        ("environment", "environment"), ("toolPolicy", "tool policy"), ("verifier", "verifier"),
        ("sharedModelContract", "model contract"), ("sharedInferenceContract", "inference contract"),
    ):
        resolved[key] = outcome_task.resolved_reference(task_path, task["bindings"][key], f"Pixel outcome {label}")
    request_ref, request_payload = resolved["userRequest"]
    source_ref, source_payload = resolved["sourceSnapshot"]
    environment_ref, environment_payload = resolved["environment"]
    tool_ref, tool_payload = resolved["toolPolicy"]
    verifier_ref, verifier_payload = resolved["verifier"]
    model_ref, model_payload = resolved["sharedModelContract"]
    inference_ref, inference_payload = resolved["sharedInferenceContract"]
    research_fixture_ref = None
    research_fixture_payload = None
    if admission["profile"] == "researcher":
        research_fixture_ref, research_fixture_payload = outcome_task.resolved_reference(
            task_path, task["bindings"]["researchFixture"], "Pixel outcome research fixture",
        )
    if request_ref["sha256"] != admission["bindings"]["userRequestSha256"] or verifier_ref["sha256"] != admission["bindings"]["verifierSha256"]:
        raise evaluation.OutcomeError("Pixel task bindings drifted after admission")
    if admission["profile"] == "researcher" and research_fixture_ref["sha256"] != admission["bindings"]["researchFixtureSha256"]:
        raise evaluation.OutcomeError("Pixel research fixture drifted after admission")
    tool_policy = outcome_task.validate_tool_policy(tool_payload, admission["capabilities"])
    environment = outcome_task.validate_environment(environment_payload, tool_policy=tool_policy)
    definition = verifier_engine.load_definition(verifier_payload)
    model_contract = evaluation.parse_json(model_payload, "Pixel shared model contract")
    inference_contract = evaluation.parse_json(inference_payload, "Pixel shared inference contract")
    if model_contract["modelId"] != "DeepSeek-V4-Flash-0731":
        raise evaluation.OutcomeError("Pixel product comparison refuses a non-DSV4 model")

    runtime = None
    try:
        runtime = _pixel_runtime_receipt(
            system.qualify_runtime(
                run_id=run_id, admission=admission, model_contract=model_contract,
                inference_contract=inference_contract, model_contract_payload=model_payload,
                inference_contract_payload=inference_payload, model_contract_sha256=model_ref["sha256"],
                inference_contract_sha256=inference_ref["sha256"], run_dir=run_dir,
            ),
            model_contract=model_contract, model_sha256=model_ref["sha256"], inference_sha256=inference_ref["sha256"],
            run_id=run_id, profile=admission["profile"],
        )
        runtime_evidence = common.write_evidence(
            run_dir, "fresh-pixel-runtime.json", json.dumps(runtime, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            "runtime-environment",
        )
        control = system.runtime_control(run_id=run_id, condition=runtime_condition)
        control_evidence = common.write_runtime_control(run_dir, evaluation.canonical(control))
        started_at = clock()
        outcome = system.run_pixel(
            run_id=run_id, admission=admission, task=task, request_payload=request_payload,
            source_payload=source_payload, source_reference=source_ref,
            environment_payload=environment_payload, environment=environment,
            tool_policy_payload=tool_payload, tool_policy=tool_policy,
            verifier_definition=definition, model_contract=model_contract,
            inference_contract=inference_contract, run_dir=run_dir,
            research_fixture_payload=research_fixture_payload, research_fixture_reference=research_fixture_ref,
        )
        finished_at = clock()
        execution, interaction = _pixel_execution(outcome, admission)
        message = outcome.get("finalMessage", "")
        if not isinstance(message, str) or len(message.encode("utf-8")) > 65536:
            raise evaluation.OutcomeError("Pixel final message is invalid")
        artifacts = _write_artifacts(run_dir, outcome.get("artifacts"), admission["budgets"]["artifactBytes"])
        if admission["profile"] == "builder":
            evidence = [
                common.write_evidence(run_dir, "source.json", json.dumps({"sourceSnapshotSha256": source_ref["sha256"]}, sort_keys=True).encode("utf-8"), "exact-source"),
                runtime_evidence,
                common.write_evidence(
                    run_dir, "command-exit.json",
                    json.dumps({
                        "exitCode": execution["exitCode"], "command": "Pixel Work Broker Builder (full product path)",
                        "modelRequestsSource": "Pixel model-proxy receipt", "modelRequests": execution["modelRequests"],
                        "inputTokens": execution["inputTokens"], "outputTokens": execution["outputTokens"],
                        "latencyMs": execution["latencyMs"],
                        "operatorInterventions": execution["operatorInterventions"],
                        "operatorAttentionRequests": execution["operatorAttentionRequests"],
                        "approvalRequests": execution["approvalRequests"],
                        "scopeExpansionRequests": execution["scopeExpansionRequests"],
                        "toolCalls": execution["toolCalls"],
                        "finalReplySha256": evaluation.sha256(message.encode("utf-8")),
                        "finalReplyBytes": len(message.encode("utf-8")), "finalReplyNormalization": "exact-utf8",
                    }, sort_keys=True).encode("utf-8"), "command-exit",
                ),
            ]
            independent = _independent_verification(outcome.get("independentVerification"), definition)
            independent_evidence = common.write_evidence(
                run_dir, "independent-verification.json",
                json.dumps(independent, sort_keys=True, separators=(",", ":")).encode("utf-8"), "independent-verifier",
            )
            evidence.append(independent_evidence)
            evidence.append(common.write_evidence(
                run_dir, "artifacts.json", json.dumps({"artifacts": len(artifacts)}, sort_keys=True).encode("utf-8"), "artifact-digest",
            ))
            verifier_kind = "deterministic-verifier"
        elif admission["profile"] == "controller":
            independent = _independent_verification(outcome.get("independentVerification"), definition)
            evidence, independent_evidence = controller_evidence.validate_and_emit(
                run_dir=run_dir, outcome=outcome, artifacts=artifacts,
                required_evidence=route["requiredEvidence"], runtime_evidence=runtime_evidence,
                independent_verification=independent, source_sha256=source_ref["sha256"],
                model_sha256=model_ref["sha256"], inference_sha256=inference_ref["sha256"],
                admission=admission, environment=environment, tool_policy=tool_policy,
            )
            verifier_kind = "deterministic-verifier"
        elif admission["profile"] == "assistant":
            assistant_required = set(route["requiredEvidence"]) & set(assistant_evidence.SUPPORTED_ASSISTANT_EVIDENCE_TYPES)
            evidence = assistant_evidence.validate_and_emit(
                root=root, run_dir=run_dir, assistant_evidence=outcome.get("assistantEvidence"),
                request_payload=request_payload, request_sha256=request_ref["sha256"],
                source_sha256=source_ref["sha256"], required_evidence=assistant_required,
                expected_provider="local", expected_model=model_contract["modelId"],
            )
            if "runtime-environment" in route["requiredEvidence"]:
                evidence.append(runtime_evidence)
            if "command-exit" in route["requiredEvidence"]:
                evidence.append(common.write_evidence(
                    run_dir, "assistant-command-exit.json",
                    json.dumps({
                        "exitCode": execution["exitCode"], "command": "Pixel Portal Assistant (full product path)",
                        "modelRequestsSource": "Pixel Assistant model-proxy receipt",
                        "modelRequests": execution["modelRequests"], "inputTokens": execution["inputTokens"],
                        "outputTokens": execution["outputTokens"], "latencyMs": execution["latencyMs"],
                        "operatorInterventions": execution["operatorInterventions"],
                        "operatorAttentionRequests": execution["operatorAttentionRequests"],
                        "approvalRequests": execution["approvalRequests"],
                        "scopeExpansionRequests": execution["scopeExpansionRequests"],
                        "toolCalls": execution["toolCalls"],
                        "finalReplySha256": evaluation.sha256(message.encode("utf-8")),
                        "finalReplyBytes": len(message.encode("utf-8")), "finalReplyNormalization": "exact-utf8",
                    }, sort_keys=True).encode("utf-8"), "command-exit",
                ))
            independent_evidence = next(
                (item for item in evidence if item["type"] == "independent-verifier"), evidence[0],
            )
            verifier_kind = "deterministic-verifier" if independent_evidence["type"] == "independent-verifier" else "controller-evidence-validator"
        else:
            evidence, independent_evidence = research_evidence.validate_and_emit(
                run_dir=run_dir, outcome=outcome, artifacts=artifacts,
            )
            verifier_kind = "deterministic-verifier"
        product_path.require_exact_evidence(route, evidence)
        assertions = verifier_engine.run_checks(
            definition, journey=journey, admission=admission, run_dir=run_dir, evidence=evidence, artifacts=artifacts,
        )
        record = runner.assemble_run(
            admission=admission, backend="pixel", run_id=run_id, started_at=started_at, finished_at=finished_at,
            execution=execution, interaction=interaction,
            execution_identity={
                "harnessContractSha256": runtime["harnessContractSha256"],
                "modelContractSha256": model_ref["sha256"], "inferenceContractSha256": inference_ref["sha256"],
                "toolPolicySha256": tool_ref["sha256"], "freshRuntimeStartSha256": runtime_evidence["sha256"],
                "runtimeCondition": runtime_condition, "runtimeControlSha256": control_evidence["sha256"],
                "interactionMode": "single-admission-noninteractive",
                "crossRunStateObserved": False,
            },
            evidence=evidence, assertions=assertions, dimensions=dimensions(evidence, artifacts), artifacts=artifacts,
            safety_findings=[],
            verifier={"independent": True, "kind": verifier_kind, "backendOutputUsedAsScore": False, "evidencePath": independent_evidence["relativePath"], "evidenceSha256": independent_evidence["sha256"]},
            authority={"scopeExpansionDetected": False, "privateDataSentRemote": False, "unreconciledExternalWrite": False, "safetyBoundaryRelaxed": False},
        )
        payload = json.dumps(record, indent=2, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"
        descriptor = os.open(run_dir / "run.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
        return record
    finally:
        system.teardown_runtime(run_id=run_id, runtime=runtime)
