#!/usr/bin/env python3
"""Live docker-backed system adapter for portal outcome orchestration.

Every docker invocation mirrors a command already proven in qualification probes;
pure parsing helpers are unit-tested and the subprocess layer stays thin.
"""

from __future__ import annotations

import base64
import importlib.util
import difflib
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import stat
import subprocess
import tempfile
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

import portal_outcome_evaluation as evaluation
import portal_outcome_runtime_control as runtime_control
import portal_outcome_task as outcome_task


NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
EXECUTABLE_PATH_RE = re.compile(r"^/[A-Za-z0-9/._-]{1,255}$")
RUN_ID_SUFFIX_RE = re.compile(r"[a-f0-9]{12}$")
WORK_JOB_RE = re.compile(r"^work-[0-9]{13}-[a-f0-9]{12}$")
CODEX_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:+/-]{0,127}$")
REQUEST_COUNTER_NAMES = ("vllm:request_success_total",)
MODEL_ALIAS = "pixel-local-model"
BACKEND_ALIAS = "pixel-local-backend"
CODEX_PINNED_VERSION = "0.147.0"
CODEX_PINNED_PROMPT_RELATIVE = Path("deploy/agent-comparison/codex-0.147.0-prompt.md")
CODEX_PINNED_PROMPT_SHA256 = "ac8ae107a0d72fe3476b430afb161ea4e67da2e446d778aefc44828160559807"
CODEX_MODEL_CATALOG_CONTAINER_PATH = "/input/model-catalog.json"
CODEX_RESEARCH_OUTPUT_SCHEMA_CONTAINER_PATH = "/input/research-output-schema.json"
CODEX_COMPARISON_TOOLCHAIN = {
    "codex": "codex-cli 0.147.0", "node": "v22.23.1", "python": "3.11.2",
    "gdb": "GNU gdb (Debian 13.1-3) 13.1", "pyright": "pyright 1.1.411",
    "debugpy": "1.6.3+git20221103.a2a3328", "sqlite": "3.40.1",
    "duckdb": "1.5.5", "polars": "1.43.2",
}
RESEARCH_MCP_ALIAS = "research-mcp"
MAX_WORKSPACE_FILES = 100000
INDEPENDENT_VERIFICATION_BOUNDARY = (
    "Content-free independent workspace verification evidence only. Controller-selected checks ran against a "
    "fresh candidate copy without network; worker output could not select checks or grant execution, external-effect, "
    "merge, deployment, publication, completion, acceptance, or promotion authority."
)
RESEARCH_MCP_CONFIG_BOUNDARY = (
    "Owner-private configuration for one job-scoped, internal-only Codex Research MCP facade over Pixel's exact "
    "split-queue Research Broker. It grants no direct public network, credential, external-write, publication, "
    "purchase, merge, deploy, policy, scope, completion, or promotion authority."
)
RESEARCH_MCP_RECEIPT_BOUNDARY = (
    "Content-free lifecycle evidence for one job-scoped Codex Research MCP facade. It proves only bounded "
    "first-class tool transport over Pixel's split queue and grants no source truth, semantic entailment, network, "
    "credential, external effect, publication, completion, acceptance, or promotion authority."
)
CODEX_RESEARCH_AUTHORITY_INPUT_BOUNDARY = (
    "Owner-private input for one same-model Codex Researcher run through Pixel's exact job-scoped Research Broker "
    "authority. It grants no model start, direct public network, credential, external write, publication, purchase, "
    "merge, deploy, policy, scope, completion, acceptance, or promotion authority."
)
CODEX_RESEARCH_FINALIZE_BOUNDARY = (
    "Owner-private handoff of one untrusted structured Codex Researcher proposal after its MCP transport has "
    "stopped. The proposal grants no verification, publication, action, completion, acceptance, or promotion authority."
)
CODEX_RESEARCH_READY_BOUNDARY = (
    "Content-free private readiness evidence for one Codex Researcher authority service. It binds the split tool "
    "queue to Pixel's exact Researcher plan, lease, claim, policy, environment, and local broker without granting "
    "research truth, model, network, credential, external-effect, completion, acceptance, or promotion authority."
)
CODEX_RESEARCH_RECEIPT_BOUNDARY = (
    "Content-free private lifecycle evidence for one Codex Researcher authority service. It proves bounded broker "
    "processing, deterministic offline citation verification, retained artifact identities, and cleanup only; it "
    "grants no semantic entailment, source truth, publication, action, completion, acceptance, or promotion authority."
)
CODEX_RESEARCH_AUTHORITY = {
    "directPublicNetworkToModel": False, "credentialsToModel": False, "externalWrites": False,
    "publish": False, "purchase": False, "merge": False, "deploy": False,
    "policyMutation": False, "scopeExpansion": False,
}
CODEX_HARNESS_FILES = (
    "scripts/portal_outcome_evaluation.py",
    "scripts/portal_outcome_task.py",
    "scripts/portal_outcome_verifier.py",
    "scripts/portal_outcome_runner.py",
    "scripts/portal_outcome_livesystem.py",
    "scripts/portal_outcome_orchestrate.py",
    "scripts/portal_outcome_runtime_control.py",
    "scripts/portal_outcome_pair.py",
    "scripts/portal_outcome_pair_preflight.py",
    "scripts/portal_outcome_pixel_livesystem.py",
    "scripts/portal_outcome_pixel_orchestrate.py",
    "scripts/portal_outcome_battery_campaign.py",
    "scripts/portal_outcome_research_evidence.py",
    "scripts/codex_comparison_workspace.py",
    "scripts/qualify_codex_comparison_surface.py",
    "deploy/frontier-broker/broker.py",
    "deploy/agent-comparison/Dockerfile.codex-runner",
    "deploy/agent-comparison/inference-boundary.mjs",
    "deploy/agent-comparison/research-mcp-server.mjs",
    "deploy/agent-comparison/codex-research-authority.mjs",
    "deploy/agent-comparison/research-fixture-adapter.mjs",
    "deploy/agent-comparison/pixel-arm.mjs",
    "deploy/agent-comparison/pixel-system-cli.mjs",
    CODEX_PINNED_PROMPT_RELATIVE.as_posix(),
    "deploy/work-broker/broker.mjs",
    "deploy/work-controller/model-backend-cli.mjs",
    "deploy/work-controller/model-backend-launch.mjs",
    "deploy/work-controller/model-qualification.mjs",
    "deploy/work-runner/runner-core.mjs",
    "deploy/work-model-proxy/inference-policy.mjs",
    "deploy/work-runner/research-tool.mjs",
    "deploy/work-research-broker/research-service.mjs",
    "deploy/work-research-broker/tool-queue.mjs",
    "deploy/work-research-broker/pipeline.mjs",
    "deploy/work-research-broker/report-finalizer.mjs",
    "deploy/work-research-broker/citation-verifier.mjs",
    "schemas/work-research-report-proposal-v1.schema.json",
    "scripts/lib/secure-files.mjs",
    "scripts/lib/work-contract.mjs",
    "tests/fixtures/agent-comparison/fake-codex-surface.mjs",
)


def _private_new(path: Path, payload: bytes) -> bytes:
    if not isinstance(payload, bytes):
        raise evaluation.OutcomeError("private immutable payload must be bytes")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    if os.name != "nt":
        path.chmod(0o600)
    return payload


def _private_new_json(path: Path, value: dict[str, Any]) -> bytes:
    payload = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    return _private_new(path, payload)


def _private_directory(path: Path) -> Path:
    path.mkdir(mode=0o700)
    if os.name != "nt":
        path.chmod(0o700)
    return path.resolve(strict=True)


def render_research_report(report: dict[str, Any]) -> str:
    try:
        title = base64.b64decode(report["titleBase64"], validate=True).decode("utf-8")
        limitations = base64.b64decode(report["limitationsBase64"], validate=True).decode("utf-8")
        findings = [
            "- " + base64.b64decode(item["statementBase64"], validate=True).decode("utf-8")
            + f" [{len(item['citations'])} citation{'s' if len(item['citations']) != 1 else ''}]"
            for item in report["findings"]
        ]
    except (KeyError, TypeError, ValueError, UnicodeError) as exc:
        raise evaluation.OutcomeError("verified Codex Researcher report is not renderable") from exc
    return f"# {title}\n\n" + "\n".join(findings) + f"\n\nLimitations: {limitations}"


def container_user_args() -> list[str]:
    if os.name == "nt":
        return []
    uid = evaluation.integer(os.getuid(), 0, 2147483647, "container user ID")
    gid = evaluation.integer(os.getgid(), 0, 2147483647, "container group ID")
    return ["--user", f"{uid}:{gid}"]


def comparison_container_identity() -> tuple[int, int]:
    if os.name == "nt":
        return 1000, 1000
    return (
        evaluation.integer(os.getuid(), 0, 2147483647, "comparison user ID"),
        evaluation.integer(os.getgid(), 0, 2147483647, "comparison group ID"),
    )


def private_tmpfs(path: str, *, size: str) -> str:
    if not isinstance(path, str) or not path.startswith("/") or not re.fullmatch(r"(?:[0-9]+|[0-9]+[kmg])", size):
        raise evaluation.OutcomeError("private tmpfs configuration is invalid")
    options = ["rw", "nosuid", "nodev", f"size={size}", "mode=0700"]
    uid, gid = comparison_container_identity()
    options.extend([f"uid={uid}", f"gid={gid}"])
    return f"{path}:{','.join(options)}"


def codex_comparison_limits(environment: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(environment, dict):
        raise evaluation.OutcomeError("Codex comparison environment is invalid")
    return evaluation.exact_fields(environment.get("limits"), {
        "maxIterations", "maxToolCalls", "maxConcurrentSubagents", "maxCpuCores", "maxMemoryMiB",
        "maxDiskBytes", "maxNetworkBytes", "maxFailures", "noProgressLimit", "maxPids",
    }, "Codex comparison environment limits")


def codex_workspace_bytes(environment: dict[str, Any]) -> int:
    limits = codex_comparison_limits(environment)
    disk_bytes = evaluation.integer(limits["maxDiskBytes"], 1048576, 1099511627776, "Codex disk ceiling")
    return disk_bytes // 2


def codex_container_resource_args(environment: dict[str, Any]) -> list[str]:
    limits = codex_comparison_limits(environment)
    cpu_cores = evaluation.integer(limits["maxCpuCores"], 1, 128, "Codex CPU ceiling")
    memory_mib = evaluation.integer(limits["maxMemoryMiB"], 256, 1048576, "Codex memory ceiling")
    scratch_bytes = codex_workspace_bytes(environment)
    admitted_pids = evaluation.integer(limits["maxPids"], 32, 1048576, "Codex PID ceiling")
    subagents = evaluation.integer(limits["maxConcurrentSubagents"], 1, 32, "Codex subagent ceiling")
    pids = min(512, max(96, subagents * 32 + 64))
    if admitted_pids < pids:
        raise evaluation.OutcomeError("Codex PID admission is below Pixel's deterministic worker ceiling")
    return [
        "--pids-limit", str(pids), "--memory", f"{memory_mib}m", "--memory-swap", f"{memory_mib}m",
        "--cpus", str(cpu_cores), "--tmpfs", private_tmpfs("/work", size=str(scratch_bytes)),
    ]


def normalize_created_at(value: Any) -> str:
    if not isinstance(value, str) or "T" not in value:
        raise evaluation.OutcomeError("container creation time is invalid")
    text = value.strip()
    if not text.endswith("Z"):
        raise evaluation.OutcomeError("container creation time must be UTC")
    body = text[:-1]
    if "." in body:
        whole, fraction = body.split(".", 1)
        fraction = re.sub(r"[^0-9].*$", "", fraction)[:6]
        body = f"{whole}.{fraction}" if fraction else whole
    normalized = body + "Z"
    evaluation.timestamp(normalized, "container creation time")
    return normalized


def parse_request_count(metrics_text: Any) -> int | None:
    if not isinstance(metrics_text, str):
        return None
    total = 0.0
    observed = False
    for line in metrics_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name = line.split("{", 1)[0].split(" ", 1)[0]
        if name in REQUEST_COUNTER_NAMES:
            try:
                total += float(line.rsplit(" ", 1)[-1])
            except ValueError:
                return None
            observed = True
    if not observed:
        return None
    if total < 0 or total != int(total):
        return None
    return int(total)


def _workspace_files(root: Path, byte_ceiling: int) -> dict[str, tuple[str, int, bytes | None]]:
    root = root.resolve(strict=True)
    files: dict[str, tuple[str, int, bytes | None]] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            raise evaluation.OutcomeError("codex workspace contains a symbolic link")
        metadata = path.stat()
        if path.is_dir():
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise evaluation.OutcomeError("codex workspace contains a special file")
        if len(files) >= MAX_WORKSPACE_FILES:
            raise evaluation.OutcomeError("codex workspace file ceiling exceeded")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_size != metadata.st_size:
                raise evaluation.OutcomeError("codex workspace file changed during collection")
            digest = hashlib.sha256()
            retained = bytearray() if metadata.st_size <= byte_ceiling else None
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                observed = 0
                while chunk := handle.read(1048576):
                    observed += len(chunk)
                    digest.update(chunk)
                    if retained is not None:
                        retained.extend(chunk)
        finally:
            os.close(descriptor)
        if observed != metadata.st_size:
            raise evaluation.OutcomeError("codex workspace file changed during collection")
        relative = path.relative_to(root).as_posix()
        files[relative] = (digest.hexdigest(), observed, bytes(retained) if retained is not None else None)
    return files


def collect_workspace_delta(baseline: Path, workspace: Path, byte_ceiling: int) -> list[dict[str, Any]]:
    evaluation.integer(byte_ceiling, 1, 1073741824, "artifact byte ceiling")
    before = _workspace_files(baseline, byte_ceiling)
    after = _workspace_files(workspace, byte_ceiling)
    changed = []
    patch_lines: list[str] = []
    changed_payload_bytes = 0
    for relative in sorted(set(before) | set(after)):
        old = before.get(relative)
        new = after.get(relative)
        if old is not None and new is not None and old[:2] == new[:2]:
            continue
        changed_payload_bytes += new[1] if new is not None else 0
        if changed_payload_bytes > byte_ceiling:
            raise evaluation.OutcomeError("codex workspace outputs exceed the artifact ceiling")
        change = "added" if old is None else "deleted" if new is None else "modified"
        changed.append({
            "path": relative, "change": change,
            "beforeSha256": old[0] if old is not None else None,
            "beforeBytes": old[1] if old is not None else None,
            "afterSha256": new[0] if new is not None else None,
            "afterBytes": new[1] if new is not None else None,
        })
        if (old is not None and old[2] is None) or (new is not None and new[2] is None):
            continue
        try:
            old_text = old[2].decode("utf-8") if old is not None else ""
            new_text = new[2].decode("utf-8") if new is not None else ""
        except UnicodeDecodeError:
            continue
        patch_lines.extend(difflib.unified_diff(
            old_text.splitlines(keepends=True), new_text.splitlines(keepends=True),
            fromfile=f"a/{relative}", tofile=f"b/{relative}", lineterm="\n",
        ))
    if not changed:
        return []
    manifest = (json.dumps({
        "schemaVersion": 1, "operation": "pixel-portal-outcome-workspace-delta",
        "changedFiles": changed,
        "boundary": "Private post-run content identity only; this record grants no execution or effect authority.",
    }, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    artifacts = [{"kind": "test-evidence", "relativePath": "workspace-delta.json", "payload": manifest}]
    if patch_lines:
        patch = "".join(patch_lines).encode("utf-8")
        artifacts.append({"kind": "patch", "relativePath": "workspace.patch", "payload": patch})
    if sum(len(item["payload"]) for item in artifacts) > byte_ceiling:
        raise evaluation.OutcomeError("codex workspace evidence exceeds the artifact ceiling")
    return artifacts


def workspace_candidate_identity(root: Path, byte_ceiling: int) -> tuple[str, dict[str, tuple[str, int, bytes | None]]]:
    files = _workspace_files(root, byte_ceiling)
    manifest = [{"path": path, "sha256": value[0], "bytes": value[1]} for path, value in sorted(files.items())]
    return evaluation.sha256(evaluation.canonical(manifest)), files


def immutable_workspace_changed(
    before: dict[str, tuple[str, int, bytes | None]],
    after: dict[str, tuple[str, int, bytes | None]],
    prefixes: list[str],
) -> bool:
    changed = {path for path in set(before) | set(after) if before.get(path, ())[:2] != after.get(path, ())[:2]}
    for path in changed:
        wrapped = f"source/{path}"
        for prefix in prefixes:
            normalized = prefix[:-1] if prefix.endswith("/") else prefix
            if wrapped == normalized or wrapped.startswith(f"{normalized}/"):
                return True
    return False


def load_hardened_codex_config(root: Path) -> list[str]:
    broker_path = root / "deploy" / "frontier-broker" / "broker.py"
    spec = importlib.util.spec_from_file_location("pixel_outcome_frontier_broker", broker_path)
    if spec is None or spec.loader is None:
        raise evaluation.OutcomeError("hardened codex configuration source is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = getattr(module, "HARDENED_CODEX_CONFIG", None)
    if not isinstance(config, list) or not config or any(not isinstance(item, str) for item in config):
        raise evaluation.OutcomeError("hardened codex configuration is invalid")
    return list(config)


def build_codex_script(
    hardened: list[str], *, model_id: str, model_context_window: int, wire_api: str, env_key: str,
    profile: str, capabilities: list[str], source_media_type: str, tool_policy: dict[str, Any],
) -> str:
    if wire_api != "responses":
        raise evaluation.OutcomeError("pinned Codex requires the Responses wire API")
    context_window = evaluation.integer(model_context_window, 1024, 2000000, "Codex model context window")
    auto_compact_token_limit = context_window * 9 // 10
    if profile not in {"assistant", "scout", "builder", "researcher", "data-lab", "controller"}:
        raise evaluation.OutcomeError("codex comparison profile is unsupported")
    if not isinstance(capabilities, list) or any(not isinstance(item, str) for item in capabilities):
        raise evaluation.OutcomeError("codex comparison capabilities are invalid")
    if source_media_type not in outcome_task.MEDIA_TYPES:
        raise evaluation.OutcomeError("codex comparison source media type is unsupported")
    process_execution = "shell" in tool_policy["tools"]
    workspace_write = tool_policy["workspace"] == "disposable-read-write"
    researcher = profile == "researcher"
    if researcher and (
        "public-research" not in tool_policy["tools"]
        or "public-research" not in tool_policy.get("brokeredServices", [])
    ):
        raise evaluation.OutcomeError("Codex Researcher requires Pixel's exact public-research tool and broker service")
    delegated_work = "task" in tool_policy["tools"]
    if delegated_work and tool_policy.get("maximumSubagents") != 1:
        raise evaluation.OutcomeError("Codex comparison delegation requires the exact single-subagent envelope")
    filtered_hardened = []
    skip = False
    for index, item in enumerate(hardened):
        if skip:
            skip = False
            continue
        if item == "-c" and index + 1 < len(hardened) and (
            hardened[index + 1].startswith("features.shell_tool=")
            or hardened[index + 1].startswith("features.multi_agent=")
        ):
            skip = True
            continue
        filtered_hardened.append(item)
    provider = [
        "-c", 'cli_auth_credentials_store="file"',
        "-c", 'model_provider="pixel_outcome"',
        "-c", 'model_providers.pixel_outcome.name="Pixel outcome lane"',
        "-c", f'model_providers.pixel_outcome.base_url="http://{MODEL_ALIAS}:8080/v1"',
        "-c", f'model_providers.pixel_outcome.env_key="{env_key}"',
        "-c", f'model_providers.pixel_outcome.wire_api="{wire_api}"',
        "-c", f'model_catalog_json="{CODEX_MODEL_CATALOG_CONTAINER_PATH}"',
        "-c", f"model_context_window={context_window}",
        "-c", f"model_auto_compact_token_limit={auto_compact_token_limit}",
        "-c", "model_providers.pixel_outcome.requires_openai_auth=false",
        "-c", "model_providers.pixel_outcome.supports_websockets=false",
        "-c", "model_providers.pixel_outcome.request_max_retries=0",
        "-c", "model_providers.pixel_outcome.stream_max_retries=0",
    ]
    research = [
        "-c", f'mcp_servers.pixel_research.url="http://{RESEARCH_MCP_ALIAS}:8081/mcp"',
        "-c", "mcp_servers.pixel_research.enabled=true",
    ] if researcher else []
    argv = [
        "/usr/local/bin/codex", "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules",
        "--strict-config", "--skip-git-repo-check", "--sandbox",
        "workspace-write" if workspace_write else "read-only", "--json",
        *(["--output-schema", CODEX_RESEARCH_OUTPUT_SCHEMA_CONTAINER_PATH] if researcher else []),
        "-m", model_id, *filtered_hardened,
        "-c", f"features.shell_tool={'true' if process_execution else 'false'}",
        "-c", f"features.multi_agent={'true' if delegated_work else 'false'}",
        *provider, *research, "-",
    ]
    quoted = " ".join(shlex.quote(item) for item in argv)
    prepare = "mkdir -p /work/.codex"
    return f"{prepare} && cd /work/source && exec {quoted}"


def build_codex_model_catalog(root: Path, *, model_id: str, context_window: int) -> bytes:
    if not isinstance(model_id, str) or not CODEX_MODEL_RE.fullmatch(model_id):
        raise evaluation.OutcomeError("Codex catalog model identity is invalid")
    window = evaluation.integer(context_window, 1024, 2000000, "Codex catalog context window")
    prompt_path = Path(root) / CODEX_PINNED_PROMPT_RELATIVE
    try:
        prompt_payload = prompt_path.read_bytes()
    except OSError as error:
        raise evaluation.OutcomeError("pinned Codex base instructions are unavailable") from error
    if evaluation.sha256(prompt_payload) != CODEX_PINNED_PROMPT_SHA256:
        raise evaluation.OutcomeError("pinned Codex base instructions differ from Codex 0.147.0")
    try:
        prompt = prompt_payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise evaluation.OutcomeError("pinned Codex base instructions are not UTF-8") from error
    if not prompt.strip():
        raise evaluation.OutcomeError("pinned Codex base instructions are empty")
    model = {
        "slug": model_id,
        "display_name": model_id,
        "supported_reasoning_levels": [],
        "shell_type": "default",
        "visibility": "none",
        "supported_in_api": True,
        "priority": 99,
        "additional_speed_tiers": [],
        "service_tiers": [],
        "base_instructions": prompt,
        "include_skills_usage_instructions": False,
        "include_plugin_usage_instructions": False,
        "include_apps_usage_instructions": False,
        "supports_reasoning_summary_parameter": True,
        "default_reasoning_summary": "auto",
        "support_verbosity": False,
        "apply_patch_tool_type": "freeform",
        "web_search_tool_type": "text",
        "truncation_policy": {"mode": "bytes", "limit": 10000},
        "supports_parallel_tool_calls": False,
        "supports_image_detail_original": False,
        "context_window": window,
        "max_context_window": window,
        "auto_compact_token_limit": window * 9 // 10,
        "effective_context_window_percent": 95,
        "experimental_supported_tools": [],
        "input_modalities": ["text", "image"],
        "supports_search_tool": False,
        "use_responses_lite": False,
        "multi_agent_version": "v1",
    }
    return (json.dumps({"models": [model]}, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def last_agent_message(transcript: str) -> str | None:
    message = None
    for line in transcript.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        item = event.get("item") if isinstance(event, dict) else None
        if (
            isinstance(event, dict) and event.get("type") == "item.completed"
            and isinstance(item, dict) and item.get("type") == "agent_message"
            and isinstance(item.get("text"), str)
        ):
            message = item["text"]
    return message


class DockerSystem:
    def __init__(
        self, *, root: Path, codex_image: str, docker_path: str = "docker",
        boundary_image: str = "pixel-work-runner-candidate:4.1.0",
        health_deadline_seconds: int | None = None, env_key: str = "PIXEL_OUTCOME_KEY", node_path: str = "node",
        pixel_system_config_path: Path | None = None,
    ):
        if not isinstance(codex_image, str) or not codex_image.strip():
            raise evaluation.OutcomeError("codex runner image is required")
        self.root = Path(root)
        self.codex_image = codex_image
        if not isinstance(boundary_image, str) or not boundary_image.strip():
            raise evaluation.OutcomeError("inference boundary image is required")
        self.boundary_image = boundary_image
        self.docker_path = docker_path
        self.env_key = env_key
        self.node_path = node_path
        self.pixel_system_config_path = Path(pixel_system_config_path).resolve() if pixel_system_config_path is not None else None
        self.pixel_runtime_root: Path | None = None
        self.pixel_environment_template_sha256: str | None = None
        self.pixel_model_backend_template_sha256: str | None = None
        if self.pixel_system_config_path is not None:
            if health_deadline_seconds is not None:
                raise evaluation.OutcomeError(
                    "health deadline is derived from the Pixel model backend template when a Pixel system configuration is bound"
                )
            pixel_configuration, _pixel_raw = evaluation.read_json(
                self.pixel_system_config_path, "private Pixel system configuration", private=True,
            )
            runtime_root = pixel_configuration.get("runtimeRoot") if isinstance(pixel_configuration, dict) else None
            if not isinstance(runtime_root, str) or not Path(runtime_root).is_absolute():
                raise evaluation.OutcomeError("Pixel system runtime root is unavailable to the Codex Researcher authority")
            self.pixel_runtime_root = Path(runtime_root).resolve(strict=True)
            environment_template_path = pixel_configuration.get("environmentTemplatePath")
            if not isinstance(environment_template_path, str) or not Path(environment_template_path).is_absolute():
                raise evaluation.OutcomeError("Pixel environment template is unavailable to the Codex Researcher authority")
            environment_template, _environment_raw = evaluation.read_json(
                Path(environment_template_path), "private Pixel environment template", private=True,
            )
            self.pixel_environment_template_sha256 = evaluation.sha256(evaluation.canonical(environment_template))
            model_backend_template_path = pixel_configuration.get("modelBackendTemplatePath")
            if not isinstance(model_backend_template_path, str) or not Path(model_backend_template_path).is_absolute():
                raise evaluation.OutcomeError("Pixel model backend template is unavailable to the Codex comparison harness")
            model_backend_template, _model_backend_raw = evaluation.read_json(
                Path(model_backend_template_path), "private Pixel model backend template", private=True,
            )
            if (
                model_backend_template.get("$schema")
                != "https://osmantic.com/pixel/schemas/work-model-backend-config-v1.schema.json"
                or model_backend_template.get("schemaVersion") != 1
            ):
                raise evaluation.OutcomeError("Pixel model backend template contract is invalid")
            readiness = evaluation.exact_fields(
                model_backend_template.get("readiness"),
                {"startupTimeoutSeconds", "probeIntervalMilliseconds"},
                "Pixel model backend readiness",
            )
            self.health_deadline_seconds = evaluation.integer(
                readiness["startupTimeoutSeconds"], 10, 3600,
                "Pixel model backend readiness startup timeout",
            )
            evaluation.integer(
                readiness["probeIntervalMilliseconds"], 100, 60000,
                "Pixel model backend readiness probe interval",
            )
            self.pixel_model_backend_template_sha256 = evaluation.sha256(
                evaluation.canonical(model_backend_template)
            )
        else:
            self.health_deadline_seconds = evaluation.integer(
                180 if health_deadline_seconds is None else health_deadline_seconds,
                10, 3600, "health deadline",
            )
        self.hardened = load_hardened_codex_config(self.root)
        self._boundary_receipts: dict[str, Path] = {}
        self._boundary_run_ids: dict[str, str] = {}

    def codex_harness_contract_sha256(self) -> str:
        files = []
        for relative in CODEX_HARNESS_FILES:
            payload = (self.root / relative).read_bytes()
            files.append({"relativePath": relative, "bytes": len(payload), "sha256": evaluation.sha256(payload)})
        image_id = self.image_id(self.codex_image)
        if not image_id.startswith("sha256:"):
            raise evaluation.OutcomeError("Codex runner image does not resolve to an immutable image identity")
        evaluation.valid_hash(image_id[7:], "Codex runner image identity")
        boundary_image_id = self.image_id(self.boundary_image)
        if not boundary_image_id.startswith("sha256:"):
            raise evaluation.OutcomeError("inference-boundary image does not resolve to an immutable image identity")
        evaluation.valid_hash(boundary_image_id[7:], "inference-boundary image identity")
        executable_sha256 = self.executable_sha256(self.codex_image, "/usr/local/bin/codex")
        toolchain_sha256 = self.codex_comparison_toolchain_sha256()
        workspace_copier_sha256 = self.executable_sha256(
            self.codex_image, "/opt/pixel/scripts/codex_comparison_workspace.py",
        )
        boundary_script_sha256 = self.executable_sha256(
            self.boundary_image, "/opt/pixel/deploy/agent-comparison/inference-boundary.mjs",
        )
        inference_policy_sha256 = self.executable_sha256(
            self.boundary_image, "/opt/pixel/deploy/work-model-proxy/inference-policy.mjs",
        )
        research_mcp_sha256 = self.executable_sha256(
            self.boundary_image, "/opt/pixel/deploy/agent-comparison/research-mcp-server.mjs",
        )
        research_tool_sha256 = self.executable_sha256(
            self.boundary_image, "/opt/pixel/deploy/work-runner/research-tool.mjs",
        )
        embedded = {
            "deploy/agent-comparison/inference-boundary.mjs": boundary_script_sha256,
            "deploy/work-model-proxy/inference-policy.mjs": inference_policy_sha256,
            "deploy/agent-comparison/research-mcp-server.mjs": research_mcp_sha256,
            "deploy/work-runner/research-tool.mjs": research_tool_sha256,
        }
        expected = {item["relativePath"]: item["sha256"] for item in files}
        if any(embedded[relative] != expected[relative] for relative in embedded):
            raise evaluation.OutcomeError("inference-boundary image code differs from the exact reviewed source")
        if workspace_copier_sha256 != expected["scripts/codex_comparison_workspace.py"]:
            raise evaluation.OutcomeError("Codex comparison image workspace code differs from the exact reviewed source")
        return evaluation.sha256(evaluation.canonical({
            "schemaVersion": 1,
            "operation": "pixel-portal-outcome-codex-harness-contract",
            "codexRunnerImageId": image_id,
            "codexExecutableSha256": executable_sha256,
            "codexComparisonToolchainSha256": toolchain_sha256,
            "codexComparisonWorkspaceCopierSha256": workspace_copier_sha256,
            "inferenceBoundaryImageId": boundary_image_id,
            "inferenceBoundaryScriptSha256": boundary_script_sha256,
            "inferencePolicyScriptSha256": inference_policy_sha256,
            "researchMcpScriptSha256": research_mcp_sha256,
            "researchToolScriptSha256": research_tool_sha256,
            "healthDeadlineSeconds": self.health_deadline_seconds,
            "pixelModelBackendTemplateSha256": self.pixel_model_backend_template_sha256,
            "hardenedArguments": self.hardened,
            "files": files,
        }))

    def _docker(self, args: list[str], *, timeout: int, input_bytes: bytes | None = None) -> str:
        result = subprocess.run(
            [self.docker_path, *args], input=input_bytes,
            capture_output=True, timeout=timeout,
        )
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace")[-400:]
            raise evaluation.OutcomeError(f"docker {args[0]} failed: {detail}")
        return result.stdout.decode("utf-8", errors="replace")

    def image_id(self, reference: str) -> str:
        return self._docker(["image", "inspect", "-f", "{{.Id}}", reference], timeout=30).strip()

    def executable_sha256(self, reference: str, path: str) -> str:
        if EXECUTABLE_PATH_RE.fullmatch(path) is None:
            raise evaluation.OutcomeError("runtime executable path is invalid")
        output = self._docker(
            [
                "run", "--rm", "--pull", "never", "--network", "none", "--read-only",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                "--pids-limit", "32", "--memory", "128m", "--memory-swap", "128m",
                "--entrypoint", "/bin/sh", reference, "-c", f"sha256sum {shlex.quote(path)}",
            ],
            timeout=120,
        )
        return evaluation.valid_hash(output.split()[0] if output.split() else "", "runtime executable digest")

    def codex_comparison_toolchain_sha256(self) -> str:
        probe = (
            "import debugpy,duckdb,json,os,polars,sqlite3,subprocess,sys;"
            "os.makedirs('/home/pixel/.codex',mode=0o700,exist_ok=True);"
            "line=lambda command:subprocess.check_output(command,text=True,stderr=subprocess.STDOUT).splitlines()[0];"
            "value={'codex':line(['codex','--version']),'node':line(['node','--version']),"
            "'python':'.'.join(map(str,sys.version_info[:3])),'gdb':line(['gdb','--version']),"
            "'pyright':line(['pyright','--version']),'debugpy':debugpy.__version__,"
            "'sqlite':sqlite3.sqlite_version,'duckdb':duckdb.__version__,'polars':polars.__version__};"
            "print(json.dumps(value,sort_keys=True,separators=(',',':')))"
        )
        output = self._docker(
            [
                "run", "--rm", "--pull", "never", "--network", "none", "--read-only",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                "--pids-limit", "96", "--memory", "512m", "--memory-swap", "512m",
                "--user", "1000:1000", "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m,mode=0700,uid=1000,gid=1000",
                "--tmpfs", "/home/pixel:rw,nosuid,nodev,size=16m,mode=0700,uid=1000,gid=1000",
                "--env", "HOME=/home/pixel", "--env", "CODEX_HOME=/home/pixel/.codex",
                "--entrypoint", "/bin/sh", self.codex_image, "-c", f"python -c {shlex.quote(probe)}",
            ],
            timeout=120,
        ).strip()
        try:
            identity = json.loads(output)
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise evaluation.OutcomeError("Codex comparison toolchain probe is invalid") from exc
        if identity != CODEX_COMPARISON_TOOLCHAIN:
            raise evaluation.OutcomeError("Codex comparison runner lacks the exact backend-neutral toolchain")
        return evaluation.sha256(evaluation.canonical(identity))

    def codex_workspace_volume_create(self, network: str, environment: dict[str, Any]) -> tuple[str, int]:
        if NAME_RE.fullmatch(network or "") is None:
            raise evaluation.OutcomeError("Codex workspace network identity is invalid")
        volume = f"{network}-codex-workspace"
        if NAME_RE.fullmatch(volume) is None:
            raise evaluation.OutcomeError("Codex workspace volume identity is invalid")
        maximum_bytes = codex_workspace_bytes(environment)
        uid, gid = comparison_container_identity()
        option = f"size={maximum_bytes},uid={uid},gid={gid},mode=0700,nosuid,nodev"
        output = self._docker([
            "volume", "create", "--driver", "local", "--opt", "type=tmpfs", "--opt", "device=tmpfs",
            "--opt", f"o={option}", "--label", "com.osmantic.pixel.work-role=codex-comparison-workspace",
            volume,
        ], timeout=30).strip()
        if output != volume:
            raise evaluation.OutcomeError("Codex workspace volume creation returned a different identity")
        return volume, maximum_bytes

    def codex_workspace_keeper_start(self, network: str, volume: str) -> str:
        keeper = f"{network}-codex-workspace-keeper"
        if NAME_RE.fullmatch(network or "") is None or NAME_RE.fullmatch(volume or "") is None or NAME_RE.fullmatch(keeper) is None:
            raise evaluation.OutcomeError("Codex workspace keeper identity is invalid")
        container_id = self._docker([
            "run", "-d", "--name", keeper, "--pull", "never", "--network", "none", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", "--pids-limit", "16",
            "--memory", "64m", "--memory-swap", "64m", *container_user_args(),
            "--mount", f"type=volume,source={volume},target=/workspace",
            "--entrypoint", "/usr/bin/tail", self.codex_image, "-f", "/dev/null",
        ], timeout=30).strip()
        evaluation.valid_hash(container_id, "Codex workspace keeper container identity")
        return keeper

    def codex_workspace_copy(
        self, operation: str, volume: str, host_path: Path, maximum_bytes: int,
    ) -> dict[str, Any]:
        if operation not in {"init", "export"} or NAME_RE.fullmatch(volume or "") is None:
            raise evaluation.OutcomeError("Codex workspace copy identity is invalid")
        host_path = host_path.resolve(strict=True)
        if not host_path.is_dir() or host_path.is_symlink():
            raise evaluation.OutcomeError("Codex workspace host path is invalid")
        source_mount = (
            ["--mount", f"type=bind,source={host_path},target=/source,readonly", "--mount", f"type=volume,source={volume},target=/workspace"]
            if operation == "init" else
            ["--mount", f"type=volume,source={volume},target=/source,readonly", "--mount", f"type=bind,source={host_path},target=/workspace"]
        )
        output = self._docker([
            "run", "--rm", "--pull", "never", "--network", "none", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", "--pids-limit", "32",
            "--memory", "512m", "--memory-swap", "512m", *container_user_args(),
            "--tmpfs", private_tmpfs("/tmp", size="64m"), *source_mount,
            "--entrypoint", "/usr/bin/python3", self.codex_image,
            "/opt/pixel/scripts/codex_comparison_workspace.py", operation,
            "/source", "/workspace", str(maximum_bytes),
        ], timeout=300).strip()
        try:
            receipt = json.loads(output)
        except json.JSONDecodeError as exc:
            raise evaluation.OutcomeError("Codex workspace copy receipt is invalid") from exc
        if (
            not isinstance(receipt, dict) or set(receipt) != {"operation", "entries", "bytes", "sha256"}
            or receipt["operation"] != f"codex-comparison-workspace-{operation}"
            or not isinstance(receipt["entries"], int) or receipt["entries"] < 0 or receipt["entries"] > 100000
            or not isinstance(receipt["bytes"], int) or receipt["bytes"] < 0 or receipt["bytes"] > maximum_bytes
        ):
            raise evaluation.OutcomeError("Codex workspace copy receipt differs from its exact contract")
        evaluation.valid_hash(receipt.get("sha256"), "Codex workspace tree digest")
        return receipt

    def network_create(self, run_id: str) -> str:
        match = RUN_ID_SUFFIX_RE.search(run_id or "")
        if match is None:
            raise evaluation.OutcomeError("run identity cannot name an isolated network")
        name = f"pixel-outcome-{match.group(0)}"
        if NAME_RE.fullmatch(name) is None:
            raise evaluation.OutcomeError("isolated network name is invalid")
        self._docker(["network", "create", "--internal", name], timeout=60)
        try:
            self._docker(["network", "create", "--internal", f"{name}-backend"], timeout=60)
        except Exception:
            subprocess.run([self.docker_path, "network", "rm", name], capture_output=True, timeout=60)
            raise
        return name

    def model_container_start(
        self, network: str, contract: dict[str, Any], arguments: list[str], artifact_path: Path,
    ) -> str:
        name = f"{network}-model"
        resources = contract["runtime"]["resources"]
        gpu_args = ["--gpus", f"driver=nvidia,count={resources['acceleratorCount']}"] if resources["acceleratorClass"] == "nvidia" else []
        artifact_kind = contract["artifact"]["kind"]
        implementation = contract["runtime"]["implementation"]
        if artifact_kind == "single-file" and implementation == "llama.cpp":
            mount_target = "/models/model.gguf"
        elif artifact_kind == "directory-manifest" and implementation == "vllm":
            mount_target = "/models/model"
        else:
            raise evaluation.OutcomeError("model artifact and runtime implementation are incompatible")
        if not artifact_path.is_absolute():
            raise evaluation.OutcomeError("model artifact mount path must be absolute")
        args = [
            "run", "-d", "--name", name, "--network", f"{network}-backend", "--network-alias", BACKEND_ALIAS,
            *gpu_args,
            "--memory", f"{resources['memoryMiB']}m", "--memory-swap", f"{resources['memoryMiB']}m",
            "--cpus", str(resources["cpuCores"]), "--pids-limit", str(resources["pidsLimit"]),
            "--ipc", "private", "--shm-size", f"{resources['sharedMemoryMiB']}m",
            "--restart", "no", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true",
            "--tmpfs", f"/tmp:rw,nosuid,nodev,noexec,size={resources['tmpfsMiB']}m,mode=1777",
            "--tmpfs", f"/cache:rw,nosuid,nodev,exec,size={resources['cacheMiB']}m,mode=0700",
            "--mount", f"type=bind,source={artifact_path},target={mount_target},readonly",
            contract["runtime"]["imageDigest"], *arguments,
        ]
        self._docker(args, timeout=120)
        return name

    def inference_boundary_start(
        self, network: str, run_id: str, model_contract: dict[str, Any],
        inference_contract: dict[str, Any], admission: dict[str, Any], run_dir: Path,
    ) -> str:
        if (
            model_contract["runtime"]["implementation"] != "vllm"
            or inference_contract["request"]["wireApi"] != "openai-chat-completions"
        ):
            raise evaluation.OutcomeError("formal exact inference boundary currently requires vLLM Chat Completions")
        policy = outcome_task.inference_policy_value(inference_contract)
        expected = outcome_task.inference_policy_sha256(inference_contract)
        if inference_contract["request"]["requestFieldPolicySha256"] != expected:
            raise evaluation.OutcomeError("inference boundary policy does not match its admitted digest")
        run_dir = Path(run_dir)
        if not run_dir.is_absolute():
            raise evaluation.OutcomeError("inference boundary run directory must be absolute")
        private = run_dir / "inference-boundary-private"
        receipts = private / "receipts"
        private.mkdir(mode=0o700, exist_ok=False)
        receipts.mkdir(mode=0o700)
        config_path = private / "config.json"
        config = {
            "schemaVersion": 1, "runId": run_id, "modelId": model_contract["modelId"],
            "backendOrigin": f"http://{BACKEND_ALIAS}:8080", "listenHost": "0.0.0.0", "listenPort": 8080,
            "receiptPath": "/run/pixel-outcome-output/inference-boundary-receipt.json",
            "maxRequestBytes": max(1024, min(268435456, admission["budgets"]["artifactBytes"])),
            "maxResponseBytes": max(1024, min(1073741824, admission["budgets"]["artifactBytes"])),
            "maxRequestSeconds": admission["budgets"]["wallTimeSeconds"],
            "maxModelRequests": admission["budgets"]["modelRequests"],
            "maxInputTokens": admission["budgets"]["inputTokens"],
            "maxOutputTokens": admission["budgets"]["outputTokens"],
            "ingressWireApi": "openai-responses", "inference": policy,
        }
        descriptor = os.open(
            config_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(config, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
        name = f"{network}-inference"
        args = [
            "run", "-d", "--pull", "never", "--name", name, "--network", network,
            "--network-alias", MODEL_ALIAS, "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true", "--pids-limit", "64", "--memory", "256m",
            "--memory-swap", "256m", "--cpus", "1", "--ipc", "none", "--restart", "no",
            "--log-driver", "none", "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=32m,mode=1777",
            *container_user_args(),
            "--mount", f"type=bind,source={config_path},target=/run/pixel-outcome/config.json,readonly",
            "--mount", f"type=bind,source={receipts},target=/run/pixel-outcome-output",
            "--entrypoint", "/opt/node/bin/node", self.boundary_image,
            "/opt/pixel/deploy/agent-comparison/inference-boundary.mjs", "/run/pixel-outcome/config.json",
        ]
        self._docker(args, timeout=120)
        self._docker(["network", "connect", f"{network}-backend", name], timeout=60)
        self._boundary_receipts[name] = receipts / "inference-boundary-receipt.json"
        self._boundary_run_ids[name] = run_id
        return name

    def wait_inference_boundary(self, container: str) -> None:
        script = "fetch('http://127.0.0.1:8080/healthz').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"
        deadline = time.monotonic() + self.health_deadline_seconds
        while time.monotonic() < deadline:
            probe = subprocess.run(
                [self.docker_path, "exec", container, "/opt/node/bin/node", "-e", script],
                capture_output=True, timeout=30,
            )
            if probe.returncode == 0:
                return
            time.sleep(1)
        raise evaluation.OutcomeError("exact inference boundary never became healthy")

    def inference_boundary_receipt(self, container: str, expected_policy_sha256: str) -> dict[str, Any]:
        path = self._boundary_receipts.get(container)
        run_id = self._boundary_run_ids.get(container)
        if path is None or run_id is None:
            raise evaluation.OutcomeError("inference boundary receipt path is unavailable")
        value, _raw = evaluation.read_json(path, "private inference boundary receipt", private=True)
        value = evaluation.exact_fields(value, {
            "schemaVersion", "runId", "inferencePolicySha256", "requests", "inputTokens", "outputTokens", "deniedRequests",
            "backendFailures", "responseBytes", "backendResponseBytes", "active", "lastFailureCode", "contentStored",
            "ingressWireApi", "backendWireApi", "adapter",
            "credentialsForwarded", "arbitraryNetwork", "externalEffects",
        }, "inference boundary receipt")
        if (
            value["schemaVersion"] != 1 or value["runId"] != run_id
            or value["inferencePolicySha256"] != expected_policy_sha256
            or value["active"] is not False or value["contentStored"] is not False
            or value["credentialsForwarded"] is not False or value["arbitraryNetwork"] is not False
            or value["externalEffects"] is not False or value["backendFailures"] != 0
            or value["lastFailureCode"] is not None
            or value["ingressWireApi"] != "openai-responses"
            or value["backendWireApi"] != "openai-chat-completions"
            or value["adapter"] != "responses-to-chat-v2"
        ):
            raise evaluation.OutcomeError("inference boundary receipt differs from the exact safe policy")
        for key, maximum in (("requests", 10000000), ("deniedRequests", 10000000), ("responseBytes", 1073741824), ("backendResponseBytes", 1073741824)):
            evaluation.integer(value[key], 0, maximum, f"inference boundary {key}")
        evaluation.integer(value["inputTokens"], 0, 1000000000000, "inference boundary inputTokens")
        evaluation.integer(value["outputTokens"], 0, 1000000000000, "inference boundary outputTokens")
        if value["requests"] < 1:
            raise evaluation.OutcomeError("inference boundary did not observe a real model request")
        return value

    def wait_healthy(self, container: str) -> None:
        deadline = time.monotonic() + self.health_deadline_seconds
        while time.monotonic() < deadline:
            probe = subprocess.run(
                [self.docker_path, "exec", container, "curl", "-sf", "http://localhost:8080/health"],
                capture_output=True, timeout=30,
            )
            if probe.returncode == 0:
                return
            time.sleep(2)
        raise evaluation.OutcomeError("model container never became healthy")

    def container_id(self, container: str) -> str:
        return self._docker(["inspect", "-f", "{{.Id}}", container], timeout=30).strip()

    def container_created_at(self, container: str) -> str:
        return normalize_created_at(self._docker(["inspect", "-f", "{{.Created}}", container], timeout=30).strip())

    def server_get(self, container: str, path: str) -> Any:
        payload = self._docker(["exec", container, "curl", "-s", f"http://localhost:8080{path}"], timeout=60)
        return evaluation.parse_json(payload.encode("utf-8"), f"model server {path}")

    def server_get_text(self, container: str, path: str) -> str:
        return self._docker(["exec", container, "curl", "-s", f"http://localhost:8080{path}"], timeout=60)

    def server_request_count(self, container: str) -> int | None:
        probe = subprocess.run(
            [self.docker_path, "exec", container, "curl", "-sf", "http://localhost:8080/metrics"],
            capture_output=True, timeout=60,
        )
        if probe.returncode != 0:
            return None
        return parse_request_count(probe.stdout.decode("utf-8", errors="replace"))

    def runtime_control(self, *, container: str, run_id: str, condition: str) -> dict[str, Any]:
        return runtime_control.execute(
            docker_path=self.docker_path, container=container, run_id=run_id, condition=condition,
        )

    def _run_verifier_check(
        self, *, image_digest: str, candidate_root: Path, check: dict[str, Any],
        environment: dict[str, Any], container_name: str,
    ) -> dict[str, Any]:
        limits = environment["limits"]
        started = time.monotonic()
        command = [
            self.docker_path, "run", "--rm", "--name", container_name, "--network", "none",
            "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
            "--pids-limit", str(limits["maxPids"]), "--memory", f"{limits['maxMemoryMiB']}m",
            "--memory-swap", f"{limits['maxMemoryMiB']}m", "--cpus", str(limits["maxCpuCores"]),
            "--ipc", "none", "--restart", "no", "--log-driver", "none",
            "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777",
            "--mount", f"type=bind,source={candidate_root},target=/workspace",
            "--workdir", f"/workspace/{check['workingDirectory']}",
            "--env", "HOME=/tmp/home", "--env", "LANG=C.UTF-8", "--env", "TZ=UTC",
        ]
        if os.name != "nt":
            command.extend(["--user", f"{os.getuid()}:{os.getgid()}"])
        command.extend(["--entrypoint", check["argv"][0], image_digest, *check["argv"][1:]])
        timed_out = False
        spawn_failed = False
        exit_code: int | None = None
        signal_name: str | None = None
        stdout = b""
        stderr = b""
        try:
            result = subprocess.run(command, capture_output=True, timeout=check["timeoutSeconds"])
            exit_code = result.returncode if 0 <= result.returncode <= 255 else None
            if result.returncode < 0:
                try:
                    signal_name = signal.Signals(-result.returncode).name
                except ValueError:
                    signal_name = f"SIGUNKNOWN{-result.returncode}"
            stdout, stderr = result.stdout, result.stderr
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout = exc.stdout or b""
            stderr = exc.stderr or b""
        except OSError:
            spawn_failed = True
        finally:
            subprocess.run([self.docker_path, "container", "rm", "--force", container_name], capture_output=True, timeout=30)
        duration_ms = int((time.monotonic() - started) * 1000)
        aggregate = len(stdout) + len(stderr)
        output_limit_exceeded = aggregate > check["maxOutputBytes"]
        inspect = subprocess.run(
            [self.docker_path, "container", "inspect", container_name], capture_output=True, timeout=30,
        )
        cleanup_verified = inspect.returncode != 0
        status = (
            "pass" if not timed_out and not spawn_failed and not output_limit_exceeded
            and exit_code == 0 and signal_name is None and cleanup_verified else "fail"
        )
        return {
            "id": check["id"], "kind": "command", "criterionIndexes": list(check["criterionIndexes"]),
            "status": status, "runtimeMilliseconds": duration_ms, "exitCode": exit_code, "signal": signal_name,
            "timedOut": timed_out, "outputLimitExceeded": output_limit_exceeded, "spawnFailed": spawn_failed,
            "stdoutBytes": len(stdout), "stdoutSha256": evaluation.sha256(stdout),
            "stderrBytes": len(stderr), "stderrSha256": evaluation.sha256(stderr),
            "cleanupVerified": cleanup_verified,
        }

    def verify_workspace(
        self, *, baseline: Path, workspace: Path, definition: dict[str, Any],
        environment: dict[str, Any], admission: dict[str, Any], run_dir: Path, run_id: str,
    ) -> dict[str, Any] | None:
        verification = definition["workspaceVerification"]
        if verification is None:
            return None
        verifier_environment = environment["verifier"]
        image_digest = verifier_environment["imageDigest"]
        if self.image_id(image_digest) != image_digest:
            raise evaluation.OutcomeError("independent verifier image differs from the admitted digest")
        run_match = RUN_ID_SUFFIX_RE.search(run_id or "")
        if run_match is None:
            raise evaluation.OutcomeError("independent verifier requires a valid unique run identity")
        run_suffix = run_match.group(0)
        byte_ceiling = admission["budgets"]["artifactBytes"]
        _baseline_sha, before = workspace_candidate_identity(baseline, byte_ceiling)
        candidate_sha, after = workspace_candidate_identity(workspace, byte_ceiling)
        immutable_changed = immutable_workspace_changed(before, after, verification["immutablePathPrefixes"])
        changed = [path for path in set(before) | set(after) if before.get(path, ())[:2] != after.get(path, ())[:2]]
        changed_bytes = sum(after[path][1] for path in changed if path in after)
        checks: list[dict[str, Any]] = []
        total_runtime = 0
        total_output = 0
        for index, check in enumerate(verification["checks"]):
            if check["kind"] == "patch-integrity":
                checks.append({
                    "id": check["id"], "kind": "patch-integrity", "criterionIndexes": list(check["criterionIndexes"]),
                    "status": "fail" if immutable_changed else "pass", "changes": len(changed),
                    "files": len(after), "bytes": changed_bytes, "cleanupVerified": True,
                })
                continue
            check_root = Path(tempfile.mkdtemp(prefix=f"verify-{index:02d}-", dir=run_dir))
            if os.name != "nt":
                check_root.chmod(0o700)
            try:
                shutil.copytree(workspace, check_root / "source", symlinks=False)
                result = self._run_verifier_check(
                    image_digest=image_digest, candidate_root=check_root, check=check,
                    environment=environment, container_name=f"pixel-outcome-v-{run_suffix}-{index:02d}",
                )
                total_runtime += result["runtimeMilliseconds"]
                total_output += result["stdoutBytes"] + result["stderrBytes"]
                checks.append(result)
            finally:
                shutil.rmtree(check_root, ignore_errors=True)
        criteria = []
        for index, _criterion in enumerate(definition["acceptanceCriteria"]):
            relevant = [check for check in checks if index in check["criterionIndexes"]]
            criteria.append({
                "index": index, "status": "pass" if relevant and all(check["status"] == "pass" for check in relevant) else "fail",
                "checkIds": [check["id"] for check in relevant],
            })
        aggregate_within_bounds = total_runtime <= verification["maxRuntimeSeconds"] * 1000 and total_output <= verification["maxOutputBytes"]
        cleanup_verified = all(check["cleanupVerified"] for check in checks)
        return {
            "schemaVersion": 1, "format": "pixel-neutral-independent-verification-v1",
            "sourceSnapshotSha256": admission["bindings"]["sourceSnapshotSha256"],
            "candidateSha256": candidate_sha,
            "status": "pass" if criteria and all(item["status"] == "pass" for item in criteria) and aggregate_within_bounds and cleanup_verified else "fail",
            "checks": checks, "criteria": criteria, "network": "none", "workerSelectedChecks": False,
            "externalEffects": False, "immutablePathViolation": immutable_changed,
            "aggregateRuntimeMilliseconds": total_runtime, "aggregateOutputBytes": total_output,
            "aggregateWithinBounds": aggregate_within_bounds, "cleanupVerified": cleanup_verified,
            "boundary": INDEPENDENT_VERIFICATION_BOUNDARY,
        }

    def _wait_private_file(self, path: Path, *, process: subprocess.Popen[bytes] | None, label: str, timeout: int) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if path.is_file():
                return
            if process is not None and process.poll() is not None:
                _stdout, stderr = process.communicate(timeout=5)
                detail = stderr.decode("utf-8", errors="replace")[-400:]
                raise evaluation.OutcomeError(f"{label} stopped before readiness: {detail}")
            time.sleep(0.025)
        raise evaluation.OutcomeError(f"{label} did not become ready before its deadline")

    def research_mcp_start(
        self, network: str, run_id: str, queue_root: Path, maximum_calls: int,
        timeout_milliseconds: int, lifecycle_root: Path,
    ) -> tuple[str, Path]:
        maximum_calls = evaluation.integer(maximum_calls, 1, 200, "research MCP call ceiling")
        timeout_milliseconds = evaluation.integer(timeout_milliseconds, 1000, 180000, "research MCP timeout")
        queue_root = Path(queue_root).resolve(strict=True)
        lifecycle_root = Path(lifecycle_root).resolve(strict=True)
        output_root = _private_directory(lifecycle_root / "mcp-output")
        config_path = lifecycle_root / "mcp-config.json"
        _private_new_json(config_path, {
            "schemaVersion": 1, "operation": "pixel-outcome-research-mcp", "runId": run_id,
            "queueRoot": "/run/pixel/research", "listenHost": "0.0.0.0", "listenPort": 8081,
            "maxCalls": maximum_calls, "timeoutMilliseconds": timeout_milliseconds,
            "readyPath": "/run/pixel-research-output/ready.json",
            "receiptPath": "/run/pixel-research-output/receipt.json", "boundary": RESEARCH_MCP_CONFIG_BOUNDARY,
        })
        name = f"{network}-research-mcp"
        self._docker([
            "run", "-d", "--pull", "never", "--name", name, "--network", network,
            "--network-alias", RESEARCH_MCP_ALIAS, "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true", "--pids-limit", "64", "--memory", "256m",
            "--memory-swap", "256m", "--cpus", "1", "--ipc", "none", "--restart", "no",
            "--log-driver", "none", "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=16m,mode=1777",
            *container_user_args(),
            "--mount", f"type=bind,source={queue_root},target=/run/pixel/research",
            "--mount", f"type=bind,source={config_path},target=/run/pixel-research/config.json,readonly",
            "--mount", f"type=bind,source={output_root},target=/run/pixel-research-output",
            "--entrypoint", "/opt/node/bin/node", self.boundary_image,
            "/opt/pixel/deploy/agent-comparison/research-mcp-server.mjs", "/run/pixel-research/config.json",
        ], timeout=120)
        ready_path = output_root / "ready.json"
        self._wait_private_file(ready_path, process=None, label="Codex Research MCP", timeout=self.health_deadline_seconds)
        script = "fetch('http://127.0.0.1:8081/healthz').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"
        deadline = time.monotonic() + self.health_deadline_seconds
        while time.monotonic() < deadline:
            probe = subprocess.run(
                [self.docker_path, "exec", name, "/opt/node/bin/node", "-e", script],
                capture_output=True, timeout=30,
            )
            if probe.returncode == 0:
                return name, output_root / "receipt.json"
            time.sleep(0.25)
        raise evaluation.OutcomeError("Codex Research MCP never became healthy")

    def research_mcp_stop(self, container: str, receipt_path: Path, run_id: str) -> tuple[dict[str, Any], bytes]:
        stopped = subprocess.run(
            [self.docker_path, "stop", "--time", "15", container], capture_output=True, timeout=30,
        )
        if stopped.returncode != 0:
            detail = stopped.stderr.decode("utf-8", errors="replace")[-400:]
            raise evaluation.OutcomeError(f"Codex Research MCP did not stop cleanly: {detail}")
        subprocess.run([self.docker_path, "rm", container], capture_output=True, timeout=30)
        value, raw = evaluation.read_json(receipt_path, "private Codex Research MCP receipt", private=True)
        value = evaluation.exact_fields(value, {
            "schemaVersion", "operation", "runId", "startedAt", "stoppedAt", "configSha256",
            "calls", "completed", "errors", "contentStoredBeyondJob", "credentialsExposed",
            "directPublicNetworkGranted", "externalWritesPerformed", "authority", "boundary",
        }, "Codex Research MCP receipt")
        authority = evaluation.exact_fields(value["authority"], {
            "directPublicNetwork", "credentials", "externalWrites", "publish", "purchase",
            "merge", "deploy", "policyMutation", "scopeExpansion",
        }, "Codex Research MCP authority")
        if (
            value["schemaVersion"] != 1 or value["operation"] != "pixel-outcome-research-mcp-stopped"
            or value["runId"] != run_id or value["boundary"] != RESEARCH_MCP_RECEIPT_BOUNDARY
            or any(authority.values()) or value["contentStoredBeyondJob"] is not False
            or value["credentialsExposed"] is not False or value["directPublicNetworkGranted"] is not False
            or value["externalWritesPerformed"] is not False or value["errors"] != 0
        ):
            raise evaluation.OutcomeError("Codex Research MCP receipt crossed its exact authority boundary")
        for field in ("calls", "completed", "errors"):
            evaluation.integer(value[field], 0, 200, f"Codex Research MCP {field}")
        started_at = evaluation.timestamp(value["startedAt"], "Codex Research MCP start time")
        stopped_at = evaluation.timestamp(value["stoppedAt"], "Codex Research MCP stop time")
        evaluation.valid_hash(value["configSha256"], "Codex Research MCP configuration")
        if value["calls"] < 1 or value["completed"] < 1 or value["completed"] != value["calls"]:
            raise evaluation.OutcomeError("Codex Research MCP did not complete a real brokered tool call")
        if stopped_at < started_at:
            raise evaluation.OutcomeError("Codex Research MCP lifecycle time moved backwards")
        return value, raw

    def _start_research_authority(
        self, *, run_id: str, admission: dict[str, Any], task: dict[str, Any], request_payload: bytes,
        source_path: Path, source_reference: dict[str, Any], environment: dict[str, Any],
        tool_policy: dict[str, Any], verifier_definition: dict[str, Any], model_payload: bytes,
        inference_payload: bytes, research_fixture_path: Path, research_fixture_reference: dict[str, Any],
        lifecycle_root: Path, output_root: Path,
    ) -> tuple[subprocess.Popen[bytes], Path, Path]:
        if self.pixel_system_config_path is None or not self.pixel_system_config_path.is_file():
            raise evaluation.OutcomeError("Codex Researcher requires the exact private Pixel system configuration")
        ready_path = lifecycle_root / "authority-ready.json"
        finalize_path = lifecycle_root / "authority-finalize.json"
        input_path = lifecycle_root / "authority-input.json"
        _private_new_json(input_path, {
            "schemaVersion": 1, "operation": "pixel-outcome-codex-research-authority", "runId": run_id,
            "now": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "idSuffix": run_id[-12:], "pixelSystemConfigPath": str(self.pixel_system_config_path),
            "admission": admission, "task": task,
            "requestPayloadBase64": base64.b64encode(request_payload).decode("ascii"),
            "sourcePath": str(source_path), "sourceReference": source_reference,
            "researchFixturePath": str(research_fixture_path), "researchFixtureReference": research_fixture_reference,
            "environment": environment, "toolPolicy": tool_policy, "verifierDefinition": verifier_definition,
            "modelContractBase64": base64.b64encode(model_payload).decode("ascii"),
            "modelContractSha256": evaluation.sha256(model_payload),
            "inferenceContractBase64": base64.b64encode(inference_payload).decode("ascii"),
            "inferenceContractSha256": evaluation.sha256(inference_payload),
            "readyPath": str(ready_path), "finalizePath": str(finalize_path),
            "outputRoot": str(output_root), "boundary": CODEX_RESEARCH_AUTHORITY_INPUT_BOUNDARY,
        })
        process = subprocess.Popen(
            [self.node_path, str(self.root / "deploy" / "agent-comparison" / "codex-research-authority.mjs"), str(input_path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self._wait_private_file(
            ready_path, process=process, label="Codex Researcher authority", timeout=self.health_deadline_seconds,
        )
        return process, ready_path, finalize_path

    def run_codex(
        self, network: str, contract: dict[str, Any], admission: dict[str, Any], request_payload: bytes,
        *, source_payload: bytes, source_reference: dict[str, Any], environment_payload: bytes,
        environment: dict[str, Any], tool_policy_payload: bytes, tool_policy: dict[str, Any],
        verifier_definition: dict[str, Any], run_dir: Path, run_id: str,
        task: dict[str, Any], model_contract_payload: bytes, inference_contract_payload: bytes,
        research_fixture_payload: bytes | None, research_fixture_reference: dict[str, Any] | None,
    ) -> dict[str, Any]:
        wire_api = "responses"
        script = build_codex_script(
            self.hardened, model_id=contract["modelId"], model_context_window=contract["runtime"]["contextWindow"],
            wire_api=wire_api, env_key=self.env_key,
            profile=admission["profile"], capabilities=admission["capabilities"],
            source_media_type=source_reference["mediaType"], tool_policy=tool_policy,
        )
        if not isinstance(run_dir, Path) or not run_dir.is_absolute() or not run_dir.is_dir():
            raise evaluation.OutcomeError("codex private run directory is unavailable")
        staging = Path(tempfile.mkdtemp(prefix="codex-input-", dir=run_dir))
        if os.name != "nt":
            staging.chmod(0o700)
        try:
            source_archive = staging / "source.tar"
            source_root = staging / "source"
            if source_reference["mediaType"] == "application/x-tar":
                source_archive.write_bytes(source_payload)
                if os.name != "nt":
                    source_archive.chmod(0o600)
                expanded_ceiling = min(4294967296, max(len(source_payload), len(source_payload) * 32))
                materialized = subprocess.run([
                    self.node_path, str(self.root / "scripts" / "portal_outcome_materialize_source.mjs"),
                    str(source_archive), str(source_root), source_reference["sha256"], str(len(source_payload)),
                    str(expanded_ceiling), "100000", str(expanded_ceiling),
                ], capture_output=True, timeout=min(600, admission["budgets"]["wallTimeSeconds"]))
                if materialized.returncode != 0:
                    detail = materialized.stderr.decode("utf-8", errors="replace")[-400:]
                    raise evaluation.OutcomeError(f"codex source materialization failed: {detail}")
                receipt = evaluation.parse_json(materialized.stdout, "codex source materialization receipt")
                if (
                    not isinstance(receipt, dict) or receipt.get("archiveSha256") != source_reference["sha256"]
                    or receipt.get("archiveBytes") != len(source_payload)
                ):
                    raise evaluation.OutcomeError("codex source materialization receipt differs from admission")
            else:
                source_root.mkdir(mode=0o700)
                source_input = source_root / "input"
                source_input.write_bytes(source_payload)
                if os.name != "nt":
                    source_input.chmod(0o600)
            environment_path = staging / "environment.json"
            tool_policy_path = staging / "tool-policy.json"
            model_catalog_path = staging / "model-catalog.json"
            research_schema_path = staging / "research-output-schema.json"
            workspace_root = staging / "workspace"
            shutil.copytree(source_root, workspace_root, symlinks=False)
            environment_path.write_bytes(environment_payload)
            tool_policy_path.write_bytes(tool_policy_payload)
            model_catalog_path.write_bytes(build_codex_model_catalog(
                self.root, model_id=contract["modelId"], context_window=contract["runtime"]["contextWindow"],
            ))
            if admission["profile"] == "researcher":
                if not isinstance(research_fixture_payload, bytes) or not isinstance(research_fixture_reference, dict):
                    raise evaluation.OutcomeError("Codex Researcher lacks its exact admitted offline fixture")
                research_fixture_path = staging / "research-fixture.json"
                _private_new(research_fixture_path, research_fixture_payload)
                research_schema_path.write_bytes(
                    (self.root / "schemas" / "work-research-report-proposal-v1.schema.json").read_bytes()
                )
            if os.name != "nt":
                environment_path.chmod(0o600)
                tool_policy_path.chmod(0o600)
                model_catalog_path.chmod(0o600)
                if admission["profile"] == "researcher":
                    research_schema_path.chmod(0o600)
            if admission["profile"] == "researcher":
                if source_reference["mediaType"] != "application/x-tar":
                    raise evaluation.OutcomeError("Codex Researcher authority currently requires the exact admitted tar source")
                lifecycle_root = _private_directory(staging / "research-lifecycle")
                authority_output = _private_directory(staging / "research-authority-output")
                authority_process: subprocess.Popen[bytes] | None = None
                mcp_container: str | None = None
                mcp_receipt_path: Path | None = None
                try:
                    authority_process, authority_ready_path, finalize_path = self._start_research_authority(
                        run_id=run_id, admission=admission, task=task, request_payload=request_payload,
                        source_path=source_archive, source_reference=source_reference,
                        environment=environment, tool_policy=tool_policy, verifier_definition=verifier_definition,
                        model_payload=model_contract_payload, inference_payload=inference_contract_payload,
                        research_fixture_path=research_fixture_path, research_fixture_reference=research_fixture_reference,
                        lifecycle_root=lifecycle_root, output_root=authority_output,
                    )
                    authority_ready, _ready_raw = evaluation.read_json(
                        authority_ready_path, "private Codex Researcher authority readiness", private=True,
                    )
                    authority_ready = evaluation.exact_fields(authority_ready, {
                        "schemaVersion", "operation", "runId", "createdAt", "jobId", "planSha256",
                        "leaseSha256", "claimSha256", "workPolicySha256", "environmentSha256", "runtimeEnvironmentSha256",
                        "modelContractSha256", "inferenceContractSha256", "researchFixtureSha256", "queueRoot", "maxCalls",
                        "leaseExpiresAt", "authority", "boundary",
                    }, "Codex Researcher authority readiness")
                    if (
                        authority_ready["schemaVersion"] != 1
                        or authority_ready["operation"] != "pixel-outcome-codex-research-authority-ready"
                        or authority_ready["runId"] != run_id
                        or authority_ready["modelContractSha256"] != evaluation.sha256(model_contract_payload)
                        or authority_ready["inferenceContractSha256"] != evaluation.sha256(inference_contract_payload)
                        or authority_ready["researchFixtureSha256"] != research_fixture_reference["sha256"]
                        or authority_ready["environmentSha256"] != self.pixel_environment_template_sha256
                        or authority_ready["authority"] != CODEX_RESEARCH_AUTHORITY
                        or authority_ready["boundary"] != CODEX_RESEARCH_READY_BOUNDARY
                    ):
                        raise evaluation.OutcomeError("Codex Researcher authority readiness differs from the exact run")
                    if not isinstance(authority_ready["jobId"], str) or WORK_JOB_RE.fullmatch(authority_ready["jobId"]) is None:
                        raise evaluation.OutcomeError("Codex Researcher authority job identity is invalid")
                    for field in ("planSha256", "leaseSha256", "claimSha256", "workPolicySha256", "environmentSha256", "runtimeEnvironmentSha256"):
                        evaluation.valid_hash(authority_ready[field], f"Codex Researcher authority {field}")
                    ready_at = evaluation.timestamp(authority_ready["createdAt"], "Codex Researcher authority readiness time")
                    lease_expires_at = evaluation.timestamp(authority_ready["leaseExpiresAt"], "Codex Researcher authority lease expiry")
                    evaluation.integer(authority_ready["maxCalls"], 1, 200, "Codex Researcher authority call ceiling")
                    if ready_at >= lease_expires_at:
                        raise evaluation.OutcomeError("Codex Researcher authority readiness is outside its immutable lease")
                    queue_root = Path(authority_ready["queueRoot"]).resolve(strict=True)
                    if self.pixel_runtime_root is None:
                        raise evaluation.OutcomeError("Codex Researcher authority runtime root is unavailable")
                    expected_queue = (self.pixel_runtime_root / run_id / "codex-research-queue").resolve()
                    if queue_root != expected_queue:
                        raise evaluation.OutcomeError("Codex Researcher authority queue identity is invalid")
                    mcp_container, mcp_receipt_path = self.research_mcp_start(
                        network, run_id, queue_root, authority_ready["maxCalls"],
                        min(180000, admission["budgets"]["wallTimeSeconds"] * 1000), lifecycle_root,
                    )
                    outcome = self._run_codex_staged(
                        network, admission, request_payload, script, source_root, workspace_root,
                        environment, environment_path, tool_policy_path, model_catalog_path,
                        research_schema_path=research_schema_path,
                    )
                    if outcome["exitCode"] != 0 or not outcome["finalMessage"].strip():
                        raise evaluation.OutcomeError("Codex Researcher did not return a structured proposal")
                    mcp_receipt, mcp_raw = self.research_mcp_stop(mcp_container, mcp_receipt_path, run_id)
                    mcp_container = None
                    _private_new_json(finalize_path, {
                        "schemaVersion": 1, "operation": "pixel-outcome-codex-research-finalize", "runId": run_id,
                        "proposalBase64": base64.b64encode(outcome["finalMessage"].encode("utf-8")).decode("ascii"),
                        "mcpReceiptSha256": evaluation.sha256(mcp_raw), "boundary": CODEX_RESEARCH_FINALIZE_BOUNDARY,
                    })
                    try:
                        _authority_stdout, authority_stderr = authority_process.communicate(
                            timeout=admission["budgets"]["wallTimeSeconds"] + 30,
                        )
                    except subprocess.TimeoutExpired:
                        authority_process.terminate()
                        authority_process.wait(timeout=30)
                        raise evaluation.OutcomeError("Codex Researcher authority did not finish before its lease")
                    if authority_process.returncode != 0:
                        detail = authority_stderr.decode("utf-8", errors="replace")[-400:]
                        raise evaluation.OutcomeError(f"Codex Researcher authority failed: {detail}")
                    authority_process = None
                    report_payload = (authority_output / "codex-research-report.json").read_bytes()
                    verification_payload = (authority_output / "codex-research-verification.json").read_bytes()
                    provenance_payload = (authority_output / "codex-research-provenance.json").read_bytes()
                    authority_receipt_payload = (authority_output / "authority-receipt.json").read_bytes()
                    report = evaluation.parse_json(report_payload, "Codex Researcher report")
                    verification = evaluation.parse_json(verification_payload, "Codex Researcher verification")
                    provenance = evaluation.parse_json(provenance_payload, "Codex Researcher provenance")
                    authority_receipt = evaluation.exact_fields(
                        evaluation.parse_json(authority_receipt_payload, "Codex Researcher authority receipt"), {
                            "schemaVersion", "operation", "runId", "completedAt", "jobId", "planSha256",
                            "leaseSha256", "claimSha256", "workPolicySha256", "environmentSha256",
                            "runtimeEnvironmentSha256", "modelContractSha256", "inferenceContractSha256", "researchFixtureSha256",
                            "mcpReceiptSha256", "broker", "batches", "reportSha256", "verificationSha256",
                            "provenanceSha256", "contentStoredBeyondJob", "credentialsExposed",
                            "directPublicNetworkGrantedToModel", "externalWritesPerformed", "authority", "boundary",
                        }, "Codex Researcher authority receipt",
                    )
                    broker = evaluation.exact_fields(authority_receipt["broker"], {
                        "completed", "rejected", "errors", "invalid", "contentStoredBeyondJob",
                        "credentialsExposed", "directNetworkGrantedToWorker", "externalWritesPerformed",
                    }, "Codex Researcher broker receipt")
                    for field in ("planSha256", "leaseSha256", "claimSha256", "workPolicySha256", "environmentSha256", "runtimeEnvironmentSha256", "modelContractSha256", "inferenceContractSha256", "researchFixtureSha256", "mcpReceiptSha256", "reportSha256", "verificationSha256", "provenanceSha256"):
                        evaluation.valid_hash(authority_receipt[field], f"Codex Researcher receipt {field}")
                    for field in ("completed", "rejected", "errors", "invalid"):
                        evaluation.integer(broker[field], 0, 200, f"Codex Researcher broker {field}")
                    completed_at = evaluation.timestamp(authority_receipt["completedAt"], "Codex Researcher authority completion time")
                    immutable_identity_fields = (
                        "jobId", "planSha256", "leaseSha256", "claimSha256", "workPolicySha256",
                        "environmentSha256", "runtimeEnvironmentSha256", "modelContractSha256",
                        "inferenceContractSha256", "researchFixtureSha256",
                    )
                    if (
                        authority_receipt["schemaVersion"] != 1
                        or authority_receipt["operation"] != "pixel-outcome-codex-research-authority-complete"
                        or authority_receipt["runId"] != run_id
                        or authority_receipt["modelContractSha256"] != evaluation.sha256(model_contract_payload)
                        or authority_receipt["inferenceContractSha256"] != evaluation.sha256(inference_contract_payload)
                        or authority_receipt["researchFixtureSha256"] != research_fixture_reference["sha256"]
                        or authority_receipt["environmentSha256"] != self.pixel_environment_template_sha256
                        or authority_receipt["mcpReceiptSha256"] != evaluation.sha256(mcp_raw)
                        or authority_receipt["reportSha256"] != evaluation.sha256(report_payload)
                        or authority_receipt["verificationSha256"] != evaluation.sha256(verification_payload)
                        or authority_receipt["provenanceSha256"] != evaluation.sha256(provenance_payload)
                        or any(authority_receipt[field] != authority_ready[field] for field in immutable_identity_fields)
                        or completed_at < ready_at or completed_at >= lease_expires_at
                        or authority_receipt["batches"] != broker["completed"] or broker["completed"] < 1
                        or broker["completed"] != mcp_receipt["completed"]
                        or broker["rejected"] != 0 or broker["errors"] != 0 or broker["invalid"] != 0
                        or any(broker[field] is not False for field in (
                            "contentStoredBeyondJob", "credentialsExposed", "directNetworkGrantedToWorker",
                            "externalWritesPerformed",
                        ))
                        or any(authority_receipt[field] is not False for field in (
                            "contentStoredBeyondJob", "credentialsExposed", "directPublicNetworkGrantedToModel",
                            "externalWritesPerformed",
                        ))
                        or authority_receipt["authority"] != CODEX_RESEARCH_AUTHORITY
                        or authority_receipt["boundary"] != CODEX_RESEARCH_RECEIPT_BOUNDARY
                    ):
                        raise evaluation.OutcomeError("Codex Researcher authority receipt differs from its exact safe run")
                    outcome["artifacts"] = [item for item in outcome["artifacts"] if item["relativePath"] != "artifact.md"]
                    outcome["artifacts"].extend([
                        {"kind": "finding-report", "relativePath": "codex-research-report.json", "payload": report_payload},
                        {"kind": "test-evidence", "relativePath": "codex-research-verification.json", "payload": verification_payload},
                        {"kind": "test-evidence", "relativePath": "codex-research-provenance.json", "payload": provenance_payload},
                        {"kind": "test-evidence", "relativePath": "codex-research-authority-receipt.json", "payload": authority_receipt_payload},
                        {"kind": "test-evidence", "relativePath": "codex-research-mcp-receipt.json", "payload": mcp_raw},
                    ])
                    outcome["finalMessage"] = render_research_report(report)
                    outcome["independentVerification"] = verification
                    outcome["researchEvidence"] = provenance
                    if sum(len(item["payload"]) for item in outcome["artifacts"]) > admission["budgets"]["artifactBytes"]:
                        raise evaluation.OutcomeError("Codex Researcher artifacts exceed the admitted byte ceiling")
                finally:
                    if mcp_container is not None:
                        subprocess.run([self.docker_path, "rm", "-f", mcp_container], capture_output=True, timeout=30)
                    if authority_process is not None and authority_process.poll() is None:
                        authority_process.terminate()
                        try:
                            authority_process.wait(timeout=30)
                        except subprocess.TimeoutExpired:
                            authority_process.kill()
                            authority_process.wait(timeout=10)
            else:
                outcome = self._run_codex_staged(
                    network, admission, request_payload, script, source_root, workspace_root,
                    environment, environment_path, tool_policy_path, model_catalog_path,
                )
            candidate_workspace = outcome.pop("_workspacePath", None)
            if candidate_workspace is not None:
                candidate_workspace = Path(candidate_workspace).resolve(strict=True)
                if candidate_workspace.parent != staging or candidate_workspace.name != "codex-workspace-export":
                    raise evaluation.OutcomeError("Codex candidate workspace escaped its private staging root")
                workspace_root = candidate_workspace
            outcome["independentVerification"] = self.verify_workspace(
                baseline=source_root, workspace=workspace_root, definition=verifier_definition,
                environment=environment, admission=admission, run_dir=run_dir, run_id=run_id,
            ) if admission["profile"] != "researcher" else outcome["independentVerification"]
            return outcome
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _run_codex_staged(
        self, network: str, admission: dict[str, Any], request_payload: bytes, script: str,
        source_path: Path, workspace_path: Path, environment: dict[str, Any],
        environment_path: Path, tool_policy_path: Path,
        model_catalog_path: Path, research_schema_path: Path | None = None,
    ) -> dict[str, Any]:
        wall_budget = admission["budgets"]["wallTimeSeconds"]
        name = f"{network}-codex"
        started = time.monotonic()
        volume = None
        keeper = None
        try:
            volume, maximum_workspace_bytes = self.codex_workspace_volume_create(network, environment)
            keeper = self.codex_workspace_keeper_start(network, volume)
            self.codex_workspace_copy("init", volume, workspace_path, maximum_workspace_bytes)
            result = subprocess.run(
                [
                    self.docker_path, "run", "--rm", "-i", "--name", name, "--network", network,
                    "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                    *codex_container_resource_args(environment),
                    *container_user_args(),
                    "--mount", f"type=bind,source={source_path},target=/input/source-baseline,readonly",
                    "--mount", f"type=volume,source={volume},target=/work/source",
                    "--mount", f"type=bind,source={environment_path},target=/input/environment.json,readonly",
                    "--mount", f"type=bind,source={tool_policy_path},target=/input/tool-policy.json,readonly",
                    "--mount", f"type=bind,source={model_catalog_path},target={CODEX_MODEL_CATALOG_CONTAINER_PATH},readonly",
                    *(["--mount", f"type=bind,source={research_schema_path},target={CODEX_RESEARCH_OUTPUT_SCHEMA_CONTAINER_PATH},readonly"] if research_schema_path is not None else []),
                    "--env", "HOME=/work", "--env", "CODEX_HOME=/work/.codex", "--env", "TMPDIR=/work",
                    "--env", f"{self.env_key}=local-no-auth",
                    "--entrypoint", "/bin/sh", self.codex_image, "-c", script,
                ],
                input=request_payload, capture_output=True, timeout=wall_budget + 120,
            )
            candidate_workspace = _private_directory(workspace_path.parent / "codex-workspace-export")
            self.codex_workspace_copy("export", volume, candidate_workspace, maximum_workspace_bytes)
        finally:
            if keeper is not None:
                subprocess.run([self.docker_path, "rm", "-f", keeper], capture_output=True, timeout=30)
            if volume is not None:
                subprocess.run([self.docker_path, "volume", "rm", volume], capture_output=True, timeout=30)
        latency_ms = int((time.monotonic() - started) * 1000)
        transcript = result.stdout.decode("utf-8", errors="replace")
        artifacts = []
        message = last_agent_message(transcript)
        if message is not None and message.strip():
            artifacts.append({
                "kind": "finding-report", "relativePath": "artifact.md",
                "payload": message.encode("utf-8"),
            })
        artifacts.extend(collect_workspace_delta(
            source_path, candidate_workspace, admission["budgets"]["artifactBytes"],
        ))
        if sum(len(item["payload"]) for item in artifacts) > admission["budgets"]["artifactBytes"]:
            raise evaluation.OutcomeError("codex artifacts exceed the admitted byte ceiling")
        return {
            "transcript": transcript, "exitCode": result.returncode,
            "latencyMs": latency_ms, "artifacts": artifacts, "finalMessage": message or "",
            "_workspacePath": str(candidate_workspace),
        }

    def teardown(self, network: str | None, container: str | None, boundary: str | None = None) -> None:
        for target in (
            f"{network}-codex" if network else None,
            boundary or (f"{network}-inference" if network else None),
            container,
        ):
            if target:
                subprocess.run([self.docker_path, "rm", "-f", target], capture_output=True, timeout=60)
        if network:
            subprocess.run([self.docker_path, "network", "rm", network], capture_output=True, timeout=60)
            subprocess.run([self.docker_path, "network", "rm", f"{network}-backend"], capture_output=True, timeout=60)
        if boundary:
            self._boundary_receipts.pop(boundary, None)
            self._boundary_run_ids.pop(boundary, None)
