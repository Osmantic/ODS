#!/usr/bin/env python3
"""Credential-free black-box qualification of Pixel's pinned Codex comparison tool surface."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import time

import portal_outcome_evaluation as evaluation
import portal_outcome_livesystem as livesystem


EXPECTED_RECEIPT = {
    "schemaVersion": 1,
    "operation": "pixel-codex-comparison-surface-qualified",
    "modelId": "DeepSeek-V4-Flash-0731",
    "functionTools": ["exec_command", "request_user_input", "update_plan", "view_image", "write_stdin"],
    "customTool": "apply_patch",
    "customFormat": "grammar:lark",
    "namespace": "multi_agent_v1",
    "namespaceTools": ["close_agent", "resume_agent", "send_input", "spawn_agent", "wait_agent"],
    "toolCount": 7,
    "contentStored": False,
    "credentialUsed": False,
    "providerCalled": False,
    "externalEffects": False,
}


def docker(args: list[str], *, timeout: int, input_bytes: bytes | None = None) -> str:
    result = subprocess.run(["docker", *args], input=input_bytes, capture_output=True, timeout=timeout)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace")[-800:]
        raise evaluation.OutcomeError(f"Codex surface docker {args[0]} failed: {detail}")
    return result.stdout.decode("utf-8", errors="replace")


def qualify(root: Path, image: str) -> dict:
    root = root.resolve(strict=True)
    fixture = (root / "tests/fixtures/agent-comparison/fake-codex-surface.mjs").resolve(strict=True)
    image_id = docker(["image", "inspect", "-f", "{{.Id}}", image], timeout=30).strip()
    if not image_id.startswith("sha256:"):
        raise evaluation.OutcomeError("Codex comparison image is not immutable after resolution")
    evaluation.valid_hash(image_id[7:], "Codex comparison image identity")
    system = livesystem.DockerSystem(root=root, codex_image=image)
    toolchain_sha256 = system.codex_comparison_toolchain_sha256()
    suffix = f"{os.getpid():x}{secrets.token_hex(4)}"[-12:]
    network = f"pixel-codex-surface-{suffix}"
    server = f"{network}-server"
    temporary = Path(tempfile.mkdtemp(prefix="pixel-codex-surface-"))
    if os.name != "nt":
        temporary.chmod(0o700)
    source = temporary / "source"
    output = temporary / "output"
    source.mkdir(mode=0o700)
    output.mkdir(mode=0o700)
    (source / "README.md").write_text("Synthetic, credential-free Codex surface probe.\n", encoding="utf-8")
    catalog = temporary / "model-catalog.json"
    catalog.write_bytes(livesystem.build_codex_model_catalog(
        root, model_id="DeepSeek-V4-Flash-0731", context_window=1048576,
    ))
    if os.name != "nt":
        catalog.chmod(0o600)
    created_network = False
    started_server = False
    workspace_volume = None
    workspace_keeper = None
    try:
        docker(["network", "create", "--internal", network], timeout=30)
        created_network = True
        docker([
            "run", "-d", "--name", server, "--network", network, "--network-alias", livesystem.MODEL_ALIAS,
            "--pull", "never", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
            "--pids-limit", "32", "--memory", "256m", "--memory-swap", "256m", "--user", "1000:1000",
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=32m,mode=0700,uid=1000,gid=1000",
            "--mount", f"type=bind,source={fixture},target=/opt/fake-codex-surface.mjs,readonly",
            "--mount", f"type=bind,source={output},target=/output",
            "--entrypoint", "/opt/node/bin/node", image, "/opt/fake-codex-surface.mjs",
        ], timeout=60)
        started_server = True
        for _attempt in range(40):
            ready = subprocess.run([
                "docker", "exec", server, "/opt/node/bin/node", "-e",
                "fetch('http://127.0.0.1:8080/healthz').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))",
            ], capture_output=True, timeout=10)
            if ready.returncode == 0:
                break
            time.sleep(0.1)
        else:
            raise evaluation.OutcomeError("Codex surface fixture did not become ready")
        hardened = livesystem.load_hardened_codex_config(root)
        script = livesystem.build_codex_script(
            hardened, model_id="DeepSeek-V4-Flash-0731", model_context_window=1048576,
            wire_api="responses", env_key="PIXEL_OUTCOME_KEY", profile="builder",
            capabilities=["filesystem-read", "filesystem-write", "process-execution", "reasoning"],
            source_media_type="application/x-tar", tool_policy={
                "workspace": "disposable-read-write",
                "tools": ["read", "search", "write", "edit", "shell", "task"],
                "maximumSubagents": 1,
            },
        )
        environment = {"limits": {
            "maxIterations": 4, "maxToolCalls": 40, "maxConcurrentSubagents": 1,
            "maxCpuCores": 2, "maxMemoryMiB": 2048, "maxDiskBytes": 1073741824,
            "maxNetworkBytes": 1048576, "maxFailures": 2, "noProgressLimit": 2, "maxPids": 256,
        }}
        workspace_volume, workspace_maximum = system.codex_workspace_volume_create(network, environment)
        workspace_keeper = system.codex_workspace_keeper_start(network, workspace_volume)
        init_receipt = system.codex_workspace_copy("init", workspace_volume, source, workspace_maximum)
        transcript = docker([
            "run", "--rm", "-i", "--network", network, "--pull", "never", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
            *livesystem.codex_container_resource_args(environment), *livesystem.container_user_args(),
            "--mount", f"type=volume,source={workspace_volume},target=/work/source",
            "--mount", f"type=bind,source={catalog},target={livesystem.CODEX_MODEL_CATALOG_CONTAINER_PATH},readonly",
            "--env", "HOME=/work", "--env", "CODEX_HOME=/work/.codex", "--env", "TMPDIR=/work",
            "--env", "PIXEL_OUTCOME_KEY=local-no-auth", "--entrypoint", "/bin/sh", image, "-c", script,
        ], timeout=120, input_bytes=b"Return exactly the fixture success marker.\n")
        if "PIXEL_CODEX_SURFACE_GREEN" not in transcript:
            raise evaluation.OutcomeError("Codex surface probe did not complete through its exact Responses lane")
        exported = temporary / "exported"
        exported.mkdir(mode=0o700)
        export_receipt = system.codex_workspace_copy("export", workspace_volume, exported, workspace_maximum)
        if export_receipt != {**init_receipt, "operation": "codex-comparison-workspace-export"}:
            raise evaluation.OutcomeError(
                "Codex bounded workspace changed during the content-free surface probe: "
                f"init={json.dumps(init_receipt, sort_keys=True)} export={json.dumps(export_receipt, sort_keys=True)}"
            )
        if (exported / "README.md").read_bytes() != (source / "README.md").read_bytes():
            raise evaluation.OutcomeError("Codex bounded workspace export differs from its exact synthetic input")
        receipt_path = output / "codex-surface.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt != EXPECTED_RECEIPT:
            raise evaluation.OutcomeError("Codex emitted tool surface differs from the exact broad comparison contract")
        return {
            **receipt,
            "codexRunnerImageId": image_id,
            "codexComparisonToolchainSha256": toolchain_sha256,
            "fixtureSha256": evaluation.sha256(fixture.read_bytes()),
            "workspaceBytes": workspace_maximum,
            "workspaceTreeSha256": export_receipt["sha256"],
            "boundary": (
                "Content-free credential-free black-box evidence of the exact Codex comparison tool catalog only. "
                "It grants no model, provider, credential, external-effect, completion, acceptance, or promotion authority."
            ),
        }
    finally:
        if workspace_keeper is not None:
            subprocess.run(["docker", "rm", "-f", workspace_keeper], capture_output=True, timeout=30)
        if workspace_volume is not None:
            subprocess.run(["docker", "volume", "rm", workspace_volume], capture_output=True, timeout=30)
        if started_server:
            subprocess.run(["docker", "rm", "-f", server], capture_output=True, timeout=30)
        if created_network:
            subprocess.run(["docker", "network", "rm", network], capture_output=True, timeout=30)
        shutil.rmtree(temporary, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--image", default="pixel-codex-comparison-candidate:0.147.0")
    args = parser.parse_args()
    try:
        print(json.dumps(qualify(args.root, args.image), sort_keys=True, separators=(",", ":")))
        return 0
    except (evaluation.OutcomeError, OSError, UnicodeError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        print(f"[pixel] ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
