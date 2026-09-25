#!/usr/bin/env python3
"""Arm-neutral cold and warm runtime controls for paired local-model evaluation."""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any

import portal_outcome_evaluation as evaluation


CONDITIONS = frozenset({"cold-first-request", "warm-neutral-probe"})
CONTAINER_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")
REQUEST_COUNTER_NAME = "vllm:request_success_total"
BOUNDARY = (
    "Content-free within-run runtime-control evidence only. A cold control proves the freshly started backend had "
    "served no inference request; a warm control performs one fixed neutral local probe before measured work. It "
    "retains no prompt or response content, carries no cross-run state, and grants no task, tool, provider, "
    "credential, external-effect, completion, acceptance, publication, deployment, or promotion authority."
)
WARMUP_REQUEST = json.dumps({
    "model": "DeepSeek-V4-Flash-0731",
    "messages": [{"role": "user", "content": "Reply with exactly READY."}],
    "max_tokens": 8,
    "temperature": 0,
    "top_p": 1,
    "seed": 0,
    "stream": False,
}, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
WARMUP_REQUEST_SHA256 = evaluation.sha256(WARMUP_REQUEST)


def _request_count(metrics: str) -> int | None:
    if not isinstance(metrics, str):
        return None
    total = 0.0
    observed = False
    for raw in metrics.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name = line.split("{", 1)[0].split(" ", 1)[0]
        if name != REQUEST_COUNTER_NAME:
            continue
        try:
            total += float(line.rsplit(" ", 1)[-1])
        except ValueError:
            return None
        observed = True
    if not observed or total < 0 or total != int(total):
        return None
    return int(total)


def _docker(docker_path: str, args: list[str], *, timeout: int) -> bytes:
    if not isinstance(docker_path, str) or not docker_path or "\x00" in docker_path:
        raise evaluation.OutcomeError("runtime-control Docker executable is invalid")
    result = subprocess.run([docker_path, *args], capture_output=True, timeout=timeout)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace")[-400:]
        raise evaluation.OutcomeError(f"runtime-control Docker probe failed: {detail}")
    if len(result.stdout) > 2 * 1024 * 1024:
        raise evaluation.OutcomeError("runtime-control Docker probe exceeded its output ceiling")
    return result.stdout


def _metrics(docker_path: str, container: str) -> int:
    payload = _docker(docker_path, [
        "exec", container, "curl", "-sfS", "--max-time", "60", "--max-filesize", "2097152",
        "http://localhost:8080/metrics",
    ], timeout=90)
    count = _request_count(payload.decode("utf-8", errors="strict"))
    if count is None:
        raise evaluation.OutcomeError("runtime-control request counter is unavailable or malformed")
    return count


def _warm(docker_path: str, container: str) -> tuple[str, int, int]:
    response = _docker(docker_path, [
        "exec", container, "curl", "-sfS", "--max-time", "600", "--max-filesize", "2097152",
        "-H", "content-type: application/json", "--data-binary", WARMUP_REQUEST.decode("utf-8"),
        "http://localhost:8080/v1/chat/completions",
    ], timeout=660)
    value = evaluation.parse_json(response, "runtime-control warm-up response")
    if not isinstance(value, dict) or value.get("model") != "DeepSeek-V4-Flash-0731":
        raise evaluation.OutcomeError("runtime-control warm-up response has the wrong model")
    choices = value.get("choices")
    usage = value.get("usage")
    if not isinstance(choices, list) or not choices or not isinstance(usage, dict):
        raise evaluation.OutcomeError("runtime-control warm-up response is incomplete")
    input_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
    output_tokens = usage.get("completion_tokens", usage.get("output_tokens"))
    input_tokens = evaluation.integer(input_tokens, 1, 1048576, "runtime-control warm-up input tokens")
    output_tokens = evaluation.integer(output_tokens, 0, 1024, "runtime-control warm-up output tokens")
    return evaluation.sha256(response), input_tokens, output_tokens


def execute(*, docker_path: str, container: str, run_id: str, condition: str) -> dict[str, Any]:
    if condition not in CONDITIONS or not isinstance(run_id, str) or not run_id.startswith("outcomerun-"):
        raise evaluation.OutcomeError("runtime-control identity or condition is invalid")
    if not isinstance(container, str) or CONTAINER_RE.fullmatch(container) is None:
        raise evaluation.OutcomeError("runtime-control container identity is invalid")
    before = _metrics(docker_path, container)
    if before != 0:
        raise evaluation.OutcomeError("runtime-control backend is not a fresh zero-request runtime")
    response_sha256 = None
    input_tokens = 0
    output_tokens = 0
    warmup_requests = 0
    if condition == "warm-neutral-probe":
        response_sha256, input_tokens, output_tokens = _warm(docker_path, container)
        warmup_requests = 1
    after = _metrics(docker_path, container)
    if after != warmup_requests:
        raise evaluation.OutcomeError("runtime-control request count differs from its declared condition")
    return {
        "schemaVersion": 1,
        "operation": "pixel-portal-outcome-runtime-control",
        "runId": run_id,
        "condition": condition,
        "status": "ready",
        "warmupRequestSha256": WARMUP_REQUEST_SHA256 if warmup_requests else None,
        "warmupResponseSha256": response_sha256,
        "requestsBefore": before,
        "requestsAfter": after,
        "warmupModelRequests": warmup_requests,
        "warmupInputTokens": input_tokens,
        "warmupOutputTokens": output_tokens,
        "measuredUsageIncludesWarmup": False,
        "promptCachePolicy": "empty-at-run-start",
        "crossRunStateObserved": False,
        "authority": {
            "grantsTaskExecution": False,
            "grantsToolUse": False,
            "grantsProviderCall": False,
            "grantsCredentialUse": False,
            "grantsExternalEffect": False,
            "grantsCompletion": False,
        },
        "boundary": BOUNDARY,
    }
