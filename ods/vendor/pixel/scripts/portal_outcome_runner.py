#!/usr/bin/env python3
"""Verify one exact shared-model runtime and assemble one real portal outcome run."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat
from typing import Any

import portal_outcome_evaluation as evaluation


CONTAINER_ID_RE = re.compile(r"^[a-f0-9]{64}$")
RUNTIME_EXECUTABLES = {"llama.cpp": "/app/llama-server", "vllm": "/opt/venv/bin/vllm"}
LAUNCH_FLAG_BINDINGS = {
    "llama.cpp": (("--ctx-size", "contextWindow"), ("--parallel", "parallelSlots")),
    "vllm": (("--max-model-len", "contextWindow"), ("--max-num-seqs", "parallelSlots")),
}
HASH_CHUNK_BYTES = 8 * 1024 * 1024
MAX_MODEL_ARTIFACT_BYTES = 1099511627776
FRESH_RUNTIME_OPERATION = "pixel-portal-outcome-fresh-runtime-start"
MODEL_ARTIFACT_MANIFEST_SCHEMA = "https://osmantic.com/pixel/schemas/work-model-artifact-manifest-v1.schema.json"
MODEL_ARTIFACT_MANIFEST_AUTHORITY = {
    "grantsExecution": False,
    "startsContainer": False,
    "grantsNetwork": False,
    "grantsDevices": False,
    "grantsCredentials": False,
    "grantsExternalEffects": False,
    "grantsCompletion": False,
}
MODEL_ARTIFACT_MANIFEST_BOUNDARY = (
    "Private deterministic identity for exact owner-selected local-model bytes. It grants no model trust, capability, "
    "execution, container start, network, device, credential, external-effect, or completion authority."
)
INTERACTION_BOUNDARY = (
    "Content-free mechanically observed interaction telemetry for one single-admission noninteractive comparison run. "
    "It records only event classes, counts, and an observation digest; it contains no prompt, response, tool argument, "
    "result, path, credential, provider content, approval authority, or completion authority."
)
INTERACTION_SOURCES = {
    "pixel-assistant-conversation-v1", "pixel-builder-checkpoint-v1", "pixel-controller-ledger-v1",
    "pixel-researcher-lifecycle-v1", "codex-jsonl-closed-stdin-v1",
}


def hash_file(path: Path, expected_bytes: int, label: str) -> str:
    size = evaluation.integer(expected_bytes, 1, MAX_MODEL_ARTIFACT_BYTES, f"{label} size")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise evaluation.OutcomeError(f"{label} is unavailable or unsafe") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != size:
            raise evaluation.OutcomeError(f"{label} is not a single-link file of the declared size")
        digest = hashlib.sha256()
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            while True:
                chunk = handle.read(HASH_CHUNK_BYTES)
                if not chunk:
                    break
                digest.update(chunk)
        current = path.lstat()
        if current.st_dev != info.st_dev or current.st_ino != info.st_ino or not stat.S_ISREG(current.st_mode):
            raise evaluation.OutcomeError(f"{label} changed during hashing")
        return digest.hexdigest()
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def verify_model_artifact(path: Path, contract: dict[str, Any]) -> None:
    artifact = contract["artifact"]
    if artifact["kind"] != "single-file":
        raise evaluation.OutcomeError("single-file verification requires a single-file model artifact")
    if hash_file(path, artifact["bytes"], "shared model artifact") != artifact["sha256"]:
        raise evaluation.OutcomeError("shared model artifact does not match its exact contract digest")


def verify_model_artifact_manifest(root: Path, manifest: dict[str, Any], contract: dict[str, Any]) -> None:
    artifact = contract["artifact"]
    if artifact["kind"] != "directory-manifest":
        raise evaluation.OutcomeError("manifest verification requires a directory-manifest model artifact")
    try:
        root_info = root.lstat()
    except OSError as exc:
        raise evaluation.OutcomeError("model artifact directory is unavailable or unsafe") from exc
    if not stat.S_ISDIR(root_info.st_mode) or stat.S_ISLNK(root_info.st_mode):
        raise evaluation.OutcomeError("model artifact directory is unavailable or unsafe")
    manifest = evaluation.exact_fields(manifest, {
        "$schema", "schemaVersion", "kind", "artifactSha256", "fileCount", "totalBytes", "files",
        "authority", "boundary",
    }, "model artifact manifest")
    if (
        manifest["$schema"] != MODEL_ARTIFACT_MANIFEST_SCHEMA
        or manifest["schemaVersion"] != 1
        or manifest["authority"] != MODEL_ARTIFACT_MANIFEST_AUTHORITY
        or manifest["boundary"] != MODEL_ARTIFACT_MANIFEST_BOUNDARY
    ):
        raise evaluation.OutcomeError("model artifact manifest trust boundary is invalid")
    files = manifest["files"]
    if (
        manifest["kind"] != "directory" or not isinstance(files, list)
        or not 2 <= len(files) <= 4096 or manifest["fileCount"] != len(files)
    ):
        raise evaluation.OutcomeError("model artifact manifest shape is invalid")
    aggregate = evaluation.sha256(evaluation.canonical({"schemaVersion": 1, "kind": "directory", "files": files}))
    total = 0
    listed: set[str] = set()
    for index, item in enumerate(files):
        item = evaluation.exact_fields(item, {"relativePath", "bytes", "sha256"}, f"manifest file {index}")
        relative = evaluation.relative_path(item["relativePath"], f"manifest file {index} path")
        evaluation.valid_hash(item["sha256"], f"manifest file {index} digest")
        if item["relativePath"] in listed:
            raise evaluation.OutcomeError("model artifact manifest lists a duplicate file")
        listed.add(item["relativePath"])
        target = root
        for component_index, component in enumerate(relative.parts):
            target = target / component
            try:
                info = target.lstat()
            except OSError as exc:
                raise evaluation.OutcomeError("model artifact manifest contains an unavailable path") from exc
            if stat.S_ISLNK(info.st_mode):
                raise evaluation.OutcomeError("model artifact manifest contains a linked path")
            if component_index < len(relative.parts) - 1 and not stat.S_ISDIR(info.st_mode):
                raise evaluation.OutcomeError("model artifact manifest path traverses a non-directory")
        if hash_file(target, item["bytes"], f"manifest file {item['relativePath']}") != item["sha256"]:
            raise evaluation.OutcomeError("model artifact file does not match its exact manifest digest")
        total += item["bytes"]
    on_disk = set()
    for path in root.rglob("*"):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise evaluation.OutcomeError("model artifact directory contains a linked path")
        if stat.S_ISREG(info.st_mode):
            on_disk.add(path.relative_to(root).as_posix())
        elif not stat.S_ISDIR(info.st_mode):
            raise evaluation.OutcomeError("model artifact directory contains an unsupported filesystem object")
    if on_disk != listed:
        raise evaluation.OutcomeError("model artifact directory does not exactly match its manifest")
    if (
        aggregate != artifact["sha256"] or manifest["artifactSha256"] != artifact["sha256"]
        or total != manifest["totalBytes"] or total != artifact["bytes"]
        or len(files) != artifact["fileCount"]
    ):
        raise evaluation.OutcomeError("model artifact directory does not match its exact contract identity")


def canonical_arguments_sha256(arguments: Any, label: str = "model launch arguments") -> str:
    if (
        not isinstance(arguments, list) or not 1 <= len(arguments) <= 64
        or any(not isinstance(item, str) or not 1 <= len(item) <= 4096 for item in arguments)
    ):
        raise evaluation.OutcomeError(f"{label} are not bounded strings")
    return evaluation.sha256(evaluation.canonical(arguments))


def verify_launch_arguments(arguments: Any, contract: dict[str, Any]) -> None:
    runtime = contract["runtime"]
    if canonical_arguments_sha256(arguments) != runtime["launchArgumentsSha256"]:
        raise evaluation.OutcomeError("model launch arguments do not match the exact shared contract")
    bindings = LAUNCH_FLAG_BINDINGS.get(runtime["implementation"])
    if bindings is None:
        raise evaluation.OutcomeError("model launch arguments implementation is unsupported")
    for flag, key in bindings:
        expected = runtime[key]
        if flag not in arguments or arguments[arguments.index(flag) + 1:arguments.index(flag) + 2] != [str(expected)]:
            raise evaluation.OutcomeError("model launch arguments disagree with the shared runtime contract")


def verify_runtime_identity(system: Any, contract: dict[str, Any]) -> None:
    runtime = contract["runtime"]
    if runtime["implementation"] not in RUNTIME_EXECUTABLES:
        raise evaluation.OutcomeError("shared model runtime implementation is unsupported")
    if system.image_id(runtime["imageDigest"]) != runtime["imageDigest"]:
        raise evaluation.OutcomeError("shared model runtime image does not match its exact digest")
    executable = RUNTIME_EXECUTABLES[runtime["implementation"]]
    if system.executable_sha256(runtime["imageDigest"], executable) != runtime["executableSha256"]:
        raise evaluation.OutcomeError("shared model runtime executable does not match its exact digest")


def fresh_runtime_start(
    contract: dict[str, Any], *, container_id: Any, created_at: Any,
    props: Any = None, slots: Any = None, models: Any = None, metrics: Any = None,
) -> dict[str, Any]:
    implementation = contract["runtime"]["implementation"]
    if not isinstance(container_id, str) or CONTAINER_ID_RE.fullmatch(container_id) is None:
        raise evaluation.OutcomeError("fresh runtime container identity is invalid")
    evaluation.timestamp(created_at, "fresh runtime start time")
    if implementation == "llama.cpp":
        if models is not None or metrics is not None:
            raise evaluation.OutcomeError("fresh runtime evidence does not match its implementation")
        if not isinstance(props, dict):
            raise evaluation.OutcomeError("fresh runtime properties are invalid")
        template = props.get("chat_template")
        if not isinstance(template, str) or evaluation.sha256(template.encode("utf-8")) != contract["artifact"]["chatTemplateSha256"]:
            raise evaluation.OutcomeError("fresh runtime template differs from the shared model contract")
        settings = props.get("default_generation_settings")
        if not isinstance(settings, dict) or settings.get("n_ctx") != contract["runtime"]["contextWindow"]:
            raise evaluation.OutcomeError("fresh runtime context differs from the shared model contract")
        if not isinstance(slots, list) or any(
            not isinstance(item, dict) or item.get("n_past") not in (None, 0) or item.get("prompt") not in (None, "")
            for item in slots
        ):
            raise evaluation.OutcomeError("fresh runtime already contains cached prompt state")
    elif implementation == "vllm":
        if props is not None or slots is not None:
            raise evaluation.OutcomeError("fresh runtime evidence does not match its implementation")
        if not isinstance(models, dict) or models.get("object") != "list" or not isinstance(models.get("data"), list):
            raise evaluation.OutcomeError("fresh runtime model listing is invalid")
        entries = models["data"]
        if len(entries) != 1 or not isinstance(entries[0], dict):
            raise evaluation.OutcomeError("fresh runtime does not serve exactly the shared model")
        entry = entries[0]
        if entry.get("id") != contract["modelId"] or entry.get("max_model_len") != contract["runtime"]["contextWindow"]:
            raise evaluation.OutcomeError("fresh runtime model identity differs from the shared model contract")
        if not isinstance(metrics, str) or len(metrics) > 4194304:
            raise evaluation.OutcomeError("fresh runtime metrics are invalid")
        for line in metrics.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name = line.split("{", 1)[0].split(" ", 1)[0]
            if name.endswith(("prompt_tokens_total", "generation_tokens_total", "request_success_total")):
                value = line.rsplit(" ", 1)[-1]
                try:
                    observed = float(value)
                except ValueError as exc:
                    raise evaluation.OutcomeError("fresh runtime metrics are unreadable") from exc
                if observed != 0.0:
                    raise evaluation.OutcomeError("fresh runtime already contains served request state")
    else:
        raise evaluation.OutcomeError("fresh runtime verification is not supported for this implementation")
    return {
        "operation": FRESH_RUNTIME_OPERATION,
        "implementation": implementation,
        "containerId": container_id,
        "imageDigest": contract["runtime"]["imageDigest"],
        "createdAt": created_at,
        "contextWindow": contract["runtime"]["contextWindow"],
        "chatTemplateSha256": contract["artifact"]["chatTemplateSha256"],
        "cachedPromptObserved": False,
    }


MAX_TRANSCRIPT_BYTES = 16 * 1024 * 1024


def extract_codex_execution(
    events_text: str, budgets: dict[str, Any], *, latency_ms: int, exit_code: Any,
    model_requests: Any, external_writes: int, real_backend: bool, real_tools: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(events_text, str) or not events_text.strip() or len(events_text) > MAX_TRANSCRIPT_BYTES:
        raise evaluation.OutcomeError("codex transcript is empty or oversized")
    input_tokens = 0
    output_tokens = 0
    turns_completed = 0
    completed_tool_items: set[str] = set()
    event_type_counts: dict[str, int] = {}
    item_type_counts: dict[str, int] = {}
    operator_attention_requests = 0
    approval_requests = 0
    scope_expansion_requests = 0
    safety_blocks = 0
    interruptions = 0
    failed = False

    def interaction_signals(*labels: str) -> tuple[bool, bool, bool, bool, bool]:
        normalized = " ".join(label.lower().replace("-", "_").replace(".", "_") for label in labels)
        approval = "approval" in normalized or "permission_request" in normalized
        scope = "scope_expansion" in normalized or "authority_expansion" in normalized
        attention = approval or scope or any(token in normalized for token in ("operator_input", "user_input_required", "human_input"))
        safety = any(token in normalized for token in ("safety_block", "policy_block", "permission_denied", "authority_denied"))
        interrupted = any(token in normalized for token in ("interrupted", "cancelled"))
        return attention, approval, scope, safety, interrupted

    for line in events_text.splitlines():
        line = line.strip()
        if not line:
            continue
        if not line.startswith("{"):
            continue
        event = evaluation.parse_json(line.encode("utf-8"), "codex transcript event")
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            raise evaluation.OutcomeError("codex transcript event is unstructured")
        event_type_counts[event["type"]] = event_type_counts.get(event["type"], 0) + 1
        labels = [event["type"]]
        if event["type"] == "item.completed":
            item = event.get("item")
            if not isinstance(item, dict) or not isinstance(item.get("type"), str):
                raise evaluation.OutcomeError("codex completed item is unstructured")
            item_type = item["type"]
            item_type_counts[item_type] = item_type_counts.get(item_type, 0) + 1
            labels.append(item_type)
            if item_type not in {"agent_message", "reasoning", "error"}:
                item_id = item.get("id")
                if not isinstance(item_id, str) or not item_id or len(item_id) > 256:
                    raise evaluation.OutcomeError("codex completed tool item has no bounded identity")
                if item_id in completed_tool_items:
                    raise evaluation.OutcomeError("codex completed tool item is duplicated")
                completed_tool_items.add(item_id)
        elif event["type"] == "turn.completed":
            usage = event.get("usage")
            if not isinstance(usage, dict):
                raise evaluation.OutcomeError("codex turn is missing measured usage")
            for key in ("input_tokens", "output_tokens", "reasoning_output_tokens"):
                value = usage.get(key, 0)
                if type(value) is not int or value < 0:
                    raise evaluation.OutcomeError("codex usage is not measured integers")
            input_tokens += usage["input_tokens"]
            output_tokens += usage.get("output_tokens", 0) + usage.get("reasoning_output_tokens", 0)
            turns_completed += 1
        elif event["type"] in {"turn.failed", "error"}:
            failed = True
        attention, approval, scope, safety, interrupted = interaction_signals(*labels)
        operator_attention_requests += int(attention)
        approval_requests += int(approval)
        scope_expansion_requests += int(scope)
        interruptions += int(interrupted)
        safety_blocks = min(1, safety_blocks + int(safety))
    if turns_completed == 0:
        failed = True
    if model_requests is None:
        model_requests = turns_completed
    status = "safety-blocked" if safety_blocks else "completed" if not failed and exit_code == 0 else "failed"
    interaction = {
        "schemaVersion": 1,
        "mode": "single-admission-noninteractive",
        "source": "codex-jsonl-closed-stdin-v1",
        "observationSha256": evaluation.sha256(evaluation.canonical({
            "eventTypes": dict(sorted(event_type_counts.items())),
            "itemTypes": dict(sorted(item_type_counts.items())),
            "turnsCompleted": turns_completed,
            "inputClosedAfterInitialRequest": True,
        })),
        "operatorInputsAfterAdmission": 0,
        "operatorAttentionRequests": operator_attention_requests,
        "approvalRequests": approval_requests,
        "scopeExpansionRequests": scope_expansion_requests,
        "safetyBlocks": safety_blocks,
        "interruptions": interruptions,
        "complete": True,
        "workerSelfReported": False,
        "boundary": INTERACTION_BOUNDARY,
    }
    execution = {
        "status": status,
        "realBackend": real_backend,
        "realTools": real_tools,
        "exitCode": exit_code if type(exit_code) is int and 0 <= exit_code <= 255 else None,
        "latencyMs": evaluation.integer(latency_ms, 0, 31536000000, "outcome run latency"),
        "operatorInterventions": interaction["operatorInputsAfterAdmission"],
        "operatorAttentionRequests": interaction["operatorAttentionRequests"],
        "approvalRequests": interaction["approvalRequests"],
        "scopeExpansionRequests": interaction["scopeExpansionRequests"],
        "interruptions": interaction["interruptions"],
        "toolCalls": len(completed_tool_items),
        "modelRequests": evaluation.integer(model_requests, 0, 10000000, "outcome run model requests"),
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "externalWrites": evaluation.integer(external_writes, 0, 1000000, "outcome run external writes"),
    }
    ceilings = (
        ("inputTokens", input_tokens), ("outputTokens", output_tokens),
        ("modelRequests", model_requests), ("externalWrites", external_writes),
    )
    for key, observed in ceilings:
        if observed > budgets[key]:
            raise evaluation.OutcomeError(f"outcome run exceeded its admitted {key} budget")
    if latency_ms > budgets["wallTimeSeconds"] * 1000:
        raise evaluation.OutcomeError("outcome run exceeded its admitted wall-time budget")
    return execution, interaction


def admitted_task_binding(admission: dict[str, Any]) -> dict[str, Any]:
    return {
        "comparisonLane": admission["comparisonLane"],
        "corpusSha256": admission["corpusSha256"],
        "journeySha256": admission["journeySha256"],
        "taskSpecificationSha256": admission["taskSpecificationSha256"],
        "taskAdmissionSha256": evaluation.sha256(evaluation.canonical(admission)),
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


def validate_interaction(value: Any, backend: str) -> dict[str, Any]:
    value = evaluation.exact_fields(value, {
        "schemaVersion", "mode", "source", "observationSha256", "operatorInputsAfterAdmission",
        "operatorAttentionRequests", "approvalRequests", "scopeExpansionRequests", "safetyBlocks",
        "interruptions", "complete", "workerSelfReported", "boundary",
    }, f"{backend} interaction telemetry")
    expected_prefix = "pixel-" if backend == "pixel" else "codex-"
    if (
        value["schemaVersion"] != 1 or value["mode"] != "single-admission-noninteractive"
        or value["source"] not in INTERACTION_SOURCES or not value["source"].startswith(expected_prefix)
        or value["complete"] is not True or value["workerSelfReported"] is not False
        or value["boundary"] != INTERACTION_BOUNDARY
    ):
        raise evaluation.OutcomeError(f"{backend} interaction telemetry is incomplete or has the wrong source")
    evaluation.valid_hash(value["observationSha256"], f"{backend} interaction observation")
    for field, maximum in (
        ("operatorInputsAfterAdmission", 1000000), ("operatorAttentionRequests", 1000000),
        ("approvalRequests", 1000000), ("scopeExpansionRequests", 1000000),
        ("safetyBlocks", 1), ("interruptions", 1000000),
    ):
        evaluation.integer(value[field], 0, maximum, f"{backend} interaction {field}")
    if value["approvalRequests"] > value["operatorAttentionRequests"] or value["scopeExpansionRequests"] > value["operatorAttentionRequests"]:
        raise evaluation.OutcomeError(f"{backend} interaction sub-count exceeds total attention requests")
    return value


def assemble_run(
    *,
    admission: dict[str, Any],
    backend: str,
    run_id: str,
    started_at: str,
    finished_at: str,
    execution: dict[str, Any],
    interaction: dict[str, Any],
    execution_identity: dict[str, Any],
    evidence: list[dict[str, Any]],
    assertions: list[dict[str, Any]],
    dimensions: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    safety_findings: list[dict[str, Any]],
    verifier: dict[str, Any],
    authority: dict[str, Any],
) -> dict[str, Any]:
    if backend not in {"pixel", "codex"}:
        raise evaluation.OutcomeError("outcome run backend is unknown")
    if not isinstance(run_id, str) or evaluation.RUN_ID_RE.fullmatch(run_id) is None:
        raise evaluation.OutcomeError("outcome run identity is invalid")
    authority = evaluation.exact_fields(authority, set(evaluation.BOUNDARY_FLAGS), "outcome run authority")
    if any(type(authority[key]) is not bool for key in evaluation.BOUNDARY_FLAGS):
        raise evaluation.OutcomeError("outcome run authority flags must be measured booleans")
    interaction = validate_interaction(interaction, backend)
    if (
        execution.get("operatorInterventions") != interaction["operatorInputsAfterAdmission"]
        or execution.get("operatorAttentionRequests") != interaction["operatorAttentionRequests"]
        or execution.get("approvalRequests") != interaction["approvalRequests"]
        or execution.get("scopeExpansionRequests") != interaction["scopeExpansionRequests"]
        or execution.get("interruptions") != interaction["interruptions"]
        or (execution.get("status") == "safety-blocked") != (interaction["safetyBlocks"] == 1)
    ):
        raise evaluation.OutcomeError("outcome execution differs from its mechanically observed interaction telemetry")
    return {
        "$schema": evaluation.RUN_SCHEMA,
        "schemaVersion": 1,
        "operation": "pixel-portal-outcome-run",
        "runId": run_id,
        "journeyId": admission["journeyId"],
        "backend": backend,
        "synthetic": False,
        "selfGraded": False,
        "startedAt": started_at,
        "finishedAt": finished_at,
        "task": admitted_task_binding(admission),
        "executionIdentity": execution_identity,
        "execution": execution,
        "interaction": interaction,
        "evidence": evidence,
        "assertions": assertions,
        "dimensions": dimensions,
        "artifacts": artifacts,
        "safetyFindings": safety_findings,
        "verifier": verifier,
        "authority": authority,
        "boundary": evaluation.RUN_BOUNDARY,
    }
