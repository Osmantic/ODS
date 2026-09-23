#!/usr/bin/env python3
"""Concrete local Docker/Work system adapter for the full Pixel outcome arm."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import time
from typing import Any

import portal_outcome_assistant_system as assistant_system
import portal_outcome_evaluation as evaluation
import portal_outcome_runtime_control as runtime_control


INTERACTION_BOUNDARY = (
    "Content-free mechanically observed interaction telemetry for one single-admission noninteractive comparison run. "
    "It records only event classes, counts, and an observation digest; it contains no prompt, response, tool argument, "
    "result, path, credential, provider content, approval authority, or completion authority."
)


def _assistant_interaction(envelope: dict[str, Any], turn: dict[str, Any]) -> dict[str, Any]:
    state = turn.get("state")
    capability = turn.get("capability") if isinstance(turn.get("capability"), dict) else {}
    capability_state = capability.get("state")
    approval = int(capability.get("approvalRequired") is True or capability_state == "broker-approval-required")
    scope = int(capability_state in {"waiting-authority", "scope-expansion-required"})
    attention = int(approval == 1 or scope == 1 or state == "attention")
    safety = int(state == "safety-blocked" or capability_state in {"safety-blocked", "policy-blocked"})
    interrupted = int(state in {"interrupted", "cancelled"})
    observation = {
        "conversationSha256": envelope.get("conversationSha256"),
        "turnIdSha256": evaluation.sha256(str(turn.get("turnId", "")).encode("utf-8")),
        "state": state,
        "capabilityState": capability_state,
        "approvalRequired": capability.get("approvalRequired"),
        "autonomousWithinPolicy": capability.get("autonomousWithinPolicy"),
        "toolCalls": turn.get("toolCalls"),
        "actionJournalChains": len(envelope.get("actionJournalChains", [])),
    }
    return {
        "schemaVersion": 1,
        "mode": "single-admission-noninteractive",
        "source": "pixel-assistant-conversation-v1",
        "observationSha256": evaluation.sha256(evaluation.canonical(observation)),
        "operatorInputsAfterAdmission": 0,
        "operatorAttentionRequests": attention,
        "approvalRequests": approval,
        "scopeExpansionRequests": scope,
        "safetyBlocks": safety,
        "interruptions": interrupted,
        "complete": True,
        "workerSelfReported": False,
        "boundary": INTERACTION_BOUNDARY,
    }


RUN_RE = re.compile(r"^outcomerun-[0-9]{13}-[a-f0-9]{12}$")
SHA_RE = re.compile(r"^[a-f0-9]{64}$")
PRODUCT_PROFILES = frozenset({"assistant", "builder", "controller", "researcher"})
MAX_ASSISTANT_WORKSPACE_FILES = 100_000
MAX_ASSISTANT_EXECUTABLE_BYTES = 512 * 1024 * 1024
MAX_PIXEL_CONTROL_DIAGNOSTIC_BYTES = 64 * 1024
RUN_ID_SUFFIX_RE = re.compile(r"[a-f0-9]{12}$")
INDEPENDENT_VERIFICATION_BOUNDARY = (
    "Content-free independent workspace verification evidence only. Controller-selected checks ran against a "
    "fresh candidate copy without network; worker output could not select checks or grant execution, external-effect, "
    "merge, deployment, publication, completion, acceptance, or promotion authority."
)
ASSISTANT_PREPARATION_BOUNDARY = (
    "Private disposable Pixel portal Assistant preparation only. It binds one exact admitted source, request, DSV4 "
    "runtime, inference contract, qualification receipt, OpenClaw tool lease, local loopback proxy, and owner-private "
    "workspace; it grants no host access, ambient credential, direct network, package installation, external effect, "
    "merge, deployment, policy mutation, completion, publication, acceptance, or promotion authority."
)
CONFIG_BOUNDARY = (
    "Owner-private templates and a disposable runtime root for exact Pixel outcome runs only. Configuration grants "
    "no model start, task execution, provider, credential, external-effect, merge, deployment, publication, "
    "completion, acceptance, or promotion authority."
)
CONTROL_FILES = (
    "pixel-system-planned-review-input.json", "pixel-system-planned-review-output.json",
    "pixel-system-review-input.json", "pixel-system-review-output.json",
    "pixel-system-start-input.json", "pixel-system-start-output.json",
    "pixel-system-run-input.json", "pixel-system-run-output.json", "pixel-system-source.tar",
    "pixel-system-stop-input.json", "pixel-system-stop-output.json",
)


def _admitted_profile(admission: Any) -> str:
    if not isinstance(admission, dict) or admission.get("profile") not in PRODUCT_PROFILES:
        raise evaluation.OutcomeError("Pixel system admission profile is not implemented")
    return admission["profile"]


def _private_new(path: Path, payload: bytes) -> None:
    if not path.is_absolute() or not path.parent.is_dir():
        raise evaluation.OutcomeError("Pixel system private output parent is unavailable")
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600,
    )
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)


def _private_json(path: Path, value: Any) -> None:
    _private_new(path, json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n")


def _write_control_failure_diagnostic(run_dir: Path, command: str, stderr: bytes) -> None:
    retained = stderr[-MAX_PIXEL_CONTROL_DIAGNOSTIC_BYTES:]
    value = {
        "schemaVersion": 1,
        "operation": "pixel-portal-outcome-system-control-failure-diagnostic",
        "command": command,
        "stderrBytes": len(stderr),
        "stderrSha256": evaluation.sha256(stderr),
        "retainedBytes": len(retained),
        "retainedBase64": base64.b64encode(retained).decode("ascii"),
        "truncated": len(stderr) > len(retained),
        "boundary": (
            "Owner-private bounded Pixel system control failure diagnostic. It may contain local paths or backend "
            "messages and grants no execution, credential, external-effect, deployment, publication, completion, "
            "acceptance, or promotion authority."
        ),
    }
    try:
        _private_json(run_dir / f"pixel-system-{command}-failure-diagnostic.json", value)
    except Exception:
        # Diagnostic persistence must never replace the primary control failure
        # or interfere with the teardown/restoration path.
        return


def _trusted_executable_identity(path: Path, label: str) -> tuple[str, int]:
    path = Path(path)
    if not path.is_absolute() or Path(os.path.abspath(path)) != path or path == Path(path.anchor):
        raise evaluation.OutcomeError(f"{label} is not an exact absolute path")
    try:
        before = path.lstat()
        actual = path.resolve(strict=True)
    except OSError as exc:
        raise evaluation.OutcomeError(f"{label} is unavailable") from exc
    if (
        actual != path or not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode)
        or before.st_nlink != 1 or not 1 <= before.st_size <= MAX_ASSISTANT_EXECUTABLE_BYTES
    ):
        raise evaluation.OutcomeError(f"{label} is linked, multiply linked, or unbounded")
    if os.name != "nt":
        trusted_owner = before.st_uid in {0, os.geteuid()}
        if not trusted_owner or stat.S_IMODE(before.st_mode) & 0o022 or not stat.S_IMODE(before.st_mode) & 0o111:
            raise evaluation.OutcomeError(f"{label} has unsafe ownership or mode")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise evaluation.OutcomeError(f"{label} cannot be opened safely") from exc
    digest = hashlib.sha256()
    total = 0
    try:
        opened = os.fstat(descriptor)
        if opened.st_dev != before.st_dev or opened.st_ino != before.st_ino:
            raise evaluation.OutcomeError(f"{label} changed while opened")
        while total <= MAX_ASSISTANT_EXECUTABLE_BYTES:
            chunk = os.read(descriptor, min(64 * 1024, MAX_ASSISTANT_EXECUTABLE_BYTES + 1 - total))
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        total != opened.st_size or total > MAX_ASSISTANT_EXECUTABLE_BYTES
        or after.st_dev != opened.st_dev or after.st_ino != opened.st_ino or after.st_size != opened.st_size
        or after.st_mtime_ns != opened.st_mtime_ns or after.st_ctime_ns != opened.st_ctime_ns
    ):
        raise evaluation.OutcomeError(f"{label} changed while its identity was measured")
    return digest.hexdigest(), total


def _read_private_payload(path: Path, expected_bytes: int, expected_sha256: str) -> bytes:
    if not path.is_absolute() or not SHA_RE.fullmatch(expected_sha256):
        raise evaluation.OutcomeError("Pixel system artifact identity is invalid")
    try:
        before = path.lstat()
    except OSError as exc:
        raise evaluation.OutcomeError("Pixel system artifact is unavailable") from exc
    if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode) or before.st_nlink != 1 or before.st_size != expected_bytes:
        raise evaluation.OutcomeError("Pixel system artifact is not a singular file of the declared size")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if opened.st_dev != before.st_dev or opened.st_ino != before.st_ino:
            raise evaluation.OutcomeError("Pixel system artifact changed before collection")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            payload = handle.read(expected_bytes + 1)
    finally:
        os.close(descriptor)
    if len(payload) != expected_bytes or evaluation.sha256(payload) != expected_sha256:
        raise evaluation.OutcomeError("Pixel system artifact bytes differ from their receipt")
    return payload


def _within(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _private_directory(path: Path, label: str) -> Path:
    if not path.is_absolute() or Path(os.path.abspath(path)) != path or path == Path(path.anchor):
        raise evaluation.OutcomeError(f"{label} is not an exact absolute directory")
    try:
        info = path.lstat()
        actual = path.resolve(strict=True)
    except OSError as exc:
        raise evaluation.OutcomeError(f"{label} is unavailable") from exc
    junction = getattr(path, "is_junction", lambda: False)()
    if actual != path or not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or junction:
        raise evaluation.OutcomeError(f"{label} is linked or not a directory")
    if os.name != "nt" and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077):
        raise evaluation.OutcomeError(f"{label} is not owner-private")
    return path


def _assistant_source_inventory(path: Path, *, source_reference: dict[str, Any], tree_sha256: str) -> dict[str, tuple[str, int, bool]]:
    value, _payload = evaluation.read_json(path, "private Assistant source inventory", private=True)
    value = evaluation.exact_fields(value, {
        "schemaVersion", "archiveSha256", "archiveBytes", "extractedBytes", "entries", "treeSha256",
    }, "Assistant source inventory")
    if (
        value["schemaVersion"] != 1 or value["archiveSha256"] != source_reference["sha256"]
        or value["archiveBytes"] != source_reference["bytes"] or value["treeSha256"] != tree_sha256
        or not isinstance(value["entries"], list) or len(value["entries"]) > MAX_ASSISTANT_WORKSPACE_FILES
    ):
        raise evaluation.OutcomeError("Assistant source inventory differs from its admitted archive")
    files: dict[str, tuple[str, int, bool]] = {}
    seen: set[str] = set()
    for item in value["entries"]:
        if not isinstance(item, dict) or item.get("kind") not in {"directory", "file"}:
            raise evaluation.OutcomeError("Assistant source inventory entry is invalid")
        expected = {"path", "sourcePath", "kind", "bytes", "inert"} | ({"sha256"} if item["kind"] == "file" else set())
        item = evaluation.exact_fields(item, expected, "Assistant source inventory entry")
        relative = evaluation.relative_path(item["path"], "Assistant source inventory path").as_posix()
        if relative in seen or type(item["inert"]) is not bool or type(item["bytes"]) is not int or item["bytes"] < 0:
            raise evaluation.OutcomeError("Assistant source inventory entry is duplicated or invalid")
        seen.add(relative)
        if item["kind"] == "file":
            evaluation.valid_hash(item["sha256"], "Assistant source file digest")
            files[relative] = (item["sha256"], item["bytes"], item["inert"])
    return files


def _assistant_workspace_files(root: Path, byte_ceiling: int) -> dict[str, tuple[str, int]]:
    root = _private_directory(root, "Assistant workspace")
    evaluation.integer(byte_ceiling, 1, 1_073_741_824, "Assistant workspace byte ceiling")
    files: dict[str, tuple[str, int]] = {}
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError as exc:
            raise evaluation.OutcomeError("Assistant workspace cannot be inventoried safely") from exc
        for entry in entries:
            path = Path(entry.path)
            try:
                metadata = path.lstat()
            except OSError as exc:
                raise evaluation.OutcomeError("Assistant workspace changed during collection") from exc
            if entry.is_symlink() or getattr(path, "is_junction", lambda: False)():
                raise evaluation.OutcomeError("Assistant workspace contains a linked entry")
            if stat.S_ISDIR(metadata.st_mode):
                stack.append(path)
                continue
            if not stat.S_ISREG(metadata.st_mode) or len(files) >= MAX_ASSISTANT_WORKSPACE_FILES:
                raise evaluation.OutcomeError("Assistant workspace contains a special file or exceeds its file ceiling")
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                opened = os.fstat(descriptor)
                if not stat.S_ISREG(opened.st_mode) or opened.st_size != metadata.st_size:
                    raise evaluation.OutcomeError("Assistant workspace file changed while opened")
                digest = hashlib.sha256()
                with os.fdopen(descriptor, "rb", closefd=False) as handle:
                    observed = 0
                    while chunk := handle.read(1_048_576):
                        observed += len(chunk); digest.update(chunk)
            finally:
                os.close(descriptor)
            if observed != metadata.st_size:
                raise evaluation.OutcomeError("Assistant workspace file changed while read")
            relative = path.relative_to(root).as_posix()
            evaluation.relative_path(relative, "Assistant workspace path")
            files[relative] = (digest.hexdigest(), observed)
    return files


def _collect_assistant_artifacts(
    *, workspace: Path, inventory_path: Path, source_reference: dict[str, Any],
    source_tree_sha256: str, byte_ceiling: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    evaluation.integer(byte_ceiling, 1, 1_073_741_824, "Assistant artifact byte ceiling")
    before = _assistant_source_inventory(inventory_path, source_reference=source_reference, tree_sha256=source_tree_sha256)
    after = _assistant_workspace_files(workspace, byte_ceiling)
    changes = []
    immutable_changed = False
    for relative in sorted(set(before) | set(after)):
        old = before.get(relative)
        new = after.get(relative)
        if old is not None and new is not None and old[:2] == new[:2]:
            continue
        immutable_changed = immutable_changed or bool(old and old[2])
        changes.append({
            "path": relative, "change": "added" if old is None else "deleted" if new is None else "modified",
            "beforeSha256": None if old is None else old[0], "beforeBytes": None if old is None else old[1],
            "afterSha256": None if new is None else new[0], "afterBytes": None if new is None else new[1],
        })
    if immutable_changed:
        raise evaluation.OutcomeError("Assistant changed an inert source control file")
    if not changes:
        return [], {"changes": 0, "files": len(after), "bytes": 0, "immutablePathViolation": False}
    manifest = (json.dumps({
        "schemaVersion": 1, "operation": "pixel-portal-outcome-workspace-delta", "changedFiles": changes,
        "boundary": "Private post-run content identity only; this record grants no execution or effect authority.",
    }, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    changed_bytes = sum(item["afterBytes"] or 0 for item in changes)
    if changed_bytes > byte_ceiling:
        raise evaluation.OutcomeError("Assistant workspace outputs exceed the artifact ceiling")
    artifacts = [{"kind": "test-evidence", "relativePath": "workspace-delta.json", "payload": manifest}]
    for change in changes:
        if change["change"] != "deleted":
            digest, size = after[change["path"]]
            payload = _read_private_payload(workspace / change["path"], size, digest)
            if payload:
                artifacts.append({"kind": "workspace-file", "relativePath": f"workspace/{change['path']}", "payload": payload})
    if len(artifacts) > 256 or sum(len(item["payload"]) for item in artifacts) > byte_ceiling:
        raise evaluation.OutcomeError("Assistant retained artifacts exceed their admitted ceiling")
    return artifacts, {
        "changes": len(changes), "files": len(after),
        "bytes": changed_bytes, "immutablePathViolation": False,
    }


def _assistant_preparation(
    value: Any, *, root: Path, runtime_root: Path, run_id: str,
    request_sha256: str, source_reference: dict[str, Any],
) -> dict[str, Any]:
    value = evaluation.exact_fields(value, {
        "schemaVersion", "operation", "runId", "requestSha256", "planSha256",
        "assistantRootPath", "workspacePath", "workspaceTreeSha256", "sourceInventoryPath",
        "openclawHomePath", "onboardingPath", "proxyConfigPath", "proxyReceiptPath",
        "openclawBinaryPath", "openclawBinarySha256", "openclawBinaryBytes",
        "nodeBinaryPath", "nodeBinarySha256", "nodeBinaryBytes", "proxyLauncherPath", "allowedTools",
        "chatEnvironment", "actionJournalRoots", "externalEffects", "boundary",
    }, "Assistant preparation receipt")
    for field in ("requestSha256", "planSha256", "workspaceTreeSha256", "openclawBinarySha256", "nodeBinarySha256"):
        evaluation.valid_hash(value[field], f"Assistant preparation {field}")
    if (
        value["schemaVersion"] != 1 or value["operation"] != "pixel-portal-outcome-assistant-preparation"
        or value["runId"] != run_id or value["requestSha256"] != request_sha256
        or value["externalEffects"] is not False or value["boundary"] != ASSISTANT_PREPARATION_BOUNDARY
    ):
        raise evaluation.OutcomeError("Assistant preparation receipt differs from the admitted run")
    assistant_root = _private_directory(Path(value["assistantRootPath"]), "Assistant disposable root")
    expected_root = runtime_root / run_id / "assistant"
    if assistant_root != expected_root or not _within(runtime_root / run_id, assistant_root):
        raise evaluation.OutcomeError("Assistant disposable root differs from the exact Pixel runtime")
    exact_paths = {
        "workspacePath": assistant_root / "workspace", "sourceInventoryPath": assistant_root / "source-inventory.json",
        "openclawHomePath": assistant_root / "openclaw-home", "onboardingPath": assistant_root / "onboarding.json",
        "proxyConfigPath": assistant_root / "model-proxy.json", "proxyReceiptPath": assistant_root / "model-proxy-receipt.json",
        "proxyLauncherPath": root / "deploy" / "agent-comparison" / "assistant-model-proxy.mjs",
    }
    for field, expected in exact_paths.items():
        path = Path(value[field])
        if not path.is_absolute() or Path(os.path.abspath(path)) != path or path != expected:
            raise evaluation.OutcomeError(f"Assistant preparation {field} is outside its exact runtime")
    _private_directory(Path(value["workspacePath"]), "Assistant workspace")
    _private_directory(Path(value["openclawHomePath"]), "Assistant OpenClaw home")
    for field in ("sourceInventoryPath", "onboardingPath", "proxyConfigPath", "openclawBinaryPath", "nodeBinaryPath", "proxyLauncherPath"):
        path = Path(value[field])
        if not path.is_absolute() or not path.is_file():
            raise evaluation.OutcomeError(f"Assistant preparation {field} is unavailable")
    for prefix in ("openclawBinary", "nodeBinary"):
        observed_sha256, observed_bytes = _trusted_executable_identity(
            Path(value[f"{prefix}Path"]), f"Assistant {prefix} executable",
        )
        if value[f"{prefix}Sha256"] != observed_sha256 or value[f"{prefix}Bytes"] != observed_bytes:
            raise evaluation.OutcomeError(f"Assistant preparation {prefix} executable identity changed")
    tools = value["allowedTools"]
    if (
        not isinstance(tools, list) or not 1 <= len(tools) <= 256 or tools != sorted(tools)
        or len(set(tools)) != len(tools) or any(not isinstance(item, str) or re.fullmatch(r"[a-z][a-z0-9_]{1,63}", item) is None for item in tools)
    ):
        raise evaluation.OutcomeError("Assistant preparation tool lease is invalid")
    if not isinstance(value["chatEnvironment"], dict) or any(not isinstance(key, str) or not isinstance(child, str) for key, child in value["chatEnvironment"].items()):
        raise evaluation.OutcomeError("Assistant preparation chat environment is invalid")
    journals = value["actionJournalRoots"]
    if not isinstance(journals, list) or len(journals) > 8 or len(set(journals)) != len(journals):
        raise evaluation.OutcomeError("Assistant preparation action journal roots are invalid")
    for journal in journals:
        _private_directory(Path(journal), "Assistant action journal root")
    config = assistant_system._read_proxy_config(Path(value["proxyConfigPath"]))
    if config.get("planSha256") != value["planSha256"] or config.get("allowedTools") != tools:
        raise evaluation.OutcomeError("Assistant preparation proxy differs from its admitted plan or tool lease")
    if config.get("receiptPath") != "/run/pixel-work-output/model-proxy-receipt.json":
        raise evaluation.OutcomeError("Assistant preparation proxy receipt boundary is invalid")
    _assistant_source_inventory(
        Path(value["sourceInventoryPath"]), source_reference=source_reference,
        tree_sha256=value["workspaceTreeSha256"],
    )
    return value


class PixelDockerSystem:
    """Runs an exact profile-bound model lifecycle and real Pixel Work path via the Node bridge."""

    def __init__(
        self, *, root: Path, configuration_path: Path, node_path: str = "node",
        command_timeout_seconds: int = 7200, docker_path: str = "docker",
        assistant_runner: Any = assistant_system.execute_assistant_turn,
    ):
        self.root = Path(root).resolve()
        self.configuration_path = Path(configuration_path).resolve()
        if not self.configuration_path.is_file():
            raise evaluation.OutcomeError("Pixel system configuration is unavailable")
        configuration, _raw = evaluation.read_json(
            self.configuration_path, "private Pixel system configuration", private=True,
        )
        configuration = evaluation.exact_fields(configuration, {
            "$schema", "schemaVersion", "runtimeRoot", "policyTemplatePath",
            "environmentTemplatePath", "modelBackendTemplatePath", "assistantTemplatePath", "boundary",
        }, "Pixel system configuration")
        if (
            configuration["$schema"] != "https://osmantic.com/pixel/schemas/portal-outcome-pixel-system-v1.schema.json"
            or configuration["schemaVersion"] != 1 or configuration["boundary"] != CONFIG_BOUNDARY
        ):
            raise evaluation.OutcomeError("Pixel system configuration contract is invalid")
        self.runtime_root = Path(configuration["runtimeRoot"])
        if not self.runtime_root.is_absolute():
            raise evaluation.OutcomeError("Pixel system runtime root is not absolute")
        self.assistant_template_path = Path(configuration["assistantTemplatePath"])
        if not self.assistant_template_path.is_absolute() or not self.assistant_template_path.is_file():
            raise evaluation.OutcomeError("Pixel Assistant runtime template is unavailable")
        self.node_path = node_path
        self.docker_path = docker_path
        if not callable(assistant_runner):
            raise evaluation.OutcomeError("Pixel Assistant runner is unavailable")
        self.assistant_runner = assistant_runner
        self.command_timeout_seconds = evaluation.integer(
            command_timeout_seconds, 60, 86400, "Pixel system command timeout",
        )
        self.cli_path = self.root / "deploy" / "agent-comparison" / "pixel-system-cli.mjs"
        if not self.cli_path.is_file():
            raise evaluation.OutcomeError("Pixel system bridge is unavailable")
        self._run_dirs: dict[str, Path] = {}
        self._started: set[str] = set()
        self._contracts: dict[str, tuple[bytes, bytes, str, str, dict[str, Any], dict[str, Any]]] = {}
        self._profiles: dict[str, str] = {}
        self._runtime_controls: dict[str, dict[str, Any]] = {}

    def runtime_control(self, *, run_id: str, condition: str) -> dict[str, Any]:
        if run_id not in self._started or run_id in self._runtime_controls:
            raise evaluation.OutcomeError("Pixel runtime control requires one exact freshly started runtime")
        receipt = runtime_control.execute(
            docker_path=self.docker_path,
            container=f"pixel-outcome-model-{run_id[-12:]}",
            run_id=run_id,
            condition=condition,
        )
        self._runtime_controls[run_id] = receipt
        return receipt

    def _assistant_verifier_check(
        self, *, image_digest: str, workspace: Path, check: dict[str, Any],
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
            "--mount", f"type=bind,source={workspace},target=/workspace/source,readonly",
            "--workdir", f"/workspace/{check['workingDirectory']}",
            "--env", "HOME=/tmp/home", "--env", "LANG=C.UTF-8", "--env", "TZ=UTC",
        ]
        if os.name != "nt":
            command.extend(["--user", f"{os.getuid()}:{os.getgid()}"])
        command.extend(["--entrypoint", check["argv"][0], image_digest, *check["argv"][1:]])
        timed_out = False; spawn_failed = False; exit_code = None; signal_name = None; stdout = b""; stderr = b""
        try:
            result = subprocess.run(command, capture_output=True, timeout=check["timeoutSeconds"])
            exit_code = result.returncode if 0 <= result.returncode <= 255 else None
            if result.returncode < 0:
                try: signal_name = signal.Signals(-result.returncode).name
                except ValueError: signal_name = f"SIGUNKNOWN{-result.returncode}"
            stdout, stderr = result.stdout, result.stderr
        except subprocess.TimeoutExpired as exc:
            timed_out = True; stdout = exc.stdout or b""; stderr = exc.stderr or b""
        except OSError:
            spawn_failed = True
        finally:
            subprocess.run([self.docker_path, "container", "rm", "--force", container_name], capture_output=True, timeout=30)
        duration_ms = int((time.monotonic() - started) * 1000)
        output_limit_exceeded = len(stdout) + len(stderr) > check["maxOutputBytes"]
        inspect = subprocess.run([self.docker_path, "container", "inspect", container_name], capture_output=True, timeout=30)
        cleanup_verified = inspect.returncode != 0
        status = "pass" if (
            not timed_out and not spawn_failed and not output_limit_exceeded and exit_code == 0
            and signal_name is None and cleanup_verified
        ) else "fail"
        return {
            "id": check["id"], "kind": "command", "criterionIndexes": list(check["criterionIndexes"]),
            "status": status, "runtimeMilliseconds": duration_ms, "exitCode": exit_code, "signal": signal_name,
            "timedOut": timed_out, "outputLimitExceeded": output_limit_exceeded, "spawnFailed": spawn_failed,
            "stdoutBytes": len(stdout), "stdoutSha256": evaluation.sha256(stdout),
            "stderrBytes": len(stderr), "stderrSha256": evaluation.sha256(stderr), "cleanupVerified": cleanup_verified,
        }

    def _verify_assistant_workspace(
        self, *, workspace: Path, definition: dict[str, Any], environment: dict[str, Any],
        admission: dict[str, Any], run_id: str, workspace_delta: dict[str, Any],
    ) -> dict[str, Any] | None:
        verification = definition.get("workspaceVerification")
        if verification is None:
            return None
        verifier_environment = environment["verifier"]
        image_digest = verifier_environment["imageDigest"]
        inspected = subprocess.run(
            [self.docker_path, "image", "inspect", "--format", "{{.Id}}", image_digest],
            capture_output=True, timeout=30,
        )
        if inspected.returncode != 0 or inspected.stdout.decode("utf-8", errors="replace").strip() != image_digest:
            raise evaluation.OutcomeError("Assistant independent verifier image differs from the admitted digest")
        match = RUN_ID_SUFFIX_RE.search(run_id or "")
        if match is None:
            raise evaluation.OutcomeError("Assistant independent verifier requires a unique run identity")
        files = _assistant_workspace_files(workspace, admission["budgets"]["artifactBytes"])
        candidate_sha256 = evaluation.sha256(evaluation.canonical([
            {"path": path, "sha256": item[0], "bytes": item[1]} for path, item in sorted(files.items())
        ]))
        checks = []; total_runtime = 0; total_output = 0
        for index, check in enumerate(verification["checks"]):
            if check["kind"] == "patch-integrity":
                checks.append({
                    "id": check["id"], "kind": "patch-integrity", "criterionIndexes": list(check["criterionIndexes"]),
                    "status": "fail" if workspace_delta["immutablePathViolation"] else "pass",
                    "changes": workspace_delta["changes"], "files": workspace_delta["files"],
                    "bytes": workspace_delta["bytes"], "cleanupVerified": True,
                })
                continue
            result = self._assistant_verifier_check(
                image_digest=image_digest, workspace=workspace, check=check, environment=environment,
                container_name=f"pixel-outcome-av-{match.group(0)}-{index:02d}",
            )
            total_runtime += result["runtimeMilliseconds"]
            total_output += result["stdoutBytes"] + result["stderrBytes"]
            checks.append(result)
        criteria = []
        for index, _criterion in enumerate(definition["acceptanceCriteria"]):
            relevant = [check for check in checks if index in check["criterionIndexes"]]
            criteria.append({
                "index": index, "status": "pass" if relevant and all(item["status"] == "pass" for item in relevant) else "fail",
                "checkIds": [item["id"] for item in relevant],
            })
        aggregate_within_bounds = total_runtime <= verification["maxRuntimeSeconds"] * 1000 and total_output <= verification["maxOutputBytes"]
        cleanup_verified = all(check["cleanupVerified"] for check in checks)
        return {
            "schemaVersion": 1, "format": "pixel-neutral-independent-verification-v1",
            "sourceSnapshotSha256": admission["bindings"]["sourceSnapshotSha256"], "candidateSha256": candidate_sha256,
            "status": "pass" if criteria and all(item["status"] == "pass" for item in criteria) and aggregate_within_bounds and cleanup_verified else "fail",
            "checks": checks, "criteria": criteria, "network": "none", "workerSelectedChecks": False,
            "externalEffects": False, "immutablePathViolation": workspace_delta["immutablePathViolation"],
            "aggregateRuntimeMilliseconds": total_runtime, "aggregateOutputBytes": total_output,
            "aggregateWithinBounds": aggregate_within_bounds, "cleanupVerified": cleanup_verified,
            "boundary": INDEPENDENT_VERIFICATION_BOUNDARY,
        }

    def _invoke(self, command: str, run_id: str, run_dir: Path, value: dict[str, Any]) -> dict[str, Any]:
        if command not in {"planned-review", "review", "start", "run", "stop"} or RUN_RE.fullmatch(run_id) is None:
            raise evaluation.OutcomeError("Pixel system invocation is invalid")
        input_path = run_dir / f"pixel-system-{command}-input.json"
        output_path = run_dir / f"pixel-system-{command}-output.json"
        _private_json(input_path, value)
        result = subprocess.run([
            self.node_path, str(self.cli_path), command,
            "--config", str(self.configuration_path), "--input", str(input_path), "--output", str(output_path),
        ], capture_output=True, timeout=self.command_timeout_seconds)
        if result.returncode != 0:
            _write_control_failure_diagnostic(run_dir, command, result.stderr)
            detail = result.stderr.decode("utf-8", errors="replace")[-400:]
            raise evaluation.OutcomeError(f"Pixel system {command} failed: {detail}")
        output, _raw = evaluation.read_json(output_path, f"private Pixel system {command} output", private=True)
        return output

    def review_runtime(
        self, *, run_id: str, admission: dict[str, Any], model_contract_payload: bytes, inference_contract_payload: bytes,
        model_contract_sha256: str, inference_contract_sha256: str, run_dir: Path,
        task: dict[str, Any], request_payload: bytes, source_reference: dict[str, Any],
        environment: dict[str, Any], tool_policy: dict[str, Any], verifier_definition: dict[str, Any],
    ) -> dict[str, Any]:
        run_dir = Path(run_dir)
        if not run_dir.is_absolute() or not run_dir.is_dir():
            raise evaluation.OutcomeError("Pixel system review directory is unavailable")
        selected_profile = _admitted_profile(admission)
        return self._invoke("review", run_id, run_dir, {
            "schemaVersion": 1, "operation": "review", "runId": run_id, "profile": selected_profile,
            "now": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "idSuffix": run_id[-12:], "admission": admission, "task": task,
            "requestPayloadBase64": base64.b64encode(request_payload).decode("ascii"),
            "sourceReference": source_reference, "environment": environment,
            "toolPolicy": tool_policy, "verifierDefinition": verifier_definition,
            "modelContractBase64": base64.b64encode(model_contract_payload).decode("ascii"),
            "modelContractSha256": model_contract_sha256,
            "inferenceContractBase64": base64.b64encode(inference_contract_payload).decode("ascii"),
            "inferenceContractSha256": inference_contract_sha256,
        })

    def review_planned_runtime(
        self, *, run_id: str, admission: dict[str, Any], model_contract_payload: bytes, inference_contract_payload: bytes,
        model_contract_sha256: str, inference_contract_sha256: str, run_dir: Path,
        task: dict[str, Any], request_payload: bytes, source_reference: dict[str, Any],
        environment: dict[str, Any], tool_policy: dict[str, Any], verifier_definition: dict[str, Any],
    ) -> dict[str, Any]:
        run_dir = Path(run_dir)
        if not run_dir.is_absolute() or not run_dir.is_dir():
            raise evaluation.OutcomeError("Pixel planned review directory is unavailable")
        selected_profile = _admitted_profile(admission)
        return self._invoke("planned-review", run_id, run_dir, {
            "schemaVersion": 1, "operation": "planned-review", "runId": run_id, "profile": selected_profile,
            "now": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "idSuffix": run_id[-12:], "admission": admission, "task": task,
            "requestPayloadBase64": base64.b64encode(request_payload).decode("ascii"),
            "sourceReference": source_reference, "environment": environment,
            "toolPolicy": tool_policy, "verifierDefinition": verifier_definition,
            "modelContractBase64": base64.b64encode(model_contract_payload).decode("ascii"),
            "modelContractSha256": model_contract_sha256,
            "inferenceContractBase64": base64.b64encode(inference_contract_payload).decode("ascii"),
            "inferenceContractSha256": inference_contract_sha256,
        })

    def qualify_runtime(
        self, *, run_id: str, admission: dict[str, Any], model_contract: dict[str, Any],
        inference_contract: dict[str, Any], model_contract_payload: bytes,
        inference_contract_payload: bytes, model_contract_sha256: str,
        inference_contract_sha256: str, run_dir: Path,
    ) -> dict[str, Any]:
        selected_profile = _admitted_profile(admission)
        run_dir = Path(run_dir)
        self._run_dirs[run_id] = run_dir
        self._profiles[run_id] = selected_profile
        self._contracts[run_id] = (
            model_contract_payload, inference_contract_payload, model_contract_sha256,
            inference_contract_sha256, model_contract, inference_contract,
        )
        result = self._invoke("start", run_id, run_dir, {
            "schemaVersion": 1, "operation": "start", "runId": run_id, "profile": selected_profile,
            "modelContractBase64": base64.b64encode(model_contract_payload).decode("ascii"),
            "modelContractSha256": model_contract_sha256,
            "inferenceContractBase64": base64.b64encode(inference_contract_payload).decode("ascii"),
            "inferenceContractSha256": inference_contract_sha256,
        })
        self._started.add(run_id)
        return result

    def run_pixel(self, **options: Any) -> dict[str, Any]:
        run_id = options["run_id"]
        run_dir = Path(options["run_dir"])
        if run_id not in self._started or self._run_dirs.get(run_id) != run_dir:
            raise evaluation.OutcomeError("Pixel system run has no exact fresh runtime")
        if run_id not in self._runtime_controls:
            raise evaluation.OutcomeError("Pixel system run lacks its exact cold or warm runtime control")
        selected_profile = _admitted_profile(options.get("admission"))
        if self._profiles.get(run_id) != selected_profile or not isinstance(options.get("task"), dict) or options["task"].get("profile") != selected_profile:
            raise evaluation.OutcomeError("Pixel system run profile differs from its fresh qualified runtime")
        contracts = self._contracts.get(run_id)
        if contracts is None or evaluation.canonical(options["model_contract"]) != evaluation.canonical(contracts[4]) or evaluation.canonical(options["inference_contract"]) != evaluation.canonical(contracts[5]):
            raise evaluation.OutcomeError("Pixel system run contracts differ from its fresh runtime")
        source_path = run_dir / "pixel-system-source.tar"
        _private_new(source_path, options["source_payload"])
        source_reference = dict(options["source_reference"])
        source_reference["bytes"] = len(options["source_payload"])
        research_fixture_payload = options.get("research_fixture_payload")
        research_fixture_reference = options.get("research_fixture_reference")
        research_fixture_path = None
        if selected_profile == "researcher":
            if not isinstance(research_fixture_payload, bytes) or not isinstance(research_fixture_reference, dict):
                raise evaluation.OutcomeError("Pixel Researcher system run lacks its admitted offline fixture")
            research_fixture_path = run_dir / "pixel-system-research-fixture.json"
            _private_new(research_fixture_path, research_fixture_payload)
        elif research_fixture_payload is not None or research_fixture_reference is not None:
            raise evaluation.OutcomeError("non-Researcher Pixel system run received a research fixture")
        result = self._invoke("run", run_id, run_dir, {
            "schemaVersion": 1, "operation": "run", "runId": run_id,
            "now": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "idSuffix": run_id[-12:], "admission": options["admission"], "task": options["task"],
            "requestPayloadBase64": base64.b64encode(options["request_payload"]).decode("ascii"),
            "sourcePath": str(source_path), "sourceReference": source_reference,
            "researchFixturePath": str(research_fixture_path) if research_fixture_path is not None else None,
            "researchFixtureReference": research_fixture_reference,
            "environment": options["environment"], "toolPolicy": options["tool_policy"],
            "verifierDefinition": options["verifier_definition"],
            "modelContractBase64": base64.b64encode(contracts[0]).decode("ascii"),
            "modelContractSha256": contracts[2],
            "inferenceContractBase64": base64.b64encode(contracts[1]).decode("ascii"),
            "inferenceContractSha256": contracts[3],
        })
        if selected_profile == "assistant":
            preparation = _assistant_preparation(
                result, root=self.root, runtime_root=self.runtime_root, run_id=run_id,
                request_sha256=evaluation.sha256(options["request_payload"]), source_reference=source_reference,
            )
            started = time.monotonic()
            envelope = self.assistant_runner(
                root=self.root, run_root=Path(preparation["assistantRootPath"]),
                onboarding_path=Path(preparation["onboardingPath"]),
                proxy_config_path=Path(preparation["proxyConfigPath"]),
                proxy_receipt_path=Path(preparation["proxyReceiptPath"]),
                node_binary=Path(preparation["nodeBinaryPath"]),
                proxy_launcher_path=Path(preparation["proxyLauncherPath"]),
                request_payload=options["request_payload"], request_sha256=evaluation.sha256(options["request_payload"]),
                expected_provider="local", expected_model=contracts[4]["modelId"],
                chat_environment=preparation["chatEnvironment"],
                action_journal_roots=[Path(path) for path in preparation["actionJournalRoots"]],
            )
            latency_ms = int((time.monotonic() - started) * 1000)
            conversation = envelope.get("conversation") if isinstance(envelope, dict) else None
            selected = [] if not isinstance(conversation, dict) else [
                turn for turn in conversation.get("turns", []) if turn.get("turnId") == envelope.get("turnId")
            ]
            if len(selected) != 1 or selected[0].get("state") != "succeeded":
                raise evaluation.OutcomeError("Assistant execution did not retain one exact succeeded turn")
            turn = selected[0]
            final = envelope.get("modelProxyFinalReceipt")
            if not isinstance(final, dict) or final.get("planSha256") != preparation["planSha256"]:
                raise evaluation.OutcomeError("Assistant final inference receipt differs from its admitted plan")
            artifacts, workspace_delta = _collect_assistant_artifacts(
                workspace=Path(preparation["workspacePath"]), inventory_path=Path(preparation["sourceInventoryPath"]),
                source_reference=source_reference, source_tree_sha256=preparation["workspaceTreeSha256"],
                byte_ceiling=options["admission"]["budgets"]["artifactBytes"],
            )
            independent = self._verify_assistant_workspace(
                workspace=Path(preparation["workspacePath"]), definition=options["verifier_definition"],
                environment=options["environment"], admission=options["admission"], run_id=run_id,
                workspace_delta=workspace_delta,
            )
            assistant_evidence = {**envelope, "independentVerification": independent}
            return {
                "exitCode": 0, "latencyMs": latency_ms, "finalMessage": turn["assistantText"],
                "artifacts": artifacts, "independentVerification": independent,
                "usage": {
                    "modelRequests": final["modelRequests"], "inputTokens": final["inputTokens"],
                    "outputTokens": final["outputTokens"], "networkBytes": final["networkBytes"],
                    "toolCalls": turn["toolCalls"],
                },
                "authority": {"sourceMutation": False, "merge": False, "deploy": False, "externalEffects": False},
                "interaction": _assistant_interaction(envelope, turn),
                "assistantEvidence": assistant_evidence, "assistantPreparation": preparation,
                "workspaceDelta": workspace_delta,
            }
        output_fields = {
            "exitCode", "latencyMs", "finalMessage", "artifacts", "independentVerification", "usage", "authority", "interaction",
        }
        if selected_profile == "controller":
            output_fields.add("controllerEvidence")
        if selected_profile == "researcher":
            output_fields.add("researchEvidence")
            output_fields.add("researchRevisionReview")
            output_fields.add("researchRevisionHistory")
        result = evaluation.exact_fields(result, output_fields, "Pixel system run output")
        artifact_root = (self.runtime_root / run_id / "artifacts").resolve()
        artifacts = []
        seen: set[str] = set()
        for item in result["artifacts"]:
            item = evaluation.exact_fields(item, {
                "kind", "relativePath", "payloadPath", "bytes", "sha256",
            }, "Pixel system artifact output")
            if item["relativePath"] in seen:
                raise evaluation.OutcomeError("Pixel system artifact output is duplicated")
            seen.add(item["relativePath"])
            payload_path = Path(item["payloadPath"]).resolve()
            if payload_path.parent != artifact_root:
                raise evaluation.OutcomeError("Pixel system artifact output escaped its private run root")
            payload = _read_private_payload(payload_path, item["bytes"], item["sha256"])
            artifacts.append({"kind": item["kind"], "relativePath": item["relativePath"], "payload": payload})
        result["artifacts"] = artifacts
        return result

    def teardown_runtime(self, *, run_id: str, runtime: dict[str, Any] | None) -> None:
        del runtime
        run_dir = self._run_dirs.get(run_id)
        if run_dir is None:
            return
        try:
            state_path = self.runtime_root / run_id / "config" / "system-state.json"
            if run_id in self._started or state_path.is_file():
                result = self._invoke("stop", run_id, run_dir, {
                    "schemaVersion": 1, "operation": "stop", "runId": run_id,
                })
                result = evaluation.exact_fields(result, {
                    "schemaVersion", "operation", "runId", "backendRemoved", "privateRunStateRemoved", "externalEffects",
                }, "Pixel system stop output")
                if (
                    result["schemaVersion"] != 1 or result["operation"] != "pixel-portal-outcome-system-stop"
                    or result["runId"] != run_id or result["backendRemoved"] is not True
                    or result["privateRunStateRemoved"] is not True or result["externalEffects"] is not False
                ):
                    raise evaluation.OutcomeError("Pixel system teardown receipt is invalid")
        finally:
            self._started.discard(run_id)
            self._run_dirs.pop(run_id, None)
            self._contracts.pop(run_id, None)
            self._profiles.pop(run_id, None)
            self._runtime_controls.pop(run_id, None)
            for name in CONTROL_FILES:
                try:
                    (run_dir / name).unlink()
                except FileNotFoundError:
                    pass
