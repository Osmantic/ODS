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

from project_runtime_protocol import select_project_runtime


def verify_runtime(image, *, runtime="npm"):
    """Confirm current Docker availability and the configured immutable image."""
    stage_arguments(image, 'ods-project-' + '0' * 24, 'build', runtime=runtime)
    result = subprocess.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', image],
                            capture_output=True, text=True, timeout=15, check=True)
    if result.stdout.strip() != image:
        raise ValueError('project runtime image identity mismatch')


def seed_project(image: str, job: str, snapshot: dict, *, manifests_only: bool, runtime: str = "npm") -> None:
    """Transfer a trusted no-follow snapshot through stdin, never a host mount.

    This is an internal controller primitive, not an untrusted API. The network
    acquisition stage must run before the full source transfer.
    """
    files = snapshot["files"]
    if select_project_runtime(files) != runtime:
        raise ValueError("snapshot runtime does not match execution binding")
    if manifests_only:
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            manifests = (("ods-project.json", "requirements.lock") if runtime == "python"
                         else ("package.json", "package-lock.json"))
            for name in manifests:
                member = tarfile.TarInfo(name)
                member.size, member.mode = len(files[name]), 0o644
                member.uid = member.gid = 1000
                archive.addfile(member, io.BytesIO(files[name]))
        payload = stream.getvalue()
    else:
        payload = snapshot["archive"]
    args = stage_arguments(image, job, "build", runtime=runtime)
    args[args.index("--name") + 1] = job + ("-seed-manifests" if manifests_only else "-seed-source")
    args = args[:args.index(image) + 1]
    subprocess.run([*args[:2], "-i", *args[2:], "tar", "-xf", "-", "--no-same-owner"],
                   input=payload, capture_output=True, check=True, timeout=60)


# These programs are controller-owned, never read from the project. -I keeps
# source, PYTHONPATH and user site packages out of the trusted bootstrap.
PYTHON_TEST = """import json, os, pathlib, subprocess, sys
root = pathlib.Path('/home/node')
env = root / '.ods-python-env'
subprocess.run([sys.executable, '-I', '-m', 'venv', str(env)], check=True)
python = str(env / 'bin/python')
subprocess.run([python, '-I', '-m', 'pip', '--isolated', '--disable-pip-version-check',
    '--no-cache-dir', 'install', '--no-index', '--find-links=/home/node/.ods-python-wheels',
    '--require-hashes', '--only-binary=:all:', '-r', '/home/node/requirements.lock'], check=True)
# A zero exit alone is insufficient: project imports can call os._exit(0)
# before unittest discovery finishes. Require bounded completion evidence from
# the runner as well. This is execution evidence, not trust in project tests.
read_fd, write_fd = os.pipe()
runner = "import json, os, sys, unittest; receipt=int(sys.argv[1]); sys.path.insert(0, '/home/node'); suite=unittest.defaultTestLoader.discover('/home/node/tests'); count=suite.countTestCases(); print('ODS discovered tests:', count, flush=True); result=unittest.TextTestRunner(verbosity=2).run(suite); os.write(receipt, json.dumps({'tests':count,'success':result.wasSuccessful()}).encode()); os.close(receipt); sys.exit(0 if count and result.wasSuccessful() else 1)"
try:
    result = subprocess.run([python, '-I', '-c', runner, str(write_fd)], pass_fds=(write_fd,))
finally:
    os.close(write_fd)
# A project can leave descendants holding the descriptor. Never wait for EOF.
os.set_blocking(read_fd, False)
try:
    try:
        receipt = json.loads(os.read(read_fd, 1024))
    except (BlockingIOError, ValueError):
        receipt = None
finally:
    os.close(read_fd)
if (result.returncode != 0 or not isinstance(receipt, dict)
        or set(receipt) != {'tests', 'success'} or type(receipt['tests']) is not int
        or receipt['tests'] <= 0 or receipt['success'] is not True):
    sys.exit('Python tests did not produce a successful completion receipt')
"""
PYTHON_BUILD = """import runpy, sys
sys.path.insert(0, '/home/node')
runpy.run_path('/home/node/main.py', run_name='__main__')
"""


def stage_arguments(image: str, job: str, stage: str, *, runtime: str = "npm") -> list[str]:
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
    if runtime == "python":
        commands = {
            "acquire": ["python", "-I", "-m", "pip", "--isolated", "--disable-pip-version-check",
                        "--no-cache-dir", "download", "--no-deps", "--require-hashes", "--only-binary=:all:",
                        "--index-url=https://pypi.org/simple", "--dest=/home/node/.ods-python-wheels",
                        "-r", "/home/node/requirements.lock"],
            "test": ["python", "-I", "-c", PYTHON_TEST],
            "build": ["/home/node/.ods-python-env/bin/python", "-I", "-c", PYTHON_BUILD],
        }
    elif runtime != "npm":
        raise ValueError("unsupported project runtime")
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
              timeout: float = 240, runtime: str = "npm") -> dict:
    """Observe one execution; drain bounded logs and stop its exact container.

    Leaves containers/volume for the caller to inspect and clean up. Never
    retries a failed or interrupted stage, and never reports it as success.
    """
    args = stage_arguments(image, job, stage, runtime=runtime)
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
    requested_interruption = None
    while process.poll() is None:
        if cancel.wait(0.05) or time.monotonic() >= deadline:
            interrupted = "cancelled" if cancel.is_set() else "timed_out"
            requested_interruption = interrupted
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
    if interrupted == "unconfirmed" and requested_interruption:
        # Stop acknowledgement can race container creation or normal exit.
        # Only identity-checked Docker evidence can confirm it is now stopped;
        # the attached CLI exiting alone does not prove container termination.
        observed = observe_stage(image, job, stage, runtime=runtime)
        if observed.get("evidence") == "docker-state" and observed["status"] in ("succeeded", "failed"):
            interrupted = requested_interruption
    return {"status": interrupted or ("succeeded" if code == 0 else "failed"),
            "exitCode": code, "started": True,
            "stdout": buffers[0].decode("utf-8", errors="replace"),
            "stderr": buffers[1].decode("utf-8", errors="replace"),
            "truncated": {"stdout": truncated[0], "stderr": truncated[1]}}


def observe_stage(image: str, job: str, stage: str, *, runtime: str = "npm") -> dict:
    """Read existing Docker evidence after interruption; never launch/retry.

    A failed lookup means unknown, including when Docker itself is unavailable.
    Recovered exit status cannot recreate lost stdout or assert test counts.
    """
    args = stage_arguments(image, job, stage, runtime=runtime)
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
