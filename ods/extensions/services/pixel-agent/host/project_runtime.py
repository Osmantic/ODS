"""Fixed Docker stages for the Portal project controller.

Internal only: callers must bind and validate the project snapshot and lock
before invoking acquisition. No host mounts or model-supplied Docker options.
The controller supplies these internal primitives to the installed project tool.
"""
from __future__ import annotations

import re
import io
import json
import subprocess
import tarfile
import threading
import time

from project_runtime_protocol import validate_project_lock


def verify_runtime(image):
    """Confirm current Docker availability and the configured immutable image."""
    stage_arguments(image, 'ods-project-' + '0' * 24, 'build')
    result = subprocess.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', image],
                            capture_output=True, text=True, timeout=15, check=True)
    if result.stdout.strip() != image:
        raise ValueError('project runtime image identity mismatch')


def seed_project(image: str, job: str, snapshot: dict, *, manifests_only: bool) -> None:
    """Transfer a trusted no-follow snapshot through stdin, never a host mount.

    This is an internal controller primitive, not an untrusted API. The network
    acquisition stage must run before the full source transfer.
    """
    files = snapshot["files"]
    validate_project_lock(json.loads(files["package.json"]), json.loads(files["package-lock.json"]))
    if manifests_only:
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            for name in ("package.json", "package-lock.json"):
                member = tarfile.TarInfo(name)
                member.size, member.mode = len(files[name]), 0o644
                member.uid = member.gid = 1000
                archive.addfile(member, io.BytesIO(files[name]))
        payload = stream.getvalue()
    else:
        payload = snapshot["archive"]
    args = stage_arguments(image, job, "build")
    args[args.index("--name") + 1] = job + ("-seed-manifests" if manifests_only else "-seed-source")
    args = args[:args.index(image) + 1]
    subprocess.run([*args[:2], "-i", *args[2:], "tar", "-xf", "-", "--no-same-owner"],
                   input=payload, capture_output=True, check=True, timeout=60)


def stage_arguments(image: str, job: str, stage: str) -> list[str]:
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", image):
        raise ValueError("immutable local image id required")
    if not re.fullmatch(r"ods-project-[a-f0-9]{24}", job):
        raise ValueError("invalid controller job identity")
    commands = {
        "acquire": ["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund",
                    "--registry=https://registry.npmjs.org"],
        "test": ["npm", "test"],
        "build": ["npm", "run", "build"],
    }
    if stage not in commands:
        raise ValueError("unsupported project stage")
    return ["docker", "run", "--name", job + "-" + stage,
            "--label", "org.osmantic.ods.project-job=" + job,
            "--network", "bridge" if stage == "acquire" else "none",
            "--read-only", "--user", "1000:1000", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--pids-limit", "256",
            "--memory", "2g", "--cpus", "2", "--log-driver", "none",
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=256m",
            "--mount", "type=volume,source=" + job + ",target=/home/node",
            "--workdir", "/home/node", image, *commands[stage]]


def run_stage(image: str, job: str, stage: str, *, cancel: threading.Event,
              timeout: float = 240) -> dict:
    """Observe one execution; drain bounded logs and stop its exact container.

    Leaves containers/volume for the caller to inspect and clean up. Never
    retries a failed or interrupted stage, and never reports it as success.
    """
    args = stage_arguments(image, job, stage)
    if not 0 < timeout <= 600:
        raise ValueError("invalid project timeout")
    if cancel.is_set():
        return {"status": "cancelled", "exitCode": None, "started": False}
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    limit = 64 * 1024
    buffers = [bytearray(), bytearray()]
    truncated = [False, False]
    def drain(index, stream):
        with stream:
            while chunk := stream.read(4096):
                room = max(0, limit - len(buffers[index]))
                buffers[index].extend(chunk[:room])
                truncated[index] |= len(chunk) > room
    readers = [threading.Thread(target=drain, args=(i, stream), daemon=True)
               for i, stream in enumerate((process.stdout, process.stderr))]
    for reader in readers:
        reader.start()
    deadline = time.monotonic() + timeout
    interrupted = None
    while process.poll() is None:
        if cancel.wait(0.05) or time.monotonic() >= deadline:
            interrupted = "cancelled" if cancel.is_set() else "timed_out"
            # This exact name belongs to this invocation; no broad cleanup.
            try:
                stopped = subprocess.run(["docker", "stop", "--time", "1", job + "-" + stage],
                                         capture_output=True, timeout=15, check=False)
                if stopped.returncode != 0:
                    interrupted = "unconfirmed"
            except subprocess.TimeoutExpired:
                interrupted = "unconfirmed"
            break
    try:
        code = process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        code = process.wait(timeout=5)
        interrupted = interrupted or "unconfirmed"
    for reader in readers:
        reader.join(timeout=5)
    return {"status": interrupted or ("succeeded" if code == 0 else "failed"),
            "exitCode": code, "started": True,
            "stdout": buffers[0].decode("utf-8", errors="replace"),
            "stderr": buffers[1].decode("utf-8", errors="replace"),
            "truncated": {"stdout": truncated[0], "stderr": truncated[1]}}


def observe_stage(image: str, job: str, stage: str) -> dict:
    """Read existing Docker evidence after interruption; never launch/retry.

    A failed lookup means unknown, including when Docker itself is unavailable.
    Recovered exit status cannot recreate lost stdout or assert test counts.
    """
    args = stage_arguments(image, job, stage)
    unknown = {"status": "unconfirmed", "exitCode": None, "evidence": "unavailable"}
    try:
        read = subprocess.run(["docker", "inspect", job + "-" + stage],
                              capture_output=True, timeout=15, check=False)
        if read.returncode != 0 or len(read.stdout) > 1024 * 1024:
            return unknown
        values = json.loads(read.stdout)
        if not isinstance(values, list) or len(values) != 1:
            return unknown
        value = values[0]
        config, host, state = value["Config"], value["HostConfig"], value["State"]
        volumes = [m for m in value["Mounts"] if m["Type"] == "volume"]
        if (config["Image"] != image or config["Cmd"] != args[args.index(image) + 1:]
                or config.get("Entrypoint") not in (None, []) or config["User"] != "1000:1000"
                or config.get("Labels", {}).get("org.osmantic.ods.project-job") != job
                or host["NetworkMode"] != ("bridge" if stage == "acquire" else "none")
                or host["ReadonlyRootfs"] is not True
                or host.get("Privileged") is not False
                or host.get("CapAdd") not in (None, []) or host.get("CapDrop") != ["ALL"]
                or "no-new-privileges" not in (host.get("SecurityOpt") or [])
                or host.get("Memory") != 2 * 1024 ** 3 or host.get("NanoCpus") != 2 * 10 ** 9
                or host.get("PidsLimit") != 256
                or any(m["Type"] == "bind" for m in value["Mounts"])
                or len(volumes) != 1 or volumes[0]["Name"] != job
                or volumes[0]["Destination"] != "/home/node"
                or value.get("RestartCount", 0) != 0):
            return {**unknown, "evidence": "identity-mismatch"}
        if state["Running"] is True:
            return {"status": "running", "exitCode": None, "evidence": "docker-state"}
        if (state["Status"] != "exited" or state.get("Error")
                or not state.get("StartedAt") or state["StartedAt"].startswith("0001-")):
            return unknown
        code = state["ExitCode"]
        if type(code) is not int:
            return unknown
        return {"status": "succeeded" if code == 0 else "failed", "exitCode": code,
                "evidence": "docker-state", "stdout": "", "stderr": "",
                "outputUnavailable": True, "truncated": {"stdout": True, "stderr": True}}
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
        return unknown
