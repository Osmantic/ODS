#!/usr/bin/env python3
"""Disposable-host candidate execution harness (qualification-root only).

This harness is deliberately separate from the release-update core. It executes
candidate code exclusively from the verified activation source inside a
fail-closed, digest-pinned Docker sandbox under bounded, private custody inside
the qualification root. The durable execution specification is fixed by the
`qualification-execution-claim` command (never by this harness, which accepts no
--probe/--probe-timeout) and this harness reads only the claim-bound fixed spec.

Honest boundary: the full candidate install/activation contract is deferred, so
the harness runs only the claim-bound candidate probe inside the sandbox. A
successful probe is explicitly NOT full install success: it is recorded as
`deferred` (phase=install, reason=install-contract-deferred). The harness writes
a fixed EXECUTION-START attempt marker (O_EXCL + fsync) before any Docker
create/start; this is the exclusive exactly-once attempt boundary. The Docker
lifecycle owns cleanup in a finally block once create/start is attempted and the
terminal observation is written only after container absence is proven.
"""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "release-update.py"
RELEASE_MANIFEST = ROOT / "RELEASE-MANIFEST.json"
MODULE_NAME = "pixel_release_update"

DOCKER = "/usr/bin/docker"
RELEASE_CONSTANTS = ROOT / "scripts" / "generated" / "release-constants.json"
SCRUBBED_ENV = {"PATH": "/usr/bin:/bin"}


class UpdateError(RuntimeError):
    pass


class SandboxError(UpdateError):
    pass


def load_release_update():
    specification = importlib.util.spec_from_file_location(MODULE_NAME, SCRIPT)
    if specification is None or specification.loader is None:
        raise RuntimeError("release-update module is unavailable")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def candidate_id_valid(candidate_id):
    return isinstance(candidate_id, str) and re.fullmatch(
        r"pixel-[0-9]{1,6}(?:\.[0-9]{1,6}){2}-[0-9a-f]{64}", candidate_id,
    ) is not None


def docker_available():
    return os.path.isfile(DOCKER) and os.access(DOCKER, os.X_OK)


def load_release_constants():
    """Load and validate the generated release constants and derive the
    qualification sandbox image and its exact digest from the pinned baseImage."""
    try:
        payload = RELEASE_CONSTANTS.read_bytes()
    except OSError as exc:
        raise SandboxError("generated release constants are unavailable") from exc
    try:
        data = json.loads(payload)
    except ValueError as exc:
        raise SandboxError("generated release constants are unparseable") from exc
    base_image = data.get("baseImage")
    if not isinstance(base_image, str):
        raise SandboxError("generated release constants baseImage is invalid")
    match = re.fullmatch(r"[^@\s]+@(sha256:[0-9a-f]{64})", base_image)
    if match is None:
        raise SandboxError("generated release constants baseImage is not digest pinned")
    return base_image, match.group(1)


def verify_spec_image(spec):
    """Fail closed unless the fixed spec's image and digest exactly match the
    digest derived from the generated release constants."""
    image, digest = load_release_constants()
    runtime = spec["runtime"]
    if runtime.get("image") != image or runtime.get("imageDigest") != digest:
        raise SandboxError("execution spec image does not match the generated release constants")
    return digest


def verify_docker_image(image, expected_digest):
    """Prove the pinned image's local identity before any candidate can run."""
    if not docker_available():
        raise SandboxError("docker is unavailable on this host")
    try:
        result = subprocess.run(
            [DOCKER, "image", "inspect", image],
            capture_output=True, text=True, env=SCRUBBED_ENV, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SandboxError("docker image inspect could not be completed") from exc
    if result.returncode != 0:
        raise SandboxError(
            "pinned docker image is not present locally; refusing to pull or execute"
        )
    try:
        inspected = json.loads(result.stdout)
    except ValueError as exc:
        raise SandboxError("docker image inspect returned unparseable output") from exc
    if not inspected:
        raise SandboxError("docker image inspect returned no image")
    record = inspected[0]
    identity = record.get("Id") or ""
    repo_digests = record.get("RepoDigests") or []
    if not isinstance(repo_digests, list):
        raise SandboxError("docker image identity does not match the pinned digest")
    repo_suffixes = []
    for item in repo_digests:
        if isinstance(item, str) and "@" in item:
            repo_suffixes.append(item.split("@", 1)[1])
    if identity != expected_digest and expected_digest not in repo_suffixes:
        raise SandboxError("docker image identity does not match the pinned digest")


def docker_run_argv(spec, source_root, labels):
    runtime = spec["runtime"]
    argv = [
        DOCKER, "run", "--name", runtime["containerName"], "--detach", "--pull=never",
        "--network", "none", "--read-only",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
        "--pids-limit", str(spec["pidsLimit"]),
        "--memory", spec["memory"], "--memory-swap", spec["memory"],
        "--cpus", spec["cpus"],
        "--cgroupns", "private", "--ipc", "none",
        "--log-driver", spec["logDriver"],
        "--user", f"{runtime['uid']}:{runtime['gid']}",
        "--tmpfs", spec["scratch"],
        "--workdir", spec["workdir"],
        "-v", f"{source_root}:/candidate:ro",
    ]
    for key, value in labels.items():
        argv.append("--label")
        argv.append(f"{key}={value}")
    argv.append("--entrypoint")
    argv.append(spec["entrypoint"])
    argv.append(runtime["image"])
    argv.extend(spec["argv"])
    return argv


def container_absent(name):
    """Prove the claim-bound container's absence exactly and fail closed.

    Absence is proven only when `docker container inspect <name>` exits nonzero
    with empty JSON stdout and the exact Docker `No such container: <name>`
    message on stderr. A zero exit (container exists), a timeout, an OSError
    (daemon/permission unavailable), any other nonzero status, or malformed
    output is ambiguity and raises SandboxError.
    """
    try:
        inspect = subprocess.run(
            [DOCKER, "container", "inspect", name],
            capture_output=True, text=True, env=SCRUBBED_ENV, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SandboxError("docker container inspect could not be completed") from exc
    if inspect.returncode == 0:
        return False  # container exists; absence not proven
    if inspect.returncode != 1:
        raise SandboxError(f"sandbox container absence could not be proven for {name}")
    if inspect.stdout.strip() != "[]":
        raise SandboxError(f"sandbox container absence could not be proven for {name}")
    try:
        payload = json.loads(inspect.stdout)
    except ValueError as exc:
        raise SandboxError(f"sandbox container absence could not be proven for {name}") from exc
    if payload != []:
        raise SandboxError(f"sandbox container absence could not be proven for {name}")
    expected_stderr = f"Error response from daemon: No such container: {name}"
    if inspect.stderr.strip() != expected_stderr:
        raise SandboxError(f"sandbox container absence could not be proven for {name}")
    return True


def container_id_valid(container_id):
    return isinstance(container_id, str) and re.fullmatch(
        r"[0-9a-f]{64}", container_id,
    ) is not None


def parse_detach_id(stdout):
    """Parse `docker run --detach` stdout as exactly one immutable 64-hex ID.

    The immutable container ID is the only safe handle for later wait/kill/rm.
    Any empty, malformed, multi-line, or otherwise extra output is ambiguous and
    fails closed (raises SandboxError); a valid parse returns the exact 64-hex
    container ID.
    """
    value = stdout.strip()
    if not container_id_valid(value):
        raise SandboxError(
            "docker run --detach did not return exactly one immutable container id"
        )
    return value


def kill_container(container_id):
    try:
        subprocess.run(
            [DOCKER, "kill", "-s", "KILL", container_id],
            capture_output=True, text=True, env=SCRUBBED_ENV, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        subprocess.run(
            [DOCKER, "wait", container_id],
            capture_output=True, text=True, env=SCRUBBED_ENV, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def inspect_container_by_id(container_id):
    """Inspect by the immutable container ID, returning the record or None.

    The ID must satisfy the exact 64-hex grammar and the inspected record's Id
    must equal the requested ID; anything else is ambiguity and fails closed.
    Absence is proven only via the exact Docker `No such container: <id>`
    contract and returns None.
    """
    if not container_id_valid(container_id):
        raise SandboxError("container id does not match the 64-hex grammar")
    try:
        inspect = subprocess.run(
            [DOCKER, "container", "inspect", container_id],
            capture_output=True, text=True, env=SCRUBBED_ENV, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SandboxError("docker container inspect could not be completed") from exc
    if inspect.returncode == 0:
        try:
            payload = json.loads(inspect.stdout)
        except ValueError as exc:
            raise SandboxError("docker container inspect returned unparseable output") from exc
        if not payload:
            raise SandboxError("docker container inspect returned no container")
        record = payload[0]
        inspected_id = record.get("Id") or ""
        if inspected_id != container_id or not container_id_valid(inspected_id):
            raise SandboxError("inspected container id does not match the requested id")
        return record
    if inspect.returncode == 1 and inspect.stdout.strip() == "[]" and (
        inspect.stderr.strip() == f"Error response from daemon: No such container: {container_id}"
    ):
        return None
    raise SandboxError(f"container custody could not be determined for {container_id}")


def inspect_container_by_name(name):
    """Inspect by the claim-bound name, returning the record or None when absent.

    Absence follows the exact `No such container: <name>` contract. Ownership is
    not judged here; callers validate custody before mutating.
    """
    try:
        inspect = subprocess.run(
            [DOCKER, "container", "inspect", name],
            capture_output=True, text=True, env=SCRUBBED_ENV, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SandboxError("docker container inspect could not be completed") from exc
    if inspect.returncode == 0:
        try:
            payload = json.loads(inspect.stdout)
        except ValueError as exc:
            raise SandboxError("docker container inspect returned unparseable output") from exc
        if not payload:
            raise SandboxError("docker container inspect returned no container")
        return payload[0]
    if inspect.returncode == 1 and inspect.stdout.strip() == "[]" and (
        inspect.stderr.strip() == f"Error response from daemon: No such container: {name}"
    ):
        return None
    raise SandboxError(f"claim-bound container custody could not be determined for {name}")


def validate_ownership(record, name, expected):
    """Validate that `record` is exactly our owned container and return its Id.

    Custody requires the exact claim-bound name, the exact Config.Image and
    image digest, and every start/spec/candidate custody label to match. A
    foreign or mismatched record fails closed (raises SandboxError) and is never
    mutated. The returned Id is validated to the exact 64-hex grammar so all
    later mutations bind to the immutable ID.
    """
    config = record.get("Config") or {}
    image_matches = (
        config.get("Image") == expected["image"]
        and record.get("Image") == expected["imageDigest"]
    )
    labels = config.get("Labels") or {}
    labels_match = all(
        labels.get(key) == value for key, value in expected["labels"].items()
    )
    name_match = record.get("Name") == "/" + name
    if not (name_match and image_matches and labels_match):
        raise SandboxError(
            "claim-bound container is foreign or mismatched; refusing to mutate it"
        )
    container_id = record.get("Id") or ""
    if not container_id_valid(container_id):
        raise SandboxError("owned container id does not match the 64-hex grammar")
    return container_id


def cleanup_docker(name, expected, container_id=None):
    """Kill/remove only the exact owned container and prove name absence.

    When the immutable ID from `docker run --detach` is available, custody is
    re-validated by that exact ID and all mutation (kill/wait/rm) binds to the
    ID only. When the ID is unavailable (docker-run timeout/error), cleanup
    discovers an owned record by the claim-bound name, extracts and validates
    its 64-hex Id, and mutates only by that ID. A foreign/mismatched container
    fails closed and is left untouched. Absence of the claim-bound name is
    proven exactly afterward; a foreign container that reappears under the name
    fails closed and is never removed.
    """
    if container_id is not None:
        if not container_id_valid(container_id):
            raise SandboxError("container id does not match the 64-hex grammar")
        record = inspect_container_by_id(container_id)
        if record is not None:
            validate_ownership(record, name, expected)
            kill_container(container_id)
            try:
                subprocess.run(
                    [DOCKER, "rm", "-f", container_id],
                    capture_output=True, text=True, env=SCRUBBED_ENV, timeout=30,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise SandboxError("sandbox container removal could not be completed") from exc
    else:
        record = inspect_container_by_name(name)
        if record is not None:
            owned_id = validate_ownership(record, name, expected)
            kill_container(owned_id)
            try:
                subprocess.run(
                    [DOCKER, "rm", "-f", owned_id],
                    capture_output=True, text=True, env=SCRUBBED_ENV, timeout=30,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise SandboxError("sandbox container removal could not be completed") from exc
    if not container_absent(name):
        raise SandboxError("sandbox container could not be removed; absence not proven")


def run_docker_sandbox(spec, source_root, start_bytes):
    """Start the exact sandboxed probe and return its faithful probe result.

    The container command, environment, workdir, and runtime identity are
    derived solely from the validated execution spec. The started attempt is
    bound into exact Docker custody labels from the start marker. `docker run
    --detach` stdout is parsed as exactly one immutable 64-hex container ID; the
    started container is immediately inspected by that exact ID and ownership is
    required before any wait. Once create/start is attempted, cleanup (inspect,
    kill/wait/remove, absence proof) is owned in a finally block, mutates only
    by the immutable ID, and absence-proof failure propagates (no terminal
    observation is written).
    """
    name = spec["runtime"]["containerName"]
    start = json.loads(start_bytes)
    labels = {
        "pixel.qualification.attemptId": start["attemptId"],
        "pixel.qualification.candidateId": start["candidateId"],
        "pixel.qualification.executionSpecSha256": start["executionSpecSha256"],
        "pixel.qualification.executionStartSha256": hashlib.sha256(start_bytes).hexdigest(),
    }
    expected = {
        "image": spec["runtime"]["image"],
        "imageDigest": spec["runtime"]["imageDigest"],
        "labels": labels,
    }
    argv = docker_run_argv(spec, source_root, labels)
    container_id = None
    probe_result = None
    invoked = False
    try:
        try:
            started = subprocess.run(argv, capture_output=True, text=True, env=SCRUBBED_ENV, timeout=90)
        except subprocess.TimeoutExpired as exc:
            invoked = True
            raise SandboxError("docker run did not return within the start window") from exc
        except OSError as exc:
            raise SandboxError("docker run could not be invoked") from exc
        invoked = True
        if started.returncode != 0:
            message = (started.stderr or started.stdout or "").strip()
            raise SandboxError(f"docker sandbox could not be started: {message}")
        container_id = parse_detach_id(started.stdout)
        record = inspect_container_by_id(container_id)
        if record is None:
            raise SandboxError("started container could not be inspected by id")
        validate_ownership(record, name, expected)
        try:
            wait = subprocess.run(
                [DOCKER, "wait", container_id],
                capture_output=True, text=True, env=SCRUBBED_ENV,
                timeout=spec["timeoutSeconds"] + 5,
            )
        except subprocess.TimeoutExpired:
            probe_result = {"outcome": "timeout", "phase": "probe", "reason": None,
                            "exitCode": None, "signal": None, "timedOut": True}
        except OSError as exc:
            raise SandboxError("docker wait could not be invoked") from exc
        else:
            if wait.returncode != 0:
                raise SandboxError("docker wait failed to observe the sandbox exit")
            try:
                exit_code = int(wait.stdout.strip())
            except ValueError as exc:
                raise SandboxError("docker wait returned an unparseable exit code") from exc
            if exit_code == 137:
                probe_result = {"outcome": "signal", "phase": "probe", "reason": None,
                                "exitCode": None, "signal": "SIGKILL", "timedOut": False}
            else:
                probe_result = {"outcome": "failure", "phase": "probe", "reason": "non-zero-exit",
                                "exitCode": int(exit_code), "signal": None, "timedOut": False}
    finally:
        if invoked:
            cleanup_docker(name, expected, container_id)
    return probe_result


def observation_for_probe_result(probe_result):
    if probe_result["outcome"] == "failure" and probe_result["exitCode"] == 0:
        return {
            "outcome": "deferred", "phase": "install",
            "reason": "install-contract-deferred", "exitCode": None,
            "signal": None, "timedOut": False,
        }
    return {
        "outcome": probe_result["outcome"],
        "phase": probe_result["phase"],
        "reason": probe_result["reason"],
        "exitCode": probe_result["exitCode"],
        "signal": probe_result["signal"],
        "timedOut": probe_result["timedOut"],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="bounded disposable-host candidate execution probe")
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--allowed-signers", required=True, type=Path)
    parser.add_argument("--identity", required=True)
    parser.add_argument("--qualification-root", required=True, type=Path)
    parser.add_argument("--baseline-version", required=True)
    parser.add_argument("--production-install-root", required=True, type=Path)
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args(argv)

    if not args.confirm:
        raise UpdateError("qualification host execution requires --confirm")
    if not candidate_id_valid(args.candidate_id):
        raise UpdateError("qualification candidate ID is invalid")
    if sys.platform != "linux":
        raise UpdateError("qualification host execution is supported only on qualified Linux hosts")
    if not args.qualification_root.is_absolute():
        raise UpdateError("qualification root path must be absolute")

    ru = load_release_update()

    try:
        return _run(ru, args)
    except ru.UpdateError as exc:
        raise UpdateError(str(exc)) from exc


def _run(ru, args):
    namespace = argparse.Namespace(
        candidate_id=args.candidate_id,
        allowed_signers=args.allowed_signers,
        identity=args.identity,
        qualification_root=args.qualification_root,
        baseline_version=args.baseline_version,
        production_install_root=args.production_install_root,
    )
    with ru.exclusive_stage_lock(args.qualification_root):
        state = ru._qualification_execution_state_locked(
            namespace,
            activation_files=set(ru.QUALIFICATION_ACTIVATION_CLAIMED_FILES),
            run_files=set(ru.QUALIFICATION_RUN_CLAIMED_FILES),
        )
        # Block 1: parse and exactly revalidate the durable claim and fixed spec
        # before any candidate process may start.
        claim_path = state["activation"] / ru.QUALIFICATION_EXECUTION_CLAIM_FILE
        claim_bytes = ru.read_regular(claim_path, ru.MAX_STAGE_RECEIPT, "qualification execution claim")
        ru.assert_private_single_link(claim_path, "qualification execution claim")
        claim, spec_bytes, spec = ru.qualification_validate_execution_claim(state, claim_bytes)
        spec_path = state["run_dir"] / ru.QUALIFICATION_EXECUTION_SPEC_FILE
        ru.assert_private_single_link(spec_path, "qualification execution spec")
        spec = ru.qualification_validate_execution_spec(state, spec_bytes, claim)

        # The probe must exist in the revalidated activation source and match the
        # claim-bound spec exactly.
        source_root = state["activation"] / "source"
        probe_resolved = ru.qualification_execution_probe(source_root, spec["probeRelativePath"])
        if ru.sha256(probe_resolved.read_bytes()) != spec["probeSha256"]:
            raise ru.UpdateError("probe file differs from the fixed execution spec")

        # Fail closed before any candidate process may start if the pinned,
        # digest-proven sandbox image is not available locally or the Docker daemon
        # is unavailable. This non-mutating verification happens before the
        # EXECUTION-START marker is written: a missing image or unavailable daemon
        # consumes no execution attempt and writes no start marker because no
        # create/start was attempted.
        image = spec["runtime"]["image"]
        expected_digest = verify_spec_image(spec)
        verify_docker_image(image, expected_digest)

        # Block 2: exclusive exactly-once attempt boundary. Before any Docker
        # create/start, prove the claim-bound container name is exactly absent; if
        # it exists or the absence proof is ambiguous, write no start marker and
        # never mutate the occupying container.
        container_name = spec["runtime"]["containerName"]
        if not container_absent(container_name):
            raise ru.UpdateError(
                "claim-bound container name is already occupied; refusing to execute"
            )

        # Before any Docker create/start, durably O_EXCL-write and fsync the fixed
        # EXECUTION-START marker hash-bound to the validated claim/spec/acquisition
        # and container.
        start_path = state["run_dir"] / ru.QUALIFICATION_EXECUTION_START_FILE
        if start_path.exists() or start_path.is_symlink():
            raise ru.UpdateError("qualification execution attempt already started; re-execution is forbidden")
        attempt_id = secrets.token_hex(16)
        start = ru.qualification_execution_start_value(
            state, claim_bytes, spec_bytes, spec, attempt_id,
        )
        start_bytes = ru.canonical_json(start)
        try:
            ru.write_private(start_path, start_bytes)
        except FileExistsError as exc:
            raise ru.UpdateError("qualification execution attempt already started; re-execution is forbidden") from exc
        ru.fsync_directory(state["run_dir"])

        # Docker lifecycle owns cleanup once create/start is attempted; the start
        # marker hash-bound custody labels are set at create and absence is proven
        # before run_docker_sandbox returns.
        #
        # Boundary: the durable EXECUTION-START marker is written before any Docker
        # create/start. A Docker infrastructure failure after that point (for
        # example an invoke/start error that escapes run_docker_sandbox) leaves the
        # marker in place. That is intentional and fail-closed: the marker must not
        # be deleted or retried, no terminal observation is fabricated, and a later
        # run is rejected as re-execution. Because candidate code may or may not
        # have started, terminal evidence for such an infrastructure failure is
        # modeled explicitly as indeterminate by the separate
        # release-qualification-execution-interruption-record slice
        # (EXECUTION-INTERRUPTION.json + QUALIFICATION-EXECUTION-INTERRUPTION.json),
        # never as an EXECUTION-RESULT.json, an observation, or promotion evidence.
        probe_result = run_docker_sandbox(spec, source_root, start_bytes)
        observation = observation_for_probe_result(probe_result)
        observed = {
            "schemaVersion": 1,
            "operation": "pixel-release-qualification-execution-observation",
            "candidateId": args.candidate_id,
            **observation,
            "observedVersion": None,
            "candidateCodeExecuted": True,
            "executionObserved": True,
            "executionSpecSha256": ru.sha256(spec_bytes),
            "sandbox": {
                "image": spec["runtime"]["image"],
                "imageDigest": spec["runtime"]["imageDigest"],
                "containerName": spec["runtime"]["containerName"],
                "network": spec["network"],
                "capDropAll": spec["capDropAll"],
                "noNewPrivileges": spec["noNewPrivileges"],
                "readOnlyRoot": spec["readOnlyRoot"],
                "uid": spec["runtime"]["uid"],
                "gid": spec["runtime"]["gid"],
            },
            "containerRemoved": True,
            "absenceProven": True,
            "networkUsed": spec["network"] != "none",
            "terminalPromotionEvidence": False,
            **ru.qualification_authority_fields(),
            "boundary": ru.QUALIFICATION_EXECUTION_OBSERVATION_BOUNDARY,
        }
        observation_path = state["run_dir"] / ru.QUALIFICATION_EXECUTION_OBSERVATION_FILE
        if observation_path.exists() or observation_path.is_symlink():
            raise ru.UpdateError("qualification execution observation already recorded; re-execution is forbidden")
        ru.write_private(observation_path, ru.canonical_json(observed))
        ru.fsync_directory(state["run_dir"])
        return observed


if __name__ == "__main__":
    try:
        result = main()
        import json as _json
        print(_json.dumps(result, sort_keys=True))
        raise SystemExit(0)
    except UpdateError as exc:
        print(f"Pixel qualification host execution rejected: {exc}", file=sys.stderr)
        raise SystemExit(1)
