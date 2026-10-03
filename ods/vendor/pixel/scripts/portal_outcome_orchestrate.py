#!/usr/bin/env python3
"""Compose one controlled-lane codex-arm outcome run from fail-closed primitives.

The live system adapter arrives with its own qualification; this module holds the
composition only, so every ordering, refusal, and teardown path is unit-verifiable.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable

import portal_outcome_evaluation as evaluation
import portal_outcome_runner as runner
import portal_outcome_task as outcome_task
import portal_outcome_verifier as verifier_engine


EVIDENCE_DIRECTORY = "evidence"
BUILDER_EVIDENCE_TYPES = {"exact-source", "runtime-environment", "command-exit", "artifact-digest", "independent-verifier"}
RESEARCH_EVIDENCE_TYPES = {"source-provenance", "observation-time", "citation-coverage", "independent-verifier"}
ASSISTANT_EVIDENCE_TYPES = {
    "exact-source", "runtime-environment", "command-exit", "checkpoint-lineage", "privacy-route", "independent-verifier",
}
CONTROLLER_EVIDENCE_TYPES = {
    "exact-source", "runtime-environment", "artifact-digest", "independent-verifier", "checkpoint-lineage", "privacy-route",
}
SUPPORTED_EVIDENCE_TYPES = BUILDER_EVIDENCE_TYPES | RESEARCH_EVIDENCE_TYPES | ASSISTANT_EVIDENCE_TYPES | CONTROLLER_EVIDENCE_TYPES
IMPLEMENTED_CODEX_PROFILES = frozenset({"assistant", "builder", "controller", "researcher"})
CODEX_ORCHESTRATOR_PROFILES = frozenset({"assistant", "builder", "controller", "researcher"})
CODEX_CHECKPOINT_BOUNDARY = (
    "Content-free ordered custody for one exact Codex comparison transcript. It retains event hashes and terminal "
    "turn counts only; it grants no transcript content, execution, retry, provider, external-effect, completion, "
    "publication, deployment, acceptance, or promotion authority."
)
CODEX_PRIVACY_ROUTE_BOUNDARY = (
    "Content-free route proof for one exact hardened Codex comparison run. It binds the admitted tool and environment "
    "contracts to local workspace and local-model-boundary routing only; it grants no network, credential, provider, "
    "external-effect, completion, publication, deployment, acceptance, or promotion authority."
)


def write_evidence(run_dir: Path, relative: str, payload: bytes, kind: str) -> dict[str, Any]:
    relative_path = f"{EVIDENCE_DIRECTORY}/{relative}"
    target = run_dir / relative_path
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
    return {"type": kind, "relativePath": relative_path, "sha256": evaluation.sha256(payload), "bytes": len(payload)}


def write_runtime_control(run_dir: Path, payload: bytes) -> dict[str, Any]:
    """Write the non-scoring runtime-control sidecar at its validator-bound path."""
    target = run_dir / "runtime-control.json"
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
    return {"relativePath": "runtime-control.json", "sha256": evaluation.sha256(payload), "bytes": len(payload)}


def codex_checkpoint_lineage(transcript: str, profile: str = "assistant") -> bytes:
    if profile not in {"assistant", "controller"}:
        raise evaluation.OutcomeError("Codex checkpoint profile is invalid")
    if not isinstance(transcript, str) or not transcript.strip() or len(transcript) > runner.MAX_TRANSCRIPT_BYTES:
        raise evaluation.OutcomeError("Codex Assistant checkpoint transcript is empty or oversized")
    previous = "0" * 64
    events = 0
    completed_turns = 0
    for line in transcript.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        event = evaluation.parse_json(line.encode("utf-8"), "Codex Assistant checkpoint event")
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            raise evaluation.OutcomeError("Codex Assistant checkpoint event is unstructured")
        event_sha256 = evaluation.sha256(line.encode("utf-8"))
        previous = evaluation.sha256(evaluation.canonical({
            "index": events, "eventSha256": event_sha256, "previousRecordSha256": previous,
        }))
        events += 1
        completed_turns += event["type"] == "turn.completed"
    if events < 1 or completed_turns < 1:
        raise evaluation.OutcomeError("Codex Assistant checkpoint has no terminal turn custody")
    return evaluation.canonical({
        "schemaVersion": 1, "format": f"codex-{profile}-checkpoint-lineage-v1",
        "transcriptSha256": evaluation.sha256(transcript.encode("utf-8")),
        "events": events, "completedTurns": completed_turns, "headRecordSha256": previous,
        "privateTextIncluded": False, "lineageValidated": True, "boundary": CODEX_CHECKPOINT_BOUNDARY,
    })


def codex_privacy_route(
    *, admission: dict[str, Any], environment: dict[str, Any], tool_policy: dict[str, Any],
) -> bytes:
    if (
        admission.get("profile") not in {"assistant", "controller"} or admission.get("dataRoute") != "local-only"
        or environment.get("isolation", {}).get("directNetwork") is not False
        or tool_policy.get("brokeredServices") != ["local-model"]
        or any(tool_policy.get(field) is not False for field in (
            "hostAccess", "ambientCredentials", "externalEffects", "mergeAuthority", "deployAuthority", "policyMutation",
        ))
    ):
        raise evaluation.OutcomeError("Codex Assistant privacy route differs from its exact local-only authority")
    return evaluation.canonical({
        "schemaVersion": 1, "format": f"codex-{admission['profile']}-privacy-route-v1",
        "environmentSha256": admission["bindings"]["environmentSha256"],
        "toolPolicySha256": admission["bindings"]["toolPolicySha256"],
        "routes": ["local-model-boundary", "local-workspace"],
        "directNetworkSurface": False, "ambientCredentials": False,
        "privateDataSentRemote": False, "externalEffects": False,
        "boundary": CODEX_PRIVACY_ROUTE_BOUNDARY,
    })


def orchestrate_codex_run(
    system: Any,
    *,
    root: Path,
    task_path: Path,
    run_dir: Path,
    run_id: str,
    artifact_path: Path,
    artifact_manifest_path: Path | None = None,
    launch_arguments: list[str],
    harness_contract_sha256: str,
    runtime_condition: str,
    dimensions: Any,
    clock: Callable[[], str],
) -> dict[str, Any]:
    admission = outcome_task.admit_task(root, task_path)
    if admission["comparisonLane"] != "same-model-harness":
        raise evaluation.OutcomeError("codex-arm orchestration requires the same-model-harness lane")
    if not callable(dimensions):
        raise evaluation.OutcomeError(
            "dimension scores require the owner rubric or an independent scorer callback; refusing to fabricate them"
        )
    evaluation.valid_hash(harness_contract_sha256, "codex harness contract digest")
    task_value, _raw = evaluation.read_json(task_path, "private outcome task", private=True)
    journey, _corpus_sha, _journey_sha = evaluation.corpus_contract(root, admission["journeyId"])
    if admission["profile"] not in CODEX_ORCHESTRATOR_PROFILES:
        raise evaluation.OutcomeError("Codex orchestrator has no exact evidence adapter for this product profile")
    supported_evidence = (
        RESEARCH_EVIDENCE_TYPES if admission["profile"] == "researcher"
        else ASSISTANT_EVIDENCE_TYPES if admission["profile"] == "assistant"
        else CONTROLLER_EVIDENCE_TYPES if admission["profile"] == "controller"
        else BUILDER_EVIDENCE_TYPES
    )
    missing = set(journey.get("requiredEvidence", [])) - supported_evidence
    if missing:
        raise evaluation.OutcomeError("orchestrator cannot yet produce every journey evidence type")

    model_ref, model_payload = outcome_task.resolved_reference(
        task_path, task_value["bindings"]["sharedModelContract"], "shared model contract",
    )
    inference_ref, inference_payload = outcome_task.resolved_reference(
        task_path, task_value["bindings"]["sharedInferenceContract"], "shared inference contract",
    )
    request_ref, request_payload = outcome_task.resolved_reference(
        task_path, task_value["bindings"]["userRequest"], "outcome task userRequest",
    )
    source_ref, source_payload = outcome_task.resolved_reference(
        task_path, task_value["bindings"]["sourceSnapshot"], "outcome task sourceSnapshot",
    )
    research_fixture_ref = None
    research_fixture_payload = None
    if admission["profile"] == "researcher":
        research_fixture_ref, research_fixture_payload = outcome_task.resolved_reference(
            task_path, task_value["bindings"]["researchFixture"], "outcome task research fixture",
        )
        if research_fixture_ref["sha256"] != admission["bindings"]["researchFixtureSha256"]:
            raise evaluation.OutcomeError("outcome task research fixture drifted after admission")
    environment_ref, environment_payload = outcome_task.resolved_reference(
        task_path, task_value["bindings"]["environment"], "outcome task environment",
    )
    tool_policy_ref, tool_policy_payload = outcome_task.resolved_reference(
        task_path, task_value["bindings"]["toolPolicy"], "outcome task toolPolicy",
    )
    tool_policy = outcome_task.validate_tool_policy(tool_policy_payload, admission["capabilities"])
    environment = outcome_task.validate_environment(environment_payload, tool_policy=tool_policy)
    verifier_ref, verifier_payload = outcome_task.resolved_reference(
        task_path, task_value["bindings"]["verifier"], "outcome task verifier",
    )
    if verifier_ref["sha256"] != admission["bindings"]["verifierSha256"]:
        raise evaluation.OutcomeError("outcome task verifier drifted after admission")
    definition = verifier_engine.load_definition(verifier_payload)
    if request_ref["sha256"] != admission["bindings"]["userRequestSha256"]:
        raise evaluation.OutcomeError("outcome task user request drifted after admission")
    model_contract = evaluation.parse_json(model_payload, "shared model contract")
    inference_contract = evaluation.parse_json(inference_payload, "shared inference contract")
    expected_wire = "openai-chat-completions" if model_contract["runtime"]["protocol"] == "openai-chat-completions-v1" else "openai-responses"
    if inference_contract["request"]["wireApi"] != expected_wire:
        raise evaluation.OutcomeError("shared model and inference wire protocols disagree")

    artifact_kind = model_contract["artifact"]["kind"]
    if artifact_kind == "single-file":
        if artifact_manifest_path is not None:
            raise evaluation.OutcomeError("single-file model orchestration refuses a directory manifest")
        runner.verify_model_artifact(artifact_path, model_contract)
    elif artifact_kind == "directory-manifest":
        if artifact_manifest_path is None:
            raise evaluation.OutcomeError("codex-arm orchestration for directory artifacts requires a manifest path")
        manifest, _manifest_raw = evaluation.read_json(
            artifact_manifest_path, "private model artifact manifest", private=True,
        )
        runner.verify_model_artifact_manifest(artifact_path, manifest, model_contract)
    else:
        raise evaluation.OutcomeError("codex-arm orchestration model artifact kind is unsupported")
    runner.verify_runtime_identity(system, model_contract)
    runner.verify_launch_arguments(launch_arguments, model_contract)
    observed_harness_contract_sha256 = system.codex_harness_contract_sha256()
    evaluation.valid_hash(observed_harness_contract_sha256, "observed codex harness contract digest")
    if observed_harness_contract_sha256 != harness_contract_sha256:
        raise evaluation.OutcomeError("codex harness contract differs from its exact image, executable, configuration, or source")

    network = system.network_create(run_id)
    container = None
    boundary = None
    try:
        container = system.model_container_start(network, model_contract, launch_arguments, artifact_path)
        system.wait_healthy(container)
        implementation = model_contract["runtime"]["implementation"]
        if implementation == "llama.cpp":
            fresh = runner.fresh_runtime_start(
                model_contract,
                container_id=system.container_id(container),
                created_at=system.container_created_at(container),
                props=system.server_get(container, "/props"),
                slots=system.server_get(container, "/slots"),
            )
        else:
            fresh = runner.fresh_runtime_start(
                model_contract,
                container_id=system.container_id(container),
                created_at=system.container_created_at(container),
                models=system.server_get(container, "/v1/models"),
                metrics=system.server_get_text(container, "/metrics"),
            )
        fresh_evidence = write_evidence(
            run_dir, "fresh-runtime-start.json",
            json.dumps(fresh, sort_keys=True).encode("utf-8"), "runtime-environment",
        )
        control = system.runtime_control(container=container, run_id=run_id, condition=runtime_condition)
        control_evidence = write_runtime_control(run_dir, evaluation.canonical(control))
        boundary = system.inference_boundary_start(
            network, run_id, model_contract, inference_contract, admission, run_dir,
        )
        system.wait_inference_boundary(boundary)
        requests_before = system.server_request_count(container)
        started_at = clock()
        outcome = system.run_codex(
            network, model_contract, admission, request_payload,
            source_payload=source_payload, source_reference=source_ref,
            environment_payload=environment_payload, environment=environment,
            tool_policy_payload=tool_policy_payload, tool_policy=tool_policy,
            verifier_definition=definition, run_dir=run_dir, run_id=run_id,
            task=task_value, model_contract_payload=model_payload,
            inference_contract_payload=inference_payload,
            research_fixture_payload=research_fixture_payload, research_fixture_reference=research_fixture_ref,
        )
        finished_at = clock()
        requests_after = system.server_request_count(container)
        if (requests_before is None) != (requests_after is None):
            raise evaluation.OutcomeError("model server request counters changed availability mid-run")
        if requests_after is not None and requests_after < requests_before:
            raise evaluation.OutcomeError("model server request counters moved backwards")
        boundary_receipt = system.inference_boundary_receipt(
            boundary, inference_contract["request"]["requestFieldPolicySha256"],
        )
        model_requests = boundary_receipt["requests"]
        if requests_after is not None and requests_after - requests_before != model_requests:
            raise evaluation.OutcomeError("model server and exact inference boundary request counts disagree")
        requests_source = "inference-boundary-receipt"
        execution, interaction = runner.extract_codex_execution(
            outcome["transcript"], admission["budgets"],
            latency_ms=outcome["latencyMs"], exit_code=outcome["exitCode"],
            model_requests=model_requests, external_writes=0,
            real_backend=True, real_tools=True,
        )
        if (
            execution["inputTokens"] != boundary_receipt["inputTokens"]
            or execution["outputTokens"] != boundary_receipt["outputTokens"]
        ):
            raise evaluation.OutcomeError("codex transcript and exact inference boundary usage disagree")
        artifact_entries = []
        for index, item in enumerate(outcome.get("artifacts", [])):
            payload = item["payload"]
            target = run_dir / item["relativePath"]
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
            artifact_entries.append({
                "kind": item["kind"], "relativePath": item["relativePath"],
                "sha256": evaluation.sha256(payload), "bytes": len(payload),
            })
        if admission["profile"] == "researcher":
            import portal_outcome_research_evidence as research_evidence
            evidence, independent_evidence = research_evidence.validate_and_emit(
                run_dir=run_dir, outcome=outcome, artifacts=artifact_entries, arm="codex",
            )
        elif admission["profile"] == "controller":
            required = set(journey.get("requiredEvidence", []))
            descriptors = {
                "runtime-environment": fresh_evidence,
                "exact-source": write_evidence(
                    run_dir, "controller-source.json",
                    json.dumps({"sourceSnapshotSha256": admission["bindings"]["sourceSnapshotSha256"]}, sort_keys=True).encode("utf-8"),
                    "exact-source",
                ),
                "artifact-digest": write_evidence(
                    run_dir, "controller-artifacts.json",
                    json.dumps({"artifacts": len(artifact_entries)}, sort_keys=True).encode("utf-8"),
                    "artifact-digest",
                ),
                "checkpoint-lineage": write_evidence(
                    run_dir, "controller-checkpoint-lineage.json",
                    codex_checkpoint_lineage(outcome["transcript"], "controller"), "checkpoint-lineage",
                ),
                "privacy-route": write_evidence(
                    run_dir, "controller-privacy-route.json",
                    codex_privacy_route(admission=admission, environment=environment, tool_policy=tool_policy),
                    "privacy-route",
                ),
            }
            independent_verification = outcome.get("independentVerification")
            independent_evidence = None
            if independent_verification is not None:
                independent_evidence = write_evidence(
                    run_dir, "controller-independent-verification.json",
                    json.dumps(independent_verification, sort_keys=True, separators=(",", ":")).encode("utf-8"),
                    "independent-verifier",
                )
                descriptors["independent-verifier"] = independent_evidence
            evidence = [descriptors[kind] for kind in sorted(required) if kind in descriptors]
            if len(evidence) != len(required):
                raise evaluation.OutcomeError("Codex Controller did not produce every required evidence type")
        else:
            evidence = [
                write_evidence(
                    run_dir, "source.json",
                    json.dumps({"sourceSnapshotSha256": admission["bindings"]["sourceSnapshotSha256"]}, sort_keys=True).encode("utf-8"),
                    "exact-source",
                ),
                fresh_evidence,
                write_evidence(
                    run_dir, "command-exit.json",
                    json.dumps({
                        "exitCode": outcome["exitCode"],
                        "command": "codex exec (hardened local-provider profile)",
                        "transcriptSha256": evaluation.sha256(outcome["transcript"].encode("utf-8")),
                        "modelRequestsSource": requests_source,
                        "modelRequests": model_requests,
                        "inferenceBoundaryReceiptSha256": evaluation.sha256(evaluation.canonical(boundary_receipt)),
                        "inferencePolicySha256": boundary_receipt["inferencePolicySha256"],
                        "inputTokens": boundary_receipt["inputTokens"],
                        "outputTokens": boundary_receipt["outputTokens"],
                        "latencyMs": execution["latencyMs"],
                        "operatorInterventions": execution["operatorInterventions"],
                        "operatorAttentionRequests": execution["operatorAttentionRequests"],
                        "approvalRequests": execution["approvalRequests"],
                        "scopeExpansionRequests": execution["scopeExpansionRequests"],
                        "toolCalls": execution["toolCalls"],
                        "finalReplySha256": evaluation.sha256(outcome.get("finalMessage", "").encode("utf-8")),
                        "finalReplyBytes": len(outcome.get("finalMessage", "").encode("utf-8")),
                        "finalReplyNormalization": "exact-utf8",
                    }, sort_keys=True).encode("utf-8"),
                    "command-exit",
                ),
            ]
            independent_verification = outcome.get("independentVerification")
            independent_evidence = None
            if independent_verification is not None:
                independent_evidence = write_evidence(
                    run_dir, "independent-verification.json",
                    json.dumps(independent_verification, sort_keys=True, separators=(",", ":")).encode("utf-8"),
                    "independent-verifier",
                )
                evidence.append(independent_evidence)
            if admission["profile"] == "assistant":
                evidence.extend([
                    write_evidence(
                        run_dir, "assistant-checkpoint-lineage.json",
                        codex_checkpoint_lineage(outcome["transcript"]), "checkpoint-lineage",
                    ),
                    write_evidence(
                        run_dir, "assistant-privacy-route.json",
                        codex_privacy_route(admission=admission, environment=environment, tool_policy=tool_policy),
                        "privacy-route",
                    ),
                ])
            else:
                evidence.append(write_evidence(
                    run_dir, "artifacts.json",
                    json.dumps({"artifacts": len(artifact_entries)}, sort_keys=True).encode("utf-8"),
                    "artifact-digest",
                ))
        assertions = verifier_engine.run_checks(
            definition, journey=journey, admission=admission,
            run_dir=run_dir, evidence=evidence, artifacts=artifact_entries,
        )
        scored_dimensions = dimensions(evidence, artifact_entries)
        record = runner.assemble_run(
            admission=admission, backend="codex", run_id=run_id,
            started_at=started_at, finished_at=finished_at,
            execution=execution, interaction=interaction,
            execution_identity={
                "harnessContractSha256": harness_contract_sha256,
                "modelContractSha256": model_ref["sha256"],
                "inferenceContractSha256": inference_ref["sha256"],
                "toolPolicySha256": admission["bindings"]["toolPolicySha256"],
                "freshRuntimeStartSha256": fresh_evidence["sha256"],
                "runtimeCondition": runtime_condition,
                "runtimeControlSha256": control_evidence["sha256"],
                "interactionMode": "single-admission-noninteractive",
                "crossRunStateObserved": False,
            },
            evidence=evidence, assertions=assertions, dimensions=scored_dimensions,
            artifacts=artifact_entries, safety_findings=[],
            verifier={
                "independent": True, "kind": "deterministic-verifier", "backendOutputUsedAsScore": False,
                "evidencePath": (independent_evidence or evidence[0])["relativePath"],
                "evidenceSha256": (independent_evidence or evidence[0])["sha256"],
            },
            authority={
                "scopeExpansionDetected": False, "privateDataSentRemote": False,
                "unreconciledExternalWrite": False, "safetyBoundaryRelaxed": False,
            },
        )
        payload = json.dumps(record, indent=2, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"
        descriptor = os.open(run_dir / "run.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
        return record
    finally:
        system.teardown(network, container, boundary)
