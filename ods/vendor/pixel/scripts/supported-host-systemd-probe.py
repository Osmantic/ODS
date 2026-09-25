#!/usr/bin/env python3
"""Exercise the Pixel appliance in a disposable, credential-free systemd VM."""

from __future__ import annotations

import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request


MAX_OUTPUT = 2 * 1024 * 1024
COMMIT_RE = re.compile(r"^[a-f0-9]{40}$")
HASH_RE = re.compile(r"^[a-f0-9]{64}$")
SENSITIVE_ENV = re.compile(
    r"(?:API[_-]?KEY|AUTH|BEARER|CREDENTIAL|PASSWORD|SECRET|SESSION|TOKEN)", re.IGNORECASE,
)


class QualificationError(RuntimeError):
    pass


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_private_directory(path: Path, source: Path) -> Path:
    if not path.is_absolute():
        raise QualificationError("evidence must be an absolute non-root path")
    resolved = path.resolve()
    if resolved == Path(resolved.anchor) or resolved == source or source in resolved.parents:
        raise QualificationError("evidence must remain outside the source snapshot")
    resolved.mkdir(parents=True, mode=0o700, exist_ok=False)
    resolved.chmod(0o700)
    return resolved


def assert_credential_free_environment() -> None:
    allowed = {"SUDO_COMMAND", "SUDO_GID", "SUDO_UID", "SUDO_USER"}
    inherited = sorted(
        key for key, value in os.environ.items()
        if value and key not in allowed and SENSITIVE_ENV.search(key)
    )
    if inherited:
        raise QualificationError("qualification inherited credential-like environment variables")


class Recorder:
    def __init__(self, evidence: Path, binding: dict[str, object]) -> None:
        self.evidence = evidence
        self.binding = binding
        self.binding_sha256 = hashlib.sha256(canonical(binding)).hexdigest()
        self.secrets: set[str] = set()
        self.records: list[dict[str, object]] = []
        self.index = 0

    def add_secret(self, value: str) -> None:
        if value:
            self.secrets.add(value)
            for log in self.evidence.glob("[0-9][0-9][0-9]-*.log"):
                body = log.read_text(encoding="utf-8", errors="replace")
                scrubbed = self._redact(body)
                if scrubbed != body:
                    log.write_text(scrubbed, encoding="utf-8")
                    log.chmod(0o600)

    def _redact(self, value: str) -> str:
        for secret in sorted(self.secrets, key=len, reverse=True):
            value = value.replace(secret, "[REDACTED-RUNTIME-CREDENTIAL]")
        return value

    def run(
        self,
        label: str,
        command: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout: int = 900,
        check: bool = True,
        retain_output: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        print(f"[host-probe] {label}", flush=True)
        started = time.monotonic()
        result = subprocess.run(
            command, cwd=cwd, env=env, capture_output=True, text=True,
            errors="replace", timeout=timeout, check=False,
        )
        elapsed = round(time.monotonic() - started, 3)
        self.index += 1
        detail = (
            f"STDOUT\n{result.stdout}\nSTDERR\n{result.stderr}"
            if retain_output else "OUTPUT\n[SUPPRESSED]"
        )
        body = self._redact(
            "EVIDENCE_BINDING "
            + json.dumps(self.binding, sort_keys=True, separators=(",", ":"))
            + f"\nexit={result.returncode}\nelapsedSeconds={elapsed}\n\n{detail}"
        )
        encoded = body.encode()
        if len(encoded) > MAX_OUTPUT:
            body = encoded[:MAX_OUTPUT].decode(errors="replace") + "\n[OUTPUT TRUNCATED]\n"
        log = self.evidence / f"{self.index:03d}-{label}.log"
        log.write_text(body, encoding="utf-8")
        log.chmod(0o600)
        self.records.append({
            "label": label,
            "exitCode": result.returncode,
            "elapsedSeconds": elapsed,
            "log": log.name,
            "evidenceBindingSha256": self.binding_sha256,
        })
        if check and result.returncode:
            raise QualificationError(f"{label} failed with exit {result.returncode}")
        return result


class SyntheticLocalServices:
    """Loopback-only SearXNG and OpenAI-compatible fixtures."""

    def __init__(self) -> None:
        self.available = True
        self.requests = 0
        self.write_schema_seen = False
        self.tool_result_seen = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *_args: object) -> None:
                return

            def respond(self, status: int, value: object) -> None:
                payload = json.dumps(value, separators=(",", ":")).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self) -> None:
                owner.requests += 1
                if not owner.available:
                    self.respond(503, {"error": "synthetic fixture unavailable"})
                elif self.path.startswith("/search?"):
                    self.respond(200, {"results": []})
                elif self.path == "/v1/models":
                    self.respond(200, {"object": "list", "data": [{"id": "pixel-qualification-local"}]})
                else:
                    self.respond(404, {"error": "not found"})

            def do_POST(self) -> None:
                owner.requests += 1
                if not owner.available:
                    self.respond(503, {"error": "synthetic fixture unavailable"})
                    return
                if self.path != "/v1/chat/completions":
                    self.respond(404, {"error": "not found"})
                    return
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > MAX_OUTPUT:
                    self.respond(400, {"error": "invalid request"})
                    return
                request = json.loads(self.rfile.read(length))
                tools = request.get("tools") or []
                write_tool = next(
                    (
                        item for item in tools
                        if item.get("type") == "function"
                        and item.get("function", {}).get("name") == "write"
                    ),
                    None,
                )
                owner.write_schema_seen = owner.write_schema_seen or write_tool is not None
                messages = request.get("messages") or []
                owner.tool_result_seen = owner.tool_result_seen or any(
                    item.get("role") == "tool" for item in messages
                )
                if owner.tool_result_seen:
                    delta = {"role": "assistant", "content": "PIXEL_HOST_QUALIFICATION_COMPLETE"}
                    finish = "stop"
                else:
                    properties = (write_tool or {}).get("function", {}).get("parameters", {}).get("properties", {})
                    path_key = "path" if "path" in properties else "file_path"
                    arguments = {
                        path_key: "host-qualification.txt",
                        "content": "pixel supported-host qualification\n",
                    }
                    delta = {
                        "role": "assistant",
                        "tool_calls": [{
                            "index": 0,
                            "id": "call_pixel_supported_host_write",
                            "type": "function",
                            "function": {
                                "name": "write",
                                "arguments": json.dumps(arguments, separators=(",", ":")),
                            },
                        }],
                    }
                    finish = "tool_calls"
                chunk = {
                    "id": f"chatcmpl-pixel-host-{owner.requests}",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "pixel-qualification-local",
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                }
                payload = f"data: {json.dumps(chunk, separators=(',', ':'))}\n\ndata: [DONE]\n\n".encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.server = ThreadingHTTPServer(("127.0.0.1", 19999), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def environment(source: Path) -> dict[str, str]:
    home = Path.home()
    env = {
        "HOME": str(home),
        "USER": os.environ.get("USER", "pixelqual"),
        "LOGNAME": os.environ.get("LOGNAME", os.environ.get("USER", "pixelqual")),
        "PATH": os.environ.get("PATH", "/opt/node/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "XDG_CONFIG_HOME": str(home / ".config"),
        "PIXEL_ROOT": str(source),
        "NPM_CONFIG_AUDIT": "false",
        "NPM_CONFIG_FUND": "false",
    }
    return env


def write_answers(source: Path) -> Path:
    home = Path.home()
    value = {
        "deploymentProfile": "prepared",
        "capabilityProfile": "minimal",
        "ownerName": "Qualification Owner",
        "organization": "Pixel Qualification",
        "deploymentName": "supported-host",
        "timeZone": "Etc/UTC",
        "agentId": "pixel",
        "agentName": "Pixel",
        "openclawBin": str(home / ".npm-global/bin/openclaw"),
        "openclawHome": str(home / ".openclaw"),
        "installDir": str(home / ".local/share/pixel"),
        "workspace": str(home / ".openclaw/workspace-pixel"),
        "modelProvider": "qualification-local",
        "modelId": "pixel-qualification-local",
        "modelName": "Synthetic Local Qualification",
        "modelBaseUrl": "http://127.0.0.1:19999/v1",
        "modelApiKey": "local-no-auth",
        "modelReasoning": False,
        "modelContextWindow": 65536,
        "modelMaxTokens": 1024,
        "searxngBaseUrl": "http://127.0.0.1:19999",
        "embeddingModel": "qualification-local.gguf",
        "embeddingCache": str(home / ".cache/pixel-qualification"),
        "googleAccount": "qualification@example.invalid",
        "calendarId": "primary",
        "gatewayPort": 19789,
        "gatewayExtensions": [],
        "emailLimbEnabled": False,
        "calendarLimbEnabled": False,
        "socialLimbEnabled": False,
        "webLimbEnabled": False,
        "operationsLimbEnabled": False,
        "frontierLimbEnabled": False,
    }
    output = source / ".qualification-answers.json"
    output.write_bytes(canonical(value))
    output.chmod(0o600)
    return output


def create_isolated_client_state(home: Path) -> Path:
    state = home / ".local/state/pixel-host-qualification/isolated-openclaw"
    try:
        state.mkdir(mode=0o700, parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise QualificationError("isolated client state must be new") from exc
    if state.is_symlink() or (os.name != "nt" and (state.stat().st_mode & 0o777) != 0o700):
        raise QualificationError("isolated client state is not private")
    return state


def validate_completed_sandbox_write(output: str) -> None:
    try:
        turn_result = json.loads(output)
    except json.JSONDecodeError as exc:
        raise QualificationError("sandboxed agent turn returned invalid JSON") from exc
    result = turn_result.get("result", {})
    tool_summary = result.get("meta", {}).get("toolSummary", {})
    if tool_summary.get("calls") != 1 or tool_summary.get("failures") != 0 or tool_summary.get("tools") != ["write"]:
        raise QualificationError("sandboxed write tool did not complete successfully")
    visible = [item.get("text") for item in result.get("payloads", []) if isinstance(item, dict)]
    if "PIXEL_HOST_QUALIFICATION_COMPLETE" not in visible:
        raise QualificationError("sandboxed agent turn did not complete")


def exercise_sandbox_turn(
    recorder: Recorder,
    env: dict[str, str],
    binary: Path,
    workspace: Path,
    model: SyntheticLocalServices,
) -> dict[str, object]:
    config = json.loads((Path.home() / ".openclaw/openclaw.json").read_text(encoding="utf-8"))
    token = config.get("gateway", {}).get("auth", {}).get("token")
    if not isinstance(token, str) or len(token) < 32:
        raise QualificationError("installed gateway token is unavailable")
    recorder.add_secret(token)
    parameters = json.dumps({
        "message": "Use the write tool to create host-qualification.txt with the exact requested qualification content.",
        "agentId": "pixel",
        "sessionKey": "agent:pixel:supported-host-qualification",
        "deliver": False,
        "timeout": 120,
        "idempotencyKey": "pixel-supported-host-qualification",
    }, separators=(",", ":"))
    command = [
        str(binary), "gateway", "call", "agent", "--url", "ws://127.0.0.1:19789",
        "--token", token, "--params", parameters, "--expect-final", "--json",
        "--timeout", "180000",
    ]
    isolated_state = create_isolated_client_state(Path.home())
    turn_env = {**env, "OPENCLAW_STATE_DIR": str(isolated_state)}
    invalid_token = "0" * 64 if token != "0" * 64 else "f" * 64
    recorder.add_secret(invalid_token)
    invalid_command = command.copy()
    invalid_command[invalid_command.index("--token") + 1] = invalid_token
    denial = recorder.run(
        "agent-gateway-token-denial", invalid_command, env=turn_env, timeout=30, check=False,
    )
    denial_output = f"{denial.stdout}\n{denial.stderr}".lower()
    if denial.returncode == 0 or "unauthorized: gateway token mismatch" not in denial_output:
        raise QualificationError("invalid shared gateway token was not explicitly denied")

    turn = recorder.run("sandboxed-agent-turn", command, env=turn_env, timeout=200)
    validate_completed_sandbox_write(turn.stdout)
    expected = workspace / "host-qualification.txt"
    if expected.read_text(encoding="utf-8") != "pixel supported-host qualification\n":
        raise QualificationError("sandboxed write did not reach the exact workspace target")
    if not model.write_schema_seen or not model.tool_result_seen:
        raise QualificationError("synthetic model did not observe the full write-tool contract")

    listed = recorder.run(
        "sandbox-container-list",
        ["docker", "ps", "-aq", "--filter", "name=^/pixel-sbx-agent-pixel-"],
        env=env,
    ).stdout.split()
    if len(listed) != 1:
        raise QualificationError("agent turn did not create exactly one Pixel sandbox")
    inspected = json.loads(recorder.run("sandbox-container-inspect", ["docker", "inspect", listed[0]], env=env).stdout)[0]
    host = inspected.get("HostConfig", {})
    mounts = inspected.get("Mounts", [])
    if not (
        inspected.get("State", {}).get("Running") is True
        and inspected.get("Config", {}).get("User") == "sandbox"
        and host.get("NetworkMode") == "none"
        and host.get("ReadonlyRootfs") is True
        and host.get("Privileged") is False
        and "ALL" in (host.get("CapDrop") or [])
        and "no-new-privileges" in (host.get("SecurityOpt") or [])
        and not (host.get("Devices") or [])
        and all(mount.get("Source") not in {"/", "/var/run/docker.sock"} for mount in mounts)
    ):
        raise QualificationError("live sandbox violated the runtime isolation contract")
    return {"workspaceToolTurn": "pass", "containerIsolation": "pass"}


def wait_for_url(url: str, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.25)
    raise QualificationError("local UI did not become ready")


def exercise_ui(recorder: Recorder, source: Path, env: dict[str, str]) -> None:
    command = [str(source / "pixel"), "ui", "--port", "43117", "--state", str(Path.home() / ".local/state/pixel-control")]
    process = subprocess.Popen(command, cwd=source, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        wait_for_url("http://127.0.0.1:43117/")
        with urllib.request.urlopen("http://127.0.0.1:43117/", timeout=5) as response:
            body = response.read(512 * 1024).decode("utf-8", "replace")
        if response.status != 200 or "Pixel" not in body or "Frontier" not in body:
            raise QualificationError("local owner UI did not render its operating surface")
        recorder.records.append({
            "label": "owner-ui-loopback",
            "exitCode": 0,
            "elapsedSeconds": 0,
            "log": None,
            "evidenceBindingSha256": recorder.binding_sha256,
        })
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def exercise_deep_work_endurance(
    recorder: Recorder,
    source: Path,
    env: dict[str, str],
    evidence: Path,
    source_commit: str,
    source_tree: str,
) -> None:
    output = evidence / "deep-work-real-crash-endurance.json"
    recorder.run(
        "deep-work-real-crash-endurance",
        [
            "node", str(source / "scripts/deep-work-endurance-probe.mjs"),
            "--output", str(output),
            "--source-commit", source_commit,
            "--source-tree", source_tree,
            "--milestones", "8",
            "--restart-delay-ms", "25",
        ],
        cwd=source,
        env=env,
        timeout=180,
    )
    value = json.loads(output.read_text(encoding="utf-8"))
    campaign = value.get("campaign", {})
    result = value.get("result", {})
    if not (
        value.get("status") == "pass"
        and value.get("sourceCommit") == source_commit
        and value.get("sourceTree") == source_tree
        and campaign.get("milestones") == 8
        and campaign.get("forcedAbruptExits") == 32
        and campaign.get("freshProcessRecoveries") == 32
        and campaign.get("processesLaunched") == 49
        and campaign.get("crashStates") == {"running": 8, "verifying": 8, "verified": 8, "completed": 8}
        and result.get("goalState") == "completed"
        and result.get("milestonesCompleted") == 8
        and result.get("jobsStarted") == 8
        and result.get("modelRequests") == 8
        and result.get("duplicateMilestoneCredit") is False
        and result.get("duplicateUsageCredit") is False
        and result.get("replayedChildExecution") is False
        and value.get("privacy") == {
            "providerCalls": 0,
            "credentialInputs": 0,
            "networkRequests": 0,
            "externalEffects": 0,
            "productionDeploymentsTouched": 0,
        }
    ):
        raise QualificationError("Deep Work real-process endurance evidence is incomplete")


def _systemd_service_start(unit: str) -> tuple[int, str, str, str]:
    result = subprocess.run(
        ["systemctl", "show", unit, "--property=ExecMainStartTimestampMonotonic", "--property=ActiveState", "--property=Result", "--property=ExecMainStatus"],
        capture_output=True, text=True, errors="replace", timeout=10, check=False,
    )
    fields = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            fields[key] = value
    try:
        started = int(fields.get("ExecMainStartTimestampMonotonic", "0"))
    except ValueError:
        started = 0
    return started, fields.get("ActiveState", ""), fields.get("Result", ""), fields.get("ExecMainStatus", "")


def wait_for_systemd_service_success(unit: str, *, after: int = -1, timeout: float = 45) -> int:
    if re.fullmatch(r"pixel-work-workgoal-[0-9]{13}-[a-f0-9]{12}\.service", unit) is None:
        raise QualificationError("Deep Work qualification service identity is invalid")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        started, active, result, status = _systemd_service_start(unit)
        if started > after and active == "inactive" and result == "success" and status == "0":
            return started
        if started > after and active == "failed":
            raise QualificationError("Deep Work qualification service entered a failed state")
        time.sleep(0.2)
    raise QualificationError("Deep Work qualification service did not converge after its durable event")


def exercise_deep_work_service_supervision(
    recorder: Recorder,
    install_root: Path,
    env: dict[str, str],
    source_commit: str,
    source_tree: str,
) -> None:
    import grp
    import pwd

    service_user = pwd.getpwuid(os.geteuid()).pw_name
    service_group = grp.getgrgid(os.getegid()).gr_name
    if service_user == "root" or service_group == "root" or re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", service_user) is None or re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", service_group) is None:
        raise QualificationError("Deep Work qualification requires a safe unprivileged service identity")
    suffix = f"{source_commit[:7]}-{os.getpid()}"
    if re.fullmatch(r"[a-f0-9]{7}-[0-9]+", suffix) is None:
        raise QualificationError("Deep Work qualification suffix is invalid")
    private_root = Path(f"/var/lib/pixel-goal-service-qualification-{suffix}")
    install_info = install_root.stat()
    if (
        install_root.resolve() != install_root or install_root.parent != Path("/opt")
        or not install_root.is_dir() or install_root.is_symlink() or install_info.st_uid != 0
        or install_info.st_mode & 0o022 or any(path.is_symlink() for path in install_root.rglob("*"))
        or private_root.exists()
    ):
        raise QualificationError("Deep Work qualification exact source or private root is unsafe")
    recorder.run(
        "deep-work-service-private-root",
        ["sudo", "install", "-d", "-o", service_user, "-g", service_group, "-m", "0700", str(private_root)],
        env=env,
    )
    prepared = recorder.run(
        "deep-work-service-prepare-paused",
        [
            "node", str(install_root / "scripts/deep-work-service-qualification.mjs"), "prepare",
            "--root", str(private_root), "--install-root", str(install_root),
            "--service-user", service_user, "--service-group", service_group, "--docker-group", "docker",
            "--source-commit", source_commit, "--source-tree", source_tree,
        ],
        cwd=install_root, env=env,
    )
    prepared_value = json.loads(prepared.stdout)
    if not (
        prepared_value.get("status") == "prepared-paused"
        and prepared_value.get("executionModel") == "durable-event-driven"
        and prepared_value.get("watchdogRole") == "liveness-only"
        and prepared_value.get("jobsStarted") == 0
        and prepared_value.get("authority") == {"grantsExecution": False, "grantsLease": False, "grantsExternalEffects": False}
    ):
        raise QualificationError("Deep Work qualification did not prepare its non-executing boundary")
    qualification = json.loads((private_root / "qualification.json").read_text(encoding="utf-8"))
    manifest = qualification.get("manifestSha256")
    service = qualification.get("serviceName")
    timer = qualification.get("timerName")
    path_unit = qualification.get("pathName")
    config = qualification.get("configPath")
    bundle = qualification.get("bundlePath")
    if not (
        HASH_RE.fullmatch(str(manifest)) and isinstance(service, str) and isinstance(timer, str) and isinstance(path_unit, str)
        and isinstance(config, str) and isinstance(bundle, str)
    ):
        raise QualificationError("Deep Work qualification private service receipt is invalid")

    recorder.run("deep-work-service-bundle-inspect", [str(install_root / "pixel"), "work-service", "inspect", "--bundle", bundle], cwd=install_root, env=env)
    recorder.run(
        "deep-work-service-install-inactive",
        ["sudo", str(install_root / "pixel"), "work-service", "install", "--bundle", bundle, "--confirm-manifest-sha256", manifest],
        env=env,
    )
    for label, unit in (("path", path_unit), ("timer", timer)):
        observed = recorder.run(f"deep-work-service-{label}-inactive", ["systemctl", "show", unit, "--property=UnitFileState", "--value"], env=env)
        if observed.stdout.strip() != "disabled":
            raise QualificationError(f"Deep Work qualification {label} was enabled by installation")
    recorder.run(
        "deep-work-service-activate",
        ["sudo", str(install_root / "pixel"), "work-service", "activate", "--bundle", bundle, "--confirm-manifest-sha256", manifest],
        env=env,
    )
    for label, unit in (("path", path_unit), ("timer", timer)):
        recorder.run(f"deep-work-service-{label}-enabled", ["systemctl", "is-enabled", "--quiet", unit], env=env)
        recorder.run(f"deep-work-service-{label}-active", ["systemctl", "is-active", "--quiet", unit], env=env)
    recorder.run("deep-work-service-watchdog-isolate-path", ["sudo", "systemctl", "stop", timer], env=env)
    stopped_watchdog = recorder.run("deep-work-service-watchdog-inactive", ["systemctl", "show", timer, "--property=ActiveState", "--value"], env=env)
    if stopped_watchdog.stdout.strip() != "inactive":
        raise QualificationError("Deep Work qualification could not isolate its event path from the watchdog")
    recorder.run("deep-work-service-path-remains-active", ["systemctl", "is-active", "--quiet", path_unit], env=env)
    initial_start = wait_for_systemd_service_success(service)
    recorder.run(
        "deep-work-service-initial-success",
        ["systemctl", "show", service, "--property=ExecMainStartTimestampMonotonic", "--property=ActiveState", "--property=Result", "--property=ExecMainStatus"],
        env=env,
    )
    recorder.run(
        "deep-work-service-paused-state",
        ["node", str(install_root / "scripts/deep-work-service-qualification.mjs"), "inspect", "--root", str(private_root), "--expected-state", "paused"],
        cwd=install_root, env=env,
    )
    reviewed = recorder.run("deep-work-service-cancel-review", [str(install_root / "pixel"), "work-cancel", "review", "--config", config], cwd=install_root, env=env)
    review_value = json.loads(reviewed.stdout)
    confirmation = review_value.get("confirmation", {}).get("sha256")
    if review_value.get("action") != "confirmation-required" or not HASH_RE.fullmatch(str(confirmation)):
        raise QualificationError("Deep Work qualification cancellation review is not exact-confirmed")
    recorder.run(
        "deep-work-service-cancel-apply",
        [str(install_root / "pixel"), "work-cancel", "apply", "--config", config, "--confirm-review-sha256", confirmation],
        cwd=install_root, env=env,
    )
    event_start = wait_for_systemd_service_success(service, after=initial_start)
    if event_start <= initial_start:
        raise QualificationError("Deep Work qualification did not observe a new event-driven service run")
    recorder.run(
        "deep-work-service-event-success",
        ["systemctl", "show", service, "--property=ExecMainStartTimestampMonotonic", "--property=ActiveState", "--property=Result", "--property=ExecMainStatus"],
        env=env,
    )
    recorder.run(
        "deep-work-service-cancelled-state",
        ["node", str(install_root / "scripts/deep-work-service-qualification.mjs"), "inspect", "--root", str(private_root), "--expected-state", "cancelled"],
        cwd=install_root, env=env,
    )
    recorder.run(
        "deep-work-service-remove",
        ["sudo", str(install_root / "pixel"), "work-service", "remove", "--bundle", bundle, "--confirm-manifest-sha256", manifest],
        env=env,
    )
    for label, unit in (("service", service), ("path", path_unit), ("timer", timer)):
        recorder.run(f"deep-work-service-{label}-removed", ["sudo", "test", "!", "-e", f"/etc/systemd/system/{unit}"], env=env)
    recorder.run("deep-work-service-private-state-retained", ["test", "-d", str(private_root / "state")], env=env)
    recorder.run(
        "deep-work-service-post-remove-state",
        ["node", str(install_root / "scripts/deep-work-service-qualification.mjs"), "inspect", "--root", str(private_root), "--expected-state", "cancelled"],
        cwd=install_root, env=env,
    )


def exercise_capability_pack_runtime(recorder: Recorder, source: Path, env: dict[str, str]) -> None:
    result = recorder.run(
        "capability-pack-live-runtime",
        ["node", "--test", "tests/work-capability-image-live.mjs"],
        cwd=source,
        env={**env, "PIXEL_LIVE_DOCKER": "1"},
        timeout=240,
    )
    if not all(marker in result.stdout for marker in (
        "a real local OCI image completes the disabled admission and exact revocation lifecycle",
        "# pass 1", "# fail 0", "# skipped 0",
    )):
        raise QualificationError("capability-pack live runtime evidence is incomplete or skipped")


def exercise_backup_restore(recorder: Recorder, source: Path, env: dict[str, str], workspace: Path) -> None:
    private = Path.home() / ".local/state/pixel-host-qualification"
    private.mkdir(mode=0o700, parents=True, exist_ok=True)
    deep_root = private / f"deep-work-{os.getpid()}"
    work_state = deep_root / "state"
    work_config = deep_root / "config"
    vault = deep_root / "knowledge-vault"
    key_root = private / f"knowledge-keys-{os.getpid()}"
    historical_key = key_root / "historical/pixel-knowledge-vault-key"
    current_key = key_root / "current/pixel-knowledge-vault-key"
    for directory in (work_state, work_config, historical_key.parent, current_key.parent):
        directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        directory.chmod(0o700)
    historical_value = os.urandom(32).hex()
    current_value = os.urandom(32).hex()
    recorder.add_secret(historical_value)
    recorder.add_secret(current_value)
    historical_key.write_text(historical_value + "\n", encoding="ascii")
    current_key.write_text(current_value + "\n", encoding="ascii")
    historical_key.chmod(0o600)
    current_key.chmod(0o600)
    (work_state / "state-marker.json").write_text('{"state":"before-backup"}\n', encoding="utf-8")
    (work_config / "config-marker.json").write_text('{"configuration":"before-backup"}\n', encoding="utf-8")
    (work_state / "state-marker.json").chmod(0o600)
    (work_config / "config-marker.json").chmod(0o600)
    import grp
    import pwd
    service_user = pwd.getpwuid(os.geteuid()).pw_name
    service_group = grp.getgrgid(os.getegid()).gr_name
    if service_user == "root" or service_group == "root":
        raise QualificationError("knowledge backup recovery must run under the non-root deployment owner")
    vault_id = "knowledgevault-a1b2c3d4e5f6"
    deep_env = {
        **env,
        "PIXEL_DEEP_WORK_BACKUP_ENABLED": "1",
        "PIXEL_WORK_STATE_ROOT": str(work_state),
        "PIXEL_WORK_CONFIG_ROOT": str(work_config),
        "PIXEL_KNOWLEDGE_VAULT_ROOT": str(vault),
        "PIXEL_KNOWLEDGE_VAULT_CREDENTIAL": str(current_key),
        "PIXEL_KNOWLEDGE_VAULT_ID": vault_id,
        "PIXEL_WORK_SERVICE_USER": service_user,
        "PIXEL_WORK_SERVICE_GROUP": service_group,
    }
    fixture = source / "tests/fixtures/work/knowledge-backup-probe.mjs"
    recorder.run(
        "knowledge-vault-backup-initialize",
        ["node", str(fixture), "initialize", "--vault", str(vault), "--vault-id", vault_id, "--historical-key", str(historical_key)],
        cwd=source, env=deep_env,
    )
    node = shutil.which("node")
    if node is None or not Path(node).is_absolute():
        raise QualificationError("knowledge credential projection requires an absolute Node runtime")
    credential_unit = f"pixel-work-knowledge-credential-qualification-{os.getpid()}"
    projected = recorder.run(
        "knowledge-vault-systemd-credential",
        [
            "sudo", "systemd-run", f"--unit={credential_unit}", "--wait", "--collect", "--pipe", "--quiet",
            "--property=Type=oneshot", f"--property=User={service_user}", f"--property=Group={service_group}",
            f"--property=WorkingDirectory={source}", f"--property=LoadCredential=pixel-knowledge-vault-key:{historical_key}",
            "--property=NoNewPrivileges=yes", "--property=PrivateNetwork=yes", "--property=PrivateDevices=yes",
            "--property=ProtectSystem=strict", "--property=ProtectHome=read-only", "--property=ProtectKernelTunables=yes",
            "--property=ProtectKernelModules=yes", "--property=ProtectControlGroups=yes", "--property=RestrictSUIDSGID=yes",
            "--property=LockPersonality=yes", "--property=RestrictAddressFamilies=AF_UNIX", "--property=CapabilityBoundingSet=",
            node, str(fixture), "credential-audit", "--vault", str(vault), "--vault-id", vault_id,
        ],
        cwd=source, env=deep_env,
    )
    if '"credentialProjected":true' not in projected.stdout:
        raise QualificationError("systemd did not project the external knowledge credential into the unprivileged worker")
    identity = private / "age-identity"
    recorder.run("backup-age-identity", ["age-keygen", "-o", str(identity)], env=env, retain_output=False)
    identity.chmod(0o600)
    recipient = recorder.run("backup-age-recipient", ["age-keygen", "-y", str(identity)], env=env).stdout.strip()
    if not recipient.startswith("age1"):
        raise QualificationError("age recipient generation failed")
    marker = workspace / "recovery-marker.txt"
    marker.write_text("before-backup\n", encoding="utf-8")
    marker.chmod(0o600)
    backup_dir = private / "backups"
    created = recorder.run(
        "private-backup-create",
        [str(source / "pixel"), "backup", str(backup_dir), recipient],
        cwd=source,
        env=deep_env,
        timeout=600,
    )
    archive = Path(created.stdout.strip().splitlines()[-1])
    if not archive.is_file() or archive.parent != backup_dir:
        raise QualificationError("backup did not return its bounded archive")
    recorder.run(
        "private-backup-validate",
        [str(source / "pixel"), "restore", str(archive), "--identity", str(identity), "--validate-only"],
        cwd=source,
        env=deep_env,
    )
    rehearsal = private / "rehearsal"
    recorder.run(
        "private-backup-rehearse",
        [str(source / "pixel"), "restore", str(archive), "--identity", str(identity), "--rehearse", str(rehearsal)],
        cwd=source,
        env=deep_env,
    )
    marker.write_text("after-backup\n", encoding="utf-8")
    recorder.run(
        "knowledge-vault-delete-and-rotate",
        ["node", str(fixture), "delete-rotate", "--vault", str(vault), "--vault-id", vault_id, "--historical-key", str(historical_key), "--current-key", str(current_key)],
        cwd=source, env=deep_env,
    )
    restore_env = {**deep_env, "PIXEL_BACKUP_AGE_RECIPIENT": recipient}
    recorder.run(
        "private-backup-restore",
        [
            str(source / "pixel"), "restore", str(archive), "--identity", str(identity), "--replace", "--confirm",
            "--knowledge-vault-id", vault_id, "--restored-knowledge-key", str(historical_key), "--current-knowledge-key", str(current_key),
        ],
        cwd=source,
        env=restore_env,
        timeout=900,
    )
    if marker.read_text(encoding="utf-8") != "before-backup\n":
        raise QualificationError("private-state restore did not recover the exact marker")
    verified = recorder.run(
        "knowledge-vault-backup-recovery-verify",
        ["node", str(fixture), "verify", "--vault", str(vault), "--vault-id", vault_id, "--current-key", str(current_key)],
        cwd=source, env=deep_env,
    )
    if '"reconciled":true' not in verified.stdout or '"rotated":true' not in verified.stdout:
        raise QualificationError("knowledge-vault historical restore did not prove tombstone reconciliation and current-key rotation")


def exercise_release_identity_refusal(
    recorder: Recorder, source: Path, env: dict[str, str], install_dir: Path,
) -> None:
    version = (source / "VERSION").read_text(encoding="ascii").strip()
    if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) is None:
        raise QualificationError("release version is unsafe for identity-refusal rehearsal")
    release = install_dir / "releases" / version
    invalid = install_dir / "releases" / f"{version}-qualification-invalid"
    current = install_dir / "current"
    if not release.is_dir() or release.is_symlink() or invalid.exists() or current.resolve() != release.resolve():
        raise QualificationError("installed release cannot be prepared for identity-refusal rehearsal")

    # The supported-host lane previously renamed a release to an invalid directory and
    # then claimed a successful rollback rehearsal. Exact rollback identity now correctly
    # refuses that state. Exercise the refusal on real systemd/Docker, prove that apply did
    # not mutate the live pointer, and restore the disposable host before verifying it.
    refusal: subprocess.CompletedProcess[str] | None = None
    identity_state_modified = False
    try:
        release.rename(invalid)
        identity_state_modified = True
        temporary = install_dir / ".current.qualification-invalid"
        temporary.symlink_to(invalid)
        temporary.replace(current)
        recorder.run("release-identity-refusal-plan", [str(source / "pixel"), "plan"], cwd=source, env=env)
        refusal = recorder.run(
            "release-identity-refusal-apply",
            [str(source / "pixel"), "apply", "--confirm"],
            cwd=source,
            env=env,
            timeout=1200,
            check=False,
        )
        if refusal.returncode == 0 or "Current release directory does not match its version identity" not in refusal.stderr:
            raise QualificationError("apply did not fail closed on a mismatched release directory identity")
        if current.resolve() != invalid.resolve() or release.exists() or not invalid.is_dir():
            raise QualificationError("identity refusal mutated the active release pointer")
    finally:
        # Repair only the disposable qualification host. A production mismatch remains a
        # fail-closed operator condition; the product never performs this recovery itself.
        if identity_state_modified:
            if invalid.is_dir() and not invalid.is_symlink() and not release.exists():
                invalid.rename(release)
            if release.is_dir() and not release.is_symlink():
                repair = install_dir / ".current.qualification-repair"
                repair.unlink(missing_ok=True)
                repair.symlink_to(release)
                repair.replace(current)
    if refusal is None:
        raise QualificationError("release identity refusal was not observed")
    recorder.run("post-identity-refusal-verify", [str(source / "pixel"), "verify"], cwd=source, env=env)


def verify_no_secret(evidence: Path, values: set[str]) -> None:
    for path in evidence.rglob("*"):
        if path.is_file():
            payload = path.read_bytes()
            if any(value.encode() in payload for value in values if value):
                raise QualificationError("retained evidence contains a runtime credential")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--immutable-source", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--lane", required=True, choices=("ubuntu-24.04-systemd", "debian-12-systemd"))
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--expected-os-id", required=True, choices=("ubuntu", "debian"))
    parser.add_argument("--expected-os-version", required=True, choices=("24.04", "12"))
    return parser.parse_args()


def main() -> int:
    os.umask(0o077)
    args = parse_args()
    assert_credential_free_environment()
    source = args.source.resolve()
    immutable_source = args.immutable_source.resolve()
    if args.immutable_source != immutable_source or not (source / "pixel").is_file() or not (immutable_source / "pixel").is_file() or not COMMIT_RE.fullmatch(args.source_commit) or not COMMIT_RE.fullmatch(args.source_tree):
        raise QualificationError("qualification source identity is invalid")
    evidence = require_private_directory(args.evidence, source)
    manifest_path = source / "RELEASE-MANIFEST.json"
    manifest_sha = sha256_file(manifest_path)
    if not HASH_RE.fullmatch(manifest_sha):
        raise QualificationError("release manifest identity is invalid")
    os_release = {}
    for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            os_release[key] = value.strip('"')
    if os_release.get("ID") != args.expected_os_id or os_release.get("VERSION_ID") != args.expected_os_version:
        raise QualificationError("guest operating system differs from the selected lane")
    binding = {
        "sourceCommit": args.source_commit,
        "sourceTree": args.source_tree,
        "releaseManifestSha256": manifest_sha,
        "lane": args.lane,
    }
    recorder = Recorder(evidence, binding)
    env = environment(source)
    answers = write_answers(source)
    model = SyntheticLocalServices()
    model.start()
    checks: dict[str, str] = {}
    try:
        recorder.run("bootstrap-pinned-runtime", [str(source / "pixel"), "bootstrap", "--apply"], cwd=source, env=env, timeout=2400)
        binary = Path.home() / ".npm-global/bin/openclaw"
        if not binary.is_file():
            raise QualificationError("pinned OpenClaw binary was not installed")
        recorder.run("configure-minimal", [str(source / "pixel"), "configure", "--answers", str(answers)], cwd=source, env=env)
        answers.unlink()
        recorder.run("deployment-plan", [str(source / "pixel"), "plan"], cwd=source, env=env, timeout=900)
        recorder.run("deployment-apply", [str(source / "pixel"), "apply", "--confirm"], cwd=source, env=env, timeout=1200)
        recorder.run("deployment-verify", [str(source / "pixel"), "verify"], cwd=source, env=env)
        checks["install-apply-verify"] = "pass"
        checks["service-isolation"] = "pass"

        exercise_deep_work_endurance(
            recorder, source, env, evidence, args.source_commit, args.source_tree,
        )
        checks["deep-work-real-crash-endurance"] = "pass"

        exercise_deep_work_service_supervision(
            recorder, immutable_source, env, args.source_commit, args.source_tree,
        )
        checks["deep-work-supervised-service"] = "pass"

        exercise_capability_pack_runtime(recorder, source, env)
        checks["capability-pack-live-runtime"] = "pass"

        workspace = Path.home() / ".openclaw/workspace-pixel"
        sandbox = exercise_sandbox_turn(recorder, env, binary, workspace, model)
        checks["sandbox-isolation"] = "pass"

        exercise_ui(recorder, source, env)
        checks["owner-ui-reachability"] = "pass"

        model.available = False
        degraded = recorder.run("degraded-model-detected", [str(source / "pixel"), "verify"], cwd=source, env=env, check=False)
        if degraded.returncode == 0:
            raise QualificationError("verification reported green while its model dependency was unavailable")
        model.available = True
        recorder.run("degraded-model-recovered", [str(source / "pixel"), "verify"], cwd=source, env=env)
        recorder.run("service-stop", ["sudo", "systemctl", "stop", "openclaw-gateway.service"], env=env)
        stopped = recorder.run("degraded-service-detected", [str(source / "pixel"), "verify"], cwd=source, env=env, check=False)
        if stopped.returncode == 0:
            raise QualificationError("verification reported green while the gateway was stopped")
        recorder.run("service-restart", ["sudo", "systemctl", "restart", "openclaw-gateway.service"], env=env)
        recorder.run("service-recovered", [str(source / "pixel"), "verify"], cwd=source, env=env)
        checks["degraded-recovery"] = "pass"

        exercise_backup_restore(recorder, source, env, workspace)
        recorder.run("post-restore-verify", [str(source / "pixel"), "verify"], cwd=source, env=env)
        checks["backup-recovery"] = "pass"
        checks["knowledge-vault-backup-recovery"] = "pass"

        install_dir = Path.home() / ".local/share/pixel"
        exercise_release_identity_refusal(recorder, source, env, install_dir)
        checks["release-identity-refusal"] = "pass"
        recorder.run("disposable-remove-stop", ["sudo", "systemctl", "disable", "--now", "openclaw-gateway.service"], env=env)
        recorder.run("disposable-remove-unit", ["sudo", "rm", "-f", "/etc/systemd/system/openclaw-gateway.service"], env=env)
        recorder.run("disposable-remove-reload", ["sudo", "systemctl", "daemon-reload"], env=env)
        inactive = recorder.run("disposable-remove-inactive", ["systemctl", "is-active", "openclaw-gateway.service"], env=env, check=False)
        if inactive.returncode == 0:
            raise QualificationError("gateway remained active after disposable removal")
        checks["disposable-gateway-removal"] = "pass"
    finally:
        answers.unlink(missing_ok=True)
        model.stop()

    required = {
        "install-apply-verify", "service-isolation", "sandbox-isolation", "deep-work-real-crash-endurance", "deep-work-supervised-service", "capability-pack-live-runtime", "backup-recovery", "knowledge-vault-backup-recovery",
        "release-identity-refusal", "disposable-gateway-removal", "degraded-recovery", "owner-ui-reachability",
    }
    if set(checks) != required or any(value != "pass" for value in checks.values()):
        raise QualificationError("supported-host checks are incomplete")
    summary = {
        "$schema": "./schemas/supported-host-systemd-evidence-v1.schema.json",
        "schemaVersion": 1,
        "operation": "pixel-supported-host-systemd-lane",
        "status": "pass",
        "lane": args.lane,
        "sourceCommit": args.source_commit,
        "sourceTree": args.source_tree,
        "releaseManifestSha256": manifest_sha,
        "evidenceBindingSha256": recorder.binding_sha256,
        "environment": {"os": {"id": args.expected_os_id, "version": args.expected_os_version}, "serviceManager": "systemd"},
        "checks": checks,
        "synthetic": {
            "providerCalls": 0,
            "credentialInputs": 0,
            "loopbackModelRequests": model.requests,
            **sandbox,
        },
        "commands": recorder.records,
    }
    output = evidence / "supported-host-systemd-lane.json"
    output.write_bytes(canonical(summary))
    output.chmod(0o600)
    verify_no_secret(evidence, recorder.secrets)
    print(json.dumps({"status": "pass", "lane": args.lane, "sha256": sha256_file(output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (QualificationError, OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError) as error:
        print(f"supported-host qualification failed: {error}", file=sys.stderr)
        raise SystemExit(1)
