#!/usr/bin/env python3
"""Attest one exact reusable DSV4 Pixel/Codex runtime without starting the model."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Callable

import portal_outcome_evaluation as evaluation
import portal_outcome_livesystem as codex_live
import portal_outcome_pair as pair_runner
import portal_outcome_pixel_livesystem as pixel_live
import portal_outcome_runner as outcome_runner
import portal_outcome_task as outcome_task
import portal_outcome_verifier as verifier_engine


PREFLIGHT_SCHEMA = pair_runner.PREFLIGHT_SCHEMA
PREFLIGHT_BOUNDARY = pair_runner.PREFLIGHT_BOUNDARY
PIXEL_REVIEW_BOUNDARY = (
    "Content-free read-only review of one exact Pixel DSV4 comparison runtime and admitted task. It proves the measured model, "
    "current qualification, launch, policy, runner, verifier, backend, inference, harness, tools, services, budgets, and task-compilation bindings after removing all temporary private "
    "review state, and grants no execution, model start, container, network, provider, credential, external-effect, "
    "completion, publication, deployment, acceptance, or promotion authority."
)
PIXEL_PLANNED_REVIEW_BOUNDARY = (
    "Content-free read-only structural compatibility review of one exact admitted task against one exact disabled, "
    "unqualified, or ready Pixel DSV4 policy template. It proves only task, model identity, inference, profile, runner, "
    "tools, services, budgets, verifier, input, and output-envelope compatibility; it does not assert qualification, "
    "launch readiness, or executable policy state, creates no plan or lease, starts no model, container, network, task, "
    "or tool, and grants no execution, credential, external effect, completion, publication, deployment, acceptance, or "
    "promotion authority."
)
REVIEW_AUTHORITY = pair_runner.PREFLIGHT_AUTHORITY
PLANNED_REVIEW_AUTHORITY = {
    **REVIEW_AUTHORITY,
    "grantsPublication": False, "grantsDeployment": False,
    "grantsAcceptance": False, "grantsPromotion": False,
}
CAPABILITY_RETENTION_BOUNDARY = (
    "Credential-free paired OMP execution-capability evidence only. Baseline and Pixel lanes run the same synthetic "
    "objective in disposable outer sandboxes; external checks, not worker claims or elapsed time, determine success. "
    "This evidence grants no provider, credential, network, external-effect, completion, deployment, or release authority."
)
REQUIRED_BUILDER_CAPABILITIES = {
    "workspace-read", "content-search", "file-discovery", "file-write", "targeted-edit", "command-execution",
    "debugging-repair", "language-intelligence", "local-evaluation", "bounded-subagent", "goal-coordination",
}


def _contracts(task_path: Path) -> tuple[dict[str, Any], bytes, dict[str, Any], bytes]:
    return pair_runner.load_shared_contracts(task_path)


def _exact_image(system: Any, digest: str, label: str) -> None:
    pair_runner._exact_image(system, digest, label)


def _validated_capability_retention(
    path: Path, *, runner_image_digest: str, source_archive_sha256: str,
) -> tuple[dict[str, Any], bytes]:
    value, raw = evaluation.read_json(path, "private Builder capability-retention evidence", private=True)
    value = evaluation.exact_fields(value, {
        "$schema", "schemaVersion", "operation", "status", "sourceCommit", "sourceTree", "sourceArchiveSha256", "startedAt", "finishedAt",
        "executor", "runner", "corpus", "baseline", "contained", "comparison", "privacy", "authority", "boundary",
    }, "Builder capability-retention evidence")
    executor = evaluation.exact_fields(value["executor"], {"id", "version", "artifactSha256"}, "capability executor")
    runner = evaluation.exact_fields(value["runner"], {"imageDigest", "builderContractSha256"}, "capability runner")
    corpus = evaluation.exact_fields(value["corpus"], {"id", "sha256", "tasks"}, "capability corpus")
    comparison = evaluation.exact_fields(value["comparison"], {
        "eligibleBaselinePasses", "retainedPasses", "retentionPermille", "thresholdPermille",
        "requiredCapabilitiesRetained", "sameTaskSet", "sameExecutor", "sameRunner",
        "oneJobAuthorization", "midJobPrompts",
    }, "capability comparison")
    privacy = evaluation.exact_fields(value["privacy"], {
        "providerCalls", "credentialInputs", "directNetworkRequests", "clientDataInputs", "productionDeploymentsTouched",
    }, "capability privacy")
    authority = evaluation.exact_fields(value["authority"], {
        "grantsExecution", "grantsCredentials", "grantsNetwork", "grantsExternalEffects", "grantsCompletion", "grantsRelease",
    }, "capability authority")
    evaluation.valid_hash(executor["artifactSha256"], "capability executor artifact")
    evaluation.valid_hash(value["sourceArchiveSha256"], "capability source archive")
    evaluation.valid_hash(runner["builderContractSha256"], "capability Builder contract")
    evaluation.valid_hash(corpus["sha256"], "capability corpus")
    lanes: dict[str, dict[str, Any]] = {}
    for lane_name in ("baseline", "contained"):
        lane = evaluation.exact_fields(value[lane_name], {
            "mode", "outerSandboxed", "network", "promptCount", "midJobPrompts", "externallyVerified",
            "taskSetSha256", "passed", "total", "results",
        }, f"capability {lane_name}")
        evaluation.valid_hash(lane["taskSetSha256"], f"capability {lane_name} task set")
        if not isinstance(lane["results"], list):
            raise evaluation.OutcomeError("Builder capability-retention results are invalid")
        ids: set[str] = set()
        for item in lane["results"]:
            item = evaluation.exact_fields(item, {"id", "status", "verification"}, f"capability {lane_name} result")
            if item["status"] != "pass" or item["verification"] != "external-fixture" or not isinstance(item["id"], str):
                raise evaluation.OutcomeError("Builder capability-retention result is not an external pass")
            ids.add(item["id"])
        if (
            ids != REQUIRED_BUILDER_CAPABILITIES or lane["outerSandboxed"] is not True
            or lane["network"] != "local-model-only" or lane["promptCount"] != 1 or lane["midJobPrompts"] != 0
            or lane["externallyVerified"] is not True or lane["passed"] != len(REQUIRED_BUILDER_CAPABILITIES)
            or lane["total"] != len(REQUIRED_BUILDER_CAPABILITIES) or len(lane["results"]) != len(ids)
        ):
            raise evaluation.OutcomeError("Builder capability-retention lane is incomplete or interactive")
        lanes[lane_name] = lane
    if (
        value["$schema"] != "./schemas/work-capability-retention-evidence-v1.schema.json"
        or value["schemaVersion"] != 1 or value["operation"] != "pixel-builder-capability-retention"
        or value["status"] != "pass" or value["boundary"] != CAPABILITY_RETENTION_BOUNDARY
        or value["sourceArchiveSha256"] != source_archive_sha256
        or executor["id"] != "omp" or not isinstance(executor["version"], str) or not executor["version"]
        or runner["imageDigest"] != runner_image_digest or not runner_image_digest.startswith("sha256:")
        or corpus["id"] != "pixel-builder-standard-v1" or corpus["tasks"] != len(REQUIRED_BUILDER_CAPABILITIES)
        or lanes["baseline"]["mode"] != "direct-omp" or lanes["contained"]["mode"] != "pixel-builder"
        or lanes["baseline"]["taskSetSha256"] != lanes["contained"]["taskSetSha256"]
        or comparison != {
            "eligibleBaselinePasses": len(REQUIRED_BUILDER_CAPABILITIES),
            "retainedPasses": len(REQUIRED_BUILDER_CAPABILITIES),
            "retentionPermille": 1000, "thresholdPermille": 900,
            "requiredCapabilitiesRetained": True, "sameTaskSet": True, "sameExecutor": True, "sameRunner": True,
            "oneJobAuthorization": True, "midJobPrompts": 0,
        }
        or any(value != 0 for value in privacy.values()) or any(value is not False for value in authority.values())
    ):
        raise evaluation.OutcomeError("Builder capability-retention evidence does not prove full contained capability")
    return value, raw


def _validated_pixel_review(
    value: dict[str, Any], *, run_id: str, profile: str,
    model_payload: bytes, inference_payload: bytes, model: dict[str, Any],
) -> dict[str, Any]:
    value = evaluation.exact_fields(value, {
        "schemaVersion", "operation", "runId", "profile", "status", "modelContractSha256", "inferenceContractSha256",
        "taskCompatibilitySha256", "workPolicySha256", "environmentSha256", "runtimeEnvironmentSha256", "harnessContractSha256", "runnerImageDigest", "verifierImageDigest",
        "backendImageDigest", "modelArtifactSha256", "launchBundleSha256", "qualificationReceiptSha256",
        "changes", "authority", "boundary",
    }, "Pixel system review")
    for field in (
        "modelContractSha256", "inferenceContractSha256", "taskCompatibilitySha256", "workPolicySha256", "environmentSha256", "runtimeEnvironmentSha256", "harnessContractSha256",
        "modelArtifactSha256", "launchBundleSha256", "qualificationReceiptSha256",
    ):
        evaluation.valid_hash(value[field], f"Pixel review {field}")
    if (
        value["schemaVersion"] != 1 or value["operation"] != "pixel-portal-outcome-system-review"
        or value["runId"] != run_id or value["status"] != "ready" or value["profile"] != profile
        or value["modelContractSha256"] != evaluation.sha256(model_payload)
        or value["inferenceContractSha256"] != evaluation.sha256(inference_payload)
        or value["modelArtifactSha256"] != model["artifact"]["sha256"]
        or value["backendImageDigest"] != model["runtime"]["imageDigest"]
        or value["changes"] != {
            "temporaryPrivateReviewStateRemoved": True, "modelStarted": False, "containerCreated": False,
            "networkCreated": False, "taskExecuted": False, "externalEffects": False,
        }
        or value["authority"] != REVIEW_AUTHORITY or value["boundary"] != PIXEL_REVIEW_BOUNDARY
    ):
        raise evaluation.OutcomeError("Pixel system review differs from the exact safe pair")
    return value


def review_materialized_pixel_task_compatibility(
    *, root: Path, task_path: Path, configuration_path: Path, temporary_parent: Path,
    pixel_system_factory: Callable[..., Any] = pixel_live.PixelDockerSystem,
) -> dict[str, Any]:
    """Compile one exact task through the qualified Pixel product path without starting its model."""
    root = root.resolve(strict=True)
    task_path = pair_runner._private_existing(
        pair_runner._absolute(str(task_path), "private compatibility task path"), "private compatibility task",
    )
    configuration_path = pair_runner._private_existing(
        pair_runner._absolute(str(configuration_path), "private compatibility configuration path"),
        "private compatibility configuration",
    )
    temporary_parent = pair_runner._private_existing(
        pair_runner._absolute(str(temporary_parent), "private compatibility temporary parent"),
        "private compatibility temporary parent", directory=True,
    )
    if temporary_parent == root or root in temporary_parent.parents:
        raise evaluation.OutcomeError("compatibility review state must remain outside the source repository")
    configuration, configuration_raw = pair_runner.load_configuration_record(configuration_path)
    task, task_raw = evaluation.read_json(task_path, "private compatibility task", private=True)
    admission = outcome_task.admit_task(root, task_path)
    if admission["comparisonLane"] != "same-model-harness":
        raise evaluation.OutcomeError("compatibility review requires the exact same-model harness lane")
    model, model_payload, inference, inference_payload = _contracts(task_path)
    resolved: dict[str, tuple[dict[str, Any], bytes]] = {}
    for key, label in (
        ("userRequest", "user request"), ("sourceSnapshot", "source snapshot"),
        ("environment", "environment"), ("toolPolicy", "tool policy"), ("verifier", "verifier"),
    ):
        resolved[key] = outcome_task.resolved_reference(task_path, task["bindings"][key], f"compatibility {label}")
    request_ref, request_payload = resolved["userRequest"]
    source_ref, source_payload = resolved["sourceSnapshot"]
    environment_ref, environment_payload = resolved["environment"]
    tool_ref, tool_payload = resolved["toolPolicy"]
    verifier_ref, verifier_payload = resolved["verifier"]
    expected_binding_hashes = {
        "userRequestSha256": request_ref["sha256"], "sourceSnapshotSha256": source_ref["sha256"],
        "environmentSha256": environment_ref["sha256"], "toolPolicySha256": tool_ref["sha256"],
        "verifierSha256": verifier_ref["sha256"],
    }
    if any(admission["bindings"][field] != digest for field, digest in expected_binding_hashes.items()):
        raise evaluation.OutcomeError("compatibility task bindings drifted after admission")
    source_ref = dict(source_ref)
    source_ref["bytes"] = len(source_payload)
    tool_policy = outcome_task.validate_tool_policy(tool_payload, admission["capabilities"])
    environment = outcome_task.validate_environment(environment_payload, tool_policy=tool_policy)
    verifier_definition = verifier_engine.load_definition(verifier_payload)
    pixel_system = pixel_system_factory(root=root, configuration_path=Path(configuration["pixelSystemConfigPath"]))
    suffix = evaluation.sha256(task_raw + configuration_raw)[:12]
    run_id = f"outcomerun-0000000000000-{suffix}"
    with tempfile.TemporaryDirectory(prefix="pixel-task-compatibility-", dir=temporary_parent) as temporary:
        review_root = Path(temporary).resolve()
        if os.name != "nt":
            review_root.chmod(0o700)
        review = pixel_system.review_runtime(
            run_id=run_id, admission=admission, model_contract_payload=model_payload,
            inference_contract_payload=inference_payload, model_contract_sha256=evaluation.sha256(model_payload),
            inference_contract_sha256=evaluation.sha256(inference_payload), run_dir=review_root,
            task=task, request_payload=request_payload, source_reference=source_ref,
            environment=environment, tool_policy=tool_policy, verifier_definition=verifier_definition,
        )
    review = _validated_pixel_review(
        review, run_id=run_id, profile=admission["profile"], model_payload=model_payload,
        inference_payload=inference_payload, model=model,
    )
    _current_task, current_task_raw = evaluation.read_json(task_path, "private compatibility task", private=True)
    _current_configuration, current_configuration_raw = pair_runner.load_configuration_record(configuration_path)
    current_admission = outcome_task.admit_task(root, task_path)
    if (
        current_task_raw != task_raw or current_configuration_raw != configuration_raw
        or evaluation.canonical(current_admission) != evaluation.canonical(admission)
    ):
        raise evaluation.OutcomeError("compatibility task or configuration changed during read-only review")
    return review


def review_materialized_pixel_task_planned_compatibility(
    *, root: Path, task_path: Path, configuration_path: Path, temporary_parent: Path,
    pixel_system_factory: Callable[..., Any] = pixel_live.PixelDockerSystem,
) -> dict[str, Any]:
    """Prove task/policy envelope compatibility without claiming qualification or readiness."""
    root = root.resolve(strict=True)
    task_path = pair_runner._private_existing(
        pair_runner._absolute(str(task_path), "private planned-compatibility task path"),
        "private planned-compatibility task",
    )
    configuration_path = pair_runner._private_existing(
        pair_runner._absolute(str(configuration_path), "private planned-compatibility configuration path"),
        "private planned-compatibility configuration",
    )
    temporary_parent = pair_runner._private_existing(
        pair_runner._absolute(str(temporary_parent), "private planned-compatibility temporary parent"),
        "private planned-compatibility temporary parent", directory=True,
    )
    if temporary_parent == root or root in temporary_parent.parents:
        raise evaluation.OutcomeError("planned-compatibility review state must remain outside the source repository")
    configuration, configuration_raw = pair_runner.load_configuration_record(configuration_path)
    task, task_raw = evaluation.read_json(task_path, "private planned-compatibility task", private=True)
    admission = outcome_task.admit_task(root, task_path)
    if admission["comparisonLane"] != "same-model-harness":
        raise evaluation.OutcomeError("planned-compatibility review requires the exact same-model harness lane")
    model, model_payload, inference, inference_payload = _contracts(task_path)
    resolved: dict[str, tuple[dict[str, Any], bytes]] = {}
    for key, label in (
        ("userRequest", "user request"), ("sourceSnapshot", "source snapshot"),
        ("environment", "environment"), ("toolPolicy", "tool policy"), ("verifier", "verifier"),
    ):
        resolved[key] = outcome_task.resolved_reference(task_path, task["bindings"][key], f"planned compatibility {label}")
    request_ref, request_payload = resolved["userRequest"]
    source_ref, source_payload = resolved["sourceSnapshot"]
    environment_ref, environment_payload = resolved["environment"]
    tool_ref, tool_payload = resolved["toolPolicy"]
    verifier_ref, verifier_payload = resolved["verifier"]
    expected_binding_hashes = {
        "userRequestSha256": request_ref["sha256"], "sourceSnapshotSha256": source_ref["sha256"],
        "environmentSha256": environment_ref["sha256"], "toolPolicySha256": tool_ref["sha256"],
        "verifierSha256": verifier_ref["sha256"],
    }
    if any(admission["bindings"][field] != digest for field, digest in expected_binding_hashes.items()):
        raise evaluation.OutcomeError("planned-compatibility task bindings drifted after admission")
    source_ref = dict(source_ref)
    source_ref["bytes"] = len(source_payload)
    tool_policy = outcome_task.validate_tool_policy(tool_payload, admission["capabilities"])
    environment = outcome_task.validate_environment(environment_payload, tool_policy=tool_policy)
    verifier_definition = verifier_engine.load_definition(verifier_payload)
    pixel_system = pixel_system_factory(root=root, configuration_path=Path(configuration["pixelSystemConfigPath"]))
    suffix = evaluation.sha256(task_raw + configuration_raw)[:12]
    run_id = f"outcomerun-0000000000000-{suffix}"
    with tempfile.TemporaryDirectory(prefix="pixel-task-planned-compatibility-", dir=temporary_parent) as temporary:
        review_root = Path(temporary).resolve()
        if os.name != "nt":
            review_root.chmod(0o700)
        review = pixel_system.review_planned_runtime(
            run_id=run_id, admission=admission, model_contract_payload=model_payload,
            inference_contract_payload=inference_payload, model_contract_sha256=evaluation.sha256(model_payload),
            inference_contract_sha256=evaluation.sha256(inference_payload), run_dir=review_root,
            task=task, request_payload=request_payload, source_reference=source_ref,
            environment=environment, tool_policy=tool_policy, verifier_definition=verifier_definition,
        )
    review = evaluation.exact_fields(review, {
        "schemaVersion", "operation", "runId", "profile", "status", "readiness", "modelContractSha256",
        "inferenceContractSha256", "taskCompatibilitySha256", "harnessContractSha256", "workPolicySha256", "environmentSha256",
        "backendConfigSha256", "runnerImageDigest", "backendImageDigest", "changes", "authority", "boundary",
    }, "Pixel planned system review")
    for field in (
        "modelContractSha256", "inferenceContractSha256", "taskCompatibilitySha256", "harnessContractSha256", "workPolicySha256",
        "environmentSha256", "backendConfigSha256",
    ):
        evaluation.valid_hash(review[field], f"Pixel planned review {field}")
    if (
        review["schemaVersion"] != 1 or review["operation"] != "pixel-portal-outcome-planned-system-review"
        or review["runId"] != run_id or review["profile"] != admission["profile"]
        or review["status"] != "structurally-compatible"
        or review["readiness"] not in {"qualification-required", "policy-ready"}
        or review["modelContractSha256"] != evaluation.sha256(model_payload)
        or review["inferenceContractSha256"] != evaluation.sha256(inference_payload)
        or review["backendImageDigest"] != model["runtime"]["imageDigest"]
        or review["changes"] != {
            "policyMutated": False, "qualificationFabricated": False, "modelStarted": False,
            "containerCreated": False, "networkCreated": False, "taskExecuted": False,
            "externalEffects": False,
        }
        or review["authority"] != PLANNED_REVIEW_AUTHORITY or review["boundary"] != PIXEL_PLANNED_REVIEW_BOUNDARY
    ):
        raise evaluation.OutcomeError("Pixel planned system review differs from the exact non-authorizing compatibility contract")
    _current_task, current_task_raw = evaluation.read_json(task_path, "private planned-compatibility task", private=True)
    _current_configuration, current_configuration_raw = pair_runner.load_configuration_record(configuration_path)
    current_admission = outcome_task.admit_task(root, task_path)
    if (
        current_task_raw != task_raw or current_configuration_raw != configuration_raw
        or evaluation.canonical(current_admission) != evaluation.canonical(admission)
    ):
        raise evaluation.OutcomeError("planned-compatibility task or configuration changed during read-only review")
    return review


def review_pair(
    *, root: Path, task_path: Path, configuration_path: Path, output_path: Path,
    pixel_system_factory: Callable[..., Any] = pixel_live.PixelDockerSystem,
    codex_system_factory: Callable[..., Any] = codex_live.DockerSystem,
) -> dict[str, Any]:
    root = root.resolve(strict=True)
    task_path = pair_runner._private_existing(pair_runner._absolute(str(task_path), "private paired task path"), "private paired task")
    configuration_path = pair_runner._private_existing(
        pair_runner._absolute(str(configuration_path), "private pair system configuration path"),
        "private pair system configuration",
    )
    if output_path == root or root in output_path.parents:
        raise evaluation.OutcomeError("private pair preflight output must remain outside the source repository")
    evaluation.private_parent(output_path)
    configuration, configuration_raw = pair_runner.load_configuration_record(configuration_path)
    configured_preflight = pair_runner._absolute(configuration["preflightPath"], "configured pair preflight path")
    if output_path != configured_preflight:
        raise evaluation.OutcomeError("pair preflight output must equal the path bound by the pair configuration")
    task, task_raw = evaluation.read_json(task_path, "private paired task", private=True)
    admission = outcome_task.admit_task(root, task_path)
    if admission["comparisonLane"] != "same-model-harness":
        raise evaluation.OutcomeError("pair preflight requires the exact same-model harness lane")
    model, model_payload, inference, inference_payload = _contracts(task_path)
    resolved: dict[str, tuple[dict[str, Any], bytes]] = {}
    for key, label in (
        ("userRequest", "user request"), ("sourceSnapshot", "source snapshot"),
        ("environment", "environment"), ("toolPolicy", "tool policy"), ("verifier", "verifier"),
    ):
        resolved[key] = outcome_task.resolved_reference(task_path, task["bindings"][key], f"paired preflight {label}")
    request_ref, request_payload = resolved["userRequest"]
    source_ref, source_payload = resolved["sourceSnapshot"]
    environment_ref, environment_payload = resolved["environment"]
    tool_ref, tool_payload = resolved["toolPolicy"]
    verifier_ref, verifier_payload = resolved["verifier"]
    expected_binding_hashes = {
        "userRequestSha256": request_ref["sha256"], "sourceSnapshotSha256": source_ref["sha256"],
        "environmentSha256": environment_ref["sha256"], "toolPolicySha256": tool_ref["sha256"],
        "verifierSha256": verifier_ref["sha256"],
    }
    if any(admission["bindings"][field] != digest for field, digest in expected_binding_hashes.items()):
        raise evaluation.OutcomeError("paired preflight task bindings drifted after admission")
    source_ref = dict(source_ref)
    source_ref["bytes"] = len(source_payload)
    tool_policy = outcome_task.validate_tool_policy(tool_payload, admission["capabilities"])
    environment = outcome_task.validate_environment(environment_payload, tool_policy=tool_policy)
    verifier_definition = verifier_engine.load_definition(verifier_payload)
    launch_arguments = pair_runner.load_launch_arguments(Path(configuration["launchArgumentsPath"]))
    manifest, manifest_raw = evaluation.read_json(
        Path(configuration["modelArtifactManifestPath"]), "private model artifact manifest", private=True,
    )
    _pixel_config, pixel_config_raw = evaluation.read_json(
        Path(configuration["pixelSystemConfigPath"]), "private Pixel system configuration", private=True,
    )
    _launch_record, launch_raw = evaluation.read_json(
        Path(configuration["launchArgumentsPath"]), "private DSV4 launch arguments", private=True,
    )
    outcome_runner.verify_model_artifact_manifest(Path(configuration["modelArtifactPath"]), manifest, model)
    outcome_runner.verify_launch_arguments(launch_arguments, model)

    codex_system = codex_system_factory(
        root=root, codex_image=configuration["codexRunnerImage"], boundary_image=configuration["codexBoundaryImage"],
        pixel_system_config_path=Path(configuration["pixelSystemConfigPath"]),
    )
    outcome_runner.verify_runtime_identity(codex_system, model)
    codex_harness_sha256 = codex_system.codex_harness_contract_sha256()
    evaluation.valid_hash(codex_harness_sha256, "Codex harness contract digest")
    codex_surface, codex_surface_raw = evaluation.read_json(
        Path(configuration["codexSurfaceQualificationPath"]), "private Codex surface qualification", private=True,
    )
    pair_runner.validate_codex_surface(
        codex_surface, codex_image=configuration["codexRunnerImage"],
        toolchain_sha256=codex_system.codex_comparison_toolchain_sha256(),
    )

    pixel_system = pixel_system_factory(root=root, configuration_path=Path(configuration["pixelSystemConfigPath"]))
    suffix = evaluation.sha256(task_raw + configuration_raw)[:12]
    run_id = f"outcomerun-0000000000000-{suffix}"
    with tempfile.TemporaryDirectory(prefix="pixel-pair-preflight-", dir=output_path.parent) as temporary:
        review_root = Path(temporary).resolve()
        if os.name != "nt":
            review_root.chmod(0o700)
        pixel_review = pixel_system.review_runtime(
            run_id=run_id, admission=admission, model_contract_payload=model_payload, inference_contract_payload=inference_payload,
            model_contract_sha256=evaluation.sha256(model_payload),
            inference_contract_sha256=evaluation.sha256(inference_payload), run_dir=review_root,
            task=task, request_payload=request_payload, source_reference=source_ref,
            environment=environment, tool_policy=tool_policy, verifier_definition=verifier_definition,
        )
    pixel_review = _validated_pixel_review(
        pixel_review, run_id=run_id, profile=admission["profile"], model_payload=model_payload,
        inference_payload=inference_payload, model=model,
    )
    _capability_evidence, capability_raw = _validated_capability_retention(
        Path(configuration["capabilityRetentionEvidencePath"]), runner_image_digest=pixel_review["runnerImageDigest"],
        source_archive_sha256=configuration["candidateSourceArchiveSha256"],
    )

    images = {
        "codexRunner": configuration["codexRunnerImage"],
        "inferenceBoundary": configuration["codexBoundaryImage"],
        "pixelRunner": pixel_review["runnerImageDigest"],
        "pixelVerifier": pixel_review["verifierImageDigest"],
        "modelBackend": pixel_review["backendImageDigest"],
    }
    for label, digest in images.items():
        _exact_image(codex_system, digest, label)
    _current_task, current_task_raw = evaluation.read_json(task_path, "private paired task", private=True)
    _current_configuration, current_configuration_raw = pair_runner.load_configuration_record(configuration_path)
    _current_manifest, current_manifest_raw = evaluation.read_json(
        Path(configuration["modelArtifactManifestPath"]), "private model artifact manifest", private=True,
    )
    _current_pixel, current_pixel_raw = evaluation.read_json(
        Path(configuration["pixelSystemConfigPath"]), "private Pixel system configuration", private=True,
    )
    _current_launch, current_launch_raw = evaluation.read_json(
        Path(configuration["launchArgumentsPath"]), "private DSV4 launch arguments", private=True,
    )
    _current_capability, current_capability_raw = evaluation.read_json(
        Path(configuration["capabilityRetentionEvidencePath"]), "private Builder capability-retention evidence", private=True,
    )
    _current_surface, current_surface_raw = evaluation.read_json(
        Path(configuration["codexSurfaceQualificationPath"]), "private Codex surface qualification", private=True,
    )
    current_admission = outcome_task.admit_task(root, task_path)
    if (
        current_task_raw != task_raw or current_configuration_raw != configuration_raw
        or current_manifest_raw != manifest_raw or current_pixel_raw != pixel_config_raw
        or current_launch_raw != launch_raw or current_capability_raw != capability_raw
        or current_surface_raw != codex_surface_raw
        or evaluation.canonical(current_admission) != evaluation.canonical(admission)
    ):
        raise evaluation.OutcomeError("pair preflight inputs changed during read-only review")
    result = {
        "$schema": PREFLIGHT_SCHEMA, "schemaVersion": 1,
        "operation": "pixel-portal-outcome-pair-preflight", "status": "ready",
        "profile": admission["profile"],
        "contractSourceTaskSha256": evaluation.sha256(task_raw),
        "contractSourceTaskAdmissionSha256": evaluation.sha256(evaluation.canonical(admission)),
        "configurationSha256": evaluation.sha256(configuration_raw),
        "pixelSystemConfigurationSha256": evaluation.sha256(pixel_config_raw),
        "modelArtifactManifestFileSha256": evaluation.sha256(manifest_raw),
        "launchArgumentsFileSha256": evaluation.sha256(launch_raw),
        "modelContractSha256": evaluation.sha256(model_payload),
        "inferenceContractSha256": evaluation.sha256(inference_payload),
        "modelArtifactSha256": model["artifact"]["sha256"],
        "launchArgumentsSha256": outcome_runner.canonical_arguments_sha256(launch_arguments),
        "capabilityRetentionEvidenceSha256": evaluation.sha256(capability_raw),
        "codexSurfaceQualificationSha256": evaluation.sha256(codex_surface_raw),
        "pixelWorkPolicySha256": pixel_review["workPolicySha256"],
        "pixelTaskCompatibilitySha256": pixel_review["taskCompatibilitySha256"],
        "pixelEnvironmentSha256": pixel_review["environmentSha256"],
        "pixelHarnessSha256": pixel_review["harnessContractSha256"],
        "pixelLaunchBundleSha256": pixel_review["launchBundleSha256"],
        "pixelModelQualificationReceiptSha256": pixel_review["qualificationReceiptSha256"],
        "codexHarnessSha256": codex_harness_sha256, "images": images,
        "checks": dict(pair_runner.PREFLIGHT_CHECKS),
        "authority": dict(REVIEW_AUTHORITY),
        "boundary": PREFLIGHT_BOUNDARY,
    }
    evaluation.write_new_private(
        output_path, json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = review_pair(
            root=args.root, task_path=Path(os.path.abspath(args.task)),
            configuration_path=Path(os.path.abspath(args.config)), output_path=Path(os.path.abspath(args.output)),
        )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except (evaluation.OutcomeError, OSError, UnicodeError) as exc:
        print(f"[pixel] ERROR: {exc}", file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
