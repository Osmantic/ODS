#!/usr/bin/env python3
"""Trusted-owner Pixel mesh peer status and message transport.

Peer allowlist is fixed at exactly Tower1/Tower2/Tower3. Messages travel only
on stdin to fixed SSH/OpenClaw argv and are never interpolated into a shell
command. All subprocess invocations use explicit argv lists (no shell=True).
Status evidence carries observation timestamps and treats an unreachable peer
as unknown, never as proof of absence.
"""

from __future__ import annotations

import copy
import errno
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

PEERS = {"tower1": "tower1", "tower2": "tower2", "tower3": "tower3"}
MAX_MESSAGE_BYTES = 128 * 1024
MAX_STATUS_BYTES = 256 * 1024
MAX_AGENT_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_SESSION_BYTES = 32 * 1024 * 1024
MAX_AGENT_MODEL_CALLS = 96
MAX_AGENT_TOOL_CALLS = 96
MAX_AGENT_COMPACTIONS = 2
# Each admitted compaction may be preceded by OpenClaw's fail-closed context
# overflow marker.  Fail and hand off when a third overflow/compaction is
# durably observed; the first two remain available for exploration recovery.
MAX_AGENT_TOOL_LOOP_OVERFLOWS = MAX_AGENT_COMPACTIONS
SYNTHESIS_AGENT_ID = "pixel-synthesis"
MAX_SYNTHESIS_EVIDENCE_BYTES = 64 * 1024
MAX_SYNTHESIS_RESPONSE_BYTES = 1024 * 1024
MAX_OPENCLAW_CONFIG_BYTES = 1024 * 1024
MAX_SYNTHESIS_OUTPUT_TOKENS = 4096
SYNTHESIS_TIMEOUT_SECONDS = 600
# Repeated no-visible-output (thinking-only/empty) length terminations hand off
# to a deterministic blocked result instead of looping or trusting more model output.
MAX_AGENT_SILENT_LENGTH_STREAK = 2
MAX_CONTRACT_SCHEMA_BYTES = 64 * 1024
MAX_CONTRACT_SCHEMA_DEPTH = 16
MAX_CONTRACT_SCHEMA_NODES = 1024
MAX_CONTRACT_OUTPUT_BYTES = 256 * 1024
MAX_CONTRACT_SAFE_INTEGER = (1 << 53) - 1
MAX_CONTRACT_ONE_OF_BRANCHES = 8
READ_ONLY_ALLOWED_TOOLS = ("read",)
OPERATIONS_BROKER_ALLOWED_TOOLS = (
    "pixel_ops_inventory",
    "pixel_ops_run",
    "pixel_ops_job_wait",
)
DOWNLOAD_STAGING_ALLOWED_TOOLS = (
    "pixel_ops_inventory",
    "pixel_ops_download_stage",
    "pixel_ops_job_wait",
)
CONTRACT_AUTHORITY_PROFILE_ENV = "PIXEL_MESH_CONTRACT_AUTHORITY_PROFILE"
CONTRACT_AUTHORITY_PROFILE_MAX_BYTES = 64
CONTRACT_AUTHORITY_PROFILES = {
    "web-read-only": ("pixel_web_browse",),
    "operations-broker": OPERATIONS_BROKER_ALLOWED_TOOLS,
    "download-staging": DOWNLOAD_STAGING_ALLOWED_TOOLS,
}
BROKER_MEDIATED_AUTHORITY_PROFILES = frozenset({"operations-broker", "download-staging"})
SANDBOXED_ALLOWED_TOOLS = ("read", "write", "edit", "apply_patch", "exec", "process")
SANDBOXED_READONLY_ALLOWED_TOOLS = ("read", "exec")
SANDBOXED_MAILBOX_READONLY_ALLOWED_TOOLS = (
    "read", "write", "exec", "pixel_gmail_search", "pixel_gmail_read",
)
# Optional sandboxed allowlist override. Sourced only from the process
# environment (never prompt or stdin) and parsed before any config creation
# or runner launch.
SANDBOXED_ALLOWED_TOOLS_ENV = "PIXEL_MESH_SANDBOXED_ALLOWED_TOOLS"
SANDBOXED_ALLOWED_TOOLS_MAX_BYTES = 256
SANDBOXED_MODEL_PIN_ENV = "PIXEL_MESH_SANDBOXED_MODEL_PIN"
SANDBOXED_MODEL_BASE_URL_ENV = "PIXEL_MESH_SANDBOXED_MODEL_BASE_URL"
SANDBOXED_MODEL_PIN_MAX_BYTES = 385
SANDBOXED_MODEL_BASE_URL_MAX_BYTES = 512
# Stable custody sentinel for a directory `.git` entry: a directory's link count
# changes whenever a child directory is created or removed, so revalidation stores
# this fixed sentinel instead of the volatile directory st_nlink.
GIT_DIRECTORY_LINK_SENTINEL = 0
SANDBOX_DOCKER_IMAGE = "local/pixel-dev-sandbox:node22.23.1"
SANDBOX_DOCKER_WORKDIR = "/workspace"
SANDBOX_WORKSPACE_CONTROL_PARTS = (".openclaw", "sandbox-skills", "skills")
MAX_SANDBOX_DOCKER_LIST_BYTES = 4096
MAX_SANDBOX_DOCKER_INSPECT_BYTES = 256 * 1024
MAX_SANDBOX_DOCKER_INSPECT_STRING_BYTES = 4096
MAX_SANDBOX_DOCKER_MOUNTS = 16
MAX_SANDBOX_DOCKER_OBSERVATIONS = 8192
SANDBOX_DOCKER_COMMAND_TIMEOUT_SECONDS = 5
SANDBOX_DOCKER_CONTAINER_ID_PATTERN = re.compile(r"[0-9a-f]{64}")
SANDBOX_DOCKER_CONFIG_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
SANDBOX_DOCKER_TMPFS_DESTINATIONS = frozenset({"/tmp", "/var/tmp", "/run"})
SANDBOX_DOCKER_MEMORY_BYTES = 8 * 1024 * 1024 * 1024
SANDBOX_DOCKER_NANO_CPUS = 4_000_000_000
SANDBOX_DOCKER_READONLY_WORKSPACE_DESTINATION = "/agent"
MAX_WORKSPACE_PATH_BYTES = 4096
MAX_GIT_TOPLEVEL_BYTES = 4096
MAX_GIT_POINTER_BYTES = 4096
# (common dir, common identity, worktrees dir, worktrees identity,
# linked-worktree control dir, control identity, exact .git pointer payload
# sha256). The common dir is the only additional host path exposed to a linked
# worktree sandbox, always read-only.
GitMetadataBinding = tuple[
    Path, tuple[int, int, int, int],
    Path, tuple[int, int, int, int],
    Path, tuple[int, int, int, int],
    str,
]
SandboxWorkspaceControlBinding = tuple[
    tuple[Path, tuple[int, int, int, int], bool], ...
]
OUTPUT_LENGTH_RECOVERY = "exploration-output-length-exhausted"
COMPACTION_BUDGET_RECOVERY = "exploration-compaction-budget-exhausted"
OVERFLOW_BUDGET_RECOVERY = "exploration-overflow-budget-exhausted"
TRANSCRIPT_OVERFLOW_RECOVERY = "exploration-transcript-overflow-exhausted"
REASONING_WITHOUT_VISIBLE_OUTPUT_RECOVERY = "exploration-reasoning-without-visible-output"
SELECTED_MODEL_BUDGET_RECOVERY = "exploration-selected-model-call-budget-exhausted"
SELECTED_TOOL_BUDGET_RECOVERY = "exploration-selected-tool-call-budget-exhausted"
SELECTED_BUDGET_RECOVERY_REASONS = frozenset({
    SELECTED_MODEL_BUDGET_RECOVERY,
    SELECTED_TOOL_BUDGET_RECOVERY,
})
CONTRACT_TERMINAL_SYNTHESIS = "contract-terminal-synthesis"
CONTRACT_TERMINAL_DETERMINISTIC = "contract-terminal-deterministic"
TOOL_LOOP_CONTEXT_OVERFLOW = (
    "Context overflow: estimated context size exceeds safe threshold during tool loop."
)
AGENT_TIMEOUT_SECONDS = 1800
EXECUTION_BUDGET_TIMEOUT_ENV = "PIXEL_MESH_AGENT_TIMEOUT_SECONDS"
EXECUTION_BUDGET_MAX_MODEL_CALLS_ENV = "PIXEL_MESH_MAX_MODEL_CALLS"
EXECUTION_BUDGET_MAX_TOOL_CALLS_ENV = "PIXEL_MESH_MAX_TOOL_CALLS"
AGENT_SUPERVISION_POLL_SECONDS = 0.25
AGENT_TERMINATION_GRACE_SECONDS = 3.0
TRANSPORT_PROBE_INTERVAL_SECONDS = 1.0
PROCESS_ENVIRONMENT_LIMIT = 1024 * 1024
TREE_TOKEN_ENV = "PIXEL_MESH_TREE_TOKEN"
SSH_PREFIX = (
    "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
    "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2",
)
SESSION_KEY_ENV = "PIXEL_MESH_SESSION_KEY"
SESSION_KEY_PATTERN = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
HOME_ENV = "PIXEL_MESH_HOME"
OPENCLAW_BIN_ENV = "PIXEL_MESH_OPENCLAW_BIN"
LOCAL_EXEC_SHELL_RELATIVE = Path(".local/share/pixel-mesh/current/exec-shell/bash")
POST_FINAL_COMPACTION_FAILURE = re.compile(
    rb"Error: CLI transcript compaction failed for "
    rb"(?P<provider>[A-Za-z0-9._-]{1,128})/"
    rb"(?P<model>[A-Za-z0-9._/-]{1,256}): Already compacted"
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def peer_name(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in PEERS:
        raise ValueError("peer must be exactly Tower1, Tower2, or Tower3")
    return normalized


def is_local_peer(name: str) -> bool:
    """Recognize the allowlisted local node without requiring self-SSH trust."""
    return socket.gethostname().split(".", 1)[0].lower() == name


def fresh_session_key() -> str:
    configured = os.environ.get(SESSION_KEY_ENV)
    if configured is not None:
        if not SESSION_KEY_PATTERN.fullmatch(configured):
            raise ValueError(f"{SESSION_KEY_ENV} must match {SESSION_KEY_PATTERN.pattern}")
        return configured
    return f"pixel-mesh-{secrets.token_hex(12)}"


def mesh_home() -> Path:
    return Path(os.environ.get(HOME_ENV) or Path.home())


def mesh_exec_shell(home: Path) -> Path:
    shell = home / LOCAL_EXEC_SHELL_RELATIVE
    releases = home / ".local" / "share" / "pixel-mesh" / "releases"
    try:
        resolved = shell.resolve(strict=True)
        releases_resolved = releases.resolve(strict=True)
        info = shell.lstat()
    except OSError as exc:
        raise ValueError(f"mesh exec shell is unavailable: {exc}") from None
    if (not resolved.is_relative_to(releases_resolved)
            or resolved.parent.name != "exec-shell" or resolved.name != "bash"):
        raise ValueError("mesh exec shell must resolve inside a content-addressed release")
    if (shell.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700
            or not os.access(shell, os.X_OK)):
        raise ValueError("mesh exec shell must be an owner-private executable regular file")
    return shell


def bounded_run(argv: list[str] | tuple[str, ...], *, input_bytes: bytes | None = None,
                timeout: int = 15, limit: int = MAX_STATUS_BYTES) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(argv, input=input_bytes, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=timeout, check=False)
    if len(result.stdout) > limit or len(result.stderr) > limit:
        raise RuntimeError("bounded command output exceeded its limit")
    return result


def service_state(name: str) -> str:
    try:
        result = bounded_run(["systemctl", "--user", "is-active", name], timeout=3, limit=4096)
        text = result.stdout.decode("utf-8", "replace").strip()
        return text or ("inactive" if result.returncode else "active")
    except (OSError, subprocess.TimeoutExpired, RuntimeError):
        return "unavailable"


def container_state(name: str) -> str:
    try:
        result = bounded_run(
            ["docker", "inspect", "--format", "{{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}", name],
            timeout=5, limit=4096,
        )
        text = result.stdout.decode("utf-8", "replace").strip()
        return text if result.returncode == 0 and text else "absent"
    except (OSError, subprocess.TimeoutExpired, RuntimeError):
        return "unavailable"


def gpu_state() -> list[dict[str, str]]:
    command = [
        "nvidia-smi", "--query-gpu=name,memory.total,memory.used,utilization.gpu",
        "--format=csv,noheader,nounits",
    ]
    try:
        result = bounded_run(command, timeout=5, limit=32768)
    except (OSError, subprocess.TimeoutExpired, RuntimeError):
        return []
    if result.returncode:
        return []
    keys = ("name", "memoryTotalMiB", "memoryUsedMiB", "utilizationPercent")
    return [dict(zip(keys, (part.strip() for part in line.split(",", 3))))
            for line in result.stdout.decode("utf-8", "replace").splitlines() if line.strip()]


def model_state() -> dict[str, object]:
    try:
        with urllib.request.urlopen("http://127.0.0.1:18080/v1/models", timeout=3) as response:
            payload = response.read(65537)
        if len(payload) > 65536:
            raise ValueError("model response exceeded limit")
        parsed = json.loads(payload)
        models = [item.get("id") for item in parsed.get("data", []) if isinstance(item, dict)]
        return {"reachable": True, "models": models[:16]}
    except Exception as error:  # Evidence collector must return a truthful bounded failure.
        return {"reachable": False, "error": type(error).__name__}


def router_state(port: int) -> dict[str, object]:
    if port not in {8000, 18080}:
        raise ValueError("router status port is not allowlisted")
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as response:
            payload = response.read(65537)
        if len(payload) > 65536:
            raise ValueError("router status response exceeded limit")
        parsed = json.loads(payload)
        if not isinstance(parsed, dict) or not isinstance(parsed.get("endpoints"), list):
            raise ValueError("router status response is invalid")
        if (
            not isinstance(parsed.get("status"), str)
            or not isinstance(parsed.get("model"), str)
            or not isinstance(parsed.get("available"), bool)
            or len(parsed["endpoints"]) > 16
        ):
            raise ValueError("router status summary is invalid")
        endpoints = []
        for item in parsed["endpoints"]:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("name"), str) or not item["name"]
                or not isinstance(item.get("healthy"), bool)
                or type(item.get("active")) is not int or item["active"] < 0
                or type(item.get("max_active")) is not int or item["max_active"] < 1
                or item.get("model_id") is not None and not isinstance(item.get("model_id"), str)
            ):
                raise ValueError("router endpoint status is invalid")
            endpoints.append({
                "name": item["name"],
                "healthy": item["healthy"],
                "active": item["active"],
                "maxActive": item["max_active"],
                "modelId": item.get("model_id"),
            })
        return {
            "reachable": True,
            "status": parsed["status"],
            "model": parsed["model"],
            "available": parsed["available"],
            "endpoints": endpoints,
        }
    except Exception as error:  # Evidence collector must return a truthful bounded failure.
        return {"reachable": False, "error": type(error).__name__}


def router_states() -> dict[str, object]:
    primary = router_state(18080)
    work = router_state(8000)
    canonical = None
    if primary.get("reachable") is True and work.get("reachable") is True:
        endpoints = work.get("endpoints")
        canonical = bool(
            work.get("model") == primary.get("model")
            and isinstance(endpoints, list) and len(endpoints) == 1
            and endpoints[0].get("name") == "canonical"
            and endpoints[0].get("healthy") is True
            and endpoints[0].get("modelId") == primary.get("model")
        )
    return {
        "primary": primary,
        "workProvider": work,
        "canonicalDelegationObserved": canonical,
    }


def local_status() -> dict[str, object]:
    observed = now()
    try:
        uptime = float(Path("/proc/uptime").read_text(encoding="utf-8").split()[0])
    except (OSError, ValueError, IndexError):
        uptime = None
    try:
        load = list(os.getloadavg())
    except OSError:
        load = []
    return {
        "schemaVersion": 1,
        "observedAt": observed,
        "reachable": True,
        "identity": {"hostname": socket.gethostname(), "user": os.environ.get("USER", "unknown")},
        "host": {"uptimeSeconds": uptime, "loadAverage": load},
        "gpu": gpu_state(),
        "model": model_state(),
        "modelRouters": router_states(),
        "userSystemd": {
            "pixel-mesh-gateway.service": service_state("pixel-mesh-gateway.service"),
            "dream-fleet-model-router.service": service_state("dream-fleet-model-router.service"),
            "dream-fleet-tunnel-leader.service": service_state("dream-fleet-tunnel-leader.service"),
            "dream-fleet-tunnel-tower1.service": service_state("dream-fleet-tunnel-tower1.service"),
            "dream-fleet-tunnel-tower3.service": service_state("dream-fleet-tunnel-tower3.service"),
            "dream-fleet-work-broker.service": service_state("dream-fleet-work-broker.service"),
        },
        "dockerContainers": {
            "ods-model-router": container_state("ods-model-router"),
            "ods-remote-provider-ssh-tunnel": container_state("ods-remote-provider-ssh-tunnel"),
        },
    }


def remote_status(peer: str) -> dict[str, object]:
    selected = peer_name(peer)
    if is_local_peer(selected):
        return {
            "peer": selected, "observedAt": now(), "reachable": True,
            "evidence": local_status(),
        }
    command = [*SSH_PREFIX, PEERS[selected], "~/.local/bin/pixel-mesh-peer", "status"]
    observed = now()
    try:
        result = bounded_run(command, timeout=18)
        if result.returncode:
            detail = result.stderr.decode("utf-8", "replace").strip()[:1000]
            return {"peer": selected, "observedAt": observed, "reachable": False,
                    "error": detail or f"ssh-exit-{result.returncode}"}
        payload = json.loads(result.stdout)
        if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
            raise ValueError("invalid peer status contract")
        return {"peer": selected, "observedAt": observed, "reachable": True, "evidence": payload}
    except (OSError, subprocess.TimeoutExpired, RuntimeError, ValueError, json.JSONDecodeError) as error:
        return {"peer": selected, "observedAt": observed, "reachable": False,
                "error": type(error).__name__}


def fleet_status() -> dict[str, object]:
    def collect(name: str) -> dict[str, object]:
        if is_local_peer(name):
            return {"peer": name, "observedAt": now(), "reachable": True, "evidence": local_status()}
        return remote_status(name)
    with ThreadPoolExecutor(max_workers=3) as pool:
        evidence = list(pool.map(collect, PEERS))
    return {"schemaVersion": 1, "observedAt": now(), "peers": evidence,
            "boundary": "Each entry is fresh per-peer evidence; an unreachable peer is not evidence that it does not exist."}


def _read_session_payload(
    path: Path, *, expected_uid: int | None = None, single_link: bool = False,
) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_size > MAX_SESSION_BYTES
            or (expected_uid is not None and info.st_uid != expected_uid)
            or (single_link and info.st_nlink != 1)
        ):
            raise ValueError("agent session must be a bounded regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            payload = source.read(MAX_SESSION_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(payload) > MAX_SESSION_BYTES:
        raise ValueError("agent session exceeded its size limit")
    return payload


def _decode_session_records(payload: bytes) -> list[dict[str, object]]:
    records = []
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise ValueError("agent session is not valid UTF-8") from error
    for number, line in enumerate(lines, 1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"agent session JSONL line {number} is invalid") from error
        if not isinstance(record, dict):
            raise ValueError(f"agent session JSONL line {number} is not an object")
        records.append(record)
    return records


def read_session_records(
    path: Path, *, expected_uid: int | None = None, single_link: bool = False,
) -> list[dict[str, object]]:
    return _decode_session_records(_read_session_payload(
        path, expected_uid=expected_uid, single_link=single_link,
    ))


def last_yield_index(records: list[dict[str, object]]) -> int | None:
    found = None
    for index, record in enumerate(records):
        message = record.get("message")
        content = message.get("content", []) if isinstance(message, dict) else []
        for part in content if isinstance(content, list) else []:
            if isinstance(part, dict) and part.get("type") == "toolCall" and part.get("name") == "sessions_yield":
                found = index
    return found


def terminal_assistant_text(records: list[dict[str, object]], after: int) -> str | None:
    for record in reversed(records[after + 1:]):
        message = record.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant" or message.get("api") == "cli":
            continue
        content = message.get("content", [])
        if not isinstance(content, list) or any(
            isinstance(part, dict) and part.get("type") == "toolCall" for part in content
        ):
            return None
        value = "\n".join(
            str(part.get("text")) for part in content
            if isinstance(part, dict) and part.get("type") == "text" and part.get("text")
        ).strip()
        return value or None
    return None


def checked_session_path(
    response: dict[str, object], state: Path, agent_id: str = "pixel",
) -> Path:
    result = response.get("result")
    meta = result.get("meta") if isinstance(result, dict) else None
    agent_meta = meta.get("agentMeta") if isinstance(meta, dict) else None
    value = agent_meta.get("sessionFile") if isinstance(agent_meta, dict) else None
    if not isinstance(value, str):
        raise ValueError("OpenClaw response omitted its agent session file")
    session = Path(value)
    if not session.is_absolute():
        raise ValueError("OpenClaw returned a non-absolute agent session file")
    info = session.lstat()
    if session.is_symlink() or not stat.S_ISREG(info.st_mode):
        raise ValueError("OpenClaw returned an unsafe agent session file")
    if agent_id not in {"pixel", SYNTHESIS_AGENT_ID}:
        raise ValueError("OpenClaw agent id is outside the closed registry")
    if agent_id == SYNTHESIS_AGENT_ID and (
        info.st_nlink != 1
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise ValueError("Pixel synthesis receipt is not owner-private")
    expected = (
        state / "pixel-mesh-synthesis"
        if agent_id == SYNTHESIS_AGENT_ID
        else state / "agents" / agent_id / "sessions"
    ).resolve(strict=True)
    resolved = session.resolve(strict=True)
    if session.parent.resolve(strict=True) != expected or resolved.parent != expected or resolved.suffix != ".jsonl":
        raise ValueError("OpenClaw returned an unsafe agent session file")
    return session


def normalize_agent_response(response_bytes: bytes) -> bytes:
    """Normalize embedded-local output to the existing gateway response contract."""
    try:
        response = json.loads(response_bytes)
    except json.JSONDecodeError as error:
        raise ValueError("OpenClaw agent returned invalid JSON") from error
    if not isinstance(response, dict):
        raise ValueError("OpenClaw agent returned an invalid response object")
    if response.get("status") == "ok" and isinstance(response.get("result"), dict):
        return response_bytes
    if isinstance(response.get("payloads"), list) and isinstance(response.get("meta"), dict):
        normalized = {"status": "ok", "summary": "completed", "result": response}
        return (json.dumps(normalized, separators=(",", ":")) + "\n").encode("utf-8")
    raise ValueError("OpenClaw agent returned a non-ok response")


def validate_completed_agent_response(response_bytes: bytes) -> bytes:
    """Require affirmative terminal success before the mesh returns exit zero."""
    try:
        response = json.loads(response_bytes)
    except json.JSONDecodeError as error:
        raise ValueError("OpenClaw agent returned invalid JSON") from error
    result = response.get("result") if isinstance(response, dict) else None
    meta = result.get("meta") if isinstance(result, dict) else None
    payloads = result.get("payloads") if isinstance(result, dict) else None
    if (
        not isinstance(meta, dict)
        or not isinstance(payloads, list)
        or not payloads
        or not all(isinstance(payload, dict) for payload in payloads)
    ):
        raise ValueError("OpenClaw agent did not report a completed terminal result")

    # A yielded response is accepted only after reconcile_yielded_response has
    # replaced it with the durable parent final and marked that transition.
    if meta.get("meshYieldReconciled") is True:
        terminal = payloads[-1] if isinstance(payloads[-1], dict) else None
        completed = (
            isinstance(terminal, dict)
            and terminal.get("livenessState") == "completed"
            and terminal.get("stopReason") == "stop"
        )
    else:
        completed = (
            meta.get("livenessState") in {"working", "completed"}
            and meta.get("stopReason") == "stop"
        )

    # replayInvalid is not a failure discriminator: live successful and blocked
    # OpenClaw turns can both set it. A non-null error and terminal state are.
    if meta.get("error") is not None or not completed:
        raise ValueError("OpenClaw agent did not report a completed terminal result")
    return response_bytes


def attach_interrupted_completion_evidence(
    response_bytes: bytes, session: Path, state: Path,
) -> bytes:
    """Fail closed when OpenClaw completed only after output-length exhaustion."""
    session = checked_expected_session_path(session, state)
    payload = _read_session_payload(session, expected_uid=os.geteuid(), single_link=True)
    records = _decode_session_records(payload)
    assistant_messages = []
    for record in records:
        message = record.get("message")
        if (
            isinstance(message, dict)
            and message.get("role") == "assistant"
            and message.get("api") != "cli"
        ):
            assistant_messages.append(message)
    if (
        len(assistant_messages) < 2
        or assistant_messages[-1].get("stopReason") != "stop"
        or not any(message.get("stopReason") == "length" for message in assistant_messages[:-1])
    ):
        return response_bytes

    try:
        response = json.loads(response_bytes)
    except json.JSONDecodeError as error:
        raise ValueError("OpenClaw interrupted completion response is invalid JSON") from error
    result = response.get("result") if isinstance(response, dict) else None
    meta = result.get("meta") if isinstance(result, dict) else None
    if not isinstance(meta, dict) or "meshInterruptedCompletion" in meta:
        raise ValueError("OpenClaw interrupted completion has an invalid metadata boundary")
    terminal = assistant_messages[-1]
    meta["meshInterruptedCompletion"] = {
        "reason": "prior-output-length-exhaustion",
        "sessionFile": str(session),
        "sessionSha256": hashlib.sha256(payload).hexdigest(),
        "provider": terminal.get("provider"),
        "model": terminal.get("model"),
        "interruptedModelCalls": sum(
            message.get("stopReason") == "length" for message in assistant_messages[:-1]
        ),
        "completionAuthority": False,
        "externalEffectAuthority": False,
        "requiresIndependentVerification": True,
        "boundary": (
            "Returned the durable terminal text but an earlier model response exhausted its output "
            "budget; the continuation grants no external-effect success, deployment, publication, "
            "or acceptance authority without independent verification."
        ),
    }
    response["summary"] = "completed-requires-independent-verification"
    return (json.dumps(response, separators=(",", ":")) + "\n").encode("utf-8")


def reconcile_yielded_response(response_bytes: bytes, state: Path, deadline: float) -> bytes:
    try:
        response = json.loads(response_bytes)
    except json.JSONDecodeError as error:
        raise ValueError("OpenClaw agent returned invalid JSON") from error
    if not isinstance(response, dict) or response.get("status") != "ok":
        raise ValueError("OpenClaw agent returned a non-ok response")
    session = checked_session_path(response, state)
    records = read_session_records(session)
    yielded_at = last_yield_index(records)
    if yielded_at is None:
        return response_bytes
    observed = None
    while True:
        final_text = terminal_assistant_text(records, yielded_at)
        if final_text is not None:
            result = response.get("result")
            if not isinstance(result, dict):
                raise ValueError("OpenClaw response omitted its result object")
            result["payloads"] = [{"text": final_text, "livenessState": "completed", "stopReason": "stop"}]
            result.setdefault("meta", {})["meshYieldReconciled"] = True
            return (json.dumps(response, separators=(",", ":")) + "\n").encode("utf-8")
        if time.monotonic() >= deadline:
            raise TimeoutError("yielded agent session did not produce a final parent response")
        current = (session.stat().st_mtime_ns, session.stat().st_size)
        if current != observed:
            records = read_session_records(session)
            yielded_at = last_yield_index(records)
            if yielded_at is None:
                raise ValueError("agent session lost its yielded transition")
            observed = current
        time.sleep(0.25)


def completed_yield_from_partial(response_bytes: bytes, state: Path) -> bytes | None:
    """Accept only a yielded response whose later parent final is already durable."""
    try:
        json.loads(response_bytes)
    except json.JSONDecodeError:
        return None
    normalized = normalize_agent_response(response_bytes)
    response = json.loads(normalized)
    session = checked_session_path(response, state)
    if last_yield_index(read_session_records(session)) is None:
        return None
    try:
        return reconcile_yielded_response(normalized, state, time.monotonic())
    except TimeoutError:
        return None


def checked_expected_session_path(session: Path, state: Path, agent_id: str = "pixel") -> Path:
    """Validate the fresh session path generated by this mesh invocation."""
    if not session.is_absolute():
        raise ValueError("mesh agent session path is not absolute")
    state_info = state.lstat()
    if (
        state.is_symlink()
        or not stat.S_ISDIR(state_info.st_mode)
        or state_info.st_uid != os.geteuid()
        or stat.S_IMODE(state_info.st_mode) & 0o077
    ):
        raise ValueError("mesh agent state root is not an owner-private directory")
    if agent_id not in {"pixel", SYNTHESIS_AGENT_ID}:
        raise ValueError("mesh agent id is outside the closed registry")
    expected = (
        state / "pixel-mesh-synthesis"
        if agent_id == SYNTHESIS_AGENT_ID
        else state / "agents" / agent_id / "sessions"
    ).resolve(strict=True)
    if session.parent.resolve(strict=True) != expected or session.suffix != ".jsonl":
        raise ValueError("mesh agent session escaped its exact state directory")
    return session


def _attach_selected_execution_budget(
    evidence: dict[str, object],
    selected: dict[str, object] | None,
) -> None:
    """Attach the exact non-secret execution-budget selection to evidence.

    Validates the object's exact shape and values and fails closed on any
    mismatch. Prompt-controlled data can never reach this attachment path.
    """
    if selected is None:
        return
    if not isinstance(selected, dict):
        raise ValueError("selected execution-budget evidence drifted from its exact shape")
    timeout_seconds = selected.get("timeoutSeconds")
    max_model_calls = selected.get("maxModelCalls")
    max_tool_calls = selected.get("maxToolCalls")
    source = selected.get("source")
    shape_valid = (
        set(selected) == {"timeoutSeconds", "maxModelCalls", "maxToolCalls", "source"}
        and isinstance(timeout_seconds, int) and not isinstance(timeout_seconds, bool)
        and isinstance(max_model_calls, int) and not isinstance(max_model_calls, bool)
        and isinstance(max_tool_calls, int) and not isinstance(max_tool_calls, bool)
        and 1 <= timeout_seconds <= AGENT_TIMEOUT_SECONDS
        and 1 <= max_model_calls <= MAX_AGENT_MODEL_CALLS
        and 1 <= max_tool_calls <= MAX_AGENT_TOOL_CALLS
        and source in {"defaults", "owner-process-environment"}
    )
    coherent = (
        source != "defaults"
        or (timeout_seconds == AGENT_TIMEOUT_SECONDS
            and max_model_calls == MAX_AGENT_MODEL_CALLS
            and max_tool_calls == MAX_AGENT_TOOL_CALLS)
    )
    if not shape_valid or not coherent:
        raise ValueError("selected execution-budget evidence drifted from its exact shape")
    evidence["executionBudget"] = {
        "timeoutSeconds": timeout_seconds,
        "maxModelCalls": max_model_calls,
        "maxToolCalls": max_tool_calls,
        "source": source,
    }


_EXECUTION_BUDGET_RAW_LIMIT = 16


def _strict_execution_budget_value(raw: object, name: str, default: int) -> int:
    """Parse one owner-process budget override with a single strict grammar.

    A missing variable keeps its default. Present values accept only ASCII
    ``[1-9][0-9]*`` within a small bounded raw length and the integer range
    1..default. Leading zeros, signs, whitespace, Unicode digits, empty
    values, oversize input, and out-of-range values fail closed with one
    stable non-secret error naming the variable and allowed range.
    """
    if raw is None:
        return default
    if not isinstance(raw, str) or not raw.isascii() or len(raw) > _EXECUTION_BUDGET_RAW_LIMIT:
        raise ValueError(
            f"{name} must be an integer between 1 and {default} (decimal digits only)"
        )
    if not re.fullmatch(r"[1-9][0-9]*", raw):
        raise ValueError(
            f"{name} must be an integer between 1 and {default} (decimal digits only)"
        )
    value = int(raw, 10)
    if value < 1 or value > default:
        raise ValueError(
            f"{name} must be an integer between 1 and {default} (decimal digits only)"
        )
    return value


def selected_execution_budget() -> tuple[int, int, int, str]:
    """Select execution budgets once from the owner-process environment.

    Each environment value is captured exactly once via ``.get``. Missing
    values keep the defaults. Source is derived only from the captured raw
    values: any present valid override marks the selection source as
    owner-process-environment; invalid values fail closed.
    """
    raw_timeout = os.environ.get(EXECUTION_BUDGET_TIMEOUT_ENV)
    raw_max_model_calls = os.environ.get(EXECUTION_BUDGET_MAX_MODEL_CALLS_ENV)
    raw_max_tool_calls = os.environ.get(EXECUTION_BUDGET_MAX_TOOL_CALLS_ENV)
    timeout = _strict_execution_budget_value(
        raw_timeout, EXECUTION_BUDGET_TIMEOUT_ENV, AGENT_TIMEOUT_SECONDS,
    )
    max_model_calls = _strict_execution_budget_value(
        raw_max_model_calls, EXECUTION_BUDGET_MAX_MODEL_CALLS_ENV, MAX_AGENT_MODEL_CALLS,
    )
    max_tool_calls = _strict_execution_budget_value(
        raw_max_tool_calls, EXECUTION_BUDGET_MAX_TOOL_CALLS_ENV, MAX_AGENT_TOOL_CALLS,
    )
    source = "owner-process-environment" if (
        raw_timeout is not None
        or raw_max_model_calls is not None
        or raw_max_tool_calls is not None
    ) else "defaults"
    return timeout, max_model_calls, max_tool_calls, source


def selected_contract_authority_profile() -> tuple[str | None, tuple[str, ...] | None]:
    """Select one closed contract authority profile from the owner process.

    The environment is read exactly once. Prompt, stdin, the source config, and
    the contract envelope cannot select or widen the returned tool tuple.
    """
    raw_profile = os.environ.get(CONTRACT_AUTHORITY_PROFILE_ENV)
    if raw_profile is None:
        return None, None
    if not isinstance(raw_profile, str):
        raise ValueError(f"{CONTRACT_AUTHORITY_PROFILE_ENV} must be a string")
    try:
        encoded = raw_profile.encode("ascii", "strict")
    except UnicodeEncodeError as error:
        raise ValueError(
            f"{CONTRACT_AUTHORITY_PROFILE_ENV} must be an ASCII profile name"
        ) from error
    if (
        not 1 <= len(encoded) <= CONTRACT_AUTHORITY_PROFILE_MAX_BYTES
        or re.fullmatch(r"[a-z][a-z0-9-]*", raw_profile) is None
    ):
        raise ValueError(f"{CONTRACT_AUTHORITY_PROFILE_ENV} has an invalid profile name")
    allowed_tools = CONTRACT_AUTHORITY_PROFILES.get(raw_profile)
    if allowed_tools is None:
        raise ValueError(f"{CONTRACT_AUTHORITY_PROFILE_ENV} names an unknown profile")
    return raw_profile, allowed_tools


def read_only_authority_evidence(
    session: Path, state: Path, config_sha256: str, *,
    profile: str = "read-only",
    allowed_tools: tuple[str, ...] = READ_ONLY_ALLOWED_TOOLS,
    extra_evidence: dict[str, object] | None = None,
    workspace_identity_revalidator: Callable[[], None] | None = None,
    execution_budget: dict[str, object] | None = None,
) -> dict[str, object]:
    """Prove the authority-reduced transcript contains only allowed tool calls."""
    if profile == "read-only":
        if allowed_tools != READ_ONLY_ALLOWED_TOOLS:
            raise ValueError("read-only mesh authority allowlist drifted")
    elif profile == "web-read-only":
        if allowed_tools != CONTRACT_AUTHORITY_PROFILES["web-read-only"]:
            raise ValueError("web-read-only mesh authority allowlist drifted")
    elif profile in BROKER_MEDIATED_AUTHORITY_PROFILES:
        if allowed_tools != CONTRACT_AUTHORITY_PROFILES[profile]:
            raise ValueError(f"{profile} mesh authority allowlist drifted")
    elif profile == "sandboxed-readonly-workspace":
        if allowed_tools != SANDBOXED_READONLY_ALLOWED_TOOLS:
            raise ValueError("sandboxed-readonly-workspace mesh authority allowlist drifted")
    elif profile == "sandboxed-mailbox-readonly-workspace":
        if allowed_tools != SANDBOXED_MAILBOX_READONLY_ALLOWED_TOOLS:
            raise ValueError(
                "sandboxed-mailbox-readonly-workspace mesh authority allowlist drifted"
            )
    elif profile != "sandboxed-workspace":
        raise ValueError("mesh authority evidence named an unknown profile")
    records = read_session_records(
        checked_expected_session_path(session, state), expected_uid=os.geteuid(), single_link=True,
    )
    names = []
    for record in records:
        message = record.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        for part in content if isinstance(content, list) else []:
            if not isinstance(part, dict) or part.get("type") != "toolCall":
                continue
            name = part.get("name")
            if not isinstance(name, str) or name not in allowed_tools:
                raise ValueError(f"{profile} mesh turn recorded a tool outside its authority")
            names.append(name)
    if profile == "read-only":
        boundary = (
            "The ephemeral per-turn config exposed only the read tool; the owner-private "
            "durable predecessor transcript was independently checked before success."
        )
    elif profile == "web-read-only":
        boundary = (
            "The ephemeral per-turn config exposed only the public read-only Web Courier "
            "tool; it granted no local-file, posting, credential, or other external-effect "
            "authority, and the owner-private durable predecessor transcript was "
            "independently checked before success."
        )
    elif profile == "operations-broker":
        boundary = (
            "The ephemeral per-turn config exposed only Operations inventory, request, and "
            "job-wait tools. Exposure does not authorize an effect: the Operations broker "
            "remains authoritative for every target, action, policy tier, approval, execution, "
            "and reconciliation decision, and the owner-private durable predecessor transcript "
            "was independently checked before success."
        )
    elif profile == "download-staging":
        boundary = (
            "The ephemeral per-turn config exposed only Operations inventory, the dedicated "
            "download-staging tool, and job wait. The Operations broker remains authoritative "
            "for URL and domain policy, expected SHA-256, approval, quarantine execution, and "
            "terminal reconciliation, and the owner-private durable predecessor transcript "
            "was independently checked before success."
        )
    elif profile == "sandboxed-readonly-workspace":
        boundary = (
            "The ephemeral per-turn config bound the exact validated git workspace as the "
            "sandboxed read-only workspace; the owner-private durable predecessor transcript "
            "was independently checked before success."
        )
    elif profile == "sandboxed-mailbox-readonly-workspace":
        boundary = (
            "The ephemeral per-turn config bound the exact validated git workspace as the "
            "only writable host bind inside a networkless sandbox; it exposed only read-only "
            "Gmail projection and workspace-local report tools, never mailbox mutation, "
            "generic host execution, or other external-effect authority, and the owner-private "
            "durable predecessor transcript was independently checked before success."
        )
    else:
        boundary = (
            "The ephemeral per-turn config bound the exact validated git workspace as the "
            "sandboxed writable workspace; the owner-private durable predecessor transcript "
            "was independently checked before success."
        )
    if profile in {"read-only", "web-read-only"}:
        core_evidence = {
            "profile": profile,
            "configSha256": config_sha256,
            "externalEffectAuthority": False,
        }
    elif profile in BROKER_MEDIATED_AUTHORITY_PROFILES:
        core_evidence = {
            "profile": profile,
            "configSha256": config_sha256,
            "brokerMediatedAuthority": True,
            "perRequestPolicyAuthority": True,
            "acceptanceAuthority": False,
        }
    elif profile in {
        "sandboxed-workspace", "sandboxed-readonly-workspace",
        "sandboxed-mailbox-readonly-workspace",
    }:
        core_evidence = {
            "profile": profile,
            "configSha256": config_sha256,
            "workspaceIdentitySha256": None,
            "freshSession": True,
            "networkEnabled": False,
            "hostFilesystemAuthority": False,
            "externalEffectAuthority": False,
            "acceptanceAuthority": False,
            "workspaceReadAuthority": True,
            "workspaceWriteAuthority": profile in {
                "sandboxed-workspace", "sandboxed-mailbox-readonly-workspace",
            },
        }
        if profile == "sandboxed-mailbox-readonly-workspace":
            core_evidence.update({
                "mailboxReadAuthority": True,
                "mailboxMutationAuthority": False,
            })
    else:
        raise ValueError("mesh authority evidence named an unknown profile")
    evidence: dict[str, object] = {
        "profile": profile,
        "allowedTools": list(allowed_tools),
        "observedToolCallRecords": len(names),
        "observedTools": sorted(set(names)),
        "observedToolsInOrder": names,
        "configSha256": config_sha256,
        "boundary": boundary,
    }
    if profile in {
        "read-only", "web-read-only", "sandboxed-workspace",
        "sandboxed-readonly-workspace", "sandboxed-mailbox-readonly-workspace",
    }:
        evidence["externalEffectAuthority"] = False
    if profile in BROKER_MEDIATED_AUTHORITY_PROFILES:
        evidence.update({
            "brokerMediatedAuthority": True,
            "perRequestPolicyAuthority": True,
            "acceptanceAuthority": False,
        })
    if profile in {
        "sandboxed-workspace", "sandboxed-readonly-workspace",
        "sandboxed-mailbox-readonly-workspace",
    }:
        evidence["workspaceReadAuthority"] = True
        evidence["workspaceWriteAuthority"] = profile in {
            "sandboxed-workspace", "sandboxed-mailbox-readonly-workspace",
        }
        if profile == "sandboxed-mailbox-readonly-workspace":
            evidence["mailboxReadAuthority"] = True
            evidence["mailboxMutationAuthority"] = False
    _attach_selected_execution_budget(evidence, execution_budget)
    if extra_evidence is not None:
        overlap = set(evidence) & set(extra_evidence)
        if overlap:
            raise ValueError("optional authority evidence overlapped a core evidence key")
        evidence.update(extra_evidence)
    if profile in BROKER_MEDIATED_AUTHORITY_PROFILES:
        for key, required in core_evidence.items():
            if evidence.get(key) != required:
                raise ValueError(f"{profile} authority evidence core key drifted: {key}")
        if (
            not isinstance(evidence.get("sourceConfigSha256"), str)
            or re.fullmatch(r"[a-f0-9]{64}", evidence["sourceConfigSha256"]) is None
            or evidence.get("sourceConfigIdentityStable") is not True
        ):
            raise ValueError(f"{profile} authority evidence lost its source config binding")
    if profile in {
        "sandboxed-workspace", "sandboxed-readonly-workspace",
        "sandboxed-mailbox-readonly-workspace",
    }:
        # Core evidence keys must survive optional-field attachment unchanged.
        for key, required in core_evidence.items():
            if key not in evidence:
                raise ValueError("sandboxed authority evidence lost a core key")
            if required is not None and evidence[key] != required:
                raise ValueError(f"sandboxed authority evidence core key drifted: {key}")
        if not isinstance(evidence.get("workspaceIdentitySha256"), str) or not re.fullmatch(
            r"[a-f0-9]{64}", evidence["workspaceIdentitySha256"],
        ):
            raise ValueError("sandboxed authority evidence lost its workspace identity hash")
        if workspace_identity_revalidator is not None:
            workspace_identity_revalidator()
    return evidence


def attach_authority_evidence(response_bytes: bytes, evidence: dict[str, object]) -> bytes:
    try:
        response = json.loads(response_bytes)
    except json.JSONDecodeError as error:
        raise ValueError("OpenClaw authority response is invalid JSON") from error
    result = response.get("result") if isinstance(response, dict) else None
    meta = result.get("meta") if isinstance(result, dict) else None
    if not isinstance(meta, dict) or "meshAuthority" in meta:
        raise ValueError("OpenClaw authority response has an invalid metadata boundary")
    meta["meshAuthority"] = evidence
    return (json.dumps(response, separators=(",", ":")) + "\n").encode("utf-8")


def _reasoning_without_visible_output_retained(
    records: list[dict[str, object]],
) -> str | None:
    """Return retained thinking only for an exact silent-length terminal."""
    terminal = None
    for record in reversed(records):
        candidate = record.get("message")
        if not isinstance(candidate, dict) or candidate.get("role") != "assistant" or candidate.get("api") == "cli":
            continue
        terminal = candidate
        break
    if (
        not isinstance(terminal, dict)
        or terminal.get("stopReason") != "length"
        or terminal.get("errorMessage") is not None
    ):
        return None
    content = terminal.get("content")
    if not isinstance(content, list) or any(
        not isinstance(part, dict) or part.get("type") not in {"text", "thinking"}
        for part in content
    ):
        return None
    thinking_parts = []
    for part in content:
        if part.get("type") == "text":
            text = part.get("text")
            if not isinstance(text, str) or text.strip():
                return None
            continue
        thinking = part.get("thinking")
        if not isinstance(thinking, str):
            return None
        if thinking:
            thinking_parts.append(thinking)
    retained = "\n".join(thinking_parts).strip()
    if not retained:
        return None
    return retained


def _terminal_reasoning_without_visible_output(session: Path, state: Path) -> bool:
    """True only when the durable terminal is an exact silent-length stop."""
    try:
        records = _decode_session_records(_read_session_payload(
            checked_expected_session_path(session, state),
            expected_uid=os.geteuid(), single_link=True,
        ))
    except (OSError, ValueError):
        return False
    return _reasoning_without_visible_output_retained(records) is not None


def _synthesis_recovery_context(
    message: bytes, session: Path, state: Path, recovery_reason: str,
) -> tuple[bytes, str] | None:
    """Build one bounded synthesis pass from an authenticated exploration suffix."""
    predecessor_payload = _read_session_payload(
        checked_expected_session_path(session, state), expected_uid=os.geteuid(), single_link=True,
    )
    records = _decode_session_records(predecessor_payload)
    retained = ""
    if recovery_reason == CONTRACT_TERMINAL_SYNTHESIS:
        retained = terminal_assistant_text(records, -1) or ""
    elif recovery_reason == OUTPUT_LENGTH_RECOVERY:
        terminal = None
        for record in reversed(records):
            candidate = record.get("message")
            if not isinstance(candidate, dict) or candidate.get("role") != "assistant" or candidate.get("api") == "cli":
                continue
            terminal = candidate
            break
        if (
            not isinstance(terminal, dict)
            or terminal.get("stopReason") != "length"
            or terminal.get("errorMessage") is not None
        ):
            return None
        content = terminal.get("content")
        if not isinstance(content, list) or any(
            not isinstance(part, dict) or part.get("type") not in {"text", "thinking"}
            for part in content
        ):
            return None
        retained_parts = []
        for part in content:
            value = part.get("thinking") if part.get("type") == "thinking" else part.get("text")
            if isinstance(value, str) and value:
                retained_parts.append(value)
        retained = "\n".join(retained_parts).strip()
    elif recovery_reason in {
        COMPACTION_BUDGET_RECOVERY, OVERFLOW_BUDGET_RECOVERY, TRANSCRIPT_OVERFLOW_RECOVERY,
    }:
        if recovery_reason == TRANSCRIPT_OVERFLOW_RECOVERY:
            terminal = None
            for record in reversed(records):
                candidate = record.get("message")
                if (
                    isinstance(candidate, dict)
                    and candidate.get("role") == "assistant"
                    and candidate.get("api") != "cli"
                ):
                    terminal = candidate
                    break
            content = terminal.get("content") if isinstance(terminal, dict) else None
            if (
                not isinstance(terminal, dict)
                or terminal.get("stopReason") != "error"
                or terminal.get("errorMessage") != TOOL_LOOP_CONTEXT_OVERFLOW
                or not isinstance(content, list)
                or any(
                    not isinstance(part, dict)
                    or part.get("type") != "text"
                    or part.get("text") not in {None, ""}
                    for part in content
                )
            ):
                return None
        for record in reversed(records):
            if record.get("type") != "compaction":
                continue
            summary = record.get("summary")
            if (
                not isinstance(summary, str)
                or not isinstance(record.get("tokensBefore"), int)
                or not isinstance(record.get("details"), dict)
            ):
                return None
            retained = summary.strip()
            break
    else:
        return None
    retained_bytes = retained.encode("utf-8")
    if not retained_bytes or len(retained_bytes) > MAX_SYNTHESIS_EVIDENCE_BYTES:
        return None
    try:
        original = message.decode("utf-8", "strict")
    except UnicodeDecodeError:
        return None
    synthesis = (
        "You are Pixel's authority-reduced terminal synthesis pass. The exploration pass "
        "reached its bounded handoff after gathering evidence. Return the completed answer "
        "to the original objective using only the retained findings below. Do not request tools, "
        "do not invent missing evidence, and clearly label anything still blocked.\n\n"
        f"ORIGINAL OBJECTIVE\n{original}\n\nRETAINED FINDINGS\n{retained}\n"
    ).encode("utf-8")
    if len(synthesis) > MAX_MESSAGE_BYTES:
        return None
    return synthesis, hashlib.sha256(predecessor_payload).hexdigest()


def synthesis_recovery_message(
    message: bytes, session: Path, state: Path, recovery_reason: str,
) -> bytes | None:
    context = _synthesis_recovery_context(message, session, state, recovery_reason)
    return context[0] if context is not None else None


def recoverable_budget_synthesis_reason(
    result: subprocess.CompletedProcess[bytes],
    max_model_calls: int = MAX_AGENT_MODEL_CALLS,
    max_tool_calls: int = MAX_AGENT_TOOL_CALLS,
) -> str | None:
    for value, default, label in (
        (max_model_calls, MAX_AGENT_MODEL_CALLS, "model-call"),
        (max_tool_calls, MAX_AGENT_TOOL_CALLS, "tool-call"),
    ):
        if type(value) is not int or not 1 <= value <= default:
            raise ValueError(
                f"recoverable budget {label} limit must be an integer "
                f"between 1 and {default}"
            )
    if result.returncode != 124:
        return None
    expected = {
        f"Pixel mesh agent budget exceeded: compaction limit reached ({MAX_AGENT_COMPACTIONS + 1})\n".encode():
            COMPACTION_BUDGET_RECOVERY,
        f"Pixel mesh agent budget exceeded: tool-loop overflow limit reached ({MAX_AGENT_TOOL_LOOP_OVERFLOWS + 1})\n".encode():
            OVERFLOW_BUDGET_RECOVERY,
        f"Pixel mesh agent budget exceeded: reasoning-without-visible-output limit reached "
        f"({MAX_AGENT_SILENT_LENGTH_STREAK})\n".encode():
            REASONING_WITHOUT_VISIBLE_OUTPUT_RECOVERY,
        f"Pixel mesh agent budget exceeded: model-call limit reached ({max_model_calls})\n".encode():
            SELECTED_MODEL_BUDGET_RECOVERY,
        f"Pixel mesh agent budget exceeded: tool-call limit reached ({max_tool_calls})\n".encode():
            SELECTED_TOOL_BUDGET_RECOVERY,
    }
    return expected.get(result.stderr)


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("JSON object contains duplicate keys")
        value[key] = item
    return value


def _reject_json_constant(_value: str) -> object:
    raise ValueError("non-finite JSON number")


def _contract_schema_type(value: object, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "boolean":
        return type(value) is bool
    if expected == "integer":
        return type(value) is int and abs(value) <= MAX_CONTRACT_SAFE_INTEGER
    if expected == "number":
        return (
            type(value) is int and abs(value) <= MAX_CONTRACT_SAFE_INTEGER
            or type(value) is float and math.isfinite(value)
        )
    if expected == "string":
        return isinstance(value, str)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return False


def validate_contract_schema(schema: object) -> dict[str, object]:
    """Validate a bounded strict JSON-Schema subset used by contract synthesis."""
    nodes = [0]

    def visit(candidate: object, depth: int) -> None:
        nodes[0] += 1
        if nodes[0] > MAX_CONTRACT_SCHEMA_NODES or depth > MAX_CONTRACT_SCHEMA_DEPTH:
            raise ValueError("mesh contract schema exceeds its complexity limit")
        if not isinstance(candidate, dict):
            raise ValueError("mesh contract schema node is not an object")
        common = {"type", "description"}
        description = candidate.get("description")
        if description is not None:
            try:
                valid_description = (
                    isinstance(description, str)
                    and len(description.encode("utf-8")) <= 4096
                )
            except UnicodeEncodeError:
                valid_description = False
            if not valid_description:
                raise ValueError("mesh contract schema description is invalid")
        if "oneOf" in candidate:
            allowed = {"oneOf", "description"}
            options = candidate.get("oneOf")
            if (
                set(candidate) - allowed
                or not isinstance(options, list)
                or not 2 <= len(options) <= MAX_CONTRACT_ONE_OF_BRANCHES
            ):
                raise ValueError("mesh contract oneOf is invalid")
            canonical_options = []
            for option in options:
                visit(option, depth + 1)
                canonical_options.append(json.dumps(option, sort_keys=True, separators=(",", ":")))
            if len(set(canonical_options)) != len(canonical_options):
                raise ValueError("mesh contract oneOf contains duplicate branches")
            return
        expected = candidate.get("type")
        if (
            not isinstance(expected, str)
            or expected not in {"object", "array", "string", "integer", "number", "boolean", "null"}
        ):
            raise ValueError("mesh contract schema type is unsupported")
        if expected == "object":
            allowed = common | {"properties", "required", "additionalProperties"}
            properties = candidate.get("properties")
            required = candidate.get("required")
            if (
                not isinstance(properties, dict)
                or not 1 <= len(properties) <= 128
                or not isinstance(required, list)
                or len(required) != len(properties)
                or candidate.get("additionalProperties") is not False
            ):
                raise ValueError("mesh contract object schema is not strict")
            names = list(properties)
            if (
                any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.:-]{0,127}", name) for name in names)
                or any(not isinstance(name, str) for name in required)
                or len(set(required)) != len(required)
                or set(required) != set(names)
            ):
                raise ValueError("mesh contract object properties are invalid")
            for nested in properties.values():
                visit(nested, depth + 1)
        elif expected == "array":
            allowed = common | {"items", "minItems", "maxItems"}
            minimum = candidate.get("minItems", 0)
            maximum = candidate.get("maxItems")
            if (
                not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 0
                or not isinstance(maximum, int) or isinstance(maximum, bool)
                or maximum < minimum or maximum > 256
            ):
                raise ValueError("mesh contract array bounds are invalid")
            visit(candidate.get("items"), depth + 1)
        elif expected == "string":
            allowed = common | {"const", "enum", "minLength", "maxLength"}
            minimum = candidate.get("minLength", 0)
            maximum = candidate.get("maxLength", MAX_CONTRACT_OUTPUT_BYTES)
            if (
                not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 0
                or not isinstance(maximum, int) or isinstance(maximum, bool)
                or maximum < minimum or maximum > MAX_CONTRACT_OUTPUT_BYTES
            ):
                raise ValueError("mesh contract string bounds are invalid")
        elif expected in {"integer", "number"}:
            allowed = common | {"const", "enum", "minimum", "maximum"}
            minimum = candidate.get("minimum")
            maximum = candidate.get("maximum")
            if minimum is not None and not _contract_schema_type(minimum, "number"):
                raise ValueError("mesh contract numeric minimum is invalid")
            if maximum is not None and not _contract_schema_type(maximum, "number"):
                raise ValueError("mesh contract numeric maximum is invalid")
            if minimum is not None and maximum is not None and minimum > maximum:
                raise ValueError("mesh contract numeric bounds are reversed")
        else:
            allowed = common | {"const", "enum"}
        if set(candidate) - allowed:
            raise ValueError("mesh contract schema contains an unsupported keyword")
        if "const" in candidate:
            constant = candidate["const"]
            if "enum" in candidate or not _contract_schema_type(constant, expected):
                raise ValueError("mesh contract const is invalid")
            if expected == "string":
                try:
                    constant_bytes = constant.encode("utf-8")
                except UnicodeEncodeError as error:
                    raise ValueError("mesh contract string const is invalid") from error
                if (
                    len(constant_bytes) > MAX_CONTRACT_OUTPUT_BYTES
                    or not candidate.get("minLength", 0) <= len(constant) <= candidate.get(
                        "maxLength", MAX_CONTRACT_OUTPUT_BYTES,
                    )
                ):
                    raise ValueError("mesh contract string const is invalid")
            if (
                expected in {"integer", "number"}
                and (
                    candidate.get("minimum") is not None and constant < candidate["minimum"]
                    or candidate.get("maximum") is not None and constant > candidate["maximum"]
                )
            ):
                raise ValueError("mesh contract numeric const is outside its bounds")
        enumeration = candidate.get("enum")
        if enumeration is not None:
            if not isinstance(enumeration, list) or not 1 <= len(enumeration) <= 256:
                raise ValueError("mesh contract enum is invalid")
            if any(not _contract_schema_type(item, expected) for item in enumeration):
                raise ValueError("mesh contract enum value has the wrong type")
            if expected == "string":
                try:
                    valid_strings = all(
                        len(item.encode("utf-8")) <= MAX_CONTRACT_OUTPUT_BYTES
                        for item in enumeration
                    )
                except UnicodeEncodeError:
                    valid_strings = False
                if not valid_strings:
                    raise ValueError("mesh contract string enum is invalid")
            canonical_items = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in enumeration]
            if len(set(canonical_items)) != len(canonical_items):
                raise ValueError("mesh contract enum contains duplicates")

    visit(schema, 0)
    canonical = json.dumps(schema, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(canonical) > MAX_CONTRACT_SCHEMA_BYTES:
        raise ValueError("mesh contract schema exceeds its byte limit")
    return schema


def validate_contract_value(value: object, schema: dict[str, object], depth: int = 0) -> None:
    """Independently validate provider JSON against the admitted strict subset."""
    if depth > MAX_CONTRACT_SCHEMA_DEPTH:
        raise ValueError("mesh contract result exceeds its depth limit")
    options = schema.get("oneOf")
    if options is not None:
        matches = 0
        for option in options:
            try:
                validate_contract_value(value, option, depth + 1)
            except ValueError:
                continue
            matches += 1
        if matches != 1:
            raise ValueError("mesh contract result does not match exactly one oneOf branch")
        return
    expected = schema["type"]
    if not _contract_schema_type(value, expected):
        raise ValueError("mesh contract result has the wrong type")
    if "const" in schema and value != schema["const"]:
        raise ValueError("mesh contract result differs from its const")
    enumeration = schema.get("enum")
    if enumeration is not None and value not in enumeration:
        raise ValueError("mesh contract result is outside its enum")
    if expected == "object":
        properties = schema["properties"]
        if set(value) != set(properties):
            raise ValueError("mesh contract result has missing or extra properties")
        for name, nested in properties.items():
            validate_contract_value(value[name], nested, depth + 1)
    elif expected == "array":
        if not schema.get("minItems", 0) <= len(value) <= schema["maxItems"]:
            raise ValueError("mesh contract result array length is invalid")
        for item in value:
            validate_contract_value(item, schema["items"], depth + 1)
    elif expected == "string":
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", MAX_CONTRACT_OUTPUT_BYTES):
            raise ValueError("mesh contract result string length is invalid")
    elif expected in {"integer", "number"}:
        if schema.get("minimum") is not None and value < schema["minimum"]:
            raise ValueError("mesh contract result is below its minimum")
        if schema.get("maximum") is not None and value > schema["maximum"]:
            raise ValueError("mesh contract result is above its maximum")


def parse_contract_request(message: bytes) -> tuple[bytes, str, dict[str, object], str]:
    """Parse one bounded caller-supplied task/schema envelope without granting authority."""
    try:
        request = json.loads(
            message.decode("utf-8", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("mesh contract request is invalid JSON") from error
    if not isinstance(request, dict) or set(request) != {"schemaVersion", "task", "schemaName", "schema"}:
        raise ValueError("mesh contract request has unexpected fields")
    task = request.get("task")
    schema_name = request.get("schemaName")
    if (
        type(request.get("schemaVersion")) is not int
        or request.get("schemaVersion") != 1
        or not isinstance(task, str)
        or not task.strip()
        or not isinstance(schema_name, str)
    ):
        raise ValueError("mesh contract request identity or task is invalid")
    try:
        task_bytes = task.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("mesh contract task is not valid UTF-8 text") from error
    if len(task_bytes) > MAX_MESSAGE_BYTES or not re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_.:-]{0,127}", schema_name,
    ):
        raise ValueError("mesh contract task or schema name is invalid")
    schema = validate_contract_schema(request.get("schema"))
    schema_bytes = json.dumps(schema, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return task_bytes, schema_name, schema, hashlib.sha256(schema_bytes).hexdigest()


def contract_exploration_message(
    task: bytes, schema_name: str, schema: dict[str, object], schema_sha256: str,
) -> bytes:
    """Bind the validated strict result contract into the exploration objective.

    The exploration model must retain every required field itself. A later
    authority-reduced pass may not infer or manufacture a field that was absent
    from the durable terminal. The canonical schema and its digest are therefore
    presented before tool use, within the same bounded owner task message.
    """
    validate_contract_schema(schema)
    canonical = json.dumps(
        schema, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    if not hmac.compare_digest(hashlib.sha256(canonical).hexdigest(), schema_sha256):
        raise ValueError("mesh contract exploration schema binding drifted")
    if (
        not isinstance(schema_name, str)
        or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.:-]{0,127}", schema_name)
    ):
        raise ValueError("mesh contract exploration schema name is invalid")
    try:
        task_text = task.decode("utf-8", "strict")
        schema_text = canonical.decode("utf-8", "strict")
    except UnicodeDecodeError as error:
        raise ValueError("mesh contract exploration message is not valid UTF-8") from error
    rendered = (
        f"{task_text}\n\n"
        "PIXEL STRICT RESPONSE CONTRACT\n"
        "Return the terminal answer as exactly one JSON value matching the owner-supplied "
        "strict schema below. Do not add prose, Markdown, or code fences. Preserve every "
        "required observed field in that terminal JSON. Do not invent missing evidence. If "
        "the evidence is insufficient, use only a schema-permitted failure or blocked value; "
        "if no such value exists, the turn must fail closed.\n"
        f"Schema name: {schema_name}\n"
        f"Schema SHA-256: {schema_sha256}\n"
        f"Canonical schema: {schema_text}\n"
    ).encode("utf-8")
    if not rendered or len(rendered) > MAX_MESSAGE_BYTES:
        raise ValueError("mesh contract exploration message exceeds its byte limit")
    return rendered


def create_read_only_config(state: Path) -> tuple[Path, str]:
    """Create an owner-private ephemeral config that exposes only `read`."""
    config, _, _ = _load_bounded_owner_private_config(state)
    agents = config.get("agents") if isinstance(config, dict) else None
    agent_list = agents.get("list") if isinstance(agents, dict) else None
    matches = [
        agent for agent in agent_list if isinstance(agent, dict) and agent.get("id") == "pixel"
    ] if isinstance(agent_list, list) else []
    if len(matches) != 1:
        raise ValueError("OpenClaw config must contain exactly one Pixel agent")
    matches[0]["tools"] = {"allow": list(READ_ONLY_ALLOWED_TOOLS)}
    rendered = (json.dumps(config, separators=(",", ":")) + "\n").encode("utf-8")
    path, rendered_sha256, _expected_identity = _write_ephemeral_config(
        state, config, prefix=".pixel-mesh-read-only-",
    )
    if hashlib.sha256(rendered).hexdigest() != rendered_sha256:
        raise ValueError("read-only ephemeral config content drifted")
    return path, rendered_sha256


def create_contract_authority_config(
    state: Path, profile: str, allowed_tools: tuple[str, ...],
) -> tuple[Path, str, tuple[int, int, int, int, int]]:
    """Create one closed-profile, owner-private config for a contract turn."""
    expected_tools = CONTRACT_AUTHORITY_PROFILES.get(profile)
    if expected_tools is None or allowed_tools != expected_tools:
        raise ValueError("contract authority profile or allowlist drifted")
    config, _, _ = _load_bounded_owner_private_config(state)
    agents = config.get("agents") if isinstance(config, dict) else None
    agent_list = agents.get("list") if isinstance(agents, dict) else None
    matches = [
        agent for agent in agent_list if isinstance(agent, dict) and agent.get("id") == "pixel"
    ] if isinstance(agent_list, list) else []
    if len(matches) != 1:
        raise ValueError("OpenClaw config must contain exactly one Pixel agent")
    tools = config.get("tools") if isinstance(config, dict) else None
    if not isinstance(tools, dict):
        raise ValueError("OpenClaw config must contain one tools object")
    denied = tools.get("deny", [])
    if not isinstance(denied, list) or any(not isinstance(name, str) for name in denied):
        raise ValueError("OpenClaw config tools deny policy must be a string list")
    if any(name in denied for name in allowed_tools):
        raise ValueError("contract authority profile conflicts with the source deny policy")
    matches[0]["tools"] = {"allow": list(allowed_tools)}
    tools["alsoAllow"] = list(allowed_tools)
    rendered = (json.dumps(config, separators=(",", ":")) + "\n").encode("utf-8")
    path, rendered_sha256, expected_identity = _write_ephemeral_config(
        state, config, prefix=f".pixel-mesh-contract-{profile}-",
    )
    try:
        if hashlib.sha256(rendered).hexdigest() != rendered_sha256:
            raise ValueError("contract authority ephemeral config content drifted")
        persisted = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
        persisted_agents = persisted.get("agents") if isinstance(persisted, dict) else None
        persisted_list = (
            persisted_agents.get("list") if isinstance(persisted_agents, dict) else None
        )
        persisted_matches = [
            agent for agent in persisted_list
            if isinstance(agent, dict) and agent.get("id") == "pixel"
        ] if isinstance(persisted_list, list) else []
        persisted_tools = persisted.get("tools") if isinstance(persisted, dict) else None
        if (
            len(persisted_matches) != 1
            or persisted_matches[0].get("tools") != {"allow": list(allowed_tools)}
            or not isinstance(persisted_tools, dict)
            or persisted_tools.get("alsoAllow") != list(allowed_tools)
        ):
            raise ValueError("contract authority ephemeral config allowlist drifted")
    except BaseException:
        _remove_and_verify_ephemeral_config(path, expected_identity)
        raise
    return path, rendered_sha256, expected_identity


def synthesis_provider_config(state: Path) -> tuple[str, str, str, str, int]:
    """Resolve one credential-free loopback OpenAI-compatible synthesis target."""
    path = state / "openclaw.json"
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077
            or info.st_size < 2
            or info.st_size > MAX_OPENCLAW_CONFIG_BYTES
        ):
            raise ValueError("OpenClaw config is not an owner-private bounded regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            payload = source.read(MAX_OPENCLAW_CONFIG_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(payload) > MAX_OPENCLAW_CONFIG_BYTES:
        raise ValueError("OpenClaw config exceeded its size limit")
    try:
        config = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("OpenClaw config is invalid") from error
    agents = config.get("agents") if isinstance(config, dict) else None
    configured = agents.get("list") if isinstance(agents, dict) else None
    if not isinstance(configured, list):
        raise ValueError("OpenClaw primary agent is unavailable")
    by_id = {}
    for agent in configured:
        identifier = agent.get("id") if isinstance(agent, dict) else None
        if not isinstance(identifier, str) or identifier in by_id:
            raise ValueError("OpenClaw agent registry is invalid or duplicated")
        by_id[identifier] = agent
    primary = by_id.get("pixel")
    model_reference = primary.get("model") if isinstance(primary, dict) else None
    if not isinstance(model_reference, str) or "/" not in model_reference:
        raise ValueError("OpenClaw primary model reference is invalid")
    provider_id, model_id = model_reference.split("/", 1)
    if (
        not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", provider_id)
        or not re.fullmatch(r"[A-Za-z0-9._/-]{1,256}", model_id)
    ):
        raise ValueError("OpenClaw primary model reference is unsafe")
    models = config.get("models") if isinstance(config, dict) else None
    providers = models.get("providers") if isinstance(models, dict) else None
    provider = providers.get(provider_id) if isinstance(providers, dict) else None
    base_url = provider.get("baseUrl") if isinstance(provider, dict) else None
    api_key = provider.get("apiKey") if isinstance(provider, dict) else None
    registry = provider.get("models") if isinstance(provider, dict) else None
    if not isinstance(provider, dict) or provider.get("api") != "openai-completions":
        raise ValueError("Pixel synthesis requires an OpenAI-compatible provider")
    if (
        not isinstance(base_url, str)
        or not isinstance(api_key, str)
        or not api_key
        or len(api_key) > 4096
        or any(ord(char) < 0x20 or ord(char) == 0x7F for char in api_key)
    ):
        raise ValueError("Pixel synthesis provider credentials are invalid")
    parsed = urlsplit(base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or ".." in Path(parsed.path).parts
    ):
        raise ValueError("Pixel synthesis provider must be a credential-free loopback URL")
    matches = [
        item for item in registry if isinstance(item, dict) and item.get("id") == model_id
    ] if isinstance(registry, list) else []
    if len(matches) != 1:
        raise ValueError("Pixel synthesis model is absent or duplicated")
    configured_max = matches[0].get("maxTokens")
    if isinstance(configured_max, bool) or not isinstance(configured_max, int) or configured_max < 1:
        raise ValueError("Pixel synthesis model output limit is invalid")
    endpoint = base_url.rstrip("/") + "/chat/completions"
    return endpoint, api_key, provider_id, model_id, min(configured_max, MAX_SYNTHESIS_OUTPUT_TOKENS)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def ensure_synthesis_receipt_directory(state: Path) -> Path:
    state_info = state.lstat()
    if (
        state.is_symlink()
        or not stat.S_ISDIR(state_info.st_mode)
        or state_info.st_uid != os.geteuid()
        or stat.S_IMODE(state_info.st_mode) & 0o077
    ):
        raise ValueError("mesh agent state root is not an owner-private directory")
    directory = state / "pixel-mesh-synthesis"
    try:
        os.mkdir(directory, 0o700)
    except FileExistsError:
        pass
    info = directory.lstat()
    if (
        directory.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) & 0o077
        or directory.parent.resolve(strict=True) != state.resolve(strict=True)
    ):
        raise ValueError("Pixel synthesis receipt directory is not owner-private")
    return directory


def write_synthesis_receipt(path: Path, record: dict[str, object]) -> None:
    payload = (json.dumps(record, separators=(",", ":")) + "\n").encode("utf-8")
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written < 1:
                raise OSError("short write for Pixel synthesis receipt")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def verify_synthesis_predecessor_binding(
    receipt: Path, predecessor: Path, state: Path, expected_sha256: str,
    *, authority_profile: str | None = None,
    authority_config_sha256: str | None = None,
) -> None:
    """Re-read and verify the receipt's exact predecessor content binding."""
    checked_expected_session_path(receipt, state, SYNTHESIS_AGENT_ID)
    receipt_records = read_session_records(
        receipt, expected_uid=os.geteuid(), single_link=True,
    )
    if len(receipt_records) != 1:
        raise ValueError("Pixel synthesis receipt must contain exactly one record")
    binding = receipt_records[0].get("meshSynthesis")
    if (
        not isinstance(binding, dict)
        or binding.get("predecessorSessionFile") != str(predecessor)
        or binding.get("predecessorSessionSha256") != expected_sha256
    ):
        raise ValueError("Pixel synthesis receipt lost its exact predecessor binding")
    if (authority_profile is None) != (authority_config_sha256 is None):
        raise ValueError("Pixel synthesis authority binding is incomplete")
    if authority_profile is not None and (
        binding.get("authorityProfile") != authority_profile
        or binding.get("authorityConfigSha256") != authority_config_sha256
    ):
        raise ValueError("Pixel synthesis receipt lost its exact authority binding")
    current_payload = _read_session_payload(
        checked_expected_session_path(predecessor, state),
        expected_uid=os.geteuid(), single_link=True,
    )
    if hashlib.sha256(current_payload).hexdigest() != expected_sha256:
        raise ValueError("Pixel synthesis predecessor changed after evidence retention")


def run_deterministic_selected_budget_blocked_recovery(
    session: Path, state: Path, recovery_reason: str,
) -> bytes | None:
    """Return one receipt-bound blocked terminal for selected budget exhaustion.

    Only the two closed selected model-call/tool-call exhaustion reasons are
    accepted. No model call is made, no hidden reasoning is used or exposed,
    and the exact owner-private predecessor is retained under custody with a
    fresh one-record synthesis receipt bound to its content hash.
    """
    if recovery_reason not in SELECTED_BUDGET_RECOVERY_REASONS:
        raise ValueError("deterministic selected-budget recovery reason is outside the closed set")
    predecessor_payload = _read_session_payload(
        checked_expected_session_path(session, state),
        expected_uid=os.geteuid(), single_link=True,
    )
    if not _decode_session_records(predecessor_payload):
        return None
    predecessor_sha256 = hashlib.sha256(predecessor_payload).hexdigest()
    synthesis_id = str(uuid.uuid4())
    synthesis_path = ensure_synthesis_receipt_directory(state) / f"{synthesis_id}.jsonl"
    if synthesis_path.exists() or synthesis_path.is_symlink():
        raise ValueError("fresh synthesis receipt path already exists")
    content = (
        "Blocked: the exploration pass exhausted its selected execution budget "
        "before a completed result was accepted. No recovery model call was "
        "made and no hidden reasoning was exposed. The durable exploration "
        "transcript is preserved for a scoped retry and independent verification."
    )
    compatible_usage = {
        "input": 0, "cacheRead": 0, "cacheWrite": 0, "output": 0, "totalTokens": 0,
    }
    receipt_record = {
        "type": "message",
        "id": synthesis_id,
        "timestamp": now(),
        "message": {
            "role": "assistant",
            "api": "pixel-deterministic-recovery",
            "provider": "pixel-mesh",
            "model": "deterministic-blocked-v1",
            "responseId": None,
            "stopReason": "stop",
            "errorMessage": None,
            "content": [{"type": "text", "text": content}],
            "usage": compatible_usage,
        },
        "meshSynthesis": {
            "reason": recovery_reason,
            "predecessorSessionFile": str(session),
            "predecessorSessionSha256": predecessor_sha256,
            "toolSchemaPresent": False,
            "transport": "deterministic-no-model",
        },
    }
    write_synthesis_receipt(synthesis_path, receipt_record)
    verify_synthesis_predecessor_binding(
        synthesis_path, session, state, predecessor_sha256,
    )
    envelope = {
        "status": "ok",
        "summary": "deterministic-blocked-budget-recovery",
        "result": {
            "payloads": [{
                "text": content, "livenessState": "completed", "stopReason": "stop",
                "blocked": True,
            }],
            "meta": {
                "livenessState": "completed",
                "stopReason": "stop",
                "error": None,
                "blocked": True,
                "agentMeta": {
                    "sessionFile": str(synthesis_path),
                    "predecessorSessionFile": str(session),
                    "predecessorSessionSha256": predecessor_sha256,
                    "provider": "pixel-mesh",
                    "model": "deterministic-blocked-v1",
                    "usage": compatible_usage,
                },
                "meshSynthesisRecovery": {
                    "reason": recovery_reason,
                    "explorationSessionFile": str(session),
                    "explorationSessionSha256": predecessor_sha256,
                    "synthesisSessionFile": str(synthesis_path),
                    "synthesisAgent": SYNTHESIS_AGENT_ID,
                    "mechanism": "deterministic-no-model-blocked",
                    "toolAuthority": "none",
                    "externalEffectAuthority": False,
                    "completionAuthority": False,
                    "acceptanceAuthority": False,
                    "blocked": True,
                    "requiresIndependentVerification": True,
                    "boundary": (
                        "The exploration pass exhausted its selected execution budget; no "
                        "recovery model call was made, no hidden reasoning was exposed or "
                        "treated as evidence, and the fixed blocked result grants no completion, "
                        "external-effect, deployment, publication, or acceptance authority."
                    ),
                },
            },
        },
    }
    return (json.dumps(envelope, separators=(",", ":")) + "\n").encode("utf-8")


def run_deterministic_reasoning_blocked_recovery(
    session: Path, state: Path,
) -> bytes | None:
    """Return one receipt-bound blocked terminal without another model call."""
    predecessor_payload = _read_session_payload(
        checked_expected_session_path(session, state),
        expected_uid=os.geteuid(), single_link=True,
    )
    records = _decode_session_records(predecessor_payload)
    if _reasoning_without_visible_output_retained(records) is None:
        return None
    predecessor_sha256 = hashlib.sha256(predecessor_payload).hexdigest()
    synthesis_id = str(uuid.uuid4())
    synthesis_path = ensure_synthesis_receipt_directory(state) / f"{synthesis_id}.jsonl"
    if synthesis_path.exists() or synthesis_path.is_symlink():
        raise ValueError("fresh synthesis receipt path already exists")
    content = (
        "Blocked: bounded reasoning ended without visible output; no completed result "
        "was accepted. The durable exploration transcript is preserved for a scoped "
        "retry and independent verification."
    )
    compatible_usage = {
        "input": 0, "cacheRead": 0, "cacheWrite": 0, "output": 0, "totalTokens": 0,
    }
    receipt_record = {
        "type": "message",
        "id": synthesis_id,
        "timestamp": now(),
        "message": {
            "role": "assistant",
            "api": "pixel-deterministic-recovery",
            "provider": "pixel-mesh",
            "model": "deterministic-blocked-v1",
            "responseId": None,
            "stopReason": "stop",
            "errorMessage": None,
            "content": [{"type": "text", "text": content}],
            "usage": compatible_usage,
        },
        "meshSynthesis": {
            "reason": REASONING_WITHOUT_VISIBLE_OUTPUT_RECOVERY,
            "predecessorSessionFile": str(session),
            "predecessorSessionSha256": predecessor_sha256,
            "toolSchemaPresent": False,
            "transport": "deterministic-no-model",
        },
    }
    write_synthesis_receipt(synthesis_path, receipt_record)
    verify_synthesis_predecessor_binding(
        synthesis_path, session, state, predecessor_sha256,
    )
    envelope = {
        "status": "ok",
        "summary": "deterministic-blocked-reasoning-recovery",
        "result": {
            "payloads": [{
                "text": content, "livenessState": "completed", "stopReason": "stop",
                "blocked": True,
            }],
            "meta": {
                "livenessState": "completed",
                "stopReason": "stop",
                "error": None,
                "blocked": True,
                "agentMeta": {
                    "sessionFile": str(synthesis_path),
                    "predecessorSessionFile": str(session),
                    "predecessorSessionSha256": predecessor_sha256,
                    "provider": "pixel-mesh",
                    "model": "deterministic-blocked-v1",
                    "usage": compatible_usage,
                },
                "meshSynthesisRecovery": {
                    "reason": REASONING_WITHOUT_VISIBLE_OUTPUT_RECOVERY,
                    "explorationSessionFile": str(session),
                    "explorationSessionSha256": predecessor_sha256,
                    "synthesisSessionFile": str(synthesis_path),
                    "synthesisAgent": SYNTHESIS_AGENT_ID,
                    "mechanism": "deterministic-no-model-blocked",
                    "toolAuthority": "none",
                    "externalEffectAuthority": False,
                    "completionAuthority": False,
                    "acceptanceAuthority": False,
                    "blocked": True,
                    "requiresIndependentVerification": True,
                    "boundary": (
                        "No recovery model call was made and no hidden reasoning was exposed or "
                        "treated as evidence; the fixed blocked result grants no completion, "
                        "external-effect, deployment, publication, or acceptance authority."
                    ),
                },
            },
        },
    }
    return (json.dumps(envelope, separators=(",", ":")) + "\n").encode("utf-8")


def run_deterministic_contract_finalization(
    session: Path, state: Path, *, response_schema: dict[str, object],
    schema_name: str, schema_sha256: str, authority_profile: str | None = None,
    authority_config_sha256: str | None = None,
) -> bytes | None:
    """Validate and canonically retain the exploration terminal without a model call.

    Contract finalization may change representation only. It never derives a
    missing value from prose, tool output, hidden reasoning, or a second model.
    """
    if (authority_profile is None) != (authority_config_sha256 is None):
        return None
    if authority_profile is not None and (
        not isinstance(authority_profile, str)
        or authority_profile not in CONTRACT_AUTHORITY_PROFILES
        or not isinstance(authority_config_sha256, str)
        or not re.fullmatch(r"[a-f0-9]{64}", authority_config_sha256)
    ):
        return None
    if (
        not isinstance(schema_name, str)
        or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.:-]{0,127}", schema_name)
        or not isinstance(schema_sha256, str)
        or not re.fullmatch(r"[a-f0-9]{64}", schema_sha256)
    ):
        return None
    try:
        validate_contract_schema(response_schema)
        canonical_schema = json.dumps(
            response_schema, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError):
        return None
    if not hmac.compare_digest(hashlib.sha256(canonical_schema).hexdigest(), schema_sha256):
        return None
    predecessor_payload = _read_session_payload(
        checked_expected_session_path(session, state),
        expected_uid=os.geteuid(), single_link=True,
    )
    records = _decode_session_records(predecessor_payload)
    terminal = None
    terminal_index = None
    for index in range(len(records) - 1, -1, -1):
        record = records[index]
        candidate = record.get("message")
        if (
            isinstance(candidate, dict)
            and candidate.get("role") == "assistant"
            and candidate.get("api") != "cli"
        ):
            terminal = candidate
            terminal_index = index
            break
    if (
        not isinstance(terminal, dict)
        or terminal.get("stopReason") != "stop"
        or terminal.get("errorMessage") is not None
    ):
        return None
    if any(
        isinstance(record.get("message"), dict)
        and record["message"].get("api") != "cli"
        for record in records[(terminal_index or 0) + 1:]
    ):
        return None
    parts = terminal.get("content")
    if not isinstance(parts, list) or not parts or any(
        not isinstance(part, dict) or part.get("type") not in {"text", "thinking"}
        for part in parts
    ):
        return None
    terminal_text = "".join(
        part["text"] for part in parts
        if part.get("type") == "text" and isinstance(part.get("text"), str)
        and part.get("text")
    ).strip()
    if not terminal_text:
        return None
    try:
        contract_value = json.loads(
            terminal_text, object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
        validate_contract_value(contract_value, response_schema)
        canonical_content = json.dumps(
            contract_value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        )
    except (json.JSONDecodeError, UnicodeEncodeError, ValueError, TypeError):
        return None
    if len(canonical_content.encode("utf-8")) > MAX_CONTRACT_OUTPUT_BYTES:
        return None
    predecessor_sha256 = hashlib.sha256(predecessor_payload).hexdigest()
    finalization_id = str(uuid.uuid4())
    receipt_path = ensure_synthesis_receipt_directory(state) / f"{finalization_id}.jsonl"
    if receipt_path.exists() or receipt_path.is_symlink():
        raise ValueError("fresh deterministic contract receipt path already exists")
    usage = {"input": 0, "cacheRead": 0, "cacheWrite": 0, "output": 0, "totalTokens": 0}
    binding = {
        "reason": CONTRACT_TERMINAL_DETERMINISTIC,
        "predecessorSessionFile": str(session),
        "predecessorSessionSha256": predecessor_sha256,
        "toolSchemaPresent": False,
        "transport": "deterministic-no-model",
        "mode": "strict-json-contract",
        "contractSchemaName": schema_name,
        "contractSchemaSha256": schema_sha256,
        "contractValidated": True,
        "terminalEvidenceOnly": True,
        **({
            "authorityProfile": authority_profile,
            "authorityConfigSha256": authority_config_sha256,
        } if authority_profile is not None else {}),
    }
    receipt_record = {
        "type": "message", "id": finalization_id, "timestamp": now(),
        "message": {
            "role": "assistant", "api": "pixel-deterministic-contract",
            "provider": "pixel-mesh", "model": "deterministic-contract-v1",
            "responseId": None, "stopReason": "stop", "errorMessage": None,
            "content": [{"type": "text", "text": canonical_content}], "usage": usage,
        },
        "meshSynthesis": binding,
    }
    write_synthesis_receipt(receipt_path, receipt_record)
    verify_synthesis_predecessor_binding(
        receipt_path, session, state, predecessor_sha256,
        authority_profile=authority_profile,
        authority_config_sha256=authority_config_sha256,
    )
    recovery = {
        "reason": CONTRACT_TERMINAL_DETERMINISTIC,
        "explorationSessionFile": str(session),
        "explorationSessionSha256": predecessor_sha256,
        "synthesisSessionFile": str(receipt_path),
        "synthesisAgent": SYNTHESIS_AGENT_ID,
        "mechanism": "deterministic-no-model-contract",
        "toolAuthority": "none", "externalEffectAuthority": False,
        "completionAuthority": False, "acceptanceAuthority": False,
        "requiresIndependentVerification": True,
        "mode": "strict-json-contract", "contractSchemaName": schema_name,
        "contractSchemaSha256": schema_sha256, "contractValidated": True,
        "terminalEvidenceOnly": True,
        **({
            "authorityProfile": authority_profile,
            "authorityConfigSha256": authority_config_sha256,
        } if authority_profile is not None else {}),
        "boundary": (
            "No finalization model call was made. The exact durable exploration terminal "
            "was parsed, strictly validated, and canonically serialized without deriving "
            "or inventing fields; independent acceptance is still required."
        ),
    }
    envelope = {
        "status": "ok", "summary": "deterministic-contract-terminal",
        "result": {
            "payloads": [{
                "text": canonical_content, "livenessState": "completed", "stopReason": "stop",
            }],
            "meta": {
                "livenessState": "completed", "stopReason": "stop", "error": None,
                "agentMeta": {
                    "sessionFile": str(receipt_path),
                    "predecessorSessionFile": str(session),
                    "predecessorSessionSha256": predecessor_sha256,
                    "provider": "pixel-mesh", "model": "deterministic-contract-v1",
                    "usage": usage,
                },
                "meshSynthesisRecovery": recovery,
            },
        },
    }
    return (json.dumps(envelope, separators=(",", ":")) + "\n").encode("utf-8")


def run_direct_synthesis_recovery(
    message: bytes, session: Path, state: Path, deadline: float, recovery_reason: str,
    *, response_schema: dict[str, object] | None = None,
    schema_name: str | None = None, schema_sha256: str | None = None,
    authority_profile: str | None = None,
    authority_config_sha256: str | None = None,
) -> bytes | None:
    checked_expected_session_path(session, state)
    endpoint, api_key, provider_id, model_id, max_tokens = synthesis_provider_config(state)
    context = _synthesis_recovery_context(message, session, state, recovery_reason)
    if context is None:
        return None
    synthesis, predecessor_sha256 = context
    remaining = int(deadline - time.monotonic())
    if remaining < 1:
        return None
    contract_mode = response_schema is not None
    if (authority_profile is None) != (authority_config_sha256 is None):
        return None
    if authority_profile is not None and (
        not contract_mode
        or authority_profile not in CONTRACT_AUTHORITY_PROFILES
        or not re.fullmatch(r"[a-f0-9]{64}", authority_config_sha256 or "")
    ):
        return None
    if contract_mode and (
        not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.:-]{0,127}", schema_name or "")
        or not re.fullmatch(r"[a-f0-9]{64}", schema_sha256 or "")
    ):
        return None
    if contract_mode:
        try:
            validate_contract_schema(response_schema)
            canonical_schema = json.dumps(
                response_schema, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError):
            return None
        if not hmac.compare_digest(hashlib.sha256(canonical_schema).hexdigest(), schema_sha256):
            return None
    system_prompt = (
        "Produce one terminal answer from the supplied retained findings. "
        "No tools or external effects are available. Do not invent evidence."
    )
    if contract_mode:
        system_prompt += (
            " Return exactly one JSON object matching the supplied strict schema. "
            "Do not add prose, Markdown, or code fences."
        )
    request_payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": synthesis.decode("utf-8", "strict")},
        ],
        "max_tokens": max_tokens,
        "temperature": 0 if contract_mode else 0.1,
        "stream": False,
        **({"response_format": {"type": "json_schema", "json_schema": {
            "name": schema_name, "strict": True, "schema": response_schema,
        }}} if contract_mode else {}),
    }
    request_bytes = json.dumps(request_payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=request_bytes,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    synthesis_id = str(uuid.uuid4())
    synthesis_path = ensure_synthesis_receipt_directory(state) / f"{synthesis_id}.jsonl"
    if synthesis_path.exists() or synthesis_path.is_symlink():
        raise ValueError("fresh synthesis receipt path already exists")
    try:
        with opener.open(request, timeout=min(remaining, SYNTHESIS_TIMEOUT_SECONDS)) as response_handle:
            response_bytes = response_handle.read(MAX_SYNTHESIS_RESPONSE_BYTES + 1)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, socket.timeout):
        return None
    if len(response_bytes) > MAX_SYNTHESIS_RESPONSE_BYTES:
        return None
    try:
        provider_response = json.loads(response_bytes.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    choices = provider_response.get("choices") if isinstance(provider_response, dict) else None
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        return None
    choice = choices[0]
    answer = choice.get("message")
    content = answer.get("content") if isinstance(answer, dict) else None
    if (
        choice.get("finish_reason") != "stop"
        or not isinstance(answer, dict)
        or answer.get("role") != "assistant"
        or not isinstance(content, str)
        or not content.strip()
        or len(content.encode("utf-8")) > MAX_AGENT_OUTPUT_BYTES
        or answer.get("tool_calls") not in (None, [])
        or answer.get("function_call") is not None
        or answer.get("refusal") not in {None, ""}
    ):
        return None
    if contract_mode:
        try:
            contract_value = json.loads(
                content, object_pairs_hook=_strict_object,
                parse_constant=_reject_json_constant,
            )
            validate_contract_value(contract_value, response_schema)
            canonical_content = json.dumps(
                contract_value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            )
        except (json.JSONDecodeError, UnicodeEncodeError, ValueError, TypeError):
            return None
        if len(canonical_content.encode("utf-8")) > MAX_CONTRACT_OUTPUT_BYTES:
            return None
        content = canonical_content
    returned_model = provider_response.get("model")
    if not isinstance(returned_model, str) or not returned_model:
        return None
    response_id = provider_response.get("id")
    if response_id is not None and (not isinstance(response_id, str) or len(response_id) > 512):
        return None
    usage = provider_response.get("usage")
    if not isinstance(usage, dict):
        return None
    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    total_tokens = usage.get("total_tokens")
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in (prompt_tokens, completion_tokens, total_tokens)
    ) or total_tokens != prompt_tokens + completion_tokens:
        return None
    prompt_details = usage.get("prompt_tokens_details")
    cached_tokens = prompt_details.get("cached_tokens", 0) if isinstance(prompt_details, dict) else 0
    if (
        isinstance(cached_tokens, bool)
        or not isinstance(cached_tokens, int)
        or cached_tokens < 0
        or cached_tokens > prompt_tokens
    ):
        return None
    compatible_usage = {
        "input": prompt_tokens - cached_tokens,
        "cacheRead": cached_tokens,
        "cacheWrite": 0,
        "output": completion_tokens,
        "totalTokens": total_tokens,
    }
    receipt_record = {
        "type": "message",
        "id": synthesis_id,
        "timestamp": now(),
        "message": {
            "role": "assistant",
            "api": "pixel-direct-synthesis",
            "provider": provider_id,
            "model": returned_model,
            "responseId": response_id,
            "stopReason": "stop",
            "errorMessage": None,
            "content": [{"type": "text", "text": content.strip()}],
            "usage": compatible_usage,
        },
        "meshSynthesis": {
            "reason": recovery_reason,
            "predecessorSessionFile": str(session),
            "predecessorSessionSha256": predecessor_sha256,
            "requestSha256": hashlib.sha256(request_bytes).hexdigest(),
            "responseSha256": hashlib.sha256(response_bytes).hexdigest(),
            "toolSchemaPresent": False,
            "transport": "loopback-openai-compatible",
            **({
                "mode": "strict-json-contract",
                "contractSchemaName": schema_name,
                "contractSchemaSha256": schema_sha256,
                "contractValidated": True,
                **({
                    "authorityProfile": authority_profile,
                    "authorityConfigSha256": authority_config_sha256,
                } if authority_profile is not None else {}),
            } if contract_mode else {}),
        },
    }
    write_synthesis_receipt(synthesis_path, receipt_record)
    verify_synthesis_predecessor_binding(
        synthesis_path, session, state, predecessor_sha256,
        authority_profile=authority_profile,
        authority_config_sha256=authority_config_sha256,
    )
    envelope = {
        "status": "ok",
        "summary": "direct-loopback-terminal-synthesis",
        "result": {
            "payloads": [{"text": content.strip(), "livenessState": "completed", "stopReason": "stop"}],
            "meta": {
                "livenessState": "completed",
                "stopReason": "stop",
                "error": None,
                "agentMeta": {
                    "sessionFile": str(synthesis_path),
                    "predecessorSessionFile": str(session),
                    "predecessorSessionSha256": predecessor_sha256,
                    "provider": provider_id,
                    "model": returned_model,
                    "usage": compatible_usage,
                },
                "meshSynthesisRecovery": {
                    "reason": recovery_reason,
                    "explorationSessionFile": str(session),
                    "explorationSessionSha256": predecessor_sha256,
                    "synthesisSessionFile": str(synthesis_path),
                    "synthesisAgent": SYNTHESIS_AGENT_ID,
                    "mechanism": "direct-loopback-openai-compatible",
                    "toolAuthority": "none",
                    "externalEffectAuthority": False,
                    "completionAuthority": False,
                    "acceptanceAuthority": False,
                    "requiresIndependentVerification": True,
                    **({
                        "mode": "strict-json-contract",
                        "contractSchemaName": schema_name,
                        "contractSchemaSha256": schema_sha256,
                        "contractValidated": True,
                        **({
                            "authorityProfile": authority_profile,
                            "authorityConfigSha256": authority_config_sha256,
                        } if authority_profile is not None else {}),
                    } if contract_mode else {}),
                    "boundary": "A fresh direct loopback model call received no tool schema and synthesized only retained local findings; it grants no external-effect, deployment, publication, or acceptance authority.",
                },
            },
        },
    }
    return (json.dumps(envelope, separators=(",", ":")) + "\n").encode("utf-8")


def recover_post_final_compaction_failure(
    session: Path, state: Path, returncode: int, stderr: bytes,
) -> bytes | None:
    """Recover only a durable final text followed by OpenClaw's known compaction exit."""
    fatal_lines = [line for line in stderr.splitlines() if line.startswith(b"Error:")]
    match = POST_FINAL_COMPACTION_FAILURE.fullmatch(fatal_lines[0]) if len(fatal_lines) == 1 else None
    if (
        returncode != 1
        or match is None
    ):
        return None
    records = read_session_records(
        checked_expected_session_path(session, state), expected_uid=os.geteuid(), single_link=True,
    )
    terminal = None
    terminal_index = None
    for index in range(len(records) - 1, -1, -1):
        record = records[index]
        message = record.get("message")
        if message is None:
            continue
        if not isinstance(message, dict):
            return None
        if (
            message.get("role") != "assistant"
            or message.get("api") != "openai-completions"
            or message.get("stopReason") != "stop"
            or message.get("provider") != match.group("provider").decode("ascii")
            or message.get("model") != match.group("model").decode("ascii")
        ):
            return None
        content = message.get("content")
        if not isinstance(content, list) or any(
            not isinstance(part, dict) or part.get("type") not in {"text", "thinking"}
            for part in content
        ):
            return None
        terminal = "\n".join(
            str(part.get("text")) for part in content
            if isinstance(part, dict) and part.get("type") == "text" and part.get("text")
        ).strip()
        terminal_index = index
        break
    trailing = records[terminal_index + 1:] if terminal_index is not None else []
    if not terminal or not trailing or any(record.get("type") != "compaction" for record in trailing):
        return None
    response = {
        "status": "ok",
        "summary": "reconciled-terminal-text-requires-verification",
        "result": {
            "payloads": [{"text": terminal, "livenessState": "completed", "stopReason": "stop"}],
            "meta": {
                "agentMeta": {"sessionFile": str(session)},
                "meshAgentExitReconciled": {
                    "reason": "post-final-cli-transcript-already-compacted",
                    "exitCode": returncode,
                    "provider": match.group("provider").decode("ascii"),
                    "model": match.group("model").decode("ascii"),
                    "stderrSha256": hashlib.sha256(stderr).hexdigest(),
                    "completionAuthority": False,
                    "externalEffectAuthority": False,
                    "requiresIndependentVerification": True,
                    "boundary": "Recovered only already-durable terminal text; grants no external-effect success, retry, deployment, publication, or acceptance authority.",
                },
            },
        },
    }
    return (json.dumps(response, separators=(",", ":")) + "\n").encode("utf-8")


def process_identity(pid: int, proc_root: Path = Path("/proc")) -> tuple[int, int, int] | None:
    """Return (pid, kernel start-time ticks, uid) for one live Linux process."""
    try:
        process_root = proc_root / str(pid)
        info = process_root.stat()
        value = (process_root / "stat").read_text(encoding="utf-8")
        closing = value.rfind(")")
        fields = value[closing + 2:].split() if closing >= 0 else []
        # Fields after comm begin with field 3 (state); starttime is field 22.
        if len(fields) < 20:
            return None
        return pid, int(fields[19]), info.st_uid
    except (FileNotFoundError, PermissionError, OSError, ValueError):
        return None


def same_process(identity: tuple[int, int, int] | None, proc_root: Path = Path("/proc")) -> bool:
    return identity is not None and process_identity(identity[0], proc_root) == identity


def process_has_tree_token(pid: int, token: str, proc_root: Path = Path("/proc")) -> bool:
    path = proc_root / str(pid) / "environ"
    descriptor = None
    try:
        if path.parent.stat().st_uid != os.geteuid():
            return False
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        chunks = []
        remaining = PROCESS_ENVIRONMENT_LIMIT + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
        return False
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if len(payload) > PROCESS_ENVIRONMENT_LIMIT:
        return False
    marker = f"{TREE_TOKEN_ENV}={token}".encode("utf-8")
    return marker in payload.split(b"\0")


def marked_process_pidfds(token: str, proc_root: Path = Path("/proc")) -> list[tuple[int, int]]:
    """Open PID-stable handles only for same-UID descendants carrying our nonce.

    Embedded local-agent tools inherit the nonce even after setsid/reparenting,
    so they remain discoverable without sharing a persistent gateway process.
    """
    if not hasattr(os, "pidfd_open"):
        raise RuntimeError("Pixel mesh agent supervision requires Linux pidfd support")
    references = []
    try:
        entries = list(proc_root.iterdir())
    except OSError as error:
        raise RuntimeError("Pixel mesh agent supervision cannot inspect /proc") from error
    for entry in entries:
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if not process_has_tree_token(pid, token, proc_root):
            continue
        try:
            descriptor = os.pidfd_open(pid, 0)
        except (PermissionError, ProcessLookupError, OSError):
            continue
        # Recheck after pidfd_open: a reused PID cannot inherit the unguessable nonce.
        if not process_has_tree_token(pid, token, proc_root):
            os.close(descriptor)
            continue
        references.append((pid, descriptor))
    return references


def send_pidfd_signal(descriptor: int, signum: int) -> None:
    try:
        signal.pidfd_send_signal(descriptor, signum, None, 0)
    except ProcessLookupError:
        pass


def signal_marked_processes(token: str, signum: int) -> int:
    references = marked_process_pidfds(token)
    try:
        for _, descriptor in references:
            send_pidfd_signal(descriptor, signum)
    finally:
        for _, descriptor in references:
            os.close(descriptor)
    return len(references)


def terminate_agent_tree(root_pidfd: int, token: str,
                         grace_seconds: float = AGENT_TERMINATION_GRACE_SECONDS) -> int:
    """Terminate only the PID-stable root and nonce-marked descendants, including setsid children."""
    send_pidfd_signal(root_pidfd, signal.SIGTERM)
    deadline = time.monotonic() + max(0.0, grace_seconds)
    initial_found = None
    while True:
        found = signal_marked_processes(token, signal.SIGTERM)
        if initial_found is None:
            initial_found = found
        if found == 0 or time.monotonic() >= deadline:
            break
        time.sleep(0.05)
    send_pidfd_signal(root_pidfd, signal.SIGKILL)
    signal_marked_processes(token, signal.SIGKILL)
    return initial_found or 0


def write_transport_probe() -> bool:
    """Write JSON-safe whitespace so sshd promptly observes a closed client."""
    try:
        sys.stdout.buffer.write(b" ")
        sys.stdout.buffer.flush()
        return True
    except OSError as error:
        if error.errno in {errno.EPIPE, errno.ECONNRESET}:
            return False
        raise


class AgentSessionBudget:
    """Incrementally enforce one agent turn's bounded transcript budget."""

    def __init__(
        self, path: Path, *,
        max_model_calls: int = MAX_AGENT_MODEL_CALLS,
        max_tool_calls: int = MAX_AGENT_TOOL_CALLS,
    ):
        # Selected limits default to the existing module constants; an explicit
        # keyword pins an exact owner-process-selected limit. Keywords must be
        # exact built-in non-bool integers within 1..module default and fail
        # closed on anything else.
        for keyword, value, default, label in (
            ("max_model_calls", max_model_calls, MAX_AGENT_MODEL_CALLS, "model-call"),
            ("max_tool_calls", max_tool_calls, MAX_AGENT_TOOL_CALLS, "tool-call"),
        ):
            if type(value) is not int or value < 1 or value > default:
                raise ValueError(
                    f"agent session budget {label} limit must be an integer "
                    f"between 1 and {default}"
                )
        self.path = path
        self.max_model_calls = max_model_calls
        self.max_tool_calls = max_tool_calls
        self.offset = 0
        self.pending = b""
        self.identity: tuple[int, int, int] | None = None
        self.model_calls = 0
        self.tool_calls = 0
        self.compactions = 0
        self.tool_loop_overflows = 0
        self.logical_progress = 0
        self.seen_response_ids: set[str] = set()
        self.seen_tool_call_ids: set[str] = set()
        self.compaction_progress: dict[str, int] = {}
        self.last_tool_loop_overflow_progress: int | None = None
        self.replay_seen_since_progress = False
        self.silent_length_streak = 0

    def _advance_progress(self) -> None:
        self.logical_progress += 1
        self.replay_seen_since_progress = False

    def _compaction_key(self, record: dict[str, object]) -> str | None:
        """Return a stable key only for a fully identified OpenClaw compaction."""
        summary = record.get("summary")
        tokens_before = record.get("tokensBefore")
        details = record.get("details")
        if not isinstance(summary, str) or not isinstance(tokens_before, int) or not isinstance(details, dict):
            return None
        canonical = json.dumps(
            {"summary": summary, "tokensBefore": tokens_before, "details": details},
            sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def _update_silent_length_streak(self, message: dict[str, object]) -> None:
        """Track consecutive thinking-only/empty length stops of new calls."""
        content = message.get("content", [])
        parts = content if isinstance(content, list) else []
        has_visible_text = any(
            isinstance(part, dict)
            and part.get("type") == "text"
            and isinstance(part.get("text"), str)
            and part.get("text").strip()
            for part in parts
        )
        has_tool_call = any(
            isinstance(part, dict) and part.get("type") == "toolCall"
            for part in parts
        )
        if message.get("stopReason") == "stop" or has_visible_text or has_tool_call:
            self.silent_length_streak = 0
        elif message.get("stopReason") == "length" and message.get("errorMessage") is None:
            self.silent_length_streak += 1

    def _record(self, record: dict[str, object]) -> None:
        if record.get("type") == "compaction":
            key = self._compaction_key(record)
            # Compaction records have no stable event ID. Deduplicate them only
            # after a stable response/tool-call replay proves transcript replay;
            # identical consecutive records remain distinct and fail closed.
            if (
                key is not None
                and self.compaction_progress.get(key) == self.logical_progress
                and self.replay_seen_since_progress
            ):
                return
            self.compactions += 1
            if key is not None:
                self.compaction_progress[key] = self.logical_progress
        message = record.get("message")
        if not isinstance(message, dict):
            return
        error_message = message.get("errorMessage")
        if (
            message.get("role") == "assistant"
            and message.get("api") != "cli"
            and (message.get("provider") or message.get("model"))
            and not (message.get("stopReason") == "error" and isinstance(error_message, str))
        ):
            response_id = message.get("responseId")
            if isinstance(response_id, str) and response_id:
                if response_id in self.seen_response_ids:
                    self.replay_seen_since_progress = True
                else:
                    self.seen_response_ids.add(response_id)
                    self.model_calls += 1
                    self._advance_progress()
                    self._update_silent_length_streak(message)
            else:
                self.model_calls += 1
                self._advance_progress()
                self._update_silent_length_streak(message)
        content = message.get("content", [])
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict) or part.get("type") != "toolCall":
                    continue
                tool_call_id = part.get("id")
                if isinstance(tool_call_id, str) and tool_call_id:
                    if tool_call_id in self.seen_tool_call_ids:
                        self.replay_seen_since_progress = True
                        continue
                    self.seen_tool_call_ids.add(tool_call_id)
                self.tool_calls += 1
                self._advance_progress()
        if isinstance(error_message, str) and "during tool loop" in error_message.lower():
            if (
                self.last_tool_loop_overflow_progress == self.logical_progress
                and self.replay_seen_since_progress
            ):
                return
            self.tool_loop_overflows += 1
            self.last_tool_loop_overflow_progress = self.logical_progress

    def observe(self) -> str | None:
        """Consume newly durable JSONL records and return a fail-closed reason."""
        descriptor = None
        try:
            descriptor = os.open(
                self.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            )
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) & 0o077
                or info.st_size > MAX_SESSION_BYTES
            ):
                raise ValueError(
                    "agent budget session must be a bounded owner-private regular file"
                )
            identity = (info.st_dev, info.st_ino, info.st_uid)
            if self.identity is None:
                self.identity = identity
            elif identity != self.identity:
                raise ValueError("agent budget session identity changed")
            if info.st_size < self.offset:
                raise ValueError("agent budget session was truncated")
            remaining = info.st_size - self.offset
            os.lseek(descriptor, self.offset, os.SEEK_SET)
            chunks = []
            while remaining:
                chunk = os.read(descriptor, min(65536, remaining))
                if not chunk:
                    raise ValueError("agent budget session changed while being read")
                chunks.append(chunk)
                remaining -= len(chunk)
            self.offset = info.st_size
        except FileNotFoundError:
            return None
        finally:
            if descriptor is not None:
                os.close(descriptor)

        payload = self.pending + b"".join(chunks)
        lines = payload.split(b"\n")
        self.pending = lines.pop()
        for line in lines:
            try:
                record = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ValueError("agent budget session contains invalid JSONL") from error
            if not isinstance(record, dict):
                raise ValueError("agent budget session JSONL record is not an object")
            self._record(record)

        if self.silent_length_streak >= MAX_AGENT_SILENT_LENGTH_STREAK:
            return (
                f"reasoning-without-visible-output limit reached "
                f"({MAX_AGENT_SILENT_LENGTH_STREAK})"
            )
        if self.tool_loop_overflows > MAX_AGENT_TOOL_LOOP_OVERFLOWS:
            return f"tool-loop overflow limit reached ({self.tool_loop_overflows})"
        if self.compactions > MAX_AGENT_COMPACTIONS:
            return f"compaction limit reached ({self.compactions})"
        if self.model_calls >= self.max_model_calls:
            # The diagnostic names the exact selected threshold, never a
            # possibly higher observed count.
            return f"model-call limit reached ({self.max_model_calls})"
        if self.tool_calls >= self.max_tool_calls:
            return f"tool-call limit reached ({self.max_tool_calls})"
        return None


def run_agent_process(
    argv: list[str], env: dict[str, str], timeout: int,
    completion_probe: Callable[[bytes], bytes | None] | None = None,
    session_path: Path | None = None,
    max_model_calls: int = MAX_AGENT_MODEL_CALLS,
    max_tool_calls: int = MAX_AGENT_TOOL_CALLS,
    container_policy_observer: SandboxContainerPolicyObserver | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run one agent with bounded output and fail-closed descendant supervision."""
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        raise RuntimeError(
            "Pixel mesh agent supervision requires Linux 5.3+ and Python pidfd support"
        )
    supervised_env = dict(env)
    token = secrets.token_hex(32)
    supervised_env[TREE_TOKEN_ENV] = token
    caller = process_identity(os.getppid())
    process = subprocess.Popen(
        argv, env=supervised_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True, umask=0o077,
    )
    root_pidfd = os.pidfd_open(process.pid, 0)
    interrupted = [None]
    cancellation_attempted = [False]
    previous_handlers = {}
    monitor_transport = bool(supervised_env.get("SSH_CONNECTION"))
    next_transport_probe = time.monotonic() + TRANSPORT_PROBE_INTERVAL_SECONDS
    budget = (
        AgentSessionBudget(
            session_path, max_model_calls=max_model_calls, max_tool_calls=max_tool_calls,
        )
        if session_path is not None else None
    )

    def request_shutdown(signum, _frame):
        interrupted[0] = signum

    def cancel_and_terminate() -> None:
        if cancellation_attempted[0]:
            return
        cancellation_attempted[0] = True
        terminate_agent_tree(root_pidfd, token)

    def observe_container_policy() -> None:
        if container_policy_observer is None:
            return
        container_policy_observer.observe_once()
        container_policy_observer.raise_if_faulted()

    try:
        for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.signal(signum, request_shutdown)
        deadline = time.monotonic() + timeout
        while True:
            try:
                budget_failure = budget.observe() if budget is not None else None
            except (OSError, ValueError) as error:
                budget_failure = f"session reconciliation failed: {error}"
            if budget_failure is not None:
                cancel_and_terminate()
                try:
                    stdout, stderr = process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    stdout, stderr = b"", b""
                diagnostic = f"Pixel mesh agent budget exceeded: {budget_failure}\n".encode("utf-8")
                return subprocess.CompletedProcess(argv, 124, b"", diagnostic)
            if monitor_transport and time.monotonic() >= next_transport_probe:
                if not write_transport_probe():
                    interrupted[0] = signal.SIGHUP
                next_transport_probe = time.monotonic() + TRANSPORT_PROBE_INTERVAL_SECONDS
            if interrupted[0] is not None or (caller is not None and not same_process(caller)):
                signum = interrupted[0] or signal.SIGHUP
                cancel_and_terminate()
                try:
                    stdout, stderr = process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    stdout, stderr = b"", b""
                return subprocess.CompletedProcess(argv, 128 + int(signum), stdout, stderr)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                cancel_and_terminate()
                try:
                    stdout, stderr = process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    stdout, stderr = b"", b""
                raise subprocess.TimeoutExpired(argv, timeout, output=stdout, stderr=stderr)
            observe_container_policy()
            try:
                stdout, stderr = process.communicate(timeout=min(AGENT_SUPERVISION_POLL_SECONDS, remaining))
                observe_container_policy()
                try:
                    budget_failure = budget.observe() if budget is not None else None
                except (OSError, ValueError) as error:
                    budget_failure = f"session reconciliation failed: {error}"
                if budget_failure is not None:
                    cancel_and_terminate()
                    diagnostic = f"Pixel mesh agent budget exceeded: {budget_failure}\n".encode("utf-8")
                    return subprocess.CompletedProcess(argv, 124, b"", diagnostic)
                residual = terminate_agent_tree(root_pidfd, token)
                if residual:
                    diagnostic = (
                        f"Pixel mesh terminated {residual} agent descendant(s) left after completion\n"
                    ).encode("utf-8")
                    return subprocess.CompletedProcess(argv, 125, stdout, stderr + diagnostic)
                return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
            except subprocess.TimeoutExpired as error:
                if completion_probe is not None and error.output:
                    completed = completion_probe(error.output)
                    if completed is not None:
                        observe_container_policy()
                        cancel_and_terminate()
                        try:
                            _, stderr = process.communicate(timeout=5)
                        except subprocess.TimeoutExpired:
                            stderr = error.stderr or b""
                        return subprocess.CompletedProcess(argv, 0, completed, stderr)
                continue
    except BaseException:
        cancel_and_terminate()
        try:
            process.communicate(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
        raise
    finally:
        os.close(root_pidfd)
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


def _custodied_docker_binary(env: dict[str, str], *, purpose: str) -> Path:
    """Resolve one trusted Docker binary without accepting ambient replacements."""
    if purpose not in {
        "sandbox runtime cleanup",
        "sandbox container policy observation",
    }:
        raise ValueError("sandbox Docker custody received an invalid purpose")
    docker_candidate = shutil.which("docker", path=env.get("PATH"))
    if docker_candidate is None:
        raise ValueError(f"{purpose} could not locate Docker for verification")
    docker_path = Path(docker_candidate)
    try:
        docker = docker_path.resolve(strict=True)
        docker_info = docker.lstat()
    except OSError as error:
        raise ValueError(f"{purpose} Docker custody failed: {error}") from None
    if (
        docker != docker_path
        or docker.is_symlink()
        or not stat.S_ISREG(docker_info.st_mode)
        or docker_info.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(docker_info.st_mode) & 0o022
    ):
        raise ValueError(f"{purpose} Docker binary failed custody checks")
    return docker


class SandboxContainerPolicyObserver:
    """Capture one bounded live container-policy observation for a sandboxed turn."""

    def __init__(
        self, *, canonical_session_key: str, config_sha256: str,
        workspace: Path, workspace_access: str,
        git_common: Path | None, env: dict[str, str],
    ) -> None:
        if (
            not canonical_session_key.startswith("agent:pixel:")
            or not SESSION_KEY_PATTERN.fullmatch(canonical_session_key)
        ):
            raise ValueError("sandbox container policy received an invalid session label")
        if not SANDBOX_DOCKER_CONFIG_SHA256_PATTERN.fullmatch(config_sha256):
            raise ValueError("sandbox container policy received an invalid config digest")
        if workspace_access not in {"rw", "ro"}:
            raise ValueError("sandbox container policy received invalid workspace access")
        self._docker = _custodied_docker_binary(
            env, purpose="sandbox container policy observation",
        )
        self._env = dict(env)
        state_value = self._env.get("OPENCLAW_STATE_DIR")
        if not isinstance(state_value, str) or not state_value:
            raise ValueError("sandbox container policy lost its OpenClaw state binding")
        try:
            state_size = len(state_value.encode("utf-8", "strict"))
        except UnicodeEncodeError:
            raise ValueError("sandbox container policy state binding is not UTF-8") from None
        state_path = Path(state_value)
        if state_size > MAX_WORKSPACE_PATH_BYTES or not state_path.is_absolute():
            raise ValueError("sandbox container policy state binding is invalid")
        self._canonical_session_key = canonical_session_key
        self._config_sha256 = config_sha256
        self._workspace = str(workspace)
        self._workspace_access = workspace_access
        self._git_common = str(git_common) if git_common is not None else None
        self._control_mount_root = (
            state_path / "sandbox" / "skills-workspaces"
            if workspace_access == "rw"
            else state_path / "sandboxes"
        )
        self._control_mount_suffix = (
            SANDBOX_WORKSPACE_CONTROL_PARTS if workspace_access == "rw" else ()
        )
        self._control_session_prefix = canonical_session_key.replace(":", "-")[:32]
        self._workspace_destination = (
            SANDBOX_DOCKER_WORKDIR
            if workspace_access == "rw"
            else SANDBOX_DOCKER_READONLY_WORKSPACE_DESTINATION
        )
        self._control_mount_destination = (
            str(Path(SANDBOX_DOCKER_WORKDIR) / Path(*SANDBOX_WORKSPACE_CONTROL_PARTS))
            if workspace_access == "rw"
            else SANDBOX_DOCKER_WORKDIR
        )
        self._state = "pending"
        self._fault_code: str | None = None
        self._observation_count = 0

    @staticmethod
    def _bounded_string(value: object, *, maximum: int) -> str:
        if not isinstance(value, str):
            raise ValueError("sandbox container policy field has the wrong type")
        try:
            size = len(value.encode("utf-8", "strict"))
        except UnicodeEncodeError:
            raise ValueError("sandbox container policy field is not UTF-8") from None
        if size > maximum:
            raise ValueError("sandbox container policy field exceeded its bound")
        return value

    @staticmethod
    def _exact_int(value: object, expected: int) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value == expected

    def _fault(self, code: str) -> None:
        if code not in {
            "custody", "observation-limit", "list-failed", "list-ambiguous",
            "inspect-failed", "inspect-invalid", "policy-mismatch",
            "identity-mismatch", "state-mismatch", "profile-mismatch",
            "resource-mismatch", "tmpfs-mismatch", "mount-mismatch",
        }:
            raise ValueError("sandbox container policy received an invalid fault code")
        self._state = "fault"
        self._fault_code = code

    def raise_if_faulted(self) -> None:
        """Abort supervision immediately after any permanent observation fault."""
        if self._state == "fault":
            raise ValueError(
                f"sandbox container policy observation failed closed: {self._fault_code}",
            )

    def _validate_inspect(self, inspected: object) -> None:
        if not isinstance(inspected, dict):
            raise ValueError("sandbox container policy inspect root is not an object")
        state = inspected.get("State")
        config = inspected.get("Config")
        host = inspected.get("HostConfig")
        mounts = inspected.get("Mounts")
        if (
            not isinstance(state, dict)
            or not isinstance(config, dict)
            or not isinstance(host, dict)
            or not isinstance(mounts, list)
            or len(mounts) > MAX_SANDBOX_DOCKER_MOUNTS
        ):
            raise ValueError("sandbox container policy inspect structure is invalid")

        labels = config.get("Labels")
        if not isinstance(labels, dict) or len(labels) > 64:
            raise ValueError("sandbox container policy labels are invalid")
        session_label = self._bounded_string(
            labels.get("openclaw.sessionKey"), maximum=128,
        )
        config_label = self._bounded_string(
            labels.get("openclaw.configHash"), maximum=64,
        )
        if (
            session_label != self._canonical_session_key
            or not SANDBOX_DOCKER_CONFIG_SHA256_PATTERN.fullmatch(config_label)
        ):
            raise ValueError("identity-mismatch")
        if self._bounded_string(state.get("Status"), maximum=32) != "running":
            raise ValueError("state-mismatch")
        if (
            self._bounded_string(config.get("Image"), maximum=256) != SANDBOX_DOCKER_IMAGE
            or self._bounded_string(config.get("WorkingDir"), maximum=256) != SANDBOX_DOCKER_WORKDIR
            or self._bounded_string(config.get("User"), maximum=64) != "1000:1000"
            or host.get("ReadonlyRootfs") is not True
            or self._bounded_string(host.get("NetworkMode"), maximum=64) != "none"
            or host.get("CapDrop") != ["ALL"]
            or host.get("CapAdd") is not None
            or host.get("Privileged") is not False
            or host.get("SecurityOpt") != ["no-new-privileges"]
            or host.get("Devices") != []
            or host.get("DeviceRequests") is not None
            or host.get("PortBindings") != {}
            or host.get("PublishAllPorts") is not False
            or self._bounded_string(host.get("PidMode"), maximum=64) != ""
            or self._bounded_string(host.get("IpcMode"), maximum=64) != "private"
            or self._bounded_string(host.get("UTSMode"), maximum=64) != ""
            or self._bounded_string(host.get("UsernsMode"), maximum=64) != ""
            or self._bounded_string(host.get("CgroupnsMode"), maximum=64) != "private"
        ):
            raise ValueError("profile-mismatch")
        if (
            not self._exact_int(host.get("PidsLimit"), 256)
            or not self._exact_int(host.get("Memory"), SANDBOX_DOCKER_MEMORY_BYTES)
            or not self._exact_int(host.get("MemorySwap"), SANDBOX_DOCKER_MEMORY_BYTES)
            or not self._exact_int(host.get("NanoCpus"), SANDBOX_DOCKER_NANO_CPUS)
        ):
            raise ValueError("resource-mismatch")

        tmpfs = host.get("Tmpfs")
        if (
            not isinstance(tmpfs, dict)
            or set(tmpfs) != SANDBOX_DOCKER_TMPFS_DESTINATIONS
            or any(
                not isinstance(value, str)
                or len(value.encode("utf-8", "strict")) > 256
                or value != ""
                for value in tmpfs.values()
            )
        ):
            raise ValueError("tmpfs-mismatch")

        workspace_matches = 0
        common_matches = 0
        control_mount_matches = 0
        for mount in mounts:
            if not isinstance(mount, dict):
                raise ValueError("mount-mismatch")
            kind = self._bounded_string(mount.get("Type"), maximum=16)
            source = self._bounded_string(
                mount.get("Source"), maximum=MAX_SANDBOX_DOCKER_INSPECT_STRING_BYTES,
            )
            destination = self._bounded_string(
                mount.get("Destination"), maximum=MAX_SANDBOX_DOCKER_INSPECT_STRING_BYTES,
            )
            writable = mount.get("RW")
            if not isinstance(writable, bool):
                raise ValueError("mount-mismatch")
            if kind == "tmpfs":
                if destination not in SANDBOX_DOCKER_TMPFS_DESTINATIONS:
                    raise ValueError("mount-mismatch")
                continue
            if kind != "bind":
                raise ValueError("mount-mismatch")
            if self._bounded_string(mount.get("Propagation"), maximum=64) != "rprivate":
                raise ValueError("mount-mismatch")
            if source == self._workspace:
                workspace_matches += 1
                if (
                    destination != self._workspace_destination
                    or writable is not (self._workspace_access == "rw")
                ):
                    raise ValueError("mount-mismatch")
            else:
                try:
                    control_relative = Path(source).relative_to(self._control_mount_root)
                except ValueError:
                    control_relative = None
                if (
                    control_relative is not None
                    and len(control_relative.parts) == 1 + len(self._control_mount_suffix)
                    and control_relative.parts[0].startswith(self._control_session_prefix)
                    and control_relative.parts[1:] == self._control_mount_suffix
                ):
                    control_mount_matches += 1
                    if destination != self._control_mount_destination or writable:
                        raise ValueError("mount-mismatch")
                elif self._git_common is not None and source == self._git_common:
                    common_matches += 1
                    if destination != self._git_common or writable:
                        raise ValueError("mount-mismatch")
                else:
                    raise ValueError("mount-mismatch")
        if (
            workspace_matches != 1
            or control_mount_matches != 1
            or common_matches != (1 if self._git_common is not None else 0)
        ):
            raise ValueError("mount-mismatch")

    def observe_once(self) -> None:
        """Poll once until a live runtime is verified or a permanent fault is frozen."""
        if self._state != "pending":
            return
        self._observation_count += 1
        if self._observation_count > MAX_SANDBOX_DOCKER_OBSERVATIONS:
            self._fault("observation-limit")
            return
        try:
            if _custodied_docker_binary(
                self._env, purpose="sandbox container policy observation",
            ) != self._docker:
                self._fault("custody")
                return
        except ValueError:
            self._fault("custody")
            return
        try:
            listed = subprocess.run(
                [
                    str(self._docker), "ps", "-aq", "--no-trunc", "--filter",
                    f"label=openclaw.sessionKey={self._canonical_session_key}",
                ],
                env=self._env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=SANDBOX_DOCKER_COMMAND_TIMEOUT_SECONDS, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            self._fault("list-failed")
            return
        if (
            listed.returncode != 0
            or len(listed.stdout) > MAX_SANDBOX_DOCKER_LIST_BYTES
            or len(listed.stderr) > MAX_SANDBOX_DOCKER_LIST_BYTES
        ):
            self._fault("list-failed")
            return
        try:
            decoded = listed.stdout.decode("utf-8", "strict")
        except UnicodeDecodeError:
            self._fault("list-failed")
            return
        identifiers = decoded.splitlines()
        if not identifiers:
            return
        if (
            len(identifiers) != 1
            or not SANDBOX_DOCKER_CONTAINER_ID_PATTERN.fullmatch(identifiers[0])
        ):
            self._fault("list-ambiguous")
            return
        try:
            inspected = subprocess.run(
                [str(self._docker), "inspect", "--format", "{{json .}}", identifiers[0]],
                env=self._env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=SANDBOX_DOCKER_COMMAND_TIMEOUT_SECONDS, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            self._fault("inspect-failed")
            return
        if (
            inspected.returncode != 0
            or len(inspected.stdout) > MAX_SANDBOX_DOCKER_INSPECT_BYTES
            or len(inspected.stderr) > MAX_SANDBOX_DOCKER_LIST_BYTES
        ):
            self._fault("inspect-failed")
            return
        try:
            payload = json.loads(
                inspected.stdout.decode("utf-8", "strict"),
                object_pairs_hook=_strict_object,
                parse_constant=_reject_json_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
            self._fault("inspect-invalid")
            return
        try:
            self._validate_inspect(payload)
        except ValueError as error:
            code = str(error)
            self._fault(
                code if code in {
                    "identity-mismatch", "state-mismatch", "profile-mismatch",
                    "resource-mismatch", "tmpfs-mismatch", "mount-mismatch",
                } else "policy-mismatch",
            )
            return
        self._state = "verified"

    def finalize(
        self, tool_call_count: int, *, authority_config_sha256: object,
    ) -> dict[str, object]:
        """Return privacy-bounded evidence or fail closed before any success output."""
        if authority_config_sha256 != self._config_sha256:
            raise ValueError("sandbox container policy lost its authority-config binding")
        if (
            not isinstance(tool_call_count, int)
            or isinstance(tool_call_count, bool)
            or not 0 <= tool_call_count <= MAX_AGENT_TOOL_CALLS
        ):
            raise ValueError("sandbox container policy tool-call count is invalid")
        if self._state == "fault":
            raise ValueError(
                f"sandbox container policy observation failed closed: {self._fault_code}",
            )
        if self._state == "pending" and tool_call_count:
            raise ValueError(
                "sandbox container policy did not verify a live runtime",
            )
        verified = self._state == "verified"
        return {
            "schemaVersion": 1,
            "state": "verified" if verified else "no-sandbox-tool-call",
            "containerObserved": verified,
            "exactSessionMatch": verified,
            "configLabelPresent": verified,
            "authorityConfigBound": verified,
            "policyMatch": verified,
            "rootReadOnly": verified,
            "networkDisabled": verified,
            "privilegeIsolationMatched": verified,
            "workspaceAccessMatched": verified,
            "externalBindsReadOnly": verified,
            "bindPropagationPrivate": verified,
            "resourceLimitsMatched": verified,
            "tmpfsMatched": verified,
            "observationCount": self._observation_count,
        }


def _remove_and_verify_sandbox_runtime(
    binary: Path, env: dict[str, str], session_key: str,
) -> None:
    """Remove one fresh Pixel session runtime and prove no exact-label residue."""
    if not SESSION_KEY_PATTERN.fullmatch(session_key):
        raise ValueError("sandbox runtime cleanup received an invalid session key")
    canonical_session_key = f"agent:pixel:{session_key}"
    try:
        removed = subprocess.run(
            [
                str(binary), "sandbox", "recreate", "--session",
                canonical_session_key, "--force",
            ],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("sandbox runtime cleanup command failed") from None
    if (
        len(removed.stdout) > MAX_AGENT_OUTPUT_BYTES
        or len(removed.stderr) > MAX_AGENT_OUTPUT_BYTES
        or removed.returncode != 0
    ):
        raise ValueError("sandbox runtime cleanup did not complete successfully")

    docker = _custodied_docker_binary(env, purpose="sandbox runtime cleanup")
    try:
        remaining = subprocess.run(
            [
                str(docker), "ps", "-aq", "--filter",
                f"label=openclaw.sessionKey={canonical_session_key}",
            ],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("sandbox runtime cleanup absence verification failed") from None
    if (
        len(remaining.stdout) > MAX_GIT_TOPLEVEL_BYTES
        or len(remaining.stderr) > MAX_GIT_TOPLEVEL_BYTES
        or remaining.returncode != 0
        or remaining.stdout.strip()
    ):
        raise ValueError("sandbox runtime cleanup left an exact-session container")


def _sandbox_workspace_control_directory_identity(
    path: Path,
) -> tuple[int, int, int, int]:
    """Pin one owner-controlled OpenClaw workspace directory without following links."""
    try:
        info = path.lstat()
    except OSError as error:
        raise ValueError(f"sandbox workspace control path is unavailable: {error}") from None
    mode = stat.S_IMODE(info.st_mode)
    if (
        path.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or mode & 0o022
    ):
        raise ValueError(
            "sandbox workspace control path must be an owner-owned non-symlink "
            "directory with no group/other write"
        )
    return info.st_dev, info.st_ino, mode, info.st_uid


def _remove_and_verify_sandbox_workspace_control_path(
    binding: SandboxWorkspaceControlBinding,
) -> None:
    """Remove only Pixel-created empty control directories and preserve prior state."""
    if not 1 <= len(binding) <= len(SANDBOX_WORKSPACE_CONTROL_PARTS):
        raise ValueError("sandbox workspace control binding is incomplete")
    for index, (path, identity, _) in enumerate(binding):
        if (
            path.name != SANDBOX_WORKSPACE_CONTROL_PARTS[index]
            or index and path.parent != binding[index - 1][0]
        ):
            raise ValueError("sandbox workspace control binding is malformed")
        if _sandbox_workspace_control_directory_identity(path) != identity:
            raise ValueError("sandbox workspace control path identity drifted")
    for path, _, created in reversed(binding):
        if not created:
            continue
        try:
            path.rmdir()
        except OSError:
            raise ValueError(
                "sandbox workspace control path was not restored to its pre-turn state"
            ) from None
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            raise ValueError("sandbox workspace control path absence is ambiguous") from None
        raise ValueError("sandbox workspace control path remained after cleanup")
    for path, identity, created in binding:
        if not created and _sandbox_workspace_control_directory_identity(path) != identity:
            raise ValueError("pre-existing sandbox workspace control path drifted")


def _prepare_sandbox_workspace_control_path(
    workspace: Path,
) -> SandboxWorkspaceControlBinding:
    """Precreate OpenClaw's fixed nested mount targets under owner custody."""
    binding: list[tuple[Path, tuple[int, int, int, int], bool]] = []
    current = workspace
    try:
        for part in SANDBOX_WORKSPACE_CONTROL_PARTS:
            current = current / part
            created = False
            try:
                current.lstat()
            except FileNotFoundError:
                try:
                    current.mkdir(mode=0o700)
                except OSError as error:
                    raise ValueError(
                        f"sandbox workspace control path creation failed: {error}"
                    ) from None
                created = True
            except OSError as error:
                raise ValueError(
                    f"sandbox workspace control path inspection failed: {error}"
                ) from None
            identity = _sandbox_workspace_control_directory_identity(current)
            binding.append((current, identity, created))
        return tuple(binding)
    except BaseException:
        if binding:
            _remove_and_verify_sandbox_workspace_control_path(tuple(binding))
        raise


def local_agent(
    message: bytes, *, read_only: bool = False, contract: bool = False,
    sandboxed: bool = False, sandboxed_workspace: Path | None = None,
    sandboxed_workspace_identity: tuple[int, int, int, int] | None = None,
    sandboxed_git_identity: tuple[int, int, int, int, int] | None = None,
    sandboxed_git_metadata_binding: GitMetadataBinding | None = None,
    sandboxed_allowed_tools: tuple[str, ...] | None = None,
    sandboxed_model_pin: tuple[str, str, str] | None = None,
    sandboxed_readonly: bool = False,
    sandboxed_mailbox_readonly: bool = False,
    fixed_authority_profile: str | None = None,
) -> int:
    if not message or len(message) > MAX_MESSAGE_BYTES:
        raise ValueError("message stdin must contain 1..131072 bytes")
    message.decode("utf-8", "strict")
    # Owner-process execution budgets are selected once, before any authority
    # config creation or process/model launch. Prompt, stdin, and config cannot
    # set or widen them.
    timeout_seconds, max_model_calls, max_tool_calls, budget_source = (
        selected_execution_budget()
    )
    contract_authority_profile, contract_authority_allowed_tools = (
        selected_contract_authority_profile()
    )
    fixed_authority_selected = fixed_authority_profile is not None
    if fixed_authority_selected:
        if fixed_authority_profile not in BROKER_MEDIATED_AUTHORITY_PROFILES:
            raise ValueError("fixed mesh authority profile is not supported")
        if contract_authority_profile is not None:
            raise ValueError("fixed mesh authority profile conflicts with the owner environment")
        contract_authority_profile = fixed_authority_profile
        contract_authority_allowed_tools = CONTRACT_AUTHORITY_PROFILES[fixed_authority_profile]
    execution_budget: dict[str, object] = {
        "timeoutSeconds": timeout_seconds,
        "maxModelCalls": max_model_calls,
        "maxToolCalls": max_tool_calls,
        "source": budget_source,
    }
    # Mutual-exclusion: sandboxed may not combine with read_only or contract.
    # Existing read-only+contract remains supported.
    if sandboxed and (read_only or contract):
        raise ValueError("sandboxed authority profile is mutually exclusive with read-only and contract")
    if contract_authority_profile is not None:
        if fixed_authority_selected:
            if read_only or sandboxed:
                raise ValueError(
                    "fixed mesh authority profile is only supported by an ordinary local operations turn"
                )
        elif not contract or read_only or sandboxed:
            raise ValueError(
                "contract authority profile is only supported by an ordinary strict contract turn"
            )
    # Fail closed: the sandbox-only allowlist keyword may never widen a
    # non-sandboxed route, and sandboxed turns always carry an exact tuple.
    if sandboxed_allowed_tools is not None and not sandboxed:
        raise ValueError("sandboxed_allowed_tools is only supported with the sandboxed authority profile")
    if sandboxed_model_pin is not None and not sandboxed:
        raise ValueError("sandboxed_model_pin is only supported with the sandboxed authority profile")
    if sandboxed_readonly and not sandboxed:
        raise ValueError("sandboxed_readonly is only supported with the sandboxed authority profile")
    if sandboxed_readonly and sandboxed_allowed_tools not in {
        None, SANDBOXED_READONLY_ALLOWED_TOOLS,
    }:
        raise ValueError("sandboxed_readonly requires the fixed read/exec allowlist")
    if sandboxed_mailbox_readonly and not sandboxed:
        raise ValueError(
            "sandboxed_mailbox_readonly is only supported with the sandboxed authority profile"
        )
    if sandboxed_readonly and sandboxed_mailbox_readonly:
        raise ValueError(
            "sandboxed_readonly and sandboxed_mailbox_readonly are mutually exclusive"
        )
    if sandboxed_mailbox_readonly and sandboxed_allowed_tools not in {
        None, SANDBOXED_MAILBOX_READONLY_ALLOWED_TOOLS,
    }:
        raise ValueError(
            "sandboxed_mailbox_readonly requires the fixed mailbox read-only allowlist"
        )
    if sandboxed_readonly:
        allowed_tools = SANDBOXED_READONLY_ALLOWED_TOOLS
    elif sandboxed_mailbox_readonly:
        allowed_tools = SANDBOXED_MAILBOX_READONLY_ALLOWED_TOOLS
    else:
        allowed_tools = (
            sandboxed_allowed_tools if sandboxed_allowed_tools is not None
            else SANDBOXED_ALLOWED_TOOLS
        )
    contract_options = {}
    exploration_message = message
    if contract:
        message, schema_name, response_schema, schema_sha256 = parse_contract_request(message)
        exploration_message = contract_exploration_message(
            message, schema_name, response_schema, schema_sha256,
        )
        contract_options = {
            "response_schema": response_schema,
            "schema_name": schema_name,
            "schema_sha256": schema_sha256,
        }
    home = mesh_home()
    state = home / ".openclaw-mesh"
    exec_shell = mesh_exec_shell(home)
    binary = Path(os.environ.get(OPENCLAW_BIN_ENV) or home / ".npm-global" / "bin" / "openclaw")
    authority_config_path = None
    authority_config_sha256 = None
    authority_workspace_identity_sha256 = None
    authority_config_identity: tuple[int, int, int, int, int] | None = None
    authority_source_config_identity: tuple[int, int, int, int, int, int] | None = None
    authority_source_config_sha256: str | None = None
    message_path = None
    authority_evidence = None
    sandboxed_config_removed = False
    sandboxed_runtime_removed = False
    sandboxed_control_path_restored = False
    sandboxed_session_key: str | None = None
    sandboxed_agent_env: dict[str, str] | None = None
    sandboxed_container_observer: SandboxContainerPolicyObserver | None = None
    sandboxed_control_binding: SandboxWorkspaceControlBinding | None = None
    contract_authority_config_removed = False

    def _revalidate_authority_source_config() -> None:
        """Prove the canonical source config remains byte-identical."""
        if contract_authority_profile is None:
            return
        if authority_source_config_identity is None or authority_source_config_sha256 is None:
            raise ValueError("authority-reduced turn lost its source config binding")
        _, payload, identity = _load_bounded_owner_private_config(state)
        if (
            identity != authority_source_config_identity
            or not hmac.compare_digest(
                hashlib.sha256(payload).hexdigest(), authority_source_config_sha256,
            )
        ):
            raise ValueError("OpenClaw config source drifted during authority-reduced turn")

    def _require_sandboxed_cleanup() -> None:
        """Remove the exact sandbox runtime/config before any success stdout."""
        nonlocal sandboxed_config_removed, sandboxed_runtime_removed
        nonlocal sandboxed_control_path_restored
        if not sandboxed:
            return
        if authority_config_path is None or authority_config_identity is None:
            raise ValueError("sandboxed mesh turn lost its ephemeral config before cleanup")
        cleanup_errors: list[OSError | ValueError] = []
        try:
            if not sandboxed_runtime_removed and sandboxed_session_key is not None:
                if sandboxed_agent_env is None:
                    raise ValueError("sandboxed mesh turn lost its cleanup environment")
                _remove_and_verify_sandbox_runtime(
                    binary, sandboxed_agent_env, sandboxed_session_key,
                )
                sandboxed_runtime_removed = True
        except (OSError, ValueError) as error:
            cleanup_errors.append(error)
        try:
            if not sandboxed_config_removed:
                _remove_and_verify_ephemeral_config(
                    authority_config_path, authority_config_identity,
                )
                sandboxed_config_removed = True
        except (OSError, ValueError) as error:
            cleanup_errors.append(error)
        try:
            if (
                not sandboxed_control_path_restored
                and sandboxed_control_binding is not None
            ):
                _remove_and_verify_sandbox_workspace_control_path(
                    sandboxed_control_binding,
                )
                sandboxed_control_path_restored = True
        except (OSError, ValueError) as error:
            cleanup_errors.append(error)
        if cleanup_errors:
            raise ValueError("sandboxed mesh cleanup failed closed") from cleanup_errors[0]

    def _require_contract_authority_cleanup() -> None:
        """Remove and prove absence of the exact selected-profile config."""
        if contract_authority_profile is None:
            return
        if authority_config_path is None or authority_config_identity is None:
            raise ValueError("contract authority turn lost its ephemeral config before cleanup")
        _remove_and_verify_ephemeral_config(authority_config_path, authority_config_identity)

    try:
        config_path = state / "openclaw.json"
        # Capture source custody identity before any authority config is derived.
        # Only sandboxed turns require a source config for the drift check; ordinary
        # ask, read-only, and contract routes do not, and read-only config custody
        # is owned by create_read_only_config.
        source_identity = (
            _source_config_lstat_identity(config_path) if sandboxed else None
        )
        if read_only:
            authority_config_path, authority_config_sha256 = create_read_only_config(state)
            config_path = authority_config_path
        if contract_authority_profile is not None:
            if contract_authority_allowed_tools is None:
                raise ValueError("contract authority profile lost its closed allowlist")
            (
                _, source_payload, authority_source_config_identity,
            ) = _load_bounded_owner_private_config(state)
            authority_source_config_sha256 = hashlib.sha256(source_payload).hexdigest()
            (
                authority_config_path,
                authority_config_sha256,
                authority_config_identity,
            ) = create_contract_authority_config(
                state, contract_authority_profile, contract_authority_allowed_tools,
            )
            config_path = authority_config_path
            contract_options.update({
                "authority_profile": contract_authority_profile,
                "authority_config_sha256": authority_config_sha256,
            })
        if sandboxed:
            if (
                sandboxed_workspace is None
                or sandboxed_workspace_identity is None
                or sandboxed_git_identity is None
            ):
                raise ValueError("sandboxed mesh turn lost its workspace binding")
            _revalidate_workspace_identity(
                sandboxed_workspace, sandboxed_workspace_identity, sandboxed_git_identity,
                sandboxed_git_metadata_binding,
            )
            sandboxed_control_binding = _prepare_sandbox_workspace_control_path(
                sandboxed_workspace,
            )
            authority_config_path, authority_config_sha256, authority_config_identity, authority_workspace_identity_sha256 = (
                create_sandboxed_config(
                    state, sandboxed_workspace, sandboxed_workspace_identity,
                    sandboxed_git_identity,
                    git_metadata_binding=sandboxed_git_metadata_binding,
                    allowed_tools=allowed_tools,
                    workspace_access="ro" if sandboxed_readonly else "rw",
                    model_pin=sandboxed_model_pin,
                )
            )
            config_path = authority_config_path
        with tempfile.NamedTemporaryFile(prefix="pixel-mesh-message-", mode="wb", delete=False) as handle:
            handle.write(exploration_message)
            message_path = Path(handle.name)
        env = dict(os.environ)
        env["OPENCLAW_STATE_DIR"] = str(state)
        env["OPENCLAW_CONFIG_PATH"] = str(config_path)
        env["SHELL"] = str(exec_shell)
        if sandboxed:
            sandboxed_agent_env = env
        # The deadline covers the complete one-shot request, including any resumed
        # parent turn after a yield. A final turn already durable at the deadline is
        # accepted; a still-incomplete turn fails closed instead of extending the
        # transport to a second unbounded agent timeout.
        deadline = time.monotonic() + timeout_seconds
        session_key = fresh_session_key()
        if sandboxed:
            sandboxed_session_key = session_key
        session_id = str(uuid.uuid4())
        session_path = state / "agents" / "pixel" / "sessions" / f"{session_id}.jsonl"
        if session_path.exists() or session_path.is_symlink():
            raise ValueError("fresh OpenClaw agent session path already exists")
        # Immediately before OpenClaw launch: reject source or workspace drift.
        if sandboxed and _source_config_lstat_identity(state / "openclaw.json") != source_identity:
            raise ValueError("OpenClaw config source drifted before launch")
        _revalidate_authority_source_config()
        if sandboxed:
            _revalidate_workspace_identity(
                sandboxed_workspace, sandboxed_workspace_identity, sandboxed_git_identity,
                sandboxed_git_metadata_binding,
            )
            if authority_config_sha256 is None or sandboxed_workspace is None:
                raise ValueError("sandboxed mesh turn lost its container-policy binding")
            sandboxed_container_observer = SandboxContainerPolicyObserver(
                canonical_session_key=f"agent:pixel:{session_key}",
                config_sha256=authority_config_sha256,
                workspace=sandboxed_workspace,
                workspace_access="ro" if sandboxed_readonly else "rw",
                git_common=(
                    sandboxed_git_metadata_binding[0]
                    if sandboxed_git_metadata_binding is not None else None
                ),
                env=env,
            )
        result = run_agent_process(
            [str(binary), "agent", "--local", "--agent", "pixel", "--message-file", str(message_path),
             "--session-key", session_key, "--session-id", session_id,
             "--json", "--timeout", str(timeout_seconds)],
            env, timeout_seconds + 30,
            completion_probe=lambda output: completed_yield_from_partial(output, state),
            session_path=session_path,
            max_model_calls=max_model_calls,
            max_tool_calls=max_tool_calls,
            container_policy_observer=sandboxed_container_observer,
        )
        if len(result.stdout) > MAX_AGENT_OUTPUT_BYTES or len(result.stderr) > MAX_AGENT_OUTPUT_BYTES:
            raise ValueError("OpenClaw agent output exceeded its limit")
        authority_evidence = None
        if read_only or sandboxed or contract_authority_profile is not None:
            if authority_config_sha256 is None or (
                sandboxed and authority_workspace_identity_sha256 is None
            ):
                raise ValueError(
                    "sandboxed or read-only mesh turn lost its authority config binding"
                )
            try:
                _revalidate_authority_source_config()
                sandboxed_revalidator = (
                    (lambda: _revalidate_workspace_identity(
                        sandboxed_workspace, sandboxed_workspace_identity, sandboxed_git_identity,
                        sandboxed_git_metadata_binding,
                    ))
                    if sandboxed and sandboxed_workspace is not None
                    and sandboxed_workspace_identity is not None and sandboxed_git_identity is not None
                    else None
                )
                sandboxed_extra_evidence = None
                if sandboxed:
                    sandboxed_extra_evidence = {
                        "workspaceIdentitySha256": authority_workspace_identity_sha256,
                        "freshSession": True,
                        "networkEnabled": False,
                        "hostFilesystemAuthority": False,
                        "hostGitMetadataReadAuthority": (
                            sandboxed_git_metadata_binding is not None
                        ),
                        "openClawExternalBindSourceOverride": (
                            sandboxed_git_metadata_binding is not None
                        ),
                        "sandboxRuntimeRemovedBeforeSuccess": True,
                        "sandboxWorkspaceControlPathRestoredBeforeSuccess": True,
                        "acceptanceAuthority": False,
                        "inferencePinned": sandboxed_model_pin is not None,
                    }
                    if sandboxed_model_pin is not None:
                        pin_provider, pin_model, pin_base_url = sandboxed_model_pin
                        sandboxed_extra_evidence["inferencePinSelected"] = {
                            "provider": pin_provider,
                            "model": pin_model,
                            "baseUrl": pin_base_url,
                        }
                authority_evidence = read_only_authority_evidence(
                    session_path, state, authority_config_sha256,
                    profile=(
                        "sandboxed-readonly-workspace"
                        if (sandboxed and sandboxed_readonly)
                        else "sandboxed-mailbox-readonly-workspace"
                        if (sandboxed and sandboxed_mailbox_readonly)
                        else "sandboxed-workspace" if sandboxed
                        else contract_authority_profile or "read-only"
                    ),
                    allowed_tools=(
                        allowed_tools if sandboxed
                        else contract_authority_allowed_tools or READ_ONLY_ALLOWED_TOOLS
                    ),
                    execution_budget=execution_budget,
                    extra_evidence=sandboxed_extra_evidence if sandboxed else (
                        {
                            "sourceConfigSha256": authority_source_config_sha256,
                            "sourceConfigIdentityStable": True,
                        }
                        if contract_authority_profile is not None else None
                    ),
                    workspace_identity_revalidator=sandboxed_revalidator,
                )
                if sandboxed:
                    if sandboxed_container_observer is None:
                        raise ValueError(
                            "sandboxed mesh turn lost its container-policy observer",
                        )
                    tool_call_count = authority_evidence.get("observedToolCallRecords")
                    if "sandboxContainerPolicy" in authority_evidence:
                        raise ValueError(
                            "sandboxed mesh authority already contains container policy evidence",
                        )
                    authority_evidence["sandboxContainerPolicy"] = (
                        sandboxed_container_observer.finalize(
                            tool_call_count,
                            authority_config_sha256=authority_evidence.get("configSha256"),
                        )
                    )
            except (OSError, ValueError) as error:
                print(json.dumps({
                    "schemaVersion": 1, "observedAt": now(),
                    "error": f"authority reconciliation failed: {error}",
                }), file=sys.stderr)
                return 2
        if result.returncode:
            budget_reason = recoverable_budget_synthesis_reason(
                result, max_model_calls, max_tool_calls,
            )
            if budget_reason is not None:
                try:
                    if budget_reason == REASONING_WITHOUT_VISIBLE_OUTPUT_RECOVERY:
                        completed = (
                            None if contract else
                            run_deterministic_reasoning_blocked_recovery(session_path, state)
                        )
                    elif budget_reason in SELECTED_BUDGET_RECOVERY_REASONS:
                        completed = (
                            None if contract else
                            run_deterministic_selected_budget_blocked_recovery(
                                session_path, state, budget_reason,
                            )
                        )
                    else:
                        completed = (
                            None if contract else
                            run_direct_synthesis_recovery(
                                message, session_path, state, deadline, budget_reason,
                                **contract_options,
                            )
                        )
                except (OSError, TimeoutError, ValueError):
                    completed = None
                if completed is not None:
                    if authority_evidence is not None:
                        completed = attach_authority_evidence(completed, authority_evidence)
                    _require_sandboxed_cleanup()
                    if sandboxed:
                        sandboxed_config_removed = True
                    _require_contract_authority_cleanup()
                    if contract_authority_profile is not None:
                        contract_authority_config_removed = True
                    sys.stdout.buffer.write(completed)
                    return 0
            if contract:
                print(json.dumps({
                    "schemaVersion": 1, "observedAt": now(),
                    "error": "mesh contract exploration failed closed",
                }), file=sys.stderr)
                return 2
            sys.stderr.buffer.write(result.stderr)
            try:
                recovered = recover_post_final_compaction_failure(
                    session_path, state, result.returncode, result.stderr,
                )
            except (OSError, ValueError):
                recovered = None
            if recovered is not None:
                if authority_evidence is not None:
                    recovered = attach_authority_evidence(recovered, authority_evidence)
                _require_sandboxed_cleanup()
                if sandboxed:
                    sandboxed_config_removed = True
                _require_contract_authority_cleanup()
                if contract_authority_profile is not None:
                    contract_authority_config_removed = True
                sys.stdout.buffer.write(recovered)
                return 0
            sys.stdout.buffer.write(result.stdout)
            return result.returncode
        try:
            normalized = normalize_agent_response(result.stdout)
            reconciled = reconcile_yielded_response(normalized, state, deadline)
        except (OSError, TimeoutError, ValueError) as error:
            print(json.dumps({"schemaVersion": 1, "observedAt": now(), "error": str(error)}), file=sys.stderr)
            return 2
        synthesized = False
        try:
            completed = validate_completed_agent_response(reconciled)
        except ValueError as error:
            if contract:
                print(json.dumps({
                    "schemaVersion": 1, "observedAt": now(),
                    "error": "mesh contract exploration did not complete",
                }), file=sys.stderr)
                return 2
            try:
                recovery_reason = OUTPUT_LENGTH_RECOVERY
                if _terminal_reasoning_without_visible_output(session_path, state):
                    recovery_reason = REASONING_WITHOUT_VISIBLE_OUTPUT_RECOVERY
                if recovery_reason == REASONING_WITHOUT_VISIBLE_OUTPUT_RECOVERY:
                    completed = (
                        None if contract else
                        run_deterministic_reasoning_blocked_recovery(session_path, state)
                    )
                else:
                    completed = run_direct_synthesis_recovery(
                        message, session_path, state, deadline, recovery_reason,
                        **contract_options,
                    )
            except (OSError, TimeoutError, ValueError):
                completed = None
            if completed is None and recovery_reason != REASONING_WITHOUT_VISIBLE_OUTPUT_RECOVERY:
                try:
                    completed = run_direct_synthesis_recovery(
                        message, session_path, state, deadline,
                        TRANSCRIPT_OVERFLOW_RECOVERY,
                        **contract_options,
                    )
                except (OSError, TimeoutError, ValueError):
                    completed = None
            if completed is None:
                print(json.dumps({"schemaVersion": 1, "observedAt": now(), "error": str(error)}), file=sys.stderr)
                return 2
            synthesized = True
        if contract and not synthesized:
            try:
                completed = run_deterministic_contract_finalization(
                    session_path, state,
                    **contract_options,
                )
            except (OSError, TimeoutError, ValueError):
                completed = None
            if completed is None:
                print(json.dumps({
                    "schemaVersion": 1, "observedAt": now(),
                    "error": "mesh contract terminal failed strict deterministic validation",
                }), file=sys.stderr)
                return 2
            synthesized = True
        try:
            response = json.loads(completed)
            returned_session = checked_session_path(
                response, state, SYNTHESIS_AGENT_ID if synthesized else "pixel",
            )
            if not synthesized and returned_session != session_path:
                raise ValueError("OpenClaw response did not bind to its fresh session")
            if not synthesized:
                completed = attach_interrupted_completion_evidence(
                    completed, session_path, state,
                )
            if synthesized:
                result_object = response.get("result") if isinstance(response, dict) else None
                meta = result_object.get("meta") if isinstance(result_object, dict) else None
                agent_meta = meta.get("agentMeta") if isinstance(meta, dict) else None
                if (
                    not isinstance(agent_meta, dict)
                    or agent_meta.get("predecessorSessionFile") != str(session_path)
                ):
                    raise ValueError("OpenClaw synthesis response lost its predecessor binding")
                if contract_authority_profile is not None:
                    recovery = (
                        meta.get("meshSynthesisRecovery") if isinstance(meta, dict) else None
                    )
                    if (
                        not isinstance(authority_evidence, dict)
                        or authority_evidence.get("profile") != contract_authority_profile
                        or authority_evidence.get("configSha256") != authority_config_sha256
                        or not isinstance(recovery, dict)
                        or recovery.get("authorityProfile") != contract_authority_profile
                        or recovery.get("authorityConfigSha256") != authority_config_sha256
                    ):
                        raise ValueError("OpenClaw synthesis response lost its authority binding")
        except (json.JSONDecodeError, OSError, ValueError) as error:
            print(json.dumps({"schemaVersion": 1, "observedAt": now(), "error": str(error)}), file=sys.stderr)
            return 2
        if authority_evidence is not None:
            completed = attach_authority_evidence(completed, authority_evidence)

        # Cleanup-before-success for sandboxed: remove ephemeral config and verify
        # absence before writing any success JSON to stdout.
        _require_sandboxed_cleanup()
        if sandboxed:
            sandboxed_config_removed = True
        _require_contract_authority_cleanup()
        if contract_authority_profile is not None:
            contract_authority_config_removed = True
        sys.stderr.buffer.write(result.stderr)
        sys.stdout.buffer.write(completed)
        return 0
    finally:
        if message_path is not None:
            message_path.unlink(missing_ok=True)
        # Read-only cleanup (unchanged behavior); skip when sandboxed already
        # removed and verified the exact config before its success stdout. On
        # sandboxed error/interrupt paths the exact verified helper below owns
        # removal so an unverified unlink can never consume a replacement.
        if (
            authority_config_path is not None
            and not sandboxed
            and contract_authority_profile is None
            and not sandboxed_config_removed
        ):
            try:
                info = authority_config_path.lstat()
            except FileNotFoundError:
                info = None
            if info is not None:
                if (
                    authority_config_path.is_symlink()
                    or not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or info.st_uid != os.geteuid()
                ):
                    raise ValueError("ephemeral authority config cleanup failed its identity recheck")
                authority_config_path.unlink()
        # Finally fallback for error/interrupt paths: if a sandboxed config was
        # created but never removed through the verified success path, remove it
        # through the same exact verified helper with the stored identity. This
        # fails closed on missing/replaced/ambiguous state rather than unlinking
        # a possible replacement.
        if sandboxed and authority_config_path is not None and (
            not sandboxed_runtime_removed or not sandboxed_config_removed
            or not sandboxed_control_path_restored
        ):
            _require_sandboxed_cleanup()
        if (
            sandboxed and authority_config_path is None
            and sandboxed_control_binding is not None
            and not sandboxed_control_path_restored
        ):
            _remove_and_verify_sandbox_workspace_control_path(
                sandboxed_control_binding,
            )
        if (
            contract_authority_profile is not None
            and authority_config_path is not None
            and not contract_authority_config_removed
        ):
            if authority_config_identity is None:
                raise ValueError(
                    "contract authority ephemeral config cleanup lost its stored identity",
                )
            _remove_and_verify_ephemeral_config(authority_config_path, authority_config_identity)


def _remove_and_verify_ephemeral_config(
    path: Path, expected_identity: tuple[int, int, int, int, int] | None = None,
) -> None:
    """Remove the exact ephemeral config and prove absence; fail closed on any mismatch.

    With no expected identity the helper refuses to touch the path at all: a
    verified removal requires the original (dev, ino, mode, uid, nlink) custody
    identity so a same-owner replacement is never deleted.
    """
    if expected_identity is None:
        raise ValueError("ephemeral config removal requires its exact expected identity")
    try:
        info = path.lstat()
    except FileNotFoundError:
        raise ValueError("sandboxed ephemeral config vanished before its verified removal") from None
    if (
        path.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != os.geteuid()
    ):
        raise ValueError("sandboxed ephemeral config failed its pre-removal identity recheck")
    if (
        info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink
    ) != expected_identity:
        raise ValueError("sandboxed ephemeral config identity drifted before its verified removal")
    try:
        path.unlink()
    except OSError as error:
        raise ValueError(f"sandboxed ephemeral config unlink failed: {error}") from None
    try:
        path.lstat()
    except FileNotFoundError:
        return
    raise ValueError("sandboxed ephemeral config still exists after its removal")


def send_message(
    peer: str, message: bytes, *, read_only: bool = False, contract: bool = False,
) -> int:
    selected = peer_name(peer)
    if not message or len(message) > MAX_MESSAGE_BYTES:
        raise ValueError("message stdin must contain 1..131072 bytes")
    message.decode("utf-8", "strict")
    receiver = "receive-message"
    if read_only:
        receiver += "-readonly"
    if contract:
        receiver += "-contract"
    if is_local_peer(selected):
        return local_agent(message, read_only=read_only, contract=contract)
    command = [*SSH_PREFIX, PEERS[selected], "~/.local/bin/pixel-mesh-peer", receiver]
    # Allow the remote one-shot deadline plus bounded OpenClaw/SSH teardown overhead.
    result = subprocess.run(command, input=message, timeout=1860, check=False)
    return result.returncode


def _source_config_lstat_identity(source: Path) -> tuple[int, int, int, int, int, int]:
    """Return (dev, ino, mode, uid, nlink, size) for the source config without following symlinks."""
    try:
        info = source.lstat()
    except OSError as error:
        raise ValueError(f"OpenClaw config source is unavailable: {error}") from None
    if source.is_symlink() or not stat.S_ISREG(info.st_mode):
        raise ValueError("OpenClaw config source must be a non-symlink regular file")
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink, info.st_size)


def _load_bounded_owner_private_config(
    state: Path,
) -> tuple[dict[str, object], bytes, tuple[int, int, int, int, int, int]]:
    """Load the bounded strict-JSON mesh config under owner-private source custody."""
    state_info = state.lstat()
    if (
        state.is_symlink()
        or not stat.S_ISDIR(state_info.st_mode)
        or state_info.st_uid != os.geteuid()
        or stat.S_IMODE(state_info.st_mode) & 0o077
    ):
        raise ValueError("mesh agent state root is not an owner-private directory")
    source = state / "openclaw.json"
    source_pre_identity = _source_config_lstat_identity(source)
    descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) & 0o077
            or info.st_size > MAX_OPENCLAW_CONFIG_BYTES
        ):
            raise ValueError("OpenClaw config must be a bounded owner-private regular file")
        if (
            info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink, info.st_size
        ) != source_pre_identity:
            raise ValueError("OpenClaw config source identity drifted during open")
        with os.fdopen(descriptor, "rb", closefd=False) as source_file:
            payload = source_file.read(MAX_OPENCLAW_CONFIG_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(payload) > MAX_OPENCLAW_CONFIG_BYTES:
        raise ValueError("OpenClaw config exceeded its size limit")
    try:
        config = json.loads(
            payload.decode("utf-8"), object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("OpenClaw config is not strict UTF-8 JSON") from error
    if not isinstance(config, dict):
        raise ValueError("OpenClaw config must be a JSON object")
    try:
        source_after = source.lstat()
    except OSError as error:
        raise ValueError(f"OpenClaw config source vanished after read: {error}") from None
    if (
        source_after.st_dev, source_after.st_ino, source_after.st_mode,
        source_after.st_uid, source_after.st_nlink, source_after.st_size,
    ) != source_pre_identity:
        raise ValueError("OpenClaw config source identity drifted after read")
    rendered = (json.dumps(config, separators=(",", ":")) + "\n").encode("utf-8")
    return config, rendered, source_pre_identity


def _write_ephemeral_config(
    state: Path, config: dict[str, object], *, prefix: str = ".pixel-mesh-sandboxed-",
) -> tuple[Path, str, tuple[int, int, int, int, int]]:
    """Create one random never-reused owner-private ephemeral config file.

    Canonical bytes are bounded before creation; the file is created O_EXCL under
    the directory fd, fully written via the fd, fsynced, and its fstat identity is
    re-verified by lstat before the descriptor closes. Returns the path, hash, and
    the original (dev, ino, mode, uid, nlink) identity for exact verified removal.
    Any failure removes only the exact still-matching file and never a replacement.
    """
    rendered = (json.dumps(config, separators=(",", ":")) + "\n").encode("utf-8")
    if len(rendered) > MAX_OPENCLAW_CONFIG_BYTES:
        raise ValueError("ephemeral mesh config exceeded its size limit before creation")
    rendered_sha256 = hashlib.sha256(rendered).hexdigest()
    directory_fd = os.open(state, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    descriptor = None
    path: Path | None = None
    try:
        while True:
            name = f"{prefix}{secrets.token_hex(16)}.json"
            try:
                descriptor = os.open(
                    name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=directory_fd,
                )
                path = state / name
                break
            except FileExistsError:
                continue
            except OSError as error:
                if error.errno == errno.ELOOP:
                    raise ValueError("ephemeral mesh config path is a symlink race") from None
                raise
        view = memoryview(rendered)
        while view:
            written = os.write(descriptor, view)
            if written < 1:
                raise OSError("short write for ephemeral mesh config")
            view = view[written:]
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_size != len(rendered)
        ):
            raise ValueError("ephemeral mesh config failed its fstat identity recheck")
        stored_identity = (
            info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
        )
        try:
            path_info = path.lstat()
        except OSError as error:
            raise ValueError(f"ephemeral mesh config lstat failed: {error}") from None
        if (
            path.is_symlink()
            or (path_info.st_dev, path_info.st_ino, path_info.st_mode, path_info.st_uid,
                path_info.st_nlink) != stored_identity
        ):
            raise ValueError("ephemeral mesh config fstat and lstat identity disagree")
        if path.read_bytes() != rendered:
            raise ValueError("ephemeral mesh config content drifted after write")
    except BaseException:
        if descriptor is not None:
            try:
                failure_identity = os.fstat(descriptor)
                failure_tuple = (failure_identity.st_dev, failure_identity.st_ino,
                                 failure_identity.st_mode, failure_identity.st_uid,
                                 failure_identity.st_nlink)
            except OSError:
                failure_tuple = None
            try:
                os.close(descriptor)
            except OSError:
                pass
        else:
            failure_tuple = None
        if path is not None:
            try:
                remaining = path.lstat()
            except OSError:
                remaining = None
            if remaining is not None:
                remaining_tuple = (remaining.st_dev, remaining.st_ino,
                                   remaining.st_mode, remaining.st_uid,
                                   remaining.st_nlink)
                if (
                    path.is_symlink()
                    or not stat.S_ISREG(remaining.st_mode)
                    or remaining.st_uid != os.geteuid()
                    or failure_tuple is not None and remaining_tuple != failure_tuple
                ):
                    raise ValueError(
                        "ephemeral mesh config removal refused a mismatched replacement"
                    ) from None
                path.unlink()
        raise
    finally:
        os.close(directory_fd)
    os.close(descriptor)
    return path, rendered_sha256, stored_identity


def _git_directory_identity(
    path: Path, *, protected_by_private_ancestor: bool = False,
) -> tuple[int, int, int, int]:
    """Return one owner-owned, non-writable Git directory identity."""
    try:
        info = path.lstat()
    except OSError as error:
        raise ValueError(f"mesh sandboxed git metadata is unavailable: {error}") from None
    mode = stat.S_IMODE(info.st_mode)
    if (
        path.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or (not protected_by_private_ancestor and mode & 0o022)
    ):
        raise ValueError(
            "mesh sandboxed git metadata directory must be a non-symlink "
            "owner-owned directory with no group/other write"
        )
    return info.st_dev, info.st_ino, mode, info.st_uid


def _read_linked_worktree_pointer(
    pointer: Path, git_identity: tuple[int, int, int, int, int],
) -> tuple[Path, str]:
    """Read one exact absolute Git worktree pointer through a no-follow descriptor."""
    if not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("mesh sandboxed linked-worktree custody requires O_NOFOLLOW")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | os.O_NOFOLLOW
    try:
        descriptor = os.open(pointer, flags)
    except OSError as error:
        raise ValueError(f"mesh sandboxed workspace .git pointer open failed: {error}") from None
    try:
        info = os.fstat(descriptor)
        observed = (
            info.st_dev, info.st_ino, stat.S_IMODE(info.st_mode), info.st_uid, info.st_nlink,
        )
        if not stat.S_ISREG(info.st_mode) or observed != git_identity:
            raise ValueError("mesh sandboxed workspace .git pointer identity drifted")
        payload = os.read(descriptor, MAX_GIT_POINTER_BYTES + 1)
        if len(payload) > MAX_GIT_POINTER_BYTES or os.read(descriptor, 1):
            raise ValueError("mesh sandboxed workspace .git pointer exceeded its limit")
    finally:
        os.close(descriptor)
    try:
        rendered = payload.decode("utf-8", "strict")
    except UnicodeDecodeError:
        raise ValueError("mesh sandboxed workspace .git pointer is not UTF-8") from None
    match = re.fullmatch(r"gitdir: (/[^\r\n:]+)\n", rendered)
    if match is None:
        raise ValueError("mesh sandboxed workspace .git pointer must be one absolute canonical line")
    raw_target = match.group(1)
    if len(raw_target.encode("utf-8")) > MAX_WORKSPACE_PATH_BYTES:
        raise ValueError("mesh sandboxed workspace .git pointer path exceeded its limit")
    target = Path(raw_target)
    try:
        resolved = target.resolve(strict=True)
    except OSError as error:
        raise ValueError(f"mesh sandboxed workspace .git pointer target is unavailable: {error}") from None
    if str(resolved) != raw_target:
        raise ValueError("mesh sandboxed workspace .git pointer target is not canonical")
    return resolved, hashlib.sha256(payload).hexdigest()


def _discover_git_common_dir(workspace: Path, git_env: dict[str, str]) -> Path:
    """Discover one canonical absolute common Git directory with fixed argv."""
    try:
        result = subprocess.run(
            ["git", "-C", str(workspace), "rev-parse", "--git-common-dir"],
            env=git_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("mesh sandboxed workspace common git discovery failed") from None
    if len(result.stdout) > MAX_GIT_TOPLEVEL_BYTES or len(result.stderr) > MAX_GIT_TOPLEVEL_BYTES:
        raise ValueError("mesh sandboxed workspace common git discovery exceeded its output limit")
    if result.returncode != 0 or not result.stdout:
        raise ValueError("mesh sandboxed workspace common git discovery failed")
    try:
        raw = result.stdout.decode("utf-8", "strict")
    except UnicodeDecodeError:
        raise ValueError("mesh sandboxed workspace common git directory is not UTF-8") from None
    if not re.fullmatch(r"/[^\r\n:]+\n", raw):
        raise ValueError("mesh sandboxed workspace common git directory must be one absolute line")
    raw_path = raw[:-1]
    if len(raw_path.encode("utf-8")) > MAX_WORKSPACE_PATH_BYTES:
        raise ValueError("mesh sandboxed workspace common git directory exceeded its path limit")
    path = Path(raw_path)
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise ValueError(f"mesh sandboxed workspace common git directory is unavailable: {error}") from None
    if str(resolved) != raw_path:
        raise ValueError("mesh sandboxed workspace common git directory is not canonical")
    return resolved


def _validated_sandboxed_workspace() -> tuple[
    Path, tuple[int, int, int, int], tuple[int, int, int, int, int],
    GitMetadataBinding | None,
]:
    """Bind the exact invocation cwd and record custody identity for revalidation.

    Returns (resolved_workspace, workspace_identity, git_identity,
    git_metadata_binding) where the
    workspace identity is (dev, ino, mode, uid) and the .git identity is
    (dev, ino, mode, uid, effective_link_count). Enforces a bounded UTF-8 absolute
    resolved cwd, owner uid, and no group/other write on the workspace or its
    `.git` entry, which must be an owner-owned non-symlink directory or a
    single-link regular worktree pointer. Git discovery uses fixed argv with no
    shell and removes every inherited GIT_* variable before exec. A linked
    worktree additionally binds the exact owner-owned, non-group/other-writable
    common Git directory as read-only metadata so Git verification works inside
    the sandbox.
    """
    raw = os.getcwd()
    try:
        raw_bytes = raw.encode("utf-8", "strict")
    except UnicodeEncodeError as error:
        raise ValueError("mesh sandboxed workspace cwd is not valid UTF-8") from error
    if len(raw_bytes) > MAX_WORKSPACE_PATH_BYTES or not os.path.isabs(raw):
        raise ValueError("mesh sandboxed workspace cwd is not a bounded absolute UTF-8 path")
    cwd = Path(raw).resolve(strict=True)
    try:
        resolved_bytes = str(cwd).encode("utf-8", "strict")
    except UnicodeEncodeError as error:
        raise ValueError("mesh sandboxed workspace resolved cwd is not valid UTF-8") from error
    if len(resolved_bytes) > MAX_WORKSPACE_PATH_BYTES or not os.path.isabs(str(cwd)):
        raise ValueError("mesh sandboxed workspace resolved cwd is not a bounded absolute path")
    try:
        ws_info = cwd.lstat()
    except OSError as error:
        raise ValueError(f"mesh sandboxed workspace is unavailable: {error}") from None
    if (
        cwd.is_symlink()
        or not stat.S_ISDIR(ws_info.st_mode)
        or ws_info.st_uid != os.geteuid()
        or stat.S_IMODE(ws_info.st_mode) & 0o022
    ):
        raise ValueError("mesh sandboxed workspace must be an owner-owned non-symlink directory with no group/other write")
    workspace_identity = (
        ws_info.st_dev, ws_info.st_ino, stat.S_IMODE(ws_info.st_mode), ws_info.st_uid,
    )
    git_env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
            env=git_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("mesh sandboxed workspace git discovery failed") from None
    if len(result.stdout) > MAX_GIT_TOPLEVEL_BYTES or len(result.stderr) > MAX_GIT_TOPLEVEL_BYTES:
        raise ValueError("mesh sandboxed workspace git discovery exceeded its output limit")
    if result.returncode != 0 or not result.stdout:
        raise ValueError("mesh sandboxed workspace is not inside a git worktree")
    try:
        top_raw = result.stdout.decode("utf-8", "strict").rstrip("\n")
    except UnicodeDecodeError:
        raise ValueError("mesh sandboxed workspace git toplevel is not UTF-8") from None
    if len(top_raw.encode("utf-8", "strict")) > MAX_WORKSPACE_PATH_BYTES or not os.path.isabs(top_raw):
        raise ValueError("mesh sandboxed workspace git toplevel is not a bounded absolute path")
    top = Path(top_raw)
    if top.resolve(strict=True) != cwd.resolve(strict=True):
        raise ValueError("mesh sandboxed workspace cwd does not equal its exact git toplevel")
    git_entry = cwd / ".git"
    try:
        git_info = git_entry.lstat()
    except OSError as error:
        raise ValueError(f"mesh sandboxed workspace .git is unavailable: {error}") from None
    if git_entry.is_symlink():
        raise ValueError("mesh sandboxed workspace .git must not be a symlink")
    git_metadata_binding: GitMetadataBinding | None = None
    if stat.S_ISDIR(git_info.st_mode):
        git_mode = stat.S_IMODE(git_info.st_mode)
        if git_info.st_uid != os.geteuid() or git_mode & 0o022:
            raise ValueError("mesh sandboxed workspace .git directory must be owner-owned with no group/other write")
        # A directory's link count changes whenever a child directory is added or
        # removed; record a stable sentinel so ordinary child churn never produces
        # false drift while dev/ino/mode/uid still pin the exact directory inode.
        git_link_count: int = GIT_DIRECTORY_LINK_SENTINEL
    elif stat.S_ISREG(git_info.st_mode):
        if git_info.st_nlink != 1:
            raise ValueError("mesh sandboxed workspace .git pointer must be single-link")
        git_mode = stat.S_IMODE(git_info.st_mode)
        if git_info.st_uid != os.geteuid() or git_mode & 0o022:
            raise ValueError("mesh sandboxed workspace .git pointer must be owner-owned with no group/other write")
        git_link_count = git_info.st_nlink
    else:
        raise ValueError("mesh sandboxed workspace .git must be a directory or single-link worktree pointer")
    git_identity = (git_info.st_dev, git_info.st_ino, git_mode, git_info.st_uid, git_link_count)
    if stat.S_ISREG(git_info.st_mode):
        target, pointer_sha256 = _read_linked_worktree_pointer(git_entry, git_identity)
        common = _discover_git_common_dir(cwd, git_env)
        common_identity = _git_directory_identity(common)
        protected_by_private_common = common_identity[2] & 0o077 == 0
        worktrees = common / "worktrees"
        worktrees_identity = _git_directory_identity(
            worktrees,
            protected_by_private_ancestor=protected_by_private_common,
        )
        target_identity = _git_directory_identity(
            target,
            protected_by_private_ancestor=protected_by_private_common,
        )
        if target.parent != worktrees or target.name in {"", ".", ".."}:
            raise ValueError(
                "mesh sandboxed workspace .git pointer is foreign to the discovered common git directory"
            )
        git_metadata_binding = (
            common, common_identity, worktrees, worktrees_identity,
            target, target_identity, pointer_sha256,
        )
    if Path(os.getcwd()).resolve(strict=True) != cwd.resolve(strict=True):
        raise ValueError("mesh sandboxed workspace cwd drifted during validation")
    _revalidate_workspace_identity(
        cwd, workspace_identity, git_identity, git_metadata_binding,
    )
    return cwd, workspace_identity, git_identity, git_metadata_binding


def _workspace_identity_sha256(
    workspace: Path, workspace_identity: tuple[int, int, int, int],
    git_identity: tuple[int, int, int, int, int],
    git_metadata_binding: GitMetadataBinding | None = None,
) -> str:
    """Hash canonical content-free custody identity data for the sandboxed workspace.

    Covers every recorded custody dimension: the full 4-int workspace identity
    (dev, ino, mode, uid), the full 5-int .git identity
    (dev, ino, mode, uid, effective_link_count), and the workspace path, hashed
    as canonical sorted compact JSON with SHA-256. No element may be sliced
    away: each dimension must change the hash.
    """
    canonical = json.dumps(
        {
            "workspace": str(workspace),
            "workspaceIdentity": list(workspace_identity),
            "gitIdentity": list(git_identity),
            "gitMetadataBinding": (
                None if git_metadata_binding is None else {
                    "common": str(git_metadata_binding[0]),
                    "commonIdentity": list(git_metadata_binding[1]),
                    "worktrees": str(git_metadata_binding[2]),
                    "worktreesIdentity": list(git_metadata_binding[3]),
                    "target": str(git_metadata_binding[4]),
                    "targetIdentity": list(git_metadata_binding[5]),
                    "pointerSha256": git_metadata_binding[6],
                }
            ),
        },
        sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _revalidate_workspace_identity(
    workspace: Path, workspace_identity: tuple[int, int, int, int],
    git_identity: tuple[int, int, int, int, int],
    git_metadata_binding: GitMetadataBinding | None = None,
) -> None:
    """Re-lstat workspace and .git and fail closed on any identity drift."""
    try:
        ws_now = workspace.lstat()
    except OSError as error:
        raise ValueError(f"mesh sandboxed workspace identity recheck failed: {error}") from None
    if (
        workspace.is_symlink()
        or not stat.S_ISDIR(ws_now.st_mode)
        or (ws_now.st_dev, ws_now.st_ino, stat.S_IMODE(ws_now.st_mode), ws_now.st_uid) != workspace_identity
    ):
        raise ValueError("mesh sandboxed workspace identity drifted")
    git_entry = workspace / ".git"
    try:
        git_now = git_entry.lstat()
    except OSError as error:
        raise ValueError(f"mesh sandboxed workspace .git identity recheck failed: {error}") from None
    if git_entry.is_symlink():
        raise ValueError("mesh sandboxed workspace .git became a symlink")
    # Type custody is revalidated structurally; the recorded identity pins the
    # exact dev/ino/mode/uid plus the effective link count (real nlink for a
    # regular pointer, stable sentinel for a directory inode).
    if stat.S_ISDIR(git_now.st_mode):
        git_effective_links: int = GIT_DIRECTORY_LINK_SENTINEL
    elif stat.S_ISREG(git_now.st_mode):
        git_effective_links = git_now.st_nlink
    else:
        raise ValueError("mesh sandboxed workspace .git changed its file type")
    if (
        git_now.st_dev, git_now.st_ino, stat.S_IMODE(git_now.st_mode), git_now.st_uid,
        git_effective_links,
    ) != git_identity:
        raise ValueError("mesh sandboxed workspace .git identity drifted")
    if git_metadata_binding is not None:
        (
            common, common_identity, worktrees, worktrees_identity,
            target, target_identity, pointer_sha256,
        ) = git_metadata_binding
        if not stat.S_ISREG(git_now.st_mode):
            raise ValueError("mesh sandboxed linked-worktree .git pointer changed its file type")
        target_now, pointer_sha256_now = _read_linked_worktree_pointer(git_entry, git_identity)
        if target_now != target or not hmac.compare_digest(pointer_sha256_now, pointer_sha256):
            raise ValueError("mesh sandboxed linked-worktree .git pointer content drifted")
        if _git_directory_identity(common) != common_identity:
            raise ValueError("mesh sandboxed common git directory identity drifted")
        protected_by_private_common = common_identity[2] & 0o077 == 0
        if _git_directory_identity(
            worktrees,
            protected_by_private_ancestor=protected_by_private_common,
        ) != worktrees_identity:
            raise ValueError("mesh sandboxed common worktrees directory identity drifted")
        if _git_directory_identity(
            target,
            protected_by_private_ancestor=protected_by_private_common,
        ) != target_identity:
            raise ValueError("mesh sandboxed linked-worktree git directory identity drifted")
        if worktrees != common / "worktrees" or target.parent != worktrees:
            raise ValueError("mesh sandboxed linked-worktree git directory became foreign")


def parse_sandboxed_allowed_tools(raw: str | None) -> tuple[str, ...]:
    """Parse the optional sandboxed allowlist override from the environment.

    An unset value returns the fixed immutable SANDBOXED_ALLOWED_TOOLS
    unchanged. A set value must be 1..SANDBOXED_ALLOWED_TOOLS_MAX_BYTES UTF-8
    bytes of comma-separated, exact case-sensitive tokens with no empty token,
    whitespace, duplicate, or value outside SANDBOXED_ALLOWED_TOOLS. The
    result is an immutable tuple in the supplied order. Prompt and stdin
    content can never set or widen this value.
    """
    if raw is None:
        return SANDBOXED_ALLOWED_TOOLS
    if not isinstance(raw, str):
        raise ValueError(f"{SANDBOXED_ALLOWED_TOOLS_ENV} must be a string")
    try:
        raw_bytes = raw.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError(
            f"{SANDBOXED_ALLOWED_TOOLS_ENV} value must be valid UTF-8"
        ) from error
    if not 1 <= len(raw_bytes) <= SANDBOXED_ALLOWED_TOOLS_MAX_BYTES:
        raise ValueError(
            f"{SANDBOXED_ALLOWED_TOOLS_ENV} must be 1.."
            f"{SANDBOXED_ALLOWED_TOOLS_MAX_BYTES} UTF-8 bytes"
        )
    tokens = raw.split(",")
    for token in tokens:
        if not token:
            raise ValueError(f"{SANDBOXED_ALLOWED_TOOLS_ENV} contains an empty token")
        if any(character.isspace() for character in token):
            raise ValueError(
                f"{SANDBOXED_ALLOWED_TOOLS_ENV} token contains whitespace: {token!r}"
            )
        if token not in SANDBOXED_ALLOWED_TOOLS:
            raise ValueError(
                f"{SANDBOXED_ALLOWED_TOOLS_ENV} token is not a permitted sandboxed "
                f"tool: {token!r}"
            )
    if len(set(tokens)) != len(tokens):
        raise ValueError(f"{SANDBOXED_ALLOWED_TOOLS_ENV} repeats a token")
    return tuple(tokens)


def parse_sandboxed_model_pin(
    pin_raw: str | None, base_url_raw: str | None,
) -> tuple[str, str, str] | None:
    """Parse one owner-process-selected loopback inference pin.

    The two environment values are an all-or-none authority input. Prompt,
    stdin, tool output, and the canonical OpenClaw config cannot select them.
    Only a canonical HTTP loopback ``/v1`` endpoint is admitted.
    """
    if pin_raw is None and base_url_raw is None:
        return None
    if pin_raw is None or base_url_raw is None:
        raise ValueError(
            f"{SANDBOXED_MODEL_PIN_ENV} and {SANDBOXED_MODEL_BASE_URL_ENV} "
            "must be set together"
        )
    if not isinstance(pin_raw, str) or not isinstance(base_url_raw, str):
        raise ValueError("sandboxed model pin values must be strings")
    try:
        pin_bytes = pin_raw.encode("ascii")
        base_url_bytes = base_url_raw.encode("ascii")
    except UnicodeEncodeError:
        raise ValueError("sandboxed model pin values must be printable ASCII") from None
    if not 1 <= len(pin_bytes) <= SANDBOXED_MODEL_PIN_MAX_BYTES:
        raise ValueError(
            f"{SANDBOXED_MODEL_PIN_ENV} must be 1..{SANDBOXED_MODEL_PIN_MAX_BYTES} bytes"
        )
    if not 1 <= len(base_url_bytes) <= SANDBOXED_MODEL_BASE_URL_MAX_BYTES:
        raise ValueError(
            f"{SANDBOXED_MODEL_BASE_URL_ENV} must be "
            f"1..{SANDBOXED_MODEL_BASE_URL_MAX_BYTES} bytes"
        )
    if any(byte < 0x21 or byte > 0x7E for byte in pin_bytes):
        raise ValueError(f"{SANDBOXED_MODEL_PIN_ENV} must be printable without whitespace")
    if pin_raw.count("/") != 1:
        raise ValueError(f"{SANDBOXED_MODEL_PIN_ENV} must be exactly provider/model")
    pin_provider, pin_model = pin_raw.split("/", 1)
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", pin_provider):
        raise ValueError(f"{SANDBOXED_MODEL_PIN_ENV} provider is invalid")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,256}", pin_model):
        raise ValueError(f"{SANDBOXED_MODEL_PIN_ENV} model is invalid")

    try:
        parsed = urlsplit(base_url_raw)
        port = parsed.port
    except ValueError as error:
        raise ValueError(f"{SANDBOXED_MODEL_BASE_URL_ENV} is invalid: {error}") from None
    if parsed.scheme != "http":
        raise ValueError(f"{SANDBOXED_MODEL_BASE_URL_ENV} must use http")
    host = parsed.hostname
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError(f"{SANDBOXED_MODEL_BASE_URL_ENV} must name an exact loopback host")
    if port is None or not 1 <= port <= 65535:
        raise ValueError(f"{SANDBOXED_MODEL_BASE_URL_ENV} must include port 1..65535")
    if parsed.path != "/v1":
        raise ValueError(f"{SANDBOXED_MODEL_BASE_URL_ENV} path must be exactly /v1")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{SANDBOXED_MODEL_BASE_URL_ENV} must not contain userinfo")
    if parsed.query or parsed.fragment:
        raise ValueError(f"{SANDBOXED_MODEL_BASE_URL_ENV} must not contain query or fragment")
    rendered_host = "[::1]" if host == "::1" else host
    canonical_url = f"http://{rendered_host}:{port}/v1"
    if base_url_raw != canonical_url:
        raise ValueError(f"{SANDBOXED_MODEL_BASE_URL_ENV} must use canonical spelling")
    return pin_provider, pin_model, canonical_url


def create_sandboxed_config(
    state: Path, workspace: Path, workspace_identity: tuple[int, int, int, int],
    git_identity: tuple[int, int, int, int, int],
    git_metadata_binding: GitMetadataBinding | None = None,
    allowed_tools: tuple[str, ...] = SANDBOXED_ALLOWED_TOOLS,
    workspace_access: str = "rw",
    model_pin: tuple[str, str, str] | None = None,
) -> tuple[Path, str, tuple[int, int, int, int, int], str]:
    """Derive one ephemeral config that sandboxes the Pixel agent to the workspace.

    Returns (path, rendered sha256, original config custody identity
    (dev, ino, mode, uid, nlink), workspace identity hash).

    The full parsed source object and all unrelated nested fields (including
    provider/model data and credentials) are preserved verbatim; only the keys
    required for the sandboxed workspace profile are added or modified.

    `allowed_tools` must be either a non-empty, duplicate-free subset of the
    fixed SANDBOXED_ALLOWED_TOOLS or the exact closed mailbox-read-only tuple;
    both the agent-level and top-level sandbox allowlists are written from it.

    `workspace_access` must be exactly ``"rw"`` or ``"ro"``. Generic ``"rw"``
    permits applyPatch, while the exact mailbox profile keeps it disabled.
    Model and prompt cannot select mount mode or inference. An optional
    owner-process model pin may add one loopback-only provider to this
    ephemeral config; the canonical source config is never modified.
    """
    # Fail closed before any filesystem checks: workspace_access is a strictly
    # validated boolean select from the mesh peer, never from model/prompt/env.
    if workspace_access not in ("rw", "ro"):
        raise ValueError("sandboxed workspace_access must be exactly 'rw' or 'ro'")
    _revalidate_workspace_identity(
        workspace, workspace_identity, git_identity, git_metadata_binding,
    )
    config, _, source_identity = _load_bounded_owner_private_config(state)
    source = state / "openclaw.json"
    if _source_config_lstat_identity(source) != source_identity:
        raise ValueError("OpenClaw config source drifted before sandboxed config creation")

    # Fail closed before any config is derived or written. The generic route
    # may only select a subset of its original fixed tool universe. The mailbox
    # route is admitted only as one exact tuple and can never be selected by the
    # generic environment parser.
    if not isinstance(allowed_tools, tuple) or not allowed_tools:
        raise ValueError("sandboxed allowed tools must be a non-empty tuple")
    if len(set(allowed_tools)) != len(allowed_tools):
        raise ValueError("sandboxed allowed tools must not repeat a tool")
    mailbox_profile = allowed_tools == SANDBOXED_MAILBOX_READONLY_ALLOWED_TOOLS
    if not mailbox_profile:
        for tool_name in allowed_tools:
            if not isinstance(tool_name, str) or tool_name not in SANDBOXED_ALLOWED_TOOLS:
                raise ValueError(f"sandboxed allowed tool is not permitted: {tool_name!r}")
    if mailbox_profile and workspace_access != "rw":
        raise ValueError("sandboxed mailbox read-only profile requires rw workspace access")
    if workspace_access == "ro" and allowed_tools != SANDBOXED_READONLY_ALLOWED_TOOLS:
        raise ValueError("sandboxed read-only workspace requires the fixed read/exec allowlist")
    apply_patch_enabled = workspace_access == "rw" and not mailbox_profile

    # Ensure `agents` is a dict and modify defaults (never agent-level keys).
    agents = config.get("agents")
    if agents is None:
        agents = {}
        config["agents"] = agents
    if not isinstance(agents, dict):
        raise ValueError("OpenClaw config `agents` must be a dict")
    defaults = agents.get("defaults")
    if defaults is None:
        defaults = {}
        agents["defaults"] = defaults
    if not isinstance(defaults, dict):
        raise ValueError("OpenClaw config `agents.defaults` must be a dict")
    defaults["skipBootstrap"] = True
    defaults["contextInjection"] = "never"

    # Exactly one Pixel agent gets the workspace, the exact validated allowlist, and sandbox.
    agent_list = agents.get("list")
    if not isinstance(agent_list, list):
        raise ValueError("OpenClaw config agents.list must be a list")
    matches = [agent for agent in agent_list if isinstance(agent, dict) and agent.get("id") == "pixel"]
    if len(matches) != 1:
        raise ValueError("OpenClaw config must contain exactly one Pixel agent")
    if model_pin is not None:
        if (
            not isinstance(model_pin, tuple)
            or len(model_pin) != 3
            or not all(isinstance(value, str) for value in model_pin)
        ):
            raise ValueError("sandboxed model pin binding is invalid")
        pin_provider, pin_model, pin_base_url = model_pin
        source_model_reference = matches[0].get("model")
        if not isinstance(source_model_reference, str) or "/" not in source_model_reference:
            raise ValueError("sandboxed model pin requires a valid source model reference")
        source_provider_id, source_model_id = source_model_reference.split("/", 1)
        models = config.get("models")
        providers = models.get("providers") if isinstance(models, dict) else None
        source_provider = (
            providers.get(source_provider_id) if isinstance(providers, dict) else None
        )
        if not isinstance(source_provider, dict):
            raise ValueError("sandboxed model pin source provider is unavailable")
        if pin_provider in providers:
            raise ValueError("sandboxed model pin provider collides with source config")
        if source_provider.get("api") != "openai-completions":
            raise ValueError("sandboxed model pin source provider is not OpenAI-compatible")
        source_api_key = source_provider.get("apiKey")
        if (
            not isinstance(source_api_key, str)
            or not source_api_key
            or len(source_api_key) > 4096
            or any(ord(char) < 0x20 or ord(char) == 0x7F for char in source_api_key)
        ):
            raise ValueError("sandboxed model pin source provider credentials are invalid")
        source_registry = source_provider.get("models")
        source_matches = [
            model for model in source_registry
            if isinstance(model, dict) and model.get("id") == source_model_id
        ] if isinstance(source_registry, list) else []
        if len(source_matches) != 1:
            raise ValueError("sandboxed model pin source model is absent or duplicated")
        # The source record proves a supported upper bound, but it describes a
        # different model. Carry only conservative generic limits so a smaller
        # pinned model is never advertised with the source model's full budget.
        pin_model_record: dict[str, object] = {"id": pin_model, "name": pin_model}
        for key, cap in (("contextWindow", 32768), ("maxTokens", 4096)):
            value = source_matches[0].get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                pin_model_record[key] = min(value, cap)
        pinned_provider = copy.deepcopy(source_provider)
        pinned_provider["baseUrl"] = pin_base_url
        pinned_provider["models"] = [pin_model_record]
        providers[pin_provider] = pinned_provider
        matches[0]["model"] = f"{pin_provider}/{pin_model}"
    matches[0]["tools"] = {"allow": list(allowed_tools)}
    matches[0]["workspace"] = str(workspace)
    matches[0]["sandbox"] = {
        "mode": "all",
        "backend": "docker",
        "scope": "session",
        "workspaceAccess": workspace_access,
        "docker": {
            "image": SANDBOX_DOCKER_IMAGE,
            "workdir": SANDBOX_DOCKER_WORKDIR,
            "readOnlyRoot": True,
            "tmpfs": ["/tmp", "/var/tmp", "/run"],
            "network": "none",
            "user": "1000:1000",
            "capDrop": ["ALL"],
            "pidsLimit": 256,
            "memory": "8g",
            "memorySwap": "8g",
            "cpus": 4,
        },
    }
    if git_metadata_binding is not None:
        common = git_metadata_binding[0]
        matches[0]["sandbox"]["docker"]["binds"] = [
            f"{common}:{common}:ro",
        ]
        # OpenClaw permits binds outside its workspace root only through this
        # break-glass key. Pixel sets it solely for the one owner-validated,
        # identity-bound, read-only common Git directory above; model and prompt
        # content cannot select the path or enable the override.
        matches[0]["sandbox"]["docker"][
            "dangerouslyAllowExternalBindSources"
        ] = True

    # Preserve the existing top-level `tools` object, then set the sandboxed keys.
    tools = config.get("tools")
    if tools is None:
        tools = {}
        config["tools"] = tools
    if not isinstance(tools, dict):
        raise ValueError("OpenClaw config `tools` must be a dict")
    tools["profile"] = "coding"
    tools["fs"] = {"workspaceOnly": True}
    exec_options = tools.get("exec")
    if exec_options is None:
        exec_options = {}
        tools["exec"] = exec_options
    if not isinstance(exec_options, dict):
        raise ValueError("OpenClaw config `tools.exec` must be a dict")
    exec_options["host"] = "sandbox"
    exec_options["timeoutSec"] = 1800
    exec_options["applyPatch"] = {"enabled": apply_patch_enabled, "workspaceOnly": True}
    tools["sandbox"] = {
        "tools": {"allow": list(allowed_tools), "deny": ["image"]},
    }
    tools["elevated"] = {"enabled": False}

    # The incorrect top-level `sandbox`/`elevated` keys introduced by PXL119 were
    # produced in code, not in the source file, and are no longer added here. Any
    # top-level keys present in the freshly parsed source pre-existed and are
    # preserved verbatim along with every other unrelated key.

    path, rendered_sha256, expected_identity = _write_ephemeral_config(state, config)
    return path, rendered_sha256, expected_identity, _workspace_identity_sha256(
        workspace, workspace_identity, git_identity, git_metadata_binding,
    )


def local_sandboxed_agent() -> int:
    """Run one sandboxed local agent turn bound to the exact validated invocation workspace."""
    # Read and parse the override exactly once, before any config creation or
    # workspace validation/runner launch; only the owner process may select it.
    allowed_tools = parse_sandboxed_allowed_tools(
        os.environ.get(SANDBOXED_ALLOWED_TOOLS_ENV)
    )
    model_pin = parse_sandboxed_model_pin(
        os.environ.get(SANDBOXED_MODEL_PIN_ENV),
        os.environ.get(SANDBOXED_MODEL_BASE_URL_ENV),
    )
    workspace, workspace_identity, git_identity, git_metadata_binding = (
        _validated_sandboxed_workspace()
    )
    return local_agent(
        sys.stdin.buffer.read(MAX_MESSAGE_BYTES + 1),
        sandboxed=True, sandboxed_workspace=workspace,
        sandboxed_workspace_identity=workspace_identity,
        sandboxed_git_identity=git_identity,
        sandboxed_git_metadata_binding=git_metadata_binding,
        sandboxed_allowed_tools=allowed_tools,
        sandboxed_model_pin=model_pin,
    )


def local_sandboxed_mailbox_readonly_agent() -> int:
    """Run one closed mailbox-read-only turn in the exact validated sandbox workspace.

    The exact workspace is the only writable host bind and is writable only
    from inside the networkless Docker sandbox. Generic host exec/filesystem
    authority is absent, applyPatch is disabled, and environment configuration
    cannot alter the fixed tool tuple.
    """
    model_pin = parse_sandboxed_model_pin(
        os.environ.get(SANDBOXED_MODEL_PIN_ENV),
        os.environ.get(SANDBOXED_MODEL_BASE_URL_ENV),
    )
    workspace, workspace_identity, git_identity, git_metadata_binding = (
        _validated_sandboxed_workspace()
    )
    return local_agent(
        sys.stdin.buffer.read(MAX_MESSAGE_BYTES + 1),
        sandboxed=True, sandboxed_workspace=workspace,
        sandboxed_workspace_identity=workspace_identity,
        sandboxed_git_identity=git_identity,
        sandboxed_git_metadata_binding=git_metadata_binding,
        sandboxed_allowed_tools=SANDBOXED_MAILBOX_READONLY_ALLOWED_TOOLS,
        sandboxed_model_pin=model_pin,
        sandboxed_mailbox_readonly=True,
    )


def local_sandboxed_readonly_agent() -> int:
    """Run one sandboxed read-only local agent turn bound to the exact validated invocation workspace.

    Uses the fixed read/exec allowlist; no environment override is permitted.
    Workspace access is strictly read-only.
    """
    model_pin = parse_sandboxed_model_pin(
        os.environ.get(SANDBOXED_MODEL_PIN_ENV),
        os.environ.get(SANDBOXED_MODEL_BASE_URL_ENV),
    )
    workspace, workspace_identity, git_identity, git_metadata_binding = (
        _validated_sandboxed_workspace()
    )
    return local_agent(
        sys.stdin.buffer.read(MAX_MESSAGE_BYTES + 1),
        sandboxed=True, sandboxed_workspace=workspace,
        sandboxed_workspace_identity=workspace_identity,
        sandboxed_git_identity=git_identity,
        sandboxed_git_metadata_binding=git_metadata_binding,
        sandboxed_allowed_tools=SANDBOXED_READONLY_ALLOWED_TOOLS,
        sandboxed_model_pin=model_pin,
        sandboxed_readonly=True,
    )


def main() -> int:
    if len(sys.argv) == 2 and sys.argv[1] == "status":
        print(json.dumps(local_status(), separators=(",", ":")))
        return 0
    if len(sys.argv) == 2 and sys.argv[1] == "fleet-status":
        print(json.dumps(fleet_status(), separators=(",", ":")))
        return 0
    if len(sys.argv) == 3 and sys.argv[1] == "peer-status":
        print(json.dumps(remote_status(sys.argv[2]), separators=(",", ":")))
        return 0
    message_commands = {
        "message", "message-readonly", "message-contract", "message-readonly-contract",
    }
    if len(sys.argv) == 3 and sys.argv[1] in message_commands:
        options = {}
        if "readonly" in sys.argv[1]:
            options["read_only"] = True
        if "contract" in sys.argv[1]:
            options["contract"] = True
        return send_message(
            sys.argv[2], sys.stdin.buffer.read(MAX_MESSAGE_BYTES + 1),
            **options,
        )
    local_commands = {
        "ask", "ask-readonly", "ask-contract", "ask-readonly-contract",
        "receive-message", "receive-message-readonly",
        "receive-message-contract", "receive-message-readonly-contract",
    }
    # Routing: recognize only len==2 and exact arg; sys.argv[0] may be absolute.
    if len(sys.argv) == 2 and sys.argv[1] == "ask-sandboxed":
        return local_sandboxed_agent()
    if len(sys.argv) == 2 and sys.argv[1] == "ask-sandboxed-mailbox-readonly":
        return local_sandboxed_mailbox_readonly_agent()
    if len(sys.argv) == 2 and sys.argv[1] == "ask-sandboxed-readonly":
        return local_sandboxed_readonly_agent()
    fixed_authority_commands = {
        "ask-operations-broker": "operations-broker",
        "ask-operations-broker-contract": "operations-broker",
        "ask-download-staging": "download-staging",
        "ask-download-staging-contract": "download-staging",
    }
    if len(sys.argv) == 2 and sys.argv[1] in fixed_authority_commands:
        return local_agent(
            sys.stdin.buffer.read(MAX_MESSAGE_BYTES + 1),
            contract=sys.argv[1].endswith("-contract"),
            fixed_authority_profile=fixed_authority_commands[sys.argv[1]],
        )
    if len(sys.argv) == 2 and sys.argv[1] in local_commands:
        options = {}
        if "readonly" in sys.argv[1]:
            options["read_only"] = True
        if "contract" in sys.argv[1]:
            options["contract"] = True
        return local_agent(
            sys.stdin.buffer.read(MAX_MESSAGE_BYTES + 1),
            **options,
        )
    print(
        "usage: pixel-mesh-peer status|fleet-status|peer-status PEER|"
        "message PEER|message-readonly PEER|message-contract PEER|"
        "message-readonly-contract PEER|ask|ask-readonly|ask-contract|"
        "ask-readonly-contract|ask-operations-broker|ask-operations-broker-contract|"
        "ask-download-staging|ask-download-staging-contract|ask-sandboxed|"
        "ask-sandboxed-mailbox-readonly|ask-sandboxed-readonly",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        print(json.dumps({"schemaVersion": 1, "observedAt": now(), "error": str(error)}), file=sys.stderr)
        raise SystemExit(2)
