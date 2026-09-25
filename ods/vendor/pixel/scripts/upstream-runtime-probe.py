#!/usr/bin/env python3
"""Qualify exact supported and candidate OpenClaw packages on a real runtime.

This program is intended for a disposable Linux guest. It never reads production
state or credentials. Package archives are verified before installation, lifecycle
scripts are disabled for the core runtime install, and all retained evidence is
secret-scanned before a passing result is emitted.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
try:
    import pwd
except ModuleNotFoundError:  # The probe itself is Linux-only; helpers remain unit-testable elsewhere.
    pwd = None
import secrets
import shutil
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request


MAX_COMMAND_OUTPUT = 2 * 1024 * 1024
EXPECTED_SOURCE_TOOLS = {
    "pixel_limb_status",
    "pixel_gmail_inbox", "pixel_gmail_sent", "pixel_gmail_search", "pixel_gmail_read", "pixel_gmail_thread",
    "pixel_calendar_list", "pixel_calendar_get", "pixel_calendar_propose_create",
    "pixel_calendar_propose_update", "pixel_calendar_propose_delete",
}
DISABLED_TOOLS = {"pixel_social_feed", "pixel_social_search", "web_search", "web_fetch"}


def phase(message: str) -> None:
    print(f"[probe] {message}", flush=True)


def fail(message: str) -> None:
    raise RuntimeError(message)


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def private_directory(path: Path, *, exclusive: bool = False) -> None:
    if exclusive:
        path.mkdir(mode=0o700, parents=True, exist_ok=False)
    else:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)


def make_service_readable(path: Path) -> None:
    """Expose a root-owned code tree as read/execute-only to the service user."""
    for root, directories, files in os.walk(path):
        Path(root).chmod(0o755)
        for name in directories:
            child = Path(root) / name
            if not child.is_symlink():
                child.chmod(0o755)
        for name in files:
            child = Path(root) / name
            if not child.is_symlink():
                mode = child.stat().st_mode
                child.chmod(0o755 if mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH) else 0o644)


class Recorder:
    def __init__(self, evidence: Path, secret: str, binding: dict[str, object] | None = None):
        self.evidence = evidence
        self.secrets = {secret}
        self.binding = binding or {}
        self.binding_sha256 = hashlib.sha256(canonical(self.binding)).hexdigest()
        self.index = 0
        self.records: list[dict[str, object]] = []

    def add_secret(self, secret: str) -> None:
        if secret:
            self.secrets.add(secret)

    def diagnostic(self, label: str, detail: str) -> Path:
        phase(label)
        self.index += 1
        for secret in sorted(self.secrets, key=len, reverse=True):
            detail = detail.replace(secret, "[REDACTED-RUNTIME-CREDENTIAL]")
        binding_line = json.dumps(self.binding, sort_keys=True, separators=(",", ":"))
        log = self.evidence / f"{self.index:03d}-{label}.log"
        log.write_text(f"EVIDENCE_BINDING {binding_line}\n{detail[:MAX_COMMAND_OUTPUT]}\n", encoding="utf-8")
        log.chmod(0o600)
        self.records.append({
            "label": label,
            "exitCode": None,
            "log": log.name,
            "evidenceBindingSha256": self.binding_sha256,
        })
        return log

    def run(
        self,
        label: str,
        command: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout: int = 600,
        check: bool = True,
        record_output: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        phase(label)
        self.index += 1
        result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, errors="replace", timeout=timeout)
        detail = f"STDOUT\n{result.stdout}\nSTDERR\n{result.stderr}" if record_output else "OUTPUT\n[SUPPRESSED]"
        binding_line = json.dumps(self.binding, sort_keys=True, separators=(",", ":"))
        output = f"EVIDENCE_BINDING {binding_line}\n$ {' '.join(command)}\nexit={result.returncode}\n\n{detail}"
        for secret in sorted(self.secrets, key=len, reverse=True):
            output = output.replace(secret, "[REDACTED-RUNTIME-CREDENTIAL]")
        if len(output.encode()) > MAX_COMMAND_OUTPUT:
            output = output.encode()[:MAX_COMMAND_OUTPUT].decode(errors="replace") + "\n[OUTPUT TRUNCATED]\n"
        log = self.evidence / f"{self.index:03d}-{label}.log"
        log.write_text(output, encoding="utf-8")
        log.chmod(0o600)
        self.records.append({
            "label": label,
            "exitCode": result.returncode,
            "log": log.name,
            "evidenceBindingSha256": self.binding_sha256,
        })
        if check and result.returncode:
            fail(f"{label} failed with exit {result.returncode}; see {log}")
        return result


def require_safe_external(path: Path, source: Path, label: str) -> Path:
    if not path.is_absolute():
        fail(f"{label} must be an absolute, non-root path outside the source repository")
    resolved = path.resolve()
    if resolved == Path(resolved.anchor) or resolved == source or source in resolved.parents:
        fail(f"{label} must be an absolute, non-root path outside the source repository")
    return resolved


def package_records(manifest: dict[str, object]) -> list[dict[str, str]]:
    plugins = manifest["openclawPlugins"]
    packages = manifest["openclawPluginPackages"]
    assert isinstance(plugins, dict) and isinstance(packages, dict)
    return [
        {"key": "openclaw", "name": "openclaw", "version": str(manifest["openclaw"]), **manifest["openclawPackage"]},
        {"key": "discord", "name": "@openclaw/discord", "version": str(plugins["@openclaw/discord"]), **packages["discord"]},
        {"key": "searxng", "name": "@openclaw/searxng-plugin", "version": str(plugins["@openclaw/searxng-plugin"]), **packages["searxng"]},
        {"key": "llamaCpp", "name": "@openclaw/llama-cpp-provider", "version": str(plugins["@openclaw/llama-cpp-provider"]), **packages["llamaCpp"]},
    ]


def locate_and_verify_archives(manifest: dict[str, object], directory: Path) -> dict[str, Path]:
    candidates = sorted(directory.glob("*.tgz"))
    output: dict[str, Path] = {}
    for record in package_records(manifest):
        matches = [path for path in candidates if sha256_file(path) == record["sha256"]]
        if len(matches) != 1:
            fail(f"expected exactly one verified archive for {record['name']}@{record['version']} in {directory}")
        path = matches[0]
        observed_integrity = "sha512-" + base64.b64encode(hashlib.sha512(path.read_bytes()).digest()).decode()
        if observed_integrity != record["integrity"]:
            fail(f"npm integrity mismatch for {record['name']}@{record['version']}")
        output[record["key"]] = path
    return output


def runtime_environment(release: Path, token: str, node_bin: Path) -> dict[str, str]:
    state = release / "state"
    env = os.environ.copy()
    env.update({
        "PATH": f"{node_bin}:{env.get('PATH', '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin')}",
        "HOME": str(state),
        "OPENCLAW_HOME": str(state),
        "OPENCLAW_STATE_DIR": str(state),
        "OPENCLAW_CONFIG_PATH": str(state / "openclaw.json"),
        "OPENCLAW_GATEWAY_TOKEN": token,
        "OPENCLAW_GATEWAY_URL": "ws://127.0.0.1:19789",
        "OPENCLAW_GATEWAY_PORT": "19789",
        "PIXEL_AGENT_ID": "pixel",
        "PIXEL_AGENT_NAME": "Pixel Qualification",
        "PIXEL_SOURCE_PROJECTION_DIR": str(state / "projections"),
        "PIXEL_ACTION_PROPOSAL_DIR": str(state / "proposals"),
        "PIXEL_LIMB_EMAIL_ENABLED": "1",
        "PIXEL_LIMB_CALENDAR_ENABLED": "1",
        "PIXEL_LIMB_SOCIAL_ENABLED": "0",
        "PIXEL_LIMB_WEB_ENABLED": "0",
        "PIXEL_LIMB_OPERATIONS_ENABLED": "0",
    })
    return env


def render_config(source: Path, release: Path, token: str, node_bin: Path, recorder: Recorder) -> None:
    state = release / "state"
    env = runtime_environment(release, token, node_bin)
    env.update({
        "PIXEL_MODEL_PROVIDER": "qualification",
        "PIXEL_MODEL_ID": "deterministic-no-model",
        "PIXEL_MODEL_NAME": "Qualification No-Model",
        "PIXEL_MODEL_BASE_URL": "http://127.0.0.1:19999/v1",
        "PIXEL_MODEL_API_KEY": "qualification-no-secret",
        "PIXEL_MODEL_CONTEXT_WINDOW": "65536",
        "PIXEL_MODEL_MAX_TOKENS": "4096",
        "PIXEL_GATEWAY_TOKEN": token,
        "PIXEL_WORKSPACE": str(state / "workspace"),
        "PIXEL_EMBEDDING_MODEL": "qualification.gguf",
        "PIXEL_EMBEDDING_CACHE": str(state / "embedding-cache"),
        "PIXEL_SANDBOX_IMAGE": "openclaw-sandbox:bookworm-slim",
        "PIXEL_SEARXNG_BASE_URL": "http://127.0.0.1:18890",
        "PIXEL_GATEWAY_EXTENSIONS": '[{"id":"discord"}]',
        "PIXEL_PLUGIN_PATH": str(release / "pixel-plugin"),
        "PIXEL_OPS_PLUGIN_PATH": str(release / "pixel-plugin-ops"),
    })
    recorder.run(
        f"{release.name}-render-config",
        [str(node_bin / "node"), str(source / "scripts" / "render-config.mjs"), str(state / "openclaw.json")],
        cwd=source,
        env=env,
    )


def write_projections(state: Path) -> None:
    generated = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    boundary = {"projectionOnly": True, "rawContentStored": False}
    values = {
        "email": {
            "schemaVersion": 1, "generatedAt": generated, "enabled": True,
            "coverage": {"complete": True, "queries": ["in:inbox", "in:sent"]}, "boundary": boundary,
            "records": [{
                "id": "qualification-email", "threadId": "qualification-thread", "folder": "inbox",
                "labels": ["INBOX"], "subject": "Runtime qualification", "summary": "Harmless projected record",
            }],
        },
        "calendar": {"schemaVersion": 1, "generatedAt": generated, "enabled": True, "boundary": boundary, "records": []},
        "social": {"schemaVersion": 1, "generatedAt": generated, "enabled": False, "boundary": boundary, "records": []},
    }
    for name, value in values.items():
        path = state / "projections" / f"{name}.json"
        path.write_bytes(canonical(value))
        path.chmod(0o600)


def install_release(
    label: str,
    manifest: dict[str, object],
    archives: dict[str, Path],
    source: Path,
    release: Path,
    token: str,
    node_bin: Path,
    recorder: Recorder,
) -> dict[str, object]:
    private_directory(release)
    runtime = release / "runtime"
    private_directory(runtime)
    install = [
        str(node_bin / "npm"), "install", "--ignore-scripts", "--no-audit", "--no-fund",
        "--no-package-lock", "--omit=dev", "--prefix", str(runtime), "--",
        *[str(archives[key]) for key in ("openclaw", "discord", "searxng", "llamaCpp")],
    ]
    env = os.environ.copy()
    env["PATH"] = f"{node_bin}:{env.get('PATH', '')}"
    recorder.run(f"{label}-runtime-install", install, env=env, timeout=1200)
    binary = runtime / "node_modules" / ".bin" / "openclaw"
    if not binary.is_file() or binary.is_symlink() is False:
        # npm's .bin entry is expected to be a symlink into the verified package.
        fail(f"{label} OpenClaw executable is missing or not npm-linked")
    version_output = recorder.run(f"{label}-version", [str(binary), "--version"], env=env).stdout
    if str(manifest["openclaw"]) not in version_output:
        fail(f"{label} runtime version differs from its manifest")

    for source_name, destination_name in (("plugin", "pixel-plugin"), ("plugin-ops", "pixel-plugin-ops")):
        destination = release / destination_name
        shutil.copytree(source / source_name, destination, ignore=shutil.ignore_patterns("node_modules", "__pycache__", "*.pyc"))
        recorder.run(
            f"{label}-{source_name}-dependencies",
            [str(node_bin / "npm"), "ci", "--ignore-scripts", "--no-audit", "--no-fund", "--omit=dev"],
            cwd=destination,
            env=env,
        )

    state = release / "state"
    for directory in (state, state / "workspace", state / "embedding-cache", state / "projections", state / "proposals"):
        private_directory(directory)
    os.chown(state / "workspace", 1000, 1000)
    write_projections(state)
    render_config(source, release, token, node_bin, recorder)
    release_env = runtime_environment(release, token, node_bin)
    for key in ("discord", "searxng", "llamaCpp"):
        recorder.run(
            f"{label}-install-{key}",
            [str(binary), "plugins", "install", "--force", str(archives[key])],
            env=release_env,
            timeout=600,
        )
    render_config(source, release, token, node_bin, recorder)
    validation = recorder.run(f"{label}-config-validate", [str(binary), "config", "validate"], env=release_env)
    validation_text = f"{validation.stdout}\n{validation.stderr}".lower()
    if "config valid" not in validation_text or "warning" in validation_text:
        fail(f"{label} config validation was not clean")

    plugins_result = recorder.run(f"{label}-plugins", [str(binary), "plugins", "list", "--enabled", "--json"], env=release_env)
    plugins = json.loads(plugins_result.stdout)
    loaded = {item["id"]: item for item in plugins["plugins"] if item.get("status") == "loaded"}
    expected_versions = {
        "discord": manifest["openclawPlugins"]["@openclaw/discord"],
        "searxng": manifest["openclawPlugins"]["@openclaw/searxng-plugin"],
        "llama-cpp": manifest["openclawPlugins"]["@openclaw/llama-cpp-provider"],
        "pixel-source-broker": manifest["pixel"],
    }
    for plugin_id, version in expected_versions.items():
        if loaded.get(plugin_id, {}).get("version") != version:
            fail(f"{label} did not load {plugin_id}@{version}")

    inspect_result = recorder.run(
        f"{label}-pixel-plugin-runtime",
        [str(binary), "plugins", "inspect", "pixel-source-broker", "--runtime", "--json"],
        env=release_env,
    )
    inspected = json.loads(inspect_result.stdout)
    tool_names = set(inspected["plugin"].get("toolNames", []))
    runtime_tool_names = {name for item in inspected.get("tools", []) for name in item.get("names", [])}
    if tool_names != EXPECTED_SOURCE_TOOLS or runtime_tool_names != EXPECTED_SOURCE_TOOLS:
        fail(f"{label} source plugin tool contract did not load exactly")

    sandbox_result = recorder.run(
        f"{label}-sandbox-policy", [str(binary), "sandbox", "explain", "--agent", "pixel", "--json"], env=release_env,
    )
    sandbox = json.loads(sandbox_result.stdout)["sandbox"]
    allowed = set(sandbox["tools"]["allow"])
    if sandbox.get("mode") != "all" or sandbox.get("scope") != "agent" or not {"exec", "read", "write", "sessions_list"}.issubset(allowed):
        fail(f"{label} sandbox or useful core tool policy regressed")
    if allowed & DISABLED_TOOLS:
        fail(f"{label} disabled limb tools leaked into the sandbox allowlist")

    config = json.loads((state / "openclaw.json").read_text(encoding="utf-8"))
    if config.get("tools", {}).get("profile") != "coding":
        fail(f"{label} did not select the coding capability profile")
    if config["tools"].get("sessions", {}).get("visibility") != "tree" or config["tools"].get("fs", {}).get("workspaceOnly") is not True:
        fail(f"{label} session or workspace isolation policy regressed")

    dependency_result = recorder.run(
        f"{label}-dependency-root", [str(node_bin / "npm"), "ls", "--depth=0", "--json", "--prefix", str(runtime)], env=env,
    )
    dependency_root = json.loads(dependency_result.stdout).get("dependencies", {})
    return {
        "label": label,
        "openclaw": manifest["openclaw"],
        "plugins": expected_versions,
        "runtimeRootDependencies": {name: value.get("version") for name, value in sorted(dependency_root.items())},
        "toolCount": len(tool_names),
        "config": "valid-without-warnings",
        "sandboxPolicy": "agent/all/network-none/read-only-root/workspace-only",
    }


def switch_active(work: Path, release: Path) -> None:
    temporary = work / ".active.next"
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(release)
    os.replace(temporary, work / "active")


def post_tool(port: int, token: str | None, body: dict[str, object]) -> tuple[int, dict[str, object]]:
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/tools/invoke", data=canonical(body), headers=headers, method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read(MAX_COMMAND_OUTPUT + 1)
            if len(data) > MAX_COMMAND_OUTPUT:
                fail("gateway tool response exceeded the evidence bound")
            return response.status, json.loads(data)
    except urllib.error.HTTPError as error:
        data = error.read(MAX_COMMAND_OUTPUT + 1)
        return error.code, json.loads(data or b"{}")


class QualificationModelServer:
    """Deterministic OpenAI-compatible model that requests one harmless write."""

    def __init__(self) -> None:
        self.requests = 0
        self.write_schema_seen = False
        self.tool_result_seen = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                return

            def do_POST(self) -> None:
                if self.path != "/v1/chat/completions":
                    self.send_error(404)
                    return
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > MAX_COMMAND_OUTPUT:
                    self.send_error(400)
                    return
                request = json.loads(self.rfile.read(length))
                owner.requests += 1
                tools = request.get("tools") or []
                write_tool = next(
                    (item for item in tools if item.get("type") == "function" and item.get("function", {}).get("name") == "write"),
                    None,
                )
                owner.write_schema_seen = owner.write_schema_seen or write_tool is not None
                messages = request.get("messages") or []
                owner.tool_result_seen = owner.tool_result_seen or any(item.get("role") == "tool" for item in messages)
                if owner.tool_result_seen:
                    delta = {"role": "assistant", "content": "PIXEL_QUALIFICATION_COMPLETE"}
                    finish_reason = "stop"
                else:
                    properties = (write_tool or {}).get("function", {}).get("parameters", {}).get("properties", {})
                    path_key = "path" if "path" in properties else "file_path"
                    arguments = {path_key: "qualification.txt", "content": "pixel runtime qualification\n"}
                    delta = {
                        "role": "assistant",
                        "tool_calls": [{
                            "index": 0, "id": "call_pixel_qualification_write", "type": "function",
                            "function": {"name": "write", "arguments": json.dumps(arguments, separators=(",", ":"))},
                        }],
                    }
                    finish_reason = "tool_calls"
                chunk = {
                    "id": f"chatcmpl-pixel-qualification-{owner.requests}",
                    "object": "chat.completion.chunk", "created": 1, "model": "deterministic-no-model",
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
                }
                payload = f"data: {json.dumps(chunk, separators=(',', ':'))}\n\ndata: [DONE]\n\n".encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.server = ThreadingHTTPServer(("127.0.0.1", 19999), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, name="pixel-qualification-model", daemon=True)

    def start(self) -> None:
        phase("deterministic-model-start")
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def find_sandbox_container(recorder: Recorder) -> dict[str, object]:
    listed = recorder.run(
        "candidate-sandbox-list", ["docker", "ps", "-aq", "--filter", "name=^/pixel-sbx-agent-pixel-"],
    ).stdout.split()
    if len(listed) != 1:
        fail(f"expected one Pixel sandbox container after the workspace turn, found {len(listed)}")
    inspected = recorder.run("candidate-sandbox-inspect", ["docker", "inspect", listed[0]]).stdout
    value = json.loads(inspected)[0]
    host = value["HostConfig"]
    labels = value["Config"].get("Labels") or {}
    if not (
        value["State"]["Running"] is True
        and value["Config"]["User"] == "sandbox"
        and host["NetworkMode"] == "none"
        and host["ReadonlyRootfs"] is True
        and host["Privileged"] is False
        and "ALL" in (host.get("CapDrop") or [])
        and "no-new-privileges" in (host.get("SecurityOpt") or [])
        and labels.get("openclaw.sandbox") == "1"
        and labels.get("openclaw.sessionKey", "").startswith("agent:pixel")
    ):
        fail("live sandbox container violated the expected confinement contract")
    for mount in value.get("Mounts", []):
        source = mount.get("Source", "")
        if source == "/" or source == "/var/run/docker.sock" or source.startswith("/etc/") or "/.ssh" in source:
            fail("live sandbox received a forbidden host mount")
    return {"id": listed[0][:12], "network": "none", "readOnlyRoot": True, "user": "sandbox", "capDrop": "ALL"}


def retire_sandbox_container(recorder: Recorder) -> None:
    listed = recorder.run(
        "candidate-sandbox-retirement-list", ["docker", "ps", "-aq", "--filter", "name=^/pixel-sbx-agent-pixel-"],
    ).stdout.split()
    if len(listed) != 1 or not all(len(value) >= 12 and all(character in "0123456789abcdef" for character in value) for value in listed):
        fail("sandbox rollback retirement found an unsafe container identity")
    recorder.run("candidate-sandbox-retirement", ["docker", "rm", "-f", "--", *listed])
    remaining = recorder.run(
        "candidate-sandbox-retirement-verify", ["docker", "ps", "-aq", "--filter", "name=^/pixel-sbx-agent-pixel-"],
    ).stdout.split()
    if remaining:
        fail("candidate sandbox survived the rollback boundary")


def create_systemd_service(work: Path, token: str, node_bin: Path, recorder: Recorder) -> tuple[str, int]:
    if os.geteuid() != 0:
        fail("systemd qualification mode requires root inside a disposable guest")
    if pwd is None:
        fail("systemd qualification mode requires the POSIX account database")
    try:
        account = pwd.getpwuid(1000)
    except KeyError:
        recorder.run("create-qualification-user", ["useradd", "--uid", "1000", "--create-home", "--shell", "/bin/bash", "pixelqual"])
        account = pwd.getpwnam("pixelqual")
    user = account.pw_name
    recorder.run("qualification-user-docker", ["usermod", "-aG", "docker", user])
    work.chmod(0o755)
    for release_name in ("candidate", "supported"):
        (work / release_name).chmod(0o755)
        for code_root in ("runtime", "pixel-plugin", "pixel-plugin-ops"):
            make_service_readable(work / release_name / code_root)
        state = work / release_name / "state"
        shutil.chown(state, user=user, group=account.pw_gid)
        for root, directories, files in os.walk(state):
            shutil.chown(root, user=user, group=account.pw_gid)
            for name in directories + files:
                shutil.chown(Path(root) / name, user=user, group=account.pw_gid)
    environment = work / "gateway.env"
    environment.write_text(
        "\n".join([
            f"{'OPENCLAW_GATEWAY_' + 'TOKEN'}={token}",
            "PIXEL_AGENT_ID=pixel",
            "PIXEL_LIMB_EMAIL_ENABLED=1", "PIXEL_LIMB_CALENDAR_ENABLED=1",
            "PIXEL_LIMB_SOCIAL_ENABLED=0", "PIXEL_LIMB_WEB_ENABLED=0", "PIXEL_LIMB_OPERATIONS_ENABLED=0",
            f"PIXEL_SOURCE_PROJECTION_DIR={work}/active/state/projections",
            f"PIXEL_ACTION_PROPOSAL_DIR={work}/active/state/proposals",
        ]) + "\n",
        encoding="utf-8",
    )
    environment.chmod(0o600)
    unit = Path("/etc/systemd/system/pixel-upstream-qualification.service")
    unit.write_text(f"""[Unit]
Description=Disposable Pixel upstream qualification gateway
After=network-online.target docker.service
Wants=network-online.target docker.service

[Service]
User={user}
ExecStart={work}/active/runtime/node_modules/.bin/openclaw gateway --bind loopback --auth token --port 19789
Environment=HOME={work}/active/state
Environment=OPENCLAW_HOME={work}/active/state
Environment=OPENCLAW_STATE_DIR={work}/active/state
Environment=OPENCLAW_CONFIG_PATH={work}/active/state/openclaw.json
Environment=PATH={node_bin}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
EnvironmentFile={environment}
NoNewPrivileges=true
PrivateDevices=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadOnlyPaths={work}/candidate/runtime {work}/supported/runtime {work}/candidate/pixel-plugin {work}/supported/pixel-plugin
ReadWritePaths={work}/candidate/state {work}/supported/state
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
RestrictNamespaces=true
RestrictRealtime=true
RestrictSUIDSGID=true
LockPersonality=true
CapabilityBoundingSet=
AmbientCapabilities=
SystemCallArchitectures=native
UMask=0077
MemoryMax=4G
TasksMax=2048
TimeoutStopSec=30
KillMode=control-group

[Install]
WantedBy=multi-user.target
""", encoding="utf-8")
    unit.chmod(0o644)
    recorder.run("systemd-reload", ["systemctl", "daemon-reload"])
    return user, 19789


class Gateway:
    def __init__(self, mode: str, work: Path, evidence: Path, token: str, node_bin: Path, recorder: Recorder):
        self.mode = mode
        self.work = work
        self.evidence = evidence
        self.token = token
        self.node_bin = node_bin
        self.recorder = recorder
        self.process: subprocess.Popen[str] | None = None
        self.log_handle = None

    def start(self, release: Path) -> int:
        switch_active(self.work, release)
        if self.mode == "systemd":
            self.recorder.run(f"{release.name}-systemd-start", ["systemctl", "restart", "pixel-upstream-qualification.service"])
            port = 19789
        else:
            log = self.evidence / f"gateway-{release.name}.log"
            self.log_handle = log.open("w", encoding="utf-8")
            self.log_handle.write(
                f"EVIDENCE_BINDING {json.dumps(self.recorder.binding, sort_keys=True, separators=(',', ':'))}\n"
            )
            self.log_handle.flush()
            binary = release / "runtime" / "node_modules" / ".bin" / "openclaw"
            self.process = subprocess.Popen(
                [str(binary), "gateway", "--bind", "loopback", "--auth", "token", "--port", "19789"],
                env=runtime_environment(release, self.token, self.node_bin), stdout=self.log_handle, stderr=subprocess.STDOUT, text=True,
            )
            port = 19789
        self.wait_healthy(release, port)
        return port

    def wait_healthy(self, release: Path, port: int) -> None:
        binary = release / "runtime" / "node_modules" / ".bin" / "openclaw"
        env = runtime_environment(release, self.token, self.node_bin)
        for _ in range(45):
            try:
                result = subprocess.run(
                    [str(binary), "gateway", "health", "--url", f"ws://127.0.0.1:{port}", "--token", self.token, "--json"],
                    env=env, capture_output=True, text=True, timeout=5,
                )
            except subprocess.TimeoutExpired:
                result = None
            if result is not None and result.returncode == 0:
                health = json.loads(result.stdout)
                if health.get("ok") is True and not health.get("plugins", {}).get("errors"):
                    (self.evidence / f"health-{release.name}.json").write_bytes(canonical({
                        "evidenceBinding": self.recorder.binding,
                        "health": health,
                    }))
                    phase(f"{release.name}-gateway-healthy")
                    return
            if self.mode == "systemd":
                failed = subprocess.run(
                    ["systemctl", "is-failed", "pixel-upstream-qualification.service"],
                    capture_output=True, text=True,
                )
                if failed.returncode == 0:
                    break
            time.sleep(1)
        if self.mode == "systemd":
            self.recorder.run(
                f"{release.name}-systemd-status",
                ["systemctl", "status", "pixel-upstream-qualification.service", "--no-pager", "--full"],
                check=False,
            )
            self.recorder.run(
                f"{release.name}-systemd-journal",
                ["journalctl", "--unit", "pixel-upstream-qualification.service", "--no-pager", "--lines", "200"],
                check=False,
            )
        fail(f"{release.name} gateway did not become healthy")

    def stop(self, label: str) -> None:
        if self.mode == "systemd":
            self.recorder.run(f"{label}-systemd-stop", ["systemctl", "stop", "pixel-upstream-qualification.service"])
            state = self.recorder.run(f"{label}-systemd-inactive", ["systemctl", "is-active", "pixel-upstream-qualification.service"], check=False)
            if state.stdout.strip() != "inactive":
                fail("qualification gateway did not stop cleanly")
        elif self.process is not None:
            self.process.terminate()
            try:
                code = self.process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.process.kill()
                fail("foreground qualification gateway ignored graceful shutdown")
            if code not in (0, 143, -15):
                fail(f"foreground qualification gateway exited unexpectedly ({code})")
            if self.log_handle:
                self.log_handle.close()
            self.process = None


def exercise_candidate(
    release: Path,
    port: int,
    token: str,
    node_bin: Path,
    recorder: Recorder,
    *,
    live_sandbox: bool,
    model_server: QualificationModelServer | None,
) -> dict[str, object]:
    for supplied in (None, "pixel-forged-qualification-token"):
        status, response = post_tool(port, supplied, {"name": "pixel_limb_status", "agentId": "pixel", "args": {}})
        if status not in (401, 404) or token in json.dumps(response):
            fail("gateway authentication boundary accepted an unauthenticated or forged request")

    status, limb = post_tool(port, token, {"name": "pixel_limb_status", "agentId": "pixel", "args": {}})
    expected_limbs = {"email": True, "calendar": True, "social": False, "web": False, "operations": False}
    if status != 200 or limb.get("ok") is not True or limb["result"].get("details", {}).get("limbs") != expected_limbs:
        fail("enabled/disabled limb state did not survive the real gateway")

    status, inbox = post_tool(port, token, {"name": "pixel_gmail_inbox", "agentId": "pixel", "args": {}})
    if status != 200 or inbox.get("ok") is not True or inbox["result"].get("details", {}).get("coverage", {}).get("complete") is not True:
        fail("enabled email limb failed through the real gateway")

    status, social = post_tool(port, token, {"name": "pixel_social_feed", "agentId": "pixel", "args": {}})
    if status != 404 or social.get("ok") is not False or social.get("error", {}).get("type") != "not_found":
        fail("disabled social limb did not refuse through the real gateway")

    status, sessions = post_tool(port, token, {"name": "sessions_list", "agentId": "pixel", "args": {}})
    visibility = sessions.get("result", {}).get("details", {}).get("visibility", {})
    if status != 200 or visibility.get("mode") != "tree" or visibility.get("restricted") is not True:
        fail("session-tree visibility was not enforced through the real gateway")

    if live_sandbox:
        binary = release / "runtime" / "node_modules" / ".bin" / "openclaw"
        agent_params = json.dumps({
            "message": "Use the write tool to create qualification.txt with the exact requested qualification content.",
            "agentId": "pixel",
            "sessionKey": "agent:pixel:qualification",
            "deliver": False,
            "timeout": 120,
            "idempotencyKey": "pixel-upstream-runtime-qualification",
        }, separators=(",", ":"))
        agent_command = [
            str(binary), "gateway", "call", "agent", "--url", f"ws://127.0.0.1:{port}",
            "--token", token, "--params", agent_params, "--expect-final", "--json", "--timeout", "180000",
        ]
        isolated_state = release / "client-state"
        private_directory(isolated_state, exclusive=True)
        turn_env = runtime_environment(release, token, node_bin)
        turn_env["OPENCLAW_STATE_DIR"] = str(isolated_state)
        invalid_token = "0" * 64 if token != "0" * 64 else "f" * 64
        recorder.add_secret(invalid_token)
        invalid_command = agent_command.copy()
        invalid_command[invalid_command.index("--token") + 1] = invalid_token
        denial = recorder.run(
            "candidate-agent-gateway-token-denial",
            invalid_command,
            env=turn_env,
            timeout=30,
            check=False,
        )
        denial_output = f"{denial.stdout}\n{denial.stderr}".lower()
        if denial.returncode == 0 or "unauthorized: gateway token mismatch" not in denial_output:
            fail("invalid shared gateway token was not explicitly denied")

        turn = recorder.run(
            "candidate-agent-sandbox-turn", agent_command, env=turn_env, timeout=180, check=False,
        )
        if turn.returncode != 0:
            recorder.run(
                "candidate-agent-failure-journal",
                ["journalctl", "--unit", "pixel-upstream-qualification.service", "--no-pager", "--lines", "200"],
                check=False,
            )
            fail("real shared-token agent turn failed")
        if "PIXEL_QUALIFICATION_COMPLETE" not in turn.stdout:
            fail("real agent turn did not complete after its sandboxed tool result")
        if model_server is None or not model_server.write_schema_seen or not model_server.tool_result_seen:
            fail("real agent turn did not expose and complete the write tool contract")
        workspace_file = release / "state" / "workspace" / "qualification.txt"
        if workspace_file.read_text(encoding="utf-8") != "pixel runtime qualification\n":
            fail("sandboxed workspace write did not reach only the intended workspace file")
        sandbox: dict[str, object] | str = find_sandbox_container(recorder)
        workspace_turn = "pass"
    else:
        sandbox = "configured-policy-pass; live boundary deferred to systemd VM lane"
        workspace_turn = "deferred-to-systemd-vm"
    return {
        "authentication": {"unauthenticated": "denied", "forged": "denied", "valid": "accepted"},
        "limbs": expected_limbs,
        "enabledEmailCapability": "pass",
        "disabledSocialRefusal": "pass",
        "sessionVisibility": "tree-restricted",
        "workspaceToolTurn": workspace_turn,
        "sandbox": sandbox,
    }


def verify_no_secret(evidence: Path, secrets_to_check: set[str]) -> None:
    for path in evidence.rglob("*"):
        if not path.is_file():
            continue
        contents = path.read_bytes()
        if any(secret.encode() in contents for secret in secrets_to_check):
            fail(f"runtime credential leaked into retained evidence: {path.name}")


def observe_candidate(
    release: Path,
    token: str,
    node_bin: Path,
    recorder: Recorder,
    seconds: int,
) -> dict[str, object]:
    phase(f"candidate-observation-window seconds={seconds}")
    binary = release / "runtime" / "node_modules" / ".bin" / "openclaw"
    started_at = time.time()
    deadline = time.monotonic() + seconds
    checks = 0
    while True:
        recorder.run(f"candidate-observation-service-{checks + 1}", ["systemctl", "is-active", "pixel-upstream-qualification.service"])
        health = recorder.run(
            f"candidate-observation-health-{checks + 1}",
            [str(binary), "gateway", "health", "--url", "ws://127.0.0.1:19789", "--token", token, "--json"],
            env=runtime_environment(release, token, node_bin),
            timeout=10,
        )
        value = json.loads(health.stdout)
        if value.get("ok") is not True or value.get("plugins", {}).get("errors"):
            fail("candidate became unhealthy during the canary observation window")
        checks += 1
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(30, remaining))
    return {
        "startedAtEpoch": int(started_at),
        "endedAtEpoch": int(time.time()),
        "seconds": seconds,
        "minutes": seconds // 60,
        "healthChecks": checks,
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--candidate-manifest", required=True, type=Path)
    parser.add_argument("--supported-manifest", required=True, type=Path)
    parser.add_argument("--quarantine", required=True, type=Path)
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--qualification-source-commit", required=True)
    parser.add_argument("--observation-seconds", type=int, default=0)
    parser.add_argument("--mode", choices=("quick", "systemd"), default="quick")
    return parser.parse_args()


def main() -> int:
    os.umask(0o077)
    args = parse_arguments()
    if args.observation_seconds < 0 or args.observation_seconds > 7200:
        fail("observation window must be between 0 and 7200 seconds")
    if args.observation_seconds and args.mode != "systemd":
        fail("candidate observation requires the systemd VM lane")
    source = args.source.resolve()
    if not (source / "pixel").is_file():
        fail("--source is not a Pixel repository snapshot")
    work = require_safe_external(args.work, source, "--work")
    evidence = require_safe_external(args.evidence, source, "--evidence")
    quarantine = require_safe_external(args.quarantine, source, "--quarantine")
    private_directory(work, exclusive=True)
    private_directory(evidence, exclusive=True)
    phase(f"qualification-start mode={args.mode}")
    candidate = json.loads(args.candidate_manifest.read_text(encoding="utf-8"))
    supported = json.loads(args.supported_manifest.read_text(encoding="utf-8"))
    intake = candidate.get("upstreamIntake")
    if not isinstance(intake, dict):
        fail("candidate manifest has no upstream intake identity")
    if candidate.get("pixel") != supported.get("pixel"):
        fail("candidate and supported manifests target different Pixel releases")
    node_runtime = candidate["nodeRuntime"]
    if node_runtime != supported.get("nodeRuntime"):
        fail("candidate changed the qualification Node runtime outside the upstream package intake")
    qualification_source_commit = args.qualification_source_commit.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", qualification_source_commit):
        fail("qualification source commit must be an exact lowercase Git commit")
    candidate_manifest_sha256 = hashlib.sha256(canonical(candidate)).hexdigest()
    supported_manifest_sha256 = hashlib.sha256(canonical(supported)).hexdigest()
    candidate_packages_sha256 = hashlib.sha256(canonical(package_records(candidate))).hexdigest()
    evidence_binding = {
        "qualificationSourceCommit": qualification_source_commit,
        "intakeSourceCommit": intake["sourceCommit"],
        "pixel": candidate["pixel"],
        "candidateOpenClaw": candidate["openclaw"],
        "candidateManifestSha256": candidate_manifest_sha256,
        "candidatePackagesSha256": candidate_packages_sha256,
    }
    token = secrets.token_hex(32)
    recorder = Recorder(evidence, token, evidence_binding)
    binding_path = evidence / "evidence-binding.json"
    binding_path.write_bytes(canonical({
        "schemaVersion": 1,
        "operation": "upstream-evidence-binding",
        "evidenceBinding": evidence_binding,
        "sha256": recorder.binding_sha256,
    }))
    binding_path.chmod(0o600)
    node_bin = Path(shutil.which("node") or "").resolve().parent
    observed_node = recorder.run("node-version", [str(node_bin / "node"), "--version"]).stdout.strip()
    if observed_node != f"v{node_runtime['version']}":
        fail(f"qualification requires Node v{node_runtime['version']}; found {observed_node}")
    required_commands = ["npm", "curl", *(["docker", "runuser", "systemctl"] if args.mode == "systemd" else [])]
    for command in required_commands:
        if not shutil.which(command, path=f"{node_bin}:{os.environ.get('PATH', '')}"):
            fail(f"qualification guest is missing {command}")
    if args.mode == "systemd":
        recorder.run("docker-ready", ["docker", "info"])

    channel = str(intake["channel"])
    candidate_archives = locate_and_verify_archives(candidate, quarantine / channel / str(candidate["openclaw"]))
    supported_archives = locate_and_verify_archives(supported, quarantine / "supported" / str(supported["openclaw"]))
    phase("package-archives-verified")
    candidate_release = work / "candidate"
    supported_release = work / "supported"
    release_results = [
        install_release("supported", supported, supported_archives, source, supported_release, token, node_bin, recorder),
        install_release("candidate", candidate, candidate_archives, source, candidate_release, token, node_bin, recorder),
    ]
    phase("supported-and-candidate-installed")
    if args.mode == "systemd":
        recorder.run(
            "sandbox-image-build",
            [
                "docker", "build", "--build-arg", "PIXEL_SANDBOX_UID=1000",
                "--label", f"org.osmantic.pixel.sandbox-version={candidate['pixel']}",
                "--label", "org.osmantic.pixel.sandbox-uid=1000",
                "-t", str(candidate["sandboxImage"]), str(source / "deploy" / "sandbox"),
            ],
            timeout=1800,
        )

    if args.mode == "systemd":
        service_user, port = create_systemd_service(work, token, node_bin, recorder)
    else:
        service_user, port = "foreground-disposable-root", 19789
    gateway = Gateway(args.mode, work, evidence, token, node_bin, recorder)
    model_server = QualificationModelServer() if args.mode == "systemd" else None
    if model_server is not None:
        model_server.start()
    try:
        phase("candidate-gateway-exercise")
        gateway.start(candidate_release)
        candidate_result = exercise_candidate(
            candidate_release,
            port,
            token,
            node_bin,
            recorder,
            live_sandbox=args.mode == "systemd",
            model_server=model_server,
        )
        canary_observation = observe_candidate(
            candidate_release,
            token,
            node_bin,
            recorder,
            args.observation_seconds,
        ) if args.observation_seconds else None
        gateway.stop("candidate")
        if args.mode == "systemd":
            retire_sandbox_container(recorder)
        candidate_pid = None
        if args.mode == "systemd":
            candidate_pid_result = recorder.run("candidate-main-pid-after-stop", ["systemctl", "show", "pixel-upstream-qualification.service", "-p", "MainPID", "--value"])
            candidate_pid = int(candidate_pid_result.stdout.strip() or "0")
            if candidate_pid != 0:
                fail("candidate gateway retained a process after clean shutdown")
        phase("supported-rollback-exercise")
        gateway.start(supported_release)
        active_binary = work / "active" / "runtime" / "node_modules" / ".bin" / "openclaw"
        rollback_version = recorder.run(
            "rollback-version", [str(active_binary), "--version"], env=runtime_environment(supported_release, token, node_bin),
        ).stdout
        if str(supported["openclaw"]) not in rollback_version:
            fail("rollback did not activate the exact supported OpenClaw runtime")
        status, rollback_limb = post_tool(port, token, {"name": "pixel_limb_status", "agentId": "pixel", "args": {}})
        if status != 200 or rollback_limb.get("ok") is not True:
            fail("supported runtime was unhealthy after rollback")
        gateway.stop("rollback")
    finally:
        try:
            gateway.stop("final-cleanup")
        except Exception:
            pass
        if model_server is not None:
            model_server.stop()

    os_release = {}
    for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            os_release[key] = value.strip('"')
    summary = {
        "schemaVersion": 1,
        "operation": "upstream-runtime-qualification",
        "status": "pass",
        "mode": args.mode,
        "sourceCommit": qualification_source_commit,
        "intakeSourceCommit": intake["sourceCommit"],
        "evidenceBinding": evidence_binding,
        "evidenceBindingSha256": recorder.binding_sha256,
        "candidateManifestSha256": candidate_manifest_sha256,
        "supportedManifestSha256": supported_manifest_sha256,
        "environment": {
            "os": {"id": os_release.get("ID"), "version": os_release.get("VERSION_ID")},
            "node": observed_node,
            "service": args.mode,
            "serviceUser": service_user,
        },
        "releases": release_results,
        "candidate": candidate_result,
        "rollback": {"target": supported["openclaw"], "gatewayHealthy": True, "cleanShutdown": True},
        "commands": recorder.records,
    }
    summary_path = evidence / "runtime-qualification.json"
    summary_path.write_bytes(canonical(summary))
    summary_path.chmod(0o600)
    verify_no_secret(evidence, recorder.secrets)
    if canary_observation is not None:
        canary_summary = {
            "schemaVersion": 1,
            "operation": "pixel-upstream-canary",
            "status": "pass",
            "sourceCommit": qualification_source_commit,
            "candidateManifestSha256": candidate_manifest_sha256,
            "isolated": True,
            "syntheticChecks": {
                "gateway": "pass",
                "sourceLimb": "pass",
                "sessionTree": "pass",
                "workspaceToolTurn": candidate_result["workspaceToolTurn"],
                "sandbox": "pass",
            },
            "observation": canary_observation,
            "rollbackRehearsed": True,
            "postRollbackHealthy": True,
        }
        canary_path = evidence / "canary-summary.json"
        canary_path.write_bytes(canonical(canary_summary))
        canary_path.chmod(0o600)
        verify_no_secret(evidence, recorder.secrets)
    digest = sha256_file(summary_path)
    phase("qualification-pass")
    print(json.dumps({"status": "pass", "mode": args.mode, "evidence": str(summary_path), "sha256": digest}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(f"upstream runtime qualification failed: {error}", file=sys.stderr)
        sys.exit(1)
