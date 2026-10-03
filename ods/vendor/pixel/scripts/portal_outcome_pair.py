#!/usr/bin/env python3
"""Execute one exact DSV4 task through full Pixel and the pinned Codex harness."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import time
from typing import Any, Callable

import portal_outcome_evaluation as evaluation
import portal_outcome_livesystem as codex_live
import portal_outcome_orchestrate as codex_orchestrate
import portal_outcome_pixel_livesystem as pixel_live
import portal_outcome_pixel_orchestrate as pixel_orchestrate
import portal_outcome_runner as outcome_runner
import portal_outcome_runtime_control as runtime_control
import portal_outcome_task as outcome_task


CONFIG_SCHEMA = "https://osmantic.com/pixel/schemas/portal-outcome-pair-system-v1.schema.json"
PREFLIGHT_SCHEMA = "https://osmantic.com/pixel/schemas/portal-outcome-pair-preflight-v1.schema.json"
CONFIG_BOUNDARY = (
    "Owner-private exact Pixel/Codex pair execution configuration only. It grants no provider, credential, "
    "external-effect, merge, deployment, publication, completion, acceptance, or promotion authority."
)
LAUNCH_BOUNDARY = (
    "Owner-private exact DSV4 server argument vector only. It grants no model start, container, device, network, "
    "provider, credential, external-effect, completion, publication, deployment, acceptance, or promotion authority."
)
PAIR_BOUNDARY = (
    "Content-free exact paired-run index only. Private task, run, artifact, and verifier content remain in owner "
    "custody. This record grants no capability, effect, acceptance, publication, deployment, or promotion authority."
)
PREFLIGHT_BOUNDARY = (
    "Content-free read-only readiness evidence for one exact private DSV4 Pixel/Codex comparison runtime. It "
    "proves model, current qualification, artifact, launch, inference, image, policy, verifier, configuration, harness, contained Builder capability retention, and exact task-compilation "
    "bindings for same-model tasks carrying the admitted contracts, without starting the model or running a task, and grants "
    "no execution, container, network, provider, credential, external-effect, completion, publication, deployment, "
    "acceptance, or promotion authority."
)
PREFLIGHT_AUTHORITY = {
    "grantsModelStart": False, "grantsExecution": False, "grantsNetwork": False,
    "grantsProviderCall": False, "grantsCredentialUse": False, "grantsExternalEffects": False,
    "grantsCompletion": False,
}
PREFLIGHT_CHECKS = {
    "sameModelLane": True, "modelArtifactMeasured": True, "launchArgumentsBound": True,
    "runtimeExecutableBound": True, "codexSurfaceQualified": True, "pixelTemplatesPrepared": True, "modelQualificationCurrent": True,
    "pixelTaskCompiles": True, "containedBuilderCapabilitiesRetained": True, "allImagesPresent": True,
    "temporaryReviewStateRemoved": True, "modelStarted": False, "taskExecuted": False,
}
DIGEST_IMAGE_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
CODEX_SURFACE_BOUNDARY = (
    "Content-free credential-free black-box evidence of the exact Codex comparison tool catalog only. "
    "It grants no model, provider, credential, external-effect, completion, acceptance, or promotion authority."
)
DIMENSIONS = (
    "outcome-completeness", "correctness", "artifact-quality", "recovery",
    "operator-effort", "latency", "resource-use",
)


def _absolute(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value or "\r" in value or "\n" in value:
        raise evaluation.OutcomeError(f"{label} is invalid")
    path = Path(value)
    if not path.is_absolute() or path != Path(os.path.abspath(path)):
        raise evaluation.OutcomeError(f"{label} is not an absolute canonical path")
    return path


def _private_existing(path: Path, label: str, *, directory: bool = False, private: bool = True) -> Path:
    try:
        info = path.lstat()
        actual = path.resolve(strict=True)
    except OSError as exc:
        raise evaluation.OutcomeError(f"{label} is unavailable") from exc
    expected = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink == 1
    if actual != path or stat.S_ISLNK(info.st_mode) or not expected:
        raise evaluation.OutcomeError(f"{label} is linked or has the wrong type")
    if private and os.name != "nt" and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077):
        raise evaluation.OutcomeError(f"{label} is not owner-private")
    return path


def load_configuration_record(path: Path) -> tuple[dict[str, Any], bytes]:
    value, raw = evaluation.read_json(path, "private pair system configuration", private=True)
    value = evaluation.exact_fields(value, {
        "$schema", "schemaVersion", "candidateSourceArchiveSha256", "pixelSystemConfigPath", "codexRunnerImage", "codexBoundaryImage",
        "codexSurfaceQualificationPath", "modelArtifactPath", "modelArtifactManifestPath", "launchArgumentsPath", "capabilityRetentionEvidencePath",
        "preflightPath", "boundary",
    }, "private pair system configuration")
    if value["$schema"] != CONFIG_SCHEMA or value["schemaVersion"] != 1 or value["boundary"] != CONFIG_BOUNDARY:
        raise evaluation.OutcomeError("pair system configuration contract is invalid")
    evaluation.valid_hash(value["candidateSourceArchiveSha256"], "pair candidate source archive")
    if DIGEST_IMAGE_RE.fullmatch(value["codexRunnerImage"] or "") is None or DIGEST_IMAGE_RE.fullmatch(value["codexBoundaryImage"] or "") is None:
        raise evaluation.OutcomeError("Codex runner or inference-boundary image is not immutable")
    for field in ("pixelSystemConfigPath", "codexSurfaceQualificationPath", "modelArtifactManifestPath", "launchArgumentsPath", "capabilityRetentionEvidencePath"):
        _private_existing(_absolute(value[field], f"pair system {field}"), f"pair system {field}")
    preflight_path = _absolute(value["preflightPath"], "pair system preflightPath")
    evaluation.private_parent(preflight_path)
    _private_existing(
        _absolute(value["modelArtifactPath"], "pair system model artifact"),
        "pair system model artifact", directory=True, private=False,
    )
    return value, raw


def load_configuration(path: Path) -> dict[str, Any]:
    return load_configuration_record(path)[0]


def validate_codex_surface(value: dict[str, Any], *, codex_image: str, toolchain_sha256: str) -> dict[str, Any]:
    value = evaluation.exact_fields(value, {
        "schemaVersion", "operation", "modelId", "functionTools", "customTool", "customFormat", "namespace",
        "namespaceTools", "toolCount", "contentStored", "credentialUsed", "providerCalled", "externalEffects",
        "codexRunnerImageId", "codexComparisonToolchainSha256", "fixtureSha256", "workspaceBytes",
        "workspaceTreeSha256", "boundary",
    }, "Codex comparison surface qualification")
    for field in ("codexComparisonToolchainSha256", "fixtureSha256", "workspaceTreeSha256"):
        evaluation.valid_hash(value[field], f"Codex surface {field}")
    evaluation.valid_hash(toolchain_sha256, "current Codex comparison toolchain")
    if (
        value["schemaVersion"] != 1 or value["operation"] != "pixel-codex-comparison-surface-qualified"
        or value["modelId"] != "DeepSeek-V4-Flash-0731"
        or value["functionTools"] != ["exec_command", "request_user_input", "update_plan", "view_image", "write_stdin"]
        or value["customTool"] != "apply_patch" or value["customFormat"] != "grammar:lark"
        or value["namespace"] != "multi_agent_v1"
        or value["namespaceTools"] != ["close_agent", "resume_agent", "send_input", "spawn_agent", "wait_agent"]
        or value["toolCount"] != 7 or value["contentStored"] is not False or value["credentialUsed"] is not False
        or value["providerCalled"] is not False or value["externalEffects"] is not False
        or value["codexRunnerImageId"] != codex_image or DIGEST_IMAGE_RE.fullmatch(codex_image or "") is None
        or value["codexComparisonToolchainSha256"] != toolchain_sha256
        or value["workspaceBytes"] != 536870912 or value["boundary"] != CODEX_SURFACE_BOUNDARY
    ):
        raise evaluation.OutcomeError("Codex comparison surface qualification differs from the exact broad tool contract")
    return value


def load_shared_contracts(task_path: Path) -> tuple[dict[str, Any], bytes, dict[str, Any], bytes]:
    task, _raw = evaluation.read_json(task_path, "private paired task", private=True)
    if not isinstance(task, dict) or not isinstance(task.get("bindings"), dict):
        raise evaluation.OutcomeError("paired task bindings are invalid")
    model_ref, model_payload = outcome_task.resolved_reference(
        task_path, task["bindings"].get("sharedModelContract"), "paired shared model contract",
    )
    inference_ref, inference_payload = outcome_task.resolved_reference(
        task_path, task["bindings"].get("sharedInferenceContract"), "paired shared inference contract",
    )
    outcome_task.validate_model_contract(model_payload)
    outcome_task.validate_inference_contract(inference_payload)
    model = evaluation.parse_json(model_payload, "paired shared model contract")
    inference = evaluation.parse_json(inference_payload, "paired shared inference contract")
    if model.get("modelId") != "DeepSeek-V4-Flash-0731":
        raise evaluation.OutcomeError("paired execution requires DSV4 Flash 0731")
    if model_ref["sha256"] != evaluation.sha256(model_payload) or inference_ref["sha256"] != evaluation.sha256(inference_payload):
        raise evaluation.OutcomeError("paired contract reference drifted after resolution")
    return model, model_payload, inference, inference_payload


def load_preflight(path: Path) -> tuple[dict[str, Any], bytes]:
    value, raw = evaluation.read_json(path, "private pair preflight", private=True)
    value = evaluation.exact_fields(value, {
        "$schema", "schemaVersion", "operation", "status", "profile", "contractSourceTaskSha256",
        "contractSourceTaskAdmissionSha256", "configurationSha256", "pixelSystemConfigurationSha256",
        "modelArtifactManifestFileSha256", "launchArgumentsFileSha256", "modelContractSha256",
        "inferenceContractSha256", "modelArtifactSha256", "launchArgumentsSha256",
        "capabilityRetentionEvidenceSha256", "codexSurfaceQualificationSha256",
        "pixelWorkPolicySha256", "pixelTaskCompatibilitySha256", "pixelEnvironmentSha256", "pixelHarnessSha256", "pixelLaunchBundleSha256",
        "pixelModelQualificationReceiptSha256", "codexHarnessSha256",
        "images", "checks", "authority", "boundary",
    }, "private pair preflight")
    hashes = (
        "contractSourceTaskSha256", "contractSourceTaskAdmissionSha256", "configurationSha256",
        "pixelSystemConfigurationSha256", "modelArtifactManifestFileSha256", "launchArgumentsFileSha256",
        "modelContractSha256", "inferenceContractSha256", "modelArtifactSha256", "launchArgumentsSha256",
        "capabilityRetentionEvidenceSha256", "codexSurfaceQualificationSha256",
        "pixelWorkPolicySha256", "pixelTaskCompatibilitySha256", "pixelEnvironmentSha256", "pixelHarnessSha256", "pixelLaunchBundleSha256",
        "pixelModelQualificationReceiptSha256", "codexHarnessSha256",
    )
    for field in hashes:
        evaluation.valid_hash(value[field], f"pair preflight {field}")
    images = evaluation.exact_fields(value["images"], {
        "codexRunner", "inferenceBoundary", "pixelRunner", "pixelVerifier", "modelBackend",
    }, "pair preflight images")
    if any(DIGEST_IMAGE_RE.fullmatch(images[field] or "") is None for field in images):
        raise evaluation.OutcomeError("pair preflight image identity is invalid")
    checks = evaluation.exact_fields(value["checks"], set(PREFLIGHT_CHECKS), "pair preflight checks")
    authority = evaluation.exact_fields(value["authority"], set(PREFLIGHT_AUTHORITY), "pair preflight authority")
    if (
        value["$schema"] != PREFLIGHT_SCHEMA or value["schemaVersion"] != 1
        or value["operation"] != "pixel-portal-outcome-pair-preflight" or value["status"] != "ready"
        or value["profile"] not in {"assistant", "builder", "controller", "researcher"}
        or checks != PREFLIGHT_CHECKS or authority != PREFLIGHT_AUTHORITY or value["boundary"] != PREFLIGHT_BOUNDARY
    ):
        raise evaluation.OutcomeError("pair preflight contract is invalid")
    return value, raw


def _exact_image(system: Any, digest: str, label: str) -> None:
    if DIGEST_IMAGE_RE.fullmatch(digest or "") is None or system.image_id(digest) != digest:
        raise evaluation.OutcomeError(f"{label} does not resolve to its exact image identity")


class _PreflightBoundPixelSystem:
    """Stop a reviewed Pixel runtime before task execution if any attested identity drifted."""

    def __init__(self, system: Any, expected: dict[str, str]):
        self._system = system
        self._expected = expected

    def __getattr__(self, name: str) -> Any:
        return getattr(self._system, name)

    def qualify_runtime(self, **options: Any) -> dict[str, Any]:
        runtime = self._system.qualify_runtime(**options)
        if not isinstance(runtime, dict) or any(runtime.get(field) != value for field, value in self._expected.items()):
            raise evaluation.OutcomeError("Pixel runtime differs from its exact read-only preflight")
        return runtime


def _assert_pair_inputs_stable(
    *, root: Path, task_path: Path, task_raw: bytes, admission_sha256: str,
    configuration_path: Path, configuration_raw: bytes, preflight_path: Path, preflight_raw: bytes,
) -> None:
    _task, current_task_raw = evaluation.read_json(task_path, "private paired task", private=True)
    _configuration, current_configuration_raw = load_configuration_record(configuration_path)
    _preflight, current_preflight_raw = load_preflight(preflight_path)
    _capability, current_capability_raw = evaluation.read_json(
        Path(_configuration["capabilityRetentionEvidencePath"]), "private Builder capability-retention evidence", private=True,
    )
    _surface, current_surface_raw = evaluation.read_json(
        Path(_configuration["codexSurfaceQualificationPath"]), "private Codex surface qualification", private=True,
    )
    current_admission = outcome_task.admit_task(root, task_path)
    if (
        current_task_raw != task_raw or current_configuration_raw != configuration_raw
        or current_preflight_raw != preflight_raw
        or evaluation.sha256(current_capability_raw) != _preflight["capabilityRetentionEvidenceSha256"]
        or evaluation.sha256(current_surface_raw) != _preflight["codexSurfaceQualificationSha256"]
        or evaluation.sha256(evaluation.canonical(current_admission)) != admission_sha256
    ):
        raise evaluation.OutcomeError("paired task, admission, configuration, or preflight changed during execution")


def validate_preflight(
    *, root: Path, task_path: Path, configuration: dict[str, Any],
    configuration_raw: bytes, admission: dict[str, Any], codex_system: Any,
) -> tuple[dict[str, Any], bytes, dict[str, Any], bytes, dict[str, Any], bytes, list[str], dict[str, str]]:
    preflight_path = _private_existing(
        _absolute(configuration["preflightPath"], "pair preflight path"), "private pair preflight",
    )
    if preflight_path == root or root in preflight_path.parents:
        raise evaluation.OutcomeError("private pair preflight must remain outside the source repository")
    preflight, preflight_raw = load_preflight(preflight_path)
    model, model_payload, inference, inference_payload = load_shared_contracts(task_path)
    launch_arguments = load_launch_arguments(Path(configuration["launchArgumentsPath"]))
    _pixel_config, pixel_config_raw = evaluation.read_json(
        Path(configuration["pixelSystemConfigPath"]), "private Pixel system configuration", private=True,
    )
    _manifest, manifest_raw = evaluation.read_json(
        Path(configuration["modelArtifactManifestPath"]), "private model artifact manifest", private=True,
    )
    _launch, launch_raw = evaluation.read_json(
        Path(configuration["launchArgumentsPath"]), "private DSV4 launch arguments", private=True,
    )
    _capability, capability_raw = evaluation.read_json(
        Path(configuration["capabilityRetentionEvidencePath"]), "private Builder capability-retention evidence", private=True,
    )
    surface, surface_raw = evaluation.read_json(
        Path(configuration["codexSurfaceQualificationPath"]), "private Codex surface qualification", private=True,
    )
    validate_codex_surface(
        surface, codex_image=configuration["codexRunnerImage"],
        toolchain_sha256=codex_system.codex_comparison_toolchain_sha256(),
    )
    expected = {
        "configurationSha256": evaluation.sha256(configuration_raw),
        "pixelSystemConfigurationSha256": evaluation.sha256(pixel_config_raw),
        "modelArtifactManifestFileSha256": evaluation.sha256(manifest_raw),
        "launchArgumentsFileSha256": evaluation.sha256(launch_raw),
        "modelContractSha256": evaluation.sha256(model_payload),
        "inferenceContractSha256": evaluation.sha256(inference_payload),
        "modelArtifactSha256": model["artifact"]["sha256"],
        "launchArgumentsSha256": outcome_runner.canonical_arguments_sha256(launch_arguments),
        "capabilityRetentionEvidenceSha256": evaluation.sha256(capability_raw),
        "codexSurfaceQualificationSha256": evaluation.sha256(surface_raw),
    }
    if any(preflight[field] != digest for field, digest in expected.items()):
        raise evaluation.OutcomeError("pair inputs or admitted contracts drifted after preflight")
    expected_images = {
        "codexRunner": configuration["codexRunnerImage"],
        "inferenceBoundary": configuration["codexBoundaryImage"],
        "modelBackend": model["runtime"]["imageDigest"],
    }
    if any(preflight["images"][field] != digest for field, digest in expected_images.items()):
        raise evaluation.OutcomeError("pair image configuration drifted after preflight")
    for label, digest in preflight["images"].items():
        _exact_image(codex_system, digest, label)
    outcome_runner.verify_runtime_identity(codex_system, model)
    if codex_system.codex_harness_contract_sha256() != preflight["codexHarnessSha256"]:
        raise evaluation.OutcomeError("Codex harness drifted after preflight")
    if admission["comparisonLane"] != "same-model-harness":
        raise evaluation.OutcomeError("pair preflight requires the exact same-model harness lane")
    if preflight["profile"] != admission["profile"]:
        raise evaluation.OutcomeError("pair preflight profile differs from the admitted task")
    # The shared preflight attests task-invariant qualified identity; the
    # reference task it was compiled from carries pixelLaunchBundleSha256, but
    # each admitted task gets its own fully hashed launch bundle and Pixel
    # enforces that hash throughout its run, so it is not bound here.
    pixel_expected = {
        "profile": preflight["profile"],
        "modelContractSha256": preflight["modelContractSha256"],
        "inferenceContractSha256": preflight["inferenceContractSha256"],
        "workPolicySha256": preflight["pixelWorkPolicySha256"],
        "environmentSha256": preflight["pixelEnvironmentSha256"],
        "harnessContractSha256": preflight["pixelHarnessSha256"],
        "runnerImageDigest": preflight["images"]["pixelRunner"],
        "verifierImageDigest": preflight["images"]["pixelVerifier"],
        "backendImageDigest": preflight["images"]["modelBackend"],
        "modelArtifactSha256": preflight["modelArtifactSha256"],
        "qualificationReceiptSha256": preflight["pixelModelQualificationReceiptSha256"],
    }
    return preflight, preflight_raw, model, model_payload, inference, inference_payload, launch_arguments, pixel_expected


def load_launch_arguments(path: Path) -> list[str]:
    value, _raw = evaluation.read_json(path, "private DSV4 launch arguments", private=True)
    value = evaluation.exact_fields(value, {"schemaVersion", "operation", "arguments", "boundary"}, "private DSV4 launch arguments")
    arguments = value["arguments"]
    if (
        value["schemaVersion"] != 1 or value["operation"] != "pixel-portal-outcome-dsv4-launch-arguments"
        or value["boundary"] != LAUNCH_BOUNDARY or not isinstance(arguments, list) or not 1 <= len(arguments) <= 128
        or any(not isinstance(item, str) or not item or len(item) > 4096 or "\x00" in item for item in arguments)
    ):
        raise evaluation.OutcomeError("DSV4 launch argument contract is invalid")
    return arguments


def _evidence(run_dir: Path, entries: list[dict[str, Any]], kind: str) -> tuple[dict[str, Any], dict[str, Any]]:
    matches = [item for item in entries if item.get("type") == kind]
    if len(matches) != 1:
        raise evaluation.OutcomeError(f"neutral dimension scorer requires one {kind} receipt")
    item = matches[0]
    payload = evaluation.evidence_bytes(
        run_dir / "run.json", item["relativePath"], item["sha256"], item["bytes"], f"dimension {kind}",
    )
    value = evaluation.parse_json(payload, f"dimension {kind}")
    if not isinstance(value, dict):
        raise evaluation.OutcomeError(f"dimension {kind} receipt is invalid")
    return item, value


def _ceiling_score(used: int, maximum: int) -> int:
    if maximum < 1 or used < 0:
        raise evaluation.OutcomeError("dimension resource ceiling is invalid")
    if used > maximum:
        return 0
    permille = used * 1000 // maximum
    if permille <= 250:
        return 4
    if permille <= 500:
        return 3
    if permille <= 750:
        return 2
    return 1


def neutral_dimensions(run_dir: Path, admission: dict[str, Any]) -> Callable[[list[dict[str, Any]], list[dict[str, Any]]], list[dict[str, Any]]]:
    """Build an arm-neutral scorer from controller and independent-verifier receipts only."""
    budgets = admission["budgets"]

    def score(entries: list[dict[str, Any]], artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        verifier_entry, verification = _evidence(run_dir, entries, "independent-verifier")
        workspace_verified = verification.get("status") == "pass" and verification.get("workerSelectedChecks") is False
        research_evidence_verified = (
            verification.get("status") == "evidence-pass" and verification.get("independent") is True
            and verification.get("semanticEntailmentVerified") is False
        )
        workspace_profile = admission["profile"] in {"assistant", "builder"}
        verified = workspace_verified if workspace_profile else False
        if workspace_profile:
            command_entry, command = _evidence(run_dir, entries, "command-exit")
            completed = command.get("exitCode") == 0
            latency = evaluation.integer(command.get("latencyMs"), 0, 31536000000, "dimension latency")
            model_requests = evaluation.integer(command.get("modelRequests"), 0, 10000000, "dimension model requests")
            input_tokens = evaluation.integer(command.get("inputTokens"), 0, 1000000000000, "dimension input tokens")
            output_tokens = evaluation.integer(command.get("outputTokens"), 0, 1000000000000, "dimension output tokens")
            operator_interventions = evaluation.integer(
                command.get("operatorInterventions"), 0, 1000000, "dimension operator interventions",
            )
            evaluation.integer(command.get("toolCalls"), 0, 1000000000, "dimension tool calls")
            resource_score = min(
                _ceiling_score(model_requests, budgets["modelRequests"]),
                _ceiling_score(input_tokens, budgets["inputTokens"]),
                _ceiling_score(output_tokens, budgets["outputTokens"]),
            )
            operational_values = {
                "operator-effort": 4 if operator_interventions == 0 else 0,
                "latency": _ceiling_score(latency, budgets["wallTimeSeconds"] * 1000),
                "resource-use": resource_score,
            }
        else:
            # Research evidence deliberately contains only the exact journey evidence set. Usage remains
            # recorded in each immutable run execution, but has no independent scoring receipt yet; award
            # no efficiency credit rather than deriving it from either harness's own narrative.
            command_entry = verifier_entry
            completed = research_evidence_verified
            operational_values = {"operator-effort": 0, "latency": 0, "resource-use": 0}
        values = {
            "outcome-completeness": 4 if verified and completed else 0,
            "correctness": 4 if verified else 0,
            "artifact-quality": 4 if (verified or research_evidence_verified) and artifacts else 0,
            # A baseline execution does not prove crash or restart recovery. Fault campaigns
            # must supply dedicated recovery evidence before this dimension can earn credit.
            "recovery": 0,
            **operational_values,
        }
        return [{
            "id": name, "score": values[name],
            "evidencePath": (verifier_entry if name in {"outcome-completeness", "correctness", "artifact-quality", "recovery"} else command_entry)["relativePath"],
            "evidenceSha256": (verifier_entry if name in {"outcome-completeness", "correctness", "artifact-quality", "recovery"} else command_entry)["sha256"],
        } for name in DIMENSIONS]

    return score


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _run_id(task_sha256: str, backend: str, epoch_ms: int) -> str:
    suffix = evaluation.sha256(f"{task_sha256}|{backend}|{epoch_ms}".encode("utf-8"))[:12]
    return f"outcomerun-{epoch_ms:013d}-{suffix}"


def _new_private_directory(path: Path) -> None:
    if not path.is_absolute() or path == Path(path.anchor):
        raise evaluation.OutcomeError("pair output must be an absolute non-root path")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    evaluation.private_parent(path)
    path.mkdir(mode=0o700, exist_ok=False)
    if os.name != "nt":
        path.chmod(0o700)


def execute_pair(
    *, root: Path, task_path: Path, configuration_path: Path, output_root: Path, order: str,
    runtime_condition: str,
    pixel_system_factory: Callable[..., Any] = pixel_live.PixelDockerSystem,
    codex_system_factory: Callable[..., Any] = codex_live.DockerSystem,
    pixel_runner: Callable[..., dict[str, Any]] = pixel_orchestrate.orchestrate_pixel_run,
    codex_runner: Callable[..., dict[str, Any]] = codex_orchestrate.orchestrate_codex_run,
) -> dict[str, Any]:
    if order not in {"pixel-first", "codex-first"}:
        raise evaluation.OutcomeError("pair arm order is invalid")
    if runtime_condition not in runtime_control.CONDITIONS:
        raise evaluation.OutcomeError("pair runtime condition is invalid")
    root = root.resolve(strict=True)
    task_path = _private_existing(_absolute(str(task_path), "private paired task path"), "private paired task")
    configuration_path = _private_existing(
        _absolute(str(configuration_path), "private pair system configuration path"),
        "private pair system configuration",
    )
    configuration, configuration_raw = load_configuration_record(configuration_path)
    _task, task_raw = evaluation.read_json(task_path, "private paired task", private=True)
    admission = outcome_task.admit_task(root, task_path)
    admission_sha256 = evaluation.sha256(evaluation.canonical(admission))
    if admission["comparisonLane"] != "same-model-harness":
        raise evaluation.OutcomeError("paired execution requires the exact same-model harness lane")
    if output_root == root or root in output_root.parents:
        raise evaluation.OutcomeError("private pair output must remain outside the source repository")
    task_sha256 = evaluation.sha256(task_raw)
    pixel_system = pixel_system_factory(root=root, configuration_path=Path(configuration["pixelSystemConfigPath"]))
    codex_system = codex_system_factory(
        root=root, codex_image=configuration["codexRunnerImage"], boundary_image=configuration["codexBoundaryImage"],
        pixel_system_config_path=Path(configuration["pixelSystemConfigPath"]),
    )
    (
        preflight, preflight_raw, _model, _model_payload, _inference, _inference_payload,
        launch_arguments, pixel_expected,
    ) = validate_preflight(
        root=root, task_path=task_path, configuration=configuration,
        configuration_raw=configuration_raw, admission=admission, codex_system=codex_system,
    )
    preflight_sha256 = evaluation.sha256(preflight_raw)
    pixel_system = _PreflightBoundPixelSystem(pixel_system, pixel_expected)
    preflight_path = Path(configuration["preflightPath"])
    _assert_pair_inputs_stable(
        root=root, task_path=task_path, task_raw=task_raw, admission_sha256=admission_sha256,
        configuration_path=configuration_path, configuration_raw=configuration_raw,
        preflight_path=preflight_path, preflight_raw=preflight_raw,
    )
    _new_private_directory(output_root)
    directories = {backend: output_root / backend for backend in ("pixel", "codex")}
    for path in directories.values():
        path.mkdir(mode=0o700)
        if os.name != "nt":
            path.chmod(0o700)
    epoch_ms = int(time.time_ns() // 1_000_000)
    run_ids = {"pixel": _run_id(task_sha256, "pixel", epoch_ms), "codex": _run_id(task_sha256, "codex", epoch_ms + 1)}
    scorers = {backend: neutral_dimensions(directories[backend], admission) for backend in directories}
    runners = {
        "pixel": lambda: pixel_runner(
            pixel_system, root=root, task_path=task_path, run_dir=directories["pixel"], run_id=run_ids["pixel"],
            runtime_condition=runtime_condition, dimensions=scorers["pixel"], clock=_timestamp,
        ),
        "codex": lambda: codex_runner(
            codex_system, root=root, task_path=task_path, run_dir=directories["codex"], run_id=run_ids["codex"],
            artifact_path=Path(configuration["modelArtifactPath"]),
            artifact_manifest_path=Path(configuration["modelArtifactManifestPath"]),
            launch_arguments=launch_arguments,
            harness_contract_sha256=preflight["codexHarnessSha256"],
            runtime_condition=runtime_condition,
            dimensions=scorers["codex"], clock=_timestamp,
        ),
    }
    sequence = ("pixel", "codex") if order == "pixel-first" else ("codex", "pixel")
    for backend in sequence:
        _assert_pair_inputs_stable(
            root=root, task_path=task_path, task_raw=task_raw, admission_sha256=admission_sha256,
            configuration_path=configuration_path, configuration_raw=configuration_raw,
            preflight_path=preflight_path, preflight_raw=preflight_raw,
        )
        runners[backend]()
        _assert_pair_inputs_stable(
            root=root, task_path=task_path, task_raw=task_raw, admission_sha256=admission_sha256,
            configuration_path=configuration_path, configuration_raw=configuration_raw,
            preflight_path=preflight_path, preflight_raw=preflight_raw,
        )
    _assert_pair_inputs_stable(
        root=root, task_path=task_path, task_raw=task_raw, admission_sha256=admission_sha256,
        configuration_path=configuration_path, configuration_raw=configuration_raw,
        preflight_path=preflight_path, preflight_raw=preflight_raw,
    )
    comparison = evaluation.compare_runs(root, directories["pixel"] / "run.json", directories["codex"] / "run.json")
    comparison_payload = json.dumps(comparison, indent=2, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"
    evaluation.write_new_private(output_root / "comparison.json", comparison_payload)
    _assert_pair_inputs_stable(
        root=root, task_path=task_path, task_raw=task_raw, admission_sha256=admission_sha256,
        configuration_path=configuration_path, configuration_raw=configuration_raw,
        preflight_path=preflight_path, preflight_raw=preflight_raw,
    )
    result = {
        "schemaVersion": 1, "operation": "pixel-portal-outcome-pair-execution",
        "taskSha256": task_sha256, "taskAdmissionSha256": admission_sha256,
        "configurationSha256": evaluation.sha256(configuration_raw), "preflightSha256": preflight_sha256,
        "modelContractSha256": preflight["modelContractSha256"],
        "inferenceContractSha256": preflight["inferenceContractSha256"],
        "runtimeCondition": runtime_condition,
        "armOrder": list(sequence), "pixelRunSha256": comparison["pixelRunSha256"],
        "codexRunSha256": comparison["codexRunSha256"],
        "comparisonSha256": evaluation.sha256(evaluation.canonical(comparison)),
        "status": comparison["status"], "classification": comparison["classification"],
        "boundary": PAIR_BOUNDARY,
    }
    evaluation.write_new_private(
        output_root / "pair.json", json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--order", choices=("pixel-first", "codex-first"), required=True)
    parser.add_argument("--runtime-condition", choices=tuple(sorted(runtime_control.CONDITIONS)), required=True)
    args = parser.parse_args()
    try:
        result = execute_pair(
            root=args.root, task_path=Path(os.path.abspath(args.task)),
            configuration_path=Path(os.path.abspath(args.config)), output_root=Path(os.path.abspath(args.output)),
            order=args.order, runtime_condition=args.runtime_condition,
        )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0 if result["status"] == "pass" else 3
    except (evaluation.OutcomeError, OSError, UnicodeError) as exc:
        print(f"[pixel] ERROR: {exc}", file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
