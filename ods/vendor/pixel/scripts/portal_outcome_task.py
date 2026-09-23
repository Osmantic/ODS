#!/usr/bin/env python3
"""Admit one private backend-neutral task for paired Pixel/Codex execution."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any

import portal_outcome_evaluation as evaluation
import portal_outcome_verifier as verifier_engine


TASK_SCHEMA = "https://osmantic.com/pixel/schemas/portal-outcome-task-v1.schema.json"
MODEL_SCHEMA = "https://osmantic.com/pixel/schemas/portal-outcome-model-contract-v1.schema.json"
INFERENCE_SCHEMA = "https://osmantic.com/pixel/schemas/portal-outcome-inference-contract-v1.schema.json"
TOOL_POLICY_SCHEMA = "https://osmantic.com/pixel/schemas/portal-outcome-tool-policy-v1.schema.json"
ENVIRONMENT_SCHEMA = "https://osmantic.com/pixel/schemas/portal-outcome-environment-v1.schema.json"
RESEARCH_FIXTURE_SCHEMA = "https://osmantic.com/pixel/schemas/portal-outcome-research-fixture-v1.schema.json"
TASK_BOUNDARY = (
    "Private backend-neutral task binding only. It defines identical admitted work for paired "
    "evaluation but grants neither backend execution, model, network, credential, external-effect, "
    "scope-expansion, safety-relaxation, completion, publication, deployment, acceptance, or "
    "promotion authority."
)
ADMISSION_BOUNDARY = (
    "Content-free admission of one exact backend-neutral task. Private task content and paths remain "
    "in owner custody. Admission proves binding and policy consistency only and grants no execution, "
    "provider call, credential use, external effect, scope expansion, safety relaxation, completion, "
    "publication, deployment, acceptance, or promotion authority."
)
TASK_ID_RE = re.compile(r"^outcometask-[0-9]{13}-[a-f0-9]{12}$")
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,127}$")
QUANTIZATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
IMAGE_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
EXECUTABLE_PATH_RE = re.compile(r"^/(?:[A-Za-z0-9._+-]+/)*[A-Za-z0-9._+-]+$")
LANES = {"product-default", "same-model-harness"}
PROFILES = {"assistant", "scout", "builder", "researcher", "data-lab", "controller"}
DATA_CLASSES = {"public", "internal", "confidential", "private"}
EFFECTS = {"none", "read-only", "local-state-change", "external-write"}
CAPABILITIES = {
    "reasoning", "filesystem-read", "filesystem-write", "process-execution", "public-web", "browser",
    "email-read", "calendar-read", "calendar-write", "github-read", "github-write", "fleet-read",
    "long-running-goals", "local-model", "remote-model", "artifact-production",
}
ROUTES = {"local-only", "brokered-public", "sanitized-remote", "authorized-provider"}
MEDIA_TYPES = {"text/plain", "application/json", "application/x-tar", "application/octet-stream"}
AUTHORITY_FIELDS = {
    "grantsExecution", "grantsProviderCall", "grantsCredentialUse", "grantsExternalEffect",
    "grantsScopeExpansion", "grantsSafetyRelaxation", "grantsCompletion",
}
BUDGET_LIMITS = {
    "wallTimeSeconds": (1, 2592000), "operatorInterventions": (0, 10000),
    "modelRequests": (1, 1000000), "inputTokens": (1, 1000000000000),
    "outputTokens": (1, 1000000000000), "artifactBytes": (1, 1073741824),
    "externalWrites": (0, 10000),
}
CAPABILITY_EFFECTS = {
    "filesystem-write": "local-state-change", "calendar-write": "external-write", "github-write": "external-write",
}
REMOTE_CAPABILITIES = {"remote-model"}
NETWORK_CAPABILITIES = {"public-web", "browser", "email-read", "calendar-read", "calendar-write", "github-read", "github-write", "fleet-read"}
MODEL_BOUNDARY = (
    "Private exact shared-model identity for controlled harness comparison only. It requires fresh "
    "cross-run-isolated runtimes and grants no model start, provider call, network, credential, tool, "
    "execution, completion, publication, deployment, acceptance, or promotion authority."
)
INFERENCE_BOUNDARY = (
    "Private exact shared-inference identity for controlled harness comparison only. Exact request-boundary "
    "enforcement and run evidence are required; this contract grants no inference, tool, execution, "
    "completion, publication, deployment, acceptance, or promotion authority."
)
TOOL_POLICY_BOUNDARY = (
    "Backend-neutral least-authority tool envelope for one paired outcome task only. Tools operate only "
    "through the declared disposable workspace or typed brokers; this policy grants no host access, ambient "
    "credential, external effect, merge, deployment, policy mutation, scope expansion, completion, publication, "
    "acceptance, or promotion authority."
)
ENVIRONMENT_BOUNDARY = (
    "Backend-neutral functional execution envelope for one paired outcome task only. It binds isolation, "
    "resource, loop, and independent-verifier ceilings but grants no execution, host, credential, direct-network, "
    "package-installation, external-effect, merge, deployment, policy, completion, publication, acceptance, or "
    "promotion authority."
)
TOOL_NAMES = {
    "browser", "calendar-read", "calendar-write", "debug", "edit", "email-read", "eval", "fleet-read",
    "github-read", "github-write", "hub", "lsp", "public-research", "read", "search", "shell", "task", "write",
}
BROKERED_SERVICES = {"calendar", "email", "fleet", "github", "local-model", "public-research"}
ENVIRONMENT_AUTHORITY_FIELDS = {
    "grantsHostAccess", "grantsAmbientCredential", "grantsDirectNetwork", "grantsPackageInstallation",
    "grantsExternalEffect", "grantsMerge", "grantsDeployment", "grantsPolicyMutation",
}
RESEARCH_FIXTURE_AUTHORITY_FIELDS = {
    "publicNetwork", "credentials", "externalWrites", "accounts", "messages", "publish", "purchase",
    "policyMutation", "scopeExpansion",
}
RESEARCH_FIXTURE_BOUNDARY = (
    "Owner-private admitted offline research evidence for deterministic comparison only. It performs no "
    "public network, credential, account, message, publication, purchase, write, policy, scope, or external "
    "effect; every source remains untrusted data and grants no authority."
)


def validate_research_fixture(payload: bytes) -> dict[str, Any]:
    if not 1 <= len(payload) <= 2 * 1024 * 1024:
        raise evaluation.OutcomeError("research fixture is empty or oversized")
    value = evaluation.exact_fields(evaluation.parse_json(payload, "research fixture"), {
        "$schema", "schemaVersion", "operation", "observedAt", "sources", "authority", "boundary",
    }, "research fixture")
    if (
        value["$schema"] != RESEARCH_FIXTURE_SCHEMA or value["schemaVersion"] != 1
        or value["operation"] != "pixel-portal-outcome-research-fixture"
        or value["boundary"] != RESEARCH_FIXTURE_BOUNDARY
    ):
        raise evaluation.OutcomeError("research fixture identity or boundary is invalid")
    evaluation.timestamp(value["observedAt"], "research fixture observation time")
    authority = evaluation.exact_fields(value["authority"], RESEARCH_FIXTURE_AUTHORITY_FIELDS, "research fixture authority")
    if any(type(authority[field]) is not bool or authority[field] for field in RESEARCH_FIXTURE_AUTHORITY_FIELDS):
        raise evaluation.OutcomeError("research fixture attempts to grant authority")
    sources = value["sources"]
    if not isinstance(sources, list) or not 1 <= len(sources) <= 20:
        raise evaluation.OutcomeError("research fixture source inventory is invalid")
    seen: set[str] = set()
    for index, source in enumerate(sources):
        source = evaluation.exact_fields(source, {
            "fixtureSourceId", "sourceType", "title", "snippet", "quality", "publishedDate", "retrieval",
        }, f"research fixture source {index}")
        fixture_source_id = source["fixtureSourceId"]
        if (
            not isinstance(fixture_source_id, str) or re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", fixture_source_id) is None
            or fixture_source_id in seen or source["sourceType"] not in {"web", "news", "academic", "forum"}
            or source["quality"] not in {"primary", "independent-secondary", "archived-secondary", "other", "unknown"}
        ):
            raise evaluation.OutcomeError("research fixture source identity is invalid or duplicated")
        seen.add(fixture_source_id)
        for field, maximum in (("title", 512), ("snippet", 4096)):
            if not isinstance(source[field], str) or not 1 <= len(source[field].encode("utf-8")) <= maximum:
                raise evaluation.OutcomeError(f"research fixture source {field} is invalid")
        published = source["publishedDate"]
        if published is not None:
            if not isinstance(published, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", published) is None:
                raise evaluation.OutcomeError("research fixture publication date is invalid")
            evaluation.timestamp(f"{published}T00:00:00Z", "research fixture publication date")
        retrieval = source["retrieval"]
        if isinstance(retrieval, dict) and retrieval.get("status") == "fetched":
            retrieval = evaluation.exact_fields(retrieval, {"status", "content"}, f"research fixture source {index} retrieval")
            if not isinstance(retrieval["content"], str) or not 1 <= len(retrieval["content"].encode("utf-8")) <= 1024 * 1024:
                raise evaluation.OutcomeError("research fixture fetched content is invalid")
        elif isinstance(retrieval, dict) and retrieval.get("status") == "rejected":
            retrieval = evaluation.exact_fields(retrieval, {"status", "reason"}, f"research fixture source {index} retrieval")
            if retrieval["reason"] not in {"policy", "network", "size", "media", "integrity"}:
                raise evaluation.OutcomeError("research fixture rejection reason is invalid")
        else:
            raise evaluation.OutcomeError("research fixture retrieval is invalid")
    return value


def validate_environment(payload: bytes, *, tool_policy: dict[str, Any]) -> dict[str, Any]:
    if len(payload) > evaluation.MAX_JSON_BYTES:
        raise evaluation.OutcomeError("execution environment is oversized")
    value = evaluation.parse_json(payload, "execution environment")
    value = evaluation.exact_fields(value, {
        "$schema", "schemaVersion", "operation", "platform", "isolation", "limits", "verifier", "authority", "boundary",
    }, "execution environment")
    if (
        value["$schema"] != ENVIRONMENT_SCHEMA or value["schemaVersion"] != 1
        or value["operation"] != "pixel-portal-outcome-environment" or value["boundary"] != ENVIRONMENT_BOUNDARY
    ):
        raise evaluation.OutcomeError("execution environment identity or boundary is invalid")
    platform = evaluation.exact_fields(value["platform"], {
        "operatingSystem", "architecture", "distribution", "locale", "timeZone",
    }, "execution environment platform")
    if (
        platform["operatingSystem"] != "linux" or platform["architecture"] not in {"amd64", "arm64"}
        or platform["distribution"] != "debian-12" or platform["locale"] != "C.UTF-8" or platform["timeZone"] != "UTC"
    ):
        raise evaluation.OutcomeError("execution environment platform is not the admitted deterministic Linux envelope")
    isolation = evaluation.exact_fields(value["isolation"], {
        "workspace", "controlFiles", "directNetwork", "packageInstallation", "inheritedEnvironment",
        "inheritedFileDescriptors", "crossRunState",
    }, "execution environment isolation")
    expected_workspace = "fresh-disposable-read-write" if tool_policy["workspace"] == "disposable-read-write" else "fresh-read-only"
    if isolation["workspace"] != expected_workspace or isolation["controlFiles"] != "inert" or any(
        isolation[key] is not False for key in (
            "directNetwork", "packageInstallation", "inheritedEnvironment", "inheritedFileDescriptors", "crossRunState",
        )
    ):
        raise evaluation.OutcomeError("execution environment isolation differs from the exact tool envelope")
    limits = evaluation.exact_fields(value["limits"], {
        "maxIterations", "maxToolCalls", "maxConcurrentSubagents", "maxCpuCores", "maxMemoryMiB",
        "maxDiskBytes", "maxNetworkBytes", "maxFailures", "noProgressLimit", "maxPids",
    }, "execution environment limits")
    for key, minimum, maximum in (
        ("maxIterations", 1, 1000), ("maxToolCalls", 1, 1000000), ("maxConcurrentSubagents", 1, 32),
        ("maxCpuCores", 1, 128), ("maxMemoryMiB", 256, 1048576),
        ("maxDiskBytes", 1048576, 1099511627776), ("maxNetworkBytes", 0, 10737418240),
        ("maxFailures", 1, 1000), ("noProgressLimit", 1, 100), ("maxPids", 32, 1048576),
    ):
        evaluation.integer(limits[key], minimum, maximum, f"execution environment {key}")
    if limits["maxConcurrentSubagents"] != tool_policy["maximumSubagents"]:
        raise evaluation.OutcomeError("execution environment subagent ceiling differs from the tool policy")
    verifier = evaluation.exact_fields(value["verifier"], {
        "imageDigest", "allowedExecutables", "maxChecks", "maxRuntimeSeconds", "maxOutputBytes", "network",
    }, "execution environment verifier")
    if not isinstance(verifier["imageDigest"], str) or IMAGE_DIGEST_RE.fullmatch(verifier["imageDigest"]) is None:
        raise evaluation.OutcomeError("execution environment verifier image is not digest pinned")
    executables = verifier["allowedExecutables"]
    if (
        not isinstance(executables, list) or not 1 <= len(executables) <= 32 or executables != sorted(executables)
        or len(set(executables)) != len(executables)
        or any(not isinstance(item, str) or EXECUTABLE_PATH_RE.fullmatch(item) is None for item in executables)
    ):
        raise evaluation.OutcomeError("execution environment verifier executables are not canonical absolute paths")
    evaluation.integer(verifier["maxChecks"], 1, 16, "execution environment verifier maxChecks")
    evaluation.integer(verifier["maxRuntimeSeconds"], 1, 3600, "execution environment verifier maxRuntimeSeconds")
    evaluation.integer(verifier["maxOutputBytes"], 1, 16777216, "execution environment verifier maxOutputBytes")
    if verifier["network"] != "none":
        raise evaluation.OutcomeError("execution environment verifier must be network isolated")
    authority = evaluation.exact_fields(value["authority"], ENVIRONMENT_AUTHORITY_FIELDS, "execution environment authority")
    if any(type(authority[key]) is not bool or authority[key] for key in ENVIRONMENT_AUTHORITY_FIELDS):
        raise evaluation.OutcomeError("execution environment attempts to grant authority")
    return value


def validate_tool_policy(payload: bytes, capabilities: list[str]) -> dict[str, Any]:
    if len(payload) > evaluation.MAX_JSON_BYTES:
        raise evaluation.OutcomeError("tool policy is oversized")
    value = evaluation.parse_json(payload, "tool policy")
    value = evaluation.exact_fields(value, {
        "$schema", "schemaVersion", "operation", "workspace", "tools", "brokeredServices",
        "maximumSubagents", "hostAccess", "ambientCredentials", "externalEffects", "mergeAuthority",
        "deployAuthority", "policyMutation", "boundary",
    }, "tool policy")
    if (
        value["$schema"] != TOOL_POLICY_SCHEMA or value["schemaVersion"] != 1
        or value["operation"] != "pixel-portal-outcome-tool-policy"
        or value["workspace"] not in {"read-only", "disposable-read-write"}
        or value["boundary"] != TOOL_POLICY_BOUNDARY
    ):
        raise evaluation.OutcomeError("tool policy identity or boundary is invalid")
    tools, services = value["tools"], value["brokeredServices"]
    if (
        not isinstance(tools, list) or not 1 <= len(tools) <= 24 or tools != sorted(tools)
        or len(set(tools)) != len(tools) or any(not isinstance(item, str) or item not in TOOL_NAMES for item in tools)
        or not isinstance(services, list) or len(services) > 8 or services != sorted(services)
        or len(set(services)) != len(services) or any(not isinstance(item, str) or item not in BROKERED_SERVICES for item in services)
    ):
        raise evaluation.OutcomeError("tool policy tools or services are not canonical")
    evaluation.integer(value["maximumSubagents"], 1, 32, "tool policy maximumSubagents")
    for key in ("hostAccess", "ambientCredentials", "externalEffects", "mergeAuthority", "deployAuthority", "policyMutation"):
        if value[key] is not False:
            raise evaluation.OutcomeError("tool policy attempts to grant ambient or external authority")
    if "read" not in tools or "search" not in tools:
        raise evaluation.OutcomeError("tool policy omits the baseline read and search tools")
    if any(item in tools for item in {"write", "edit"}) and value["workspace"] != "disposable-read-write":
        raise evaluation.OutcomeError("tool policy grants mutation in a read-only workspace")
    if any(item in tools for item in {"shell", "eval", "lsp", "debug"}) != ("process-execution" in capabilities):
        raise evaluation.OutcomeError("tool policy process surface differs from task capabilities")
    expected_services = set()
    if "local-model" in capabilities:
        expected_services.add("local-model")
    if "public-web" in capabilities:
        expected_services.add("public-research")
    for capability, service in (("email-read", "email"), ("calendar-read", "calendar"), ("calendar-write", "calendar"), ("github-read", "github"), ("github-write", "github"), ("fleet-read", "fleet")):
        if capability in capabilities:
            expected_services.add(service)
    if set(services) != expected_services:
        raise evaluation.OutcomeError("tool policy broker services differ from task capabilities")
    for capability, tool in (("browser", "browser"), ("email-read", "email-read"), ("calendar-read", "calendar-read"), ("calendar-write", "calendar-write"), ("github-read", "github-read"), ("github-write", "github-write"), ("fleet-read", "fleet-read")):
        if (capability in capabilities) != (tool in tools):
            raise evaluation.OutcomeError("tool policy typed tools differ from task capabilities")
    if ("public-web" in capabilities) != ("public-research" in tools):
        raise evaluation.OutcomeError("tool policy research tool differs from task capabilities")
    return value


def inference_policy_value(value: dict[str, Any]) -> dict[str, Any]:
    sampling, request = value["sampling"], value["request"]
    return {
        "enforcement": "exact-request-boundary-v1",
        "wireApi": request["wireApi"],
        "temperaturePermille": sampling["temperaturePermille"],
        "topPPermille": sampling["topPPermille"],
        "topK": sampling["topK"],
        "minPPermille": sampling["minPPermille"],
        "repeatPenaltyPermille": sampling["repeatPenaltyPermille"],
        "seed": sampling["seed"],
        "reasoningEffort": sampling["reasoningEffort"],
        "reasoningVisibility": sampling["reasoningVisibility"],
        "stream": request["stream"],
        "maxOutputTokens": request["maxOutputTokens"],
        "toolEncoding": request["toolEncoding"],
    }


def inference_policy_sha256(value: dict[str, Any]) -> str:
    encoded = json.dumps(inference_policy_value(value), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def resolved_reference(task_path: Path, value: Any, label: str) -> tuple[dict[str, Any], bytes]:
    reference = evaluation.exact_fields(value, {"relativePath", "sha256", "bytes", "mediaType"}, label)
    if not isinstance(reference["mediaType"], str) or reference["mediaType"] not in MEDIA_TYPES:
        raise evaluation.OutcomeError(f"{label} media type is invalid")
    relative = evaluation.relative_path(reference["relativePath"], f"{label} path")
    current = task_path.parent
    for part in relative.parts:
        current = current / part
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                raise evaluation.OutcomeError(f"{label} path contains a link")
        except OSError as exc:
            raise evaluation.OutcomeError(f"{label} is unavailable") from exc
    payload = evaluation.read_bytes(current, limit=evaluation.MAX_EVIDENCE_BYTES)
    digest = evaluation.valid_hash(reference["sha256"], f"{label} digest")
    size = evaluation.integer(reference["bytes"], 1, evaluation.MAX_EVIDENCE_BYTES, f"{label} size")
    if len(payload) != size or evaluation.sha256(payload) != digest:
        raise evaluation.OutcomeError(f"{label} does not match its exact size and digest")
    return reference, payload


def validate_model_contract(payload: bytes) -> None:
    if len(payload) > evaluation.MAX_JSON_BYTES:
        raise evaluation.OutcomeError("shared model contract is oversized")
    value = evaluation.parse_json(payload, "shared model contract")
    value = evaluation.exact_fields(value, {
        "$schema", "schemaVersion", "operation", "modelId", "artifact", "runtime", "authority", "boundary",
    }, "shared model contract")
    if (
        value["$schema"] != MODEL_SCHEMA or value["schemaVersion"] != 1
        or value["operation"] != "pixel-portal-outcome-model-contract"
        or not isinstance(value["modelId"], str) or MODEL_ID_RE.fullmatch(value["modelId"]) is None
        or value["boundary"] != MODEL_BOUNDARY
    ):
        raise evaluation.OutcomeError("shared model contract identity or boundary is invalid")
    artifact = evaluation.exact_fields(value["artifact"], {
        "kind", "sha256", "bytes", "fileCount", "format", "quantization", "tokenizerSha256",
        "chatTemplateSha256", "metadataSha256",
    }, "shared model artifact")
    evaluation.valid_hash(artifact["sha256"], "shared model artifact sha256")
    evaluation.integer(artifact["bytes"], 1, 1099511627776, "shared model artifact bytes")
    evaluation.integer(artifact["fileCount"], 1, 4096, "shared model artifact file count")
    if not isinstance(artifact["kind"], str) or artifact["kind"] not in {"single-file", "directory-manifest"}:
        raise evaluation.OutcomeError("shared model artifact kind is invalid")
    if artifact["kind"] == "single-file":
        if artifact["fileCount"] != 1:
            raise evaluation.OutcomeError("single-file model artifact must declare exactly one file")
        for key in ("tokenizerSha256", "chatTemplateSha256", "metadataSha256"):
            evaluation.valid_hash(artifact[key], f"shared model artifact {key}")
    else:
        if artifact["fileCount"] < 2:
            raise evaluation.OutcomeError("directory-manifest model artifact must declare its exact file count")
        for key in ("tokenizerSha256", "chatTemplateSha256", "metadataSha256"):
            if artifact[key] is not None:
                evaluation.valid_hash(artifact[key], f"shared model artifact {key}")
    if not isinstance(artifact["format"], str) or artifact["format"] not in {"gguf", "safetensors"} or not isinstance(artifact["quantization"], str) or QUANTIZATION_RE.fullmatch(artifact["quantization"]) is None:
        raise evaluation.OutcomeError("shared model artifact format or quantization is invalid")
    runtime = evaluation.exact_fields(value["runtime"], {
        "implementation", "imageDigest", "executableSha256", "launchArgumentsSha256", "protocol",
        "contextWindow", "parallelSlots", "resources", "runtimeIsolation", "restartPolicy", "crossRunStateAllowed",
    }, "shared model runtime")
    if (
        not isinstance(runtime["implementation"], str) or runtime["implementation"] not in {"llama.cpp", "vllm"}
        or not isinstance(runtime["imageDigest"], str) or IMAGE_DIGEST_RE.fullmatch(runtime["imageDigest"]) is None
        or runtime["protocol"] not in {"openai-responses-v1", "openai-chat-completions-v1"}
        or runtime["runtimeIsolation"] != "fresh-per-run"
        or runtime["restartPolicy"] != "no"
        or runtime["crossRunStateAllowed"] is not False
    ):
        raise evaluation.OutcomeError("shared model runtime is not exact and cross-run isolated")
    evaluation.valid_hash(runtime["executableSha256"], "shared model executable digest")
    evaluation.valid_hash(runtime["launchArgumentsSha256"], "shared model launch digest")
    evaluation.integer(runtime["contextWindow"], 4096, 1048576, "shared model context window")
    evaluation.integer(runtime["parallelSlots"], 1, 64, "shared model parallel slots")
    resources = evaluation.exact_fields(runtime["resources"], {
        "acceleratorClass", "acceleratorCount", "cpuCores", "memoryMiB", "sharedMemoryMiB",
        "tmpfsMiB", "cacheMiB", "pidsLimit",
    }, "shared model runtime resources")
    if resources["acceleratorClass"] not in {"cpu", "nvidia"}:
        raise evaluation.OutcomeError("shared model accelerator class is invalid")
    for key, minimum, maximum in (
        ("acceleratorCount", 0, 8), ("cpuCores", 1, 256), ("memoryMiB", 1024, 1048576),
        ("sharedMemoryMiB", 64, 1048576), ("tmpfsMiB", 16, 1048576),
        ("cacheMiB", 16, 1048576), ("pidsLimit", 64, 1048576),
    ):
        evaluation.integer(resources[key], minimum, maximum, f"shared model runtime {key}")
    if (resources["acceleratorClass"] == "cpu") != (resources["acceleratorCount"] == 0):
        raise evaluation.OutcomeError("shared model accelerator class and count disagree")
    if runtime["implementation"] == "vllm" and (
        resources["sharedMemoryMiB"] < 1024 or resources["cacheMiB"] < 1024
    ):
        raise evaluation.OutcomeError("shared vLLM resources require private shared memory and cache")
    authority = evaluation.exact_fields(value["authority"], {
        "grantsModelStart", "grantsProviderCall", "grantsNetwork", "grantsCredentialUse", "grantsExecution", "grantsCompletion",
    }, "shared model authority")
    if any(type(item) is not bool for item in authority.values()) or any(authority.values()):
        raise evaluation.OutcomeError("shared model contract attempts to grant authority")


def validate_inference_contract(payload: bytes) -> None:
    if len(payload) > evaluation.MAX_JSON_BYTES:
        raise evaluation.OutcomeError("shared inference contract is oversized")
    value = evaluation.parse_json(payload, "shared inference contract")
    value = evaluation.exact_fields(value, {
        "$schema", "schemaVersion", "operation", "sampling", "request", "authority", "boundary",
    }, "shared inference contract")
    if (
        value["$schema"] != INFERENCE_SCHEMA or value["schemaVersion"] != 1
        or value["operation"] != "pixel-portal-outcome-inference-contract"
        or value["boundary"] != INFERENCE_BOUNDARY
    ):
        raise evaluation.OutcomeError("shared inference contract identity or boundary is invalid")
    sampling = evaluation.exact_fields(value["sampling"], {
        "source", "temperaturePermille", "topPPermille", "topK", "minPPermille", "repeatPenaltyPermille", "seed",
        "reasoningEffort", "reasoningVisibility",
    }, "shared inference sampling")
    if (
        sampling["source"] != "request-boundary-enforced"
        or sampling["reasoningEffort"] not in {"backend-default", "low", "medium", "high", "max"}
        or sampling["reasoningVisibility"] != "hidden"
    ):
        raise evaluation.OutcomeError("shared inference sampling is not exactly request-boundary enforced")
    for key, maximum in {
        "temperaturePermille": 2000, "topPPermille": 1000, "topK": 1000000,
        "minPPermille": 1000, "repeatPenaltyPermille": 10000, "seed": 4294967295,
    }.items():
        evaluation.integer(sampling[key], 0, maximum, f"shared inference {key}")
    request = evaluation.exact_fields(value["request"], {
        "wireApi", "stream", "maxOutputTokens", "toolEncoding", "requestFieldPolicySha256", "promptCachePolicy",
    }, "shared inference request")
    if (
        request["wireApi"] not in {"openai-responses", "openai-chat-completions"}
        or type(request["stream"]) is not bool or request["toolEncoding"] != "function"
        or request["promptCachePolicy"] != "empty-at-run-start"
    ):
        raise evaluation.OutcomeError("shared inference request protocol is invalid")
    evaluation.integer(request["maxOutputTokens"], 1, 1048576, "shared inference output ceiling")
    evaluation.valid_hash(request["requestFieldPolicySha256"], "shared inference field-policy digest")
    if request["requestFieldPolicySha256"] != inference_policy_sha256(value):
        raise evaluation.OutcomeError("shared inference field-policy digest does not bind the enforced request policy")
    authority = evaluation.exact_fields(value["authority"], {
        "grantsInference", "grantsToolUse", "grantsExecution", "grantsCompletion",
    }, "shared inference authority")
    if any(type(item) is not bool for item in authority.values()) or any(authority.values()):
        raise evaluation.OutcomeError("shared inference contract attempts to grant authority")


def validate_task(root: Path, task_path: Path) -> tuple[dict[str, Any], bytes, dict[str, str | None], dict[str, Any]]:
    task, raw = evaluation.read_json(task_path, "private outcome task", private=True)
    evaluation.exact_fields(task, {
        "$schema", "schemaVersion", "operation", "taskId", "createdAt", "journeyId", "comparisonLane", "profile",
        "dataClass", "effectBoundary", "scenario", "bindings", "capabilities", "dataRoute", "budgets",
        "authority", "boundary",
    }, "private outcome task")
    profile, data_class = task["profile"], task["dataClass"]
    effect, data_route = task["effectBoundary"], task["dataRoute"]
    if (
        task["$schema"] != TASK_SCHEMA or task["schemaVersion"] != 1
        or task["operation"] != "pixel-portal-outcome-task"
        or not isinstance(task["taskId"], str) or TASK_ID_RE.fullmatch(task["taskId"]) is None
        or not isinstance(task["comparisonLane"], str) or task["comparisonLane"] not in LANES
        or not isinstance(profile, str) or profile not in PROFILES
        or not isinstance(data_class, str) or data_class not in DATA_CLASSES
        or not isinstance(effect, str) or effect not in EFFECTS
        or not isinstance(data_route, str) or data_route not in ROUTES
        or task["boundary"] != TASK_BOUNDARY
    ):
        raise evaluation.OutcomeError("outcome task identity or boundary is invalid")
    evaluation.timestamp(task["createdAt"], "outcome task creation time")
    journey, corpus_sha, journey_sha = evaluation.corpus_contract(root, task["journeyId"])
    if task["profile"] != journey.get("profile") or task["dataClass"] != journey.get("dataClass") or task["effectBoundary"] != journey.get("effect"):
        raise evaluation.OutcomeError("outcome task differs from its journey profile, data class, or effect")
    scenario = evaluation.exact_fields(task["scenario"], {"kind", "fault", "seedSha256"}, "outcome task scenario")
    evaluation.valid_hash(scenario["seedSha256"], "outcome task scenario seed")
    if (
        not isinstance(scenario["kind"], str)
        or scenario["kind"] not in {"baseline", "fault-injection"}
        or scenario["kind"] == "baseline" and scenario["fault"] is not None
        or scenario["kind"] == "fault-injection" and (
            not isinstance(scenario["fault"], str) or scenario["fault"] not in journey.get("faults", [])
        )
    ):
        raise evaluation.OutcomeError("outcome task scenario is not declared by its journey")

    bindings = evaluation.exact_fields(task["bindings"], {
        "userRequest", "sourceSnapshot", "environment", "toolPolicy", "verifier", "sharedModelContract",
        "sharedInferenceContract", "sanitizationEvidence", "researchFixture",
    }, "outcome task bindings")
    digests: dict[str, str | None] = {}
    bound_payloads = {}
    for key in ("userRequest", "sourceSnapshot", "environment", "toolPolicy", "verifier"):
        reference, bound_payloads[key] = resolved_reference(task_path, bindings[key], f"outcome task {key}")
        digests[f"{key}Sha256"] = reference["sha256"]
    shared_model, shared_inference = bindings["sharedModelContract"], bindings["sharedInferenceContract"]
    if task["comparisonLane"] == "same-model-harness":
        model_reference, model_payload = resolved_reference(task_path, shared_model, "outcome task shared model contract")
        inference_reference, inference_payload = resolved_reference(task_path, shared_inference, "outcome task shared inference contract")
        if model_reference["mediaType"] != "application/json" or inference_reference["mediaType"] != "application/json":
            raise evaluation.OutcomeError("shared model and inference contracts must be JSON")
        validate_model_contract(model_payload)
        validate_inference_contract(inference_payload)
        digests["sharedModelContractSha256"] = model_reference["sha256"]
        digests["sharedInferenceContractSha256"] = inference_reference["sha256"]
    elif shared_model is not None or shared_inference is not None:
        raise evaluation.OutcomeError("product-default task cannot claim a shared model or inference contract")
    else:
        digests["sharedModelContractSha256"] = None
        digests["sharedInferenceContractSha256"] = None
    sanitization = bindings["sanitizationEvidence"]
    if task["dataRoute"] == "sanitized-remote":
        reference, _payload = resolved_reference(task_path, sanitization, "outcome task sanitization evidence")
        digests["sanitizationEvidenceSha256"] = reference["sha256"]
    elif sanitization is not None:
        raise evaluation.OutcomeError("outcome task has sanitization evidence for a non-sanitized route")
    else:
        digests["sanitizationEvidenceSha256"] = None
    research_fixture = bindings["researchFixture"]
    if task["profile"] == "researcher":
        fixture_reference, fixture_payload = resolved_reference(task_path, research_fixture, "outcome task research fixture")
        if fixture_reference["mediaType"] != "application/json":
            raise evaluation.OutcomeError("research fixture must be JSON")
        validate_research_fixture(fixture_payload)
        digests["researchFixtureSha256"] = fixture_reference["sha256"]
    elif research_fixture is not None:
        raise evaluation.OutcomeError("non-Researcher outcome task cannot bind a research fixture")
    else:
        digests["researchFixtureSha256"] = None

    capabilities = task["capabilities"]
    if not isinstance(capabilities, list) or not 1 <= len(capabilities) <= 16 or any(
        not isinstance(item, str) or item not in CAPABILITIES for item in capabilities
    ) or len(set(capabilities)) != len(capabilities) or capabilities != sorted(capabilities):
        raise evaluation.OutcomeError("outcome task capabilities must be unique canonical known values")
    if "reasoning" not in capabilities:
        raise evaluation.OutcomeError("outcome task omits the shared reasoning capability")
    tool_reference = bindings["toolPolicy"]
    if tool_reference["mediaType"] != "application/json":
        raise evaluation.OutcomeError("tool policy must be JSON")
    tool_policy = validate_tool_policy(bound_payloads["toolPolicy"], capabilities)
    if bindings["environment"]["mediaType"] != "application/json":
        raise evaluation.OutcomeError("execution environment must be JSON")
    environment = validate_environment(bound_payloads["environment"], tool_policy=tool_policy)
    if bindings["verifier"]["mediaType"] != "application/json":
        raise evaluation.OutcomeError("deterministic verifier must be JSON")
    verifier = verifier_engine.load_definition(bound_payloads["verifier"])
    if verifier["journeyId"] != task["journeyId"]:
        raise evaluation.OutcomeError("deterministic verifier is bound to a different journey")
    workspace_verification = verifier["workspaceVerification"]
    if "filesystem-write" in capabilities and workspace_verification is None and task["profile"] != "researcher":
        raise evaluation.OutcomeError("writable outcome task has no independent workspace verification")
    if task["profile"] == "researcher" and workspace_verification is None and (
        "independent-verifier" not in journey.get("requiredEvidence", [])
        or not any(check["check"] == "research-inline-citations" for check in verifier["checks"])
    ):
        raise evaluation.OutcomeError("Researcher outcome task has no independent citation verification")
    if workspace_verification is not None:
        if "independent-verifier" not in journey.get("requiredEvidence", []) or not any(
            check["check"] == "workspace-verification-passes" for check in verifier["checks"]
        ):
            raise evaluation.OutcomeError("workspace verification is not a required scored journey evidence")
        verifier_limits = environment["verifier"]
        if len(workspace_verification["checks"]) > verifier_limits["maxChecks"]:
            raise evaluation.OutcomeError("workspace verification exceeds the environment check ceiling")
        if workspace_verification["maxRuntimeSeconds"] > verifier_limits["maxRuntimeSeconds"]:
            raise evaluation.OutcomeError("workspace verification exceeds the environment runtime ceiling")
        if workspace_verification["maxOutputBytes"] > verifier_limits["maxOutputBytes"]:
            raise evaluation.OutcomeError("workspace verification exceeds the environment output ceiling")
        allowed_executables = set(verifier_limits["allowedExecutables"])
        for check in workspace_verification["checks"]:
            if check["kind"] == "command" and check["argv"][0] not in allowed_executables:
                raise evaluation.OutcomeError("workspace verifier executable is outside the environment allowlist")
    if any(CAPABILITY_EFFECTS.get(item) == "external-write" for item in capabilities) and task["effectBoundary"] != "external-write":
        raise evaluation.OutcomeError("outcome task write capability exceeds its effect boundary")
    if "filesystem-write" in capabilities and task["effectBoundary"] not in {"local-state-change", "external-write"}:
        if task["effectBoundary"] not in {"none", "read-only"} or tool_policy["workspace"] != "disposable-read-write":
            raise evaluation.OutcomeError("outcome task filesystem-write exceeds its effect boundary")
    if any(item in REMOTE_CAPABILITIES for item in capabilities) and task["dataRoute"] not in {"sanitized-remote", "authorized-provider"}:
        raise evaluation.OutcomeError("outcome task remote model exceeds its data route")
    if task["dataRoute"] == "local-only" and any(item in NETWORK_CAPABILITIES or item in REMOTE_CAPABILITIES for item in capabilities):
        raise evaluation.OutcomeError("local-only outcome task contains a network capability")
    if task["dataClass"] != "public" and task["dataRoute"] == "brokered-public":
        raise evaluation.OutcomeError("non-public outcome task cannot use a public-only route")

    budgets = evaluation.exact_fields(task["budgets"], set(BUDGET_LIMITS), "outcome task budgets")
    for key, (minimum, maximum) in BUDGET_LIMITS.items():
        evaluation.integer(budgets[key], minimum, maximum, f"outcome task budget {key}")
    if budgets["externalWrites"] > 0 and task["effectBoundary"] != "external-write":
        raise evaluation.OutcomeError("outcome task external-write budget exceeds its effect boundary")
    if task["effectBoundary"] == "external-write" and budgets["externalWrites"] < 1:
        raise evaluation.OutcomeError("external-write outcome task has no external-write budget")
    authority = evaluation.exact_fields(task["authority"], AUTHORITY_FIELDS, "outcome task authority")
    if any(authority.values()) or any(type(authority[key]) is not bool for key in AUTHORITY_FIELDS):
        raise evaluation.OutcomeError("outcome task attempts to grant execution or authority")
    return task, raw, digests, {"journey": journey, "corpusSha256": corpus_sha, "journeySha256": journey_sha}


def admit_task(root: Path, task_path: Path) -> dict[str, Any]:
    task, raw, digests, contract = validate_task(root, task_path)
    task_sha = evaluation.sha256(raw)
    admission_seed = evaluation.canonical({"taskSha256": task_sha, "journeySha256": contract["journeySha256"]})
    return {
        "schemaVersion": 1,
        "operation": "pixel-portal-outcome-task-admission",
        "admissionId": f"outcometaskadmission-{evaluation.sha256(admission_seed)[:24]}",
        "taskId": task["taskId"], "createdAt": task["createdAt"], "journeyId": task["journeyId"],
        "comparisonLane": task["comparisonLane"],
        "profile": task["profile"], "dataClass": task["dataClass"], "effectBoundary": task["effectBoundary"],
        "scenario": task["scenario"], "corpusSha256": contract["corpusSha256"],
        "journeySha256": contract["journeySha256"], "taskSpecificationSha256": task_sha,
        "bindings": digests, "capabilities": task["capabilities"], "dataRoute": task["dataRoute"],
        "budgets": task["budgets"], "status": "admitted-inert",
        "privacy": {
            "pathsIncluded": False, "requestContentIncluded": False, "sourceContentIncluded": False,
            "verifierContentIncluded": False, "credentialsIncluded": False,
        },
        "authority": {
            "grantsExecution": False, "grantsProviderCall": False, "grantsCredentialUse": False,
            "grantsExternalEffect": False, "grantsScopeExpansion": False, "grantsSafetyRelaxation": False,
            "grantsCompletion": False, "grantsPromotion": False,
        },
        "boundary": ADMISSION_BOUNDARY,
    }


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Admit one private backend-neutral Pixel/Codex outcome task")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = arguments()
    try:
        root = args.root.resolve()
        task_path = Path(os.path.abspath(args.task))
        output_path = Path(os.path.abspath(args.output)) if args.output else None
        for path, label in ((task_path, "task"), (output_path, "output")):
            if path is not None and (path == root or root in path.parents):
                raise evaluation.OutcomeError(f"outcome {label} must remain outside the source repository")
        evaluation.private_parent(task_path)
        try:
            if task_path.resolve(strict=True) != task_path:
                raise evaluation.OutcomeError("private outcome task path contains a link")
        except OSError as exc:
            raise evaluation.OutcomeError("private outcome task is unavailable") from exc
        result = admit_task(root, task_path)
        payload = json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"
        if output_path:
            evaluation.write_new_private(output_path, payload)
        print(payload.decode("utf-8"), end="")
        return 0
    except (evaluation.OutcomeError, UnicodeError, OSError) as exc:
        print(f"[pixel] ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
