#!/usr/bin/env python3
"""Run or resume the exact materialized Pixel/Codex battery without overwriting evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Callable

import portal_outcome_evaluation as evaluation
import portal_outcome_materialize_battery as materializer
import portal_outcome_pair as pair_runner
import portal_outcome_sealed_corpus as sealed
import portal_outcome_pair_preflight as pair_preflight
import portal_outcome_task as outcome_task


CAMPAIGN_BOUNDARY = (
    "Content-free resumable DSV4 paired-battery campaign identity only. Private configuration, tasks, runs, "
    "artifacts, verifier evidence, failures, and paths remain in owner custody. This record grants no execution, "
    "effect, acceptance, publication, deployment, or promotion authority."
)
SUMMARY_BOUNDARY = (
    "Content-free DSV4 paired-battery progress snapshot only. Private configuration, tasks, runs, artifacts, "
    "verifier evidence, failures, and paths remain in owner custody. This record grants no execution, effect, "
    "acceptance, publication, deployment, or promotion authority."
)
FREEZE_BOUNDARY = (
    "Content-free immutable tuning-baseline freeze only. It binds every completed tuning comparison before "
    "the campaign opens held-out task bytes and grants no execution, retuning, model, provider, credential, external-effect, "
    "acceptance, publication, deployment, or promotion authority."
)
FREEZE_FILE = "tuning-baseline-freeze.json"
TASK_ID_RE = re.compile(r"^(?:battery|stress|trial|heldout)-[a-z0-9][a-z0-9-]{2,62}$")
ATTEMPT_RE = re.compile(r"^attempt-([0-9]{3})$")


def _canonical_payload(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"


def _private_directory(path: Path, label: str) -> Path:
    return pair_runner._private_existing(path, label, directory=True)


def load_materialization(root: Path, source_root: Path) -> tuple[dict[str, Any], str]:
    """Load and validate only the content-free materialization inventory.

    Task payloads are deliberately not opened here.  The campaign validates a
    partition's task bytes only after that partition is eligible for disclosure.
    In particular, tuning and freeze invocations must not read held-out prompts,
    workspaces, or verifiers merely to validate the inventory commitment.
    """
    _private_directory(root, "private battery materialization root")
    path = root / "materialization.json"
    value, raw = evaluation.read_json(path, "private battery materialization", private=True)
    allowed = {
        "schemaVersion", "operation", "batterySha256", "modelContractSha256", "inferenceContractSha256",
        "verifierImageDigest", "architecture", "profile", "evaluationRegime", "mode", "tasks", "boundary",
        "sealedCommitmentSha256",
    }
    if not isinstance(value, dict) or set(value) - allowed:
        raise evaluation.OutcomeError("private battery materialization has missing or unknown fields")
    required = allowed - {"sealedCommitmentSha256"}
    if not required.issubset(value):
        raise evaluation.OutcomeError("private battery materialization has missing fields")
    if (
        value["schemaVersion"] != 1 or value["operation"] != "pixel-portal-outcome-battery-materialization"
        or value["boundary"] != materializer.MATERIALIZATION_BOUNDARY or value["architecture"] not in {"amd64", "arm64"}
        or value["profile"] not in {"assistant", "builder", "controller", "researcher"}
        or value["evaluationRegime"] not in materializer.EVALUATION_REGIMES
        or value["mode"] not in {"combined", "sealed"}
        or not isinstance(value["tasks"], list) or not value["tasks"]
    ):
        raise evaluation.OutcomeError("battery materialization contract is invalid")
    for field in ("batterySha256", "modelContractSha256", "inferenceContractSha256"):
        evaluation.valid_hash(value[field], f"battery materialization {field}")
    sealed_commitment = value.get("sealedCommitmentSha256")
    if value["mode"] == "sealed":
        if not isinstance(sealed_commitment, str) or evaluation.HASH_RE.fullmatch(sealed_commitment) is None:
            raise evaluation.OutcomeError("sealed battery materialization commitment digest is invalid")
    elif sealed_commitment is not None:
        raise evaluation.OutcomeError("combined battery materialization must not bind a sealed commitment")
    if pair_runner.DIGEST_IMAGE_RE.fullmatch(value["verifierImageDigest"] or "") is None:
        raise evaluation.OutcomeError("battery materialization verifier image is invalid")
    seen: set[str] = set()
    for index, item in enumerate(value["tasks"]):
        item = evaluation.exact_fields(item, {
            "batteryTaskId", "axis", "evaluationRegime", "executionProfile", "targetProfile", "profileFidelity",
            "partition", "proofClass", "rehearsalJourneyId", "trialJourneyIds", "rehearsalFault",
            "formalProductJourneyId",
            "scaleClass", "workspaceFiles", "workspaceBytes", "workspaceLanguages",
            "taskRelativePath", "taskSha256", "taskAdmissionSha256",
            "sourceSnapshotSha256", "verifierSha256", "researchFixtureSha256", "expectedReplyRequired",
        }, f"battery materialization task {index}")
        task_id = item["batteryTaskId"]
        if not isinstance(task_id, str) or TASK_ID_RE.fullmatch(task_id) is None or task_id in seen:
            raise evaluation.OutcomeError("battery materialization task identity is invalid or duplicated")
        seen.add(task_id)
        if (
            not isinstance(item["axis"], str) or not item["axis"] or type(item["expectedReplyRequired"]) is not bool
            or item["executionProfile"] not in {"assistant", "builder", "controller", "researcher"}
            or item["evaluationRegime"] != value["evaluationRegime"]
            or item["targetProfile"] not in {"assistant", "builder", "controller", "researcher"}
            or item["profileFidelity"] not in {"exact", "surrogate-rehearsal"}
            or (item["profileFidelity"] == "exact") != (item["executionProfile"] == item["targetProfile"])
            or item["partition"] not in {"tuning", "held-out"}
            or item["proofClass"] not in {"real-disposable-workspace", materializer.REHEARSAL_PROOF_CLASS, materializer.FORMAL_PRODUCT_TASK_PROOF_CLASS}
            or item["scaleClass"] not in {"micro", "repository"}
            or not isinstance(item["workspaceFiles"], int) or not 1 <= item["workspaceFiles"] <= 1000
            or not isinstance(item["workspaceBytes"], int) or not 1 <= item["workspaceBytes"] <= 64 * 1024 * 1024
            or not isinstance(item["workspaceLanguages"], list) or len(set(item["workspaceLanguages"])) != len(item["workspaceLanguages"])
            or any(language not in {"python", "javascript", "typescript", "shell", "json", "yaml", "markdown"} for language in item["workspaceLanguages"])
            or item["scaleClass"] == "repository" and (item["workspaceFiles"] < 5 or item["workspaceBytes"] < 1000 or not item["workspaceLanguages"])
            or item["scaleClass"] == "micro" and item["workspaceLanguages"]
            or item["rehearsalJourneyId"] is not None and not isinstance(item["rehearsalJourneyId"], str)
            or item["formalProductJourneyId"] is not None and not isinstance(item["formalProductJourneyId"], str)
            or not isinstance(item["trialJourneyIds"], list) or len(set(item["trialJourneyIds"])) != len(item["trialJourneyIds"])
            or any(not isinstance(value, str) for value in item["trialJourneyIds"])
            or item["rehearsalFault"] is not None and not isinstance(item["rehearsalFault"], str)
            or (item["proofClass"] == materializer.REHEARSAL_PROOF_CLASS) != (item["rehearsalJourneyId"] is not None)
            or (item["proofClass"] == materializer.FORMAL_PRODUCT_TASK_PROOF_CLASS) != (item["formalProductJourneyId"] is not None)
            or item["rehearsalJourneyId"] is not None and item["formalProductJourneyId"] is not None
            or item["profileFidelity"] == "surrogate-rehearsal" and item["proofClass"] != materializer.REHEARSAL_PROOF_CLASS
        ):
            raise evaluation.OutcomeError("battery materialization task metadata is invalid")
        for field in ("taskSha256", "taskAdmissionSha256", "sourceSnapshotSha256", "verifierSha256"):
            evaluation.valid_hash(item[field], f"battery materialization task {field}")
        if item["executionProfile"] == "researcher":
            evaluation.valid_hash(item["researchFixtureSha256"], "battery materialization task research fixture")
        elif item["researchFixtureSha256"] is not None:
            raise evaluation.OutcomeError("non-Researcher materialization task binds a research fixture")
        relative = evaluation.relative_path(item["taskRelativePath"], "battery materialization task path")
        if relative.parts[0] != task_id or relative.as_posix() != f"{task_id}/task.json":
            raise evaluation.OutcomeError("battery materialization task path differs from its identity")
    return value, evaluation.sha256(raw)


def _validate_materialization_partition(
    root: Path, source_root: Path, value: dict[str, Any], partition: str,
) -> int:
    if partition not in {"tuning", "held-out"}:
        raise evaluation.OutcomeError("battery materialization disclosure partition is invalid")
    validated = 0
    for item in value["tasks"]:
        if item["partition"] != partition:
            continue
        relative = evaluation.relative_path(item["taskRelativePath"], "battery materialization task path")
        task_path = pair_runner._private_existing(root.joinpath(*relative.parts), "private materialized task")
        if evaluation.sha256(task_path.read_bytes()) != item["taskSha256"]:
            raise evaluation.OutcomeError("materialized task bytes differ from their inventory")
        admission = outcome_task.admit_task(source_root, task_path)
        if admission["profile"] != value["profile"] or item["executionProfile"] != value["profile"]:
            raise evaluation.OutcomeError("materialized task profile differs from its campaign materialization")
        if evaluation.sha256(evaluation.canonical(admission)) != item["taskAdmissionSha256"]:
            raise evaluation.OutcomeError("materialized task admission differs from its inventory")
        task, _task_raw = evaluation.read_json(task_path, "private materialized task", private=True)
        _environment_ref, environment_payload = outcome_task.resolved_reference(
            task_path, task["bindings"]["environment"], "materialized task environment",
        )
        _tool_ref, tool_payload = outcome_task.resolved_reference(
            task_path, task["bindings"]["toolPolicy"], "materialized task tool policy",
        )
        tool_policy = outcome_task.validate_tool_policy(tool_payload, admission["capabilities"])
        environment = outcome_task.validate_environment(environment_payload, tool_policy=tool_policy)
        materializer.validate_regime_bindings(value["evaluationRegime"], admission, environment, tool_policy)
        validated += 1
    if validated < 1:
        raise evaluation.OutcomeError("battery materialization disclosure partition has no tasks")
    return validated


def _campaign_identity(
    materialization: dict[str, Any], materialization_sha256: str, pair_config_sha256: str,
    preflight_sha256: str, runtime_condition: str, candidate_source_archive_sha256: str,
) -> dict[str, Any]:
    seed = evaluation.canonical({
        "materializationSha256": materialization_sha256, "pairConfigSha256": pair_config_sha256,
        "preflightSha256": preflight_sha256, "runtimeCondition": runtime_condition,
        "verifierImageDigest": materialization["verifierImageDigest"],
        "candidateSourceArchiveSha256": candidate_source_archive_sha256,
        "architecture": materialization["architecture"],
        "tasks": [item["taskSha256"] for item in materialization["tasks"]],
    })
    return {
        "schemaVersion": 1, "operation": "pixel-portal-outcome-battery-campaign",
        "campaignId": f"outcomebattery-{evaluation.sha256(seed)[:24]}",
        "materializationSha256": materialization_sha256, "pairConfigSha256": pair_config_sha256,
        "preflightSha256": preflight_sha256, "runtimeCondition": runtime_condition,
        "batterySha256": materialization["batterySha256"],
        "modelContractSha256": materialization["modelContractSha256"],
        "inferenceContractSha256": materialization["inferenceContractSha256"],
        "verifierImageDigest": materialization["verifierImageDigest"],
        "candidateSourceArchiveSha256": candidate_source_archive_sha256,
        "architecture": materialization["architecture"],
        "profile": materialization["profile"], "evaluationRegime": materialization["evaluationRegime"],
        "requiredPairs": len(materialization["tasks"]),
        "armOrderPolicy": "alternating-by-immutable-task-index-reversed-for-warm-condition",
        "boundary": CAMPAIGN_BOUNDARY,
    }


def _arm_order(index: int, runtime_condition: str) -> tuple[str, str]:
    evaluation.integer(index, 0, 499, "campaign immutable task index")
    if runtime_condition not in pair_runner.runtime_control.CONDITIONS:
        raise evaluation.OutcomeError("campaign arm-order runtime condition is invalid")
    offset = 1 if runtime_condition == "warm-neutral-probe" else 0
    return ("pixel", "codex") if (index + offset) % 2 == 0 else ("codex", "pixel")


def _review_partition_task_compatibility(
    *, root: Path, materialization_root: Path, materialization: dict[str, Any],
    partition: str, pair_configuration_path: Path, preflight: dict[str, Any], temporary_parent: Path,
    reviewer: Callable[..., dict[str, Any]],
) -> int:
    # A shared preflight attests task-invariant qualified identity; each admitted
    # task gets its own fully hashed launch bundle and Pixel enforces that hash
    # throughout its run, so launchBundleSha256 is not cross-task constant.
    expected = {
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
    reviewed = 0
    for item in materialization["tasks"]:
        if item["partition"] != partition:
            continue
        relative = evaluation.relative_path(item["taskRelativePath"], "battery compatibility task path")
        task_path = materialization_root.joinpath(*relative.parts)
        result = reviewer(
            root=root, task_path=task_path, configuration_path=pair_configuration_path,
            temporary_parent=temporary_parent,
        )
        if not isinstance(result, dict) or any(result.get(field) != value for field, value in expected.items()):
            raise evaluation.OutcomeError("battery task compatibility differs from the exact qualified preflight")
        reviewed += 1
    expected_count = sum(item["partition"] == partition for item in materialization["tasks"])
    if reviewed != expected_count or reviewed < 1:
        raise evaluation.OutcomeError("battery partition task compatibility review is incomplete")
    return reviewed


def _load_or_create_campaign(
    output_root: Path, expected: dict[str, Any], *, create_directory: Callable[[Path], None] = pair_runner._new_private_directory,
) -> None:
    campaign_path = output_root / "campaign.json"
    if not output_root.exists():
        create_directory(output_root)
        pairs = output_root / "pairs"
        pairs.mkdir(mode=0o700)
        if os.name != "nt":
            pairs.chmod(0o700)
        evaluation.write_new_private(campaign_path, json.dumps(expected, indent=2).encode("utf-8") + b"\n")
        return
    _private_directory(output_root, "private battery campaign root")
    observed, _raw = evaluation.read_json(campaign_path, "private battery campaign identity", private=True)
    if observed != expected:
        raise evaluation.OutcomeError("battery campaign identity differs from its exact materialization or pair configuration")
    _private_directory(output_root / "pairs", "private battery campaign pair root")


def _freeze_value(identity: dict[str, Any], materialization: dict[str, Any],
                completed: dict[str, tuple[int, dict[str, Any]]],
                sealed_commitment: dict[str, Any] | None = None,
                sealed_commitment_raw: bytes | None = None) -> dict[str, Any]:
    tuning = []
    heldout_commitment = []
    for item in materialization["tasks"]:
        if item["partition"] == "held-out":
            heldout_commitment.append({
                "batteryTaskId": item["batteryTaskId"], "taskSha256": item["taskSha256"],
                "taskAdmissionSha256": item["taskAdmissionSha256"],
                "sourceSnapshotSha256": item["sourceSnapshotSha256"], "verifierSha256": item["verifierSha256"],
                "researchFixtureSha256": item["researchFixtureSha256"],
            })
            continue
        observed = completed.get(item["batteryTaskId"])
        if observed is None:
            raise evaluation.OutcomeError("cannot freeze tuning baseline before every tuning task has immutable pair evidence")
        if observed[1]["status"] != "pass":
            raise evaluation.OutcomeError(
                f"cannot freeze tuning baseline while tuning pair {item['batteryTaskId']} is {observed[1]['status']}, not pass",
            )
        tuning.append({
            "batteryTaskId": item["batteryTaskId"], "taskSha256": item["taskSha256"],
            "attempt": observed[0], "comparisonSha256": observed[1]["comparisonSha256"],
            "status": observed[1]["status"], "classification": observed[1]["classification"],
        })
    if not tuning:
        raise evaluation.OutcomeError("cannot freeze an empty tuning baseline")
    if sealed_commitment is not None:
        if sealed_commitment_raw is None:
            raise evaluation.OutcomeError("sealed tuning freeze requires the exact commitment bytes")
        freeze_value, _raw = sealed.build_freeze(
            identity=identity, materialization=materialization, commitment=sealed_commitment,
            commitment_raw=sealed_commitment_raw, tuning_results=tuning,
        )
        return freeze_value
    return {
        "schemaVersion": 1, "operation": "pixel-portal-outcome-tuning-baseline-freeze",
        "campaignId": identity["campaignId"], "materializationSha256": identity["materializationSha256"],
        "pairConfigSha256": identity["pairConfigSha256"], "preflightSha256": identity["preflightSha256"],
        "tuningTaskSetSha256": evaluation.sha256(evaluation.canonical(tuning)), "tuningTasks": tuning,
        "heldOutTaskSetSha256": evaluation.sha256(evaluation.canonical(heldout_commitment)),
        "heldOutTaskCount": len(heldout_commitment), "heldOutTaskBytesOpened": False,
        "authority": {"execution": False, "retuning": False, "heldOutDisclosure": False, "completion": False, "promotion": False},
        "boundary": FREEZE_BOUNDARY,
    }


def _load_freeze(path: Path, expected: dict[str, Any]) -> dict[str, Any]:
    value, raw = evaluation.read_json(path, "private tuning baseline freeze", private=True)
    if set(value) == sealed.FREEZE_FIELDS:
        value = sealed._validate_freeze(value)
    else:
        value = evaluation.exact_fields(value, {
            "schemaVersion", "operation", "campaignId", "materializationSha256", "pairConfigSha256",
            "preflightSha256", "tuningTaskSetSha256", "tuningTasks", "heldOutTaskSetSha256",
            "heldOutTaskCount", "heldOutTaskBytesOpened", "authority", "boundary",
        }, "private tuning baseline freeze")
    if value != expected or raw != _canonical_payload(expected):
        raise evaluation.OutcomeError("tuning baseline freeze differs from the exact completed pre-disclosure baseline")
    return value


def _valid_pair(
    root: Path, task_path: Path, attempt: Path, expected_order: tuple[str, str],
    expected_bindings: dict[str, str], expected_runtime_condition: str,
) -> dict[str, Any] | None:
    pair_path = attempt / "pair.json"
    if not pair_path.is_file():
        return None
    value, _raw = evaluation.read_json(pair_path, "private paired execution index", private=True)
    value = evaluation.exact_fields(value, {
        "schemaVersion", "operation", "taskSha256", "taskAdmissionSha256", "armOrder", "pixelRunSha256",
        "codexRunSha256", "comparisonSha256", "configurationSha256", "preflightSha256",
        "modelContractSha256", "inferenceContractSha256", "runtimeCondition", "status", "classification", "boundary",
    }, "private paired execution index")
    admission = outcome_task.admit_task(root, task_path)
    if (
        value["schemaVersion"] != 1 or value["operation"] != "pixel-portal-outcome-pair-execution"
        or value["taskSha256"] != evaluation.sha256(task_path.read_bytes())
        or value["taskAdmissionSha256"] != evaluation.sha256(evaluation.canonical(admission))
        or value["armOrder"] != list(expected_order)
        or value["runtimeCondition"] != expected_runtime_condition
        or any(value[field] != digest for field, digest in expected_bindings.items())
        or value["status"] not in {"pass", "blocked"} or value["boundary"] != pair_runner.PAIR_BOUNDARY
    ):
        raise evaluation.OutcomeError("paired execution index identity or task binding is invalid")
    comparison = evaluation.compare_runs(root, attempt / "pixel" / "run.json", attempt / "codex" / "run.json")
    for field in ("pixelRunSha256", "codexRunSha256", "status", "classification"):
        if value[field] != comparison[field]:
            raise evaluation.OutcomeError("paired execution index differs from recomputed run comparison")
    if value["comparisonSha256"] != evaluation.sha256(evaluation.canonical(comparison)):
        raise evaluation.OutcomeError("paired execution comparison digest is invalid")
    stored, _comparison_raw = evaluation.read_json(attempt / "comparison.json", "private paired comparison", private=True)
    if stored != comparison:
        raise evaluation.OutcomeError("stored paired comparison differs from recomputed evidence")
    return value


def _completed_attempt(
    root: Path, task_path: Path, task_root: Path, expected_order: tuple[str, str],
    expected_bindings: dict[str, str], expected_runtime_condition: str,
) -> tuple[int, dict[str, Any]] | None:
    if not task_root.exists():
        return None
    _private_directory(task_root, "private battery task result root")
    completed = []
    for path in task_root.iterdir():
        match = ATTEMPT_RE.fullmatch(path.name)
        if match is None:
            raise evaluation.OutcomeError("battery task result root contains an unknown entry")
        _private_directory(path, "private battery pair attempt")
        value = _valid_pair(root, task_path, path, expected_order, expected_bindings, expected_runtime_condition)
        if value is not None:
            completed.append((int(match.group(1)), value))
    if len(completed) > 1:
        raise evaluation.OutcomeError("battery task has multiple completed attempts")
    return completed[0] if completed else None


def _next_attempt(task_root: Path) -> Path:
    task_root.mkdir(mode=0o700, exist_ok=True)
    if os.name != "nt":
        task_root.chmod(0o700)
    numbers = []
    for path in task_root.iterdir():
        match = ATTEMPT_RE.fullmatch(path.name)
        if match is None:
            raise evaluation.OutcomeError("battery task result root contains an unknown entry")
        numbers.append(int(match.group(1)))
    next_number = max(numbers, default=0) + 1
    if next_number > 999:
        raise evaluation.OutcomeError("battery task exhausted its bounded attempt namespace")
    return task_root / f"attempt-{next_number:03d}"


def _recompute_tuning_freeze(
    *, root: Path, tuning_materialization_root: Path, tuning_output_root: Path,
    pair_config_sha256: str, preflight_sha256: str, configuration: dict[str, Any],
    preflight: dict[str, Any], runtime_condition: str, commitment: dict[str, Any],
    commitment_raw: bytes,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Reconstruct the authoritative tuning identity and freeze from the sealed tuning materialization.

    The tuning materialization root is reloaded and its commitment binding, pair
    config/preflight/model/inference/runtime identity, and every completed tuning
    pair are recomputed using the real _valid_pair path.  The freeze is rebuilt
    and must be exactly canonical-equal to the stored freeze by the caller.
    """
    if not tuning_output_root.exists():
        raise evaluation.OutcomeError("held-out execution requires the original sealed tuning campaign output")
    # Tighten path custody: the original tuning output root must be an absolute canonical
    # owner-private directory before campaign/freeze/pair evidence is read below.
    _private_directory(tuning_output_root, "private original tuning campaign output root")
    tuning_materialization, tuning_mat_sha = load_materialization(tuning_materialization_root, root)
    if tuning_materialization["sealedCommitmentSha256"] != evaluation.sha256(commitment_raw):
        raise evaluation.OutcomeError("sealed tuning materialization does not bind the exact commitment")
    _validate_materialization_partition(tuning_materialization_root, root, tuning_materialization, "tuning")
    identity = _campaign_identity(
        tuning_materialization, tuning_mat_sha, pair_config_sha256, preflight_sha256,
        runtime_condition, configuration["candidateSourceArchiveSha256"],
    )
    candidate = sealed.candidate_identity(identity, tuning_materialization)
    # Before any pair evidence is credited, require the sealed tuning materialization to bind
    # the exact committed tuning corpus and commitment, the exact committed tuning task count,
    # and the qualified candidate/preflight model/inference/verifier/architecture/profile/
    # regime evidence.  Any mismatch fails closed before _verify_reveal.
    if tuning_materialization["batterySha256"] != commitment["tuningCorpusSha256"]:
        raise evaluation.OutcomeError("sealed tuning materialization battery differs from the committed tuning corpus")
    sealed.validate_materialization_binding(
        materialization=tuning_materialization, commitment=commitment, commitment_raw=commitment_raw,
        candidate=candidate, expected_task_count=commitment["tuningTaskCount"],
        expected_battery_sha256=commitment["tuningCorpusSha256"],
    )
    if (
        tuning_materialization["modelContractSha256"] != preflight["modelContractSha256"]
        or tuning_materialization["inferenceContractSha256"] != preflight["inferenceContractSha256"]
        or tuning_materialization["profile"] != preflight["profile"]
    ):
        raise evaluation.OutcomeError("sealed tuning materialization does not bind the exact preflight contract")
    expected_bindings = {
        "configurationSha256": pair_config_sha256, "preflightSha256": preflight_sha256,
        "modelContractSha256": tuning_materialization["modelContractSha256"],
        "inferenceContractSha256": tuning_materialization["inferenceContractSha256"],
    }
    tuning_completed: dict[str, tuple[int, dict[str, Any]]] = {}
    for index, item in enumerate(tuning_materialization["tasks"]):
        relative = evaluation.relative_path(item["taskRelativePath"], "tuning materialization task path")
        task_path = tuning_materialization_root.joinpath(*relative.parts)
        if item["partition"] != "tuning":
            continue
        task_id = item["batteryTaskId"]
        observed = _completed_attempt(
            root, task_path, tuning_output_root / "pairs" / task_id,
            _arm_order(index, runtime_condition), expected_bindings, runtime_condition,
        )
        if observed is None:
            raise evaluation.OutcomeError("cannot recompute the tuning baseline: a tuning pair has no immutable evidence")
        tuning_completed[task_id] = observed
    tuning_results = []
    for item in tuning_materialization["tasks"]:
        if item["partition"] != "tuning":
            continue
        task_id = item["batteryTaskId"]
        observed = tuning_completed[task_id]
        tuning_results.append({
            "batteryTaskId": task_id, "taskSha256": item["taskSha256"],
            "attempt": observed[0], "comparisonSha256": observed[1]["comparisonSha256"],
            "status": observed[1]["status"], "classification": observed[1]["classification"],
        })
    expected_freeze, _raw = sealed.build_freeze(
        identity=identity, materialization=tuning_materialization, commitment=commitment,
        commitment_raw=commitment_raw, tuning_results=tuning_results,
    )
    return identity, expected_freeze


def run_campaign(

    *, root: Path, materialization_root: Path, pair_configuration_path: Path, output_root: Path,
    max_pairs: int, partition: str = "tuning", freeze_tuning: bool = False,
    runtime_condition: str = "cold-first-request",
    execute_pair: Callable[..., dict[str, Any]] = pair_runner.execute_pair,
    review_task_compatibility: Callable[..., dict[str, Any]] | None = None,
    sealed_tuning_root: Path | None = None, sealed_reveal_receipt: Path | None = None,
    sealed_tuning_materialization_root: Path | None = None,
    sealed_tuning_output_root: Path | None = None,
) -> dict[str, Any]:
    if partition not in {"tuning", "held-out"} or type(freeze_tuning) is not bool:
        raise evaluation.OutcomeError("battery campaign partition or freeze mode is invalid")
    if runtime_condition not in pair_runner.runtime_control.CONDITIONS:
        raise evaluation.OutcomeError("battery campaign runtime condition is invalid")
    root = root.resolve(strict=True)
    if output_root == root or root in output_root.parents:
        raise evaluation.OutcomeError("private battery campaign output must remain outside the source repository")
    materialization_root = pair_runner._private_existing(
        pair_runner._absolute(str(materialization_root), "battery materialization path"),
        "private battery materialization root", directory=True,
    )
    pair_configuration_path = pair_runner._private_existing(
        pair_runner._absolute(str(pair_configuration_path), "pair configuration path"),
        "private pair system configuration",
    )
    sealed_mode = sealed_tuning_root is not None
    sealed_commitment: dict[str, Any] | None = None
    sealed_commitment_raw: bytes | None = None
    if sealed_mode:
        if not isinstance(sealed_tuning_root, Path):
            sealed_tuning_root = Path(os.path.abspath(str(sealed_tuning_root)))
        sealed_commitment, sealed_commitment_raw = sealed._read_commitment(sealed_tuning_root)
        if sealed_reveal_receipt is not None and not isinstance(sealed_reveal_receipt, Path):
            sealed_reveal_receipt = Path(os.path.abspath(str(sealed_reveal_receipt)))
        if sealed_tuning_materialization_root is not None and not isinstance(sealed_tuning_materialization_root, Path):
            sealed_tuning_materialization_root = Path(os.path.abspath(str(sealed_tuning_materialization_root)))
        if sealed_tuning_output_root is not None and not isinstance(sealed_tuning_output_root, Path):
            sealed_tuning_output_root = Path(os.path.abspath(str(sealed_tuning_output_root)))
    else:
        if sealed_reveal_receipt is not None:
            raise evaluation.OutcomeError("reveal receipt requires a sealed tuning root")
    materialization, materialization_sha256 = load_materialization(materialization_root, root)
    if materialization["evaluationRegime"] == "maximum-quality" and (freeze_tuning or partition == "held-out"):
        raise evaluation.OutcomeError(
            "maximum-quality execution cannot freeze or disclose held-out tasks before a separate frozen matched-budget baseline",
        )
    configuration, configuration_raw = pair_runner.load_configuration_record(pair_configuration_path)
    preflight_path = pair_runner._private_existing(
        pair_runner._absolute(configuration["preflightPath"], "pair preflight path"), "private pair preflight",
    )
    if preflight_path == root or root in preflight_path.parents:
        raise evaluation.OutcomeError("private pair preflight must remain outside the source repository")
    preflight, preflight_raw = pair_runner.load_preflight(preflight_path)
    pair_config_sha256 = evaluation.sha256(configuration_raw)
    preflight_sha256 = evaluation.sha256(preflight_raw)
    if (
        preflight["configurationSha256"] != pair_config_sha256
        or preflight["modelContractSha256"] != materialization["modelContractSha256"]
        or preflight["inferenceContractSha256"] != materialization["inferenceContractSha256"]
        or preflight["profile"] != materialization["profile"]
    ):
        raise evaluation.OutcomeError("battery materialization or pair configuration differs from its preflight")
    expected_bindings = {
        "configurationSha256": pair_config_sha256, "preflightSha256": preflight_sha256,
        "modelContractSha256": materialization["modelContractSha256"],
        "inferenceContractSha256": materialization["inferenceContractSha256"],
    }
    evaluation.integer(max_pairs, 1, 500, "campaign invocation pair ceiling")
    selected = [item for item in materialization["tasks"] if item["partition"] == partition]
    if not selected:
        raise evaluation.OutcomeError("battery campaign partition has no tasks")
    completed: dict[str, tuple[int, dict[str, Any]]] = {}
    task_paths: dict[str, Path] = {}
    expected_orders: dict[str, tuple[str, str]] = {}
    for index, item in enumerate(materialization["tasks"]):
        relative = evaluation.relative_path(item["taskRelativePath"], "battery task path")
        task_path = materialization_root.joinpath(*relative.parts)
        task_paths[item["batteryTaskId"]] = task_path
        expected_order = _arm_order(index, runtime_condition)
        expected_orders[item["batteryTaskId"]] = expected_order

    # Tuning bytes are the only task payloads eligible before the immutable
    # baseline freeze.  Held-out inventory hashes remain committed by the
    # campaign identity, but their files are not opened or compatibility-
    # reviewed until the exact freeze has been validated below.
    compatibility_reviewed = 0
    reviewer = review_task_compatibility or pair_preflight.review_materialized_pixel_task_compatibility
    heldout_state_exists = any(
        (output_root / "pairs" / item["batteryTaskId"]).exists()
        for item in materialization["tasks"] if item["partition"] == "held-out"
    )
    freeze_path = output_root / FREEZE_FILE
    tuning_baseline_freeze_sha256 = None
    stored_identity: dict[str, Any] | None = None
    stored_freeze: dict[str, Any] | None = None

    if partition == "tuning":
        _validate_materialization_partition(materialization_root, root, materialization, "tuning")
        compatibility_reviewed = _review_partition_task_compatibility(
            root=root, materialization_root=materialization_root, materialization=materialization,
            partition="tuning", pair_configuration_path=pair_configuration_path, preflight=preflight,
            temporary_parent=output_root.parent, reviewer=reviewer,
        )
        identity = _campaign_identity(
            materialization, materialization_sha256, pair_config_sha256, preflight_sha256, runtime_condition,
            configuration["candidateSourceArchiveSha256"],
        )
        _load_or_create_campaign(output_root, identity)
        if heldout_state_exists:
            if freeze_tuning:
                raise evaluation.OutcomeError("cannot freeze tuning baseline after held-out evidence exists")
            raise evaluation.OutcomeError("tuning cannot continue after held-out campaign state exists")
        if freeze_path.exists():
            if freeze_tuning:
                raise evaluation.OutcomeError("retuning after freeze requires a new candidate and a fresh commitment")
            raise evaluation.OutcomeError("tuning baseline is frozen; retuning requires a new campaign identity")
        for item in materialization["tasks"]:
            if item["partition"] != "tuning":
                continue
            task_id = item["batteryTaskId"]
            observed = _completed_attempt(
                root, task_paths[task_id], output_root / "pairs" / task_id, expected_orders[task_id], expected_bindings,
                runtime_condition,
            )
            if observed is not None:
                completed[task_id] = observed
        tuning_complete = all(
            item["partition"] != "tuning" or item["batteryTaskId"] in completed for item in materialization["tasks"]
        )
        expected_freeze = _freeze_value(
            identity, materialization, completed, sealed_commitment, sealed_commitment_raw,
        ) if tuning_complete else None
        if freeze_tuning:
            if expected_freeze is None:
                raise evaluation.OutcomeError("tuning baseline freeze requires every tuning pair and tuning partition mode")
            if heldout_state_exists:
                raise evaluation.OutcomeError("cannot freeze tuning baseline after held-out evidence exists")
            evaluation.write_new_private(freeze_path, _canonical_payload(expected_freeze))
            return expected_freeze
    else:
        # held-out partition
        if not output_root.exists():
            raise evaluation.OutcomeError("held-out execution requires an exact completed tuning-baseline freeze")
        _private_directory(output_root, "private battery campaign root")
        if sealed_mode:
            if not freeze_path.exists():
                raise evaluation.OutcomeError("held-out execution requires an exact completed tuning-baseline freeze")
            if sealed_reveal_receipt is None:
                raise evaluation.OutcomeError("held-out execution requires an exact reveal receipt")
            if sealed_tuning_materialization_root is None or sealed_tuning_output_root is None:
                raise evaluation.OutcomeError("held-out execution requires the original sealed tuning materialization and campaign output")
            recomputed_identity, expected_freeze = _recompute_tuning_freeze(
                root=root, tuning_materialization_root=sealed_tuning_materialization_root,
                tuning_output_root=sealed_tuning_output_root, pair_config_sha256=pair_config_sha256,
                preflight_sha256=preflight_sha256, configuration=configuration, preflight=preflight,
                runtime_condition=runtime_condition, commitment=sealed_commitment,
                commitment_raw=sealed_commitment_raw,
            )
            stored_freeze = _load_freeze(freeze_path, expected_freeze)
            tuning_baseline_freeze_sha256 = evaluation.sha256(_canonical_payload(expected_freeze))
            campaign_observed, _raw = evaluation.read_json(
                output_root / "campaign.json", "private battery campaign identity", private=True,
            )
            if campaign_observed != recomputed_identity:
                raise evaluation.OutcomeError("held-out campaign identity differs from the recomputed original tuning campaign")
            identity = recomputed_identity
            candidate = sealed._validate_candidate(stored_freeze["candidate"])
            sealed.freeze_binds(stored_freeze, sealed_commitment, sealed_commitment_raw, candidate)
            materialization_raw = evaluation.read_bytes(
                materialization_root / "materialization.json", limit=evaluation.MAX_JSON_BYTES, private=True,
            )
            sealed.validate_heldout_materialization(
                materialization=materialization, materialization_raw=materialization_raw,
                freeze=stored_freeze, commitment=sealed_commitment,
                commitment_raw=sealed_commitment_raw, receipt_path=sealed_reveal_receipt,
            )
            _validate_materialization_partition(materialization_root, root, materialization, "held-out")
            compatibility_reviewed = _review_partition_task_compatibility(
                root=root, materialization_root=materialization_root, materialization=materialization,
                partition="held-out", pair_configuration_path=pair_configuration_path, preflight=preflight,
                temporary_parent=output_root.parent, reviewer=reviewer,
            )
            for item in selected:
                task_id = item["batteryTaskId"]
                observed = _completed_attempt(
                    root, task_paths[task_id], output_root / "pairs" / task_id, expected_orders[task_id], expected_bindings,
                    runtime_condition,
                )
                if observed is not None:
                    completed[task_id] = observed
        else:
            identity = _campaign_identity(
                materialization, materialization_sha256, pair_config_sha256, preflight_sha256, runtime_condition,
                configuration["candidateSourceArchiveSha256"],
            )
            _validate_materialization_partition(materialization_root, root, materialization, "tuning")
            _load_or_create_campaign(output_root, identity)
            for item in materialization["tasks"]:
                if item["partition"] != "tuning":
                    continue
                task_id = item["batteryTaskId"]
                observed = _completed_attempt(
                    root, task_paths[task_id], output_root / "pairs" / task_id, expected_orders[task_id], expected_bindings,
                    runtime_condition,
                )
                if observed is not None:
                    completed[task_id] = observed
            tuning_complete = all(
                item["partition"] != "tuning" or item["batteryTaskId"] in completed for item in materialization["tasks"]
            )
            expected_freeze = _freeze_value(identity, materialization, completed) if tuning_complete else None
            if expected_freeze is None or not freeze_path.exists():
                raise evaluation.OutcomeError("held-out execution requires an exact completed tuning-baseline freeze")
            _load_freeze(freeze_path, expected_freeze)
            tuning_baseline_freeze_sha256 = evaluation.sha256(_canonical_payload(expected_freeze))
            _validate_materialization_partition(materialization_root, root, materialization, "held-out")
            compatibility_reviewed = _review_partition_task_compatibility(
                root=root, materialization_root=materialization_root, materialization=materialization,
                partition="held-out", pair_configuration_path=pair_configuration_path, preflight=preflight,
                temporary_parent=output_root.parent, reviewer=reviewer,
            )
            for item in selected:
                task_id = item["batteryTaskId"]
                observed = _completed_attempt(
                    root, task_paths[task_id], output_root / "pairs" / task_id, expected_orders[task_id], expected_bindings,
                    runtime_condition,
                )
                if observed is not None:
                    completed[task_id] = observed
    executed = 0
    for item in selected:
        task_id = item["batteryTaskId"]
        if task_id in completed or executed >= max_pairs:
            continue
        task_root = output_root / "pairs" / task_id
        attempt = _next_attempt(task_root)
        expected_order = expected_orders[task_id]
        order = "pixel-first" if expected_order[0] == "pixel" else "codex-first"
        execute_pair(
            root=root, task_path=task_paths[task_id], configuration_path=pair_configuration_path,
            output_root=attempt, order=order, runtime_condition=runtime_condition,
        )
        observed = _valid_pair(
            root, task_paths[task_id], attempt, expected_order, expected_bindings, runtime_condition,
        )
        if observed is None:
            raise evaluation.OutcomeError("paired runner returned without complete evidence")
        completed[task_id] = (int(attempt.name[-3:]), observed)
        executed += 1
    entries = [{
        "batteryTaskId": item["batteryTaskId"], "axis": item["axis"],
        "executionProfile": item["executionProfile"], "targetProfile": item["targetProfile"],
        "profileFidelity": item["profileFidelity"],
        "partition": item["partition"], "proofClass": item["proofClass"],
        "rehearsalJourneyId": item["rehearsalJourneyId"], "trialJourneyIds": item["trialJourneyIds"], "rehearsalFault": item["rehearsalFault"],
        "attempt": completed[item["batteryTaskId"]][0],
        "armOrder": completed[item["batteryTaskId"]][1]["armOrder"],
        "comparisonSha256": completed[item["batteryTaskId"]][1]["comparisonSha256"],
        "status": completed[item["batteryTaskId"]][1]["status"],
        "classification": completed[item["batteryTaskId"]][1]["classification"],
    } for item in selected if item["batteryTaskId"] in completed]
    required = len(selected)
    status = "in-progress" if len(entries) < required else "pass" if all(item["status"] == "pass" for item in entries) else "blocked"
    result = {
        "schemaVersion": 1, "operation": "pixel-portal-outcome-battery-campaign-progress",
        "campaignId": identity["campaignId"], "observedAt": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "partition": partition, "profile": materialization["profile"],
        "evaluationRegime": materialization["evaluationRegime"],
        "tuningBaselineFrozen": tuning_baseline_freeze_sha256 is not None,
        "tuningBaselineFreezeSha256": tuning_baseline_freeze_sha256,
        "runtimeCondition": runtime_condition,
        "status": status, "requiredPairs": required, "completedPairs": len(entries),
        "pass": sum(item["status"] == "pass" for item in entries),
        "blocked": sum(item["status"] == "blocked" for item in entries),
        "promotionEligiblePairs": sum(item["proofClass"] == "real-disposable-workspace" for item in entries),
        "nonPromotionalRehearsalPairs": sum(item["proofClass"] == materializer.REHEARSAL_PROOF_CLASS for item in entries),
        "exactProfilePairs": sum(item["profileFidelity"] == "exact" for item in entries),
        "surrogateProfileRehearsalPairs": sum(item["profileFidelity"] == "surrogate-rehearsal" for item in entries),
        "compatibilityReviewedTasks": compatibility_reviewed,
        "executedThisInvocation": executed, "pairs": entries, "boundary": SUMMARY_BOUNDARY,
    }
    snapshot = output_root / f"progress-{time.time_ns():019d}.json"
    evaluation.write_new_private(snapshot, json.dumps(result, indent=2, ensure_ascii=False).encode("utf-8") + b"\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--materialization", type=Path, required=True)
    parser.add_argument("--pair-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-pairs", type=int, default=1)
    parser.add_argument("--partition", choices=("tuning", "held-out"), default="tuning")
    parser.add_argument("--freeze-tuning", action="store_true")
    parser.add_argument("--runtime-condition", choices=tuple(sorted(pair_runner.runtime_control.CONDITIONS)), required=True)
    parser.add_argument("--sealed-tuning-root", type=Path)
    parser.add_argument("--sealed-reveal-receipt", type=Path)
    parser.add_argument("--sealed-tuning-materialization-root", type=Path)
    parser.add_argument("--sealed-tuning-output-root", type=Path)
    args = parser.parse_args()
    try:
        sealed_tuning_root = (
            Path(os.path.abspath(args.sealed_tuning_root)) if args.sealed_tuning_root is not None else None
        )
        sealed_reveal_receipt = (
            Path(os.path.abspath(args.sealed_reveal_receipt)) if args.sealed_reveal_receipt is not None else None
        )
        sealed_tuning_materialization_root = (
            Path(os.path.abspath(args.sealed_tuning_materialization_root))
            if args.sealed_tuning_materialization_root is not None else None
        )
        sealed_tuning_output_root = (
            Path(os.path.abspath(args.sealed_tuning_output_root))
            if args.sealed_tuning_output_root is not None else None
        )
        if args.freeze_tuning and args.partition != "tuning":
            raise evaluation.OutcomeError("freeze tuning is valid only for the tuning partition")
        guardian_arguments = (sealed_reveal_receipt, sealed_tuning_materialization_root, sealed_tuning_output_root)
        if any(value is not None for value in guardian_arguments) and sealed_tuning_root is None:
            raise evaluation.OutcomeError("sealed guardian/original-tuning arguments require a sealed tuning root")
        if args.partition == "tuning" and any(value is not None for value in guardian_arguments):
            raise evaluation.OutcomeError("sealed receipt/original-tuning materialization and output are forbidden for tuning")
        if (
            args.partition == "held-out" and sealed_tuning_root is not None
            and not all(value is not None for value in guardian_arguments)
        ):
            raise evaluation.OutcomeError(
                "held-out sealed execution requires the sealed reveal receipt and original tuning materialization and output",
            )
        result = run_campaign(
            root=args.root, materialization_root=Path(os.path.abspath(args.materialization)),
            pair_configuration_path=Path(os.path.abspath(args.pair_config)),
            output_root=Path(os.path.abspath(args.output)), max_pairs=args.max_pairs,
            partition=args.partition, freeze_tuning=args.freeze_tuning, runtime_condition=args.runtime_condition,
            sealed_tuning_root=sealed_tuning_root, sealed_reveal_receipt=sealed_reveal_receipt,
            sealed_tuning_materialization_root=sealed_tuning_materialization_root,
            sealed_tuning_output_root=sealed_tuning_output_root,
        )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        operation = result.get("operation")
        if operation == "pixel-portal-outcome-tuning-baseline-freeze":
            return 0
        if operation != "pixel-portal-outcome-battery-campaign-progress":
            raise evaluation.OutcomeError("campaign returned an unknown result operation")
        return 0 if result.get("status") == "pass" else 3
    except (evaluation.OutcomeError, OSError, UnicodeError) as exc:
        print(f"[pixel] ERROR: {exc}", file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
