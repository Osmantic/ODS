#!/usr/bin/env python3
"""Sign, inspect, or privately stage one exact Pixel release-update bundle without executing it."""

from __future__ import annotations

import argparse
import ast
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import errno
import hashlib
import hmac
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import select
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
NAMESPACE = "pixel-release-update"
QUALIFICATION_NAMESPACE = "pixel-release-update-qualification"
BOUNDARY = "Signed release metadata for verification and preparation only; activation requires a separate exact confirmation."
RELEASE_IDENTITY_BOUNDARY = "Content-free source and baseline-qualification identity only. This record does not prove installed-byte integrity, runtime health, provider capability, release promotion, publication, deployment approval, or client acceptance."
STAGE_BOUNDARY = "Verified release bytes staged privately without extraction or execution; activation requires a separate exact confirmation."
REHEARSAL_BOUNDARY = "Verified candidate compatibility rehearsal only; candidate programs were not executed and the active deployment was not changed."
ACTIVATION_BOUNDARY = "Single-use exact activation claimed on a private verified copy; candidate programs have not yet executed and the active deployment has not changed."
ACTIVATION_RESULT_BOUNDARY = "Terminal-confirmed candidate execution finished; this content-free receipt records the observed active version and update-bound rollback availability."
REACTIVATION_BOUNDARY = "Single-use exact reactivation claimed only after a verified activation and successful rollback; candidate programs have not yet re-executed and the active deployment has not changed."
REACTIVATION_RESULT_BOUNDARY = "Terminal-confirmed reactivation execution finished; this content-free receipt records the observed active version and new rollback availability."
REACTIVATION_RETRY_BOUNDARY = "Exact append-only authorization for one new hash-bound reactivation attempt after a terminal failure that provably left the restored release active with no rollback marker or rollback claim; no candidate code executes during authorization."
REACTIVATION_LIVE_MUTATION_MARKER = b"pixel-release-live-mutation-started-v1\n"
REACTIVATION_ROLLBACK_BOUNDARY = "Single-use exact rollback claimed against the reactivated release and its unchanged rollback marker; trusted controller rollback has not yet run."
REACTIVATION_ROLLBACK_RESULT_BOUNDARY = "Trusted controller reactivation rollback finished; this content-free receipt records whether the preceding Pixel version is active and recovery is required."
REACTIVATION_RECOVERY_BOUNDARY = "Content-free reactivation interruption diagnosis only; exact confirmation may finalize a missing trusted receipt or authorize a distinct later hash-bound retry after a proven no-mutation terminal failure, but recovery itself never executes candidate code or resumes deployment work."
ROLLBACK_BOUNDARY = "Single-use exact rollback claimed against the activated release and its unchanged rollback marker; trusted controller rollback has not yet run."
ROLLBACK_RESULT_BOUNDARY = "Trusted controller rollback finished; this content-free receipt records whether the preceding Pixel version is active and recovery is required."
RECOVERY_BOUNDARY = "Content-free interruption diagnosis only; exact confirmation may finalize a missing trusted receipt but never executes candidate code or resumes deployment work."
CLEANUP_BOUNDARY = "Completed rolled-back update evidence only; fixed staging copies are quarantined and a content-free audit tombstone is preserved before deletion."
CLEANUP_FAILED_ACTIVATION_BOUNDARY = "Completed terminal no-mutation activation failure evidence only; fixed staging copies are quarantined and a content-free audit tombstone is preserved before deletion."
ARCHIVE_BOUNDARY = "Terminal failed rollback evidence is preserved byte-for-byte outside the bounded staging namespace; no installed release or active deployment changes."
REACTIVATION_ARCHIVE_BOUNDARY = "Terminal no-live-mutation reactivation failure evidence only; fixed staging copies are archived byte-for-byte and a content-free receipt records the no-active-deployment-change outcome."
SPECIAL_OPERATOR_BINDING_FIELDS = (
    "controllerVersion", "controllerSourceCommit", "controllerSourceTree",
    "controllerArchiveSha256", "controllerEnvelopeSha256", "controllerSignatureSha256",
    "controllerDispatcherSha256", "controllerEngineSha256", "controllerCommandSha256",
    "controllerAuthority",
)
LEGACY_NO_MARKER_REACTIVATION_ARCHIVE_SOURCE = {
    "version": "4.3.15",
    "sourceCommit": "057ace691d2db552335349ca33187cf251e90f27",
    "sourceTree": "0f15f606d340b7eef4a3af49d2f754298aa429ce",
}
ARCHIVE_POLICY_BRIDGE_VERSION = "4.3.23"
ARCHIVE_POLICY_BRIDGE = {
    "schemaVersion": 1,
    "receiptSchema": "./schemas/release-update-archive-v1.schema.json",
    "operation": "exact-failed-rollback-preservation-outside-bounded-staging",
    "boundary": "terminal-failed-rollback-evidence-only-no-active-deployment-change",
}
REACTIVATION_ARCHIVE_POLICY_BRIDGE_VERSION = "4.3.27"
REACTIVATION_ARCHIVE_POLICY_BRIDGE = {
    "schemaVersion": 1,
    "receiptSchema": "./schemas/release-update-reactivation-archive-v1.schema.json",
    "operation": "exact-terminal-no-live-mutation-reactivation-preservation-outside-bounded-staging",
    "boundary": "terminal-no-live-mutation-reactivation-evidence-only-no-active-deployment-change",
}
QUALIFICATION_ROOT_BOUNDARY = "Disposable qualification-root attestation only; it grants no production staging, activation, publication, update, or trust authority."
QUALIFICATION_PREPARE_BOUNDARY = "Qualification-root-only private copy of a Candidate bundle; candidate programs were not extracted or executed and no production state was staged, activated, or changed."
QUALIFICATION_REHEARSAL_BOUNDARY = "Qualification-root-only candidate compatibility rehearsal; candidate programs were not executed and no production state or compatibility record was changed."
QUALIFICATION_ACTIVATION_BOUNDARY = "Qualification-root-only single-use activation claim on a private verified copy; candidate programs were not executed and no production activation or state change occurred."
QUALIFICATION_HOST_RUN_BOUNDARY = "Qualification-root-only private host-run acquisition for one already-claimed activation; candidate programs were not executed, no terminal promotion evidence was produced, and no production state changed."
QUALIFICATION_EXECUTION_TOMBSTONE_BOUNDARY = "Content-free execution tombstone proving this host-run acquisition slice never executed candidate programs, produced no terminal promotion evidence, and changed no production state."
QUALIFICATION_ACQUISITION_BOUNDARY = "Qualification-root-only private single-use host-run acquisition marker; candidate programs were not executed, no terminal promotion evidence was produced, and no production state changed."
QUALIFICATION_ACQUISITION_FILE = "QUALIFICATION-ACQUISITION.json"
QUALIFICATION_EXECUTION_CLAIM_FILE = "QUALIFICATION-EXECUTION-CLAIM.json"
QUALIFICATION_EXECUTION_SPEC_FILE = "QUALIFICATION-EXECUTION-SPEC.json"
QUALIFICATION_EXECUTION_START_FILE = "EXECUTION-START.json"
QUALIFICATION_EXECUTION_RESULT_FILE = "EXECUTION-RESULT.json"
QUALIFICATION_EXECUTION_RESULT_MARKER = "QUALIFICATION-EXECUTION-RESULT.json"
QUALIFICATION_EXECUTION_OBSERVATION_FILE = "QUALIFICATION-EXECUTION-OBSERVATION.json"
QUALIFICATION_EXECUTION_INTERRUPTION_FILE = "EXECUTION-INTERRUPTION.json"
QUALIFICATION_EXECUTION_INTERRUPTION_MARKER = "QUALIFICATION-EXECUTION-INTERRUPTION.json"
QUALIFICATION_EXECUTION_CLAIM_BOUNDARY = "Qualification-root-only private single-use execution claim for one acquired host-run; written and fsynced before any candidate process may start, forbids automatic re-execution after any crash or interruption, and grants no production, publication, compatibility, or promotion authority."
QUALIFICATION_EXECUTION_SPEC_BOUNDARY = "Qualification-root-only fixed, durable, owner-private execution specification for one claimed host-run; written with O_EXCL and fsynced before the execution claim, it fixes probe, argv, cwd, environment, image, sandbox, resource, and container-name contract, and grants no production, publication, compatibility, or promotion authority."
QUALIFICATION_EXECUTION_START_BOUNDARY = "Qualification-root-only exclusive exactly-once execution-attempt start marker; written with O_EXCL and fsynced before any Docker create/start, it binds the validated claim, spec, and acquisition to the exact container name and grants no production, publication, compatibility, or promotion authority."
QUALIFICATION_EXECUTION_RESULT_BOUNDARY = "Content-free immutable qualification execution result; it accurately reports only the bounded candidate execution observed under disposable-host custody and grants no production, publication, compatibility, or promotion authority."
QUALIFICATION_EXECUTION_RESULT_MARKER_BOUNDARY = "Qualification-root-only private terminal execution-result marker; it binds the immutable execution result to the candidate and activation for deletion/recovery durability and grants no production, publication, compatibility, or promotion authority."
QUALIFICATION_EXECUTION_OBSERVATION_BOUNDARY = "Content-free disposable-host candidate-execution observation; it is not terminal promotion evidence and grants no production, publication, compatibility, or promotion authority."
QUALIFICATION_EXECUTION_INTERRUPTION_BOUNDARY = "Content-free immutable terminal interruption artifact for one post-start indeterminate qualification execution; it does not assert whether candidate code executed, records no observation or result, is not terminal promotion evidence, and grants no production, publication, compatibility, or promotion authority."
QUALIFICATION_EXECUTION_INTERRUPTION_MARKER_BOUNDARY = "Qualification-root-only private terminal interruption marker; it binds the immutable interruption artifact to the candidate and activation for deletion/recovery durability, records no observation or result, is not terminal promotion evidence, and grants no production, publication, compatibility, or promotion authority."
QUALIFICATION_EXECUTION_INTERRUPTION_REASON = "post-start-infrastructure-failure"
QUALIFICATION_EXECUTION_INTERRUPTION_STATE = "indeterminate"
QUALIFICATION_EXECUTION_DOCKER = "/usr/bin/docker"
QUALIFICATION_EXECUTION_INTERRUPTION_TIMEOUT = 30.0
QUALIFICATION_EXECUTION_INTERRUPTION_STREAM_LIMIT = 64 * 1024
QUALIFICATION_RUN_ACQUIRED_FILES = frozenset({"HARNESS-RUN.json", "EXECUTION-TOMBSTONE.json"})
QUALIFICATION_RUN_CLAIMED_FILES = frozenset({"HARNESS-RUN.json", "EXECUTION-TOMBSTONE.json", QUALIFICATION_EXECUTION_SPEC_FILE})
QUALIFICATION_RUN_STARTED_FILES = frozenset({"HARNESS-RUN.json", "EXECUTION-TOMBSTONE.json", QUALIFICATION_EXECUTION_SPEC_FILE, QUALIFICATION_EXECUTION_START_FILE})
QUALIFICATION_RUN_OBSERVED_FILES = frozenset({"HARNESS-RUN.json", "EXECUTION-TOMBSTONE.json", QUALIFICATION_EXECUTION_SPEC_FILE, QUALIFICATION_EXECUTION_START_FILE, QUALIFICATION_EXECUTION_OBSERVATION_FILE})
QUALIFICATION_RUN_RESULT_FILES = frozenset({"HARNESS-RUN.json", "EXECUTION-TOMBSTONE.json", QUALIFICATION_EXECUTION_SPEC_FILE, QUALIFICATION_EXECUTION_START_FILE, QUALIFICATION_EXECUTION_OBSERVATION_FILE, QUALIFICATION_EXECUTION_RESULT_FILE})
QUALIFICATION_RUN_INTERRUPTED_FILES = frozenset({"HARNESS-RUN.json", "EXECUTION-TOMBSTONE.json", QUALIFICATION_EXECUTION_SPEC_FILE, QUALIFICATION_EXECUTION_START_FILE, QUALIFICATION_EXECUTION_INTERRUPTION_FILE})
QUALIFICATION_ACTIVATION_ACQUIRED_FILES = frozenset({"source", "QUALIFICATION-ACTIVATION.json", QUALIFICATION_ACQUISITION_FILE})
QUALIFICATION_ACTIVATION_CLAIMED_FILES = frozenset({"source", "QUALIFICATION-ACTIVATION.json", QUALIFICATION_ACQUISITION_FILE, QUALIFICATION_EXECUTION_CLAIM_FILE})
QUALIFICATION_ACTIVATION_RESULT_FILES = frozenset({"source", "QUALIFICATION-ACTIVATION.json", QUALIFICATION_ACQUISITION_FILE, QUALIFICATION_EXECUTION_CLAIM_FILE, QUALIFICATION_EXECUTION_RESULT_MARKER})
QUALIFICATION_ACTIVATION_INTERRUPTED_FILES = frozenset({"source", "QUALIFICATION-ACTIVATION.json", QUALIFICATION_ACQUISITION_FILE, QUALIFICATION_EXECUTION_CLAIM_FILE, QUALIFICATION_EXECUTION_INTERRUPTION_MARKER})
QUALIFICATION_EXECUTION_PIDS_LIMIT = 64
QUALIFICATION_EXECUTION_MEMORY = "256m"
QUALIFICATION_EXECUTION_CPUS = "1"
QUALIFICATION_EXECUTION_SCRATCH_SIZE = "64m"
QUALIFICATION_EXECUTION_SCRUBBED_ENV = {"PATH": "/usr/bin:/bin"}
QUALIFICATION_EXECUTION_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "PIXEL_QUALIFICATION_CUSTODY": "1",
}
QUALIFICATION_EXECUTION_OUTCOMES = frozenset({"success", "failure", "timeout", "signal", "deferred"})
QUALIFICATION_EXECUTION_PHASES = frozenset({"probe", "install", "activate", "cleanup"})
QUALIFICATION_EXECUTION_REASONS = frozenset({
    "non-zero-exit", "candidate-rejected", "install-contract-deferred",
    "unsupported-host", "harness-error", "execution-interrupted",
})
QUALIFICATION_EXECUTION_SIGNALS = frozenset({"SIGTERM", "SIGKILL", "SIGINT", "SIGABRT", "SIGHUP", "SIGQUIT"})
QUALIFICATION_ROOT_FILE = "QUALIFICATION-ROOT.json"
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
HASH = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@+-]{0,127}$")
MAX_ENVELOPE = 64 * 1024
MAX_SIGNATURE = 64 * 1024
MAX_TRUST = 256 * 1024
MAX_ARCHIVE = 512 * 1024 * 1024
MAX_SBOM = 32 * 1024 * 1024
MAX_PROVENANCE = 2 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 20_000
MAX_ARCHIVE_UNPACKED = 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBER = 256 * 1024 * 1024
MAX_STAGE_RECEIPT = 64 * 1024
MAX_STAGED_CANDIDATES = 8
MAX_REACTIVATION_ATTEMPTS = 8
MAX_CODE_FILE = 2 * 1024 * 1024
MAX_SYNTAX_FILES = 1000
MAX_SYNTAX_BYTES = 128 * 1024 * 1024
MAX_TREE_DIRECTORIES = MAX_ARCHIVE_MEMBERS
MAX_TREE_ENTRIES = MAX_ARCHIVE_MEMBERS + MAX_TREE_DIRECTORIES + 4
MAX_CLEANUP_ENTRIES = 250_000
MAX_CLEANUP_BYTES = 4 * 1024 * 1024 * 1024
MAX_CLEANUP_HISTORY = 128
MAX_ARCHIVE_MANIFEST = 32 * 1024 * 1024
ARTIFACT_LIMITS = {"archive": MAX_ARCHIVE, "sbom": MAX_SBOM, "provenance": MAX_PROVENANCE}
BOOTSTRAP_POLICY_RELEASES = {"4.0.0", "4.1.0"}


class UpdateError(RuntimeError):
    pass


def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise UpdateError(f"duplicate JSON field denied: {key}")
        result[key] = value
    return result


def parse_json(payload: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            payload.decode("utf-8"), object_pairs_hook=reject_duplicate_keys,
            parse_constant=lambda item: (_ for _ in ()).throw(UpdateError(f"non-finite JSON denied: {item}")),
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise UpdateError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise UpdateError(f"{label} must be a JSON object")
    return value


def read_regular(path: Path, maximum: int, label: str, *, trust_anchor: bool = False) -> bytes:
    if not path.is_absolute():
        raise UpdateError(f"{label} path must be absolute")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise UpdateError(f"{label} is unavailable or unsafe") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 1 <= info.st_size <= maximum:
            raise UpdateError(f"{label} must be one bounded regular single-link file")
        if trust_anchor and os.name != "nt":
            if info.st_uid not in {0, os.geteuid()} or stat.S_IMODE(info.st_mode) & 0o022:
                raise UpdateError(f"{label} ownership or permissions are unsafe")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            payload = handle.read(maximum + 1)
        if len(payload) > maximum:
            raise UpdateError(f"{label} exceeds its byte limit")
        current = path.lstat()
        if current.st_dev != info.st_dev or current.st_ino != info.st_ino or not stat.S_ISREG(current.st_mode):
            raise UpdateError(f"{label} changed during its no-follow read")
        return payload
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical_json(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")


def exact_keys(value: Any, required: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != required:
        raise UpdateError(f"{label} shape is invalid")
    return value


def semantic_version(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SEMVER.fullmatch(value):
        raise UpdateError(f"{label} is not a semantic version")
    if any(int(part) > 999_999 for part in value.split(".")):
        raise UpdateError(f"{label} component is too large")
    return value


def version_tuple(value: str) -> tuple[int, int, int]:
    return tuple(int(part) for part in value.split("."))  # type: ignore[return-value]


def qualification_mode_for_release(version: str) -> str:
    """Keep the frozen 4.0 updater bridge-compatible, then require forward policy."""
    semantic_version(version, "release version")
    return "bootstrap" if version in BOOTSTRAP_POLICY_RELEASES else "forward"


def validate_envelope(value: Any) -> dict[str, Any]:
    required = {
        "schemaVersion", "operation", "product", "version", "channel", "minimumUpgradablePixel",
        "sourceCommit", "sourceTree", "qualificationSourceCommit", "supportedHosts",
        "releaseManifestSha256", "compatibilitySha256",
        "artifacts", "boundary",
    }
    value = exact_keys(value, required, "release update envelope")
    if value["schemaVersion"] != 1 or value["operation"] != "pixel-release-update" or value["product"] != "Pixel":
        raise UpdateError("release update identity is invalid")
    if value["channel"] != "stable" or value["boundary"] != BOUNDARY:
        raise UpdateError("release update channel or boundary is invalid")
    version = semantic_version(value["version"], "release version")
    minimum = semantic_version(value["minimumUpgradablePixel"], "minimum upgradable Pixel version")
    if version_tuple(minimum) > version_tuple(version):
        raise UpdateError("minimum upgradable Pixel version exceeds the release")
    if not isinstance(value["sourceCommit"], str) or not COMMIT.fullmatch(value["sourceCommit"]):
        raise UpdateError("release source commit is invalid")
    if not isinstance(value["sourceTree"], str) or not COMMIT.fullmatch(value["sourceTree"]):
        raise UpdateError("release source tree is invalid")
    if not isinstance(value["qualificationSourceCommit"], str) or not COMMIT.fullmatch(value["qualificationSourceCommit"]):
        raise UpdateError("release qualification source commit is invalid")
    hosts = value["supportedHosts"]
    if (
        not isinstance(hosts, list) or not 1 <= len(hosts) <= 8
        or any(not isinstance(host, str) or not 1 <= len(host) <= 80 or any(ord(char) < 32 for char in host) for host in hosts)
        or len(hosts) != len(set(hosts))
    ):
        raise UpdateError("supported-host list is invalid")
    for field in ("releaseManifestSha256", "compatibilitySha256"):
        if not isinstance(value[field], str) or not HASH.fullmatch(value[field]):
            raise UpdateError(f"{field} is invalid")
    artifacts = exact_keys(value["artifacts"], {"archive", "sbom", "provenance"}, "release artifacts")
    expected_names = {
        "archive": f"pixel-{version}.tar.gz",
        "sbom": f"pixel-{version}.cdx.json",
        "provenance": f"pixel-{version}.intoto.jsonl",
    }
    for kind, expected_name in expected_names.items():
        artifact = exact_keys(artifacts[kind], {"name", "sha256", "bytes"}, f"{kind} artifact")
        if artifact["name"] != expected_name or not isinstance(artifact["sha256"], str) or not HASH.fullmatch(artifact["sha256"]):
            raise UpdateError(f"{kind} artifact identity is invalid")
        if type(artifact["bytes"]) is not int or not 1 <= artifact["bytes"] <= ARTIFACT_LIMITS[kind]:
            raise UpdateError(f"{kind} artifact size is invalid")
    return value


def validate_signature_envelope(payload: bytes) -> None:
    try:
        lines = payload.decode("ascii").strip().splitlines()
        if len(lines) < 3 or lines[0] != "-----BEGIN SSH SIGNATURE-----" or lines[-1] != "-----END SSH SIGNATURE-----":
            raise ValueError
        body = "".join(lines[1:-1])
        if any(not re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", line) for line in lines[1:-1]):
            raise ValueError
        base64.b64decode(body, validate=True)
    except (UnicodeError, ValueError) as exc:
        raise UpdateError("release signature envelope is malformed") from exc


def write_private(path: Path, payload: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def restrict_windows_private_directory(path: Path) -> None:
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    size = wintypes.ULONG(0)
    ctypes.windll.secur32.GetUserNameExW(2, None, ctypes.byref(size))
    if size.value < 2 or size.value > 32_768:
        raise UpdateError("current Windows signing identity is unavailable")
    identity_buffer = ctypes.create_unicode_buffer(size.value)
    if not ctypes.windll.secur32.GetUserNameExW(2, identity_buffer, ctypes.byref(size)):
        raise UpdateError("current Windows signing identity is unavailable")
    system_directory = ctypes.create_unicode_buffer(32_768)
    length = ctypes.windll.kernel32.GetSystemDirectoryW(system_directory, len(system_directory))
    if not 1 <= length < len(system_directory):
        raise UpdateError("Windows system directory is unavailable")
    icacls = Path(system_directory.value) / "icacls.exe"
    result = subprocess.run(
        [str(icacls), str(path), "/inheritance:r", "/grant:r", f"{identity_buffer.value}:(OI)(CI)(F)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        raise UpdateError("temporary signing directory permissions could not be restricted")
    result = subprocess.run(
        [str(icacls), str(path), "/remove:g", "*S-1-3-4"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        raise UpdateError("temporary signing directory owner-rights entry could not be removed")


def ensure_private_directory(path: Path, label: str, *, create: bool = False) -> None:
    if not path.is_absolute():
        raise UpdateError(f"{label} path must be absolute")
    if create and not path.exists() and not path.is_symlink():
        try:
            if path.parent.resolve(strict=True) != path.parent:
                raise UpdateError(f"{label} parent must not contain symbolic links")
            os.mkdir(path, 0o700)
        except OSError as exc:
            raise UpdateError(f"{label} could not be created safely") from exc
    try:
        info = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise UpdateError(f"{label} is unavailable") from exc
    if resolved != path or not stat.S_ISDIR(info.st_mode):
        raise UpdateError(f"{label} must be one real directory without symbolic-link components")
    if os.name != "nt" and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077):
        raise UpdateError(f"{label} must be owned by the current user and mode 0700 or stricter")


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def rename_noreplace(source: Path, destination: Path, label: str) -> None:
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise UpdateError("atomic no-replace staging publication is unavailable")
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    if renameat2(-100, os.fsencode(source), -100, os.fsencode(destination), 1) != 0:
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise UpdateError(f"{label} appeared during staging")
        raise UpdateError(f"{label} could not be published atomically (errno {error})")


def rename_directory_noreplace(source: Path, destination: Path) -> None:
    rename_noreplace(source, destination, "release candidate")


@contextmanager
def exclusive_stage_lock(staging_root: Path):
    if sys.platform != "linux":
        raise UpdateError("release update staging is supported only on qualified Linux hosts")
    import fcntl

    lock_path = staging_root / ".stage.lock"
    descriptor = os.open(
        lock_path,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise UpdateError("release staging lock is unsafe")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise UpdateError("another release staging operation is active") from exc
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def verify_signature(
    envelope: bytes, signature: bytes, trust: bytes, identity: str, *, namespace: str = NAMESPACE,
) -> None:
    validate_signature_envelope(signature)
    with tempfile.TemporaryDirectory(prefix="pixel-update-trust-") as temporary:
        root = Path(temporary)
        trusted = root / "allowed-signers"
        signature_path = root / "update.sig"
        write_private(trusted, trust)
        write_private(signature_path, signature)
        result = subprocess.run(
            ["ssh-keygen", "-Y", "verify", "-f", str(trusted), "-I", identity, "-n", namespace, "-s", str(signature_path)],
            input=envelope, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
    if result.returncode:
        raise UpdateError("release signature is invalid or the publisher is not trusted")


def safe_archive_path(name: str, version: str) -> PurePosixPath:
    try:
        pure = PurePosixPath(name)
        invalid = (
            not name or len(name.encode("utf-8")) > 4096 or len(pure.parts) > 64
            or any(len(part.encode("utf-8")) > 255 or any(ord(character) < 32 for character in part) for part in pure.parts)
            or pure.is_absolute() or ".." in pure.parts or "." in pure.parts
            or pure.as_posix() != name or not pure.parts or pure.parts[0] != f"pixel-{version}"
        )
    except UnicodeError as exc:
        raise UpdateError("release archive contains an unsafe or duplicate path") from exc
    if invalid:
        raise UpdateError("release archive contains an unsafe or duplicate path")
    return pure


def archive_members(
    archive_payload: bytes, version: str, *, additional_required: dict[str, int] | None = None,
) -> dict[str, bytes]:
    required = {
        f"pixel-{version}/VERSION": 64,
        f"pixel-{version}/RELEASE-MANIFEST.json": 2 * 1024 * 1024,
        f"pixel-{version}/OPENCLAW-COMPATIBILITY.json": 2 * 1024 * 1024,
        f"pixel-{version}/OPENCLAW-COMPATIBILITY.md": 2 * 1024 * 1024,
        f"pixel-{version}/LIVE-AUDIT-{version}.md": 2 * 1024 * 1024,
        f"pixel-{version}/SBOM.cdx.json": MAX_SBOM,
    }
    if additional_required is not None:
        prefix = f"pixel-{version}/"
        if any(
            not isinstance(name, str) or not name.startswith(prefix) or name in required
            or type(limit) is not int or not 1 <= limit <= MAX_ARCHIVE_MEMBER
            for name, limit in additional_required.items()
        ):
            raise UpdateError("additional release archive member requirements are invalid")
        required.update(additional_required)
    captured: dict[str, bytes] = {}
    observed: set[str] = set()
    total = 0
    count = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_payload), mode="r:gz") as archive:
            for member in archive:
                count += 1
                if count > MAX_ARCHIVE_MEMBERS:
                    raise UpdateError("release archive has too many members")
                name = member.name
                safe_archive_path(name, version)
                if name in observed:
                    raise UpdateError("release archive contains an unsafe or duplicate path")
                observed.add(name)
                if not (member.isdir() or member.isreg()) or member.issym() or member.islnk() or member.mode & 0o6000:
                    raise UpdateError("release archive contains a special, linked, or privileged member")
                if member.size < 0 or member.size > MAX_ARCHIVE_MEMBER:
                    raise UpdateError("release archive member exceeds its size bound")
                total += member.size
                if total > MAX_ARCHIVE_UNPACKED:
                    raise UpdateError("release archive exceeds its unpacked size bound")
                if name in required:
                    if not member.isreg() or member.size > required[name]:
                        raise UpdateError("required release archive member is unsafe or oversized")
                    handle = archive.extractfile(member)
                    if handle is None:
                        raise UpdateError("required release archive member is unreadable")
                    payload = handle.read(required[name] + 1)
                    if len(payload) != member.size:
                        raise UpdateError("required release archive member is truncated")
                    captured[name] = payload
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise UpdateError("release archive is malformed") from exc
    if set(captured) != set(required):
        raise UpdateError("release archive is missing required identity files")
    return captured


def validate_provenance(value: dict[str, Any], envelope: dict[str, Any]) -> None:
    if value.get("_type") != "https://in-toto.io/Statement/v1" or value.get("predicateType") != "https://slsa.dev/provenance/v1":
        raise UpdateError("release provenance statement identity is invalid")
    subjects = value.get("subject")
    if not isinstance(subjects, list) or len(subjects) != 2:
        raise UpdateError("release provenance subjects are invalid")
    observed: dict[str, str] = {}
    for subject in subjects:
        if not isinstance(subject, dict) or set(subject) != {"name", "digest"}:
            raise UpdateError("release provenance subject shape is invalid")
        digest = subject.get("digest")
        if (
            not isinstance(subject.get("name"), str) or not isinstance(digest, dict) or set(digest) != {"sha256"}
            or not isinstance(digest["sha256"], str) or not HASH.fullmatch(digest["sha256"])
        ):
            raise UpdateError("release provenance subject digest is invalid")
        observed[subject["name"]] = digest["sha256"]
    expected = {
        envelope["artifacts"]["archive"]["name"]: envelope["artifacts"]["archive"]["sha256"],
        envelope["artifacts"]["sbom"]["name"]: envelope["artifacts"]["sbom"]["sha256"],
    }
    if observed != expected:
        raise UpdateError("release provenance subjects do not match the signed artifacts")
    try:
        definition = value["predicate"]["buildDefinition"]
        if (
            not isinstance(definition, dict)
            or definition.get("buildType") != "https://github.com/Osmantic/Pixel/blob/main/scripts/package-release.sh"
            or definition.get("internalParameters") != {}
        ):
            raise UpdateError("release provenance build definition is invalid")
        if definition["externalParameters"] != {"version": envelope["version"]}:
            raise UpdateError("release provenance version does not match")
        dependencies = definition["resolvedDependencies"]
    except (KeyError, TypeError) as exc:
        raise UpdateError("release provenance build definition is incomplete") from exc
    if (
        not isinstance(dependencies, list) or len(dependencies) != 3
        or any(not isinstance(item, dict) or set(item) != {"uri", "digest"} for item in dependencies)
    ):
        raise UpdateError("release provenance dependencies are invalid")
    expected_dependencies = {
        f"git+https://github.com/Osmantic/Pixel@{envelope['sourceCommit']}": {
            "gitCommit": envelope["sourceCommit"], "gitTree": envelope["sourceTree"],
        },
        "RELEASE-MANIFEST.json": {"sha256": envelope["releaseManifestSha256"]},
        "OPENCLAW-COMPATIBILITY.json": {"sha256": envelope["compatibilitySha256"]},
    }
    observed_dependencies: dict[str, Any] = {}
    for item in dependencies:
        uri = item["uri"]
        digest = item["digest"]
        if not isinstance(uri, str) or uri in observed_dependencies or not isinstance(digest, dict):
            raise UpdateError("release provenance dependencies are invalid")
        observed_dependencies[uri] = digest
    if observed_dependencies != expected_dependencies:
        raise UpdateError("release provenance dependencies do not match the signed source identity")


def load_unsigned_bundle(
    envelope_path: Path, *, compatibility_status: str = "supported",
    allow_pre_archive_policy: bool = False,
) -> tuple[dict[str, Any], bytes, dict[str, bytes], dict[str, Any]]:
    if compatibility_status not in {"candidate", "supported"}:
        raise UpdateError("release compatibility status requirement is invalid")
    envelope_bytes = read_regular(envelope_path, MAX_ENVELOPE, "release update envelope")
    envelope = validate_envelope(parse_json(envelope_bytes, "release update envelope"))
    if envelope_path.name != f"pixel-{envelope['version']}.update.json":
        raise UpdateError("release update envelope filename does not match its version")
    artifacts: dict[str, bytes] = {}
    for kind, specification in envelope["artifacts"].items():
        payload = read_regular(envelope_path.parent / specification["name"], ARTIFACT_LIMITS[kind], f"release {kind}")
        if len(payload) != specification["bytes"] or not hmac.compare_digest(sha256(payload), specification["sha256"]):
            raise UpdateError(f"release {kind} differs from the signed envelope")
        artifacts[kind] = payload
    members = archive_members(artifacts["archive"], envelope["version"])
    prefix = f"pixel-{envelope['version']}"
    if members[f"{prefix}/VERSION"].decode("ascii").strip() != envelope["version"]:
        raise UpdateError("release archive VERSION differs from the signed envelope")
    manifest_bytes = members[f"{prefix}/RELEASE-MANIFEST.json"]
    compatibility_bytes = members[f"{prefix}/OPENCLAW-COMPATIBILITY.json"]
    if sha256(manifest_bytes) != envelope["releaseManifestSha256"] or sha256(compatibility_bytes) != envelope["compatibilitySha256"]:
        raise UpdateError("release archive manifest identity differs from the signed envelope")
    manifest = parse_json(manifest_bytes, "embedded release manifest")
    compatibility = parse_json(compatibility_bytes, "embedded compatibility matrix")
    if (
        manifest.get("schemaVersion") != 1 or manifest.get("pixel") != envelope["version"]
        or manifest.get("supportedHosts") != envelope["supportedHosts"]
    ):
        raise UpdateError("embedded release manifest does not match the signed release")
    update_policy = manifest.get("releaseUpdate")
    expected_update_policy = {
        "schemaVersion": 1,
        "channel": envelope["channel"],
        "envelopeSchema": "./schemas/release-update-v1.schema.json",
        "stageReceiptSchema": "./schemas/release-update-stage-v1.schema.json",
        "rehearsalReceiptSchema": "./schemas/release-update-rehearsal-v1.schema.json",
        "activationReceiptSchema": "./schemas/release-update-activation-v1.schema.json",
        "activationResultSchema": "./schemas/release-update-activation-result-v1.schema.json",
        "rollbackReceiptSchema": "./schemas/release-update-rollback-v1.schema.json",
        "rollbackResultSchema": "./schemas/release-update-rollback-result-v1.schema.json",
        "recoverySchema": "./schemas/release-update-recovery-v1.schema.json",
        "cleanupReceiptSchema": "./schemas/release-update-cleanup-v1.schema.json",
        "archiveReceiptSchema": "./schemas/release-update-archive-v1.schema.json",
        "reactivationArchiveReceiptSchema": "./schemas/release-update-reactivation-archive-v1.schema.json",
        "signature": "openssh-ed25519-detached",
        "signatureNamespace": NAMESPACE,
        "qualificationSignature": "openssh-ed25519-detached",
        "qualificationSignatureNamespace": QUALIFICATION_NAMESPACE,
        "qualificationAuthority": "verify-only-no-publication-staging-activation",
        "qualificationMode": qualification_mode_for_release(envelope["version"]),
        "minimumUpgradablePixel": envelope["minimumUpgradablePixel"],
        "preparation": "verify-without-execution",
        "staging": "private-copy-without-extraction",
        "rehearsal": "private-syntax-and-host-contract-no-candidate-execution",
        "activation": "external-exact-confirmation-transactional-apply",
        "rollback": "single-use-update-bound-last-apply",
        "recovery": "content-free-receipt-finalization-no-candidate-execution",
        "cleanup": "exact-quarantine-audit-tombstone",
        "archive": "exact-failed-rollback-preservation-outside-bounded-staging",
        "reactivationArchive": "exact-terminal-no-live-mutation-reactivation-preservation-outside-bounded-staging",
    }
    pre_reactivation_archive_policy = {
        key: value for key, value in expected_update_policy.items()
        if key not in {"reactivationArchiveReceiptSchema", "reactivationArchive"}
    }
    pre_archive_policy = {
        key: value for key, value in pre_reactivation_archive_policy.items()
        if key not in {"archiveReceiptSchema", "archive"}
    }
    historical_pre_reactivation_archive_policy = (
        allow_pre_archive_policy
        and version_tuple(envelope["version"]) < version_tuple(current_version())
        and update_policy == pre_reactivation_archive_policy
    )
    historical_pre_archive_policy = (
        allow_pre_archive_policy
        and version_tuple(envelope["version"]) < version_tuple(current_version())
        and update_policy == pre_archive_policy
    )
    archive_policy_bridge = (
        envelope["version"] == ARCHIVE_POLICY_BRIDGE_VERSION
        and update_policy == pre_archive_policy
    )
    reactivation_archive_policy_bridge = (
        envelope["version"] == REACTIVATION_ARCHIVE_POLICY_BRIDGE_VERSION
        and update_policy == pre_reactivation_archive_policy
    )
    if (
        update_policy != expected_update_policy
        and not historical_pre_reactivation_archive_policy
        and not historical_pre_archive_policy
        and not archive_policy_bridge
        and not reactivation_archive_policy_bridge
    ):
        raise UpdateError("embedded release update policy is invalid")
    if archive_policy_bridge and not allow_pre_archive_policy and manifest.get("releaseArchive") != ARCHIVE_POLICY_BRIDGE:
        raise UpdateError("embedded release archive bridge policy is invalid")
    if (
        reactivation_archive_policy_bridge
        and manifest.get("releaseReactivationArchive") != REACTIVATION_ARCHIVE_POLICY_BRIDGE
    ):
        raise UpdateError("embedded release reactivation archive bridge policy is invalid")
    if (
        not reactivation_archive_policy_bridge
        and "releaseReactivationArchive" in manifest
    ):
        raise UpdateError("embedded release reactivation archive bridge policy is invalid")
    combinations = compatibility.get("combinations")
    if (
        compatibility.get("$schema") != "./schemas/openclaw-compatibility-v1.schema.json"
        or compatibility.get("schemaVersion") != 1 or not isinstance(combinations, list)
    ):
        raise UpdateError("embedded compatibility matrix identity is invalid")
    compatible: list[dict[str, Any]] = []
    for item in combinations:
        if not isinstance(item, dict):
            continue
        evidence = item.get("evidence")
        if (
            item.get("pixel") == envelope["version"] and item.get("openclaw") == manifest.get("openclaw")
            and item.get("plugins") == manifest.get("openclawPlugins")
            and item.get("status") == compatibility_status
            and isinstance(evidence, dict)
            and evidence.get("sourceCommit") == envelope["qualificationSourceCommit"]
        ):
            compatible.append(item)
    if len(compatible) != 1:
        raise UpdateError(
            f"release has no matching {compatibility_status} compatibility record or record is ambiguous"
        )
    evidence = compatible[0]["evidence"]
    if evidence.get("liveAudit") != f"LIVE-AUDIT-{envelope['version']}.md":
        raise UpdateError("release compatibility record does not name its versioned live audit")
    if members[f"{prefix}/SBOM.cdx.json"] != artifacts["sbom"]:
        raise UpdateError("embedded and standalone release SBOM differ")
    sbom = parse_json(artifacts["sbom"], "release SBOM")
    metadata = sbom.get("metadata")
    component = metadata.get("component") if isinstance(metadata, dict) else None
    if (
        sbom.get("bomFormat") != "CycloneDX" or sbom.get("specVersion") != "1.6"
        or not isinstance(component, dict) or component.get("name") != "Pixel"
        or component.get("version") != envelope["version"]
    ):
        raise UpdateError("release SBOM identity is invalid")
    properties = component.get("properties")
    if (
        not isinstance(properties, list) or any(
            not isinstance(item, dict) or set(item) != {"name", "value"}
            or not isinstance(item["name"], str) or not isinstance(item["value"], str)
            for item in properties
        )
    ):
        raise UpdateError("release SBOM source binding is invalid")
    if len({item["name"] for item in properties}) != len(properties):
        raise UpdateError("release SBOM source binding contains duplicate properties")
    observed_properties = {item["name"]: item["value"] for item in properties}
    expected_properties = {
        "pixel:source-commit": envelope["sourceCommit"],
        "pixel:source-tree": envelope["sourceTree"],
        "pixel:release-manifest-sha256": envelope["releaseManifestSha256"],
    }
    if any(observed_properties.get(name) != expected for name, expected in expected_properties.items()):
        raise UpdateError("release SBOM does not match the signed source identity")
    provenance = parse_json(artifacts["provenance"], "release provenance")
    validate_provenance(provenance, envelope)
    return envelope, envelope_bytes, artifacts, manifest


def current_version() -> str:
    try:
        return semantic_version((ROOT / "VERSION").read_text(encoding="ascii").strip(), "current Pixel version")
    except (OSError, UnicodeError) as exc:
        raise UpdateError("current Pixel version is unavailable") from exc


def qualified_host(envelope: dict[str, Any]) -> dict[str, Any]:
    if sys.platform != "linux":
        raise UpdateError("release update staging is supported only on qualified Linux hosts")
    try:
        os_release_path = Path("/etc/os-release").resolve(strict=True)
    except OSError as exc:
        raise UpdateError("host operating-system identity is unavailable") from exc
    payload = read_regular(os_release_path, 64 * 1024, "host operating-system identity", trust_anchor=True)
    values: dict[str, str] = {}
    try:
        for line in payload.decode("utf-8").splitlines():
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key not in {"ID", "VERSION_ID"}:
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            values[key] = value
    except UnicodeError as exc:
        raise UpdateError("host operating-system identity is malformed") from exc
    host_id = values.get("ID", "")
    version_id = values.get("VERSION_ID", "")
    if not re.fullmatch(r"[a-z0-9]+", host_id) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", version_id):
        raise UpdateError("host operating-system identity is malformed")
    labels = {("ubuntu", "24.04"): "Ubuntu 24.04 LTS", ("debian", "12"): "Debian 12"}
    label = labels.get((host_id, version_id))
    if label is None or label not in envelope["supportedHosts"]:
        raise UpdateError("release is not supported on this exact host version")
    return {"id": host_id, "versionId": version_id, "label": label, "supported": True}


def load_signed_bundle(
    envelope_path: Path, allowed_signers: Path, identity: str, *,
    allow_pre_archive_policy: bool = False,
) -> tuple[dict[str, Any], bytes, dict[str, bytes], dict[str, Any], bytes]:
    if not envelope_path.is_absolute() or not allowed_signers.is_absolute():
        raise UpdateError("update envelope and allowed-signers paths must be absolute")
    if not IDENTITY.fullmatch(identity):
        raise UpdateError("release publisher identity is invalid")
    envelope, envelope_bytes, artifacts, manifest = load_unsigned_bundle(
        envelope_path, allow_pre_archive_policy=allow_pre_archive_policy,
    )
    signature = read_regular(Path(f"{envelope_path}.sig"), MAX_SIGNATURE, "release signature")
    trust = read_regular(allowed_signers, MAX_TRUST, "release allowed-signers file", trust_anchor=True)
    verify_signature(envelope_bytes, signature, trust, identity)
    return envelope, envelope_bytes, artifacts, manifest, signature


def load_qualification_bundle(
    envelope_path: Path, allowed_signers: Path, identity: str,
) -> tuple[dict[str, Any], bytes, dict[str, bytes], dict[str, Any], bytes]:
    if not envelope_path.is_absolute() or not allowed_signers.is_absolute():
        raise UpdateError("qualification envelope and allowed-signers paths must be absolute")
    if not IDENTITY.fullmatch(identity):
        raise UpdateError("qualification signer identity is invalid")
    envelope, envelope_bytes, artifacts, manifest = load_unsigned_bundle(
        envelope_path, compatibility_status="candidate",
    )
    signature = read_regular(
        Path(f"{envelope_path}.qualification.sig"), MAX_SIGNATURE, "qualification signature",
    )
    trust = read_regular(
        allowed_signers, MAX_TRUST, "qualification allowed-signers file", trust_anchor=True,
    )
    verify_signature(
        envelope_bytes, signature, trust, identity, namespace=QUALIFICATION_NAMESPACE,
    )
    return envelope, envelope_bytes, artifacts, manifest, signature


def release_relation(envelope: dict[str, Any]) -> tuple[str, str, bool]:
    current = current_version()
    release_tuple = version_tuple(envelope["version"])
    current_tuple = version_tuple(current)
    minimum_tuple = version_tuple(envelope["minimumUpgradablePixel"])
    relation = "upgrade" if release_tuple > current_tuple else "same" if release_tuple == current_tuple else "downgrade"
    return current, relation, relation == "upgrade" and current_tuple >= minimum_tuple


def inspect(args: argparse.Namespace) -> dict[str, Any]:
    envelope, envelope_bytes, artifacts, _manifest, _signature = load_signed_bundle(
        args.envelope, args.allowed_signers, args.identity,
    )
    current, relation, eligible = release_relation(envelope)
    return {
        "schemaVersion": 1,
        "status": "verified",
        "product": "Pixel",
        "version": envelope["version"],
        "currentVersion": current,
        "relation": relation,
        "channel": envelope["channel"],
        "publisherIdentity": args.identity,
        "sourceCommit": envelope["sourceCommit"],
        "sourceTree": envelope["sourceTree"],
        "qualificationSourceCommit": envelope["qualificationSourceCommit"],
        "supportedHosts": envelope["supportedHosts"],
        "minimumUpgradablePixel": envelope["minimumUpgradablePixel"],
        "upgradeEligible": eligible,
        "artifacts": {
            kind: {"sha256": envelope["artifacts"][kind]["sha256"], "bytes": len(payload)}
            for kind, payload in artifacts.items()
        },
        "envelopeSha256": sha256(envelope_bytes),
        "activationAuthority": "external-exact-confirmation-only",
        "candidateCodeExecuted": False,
        "boundary": BOUNDARY,
    }


def qualification_inspect(args: argparse.Namespace) -> dict[str, Any]:
    envelope, envelope_bytes, artifacts, _manifest, _signature = load_qualification_bundle(
        args.envelope, args.allowed_signers, args.identity,
    )
    return {
        "schemaVersion": 1,
        "status": "qualification-verified",
        "product": "Pixel",
        "version": envelope["version"],
        "channel": envelope["channel"],
        "qualifierIdentity": args.identity,
        "sourceCommit": envelope["sourceCommit"],
        "sourceTree": envelope["sourceTree"],
        "qualificationSourceCommit": envelope["qualificationSourceCommit"],
        "supportedHosts": envelope["supportedHosts"],
        "artifacts": {
            kind: {"sha256": envelope["artifacts"][kind]["sha256"], "bytes": len(payload)}
            for kind, payload in artifacts.items()
        },
        "envelopeSha256": sha256(envelope_bytes),
        "signatureNamespace": QUALIFICATION_NAMESPACE,
        "publicationAuthority": False,
        "stagingAuthority": False,
        "activationAuthority": False,
        "candidateCodeExtracted": False,
        "candidateCodeExecuted": False,
        "boundary": (
            "Candidate qualification signature only; it grants no publication, staging, "
            "activation, update, or production trust authority."
        ),
    }


def stage_receipt(
    envelope: dict[str, Any], envelope_bytes: bytes, signature: bytes, identity: str,
    current: str, host: dict[str, Any], candidate_id: str, prepared_at: str, status: str = "prepared",
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "status": status,
        "candidateId": candidate_id,
        "product": "Pixel",
        "version": envelope["version"],
        "currentVersion": current,
        "relation": "upgrade",
        "channel": envelope["channel"],
        "publisherIdentity": identity,
        "sourceCommit": envelope["sourceCommit"],
        "sourceTree": envelope["sourceTree"],
        "minimumUpgradablePixel": envelope["minimumUpgradablePixel"],
        "upgradeEligible": True,
        "host": host,
        "artifacts": envelope["artifacts"],
        "envelopeSha256": sha256(envelope_bytes),
        "signatureSha256": sha256(signature),
        "preparedAt": prepared_at,
        "candidateCodeExtracted": False,
        "candidateCodeExecuted": False,
        "activationAuthority": "external-exact-confirmation-only",
        "boundary": STAGE_BOUNDARY,
    }


def validate_existing_stage(
    candidate: Path, envelope: dict[str, Any], envelope_bytes: bytes, artifacts: dict[str, bytes],
    signature: bytes, identity: str, current: str, host: dict[str, Any], candidate_id: str,
) -> dict[str, Any]:
    ensure_private_directory(candidate, "prepared release candidate")
    filenames = {
        "STAGED-UPDATE.json",
        Path(f"{envelope['artifacts']['archive']['name']}").name,
        Path(f"{envelope['artifacts']['sbom']['name']}").name,
        Path(f"{envelope['artifacts']['provenance']['name']}").name,
        f"pixel-{envelope['version']}.update.json",
        f"pixel-{envelope['version']}.update.json.sig",
    }
    try:
        observed = {entry.name for entry in os.scandir(candidate)}
    except OSError as exc:
        raise UpdateError("prepared release candidate cannot be enumerated safely") from exc
    if observed != filenames:
        raise UpdateError("prepared release candidate file set is invalid")
    expected_payloads = {
        f"pixel-{envelope['version']}.update.json": (envelope_bytes, MAX_ENVELOPE),
        f"pixel-{envelope['version']}.update.json.sig": (signature, MAX_SIGNATURE),
        **{
            envelope["artifacts"][kind]["name"]: (payload, ARTIFACT_LIMITS[kind])
            for kind, payload in artifacts.items()
        },
    }
    for name, (expected, maximum) in expected_payloads.items():
        path = candidate / name
        actual = read_regular(path, maximum, f"prepared {name}")
        info = path.lstat()
        if os.name != "nt" and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077):
            raise UpdateError("prepared release file permissions are unsafe")
        if len(actual) != len(expected) or not hmac.compare_digest(sha256(actual), sha256(expected)):
            raise UpdateError("prepared release candidate differs from the verified bundle")
    receipt_path = candidate / "STAGED-UPDATE.json"
    receipt_bytes = read_regular(receipt_path, MAX_STAGE_RECEIPT, "prepared release receipt")
    receipt_info = receipt_path.lstat()
    if os.name != "nt" and (receipt_info.st_uid != os.geteuid() or stat.S_IMODE(receipt_info.st_mode) & 0o077):
        raise UpdateError("prepared release receipt permissions are unsafe")
    receipt = parse_json(receipt_bytes, "prepared release receipt")
    prepared_at = receipt.get("preparedAt")
    if not isinstance(prepared_at, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", prepared_at):
        raise UpdateError("prepared release timestamp is invalid")
    expected_receipt = stage_receipt(
        envelope, envelope_bytes, signature, identity, current, host, candidate_id, prepared_at,
    )
    if receipt != expected_receipt:
        raise UpdateError("prepared release receipt differs from the verified bundle")
    return {**receipt, "status": "already-prepared"}


def remove_incomplete_stage(path: Path, names: set[str]) -> None:
    for name in names:
        candidate = path / name
        try:
            candidate.unlink()
        except FileNotFoundError:
            pass
    try:
        path.rmdir()
    except FileNotFoundError:
        pass


def recover_incomplete_stage(path: Path) -> None:
    match = re.fullmatch(
        r"\.pixel-([0-9]{1,6}(?:\.[0-9]{1,6}){2})-[0-9a-f]{64}\.stage-[0-9a-f]{16}",
        path.name,
    )
    if match is None:
        raise UpdateError("release candidate directory contains an unrecognized entry")
    ensure_private_directory(path, "incomplete release staging directory")
    version = match.group(1)
    allowed = {
        "STAGED-UPDATE.json",
        f"pixel-{version}.tar.gz",
        f"pixel-{version}.cdx.json",
        f"pixel-{version}.intoto.jsonl",
        f"pixel-{version}.update.json",
        f"pixel-{version}.update.json.sig",
    }
    try:
        entries = list(os.scandir(path))
    except OSError as exc:
        raise UpdateError("incomplete release staging directory cannot be enumerated safely") from exc
    if len(entries) > len(allowed):
        raise UpdateError("incomplete release staging directory has too many files")
    for entry in entries:
        try:
            info = entry.stat(follow_symlinks=False)
        except OSError as exc:
            raise UpdateError("incomplete release staging entry is unsafe") from exc
        if (
            entry.name not in allowed or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or (os.name != "nt" and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077))
        ):
            raise UpdateError("incomplete release staging entry is unsafe")
    for entry in entries:
        os.unlink(entry.path)
    path.rmdir()


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    if not args.confirm:
        raise UpdateError("release staging requires --confirm")
    if sys.platform != "linux":
        raise UpdateError("release update staging is supported only on qualified Linux hosts")
    if not args.staging_root.is_absolute():
        raise UpdateError("release staging root path must be absolute")
    envelope, envelope_bytes, artifacts, _manifest, signature = load_signed_bundle(
        args.envelope, args.allowed_signers, args.identity,
    )
    current, relation, eligible = release_relation(envelope)
    if relation != "upgrade" or not eligible:
        raise UpdateError("only an eligible forward release can be staged")
    host = qualified_host(envelope)
    ensure_private_directory(args.staging_root, "release staging root", create=True)
    envelope_hash = sha256(envelope_bytes)
    candidate_id = f"pixel-{envelope['version']}-{envelope_hash}"
    candidates = args.staging_root / "candidates"
    with exclusive_stage_lock(args.staging_root):
        # A cleanup history record is a permanent single-use tombstone. The live
        # 4.3.9 journey exposed that the same signed candidate could otherwise be
        # staged again after its exact evidence had deliberately been deleted.
        quarantine, history = cleanup_directories(args.staging_root)
        validate_cleanup_store(quarantine, history)
        history_path = history / f"{candidate_id}.json"
        if history_path.exists() or history_path.is_symlink():
            read_cleanup_history(history_path, candidate_id)
            raise UpdateError("release candidate was already permanently cleaned and cannot be reused")
        archived = args.staging_root.parent / "update-archive" / candidate_id
        if archived.exists() or archived.is_symlink():
            ensure_private_directory(archived, "archived release journey")
            read_archive_claim(archived, candidate_id=candidate_id)
            raise UpdateError("release candidate was already archived and cannot be reused")
        reactivation_archived = args.staging_root.parent / "update-reactivation-archive" / candidate_id
        if reactivation_archived.exists() or reactivation_archived.is_symlink():
            ensure_private_directory(reactivation_archived, "archived release reactivation journey")
            read_reactivation_archive_claim(reactivation_archived, candidate_id=candidate_id)
            raise UpdateError("release candidate reactivation was already archived and cannot be reused")
        ensure_private_directory(candidates, "release candidate directory", create=True)
        fsync_directory(args.staging_root)
        final = candidates / candidate_id
        if final.exists() or final.is_symlink():
            return validate_existing_stage(
                final, envelope, envelope_bytes, artifacts, signature, args.identity, current, host, candidate_id,
            )
        try:
            entries = list(os.scandir(candidates))
        except OSError as exc:
            raise UpdateError("release candidate directory cannot be enumerated safely") from exc
        for entry in entries:
            if entry.name.startswith("."):
                recover_incomplete_stage(Path(entry.path))
        try:
            entries = list(os.scandir(candidates))
        except OSError as exc:
            raise UpdateError("release candidate directory cannot be re-enumerated safely") from exc
        if any(
            not entry.is_dir(follow_symlinks=False)
            or not re.fullmatch(r"pixel-[0-9]{1,6}(?:\.[0-9]{1,6}){2}-[0-9a-f]{64}", entry.name)
            for entry in entries
        ):
            raise UpdateError("release candidate directory contains an unrecognized entry")
        if len(entries) >= MAX_STAGED_CANDIDATES:
            raise UpdateError("release candidate retention limit is reached")
        temporary = candidates / f".{candidate_id}.stage-{secrets.token_hex(8)}"
        os.mkdir(temporary, 0o700)
        filenames = {
            f"pixel-{envelope['version']}.update.json",
            f"pixel-{envelope['version']}.update.json.sig",
            *(specification["name"] for specification in envelope["artifacts"].values()),
            "STAGED-UPDATE.json",
        }
        prepared_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        receipt = stage_receipt(
            envelope, envelope_bytes, signature, args.identity, current, host, candidate_id, prepared_at,
        )
        try:
            write_private(temporary / f"pixel-{envelope['version']}.update.json", envelope_bytes)
            write_private(temporary / f"pixel-{envelope['version']}.update.json.sig", signature)
            for kind, payload in artifacts.items():
                write_private(temporary / envelope["artifacts"][kind]["name"], payload)
            write_private(
                temporary / "STAGED-UPDATE.json",
                (json.dumps(receipt, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8"),
            )
            fsync_directory(temporary)
            rename_directory_noreplace(temporary, final)
            fsync_directory(candidates)
        except BaseException:
            remove_incomplete_stage(temporary, filenames)
            raise
    return receipt


def prepared_candidate(
    staging_root: Path, candidate_id: str, allowed_signers: Path, identity: str,
) -> tuple[Path, dict[str, Any], bytes, dict[str, bytes], bytes, dict[str, Any], bytes, str, dict[str, Any]]:
    match = re.fullmatch(r"pixel-([0-9]{1,6}(?:\.[0-9]{1,6}){2})-([0-9a-f]{64})", candidate_id)
    if match is None:
        raise UpdateError("release candidate ID is invalid")
    ensure_private_directory(staging_root, "release staging root")
    candidates = staging_root / "candidates"
    ensure_private_directory(candidates, "release candidate directory")
    candidate = candidates / candidate_id
    ensure_private_directory(candidate, "prepared release candidate")
    version, expected_envelope_hash = match.groups()
    envelope_path = candidate / f"pixel-{version}.update.json"
    envelope, envelope_bytes, artifacts, manifest, signature = load_signed_bundle(
        envelope_path, allowed_signers, identity,
    )
    if envelope["version"] != version or not hmac.compare_digest(sha256(envelope_bytes), expected_envelope_hash):
        raise UpdateError("release candidate ID differs from its signed envelope")
    current, relation, eligible = release_relation(envelope)
    if relation != "upgrade" or not eligible:
        raise UpdateError("prepared release is no longer an eligible forward release")
    host = qualified_host(envelope)
    validate_existing_stage(
        candidate, envelope, envelope_bytes, artifacts, signature, identity, current, host, candidate_id,
    )
    stage_receipt_bytes = read_regular(candidate / "STAGED-UPDATE.json", MAX_STAGE_RECEIPT, "prepared release receipt")
    return candidate, envelope, envelope_bytes, artifacts, signature, manifest, stage_receipt_bytes, current, host


def historical_prepared_candidate(
    staging_root: Path, candidate_id: str, allowed_signers: Path, identity: str,
) -> tuple[Path, dict[str, Any], bytes, dict[str, bytes], bytes, dict[str, Any], bytes, str, dict[str, Any]]:
    """Revalidate terminal custody against its recorded staging baseline.

    Execution must remain relative to the live Pixel version. Cleanup instead needs to
    authenticate an older journey after later releases advanced, so its baseline comes
    only from the exact signed candidate's owner-private stage receipt.
    """
    match = re.fullmatch(r"pixel-([0-9]{1,6}(?:\.[0-9]{1,6}){2})-([0-9a-f]{64})", candidate_id)
    if match is None:
        raise UpdateError("release candidate ID is invalid")
    ensure_private_directory(staging_root, "release staging root")
    candidate = staging_root / "candidates" / candidate_id
    ensure_private_directory(candidate, "prepared release candidate")
    version, expected_envelope_hash = match.groups()
    envelope_path = candidate / f"pixel-{version}.update.json"
    envelope, envelope_bytes, artifacts, manifest, signature = load_signed_bundle(
        envelope_path, allowed_signers, identity, allow_pre_archive_policy=True,
    )
    if envelope["version"] != version or not hmac.compare_digest(sha256(envelope_bytes), expected_envelope_hash):
        raise UpdateError("release candidate ID differs from its signed envelope")
    receipt_path = candidate / "STAGED-UPDATE.json"
    receipt_value = parse_json(
        read_regular(receipt_path, MAX_STAGE_RECEIPT, "historical prepared release receipt"),
        "historical prepared release receipt",
    )
    current = semantic_version(receipt_value.get("currentVersion"), "historical staged Pixel version")
    if not (
        version_tuple(envelope["version"]) > version_tuple(current)
        and version_tuple(current) >= version_tuple(envelope["minimumUpgradablePixel"])
    ):
        raise UpdateError("historical prepared release was not an eligible forward release")
    host = qualified_host(envelope)
    validate_existing_stage(
        candidate, envelope, envelope_bytes, artifacts, signature, identity, current, host, candidate_id,
    )
    stage_receipt_bytes = read_regular(receipt_path, MAX_STAGE_RECEIPT, "historical prepared release receipt")
    return candidate, envelope, envelope_bytes, artifacts, signature, manifest, stage_receipt_bytes, current, host


def make_private_parents(root: Path, relative: PurePosixPath) -> Path:
    current = root
    for part in relative.parts:
        current = current / part
        if current.exists() or current.is_symlink():
            ensure_private_directory(current, "rehearsal extraction directory")
        else:
            os.mkdir(current, 0o700)
    return current


def write_tar_file(path: Path, handle: Any, size: int) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    remaining = size
    try:
        with os.fdopen(descriptor, "wb") as output:
            descriptor = -1
            while remaining:
                block = handle.read(min(1024 * 1024, remaining))
                if not block:
                    raise UpdateError("release archive member is truncated")
                output.write(block)
                remaining -= len(block)
            output.flush()
            os.fsync(output.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def extract_archive_privately(archive_payload: bytes, version: str, destination: Path) -> tuple[str, int, int]:
    ensure_private_directory(destination, "rehearsal extraction root")
    observed: set[str] = set()
    observed_directories: set[str] = set()
    count = 0
    total = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_payload), mode="r:gz") as archive:
            for member in archive:
                count += 1
                if count > MAX_ARCHIVE_MEMBERS:
                    raise UpdateError("release archive has too many members")
                name = member.name
                pure = safe_archive_path(name, version)
                if name in observed:
                    raise UpdateError("release archive contains an unsafe or duplicate path")
                observed.add(name)
                if not (member.isdir() or member.isreg()) or member.issym() or member.islnk() or member.mode & 0o6000:
                    raise UpdateError("release archive contains a special, linked, or privileged member")
                if member.size < 0 or member.size > MAX_ARCHIVE_MEMBER:
                    raise UpdateError("release archive member exceeds its size bound")
                total += member.size
                if total > MAX_ARCHIVE_UNPACKED:
                    raise UpdateError("release archive exceeds its unpacked size bound")
                relative = PurePosixPath(*pure.parts[1:])
                if not relative.parts:
                    if not member.isdir():
                        raise UpdateError("release archive root must be a directory")
                    continue
                directory_parts = relative.parts if member.isdir() else relative.parts[:-1]
                for depth in range(1, len(directory_parts) + 1):
                    observed_directories.add(PurePosixPath(*directory_parts[:depth]).as_posix())
                if len(observed_directories) > MAX_TREE_DIRECTORIES:
                    raise UpdateError("release archive creates too many directories")
                parent = make_private_parents(destination, PurePosixPath(*relative.parts[:-1]))
                target = parent / relative.parts[-1]
                if member.isdir():
                    if target.exists() or target.is_symlink():
                        ensure_private_directory(target, "rehearsal extraction directory")
                    else:
                        os.mkdir(target, 0o700)
                    continue
                if shutil.disk_usage(destination).free < member.size + 64 * 1024 * 1024:
                    raise UpdateError("insufficient private staging space for release rehearsal")
                handle = archive.extractfile(member)
                if handle is None:
                    raise UpdateError("release archive member is unreadable")
                write_tar_file(target, handle, member.size)
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise UpdateError("release archive could not be extracted safely") from exc
    return tree_identity(destination)


def tree_identity(root: Path) -> tuple[str, int, int]:
    ensure_private_directory(root, "rehearsal source tree")
    records: list[tuple[str, int, str]] = []
    total = 0
    directory_count = 0
    for directory, names, filenames in os.walk(root, topdown=True, followlinks=False):
        names.sort()
        filenames.sort()
        directory_path = Path(directory)
        ensure_private_directory(directory_path, "rehearsal source directory")
        for name in names:
            ensure_private_directory(directory_path / name, "rehearsal source directory")
            directory_count += 1
            if directory_count > MAX_TREE_DIRECTORIES:
                raise UpdateError("rehearsal source tree has too many directories")
        for name in filenames:
            path = directory_path / name
            relative = path.relative_to(root).as_posix()
            payload = read_regular(path, MAX_ARCHIVE_MEMBER, "rehearsal source file")
            info = path.lstat()
            if os.name != "nt" and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077):
                raise UpdateError("rehearsal source file permissions are unsafe")
            records.append((relative, len(payload), sha256(payload)))
            total += len(payload)
            if len(records) > MAX_ARCHIVE_MEMBERS or total > MAX_ARCHIVE_UNPACKED:
                raise UpdateError("rehearsal source tree exceeds its bounds")
    digest = hashlib.sha256()
    for relative, size, file_hash in sorted(records):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
        digest.update(file_hash.encode("ascii"))
        digest.update(b"\0")
    if not records:
        raise UpdateError("rehearsal source tree is empty")
    return digest.hexdigest(), len(records), total


def trusted_parser(name: str, source: Path) -> str:
    discovered = shutil.which(name)
    if discovered is None:
        raise UpdateError(f"release rehearsal requires the trusted {name} parser")
    try:
        resolved = Path(discovered).resolve(strict=True)
        info = resolved.stat()
    except OSError as exc:
        raise UpdateError(f"release rehearsal {name} parser is unavailable") from exc
    if (
        not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
        or (os.name != "nt" and (info.st_uid not in {0, os.geteuid()} or stat.S_IMODE(info.st_mode) & 0o022))
        or resolved.is_relative_to(source)
    ):
        raise UpdateError(f"release rehearsal {name} parser is unsafe")
    return str(resolved)


def candidate_syntax_checks(source: Path, manifest: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    node = trusted_parser("node", source)
    bash = trusted_parser("bash", source)
    environment = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/nonexistent", "NODE_OPTIONS": ""}
    node_result = subprocess.run(
        [node, "--version"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False, timeout=10, env=environment,
    )
    try:
        node_observed = node_result.stdout.decode("ascii", "strict").strip().removeprefix("v") if node_result.returncode == 0 else ""
    except UnicodeError as exc:
        raise UpdateError("installed Node runtime returned an invalid version") from exc
    node_required = manifest.get("nodeRuntime", {}).get("version") if isinstance(manifest.get("nodeRuntime"), dict) else None
    if not isinstance(node_required, str) or node_observed != node_required:
        raise UpdateError("installed Node runtime differs from the signed release requirement")
    python_observed = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    web_courier = manifest.get("webCourier")
    if sys.version_info < (3, 11) or not isinstance(web_courier, dict) or web_courier.get("python") != ">=3.11":
        raise UpdateError("installed Python runtime differs from the signed release requirement")
    json_paths: list[Path] = []
    shell_paths: list[Path] = []
    javascript_paths: list[Path] = []
    python_paths: list[Path] = []
    fixed_json = {"RELEASE-MANIFEST.json", "OPENCLAW-COMPATIBILITY.json", "onboarding.example.json"}
    ignored_source_components = {".git", "__pycache__", "dist", "node_modules"}
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        if any(part in ignored_source_components for part in relative.parts):
            continue
        if path.is_symlink():
            raise UpdateError("candidate source tree contains a symbolic link")
        if not path.is_file():
            continue
        top = relative.parts[0]
        if path.suffix == ".json" and (
            len(relative.parts) == 1 and path.name in fixed_json
            or top in {"schemas", "profiles"}
            or path.name in {"package.json", "package-lock.json", "openclaw.plugin.json", "policy.example.json", "actions.example.json", "managed.example.json"}
        ):
            json_paths.append(path)
        if path.suffix == ".sh" or relative.as_posix() == "pixel":
            shell_paths.append(path)
        if path.suffix in {".js", ".mjs"} and top in {"scripts", "plugin", "plugin-ops", "plugin-frontier"}:
            javascript_paths.append(path)
        if path.suffix == ".py" and top in {"scripts", "control", "deploy", "workspace-template"}:
            python_paths.append(path)
    for collection in (json_paths, shell_paths, javascript_paths, python_paths):
        collection.sort()
    if sum(len(collection) for collection in (json_paths, shell_paths, javascript_paths, python_paths)) > MAX_SYNTAX_FILES:
        raise UpdateError("release rehearsal syntax file count exceeds its bound")
    syntax_bytes = 0
    deadline = time.monotonic() + 120

    def bounded_source(path: Path, label: str) -> bytes:
        nonlocal syntax_bytes
        payload = read_regular(path, MAX_CODE_FILE, label)
        syntax_bytes += len(payload)
        if syntax_bytes > MAX_SYNTAX_BYTES:
            raise UpdateError("release rehearsal syntax bytes exceed their bound")
        return payload

    def parser_timeout() -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise UpdateError("release rehearsal syntax deadline expired")
        return min(5, remaining)

    for path in json_paths:
        payload = bounded_source(path, "candidate JSON document")
        try:
            json.loads(
                payload.decode("utf-8"), object_pairs_hook=reject_duplicate_keys,
                parse_constant=lambda item: (_ for _ in ()).throw(UpdateError(f"non-finite JSON denied: {item}")),
            )
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise UpdateError("candidate JSON document is malformed") from exc
    for path in shell_paths:
        bounded_source(path, "candidate shell file")
        result = subprocess.run(
            [bash, "--noprofile", "--norc", "-n", str(path)], cwd=source, env=environment,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=parser_timeout(),
        )
        if result.returncode:
            raise UpdateError("candidate shell syntax check failed")
    for path in javascript_paths:
        bounded_source(path, "candidate JavaScript file")
        result = subprocess.run(
            [node, "--check", str(path)], cwd=source, env=environment,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=parser_timeout(),
        )
        if result.returncode:
            raise UpdateError("candidate JavaScript syntax check failed")
    for path in python_paths:
        payload = bounded_source(path, "candidate Python file")
        try:
            compile(payload, path.relative_to(source).as_posix(), "exec", flags=ast.PyCF_ONLY_AST, dont_inherit=True)
        except (SyntaxError, ValueError) as exc:
            raise UpdateError("candidate Python syntax check failed") from exc
    return (
        {
            "nodeRequired": node_required,
            "nodeObserved": node_observed,
            "nodeCompatible": True,
            "pythonRequired": ">=3.11",
            "pythonObserved": python_observed,
            "pythonCompatible": True,
        },
        {
            "signedBundleRevalidated": True,
            "stageRevalidated": True,
            "safeExtraction": True,
            "hostCompatible": True,
            "releaseContract": True,
            "jsonDocuments": len(json_paths),
            "shellFiles": len(shell_paths),
            "javascriptFiles": len(javascript_paths),
            "pythonFiles": len(python_paths),
            "syntaxFailures": 0,
        },
    )


def rehearsal_receipt(
    envelope: dict[str, Any], envelope_bytes: bytes, stage_receipt_bytes: bytes, identity: str,
    current: str, host: dict[str, Any], candidate_id: str, toolchain: dict[str, Any], checks: dict[str, Any],
    tree_hash: str, file_count: int, extracted_bytes: int, rehearsed_at: str, status: str = "rehearsed",
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "status": status,
        "candidateId": candidate_id,
        "product": "Pixel",
        "version": envelope["version"],
        "currentVersion": current,
        "relation": "upgrade",
        "channel": envelope["channel"],
        "publisherIdentity": identity,
        "sourceCommit": envelope["sourceCommit"],
        "sourceTree": envelope["sourceTree"],
        "host": host,
        "toolchain": toolchain,
        "checks": checks,
        "envelopeSha256": sha256(envelope_bytes),
        "stageReceiptSha256": sha256(stage_receipt_bytes),
        "extractedTreeSha256": tree_hash,
        "extractedFileCount": file_count,
        "extractedBytes": extracted_bytes,
        "rehearsedAt": rehearsed_at,
        "candidateCodeExtracted": True,
        "candidateCodeParsed": True,
        "candidateCodeExecuted": False,
        "activeDeploymentChanged": False,
        "networkUsed": False,
        "activationAuthority": "external-exact-confirmation-only",
        "boundary": REHEARSAL_BOUNDARY,
    }


def remove_private_tree(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    ensure_private_directory(path, "incomplete rehearsal directory")
    count = 0
    for directory, names, filenames in os.walk(path, topdown=False, followlinks=False):
        directory_path = Path(directory)
        for name in filenames:
            target = directory_path / name
            info = target.lstat()
            count += 1
            if (
                count > MAX_TREE_ENTRIES or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or (os.name != "nt" and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077))
            ):
                raise UpdateError("incomplete rehearsal tree contains an unsafe file")
            target.unlink()
        for name in names:
            target = directory_path / name
            count += 1
            if count > MAX_TREE_ENTRIES:
                raise UpdateError("incomplete rehearsal tree has too many entries")
            ensure_private_directory(target, "incomplete rehearsal subdirectory")
            target.rmdir()
    path.rmdir()


def validate_existing_rehearsal(
    rehearsal: Path, envelope: dict[str, Any], envelope_bytes: bytes, stage_receipt_bytes: bytes,
    identity: str, current: str, host: dict[str, Any], candidate_id: str, manifest: dict[str, Any],
) -> dict[str, Any]:
    ensure_private_directory(rehearsal, "release rehearsal")
    try:
        observed = {entry.name for entry in os.scandir(rehearsal)}
    except OSError as exc:
        raise UpdateError("release rehearsal cannot be enumerated safely") from exc
    if observed != {"source", "REHEARSAL.json"}:
        raise UpdateError("release rehearsal file set is invalid")
    source = rehearsal / "source"
    tree_hash, file_count, extracted_bytes = tree_identity(source)
    toolchain, checks = candidate_syntax_checks(source, manifest)
    receipt_path = rehearsal / "REHEARSAL.json"
    receipt_bytes = read_regular(receipt_path, MAX_STAGE_RECEIPT, "release rehearsal receipt")
    receipt_info = receipt_path.lstat()
    if os.name != "nt" and (receipt_info.st_uid != os.geteuid() or stat.S_IMODE(receipt_info.st_mode) & 0o077):
        raise UpdateError("release rehearsal receipt permissions are unsafe")
    receipt = parse_json(receipt_bytes, "release rehearsal receipt")
    rehearsed_at = receipt.get("rehearsedAt")
    if not isinstance(rehearsed_at, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", rehearsed_at):
        raise UpdateError("release rehearsal timestamp is invalid")
    expected = rehearsal_receipt(
        envelope, envelope_bytes, stage_receipt_bytes, identity, current, host, candidate_id,
        toolchain, checks, tree_hash, file_count, extracted_bytes, rehearsed_at,
    )
    if receipt != expected:
        raise UpdateError("release rehearsal receipt differs from the revalidated candidate")
    return {**receipt, "status": "already-rehearsed"}


def rehearsed_candidate(
    staging_root: Path, candidate_id: str, allowed_signers: Path, identity: str,
) -> tuple[dict[str, Any], bytes, bytes, dict[str, Any], str, dict[str, Any], Path, bytes, dict[str, Any]]:
    (
        _candidate, envelope, envelope_bytes, _artifacts, _signature, manifest,
        stage_receipt_bytes, current, host,
    ) = prepared_candidate(staging_root, candidate_id, allowed_signers, identity)
    rehearsals = staging_root / "rehearsals"
    ensure_private_directory(rehearsals, "release rehearsal directory")
    rehearsal = rehearsals / candidate_id
    receipt = validate_existing_rehearsal(
        rehearsal, envelope, envelope_bytes, stage_receipt_bytes, identity, current, host,
        candidate_id, manifest,
    )
    receipt.pop("status", None)
    receipt["status"] = "rehearsed"
    receipt_bytes = read_regular(rehearsal / "REHEARSAL.json", MAX_STAGE_RECEIPT, "release rehearsal receipt")
    return envelope, envelope_bytes, stage_receipt_bytes, manifest, current, host, rehearsal / "source", receipt_bytes, receipt


def activation_intent(
    envelope: dict[str, Any], envelope_bytes: bytes, stage_receipt_bytes: bytes,
    rehearsal_receipt_bytes: bytes, rehearsal_receipt_value: dict[str, Any], identity: str,
    current: str, host: dict[str, Any], candidate_id: str,
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "operation": "pixel-release-activation",
        "candidateId": candidate_id,
        "product": "Pixel",
        "version": envelope["version"],
        "currentVersion": current,
        "relation": "upgrade",
        "channel": envelope["channel"],
        "publisherIdentity": identity,
        "sourceCommit": envelope["sourceCommit"],
        "sourceTree": envelope["sourceTree"],
        "host": host,
        "envelopeSha256": sha256(envelope_bytes),
        "stageReceiptSha256": sha256(stage_receipt_bytes),
        "rehearsalReceiptSha256": sha256(rehearsal_receipt_bytes),
        "extractedTreeSha256": rehearsal_receipt_value["extractedTreeSha256"],
        "candidateCodeWillExecute": True,
        "privateConfigurationWillBeRead": True,
        "activeDeploymentWillChange": True,
        "networkMayBeUsed": True,
        "rollback": "single-use-update-bound",
    }


def activation_preview_value(intent: dict[str, Any]) -> dict[str, Any]:
    activation_hash = sha256(canonical_json(intent))
    return {
        **intent,
        "status": "ready",
        "activationHash": activation_hash,
        "confirmationRequired": "repeat-the-full-activation-hash-with-confirm",
        "activationAuthority": "terminal-exact-confirmation-only",
        "candidateCodeExecuted": False,
        "activeDeploymentChanged": False,
        "networkUsed": False,
        "boundary": ACTIVATION_BOUNDARY,
    }


def activation_claim_value(intent: dict[str, Any], activation_hash: str, claimed_at: str) -> dict[str, Any]:
    return {
        **intent,
        "status": "claimed",
        "activationHash": activation_hash,
        "claimedAt": claimed_at,
        "claim": "single-use-atomic-no-replace",
        "candidateCodeCopied": True,
        "candidateCodeExecuted": False,
        "activeDeploymentChanged": False,
        "networkUsed": False,
        "boundary": ACTIVATION_BOUNDARY,
    }


def activation_preview(args: argparse.Namespace) -> dict[str, Any]:
    if sys.platform != "linux":
        raise UpdateError("release activation is supported only on qualified Linux hosts")
    if not args.staging_root.is_absolute():
        raise UpdateError("release staging root path must be absolute")
    with exclusive_stage_lock(args.staging_root):
        (
            envelope, envelope_bytes, stage_receipt_bytes, _manifest, current, host, _source,
            rehearsal_receipt_bytes, rehearsal_receipt_value,
        ) = rehearsed_candidate(args.staging_root, args.candidate_id, args.allowed_signers, args.identity)
        final = args.staging_root / "activations" / args.candidate_id
        if final.exists() or final.is_symlink():
            raise UpdateError("release activation has already been claimed")
        intent = activation_intent(
            envelope, envelope_bytes, stage_receipt_bytes, rehearsal_receipt_bytes,
            rehearsal_receipt_value, args.identity, current, host, args.candidate_id,
        )
        return activation_preview_value(intent)


def copy_private_tree(source: Path, destination: Path) -> tuple[str, int, int]:
    ensure_private_directory(source, "release activation source")
    ensure_private_directory(destination, "release activation copy")
    directory_count = 0
    for directory, names, filenames in os.walk(source, topdown=True, followlinks=False):
        names.sort()
        filenames.sort()
        source_directory = Path(directory)
        ensure_private_directory(source_directory, "release activation source directory")
        relative_directory = source_directory.relative_to(source)
        destination_directory = destination / relative_directory
        ensure_private_directory(destination_directory, "release activation copy directory")
        for name in names:
            source_child = source_directory / name
            ensure_private_directory(source_child, "release activation source directory")
            os.mkdir(destination_directory / name, 0o700)
            directory_count += 1
            if directory_count > MAX_TREE_DIRECTORIES:
                raise UpdateError("release activation source has too many directories")
        for name in filenames:
            payload = read_regular(source_directory / name, MAX_ARCHIVE_MEMBER, "release activation source file")
            write_private(destination_directory / name, payload)
    for directory, _names, _filenames in os.walk(destination, topdown=False, followlinks=False):
        fsync_directory(Path(directory))
    return tree_identity(destination)


def recover_incomplete_activations(activations: Path) -> None:
    try:
        entries = list(os.scandir(activations))
    except OSError as exc:
        raise UpdateError("release activation directory cannot be enumerated safely") from exc
    for entry in entries:
        if not entry.name.startswith("."):
            continue
        if re.fullmatch(
            r"\.pixel-[0-9]{1,6}(?:\.[0-9]{1,6}){2}-[0-9a-f]{64}\.activation-[0-9a-f]{16}",
            entry.name,
        ) is None:
            raise UpdateError("release activation directory contains an unrecognized entry")
        remove_private_tree(Path(entry.path))


def claim_activation(args: argparse.Namespace) -> dict[str, Any]:
    if not args.confirm:
        raise UpdateError("release activation requires --confirm")
    if not isinstance(args.activation_hash, str) or HASH.fullmatch(args.activation_hash) is None:
        raise UpdateError("release activation hash is invalid")
    if not isinstance(args.candidate_id, str) or re.fullmatch(r"pixel-[0-9]{1,6}(?:\.[0-9]{1,6}){2}-[0-9a-f]{64}", args.candidate_id) is None:
        raise UpdateError("release candidate ID is invalid")
    if sys.platform != "linux":
        raise UpdateError("release activation is supported only on qualified Linux hosts")
    if not args.staging_root.is_absolute():
        raise UpdateError("release staging root path must be absolute")
    with exclusive_stage_lock(args.staging_root):
        (
            envelope, envelope_bytes, stage_receipt_bytes, manifest, current, host, source,
            rehearsal_receipt_bytes, rehearsal_receipt_value,
        ) = rehearsed_candidate(args.staging_root, args.candidate_id, args.allowed_signers, args.identity)
        intent = activation_intent(
            envelope, envelope_bytes, stage_receipt_bytes, rehearsal_receipt_bytes,
            rehearsal_receipt_value, args.identity, current, host, args.candidate_id,
        )
        preview = activation_preview_value(intent)
        if not hmac.compare_digest(preview["activationHash"], args.activation_hash):
            raise UpdateError("release activation hash differs from the current verified preview")
        activations = args.staging_root / "activations"
        ensure_private_directory(activations, "release activation directory", create=True)
        fsync_directory(args.staging_root)
        recover_incomplete_activations(activations)
        final = activations / args.candidate_id
        if final.exists() or final.is_symlink():
            raise UpdateError("release activation has already been claimed")
        try:
            entries = list(os.scandir(activations))
        except OSError as exc:
            raise UpdateError("release activation directory cannot be enumerated safely") from exc
        if any(
            not entry.is_dir(follow_symlinks=False)
            or re.fullmatch(r"pixel-[0-9]{1,6}(?:\.[0-9]{1,6}){2}-[0-9a-f]{64}", entry.name) is None
            for entry in entries
        ):
            raise UpdateError("release activation directory contains an unrecognized entry")
        if len(entries) >= MAX_STAGED_CANDIDATES:
            raise UpdateError("release activation retention limit is reached")
        temporary = activations / f".{args.candidate_id}.activation-{secrets.token_hex(8)}"
        os.mkdir(temporary, 0o700)
        copied_source = temporary / "source"
        os.mkdir(copied_source, 0o700)
        try:
            copied_identity = copy_private_tree(source, copied_source)
            expected_identity = (
                rehearsal_receipt_value["extractedTreeSha256"],
                rehearsal_receipt_value["extractedFileCount"],
                rehearsal_receipt_value["extractedBytes"],
            )
            if copied_identity != expected_identity:
                raise UpdateError("private activation copy differs from the verified rehearsal")
            copied_toolchain, copied_checks = candidate_syntax_checks(copied_source, manifest)
            if copied_toolchain != rehearsal_receipt_value["toolchain"] or copied_checks != rehearsal_receipt_value["checks"]:
                raise UpdateError("private activation copy differs from the rehearsed compatibility result")
            claimed_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            claim = activation_claim_value(intent, preview["activationHash"], claimed_at)
            write_private(temporary / "ACTIVATION.json", canonical_json(claim))
            fsync_directory(temporary)
            rename_directory_noreplace(temporary, final)
            fsync_directory(activations)
        except BaseException:
            remove_private_tree(temporary)
            raise
    return claim


def validate_activation_claim(
    activation: Path, intent: dict[str, Any], activation_hash: str,
) -> tuple[dict[str, Any], bytes]:
    ensure_private_directory(activation, "release activation")
    try:
        observed = {entry.name for entry in os.scandir(activation)}
    except OSError as exc:
        raise UpdateError("release activation cannot be enumerated safely") from exc
    allowed_sets = (
        {"source", "ACTIVATION.json"},
        {"source", "ACTIVATION.json", "ACTIVATION-RESULT.json"},
        {"source", "ACTIVATION.json", "ACTIVATION-RESULT.json", "ROLLBACK.json"},
        {"source", "ACTIVATION.json", "ACTIVATION-RESULT.json", "ROLLBACK.json", "ROLLBACK-RESULT.json"},
    )
    if observed not in allowed_sets:
        raise UpdateError("release activation file set is invalid")
    ensure_private_directory(activation / "source", "release activation execution source")
    claim_path = activation / "ACTIVATION.json"
    claim_bytes = read_regular(claim_path, MAX_STAGE_RECEIPT, "release activation claim")
    claim_info = claim_path.lstat()
    if os.name != "nt" and (claim_info.st_uid != os.geteuid() or stat.S_IMODE(claim_info.st_mode) & 0o077):
        raise UpdateError("release activation claim permissions are unsafe")
    claim = parse_json(claim_bytes, "release activation claim")
    claimed_at = claim.get("claimedAt")
    if not isinstance(claimed_at, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", claimed_at,
    ):
        raise UpdateError("release activation claim timestamp is invalid")
    expected_hash = sha256(canonical_json(intent))
    if not hmac.compare_digest(expected_hash, activation_hash):
        raise UpdateError("release activation hash differs from the current verified preview")
    expected = activation_claim_value(intent, expected_hash, claimed_at)
    if claim != expected:
        raise UpdateError("release activation claim differs from the verified candidate")
    return claim, claim_bytes


def active_version(path: Path) -> str:
    try:
        return semantic_version(read_regular(path, 64, "active Pixel version").decode("ascii").strip(), "active Pixel version")
    except UnicodeError as exc:
        raise UpdateError("active Pixel version is malformed") from exc


def write_activation_result_receipt(
    activation: Path, envelope: dict[str, Any], current: str, activation_hash: str,
    claim_bytes: bytes, outcome: str, phase: str, active_version_file: Path,
    rollback_marker: Path | None,
) -> dict[str, Any]:
    result_path = activation / "ACTIVATION-RESULT.json"
    if result_path.exists() or result_path.is_symlink():
        raise UpdateError("release activation result is already recorded")
    observed_active = active_version(active_version_file)
    active_changed = observed_active == envelope["version"]
    if observed_active not in {current, envelope["version"]}:
        raise UpdateError("observed active version is outside the claimed update transition")
    marker_hash: str | None = None
    rollback_available = False
    if active_changed and rollback_marker is not None and (rollback_marker.exists() or rollback_marker.is_symlink()):
        marker = read_regular(rollback_marker, 4096, "activation rollback marker")
        marker_hash = sha256(marker)
        rollback_available = True
    if outcome == "activated":
        if not active_changed:
            raise UpdateError("successful activation did not install the claimed release")
        if marker_hash is None or not rollback_available:
            raise UpdateError("successful activation has no update-bound rollback marker")
        if phase != "record":
            raise UpdateError("successful activation must finish in the record phase")
    completed_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    result = {
        "schemaVersion": 1,
        "operation": "pixel-release-activation-result",
        "status": outcome,
        "candidateId": activation.name,
        "product": "Pixel",
        "version": envelope["version"],
        "previousVersion": current,
        "activeVersion": observed_active,
        "activationHash": activation_hash,
        "activationClaimSha256": sha256(claim_bytes),
        "executionPhase": phase,
        "completedAt": completed_at,
        "candidateCodeExecuted": True,
        "privateConfigurationRead": True,
        "activeDeploymentChanged": active_changed,
        "networkMayHaveBeenUsed": True,
        "rollbackAvailable": rollback_available,
        "rollbackMarkerSha256": marker_hash,
        "boundary": ACTIVATION_RESULT_BOUNDARY,
    }
    write_private(result_path, canonical_json(result))
    fsync_directory(activation)
    return result


def record_activation_result(args: argparse.Namespace) -> dict[str, Any]:
    if not args.confirm:
        raise UpdateError("release activation result requires --confirm")
    if not isinstance(args.candidate_id, str) or re.fullmatch(r"pixel-[0-9]{1,6}(?:\.[0-9]{1,6}){2}-[0-9a-f]{64}", args.candidate_id) is None:
        raise UpdateError("release candidate ID is invalid")
    if args.outcome not in {"activated", "failed"}:
        raise UpdateError("release activation outcome is invalid")
    if args.phase not in {"configure", "bootstrap", "plan", "apply", "verify", "record"}:
        raise UpdateError("release activation phase is invalid")
    if not isinstance(args.activation_hash, str) or HASH.fullmatch(args.activation_hash) is None:
        raise UpdateError("release activation hash is invalid")
    if sys.platform != "linux":
        raise UpdateError("release activation is supported only on qualified Linux hosts")
    if not args.staging_root.is_absolute() or not args.active_version_file.is_absolute():
        raise UpdateError("release activation paths must be absolute")
    early_result = args.staging_root / "activations" / args.candidate_id / "ACTIVATION-RESULT.json"
    if early_result.exists() or early_result.is_symlink():
        raise UpdateError("release activation result is already recorded")
    with exclusive_stage_lock(args.staging_root):
        (
            envelope, envelope_bytes, stage_receipt_bytes, _manifest, current, host, _source,
            rehearsal_receipt_bytes, rehearsal_receipt_value,
        ) = rehearsed_candidate(args.staging_root, args.candidate_id, args.allowed_signers, args.identity)
        intent = activation_intent(
            envelope, envelope_bytes, stage_receipt_bytes, rehearsal_receipt_bytes,
            rehearsal_receipt_value, args.identity, current, host, args.candidate_id,
        )
        activation = args.staging_root / "activations" / args.candidate_id
        _claim, claim_bytes = validate_activation_claim(activation, intent, args.activation_hash)
        result = write_activation_result_receipt(
            activation, envelope, current, args.activation_hash, claim_bytes, args.outcome, args.phase,
            args.active_version_file, args.rollback_marker,
        )
    return result


def validate_activated_result(
    activation: Path, envelope: dict[str, Any], current: str, activation_hash: str, claim_bytes: bytes,
) -> tuple[dict[str, Any], bytes]:
    result_path = activation / "ACTIVATION-RESULT.json"
    result_bytes = read_regular(result_path, MAX_STAGE_RECEIPT, "release activation result")
    result_info = result_path.lstat()
    if os.name != "nt" and (result_info.st_uid != os.geteuid() or stat.S_IMODE(result_info.st_mode) & 0o077):
        raise UpdateError("release activation result permissions are unsafe")
    result = parse_json(result_bytes, "release activation result")
    completed_at = result.get("completedAt")
    marker_hash = result.get("rollbackMarkerSha256")
    if not isinstance(completed_at, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", completed_at,
    ):
        raise UpdateError("release activation result timestamp is invalid")
    if not isinstance(marker_hash, str) or HASH.fullmatch(marker_hash) is None:
        raise UpdateError("release activation rollback binding is invalid")
    expected = {
        "schemaVersion": 1,
        "operation": "pixel-release-activation-result",
        "status": "activated",
        "candidateId": activation.name,
        "product": "Pixel",
        "version": envelope["version"],
        "previousVersion": current,
        "activeVersion": envelope["version"],
        "activationHash": activation_hash,
        "activationClaimSha256": sha256(claim_bytes),
        "executionPhase": "record",
        "completedAt": completed_at,
        "candidateCodeExecuted": True,
        "privateConfigurationRead": True,
        "activeDeploymentChanged": True,
        "networkMayHaveBeenUsed": True,
        "rollbackAvailable": True,
        "rollbackMarkerSha256": marker_hash,
        "boundary": ACTIVATION_RESULT_BOUNDARY,
    }
    if result != expected:
        raise UpdateError("release activation result is not an exact successful activation receipt")
    return result, result_bytes


def validate_activation_result(
    activation: Path, envelope: dict[str, Any], current: str, activation_hash: str, claim_bytes: bytes,
) -> tuple[dict[str, Any], bytes, str]:
    result_path = activation / "ACTIVATION-RESULT.json"
    result_bytes = read_regular(result_path, MAX_STAGE_RECEIPT, "release activation result")
    result_info = result_path.lstat()
    if os.name != "nt" and (result_info.st_uid != os.geteuid() or stat.S_IMODE(result_info.st_mode) & 0o077):
        raise UpdateError("release activation result permissions are unsafe")
    result = parse_json(result_bytes, "release activation result")
    if result.get("status") == "activated":
        validated, validated_bytes = validate_activated_result(
            activation, envelope, current, activation_hash, claim_bytes,
        )
        return validated, validated_bytes, "activated"
    if result.get("status") != "failed":
        raise UpdateError("release activation result status is invalid")
    completed_at = result.get("completedAt")
    if not isinstance(completed_at, str) or re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", completed_at,
    ) is None:
        raise UpdateError("release activation result timestamp is invalid")
    active = result.get("activeVersion")
    if active not in {current, envelope["version"]}:
        raise UpdateError("failed activation result has an invalid active version")
    phase = result.get("executionPhase")
    if phase not in {"configure", "bootstrap", "plan", "apply", "verify", "record"}:
        raise UpdateError("failed activation result phase is invalid")
    marker_hash = result.get("rollbackMarkerSha256")
    if marker_hash is not None and (not isinstance(marker_hash, str) or HASH.fullmatch(marker_hash) is None):
        raise UpdateError("failed activation rollback binding is invalid")
    active_changed = active == envelope["version"]
    rollback_available = active_changed and marker_hash is not None
    if marker_hash is not None and not active_changed:
        raise UpdateError("failed activation cannot bind rollback without an active deployment change")
    expected = {
        "schemaVersion": 1,
        "operation": "pixel-release-activation-result",
        "status": "failed",
        "candidateId": activation.name,
        "product": "Pixel",
        "version": envelope["version"],
        "previousVersion": current,
        "activeVersion": active,
        "activationHash": activation_hash,
        "activationClaimSha256": sha256(claim_bytes),
        "executionPhase": phase,
        "completedAt": completed_at,
        "candidateCodeExecuted": True,
        "privateConfigurationRead": True,
        "activeDeploymentChanged": active_changed,
        "networkMayHaveBeenUsed": True,
        "rollbackAvailable": rollback_available,
        "rollbackMarkerSha256": marker_hash,
        "boundary": ACTIVATION_RESULT_BOUNDARY,
    }
    if result != expected:
        raise UpdateError("release activation result is not an exact failed activation receipt")
    return result, result_bytes, "failed"


def rollback_intent(
    candidate_id: str, envelope: dict[str, Any], current: str, activation_hash: str,
    activation_result_bytes: bytes, marker_hash: str,
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "operation": "pixel-release-update-rollback",
        "candidateId": candidate_id,
        "product": "Pixel",
        "version": envelope["version"],
        "restoreVersion": current,
        "activationHash": activation_hash,
        "activationResultSha256": sha256(activation_result_bytes),
        "rollbackMarkerSha256": marker_hash,
        "trustedControllerOnly": True,
        "singleUse": True,
    }


def rollback_preview_value(intent: dict[str, Any]) -> dict[str, Any]:
    return {
        **intent,
        "status": "ready",
        "rollbackHash": sha256(canonical_json(intent)),
        "confirmationRequired": "repeat-the-full-rollback-hash-with-confirm",
        "activeDeploymentChanged": False,
        "boundary": ROLLBACK_BOUNDARY,
    }


def stored_rehearsal_receipt_for_rollback(
    staging_root: Path, candidate_id: str, envelope: dict[str, Any], envelope_bytes: bytes,
    stage_receipt_bytes: bytes, identity: str, current: str, host: dict[str, Any],
) -> tuple[bytes, dict[str, Any]]:
    rehearsal = staging_root / "rehearsals" / candidate_id
    ensure_private_directory(rehearsal, "release rehearsal")
    try:
        observed = {entry.name for entry in os.scandir(rehearsal)}
    except OSError as exc:
        raise UpdateError("release rehearsal cannot be enumerated safely") from exc
    if observed != {"source", "REHEARSAL.json"}:
        raise UpdateError("release rehearsal file set is invalid")
    ensure_private_directory(rehearsal / "source", "release rehearsal source")
    receipt_path = rehearsal / "REHEARSAL.json"
    receipt_bytes = read_regular(receipt_path, MAX_STAGE_RECEIPT, "release rehearsal receipt")
    receipt_info = receipt_path.lstat()
    if os.name != "nt" and (receipt_info.st_uid != os.geteuid() or stat.S_IMODE(receipt_info.st_mode) & 0o077):
        raise UpdateError("release rehearsal receipt permissions are unsafe")
    receipt = parse_json(receipt_bytes, "release rehearsal receipt")
    toolchain = exact_keys(
        receipt.get("toolchain"),
        {"nodeRequired", "nodeObserved", "nodeCompatible", "pythonRequired", "pythonObserved", "pythonCompatible"},
        "release rehearsal toolchain",
    )
    if (
        any(not isinstance(toolchain.get(field), str) or SEMVER.fullmatch(toolchain[field]) is None for field in ("nodeRequired", "nodeObserved", "pythonObserved"))
        or toolchain.get("nodeCompatible") is not True or toolchain.get("pythonCompatible") is not True
        or toolchain.get("pythonRequired") != ">=3.11"
    ):
        raise UpdateError("release rehearsal toolchain receipt is invalid")
    checks = exact_keys(
        receipt.get("checks"),
        {"signedBundleRevalidated", "stageRevalidated", "safeExtraction", "hostCompatible", "releaseContract", "jsonDocuments", "shellFiles", "javascriptFiles", "pythonFiles", "syntaxFailures"},
        "release rehearsal checks",
    )
    if (
        any(checks.get(field) is not True for field in ("signedBundleRevalidated", "stageRevalidated", "safeExtraction", "hostCompatible", "releaseContract"))
        or type(checks.get("syntaxFailures")) is not int or checks.get("syntaxFailures") != 0
        or any(type(checks.get(field)) is not int or not 0 <= checks[field] <= MAX_SYNTAX_FILES for field in ("jsonDocuments", "shellFiles", "javascriptFiles", "pythonFiles"))
        or checks.get("jsonDocuments", 0) < 1
        or sum(checks[field] for field in ("jsonDocuments", "shellFiles", "javascriptFiles", "pythonFiles")) > MAX_SYNTAX_FILES
    ):
        raise UpdateError("release rehearsal check receipt is invalid")
    rehearsed_at = receipt.get("rehearsedAt")
    tree_hash = receipt.get("extractedTreeSha256")
    file_count = receipt.get("extractedFileCount")
    extracted_bytes = receipt.get("extractedBytes")
    if (
        not isinstance(rehearsed_at, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", rehearsed_at) is None
        or not isinstance(tree_hash, str) or HASH.fullmatch(tree_hash) is None
        or type(file_count) is not int or not 1 <= file_count <= MAX_ARCHIVE_MEMBERS
        or type(extracted_bytes) is not int or not 1 <= extracted_bytes <= MAX_ARCHIVE_UNPACKED
    ):
        raise UpdateError("release rehearsal extraction receipt is invalid")
    expected = rehearsal_receipt(
        envelope, envelope_bytes, stage_receipt_bytes, identity, current, host, candidate_id,
        toolchain, checks, tree_hash, file_count, extracted_bytes, rehearsed_at,
    )
    if receipt != expected:
        raise UpdateError("release rehearsal receipt differs from the signed staged update")
    return receipt_bytes, receipt


def rollback_context(args: argparse.Namespace) -> tuple[Path, dict[str, Any], str, dict[str, Any], bytes, dict[str, Any], bytes]:
    (
        _candidate, envelope, envelope_bytes, _artifacts, _signature, _manifest,
        stage_receipt_bytes, current, host,
    ) = prepared_candidate(args.staging_root, args.candidate_id, args.allowed_signers, args.identity)
    rehearsal_receipt_bytes, rehearsal_receipt_value = stored_rehearsal_receipt_for_rollback(
        args.staging_root, args.candidate_id, envelope, envelope_bytes, stage_receipt_bytes,
        args.identity, current, host,
    )
    activation_intent_value = activation_intent(
        envelope, envelope_bytes, stage_receipt_bytes, rehearsal_receipt_bytes,
        rehearsal_receipt_value, args.identity, current, host, args.candidate_id,
    )
    activation = args.staging_root / "activations" / args.candidate_id
    _claim, claim_bytes = validate_activation_claim(activation, activation_intent_value, args.activation_hash)
    activated, activated_bytes = validate_activated_result(
        activation, envelope, current, args.activation_hash, claim_bytes,
    )
    return activation, envelope, current, activated, activated_bytes, activation_intent_value, claim_bytes


def rollback_preview(args: argparse.Namespace) -> dict[str, Any]:
    if sys.platform != "linux":
        raise UpdateError("release rollback is supported only on qualified Linux hosts")
    if not isinstance(args.activation_hash, str) or HASH.fullmatch(args.activation_hash) is None:
        raise UpdateError("release activation hash is invalid")
    if not args.staging_root.is_absolute() or not args.active_version_file.is_absolute() or not args.rollback_marker.is_absolute():
        raise UpdateError("release rollback paths must be absolute")
    with exclusive_stage_lock(args.staging_root):
        activation, envelope, current, activated, activated_bytes, _intent, _claim = rollback_context(args)
        if (activation / "ROLLBACK.json").exists() or (activation / "ROLLBACK.json").is_symlink():
            raise UpdateError("release rollback has already been claimed")
        if active_version(args.active_version_file) != envelope["version"]:
            raise UpdateError("the activated release is no longer the active Pixel version")
        marker = read_regular(args.rollback_marker, 4096, "activation rollback marker")
        marker_hash = sha256(marker)
        if not hmac.compare_digest(marker_hash, activated["rollbackMarkerSha256"]):
            raise UpdateError("rollback marker differs from the activated update receipt")
        return rollback_preview_value(
            rollback_intent(args.candidate_id, envelope, current, args.activation_hash, activated_bytes, marker_hash),
        )


def rollback_claim_value(intent: dict[str, Any], rollback_hash: str, claimed_at: str) -> dict[str, Any]:
    return {
        **intent,
        "status": "claimed",
        "rollbackHash": rollback_hash,
        "claimedAt": claimed_at,
        "claim": "single-use-exclusive-create",
        "activeDeploymentChanged": False,
        "boundary": ROLLBACK_BOUNDARY,
    }


def claim_update_rollback(args: argparse.Namespace) -> dict[str, Any]:
    if not args.confirm:
        raise UpdateError("release rollback requires --confirm")
    if not isinstance(args.rollback_hash, str) or HASH.fullmatch(args.rollback_hash) is None:
        raise UpdateError("release rollback hash is invalid")
    preview = rollback_preview(args)
    if not hmac.compare_digest(preview["rollbackHash"], args.rollback_hash):
        raise UpdateError("release rollback hash differs from the current verified preview")
    with exclusive_stage_lock(args.staging_root):
        activation, envelope, current, activated, activated_bytes, _intent, _claim = rollback_context(args)
        claim_path = activation / "ROLLBACK.json"
        if claim_path.exists() or claim_path.is_symlink():
            raise UpdateError("release rollback has already been claimed")
        if active_version(args.active_version_file) != envelope["version"]:
            raise UpdateError("the activated release is no longer the active Pixel version")
        marker = read_regular(args.rollback_marker, 4096, "activation rollback marker")
        marker_hash = sha256(marker)
        if not hmac.compare_digest(marker_hash, activated["rollbackMarkerSha256"]):
            raise UpdateError("rollback marker differs from the activated update receipt")
        intent = rollback_intent(
            args.candidate_id, envelope, current, args.activation_hash, activated_bytes, marker_hash,
        )
        expected_hash = sha256(canonical_json(intent))
        if not hmac.compare_digest(expected_hash, args.rollback_hash):
            raise UpdateError("release rollback hash differs from the current verified preview")
        claimed_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        claim = rollback_claim_value(intent, expected_hash, claimed_at)
        write_private(claim_path, canonical_json(claim))
        fsync_directory(activation)
    return claim


def validate_rollback_claim(
    activation: Path, intent: dict[str, Any], rollback_hash: str,
) -> tuple[dict[str, Any], bytes]:
    claim_path = activation / "ROLLBACK.json"
    claim_bytes = read_regular(claim_path, MAX_STAGE_RECEIPT, "release rollback claim")
    claim_info = claim_path.lstat()
    if os.name != "nt" and (claim_info.st_uid != os.geteuid() or stat.S_IMODE(claim_info.st_mode) & 0o077):
        raise UpdateError("release rollback claim permissions are unsafe")
    claim = parse_json(claim_bytes, "release rollback claim")
    claimed_at = claim.get("claimedAt")
    if not isinstance(claimed_at, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", claimed_at,
    ):
        raise UpdateError("release rollback claim timestamp is invalid")
    expected_hash = sha256(canonical_json(intent))
    if not hmac.compare_digest(expected_hash, rollback_hash):
        raise UpdateError("release rollback hash differs from the exact claim")
    if claim != rollback_claim_value(intent, expected_hash, claimed_at):
        raise UpdateError("release rollback claim differs from the activated update")
    return claim, claim_bytes


def record_update_rollback(args: argparse.Namespace) -> dict[str, Any]:
    if not args.confirm:
        raise UpdateError("release rollback result requires --confirm")
    if args.outcome not in {"rolled-back", "failed"}:
        raise UpdateError("release rollback outcome is invalid")
    if args.phase not in {"rollback", "record"}:
        raise UpdateError("release rollback phase is invalid")
    if not isinstance(args.rollback_hash, str) or HASH.fullmatch(args.rollback_hash) is None:
        raise UpdateError("release rollback hash is invalid")
    if not isinstance(args.activation_hash, str) or HASH.fullmatch(args.activation_hash) is None:
        raise UpdateError("release activation hash is invalid")
    if sys.platform != "linux":
        raise UpdateError("release rollback is supported only on qualified Linux hosts")
    if not args.staging_root.is_absolute() or not args.active_version_file.is_absolute() or not args.rollback_marker.is_absolute():
        raise UpdateError("release rollback paths must be absolute")
    with exclusive_stage_lock(args.staging_root):
        activation, envelope, current, activated, activated_bytes, _activation_intent, _claim = rollback_context(args)
        marker_hash = activated["rollbackMarkerSha256"]
        intent = rollback_intent(
            args.candidate_id, envelope, current, args.activation_hash, activated_bytes, marker_hash,
        )
        _rollback_claim, rollback_claim_bytes = validate_rollback_claim(activation, intent, args.rollback_hash)
        result_path = activation / "ROLLBACK-RESULT.json"
        if result_path.exists() or result_path.is_symlink():
            raise UpdateError("release rollback result is already recorded")
        observed_active = active_version(args.active_version_file)
        if observed_active not in {current, envelope["version"]}:
            raise UpdateError("observed active version is outside the claimed rollback transition")
        marker_present = args.rollback_marker.exists() or args.rollback_marker.is_symlink()
        if marker_present:
            marker = read_regular(args.rollback_marker, 4096, "activation rollback marker")
            if not hmac.compare_digest(sha256(marker), marker_hash):
                raise UpdateError("rollback marker changed during trusted rollback")
        restored = observed_active == current
        if args.outcome == "rolled-back" and (not restored or marker_present or args.phase != "record"):
            raise UpdateError("successful rollback did not consume the marker and restore the preceding release")
        completed_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        result = {
            "schemaVersion": 1,
            "operation": "pixel-release-update-rollback-result",
            "status": args.outcome,
            "candidateId": args.candidate_id,
            "product": "Pixel",
            "version": envelope["version"],
            "restoredVersion": current if restored else None,
            "activeVersion": observed_active,
            "activationHash": args.activation_hash,
            "rollbackHash": args.rollback_hash,
            "rollbackClaimSha256": sha256(rollback_claim_bytes),
            "executionPhase": args.phase,
            "completedAt": completed_at,
            "trustedControllerExecuted": True,
            "activeDeploymentRestored": restored,
            "rollbackMarkerConsumed": not marker_present,
            "recoveryRequired": args.outcome == "failed",
            "boundary": ROLLBACK_RESULT_BOUNDARY,
        }
        write_private(result_path, canonical_json(result))
        fsync_directory(activation)
    return result


def validate_successful_rollback_result(
    activation: Path, envelope: dict[str, Any], current: str, activation_hash: str,
    rollback_hash: str, rollback_claim_bytes: bytes,
) -> tuple[dict[str, Any], bytes]:
    result_path = activation / "ROLLBACK-RESULT.json"
    result_bytes = read_regular(result_path, MAX_STAGE_RECEIPT, "release rollback result")
    result_info = result_path.lstat()
    if os.name != "nt" and (result_info.st_uid != os.geteuid() or stat.S_IMODE(result_info.st_mode) & 0o077):
        raise UpdateError("release rollback result permissions are unsafe")
    result = parse_json(result_bytes, "release rollback result")
    completed_at = result.get("completedAt")
    if not isinstance(completed_at, str) or re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", completed_at,
    ) is None:
        raise UpdateError("release rollback result timestamp is invalid")
    expected = {
        "schemaVersion": 1,
        "operation": "pixel-release-update-rollback-result",
        "status": "rolled-back",
        "candidateId": activation.name,
        "product": "Pixel",
        "version": envelope["version"],
        "restoredVersion": current,
        "activeVersion": current,
        "activationHash": activation_hash,
        "rollbackHash": rollback_hash,
        "rollbackClaimSha256": sha256(rollback_claim_bytes),
        "executionPhase": "record",
        "completedAt": completed_at,
        "trustedControllerExecuted": True,
        "activeDeploymentRestored": True,
        "rollbackMarkerConsumed": True,
        "recoveryRequired": False,
        "boundary": ROLLBACK_RESULT_BOUNDARY,
    }
    if result != expected:
        raise UpdateError("release rollback result is not an exact successful rollback receipt")
    return result, result_bytes


def checksum_manifest_entry(
    payload: bytes, target: str, label: str, *, leading_dot: bool,
) -> str:
    if not payload.endswith(b"\n"):
        raise UpdateError(f"{label} must end with one complete checksum line")
    try:
        lines = payload.decode("ascii").splitlines()
    except UnicodeError as exc:
        raise UpdateError(f"{label} is malformed") from exc
    if not 1 <= len(lines) <= MAX_TREE_ENTRIES:
        raise UpdateError(f"{label} entry count is invalid")
    observed: dict[str, str] = {}
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if match is None:
            raise UpdateError(f"{label} contains a malformed checksum line")
        file_hash, relative = match.groups()
        prefix = "./" if leading_dot else ""
        if not relative.startswith(prefix) or (not leading_dot and relative.startswith("./")):
            raise UpdateError(f"{label} contains a non-canonical path")
        normalized_source = relative[2:] if leading_dot else relative
        pure = PurePosixPath(normalized_source)
        if (
            not pure.parts or pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts)
            or f"{prefix}{pure.as_posix()}" != relative
            or any(ord(character) < 0x20 or ord(character) == 0x7f for character in relative)
        ):
            raise UpdateError(f"{label} contains a non-canonical path")
        if relative in observed:
            raise UpdateError(f"{label} contains a duplicate path")
        observed[relative] = file_hash
    result = observed.get(target)
    if result is None:
        raise UpdateError(f"{label} does not bind {target}")
    return result


def reactivation_deployment_record(
    staging_root: Path, candidate_id: str, version: str,
) -> tuple[bytes, str, str, str]:
    activation_source = staging_root / "activations" / candidate_id / "source"
    generated = activation_source / ".generated"
    ensure_private_directory(activation_source, "release activation source")
    ensure_private_directory(generated, "release activation generated directory")
    record_path = generated / "deployment.json"
    record_bytes = read_regular(
        record_path, MAX_STAGE_RECEIPT, "release activation deployment record",
    )
    record_info = record_path.lstat()
    if os.name != "nt" and (
        record_info.st_uid != os.geteuid() or stat.S_IMODE(record_info.st_mode) & 0o077
    ):
        raise UpdateError("release activation deployment record permissions are unsafe")
    record = parse_json(record_bytes, "release activation deployment record")
    generated_at = record.get("generatedAt")
    if not isinstance(generated_at, str) or re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{3})?Z",
        generated_at,
    ) is None:
        raise UpdateError("release activation deployment record timestamp is invalid")
    record_hash = sha256(record_bytes)

    release = staging_root.parent / "releases" / version
    if not release.is_absolute():
        raise UpdateError("retained release path must be absolute")
    try:
        release_info = release.lstat()
        resolved_release = release.resolve(strict=True)
    except OSError as exc:
        raise UpdateError("retained release is unavailable or unsafe") from exc
    if (
        resolved_release != release or not stat.S_ISDIR(release_info.st_mode)
        or release_info.st_uid != os.geteuid() or stat.S_IMODE(release_info.st_mode) & 0o022
    ):
        raise UpdateError("retained release ownership or permissions are unsafe")

    deployment_inputs_path = release / "deployment-inputs.sha256"
    deployment_inputs_bytes = read_regular(
        deployment_inputs_path, MAX_CODE_FILE, "retained release deployment inputs",
        trust_anchor=True,
    )
    deployment_record_hash = checksum_manifest_entry(
        deployment_inputs_bytes, ".generated/deployment.json",
        "retained release deployment inputs", leading_dot=False,
    )
    if not hmac.compare_digest(deployment_record_hash, record_hash):
        raise UpdateError("release activation deployment record differs from the retained release")

    install_manifest_bytes = read_regular(
        release / "install-manifest.sha256", MAX_CODE_FILE,
        "retained release install manifest", trust_anchor=True,
    )
    deployment_inputs_hash = sha256(deployment_inputs_bytes)
    installed_deployment_inputs_hash = checksum_manifest_entry(
        install_manifest_bytes, "./deployment-inputs.sha256",
        "retained release install manifest", leading_dot=True,
    )
    if not hmac.compare_digest(installed_deployment_inputs_hash, deployment_inputs_hash):
        raise UpdateError("retained release deployment inputs differ from its install manifest")
    return record_bytes, record_hash, deployment_inputs_hash, sha256(install_manifest_bytes)


def reactivation_intent(
    candidate_id: str, envelope: dict[str, Any], envelope_bytes: bytes, current: str,
    host: dict[str, Any], identity: str, stage_receipt_bytes: bytes,
    rehearsal_receipt_bytes: bytes, rehearsal_receipt_value: dict[str, Any],
    activation_hash: str, activation_claim_bytes: bytes, activation_result_bytes: bytes,
    rollback_claim_bytes: bytes, rollback_result_bytes: bytes,
    activation_deployment_record_sha256: str,
    retained_deployment_inputs_sha256: str, retained_install_manifest_sha256: str,
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "operation": "pixel-release-reactivation",
        "candidateId": candidate_id,
        "product": "Pixel",
        "version": envelope["version"],
        "restoreVersion": current,
        "channel": envelope["channel"],
        "publisherIdentity": identity,
        "sourceCommit": envelope["sourceCommit"],
        "sourceTree": envelope["sourceTree"],
        "host": host,
        "envelopeSha256": sha256(envelope_bytes),
        "stageReceiptSha256": sha256(stage_receipt_bytes),
        "rehearsalReceiptSha256": sha256(rehearsal_receipt_bytes),
        "extractedTreeSha256": rehearsal_receipt_value["extractedTreeSha256"],
        "originalActivationHash": activation_hash,
        "activationClaimSha256": sha256(activation_claim_bytes),
        "activationResultSha256": sha256(activation_result_bytes),
        "rollbackClaimSha256": sha256(rollback_claim_bytes),
        "rollbackResultSha256": sha256(rollback_result_bytes),
        "activationDeploymentRecordSha256": activation_deployment_record_sha256,
        "retainedDeploymentInputsSha256": retained_deployment_inputs_sha256,
        "retainedInstallManifestSha256": retained_install_manifest_sha256,
        "previouslyActivatedController": True,
        "candidateCodeWillExecute": True,
        "privateConfigurationWillBeRead": True,
        "activeDeploymentWillChange": True,
        "networkMayBeUsed": True,
        "rollback": "single-use-reactivation-bound",
    }


def reactivation_context(args: argparse.Namespace, *, require_restored: bool) -> dict[str, Any]:
    (
        _candidate, envelope, envelope_bytes, _artifacts, _signature, manifest,
        stage_receipt_bytes, current, host,
    ) = historical_prepared_candidate(
        args.staging_root, args.candidate_id, args.allowed_signers, args.identity,
    )
    if current_version() != envelope["version"]:
        raise UpdateError("release reactivation requires the previously activated release controller")
    rehearsal_receipt_bytes, rehearsal_receipt_value = stored_rehearsal_receipt_for_rollback(
        args.staging_root, args.candidate_id, envelope, envelope_bytes, stage_receipt_bytes,
        args.identity, current, host,
    )
    source = args.staging_root / "rehearsals" / args.candidate_id / "source"
    expected_identity = (
        rehearsal_receipt_value["extractedTreeSha256"],
        rehearsal_receipt_value["extractedFileCount"],
        rehearsal_receipt_value["extractedBytes"],
    )
    if tree_identity(source) != expected_identity:
        raise UpdateError("release reactivation source differs from the verified rehearsal")
    toolchain, checks = candidate_syntax_checks(source, manifest)
    if toolchain != rehearsal_receipt_value["toolchain"] or checks != rehearsal_receipt_value["checks"]:
        raise UpdateError("release reactivation source differs from the rehearsed compatibility result")
    activation_intent_value = activation_intent(
        envelope, envelope_bytes, stage_receipt_bytes, rehearsal_receipt_bytes,
        rehearsal_receipt_value, args.identity, current, host, args.candidate_id,
    )
    activation = args.staging_root / "activations" / args.candidate_id
    _activation_claim, activation_claim_bytes = validate_activation_claim(
        activation, activation_intent_value, args.activation_hash,
    )
    activated, activation_result_bytes = validate_activated_result(
        activation, envelope, current, args.activation_hash, activation_claim_bytes,
    )
    rollback_intent_value = rollback_intent(
        args.candidate_id, envelope, current, args.activation_hash,
        activation_result_bytes, activated["rollbackMarkerSha256"],
    )
    rollback_hash = sha256(canonical_json(rollback_intent_value))
    _rollback_claim, rollback_claim_bytes = validate_rollback_claim(
        activation, rollback_intent_value, rollback_hash,
    )
    _rollback_result, rollback_result_bytes = validate_successful_rollback_result(
        activation, envelope, current, args.activation_hash, rollback_hash, rollback_claim_bytes,
    )
    if require_restored:
        if active_version(args.active_version_file) != current:
            raise UpdateError("release reactivation requires the restored Pixel version to remain active")
        if args.rollback_marker.exists() or args.rollback_marker.is_symlink():
            raise UpdateError("release reactivation requires the original rollback marker to remain consumed")
    (
        deployment_record_bytes, deployment_record_sha256,
        retained_deployment_inputs_sha256, retained_install_manifest_sha256,
    ) = reactivation_deployment_record(
        args.staging_root, args.candidate_id, envelope["version"],
    )
    intent = reactivation_intent(
        args.candidate_id, envelope, envelope_bytes, current, host, args.identity,
        stage_receipt_bytes, rehearsal_receipt_bytes, rehearsal_receipt_value,
        args.activation_hash, activation_claim_bytes, activation_result_bytes,
        rollback_claim_bytes, rollback_result_bytes,
        deployment_record_sha256,
        retained_deployment_inputs_sha256, retained_install_manifest_sha256,
    )
    return {
        "envelope": envelope,
        "current": current,
        "manifest": manifest,
        "source": source,
        "sourceIdentity": expected_identity,
        "toolchain": toolchain,
        "checks": checks,
        "deploymentRecordBytes": deployment_record_bytes,
        "intent": intent,
    }


def reactivation_preview_value(intent: dict[str, Any]) -> dict[str, Any]:
    return {
        **intent,
        "status": "ready",
        "reactivationHash": sha256(canonical_json(intent)),
        "confirmationRequired": "repeat-the-full-reactivation-hash-with-confirm",
        "reactivationAuthority": "terminal-exact-confirmation-only",
        "candidateCodeExecuted": False,
        "activeDeploymentChanged": False,
        "networkUsed": False,
        "boundary": REACTIVATION_BOUNDARY,
    }


def reactivation_preview(args: argparse.Namespace) -> dict[str, Any]:
    if sys.platform != "linux":
        raise UpdateError("release reactivation is supported only on qualified Linux hosts")
    if not isinstance(args.activation_hash, str) or HASH.fullmatch(args.activation_hash) is None:
        raise UpdateError("release activation hash is invalid")
    if (
        not args.staging_root.is_absolute() or not args.active_version_file.is_absolute()
        or not args.rollback_marker.is_absolute()
    ):
        raise UpdateError("release reactivation paths must be absolute")
    with exclusive_stage_lock(args.staging_root):
        context = reactivation_context(args, require_restored=True)
        _attempt, intent = next_reactivation_attempt(
            context, args.staging_root, args.candidate_id, args.activation_hash,
        )
        return reactivation_preview_value(intent)


def reactivation_claim_value(
    intent: dict[str, Any], reactivation_hash: str, claimed_at: str,
) -> dict[str, Any]:
    return {
        **intent,
        "status": "claimed",
        "reactivationHash": reactivation_hash,
        "claimedAt": claimed_at,
        "claim": "single-use-atomic-no-replace",
        "candidateCodeCopied": True,
        "candidateCodeExecuted": False,
        "activeDeploymentChanged": False,
        "networkUsed": False,
        "boundary": REACTIVATION_BOUNDARY,
    }


def recover_incomplete_reactivations(reactivations: Path) -> None:
    try:
        entries = list(os.scandir(reactivations))
    except OSError as exc:
        raise UpdateError("release reactivation directory cannot be enumerated safely") from exc
    pattern = r"\.pixel-[0-9]{1,6}(?:\.[0-9]{1,6}){2}-[0-9a-f]{64}\.reactivation-[0-9a-f]{16}"
    for entry in entries:
        if not entry.name.startswith("."):
            continue
        if not entry.is_dir(follow_symlinks=False) or re.fullmatch(pattern, entry.name) is None:
            raise UpdateError("release reactivation directory contains an unrecognized entry")
        remove_private_tree(Path(entry.path))


def recover_incomplete_reactivation_attempts(attempts: Path) -> None:
    try:
        entries = list(os.scandir(attempts))
    except OSError as exc:
        raise UpdateError("release reactivation attempt directory cannot be enumerated safely") from exc
    pattern = r"\.[0-9a-f]{64}\.reactivation-[0-9a-f]{16}"
    for entry in entries:
        if not entry.name.startswith("."):
            continue
        if not entry.is_dir(follow_symlinks=False) or re.fullmatch(pattern, entry.name) is None:
            raise UpdateError("release reactivation attempt directory contains an unrecognized entry")
        remove_private_tree(Path(entry.path))


def claim_reactivation(args: argparse.Namespace) -> dict[str, Any]:
    if not args.confirm:
        raise UpdateError("release reactivation requires --confirm")
    if not isinstance(args.reactivation_hash, str) or HASH.fullmatch(args.reactivation_hash) is None:
        raise UpdateError("release reactivation hash is invalid")
    preview = reactivation_preview(args)
    if not hmac.compare_digest(preview["reactivationHash"], args.reactivation_hash):
        raise UpdateError("release reactivation hash differs from the current verified preview")
    with exclusive_stage_lock(args.staging_root):
        context = reactivation_context(args, require_restored=True)
        final, intent = next_reactivation_attempt(
            context, args.staging_root, args.candidate_id, args.activation_hash,
        )
        expected_hash = sha256(canonical_json(intent))
        if not hmac.compare_digest(expected_hash, args.reactivation_hash):
            raise UpdateError("release reactivation hash differs from the locked verified preview")
        reactivations = args.staging_root / "reactivations"
        ensure_private_directory(reactivations, "release reactivation directory", create=True)
        fsync_directory(args.staging_root)
        recover_incomplete_reactivations(reactivations)
        if final.exists() or final.is_symlink():
            raise UpdateError("release reactivation has already been claimed")
        if final.parent == reactivations:
            entries = list(os.scandir(reactivations))
            if any(
                not entry.is_dir(follow_symlinks=False)
                or re.fullmatch(r"pixel-[0-9]{1,6}(?:\.[0-9]{1,6}){2}-[0-9a-f]{64}", entry.name) is None
                for entry in entries
            ):
                raise UpdateError("release reactivation directory contains an unrecognized entry")
            if len(entries) >= MAX_STAGED_CANDIDATES:
                raise UpdateError("release reactivation retention limit is reached")
            temporary = reactivations / f".{args.candidate_id}.reactivation-{secrets.token_hex(8)}"
        else:
            attempts_root = args.staging_root / "reactivation-attempts"
            ensure_private_directory(attempts_root, "release reactivation attempt root", create=True)
            if any(
                not entry.is_dir(follow_symlinks=False)
                or re.fullmatch(r"pixel-[0-9]{1,6}(?:\.[0-9]{1,6}){2}-[0-9a-f]{64}", entry.name) is None
                for entry in os.scandir(attempts_root)
            ):
                raise UpdateError("release reactivation attempt root contains an unrecognized entry")
            candidate_attempts = reactivation_attempt_store(args.staging_root, args.candidate_id)
            ensure_private_directory(
                candidate_attempts, "release reactivation attempt directory", create=True,
            )
            recover_incomplete_reactivation_attempts(candidate_attempts)
            existing_attempts = list(os.scandir(candidate_attempts))
            if any(
                not entry.is_dir(follow_symlinks=False) or HASH.fullmatch(entry.name) is None
                for entry in existing_attempts
            ):
                raise UpdateError("release reactivation attempt directory contains an unrecognized entry")
            if len(existing_attempts) >= MAX_REACTIVATION_ATTEMPTS - 1:
                raise UpdateError("release reactivation attempt retention limit is reached")
            temporary = candidate_attempts / f".{expected_hash}.reactivation-{secrets.token_hex(8)}"
        os.mkdir(temporary, 0o700)
        copied_source = temporary / "source"
        os.mkdir(copied_source, 0o700)
        try:
            copied_identity = copy_private_tree(context["source"], copied_source)
            if copied_identity != context["sourceIdentity"]:
                raise UpdateError("private reactivation copy differs from the verified rehearsal")
            copied_toolchain, copied_checks = candidate_syntax_checks(copied_source, context["manifest"])
            if copied_toolchain != context["toolchain"] or copied_checks != context["checks"]:
                raise UpdateError("private reactivation copy differs from the rehearsed compatibility result")
            generated = copied_source / ".generated"
            if generated.exists() or generated.is_symlink():
                raise UpdateError("private reactivation copy already contains generated deployment state")
            os.mkdir(generated, 0o700)
            write_private(generated / "deployment.json", context["deploymentRecordBytes"])
            fsync_directory(generated)
            claimed_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            claim = reactivation_claim_value(intent, expected_hash, claimed_at)
            write_private(temporary / "REACTIVATION.json", canonical_json(claim))
            fsync_directory(temporary)
            rename_directory_noreplace(temporary, final)
            fsync_directory(final.parent)
        except BaseException:
            remove_private_tree(temporary)
            raise
    return claim


def validate_reactivation_claim(
    reactivation: Path, intent: dict[str, Any], reactivation_hash: str,
) -> tuple[dict[str, Any], bytes]:
    ensure_private_directory(reactivation, "release reactivation")
    observed = {entry.name for entry in os.scandir(reactivation)}
    allowed_sets = (
        {"source", "REACTIVATION.json"},
        {"source", "REACTIVATION.json", "REACTIVATION-RESULT.json"},
        {"source", "REACTIVATION.json", "REACTIVATION-RESULT.json", "ROLLBACK.json"},
        {"source", "REACTIVATION.json", "REACTIVATION-RESULT.json", "ROLLBACK.json", "ROLLBACK-RESULT.json"},
    )
    receipt_set = observed - {"RETRY-AUTHORIZATION.json", "LIVE-MUTATION-STARTED"}
    if receipt_set not in allowed_sets or (
        "RETRY-AUTHORIZATION.json" in observed
        and "REACTIVATION-RESULT.json" not in observed
    ):
        raise UpdateError("release reactivation file set is invalid")
    ensure_private_directory(reactivation / "source", "release reactivation execution source")
    claim_path = reactivation / "REACTIVATION.json"
    claim_bytes = read_regular(claim_path, MAX_STAGE_RECEIPT, "release reactivation claim")
    claim_info = claim_path.lstat()
    if os.name != "nt" and (claim_info.st_uid != os.geteuid() or stat.S_IMODE(claim_info.st_mode) & 0o077):
        raise UpdateError("release reactivation claim permissions are unsafe")
    claim = parse_json(claim_bytes, "release reactivation claim")
    claimed_at = claim.get("claimedAt")
    if not isinstance(claimed_at, str) or re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", claimed_at,
    ) is None:
        raise UpdateError("release reactivation claim timestamp is invalid")
    expected_hash = sha256(canonical_json(intent))
    if not hmac.compare_digest(expected_hash, reactivation_hash):
        raise UpdateError("release reactivation hash differs from the verified rollback journey")
    if claim != reactivation_claim_value(intent, expected_hash, claimed_at):
        raise UpdateError("release reactivation claim differs from the verified rollback journey")
    return claim, claim_bytes


def validate_reactivation_live_mutation_marker(path: Path) -> bytes:
    marker = read_regular(path, 128, "release reactivation live-mutation marker")
    info = path.lstat()
    if os.name != "nt" and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077):
        raise UpdateError("release reactivation live-mutation marker permissions are unsafe")
    if marker != REACTIVATION_LIVE_MUTATION_MARKER:
        raise UpdateError("release reactivation live-mutation marker is invalid")
    return marker


def write_reactivation_result_receipt(
    reactivation: Path, envelope: dict[str, Any], current: str, reactivation_hash: str,
    claim_bytes: bytes, outcome: str, phase: str, active_version_file: Path,
    rollback_marker: Path | None, candidate_id: str | None = None,
    live_mutation_marker: Path | None = None,
) -> dict[str, Any]:
    result_path = reactivation / "REACTIVATION-RESULT.json"
    if result_path.exists() or result_path.is_symlink():
        raise UpdateError("release reactivation result is already recorded")
    observed_active = active_version(active_version_file)
    if observed_active not in {current, envelope["version"]}:
        raise UpdateError("observed active version is outside the claimed reactivation transition")
    active_changed = observed_active == envelope["version"]
    marker_hash = None
    if active_changed and rollback_marker is not None and (rollback_marker.exists() or rollback_marker.is_symlink()):
        marker_hash = sha256(read_regular(rollback_marker, 4096, "reactivation rollback marker"))
    rollback_available = active_changed and marker_hash is not None
    live_mutation_marker_bytes = None
    if live_mutation_marker is not None and (
        live_mutation_marker.exists() or live_mutation_marker.is_symlink()
    ):
        live_mutation_marker_bytes = validate_reactivation_live_mutation_marker(
            live_mutation_marker,
        )
    live_mutation_started = live_mutation_marker_bytes is not None
    if outcome == "reactivated" and (
        not rollback_available or phase != "record" or not live_mutation_started
    ):
        raise UpdateError("successful reactivation did not install the release with a new rollback marker")
    completed_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    result = {
        "schemaVersion": 1,
        "operation": "pixel-release-reactivation-result",
        "status": outcome,
        "candidateId": candidate_id or reactivation.name,
        "product": "Pixel",
        "version": envelope["version"],
        "previousVersion": current,
        "activeVersion": observed_active,
        "reactivationHash": reactivation_hash,
        "reactivationClaimSha256": sha256(claim_bytes),
        "executionPhase": phase,
        "completedAt": completed_at,
        "candidateCodeExecuted": True,
        "privateConfigurationRead": True,
        "activeDeploymentChanged": active_changed,
        "liveMutationStarted": live_mutation_started,
        "liveMutationMarkerSha256": (
            sha256(live_mutation_marker_bytes) if live_mutation_marker_bytes is not None else None
        ),
        "networkMayHaveBeenUsed": True,
        "rollbackAvailable": rollback_available,
        "rollbackMarkerSha256": marker_hash,
        "boundary": REACTIVATION_RESULT_BOUNDARY,
    }
    write_private(result_path, canonical_json(result))
    fsync_directory(reactivation)
    return result


def record_reactivation_result(args: argparse.Namespace) -> dict[str, Any]:
    if not args.confirm:
        raise UpdateError("release reactivation result requires --confirm")
    if args.outcome not in {"reactivated", "failed"}:
        raise UpdateError("release reactivation outcome is invalid")
    if args.phase not in {"configure", "bootstrap", "plan", "apply", "verify", "record"}:
        raise UpdateError("release reactivation phase is invalid")
    if not isinstance(args.reactivation_hash, str) or HASH.fullmatch(args.reactivation_hash) is None:
        raise UpdateError("release reactivation hash is invalid")
    if sys.platform != "linux":
        raise UpdateError("release reactivation is supported only on qualified Linux hosts")
    if not args.staging_root.is_absolute() or not args.active_version_file.is_absolute():
        raise UpdateError("release reactivation paths must be absolute")
    with exclusive_stage_lock(args.staging_root):
        context = reactivation_context(args, require_restored=False)
        attempt = find_reactivation_attempt(
            context, args.staging_root, args.candidate_id,
            args.activation_hash, args.reactivation_hash,
        )
        reactivation = attempt["path"]
        claim_bytes = attempt["claimBytes"]
        return write_reactivation_result_receipt(
            reactivation, context["envelope"], context["current"], args.reactivation_hash,
            claim_bytes, args.outcome, args.phase, args.active_version_file, args.rollback_marker,
            args.candidate_id, reactivation / "LIVE-MUTATION-STARTED",
        )


def validate_reactivation_result(
    reactivation: Path, envelope: dict[str, Any], current: str,
    reactivation_hash: str, claim_bytes: bytes, candidate_id: str | None = None,
) -> tuple[dict[str, Any], bytes, str]:
    result_path = reactivation / "REACTIVATION-RESULT.json"
    result_bytes = read_regular(result_path, MAX_STAGE_RECEIPT, "release reactivation result")
    result_info = result_path.lstat()
    if os.name != "nt" and (result_info.st_uid != os.geteuid() or stat.S_IMODE(result_info.st_mode) & 0o077):
        raise UpdateError("release reactivation result permissions are unsafe")
    result = parse_json(result_bytes, "release reactivation result")
    status_value = result.get("status")
    if status_value not in {"reactivated", "failed"}:
        raise UpdateError("release reactivation result status is invalid")
    completed_at = result.get("completedAt")
    phase = result.get("executionPhase")
    active = result.get("activeVersion")
    marker_hash = result.get("rollbackMarkerSha256")
    live_mutation_started = result.get("liveMutationStarted")
    live_mutation_marker_hash = result.get("liveMutationMarkerSha256")
    if (
        not isinstance(completed_at, str) or re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", completed_at,
        ) is None
        or phase not in {"configure", "bootstrap", "plan", "apply", "verify", "record"}
        or active not in {current, envelope["version"]}
        or marker_hash is not None and (not isinstance(marker_hash, str) or HASH.fullmatch(marker_hash) is None)
        or not isinstance(live_mutation_started, bool)
        or live_mutation_marker_hash is not None and (
            not isinstance(live_mutation_marker_hash, str)
            or HASH.fullmatch(live_mutation_marker_hash) is None
        )
    ):
        raise UpdateError("release reactivation result is invalid")
    live_marker_path = reactivation / "LIVE-MUTATION-STARTED"
    live_marker_present = live_marker_path.exists() or live_marker_path.is_symlink()
    if live_marker_present != live_mutation_started:
        raise UpdateError("release reactivation result differs from its live-mutation marker")
    if live_marker_present:
        live_marker = validate_reactivation_live_mutation_marker(live_marker_path)
        if not hmac.compare_digest(sha256(live_marker), live_mutation_marker_hash or ""):
            raise UpdateError("release reactivation result differs from its live-mutation marker")
    elif live_mutation_marker_hash is not None:
        raise UpdateError("release reactivation result differs from its live-mutation marker")
    active_changed = active == envelope["version"]
    rollback_available = active_changed and marker_hash is not None
    if status_value == "reactivated" and (
        phase != "record" or not rollback_available or not live_mutation_started
    ):
        raise UpdateError("release reactivation result is not an exact successful receipt")
    expected = {
        "schemaVersion": 1, "operation": "pixel-release-reactivation-result",
        "status": status_value, "candidateId": candidate_id or reactivation.name, "product": "Pixel",
        "version": envelope["version"], "previousVersion": current, "activeVersion": active,
        "reactivationHash": reactivation_hash,
        "reactivationClaimSha256": sha256(claim_bytes), "executionPhase": phase,
        "completedAt": completed_at, "candidateCodeExecuted": True,
        "privateConfigurationRead": True, "activeDeploymentChanged": active_changed,
        "liveMutationStarted": live_mutation_started,
        "liveMutationMarkerSha256": live_mutation_marker_hash,
        "networkMayHaveBeenUsed": True, "rollbackAvailable": rollback_available,
        "rollbackMarkerSha256": marker_hash, "boundary": REACTIVATION_RESULT_BOUNDARY,
    }
    if result != expected:
        raise UpdateError("release reactivation result differs from its exact claim")
    return result, result_bytes, status_value


def reactivation_attempt_store(staging_root: Path, candidate_id: str) -> Path:
    return staging_root / "reactivation-attempts" / candidate_id


def failed_reactivation_is_retryable(
    attempt: Path, result: dict[str, Any], current: str,
) -> bool:
    return (
        result["status"] == "failed"
        and result["activeVersion"] == current
        and result["activeDeploymentChanged"] is False
        and result["liveMutationStarted"] is False
        and result["liveMutationMarkerSha256"] is None
        and result["rollbackAvailable"] is False
        and result["rollbackMarkerSha256"] is None
        and not (attempt / "ROLLBACK.json").exists()
        and not (attempt / "ROLLBACK.json").is_symlink()
        and not (attempt / "ROLLBACK-RESULT.json").exists()
        and not (attempt / "ROLLBACK-RESULT.json").is_symlink()
    )


def reactivation_retry_intent(
    base_intent: dict[str, Any], attempt_number: int, previous_hash: str,
    previous_claim_bytes: bytes, previous_result_bytes: bytes,
    authorization_bytes: bytes,
) -> dict[str, Any]:
    return {
        **base_intent,
        "attemptNumber": attempt_number,
        "previousReactivationHash": previous_hash,
        "previousReactivationClaimSha256": sha256(previous_claim_bytes),
        "previousReactivationResultSha256": sha256(previous_result_bytes),
        "retryAuthorizationSha256": sha256(authorization_bytes),
        "retry": "explicit-recovery-authorized-append-only",
    }


def reactivation_retry_authorization_value(
    candidate_id: str, envelope: dict[str, Any], current: str,
    reactivation_hash: str, claim_bytes: bytes, result_bytes: bytes,
    result: dict[str, Any], recovery_hash: str, authorized_at: str,
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "operation": "pixel-release-reactivation-retry-authorization",
        "status": "authorized",
        "candidateId": candidate_id,
        "product": "Pixel",
        "version": envelope["version"],
        "restoreVersion": current,
        "failedReactivationHash": reactivation_hash,
        "failedReactivationClaimSha256": sha256(claim_bytes),
        "failedReactivationResultSha256": sha256(result_bytes),
        "failedExecutionPhase": result["executionPhase"],
        "activeVersion": current,
        "activeDeploymentChanged": False,
        "liveMutationStarted": False,
        "liveMutationMarkerPresent": False,
        "rollbackMarkerPresent": False,
        "rollbackArtifactsPresent": False,
        "recoveryHash": recovery_hash,
        "authorizedAt": authorized_at,
        "candidateCodeWillExecute": False,
        "networkWillBeUsed": False,
        "boundary": REACTIVATION_RETRY_BOUNDARY,
    }


def validate_reactivation_retry_authorization(
    attempt: Path, candidate_id: str, envelope: dict[str, Any], current: str,
    reactivation_hash: str, claim_bytes: bytes, result_bytes: bytes,
    result: dict[str, Any], activation_hash: str,
) -> tuple[dict[str, Any], bytes]:
    path = attempt / "RETRY-AUTHORIZATION.json"
    authorization_bytes = read_regular(
        path, MAX_STAGE_RECEIPT, "release reactivation retry authorization",
    )
    info = path.lstat()
    if os.name != "nt" and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077):
        raise UpdateError("release reactivation retry authorization permissions are unsafe")
    authorization = parse_json(authorization_bytes, "release reactivation retry authorization")
    authorized_at = authorization.get("authorizedAt")
    recovery_hash = authorization.get("recoveryHash")
    if (
        not isinstance(authorized_at, str)
        or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", authorized_at) is None
        or not isinstance(recovery_hash, str) or HASH.fullmatch(recovery_hash) is None
    ):
        raise UpdateError("release reactivation retry authorization is invalid")
    recovery_intent = reactivation_recovery_intent(
        candidate_id, envelope, current, current, activation_hash,
        reactivation_hash, "authorize-reactivation-retry", claim_bytes,
        result_bytes, None, None,
    )
    if not hmac.compare_digest(sha256(canonical_json(recovery_intent)), recovery_hash):
        raise UpdateError("release reactivation retry authorization differs from the failed attempt")
    expected = reactivation_retry_authorization_value(
        candidate_id, envelope, current, reactivation_hash, claim_bytes,
        result_bytes, result, recovery_hash, authorized_at,
    )
    if authorization != expected:
        raise UpdateError("release reactivation retry authorization differs from the failed attempt")
    return authorization, authorization_bytes


def reactivation_attempt_chain(
    context: dict[str, Any], staging_root: Path, candidate_id: str,
    activation_hash: str,
) -> list[dict[str, Any]]:
    root = staging_root / "reactivations" / candidate_id
    base_hash = sha256(canonical_json(context["intent"]))
    _claim, claim_bytes = validate_reactivation_claim(root, context["intent"], base_hash)
    chain: list[dict[str, Any]] = [{
        "number": 1, "path": root, "intent": context["intent"],
        "hash": base_hash, "claimBytes": claim_bytes,
        "result": None, "resultBytes": None,
        "authorization": None, "authorizationBytes": None,
    }]
    store = reactivation_attempt_store(staging_root, candidate_id)
    remaining: set[str] = set()
    if store.exists() or store.is_symlink():
        ensure_private_directory(store, "release reactivation attempt directory")
        with os.scandir(store) as iterator:
            for entry in iterator:
                if entry.name.startswith("."):
                    if (
                        not entry.is_dir(follow_symlinks=False)
                        or re.fullmatch(r"\.[0-9a-f]{64}\.reactivation-[0-9a-f]{16}", entry.name) is None
                    ):
                        raise UpdateError("release reactivation attempt directory contains an unrecognized entry")
                    continue
                if (
                    not entry.is_dir(follow_symlinks=False)
                    or HASH.fullmatch(entry.name) is None
                ):
                    raise UpdateError("release reactivation attempt directory contains an unrecognized entry")
                remaining.add(entry.name)
        if len(remaining) >= MAX_REACTIVATION_ATTEMPTS:
            raise UpdateError("release reactivation attempt retention limit is reached")
    while True:
        current_attempt = chain[-1]
        live_marker_path = current_attempt["path"] / "LIVE-MUTATION-STARTED"
        if live_marker_path.exists() or live_marker_path.is_symlink():
            validate_reactivation_live_mutation_marker(live_marker_path)
        result_path = current_attempt["path"] / "REACTIVATION-RESULT.json"
        if result_path.exists() or result_path.is_symlink():
            result, result_bytes, _status = validate_reactivation_result(
                current_attempt["path"], context["envelope"], context["current"],
                current_attempt["hash"], current_attempt["claimBytes"], candidate_id,
            )
            current_attempt["result"] = result
            current_attempt["resultBytes"] = result_bytes
        authorization_path = current_attempt["path"] / "RETRY-AUTHORIZATION.json"
        if authorization_path.exists() or authorization_path.is_symlink():
            if current_attempt["result"] is None or not failed_reactivation_is_retryable(
                current_attempt["path"], current_attempt["result"], context["current"],
            ):
                raise UpdateError("release reactivation retry authorization is not bound to a safe terminal failure")
            authorization, authorization_bytes = validate_reactivation_retry_authorization(
                current_attempt["path"], candidate_id, context["envelope"], context["current"],
                current_attempt["hash"], current_attempt["claimBytes"],
                current_attempt["resultBytes"], current_attempt["result"], activation_hash,
            )
            current_attempt["authorization"] = authorization
            current_attempt["authorizationBytes"] = authorization_bytes
        if not remaining:
            break
        if current_attempt["authorizationBytes"] is None:
            raise UpdateError("release reactivation attempt chain contains an unauthorized successor")
        next_intent = reactivation_retry_intent(
            context["intent"], current_attempt["number"] + 1, current_attempt["hash"],
            current_attempt["claimBytes"], current_attempt["resultBytes"],
            current_attempt["authorizationBytes"],
        )
        next_hash = sha256(canonical_json(next_intent))
        if next_hash not in remaining:
            raise UpdateError("release reactivation attempt chain is forked or malformed")
        next_path = store / next_hash
        _next_claim, next_claim_bytes = validate_reactivation_claim(
            next_path, next_intent, next_hash,
        )
        remaining.remove(next_hash)
        chain.append({
            "number": current_attempt["number"] + 1, "path": next_path,
            "intent": next_intent, "hash": next_hash, "claimBytes": next_claim_bytes,
            "result": None, "resultBytes": None,
            "authorization": None, "authorizationBytes": None,
        })
    return chain


def next_reactivation_attempt(
    context: dict[str, Any], staging_root: Path, candidate_id: str,
    activation_hash: str,
) -> tuple[Path, dict[str, Any]]:
    root = staging_root / "reactivations" / candidate_id
    if not root.exists() and not root.is_symlink():
        orphan_attempts = reactivation_attempt_store(staging_root, candidate_id)
        if orphan_attempts.exists() or orphan_attempts.is_symlink():
            raise UpdateError("release reactivation attempt history exists without its initial claim")
        return root, context["intent"]
    chain = reactivation_attempt_chain(context, staging_root, candidate_id, activation_hash)
    latest = chain[-1]
    if latest["authorizationBytes"] is None:
        raise UpdateError("release reactivation has already been claimed")
    if latest["number"] >= MAX_REACTIVATION_ATTEMPTS:
        raise UpdateError("release reactivation attempt retention limit is reached")
    intent = reactivation_retry_intent(
        context["intent"], latest["number"] + 1, latest["hash"],
        latest["claimBytes"], latest["resultBytes"], latest["authorizationBytes"],
    )
    reactivation_hash = sha256(canonical_json(intent))
    return reactivation_attempt_store(staging_root, candidate_id) / reactivation_hash, intent


def find_reactivation_attempt(
    context: dict[str, Any], staging_root: Path, candidate_id: str,
    activation_hash: str, reactivation_hash: str,
) -> dict[str, Any]:
    for attempt in reactivation_attempt_chain(context, staging_root, candidate_id, activation_hash):
        if hmac.compare_digest(attempt["hash"], reactivation_hash):
            return attempt
    raise UpdateError("release reactivation hash does not identify a claimed attempt")


def reactivation_rollback_intent(
    candidate_id: str, envelope: dict[str, Any], current: str,
    activation_hash: str, reactivation_hash: str,
    reactivation_result_bytes: bytes, marker_hash: str,
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "operation": "pixel-release-reactivation-rollback",
        "candidateId": candidate_id,
        "product": "Pixel",
        "version": envelope["version"],
        "restoreVersion": current,
        "originalActivationHash": activation_hash,
        "reactivationHash": reactivation_hash,
        "reactivationResultSha256": sha256(reactivation_result_bytes),
        "rollbackMarkerSha256": marker_hash,
        "trustedControllerOnly": True,
        "singleUse": True,
    }


def reactivation_rollback_preview_value(intent: dict[str, Any]) -> dict[str, Any]:
    return {
        **intent,
        "status": "ready",
        "rollbackHash": sha256(canonical_json(intent)),
        "confirmationRequired": "repeat-the-full-reactivation-rollback-hash-with-confirm",
        "activeDeploymentChanged": False,
        "boundary": REACTIVATION_ROLLBACK_BOUNDARY,
    }


def reactivation_rollback_claim_value(
    intent: dict[str, Any], rollback_hash: str, claimed_at: str,
) -> dict[str, Any]:
    return {
        **intent,
        "status": "claimed",
        "rollbackHash": rollback_hash,
        "claimedAt": claimed_at,
        "claim": "single-use-exclusive-create",
        "activeDeploymentChanged": False,
        "boundary": REACTIVATION_ROLLBACK_BOUNDARY,
    }


def reactivation_rollback_context(
    args: argparse.Namespace,
) -> tuple[Path, dict[str, Any], str, dict[str, Any], bytes, bytes]:
    context = reactivation_context(args, require_restored=False)
    attempt = find_reactivation_attempt(
        context, args.staging_root, args.candidate_id,
        args.activation_hash, args.reactivation_hash,
    )
    reactivation = attempt["path"]
    claim_bytes = attempt["claimBytes"]
    result, result_bytes, status_value = validate_reactivation_result(
        reactivation, context["envelope"], context["current"],
        args.reactivation_hash, claim_bytes, args.candidate_id,
    )
    if status_value != "reactivated":
        raise UpdateError("release reactivation rollback requires an exact successful reactivation receipt")
    return (
        reactivation, context["envelope"], context["current"],
        result, result_bytes, claim_bytes,
    )


def reactivation_rollback_preview(args: argparse.Namespace) -> dict[str, Any]:
    if sys.platform != "linux":
        raise UpdateError("release reactivation rollback is supported only on qualified Linux hosts")
    if not isinstance(args.activation_hash, str) or HASH.fullmatch(args.activation_hash) is None:
        raise UpdateError("release activation hash is invalid")
    if not isinstance(args.reactivation_hash, str) or HASH.fullmatch(args.reactivation_hash) is None:
        raise UpdateError("release reactivation hash is invalid")
    if (
        not args.staging_root.is_absolute() or not args.active_version_file.is_absolute()
        or not args.rollback_marker.is_absolute()
    ):
        raise UpdateError("release reactivation rollback paths must be absolute")
    with exclusive_stage_lock(args.staging_root):
        reactivation, envelope, current, result, result_bytes, _claim = reactivation_rollback_context(args)
        if (reactivation / "ROLLBACK.json").exists() or (reactivation / "ROLLBACK.json").is_symlink():
            raise UpdateError("release reactivation rollback has already been claimed")
        if active_version(args.active_version_file) != envelope["version"]:
            raise UpdateError("the reactivated release is no longer the active Pixel version")
        marker = read_regular(args.rollback_marker, 4096, "reactivation rollback marker")
        marker_hash = sha256(marker)
        if not hmac.compare_digest(marker_hash, result["rollbackMarkerSha256"]):
            raise UpdateError("rollback marker differs from the reactivated update receipt")
        return reactivation_rollback_preview_value(reactivation_rollback_intent(
            args.candidate_id, envelope, current, args.activation_hash,
            args.reactivation_hash, result_bytes, marker_hash,
        ))


def claim_reactivation_rollback(args: argparse.Namespace) -> dict[str, Any]:
    if not args.confirm:
        raise UpdateError("release reactivation rollback requires --confirm")
    if not isinstance(args.rollback_hash, str) or HASH.fullmatch(args.rollback_hash) is None:
        raise UpdateError("release reactivation rollback hash is invalid")
    preview = reactivation_rollback_preview(args)
    if not hmac.compare_digest(preview["rollbackHash"], args.rollback_hash):
        raise UpdateError("release reactivation rollback hash differs from the current verified preview")
    with exclusive_stage_lock(args.staging_root):
        reactivation, envelope, current, result, result_bytes, _claim = reactivation_rollback_context(args)
        claim_path = reactivation / "ROLLBACK.json"
        if claim_path.exists() or claim_path.is_symlink():
            raise UpdateError("release reactivation rollback has already been claimed")
        if active_version(args.active_version_file) != envelope["version"]:
            raise UpdateError("the reactivated release is no longer the active Pixel version")
        marker = read_regular(args.rollback_marker, 4096, "reactivation rollback marker")
        marker_hash = sha256(marker)
        if not hmac.compare_digest(marker_hash, result["rollbackMarkerSha256"]):
            raise UpdateError("rollback marker differs from the reactivated update receipt")
        intent = reactivation_rollback_intent(
            args.candidate_id, envelope, current, args.activation_hash,
            args.reactivation_hash, result_bytes, marker_hash,
        )
        expected_hash = sha256(canonical_json(intent))
        if not hmac.compare_digest(expected_hash, args.rollback_hash):
            raise UpdateError("release reactivation rollback hash differs from the locked preview")
        claimed_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        claim = reactivation_rollback_claim_value(intent, expected_hash, claimed_at)
        write_private(claim_path, canonical_json(claim))
        fsync_directory(reactivation)
    return claim


def validate_reactivation_rollback_claim(
    reactivation: Path, intent: dict[str, Any], rollback_hash: str,
) -> tuple[dict[str, Any], bytes]:
    claim_path = reactivation / "ROLLBACK.json"
    claim_bytes = read_regular(claim_path, MAX_STAGE_RECEIPT, "release reactivation rollback claim")
    claim_info = claim_path.lstat()
    if os.name != "nt" and (claim_info.st_uid != os.geteuid() or stat.S_IMODE(claim_info.st_mode) & 0o077):
        raise UpdateError("release reactivation rollback claim permissions are unsafe")
    claim = parse_json(claim_bytes, "release reactivation rollback claim")
    claimed_at = claim.get("claimedAt")
    if not isinstance(claimed_at, str) or re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", claimed_at,
    ) is None:
        raise UpdateError("release reactivation rollback claim timestamp is invalid")
    expected_hash = sha256(canonical_json(intent))
    if not hmac.compare_digest(expected_hash, rollback_hash):
        raise UpdateError("release reactivation rollback hash differs from the exact claim")
    if claim != reactivation_rollback_claim_value(intent, expected_hash, claimed_at):
        raise UpdateError("release reactivation rollback claim differs from the reactivated update")
    return claim, claim_bytes


def write_reactivation_rollback_result_receipt(
    reactivation: Path, envelope: dict[str, Any], current: str,
    activation_hash: str, reactivation_hash: str, rollback_hash: str,
    rollback_claim_bytes: bytes, outcome: str, phase: str,
    active_version_file: Path, rollback_marker: Path,
) -> dict[str, Any]:
    result_path = reactivation / "ROLLBACK-RESULT.json"
    if result_path.exists() or result_path.is_symlink():
        raise UpdateError("release reactivation rollback result is already recorded")
    observed_active = active_version(active_version_file)
    if observed_active not in {current, envelope["version"]}:
        raise UpdateError("observed active version is outside the claimed reactivation rollback transition")
    marker_present = rollback_marker.exists() or rollback_marker.is_symlink()
    restored = observed_active == current
    if outcome == "rolled-back" and (not restored or marker_present or phase != "record"):
        raise UpdateError("successful reactivation rollback did not consume the marker and restore the preceding release")
    completed_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    result = {
        "schemaVersion": 1,
        "operation": "pixel-release-reactivation-rollback-result",
        "status": outcome,
        "candidateId": reactivation.name,
        "product": "Pixel",
        "version": envelope["version"],
        "restoredVersion": current if restored else None,
        "activeVersion": observed_active,
        "originalActivationHash": activation_hash,
        "reactivationHash": reactivation_hash,
        "rollbackHash": rollback_hash,
        "rollbackClaimSha256": sha256(rollback_claim_bytes),
        "executionPhase": phase,
        "completedAt": completed_at,
        "trustedControllerExecuted": True,
        "activeDeploymentRestored": restored,
        "rollbackMarkerConsumed": not marker_present,
        "recoveryRequired": outcome == "failed",
        "boundary": REACTIVATION_ROLLBACK_RESULT_BOUNDARY,
    }
    write_private(result_path, canonical_json(result))
    fsync_directory(reactivation)
    return result


def record_reactivation_rollback(args: argparse.Namespace) -> dict[str, Any]:
    if not args.confirm:
        raise UpdateError("release reactivation rollback result requires --confirm")
    if args.outcome not in {"rolled-back", "failed"}:
        raise UpdateError("release reactivation rollback outcome is invalid")
    if args.phase not in {"rollback", "record"}:
        raise UpdateError("release reactivation rollback phase is invalid")
    if not isinstance(args.rollback_hash, str) or HASH.fullmatch(args.rollback_hash) is None:
        raise UpdateError("release reactivation rollback hash is invalid")
    if sys.platform != "linux":
        raise UpdateError("release reactivation rollback is supported only on qualified Linux hosts")
    if (
        not args.staging_root.is_absolute() or not args.active_version_file.is_absolute()
        or not args.rollback_marker.is_absolute()
    ):
        raise UpdateError("release reactivation rollback paths must be absolute")
    with exclusive_stage_lock(args.staging_root):
        reactivation, envelope, current, result, result_bytes, _claim = reactivation_rollback_context(args)
        intent = reactivation_rollback_intent(
            args.candidate_id, envelope, current, args.activation_hash,
            args.reactivation_hash, result_bytes, result["rollbackMarkerSha256"],
        )
        _rollback_claim, rollback_claim_bytes = validate_reactivation_rollback_claim(
            reactivation, intent, args.rollback_hash,
        )
        marker_present = args.rollback_marker.exists() or args.rollback_marker.is_symlink()
        if marker_present:
            marker = read_regular(args.rollback_marker, 4096, "reactivation rollback marker")
            if not hmac.compare_digest(sha256(marker), result["rollbackMarkerSha256"]):
                raise UpdateError("rollback marker changed during trusted reactivation rollback")
        return write_reactivation_rollback_result_receipt(
            reactivation, envelope, current, args.activation_hash, args.reactivation_hash,
            args.rollback_hash, rollback_claim_bytes, args.outcome, args.phase,
            args.active_version_file, args.rollback_marker,
        )


def validate_reactivation_rollback_result(
    reactivation: Path, envelope: dict[str, Any], current: str,
    activation_hash: str, reactivation_hash: str, rollback_hash: str,
    rollback_claim_bytes: bytes,
) -> tuple[dict[str, Any], bytes, str]:
    result_path = reactivation / "ROLLBACK-RESULT.json"
    result_bytes = read_regular(result_path, MAX_STAGE_RECEIPT, "release reactivation rollback result")
    result_info = result_path.lstat()
    if os.name != "nt" and (result_info.st_uid != os.geteuid() or stat.S_IMODE(result_info.st_mode) & 0o077):
        raise UpdateError("release reactivation rollback result permissions are unsafe")
    result = parse_json(result_bytes, "release reactivation rollback result")
    status_value = result.get("status")
    if status_value not in {"rolled-back", "failed"}:
        raise UpdateError("release reactivation rollback result status is invalid")
    completed_at = result.get("completedAt")
    active = result.get("activeVersion")
    phase = result.get("executionPhase")
    marker_consumed = result.get("rollbackMarkerConsumed")
    if (
        not isinstance(completed_at, str) or re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", completed_at,
        ) is None
        or active not in {current, envelope["version"]}
        or phase not in {"rollback", "record"}
        or not isinstance(marker_consumed, bool)
    ):
        raise UpdateError("release reactivation rollback result is invalid")
    restored = active == current
    if status_value == "rolled-back" and (phase != "record" or not restored or not marker_consumed):
        raise UpdateError("release reactivation rollback result is not an exact successful receipt")
    expected = {
        "schemaVersion": 1, "operation": "pixel-release-reactivation-rollback-result",
        "status": status_value, "candidateId": reactivation.name, "product": "Pixel",
        "version": envelope["version"], "restoredVersion": current if restored else None,
        "activeVersion": active, "originalActivationHash": activation_hash,
        "reactivationHash": reactivation_hash, "rollbackHash": rollback_hash,
        "rollbackClaimSha256": sha256(rollback_claim_bytes), "executionPhase": phase,
        "completedAt": completed_at, "trustedControllerExecuted": True,
        "activeDeploymentRestored": restored, "rollbackMarkerConsumed": marker_consumed,
        "recoveryRequired": status_value == "failed",
        "boundary": REACTIVATION_ROLLBACK_RESULT_BOUNDARY,
    }
    if result != expected:
        raise UpdateError("release reactivation rollback result differs from its exact claim")
    return result, result_bytes, status_value


def reactivation_recovery_intent(
    candidate_id: str, envelope: dict[str, Any], current: str, active: str,
    activation_hash: str, reactivation_hash: str, action: str,
    reactivation_claim_bytes: bytes, reactivation_result_bytes: bytes | None,
    rollback_claim_bytes: bytes | None, marker_hash: str | None,
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "operation": "pixel-release-reactivation-recovery",
        "candidateId": candidate_id,
        "product": "Pixel",
        "version": envelope["version"],
        "restoreVersion": current,
        "activeVersion": active,
        "originalActivationHash": activation_hash,
        "reactivationHash": reactivation_hash,
        "action": action,
        "reactivationClaimSha256": sha256(reactivation_claim_bytes),
        "reactivationResultSha256": sha256(reactivation_result_bytes) if reactivation_result_bytes is not None else None,
        "rollbackClaimSha256": sha256(rollback_claim_bytes) if rollback_claim_bytes is not None else None,
        "rollbackMarkerSha256": marker_hash,
        "trustedControllerOnly": True,
        "candidateCodeWillExecute": False,
        "networkWillBeUsed": False,
    }


def reactivation_recovery_preview(args: argparse.Namespace) -> dict[str, Any]:
    if sys.platform != "linux":
        raise UpdateError("release reactivation recovery is supported only on qualified Linux hosts")
    if not isinstance(args.reactivation_hash, str) or HASH.fullmatch(args.reactivation_hash) is None:
        raise UpdateError("release reactivation hash is invalid")
    if (
        not args.staging_root.is_absolute() or not args.active_version_file.is_absolute()
        or not args.rollback_marker.is_absolute()
    ):
        raise UpdateError("release reactivation recovery paths must be absolute")
    with exclusive_stage_lock(args.staging_root):
        context = reactivation_context(args, require_restored=False)
        attempt = find_reactivation_attempt(
            context, args.staging_root, args.candidate_id,
            args.activation_hash, args.reactivation_hash,
        )
        reactivation = attempt["path"]
        claim_bytes = attempt["claimBytes"]
        envelope = context["envelope"]
        current = context["current"]
        observed_active = active_version(args.active_version_file)
        if observed_active not in {current, envelope["version"]}:
            raise UpdateError("observed active version is outside the claimed reactivation transition")
        marker_present = args.rollback_marker.exists() or args.rollback_marker.is_symlink()
        marker_hash = sha256(read_regular(args.rollback_marker, 4096, "reactivation rollback marker")) if marker_present else None
        result_path = reactivation / "REACTIVATION-RESULT.json"
        rollback_claim_path = reactivation / "ROLLBACK.json"
        rollback_result_path = reactivation / "ROLLBACK-RESULT.json"
        action: str | None = None
        state = "manual-review"
        result_bytes: bytes | None = None
        rollback_claim_bytes: bytes | None = None
        if not result_path.exists() and not result_path.is_symlink():
            if observed_active == envelope["version"] and marker_hash is not None:
                state = "reactivation-applied-result-missing"
                action = "finalize-reactivation-result"
            elif observed_active == current and not marker_present:
                state = "reactivation-claimed-before-active-change"
        else:
            result, result_bytes, status_value = validate_reactivation_result(
                reactivation, envelope, current, args.reactivation_hash, claim_bytes,
                args.candidate_id,
            )
            if status_value == "failed":
                rollback_artifacts_present = (
                    rollback_claim_path.exists() or rollback_claim_path.is_symlink()
                    or rollback_result_path.exists() or rollback_result_path.is_symlink()
                )
                state_matches = observed_active == result["activeVersion"]
                marker_matches = (
                    not result["rollbackAvailable"]
                    or marker_hash is not None and hmac.compare_digest(marker_hash, result["rollbackMarkerSha256"])
                )
                if not rollback_artifacts_present and state_matches and marker_matches:
                    authorization_path = reactivation / "RETRY-AUTHORIZATION.json"
                    if authorization_path.exists() or authorization_path.is_symlink():
                        validate_reactivation_retry_authorization(
                            reactivation, args.candidate_id, envelope, current,
                            args.reactivation_hash, claim_bytes, result_bytes, result,
                            args.activation_hash,
                        )
                        state = "reactivation-retry-authorized"
                    elif (
                        observed_active == current and not marker_present
                        and failed_reactivation_is_retryable(reactivation, result, current)
                    ):
                        state = "reactivation-failed-before-active-change"
                        action = "authorize-reactivation-retry"
                    else:
                        state = "reactivation-failed"
            elif not rollback_claim_path.exists() and not rollback_claim_path.is_symlink():
                if (
                    observed_active == envelope["version"] and marker_hash is not None
                    and hmac.compare_digest(marker_hash, result["rollbackMarkerSha256"])
                ):
                    state = "reactivation-complete"
            else:
                rollback_intent_value = reactivation_rollback_intent(
                    args.candidate_id, envelope, current, args.activation_hash,
                    args.reactivation_hash, result_bytes, result["rollbackMarkerSha256"],
                )
                rollback_hash = sha256(canonical_json(rollback_intent_value))
                _rollback_claim, rollback_claim_bytes = validate_reactivation_rollback_claim(
                    reactivation, rollback_intent_value, rollback_hash,
                )
                if rollback_result_path.exists() or rollback_result_path.is_symlink():
                    rollback_result, _result_bytes, rollback_status = validate_reactivation_rollback_result(
                        reactivation, envelope, current, args.activation_hash,
                        args.reactivation_hash, rollback_hash, rollback_claim_bytes,
                    )
                    if rollback_status == "failed":
                        if (
                            observed_active == rollback_result["activeVersion"]
                            and rollback_result["rollbackMarkerConsumed"] == (not marker_present)
                        ):
                            state = "reactivation-rollback-failed"
                    elif observed_active == current and not marker_present:
                        state = "reactivation-rollback-result-present"
                elif observed_active == current and not marker_present:
                    state = "reactivation-rollback-restored-result-missing"
                    action = "finalize-reactivation-rollback-result"
                elif observed_active == envelope["version"] and marker_hash is not None:
                    state = "reactivation-rollback-claimed-before-restoration"
                else:
                    state = "reactivation-rollback-interrupted-manual-review"
        complete_states = {
            "reactivation-complete", "reactivation-rollback-result-present",
            "reactivation-retry-authorized",
        }
        base = {
            "schemaVersion": 1,
            "operation": "pixel-release-reactivation-recovery",
            "status": "recoverable" if action is not None else "complete" if state in complete_states else "manual-review",
            "candidateId": args.candidate_id,
            "product": "Pixel",
            "version": envelope["version"],
            "restoreVersion": current,
            "activeVersion": observed_active,
            "originalActivationHash": args.activation_hash,
            "reactivationHash": args.reactivation_hash,
            "state": state,
            "safeAction": action,
            "confirmationAvailable": action is not None,
            "rollbackMarkerPresent": marker_present,
            "candidateCodeWillExecute": False,
            "networkWillBeUsed": False,
            "boundary": REACTIVATION_RECOVERY_BOUNDARY,
        }
        if action is None:
            return {**base, "recoveryHash": None}
        intent = reactivation_recovery_intent(
            args.candidate_id, envelope, current, observed_active,
            args.activation_hash, args.reactivation_hash, action, claim_bytes,
            result_bytes, rollback_claim_bytes, marker_hash,
        )
        return {**base, "recoveryHash": sha256(canonical_json(intent))}


def finalize_reactivation_recovery(args: argparse.Namespace) -> dict[str, Any]:
    if not args.confirm:
        raise UpdateError("release reactivation recovery requires --confirm")
    if not isinstance(args.recovery_hash, str) or HASH.fullmatch(args.recovery_hash) is None:
        raise UpdateError("release reactivation recovery hash is invalid")
    preview = reactivation_recovery_preview(args)
    if preview["safeAction"] is None or preview["recoveryHash"] is None:
        raise UpdateError("release reactivation recovery has no safely finalizable receipt")
    if not hmac.compare_digest(preview["recoveryHash"], args.recovery_hash):
        raise UpdateError("release reactivation recovery hash differs from the current diagnosis")
    if preview["safeAction"] == "finalize-reactivation-result":
        with exclusive_stage_lock(args.staging_root):
            context = reactivation_context(args, require_restored=False)
            attempt = find_reactivation_attempt(
                context, args.staging_root, args.candidate_id,
                args.activation_hash, args.reactivation_hash,
            )
            reactivation = attempt["path"]
            claim_bytes = attempt["claimBytes"]
            observed_active = active_version(args.active_version_file)
            if observed_active != context["envelope"]["version"]:
                raise UpdateError("reactivation recovery no longer observes the claimed release as active")
            marker = read_regular(args.rollback_marker, 4096, "reactivation rollback marker")
            locked_intent = reactivation_recovery_intent(
                args.candidate_id, context["envelope"], context["current"], observed_active,
                args.activation_hash, args.reactivation_hash, "finalize-reactivation-result",
                claim_bytes, None, None, sha256(marker),
            )
            if not hmac.compare_digest(sha256(canonical_json(locked_intent)), args.recovery_hash):
                raise UpdateError("release reactivation recovery hash differs from the locked diagnosis")
            return write_reactivation_result_receipt(
                reactivation, context["envelope"], context["current"], args.reactivation_hash,
                claim_bytes, "reactivated", "record", args.active_version_file, args.rollback_marker,
                args.candidate_id, reactivation / "LIVE-MUTATION-STARTED",
            )
    if preview["safeAction"] == "authorize-reactivation-retry":
        with exclusive_stage_lock(args.staging_root):
            context = reactivation_context(args, require_restored=False)
            attempt = find_reactivation_attempt(
                context, args.staging_root, args.candidate_id,
                args.activation_hash, args.reactivation_hash,
            )
            reactivation = attempt["path"]
            claim_bytes = attempt["claimBytes"]
            result, result_bytes, status_value = validate_reactivation_result(
                reactivation, context["envelope"], context["current"],
                args.reactivation_hash, claim_bytes, args.candidate_id,
            )
            if (
                status_value != "failed"
                or active_version(args.active_version_file) != context["current"]
                or args.rollback_marker.exists() or args.rollback_marker.is_symlink()
                or not failed_reactivation_is_retryable(
                    reactivation, result, context["current"],
                )
            ):
                raise UpdateError("release reactivation retry authorization no longer observes a safe terminal failure")
            locked_intent = reactivation_recovery_intent(
                args.candidate_id, context["envelope"], context["current"],
                context["current"], args.activation_hash, args.reactivation_hash,
                "authorize-reactivation-retry", claim_bytes, result_bytes, None, None,
            )
            locked_hash = sha256(canonical_json(locked_intent))
            if not hmac.compare_digest(locked_hash, args.recovery_hash):
                raise UpdateError("release reactivation recovery hash differs from the locked diagnosis")
            authorization_path = reactivation / "RETRY-AUTHORIZATION.json"
            if authorization_path.exists() or authorization_path.is_symlink():
                raise UpdateError("release reactivation retry authorization is already recorded")
            authorized_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            authorization = reactivation_retry_authorization_value(
                args.candidate_id, context["envelope"], context["current"],
                args.reactivation_hash, claim_bytes, result_bytes, result,
                locked_hash, authorized_at,
            )
            write_private(authorization_path, canonical_json(authorization))
            fsync_directory(reactivation)
            return authorization
    if preview["safeAction"] == "finalize-reactivation-rollback-result":
        context = reactivation_context(args, require_restored=False)
        attempt = find_reactivation_attempt(
            context, args.staging_root, args.candidate_id,
            args.activation_hash, args.reactivation_hash,
        )
        rollback_claim = parse_json(read_regular(
            attempt["path"] / "ROLLBACK.json",
            MAX_STAGE_RECEIPT, "release reactivation rollback claim",
        ), "release reactivation rollback claim")
        rollback_hash = rollback_claim.get("rollbackHash")
        if not isinstance(rollback_hash, str) or HASH.fullmatch(rollback_hash) is None:
            raise UpdateError("release reactivation rollback claim has no valid exact hash")
        return record_reactivation_rollback(argparse.Namespace(
            candidate_id=args.candidate_id, allowed_signers=args.allowed_signers,
            identity=args.identity, staging_root=args.staging_root,
            activation_hash=args.activation_hash, reactivation_hash=args.reactivation_hash,
            rollback_hash=rollback_hash, active_version_file=args.active_version_file,
            rollback_marker=args.rollback_marker, outcome="rolled-back", phase="record", confirm=True,
        ))
    raise UpdateError("release reactivation recovery action is unsupported")


def validate_rollback_result(
    activation: Path, envelope: dict[str, Any], current: str, activation_hash: str,
    rollback_hash: str, rollback_claim_bytes: bytes,
) -> tuple[dict[str, Any], bytes, str]:
    result_path = activation / "ROLLBACK-RESULT.json"
    result_bytes = read_regular(result_path, MAX_STAGE_RECEIPT, "release rollback result")
    result_info = result_path.lstat()
    if os.name != "nt" and (result_info.st_uid != os.geteuid() or stat.S_IMODE(result_info.st_mode) & 0o077):
        raise UpdateError("release rollback result permissions are unsafe")
    result = parse_json(result_bytes, "release rollback result")
    if result.get("status") == "rolled-back":
        validated, validated_bytes = validate_successful_rollback_result(
            activation, envelope, current, activation_hash, rollback_hash, rollback_claim_bytes,
        )
        return validated, validated_bytes, "rolled-back"
    if result.get("status") != "failed":
        raise UpdateError("release rollback result status is invalid")
    completed_at = result.get("completedAt")
    if not isinstance(completed_at, str) or re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", completed_at,
    ) is None:
        raise UpdateError("release rollback result timestamp is invalid")
    active = result.get("activeVersion")
    if active not in {current, envelope["version"]}:
        raise UpdateError("failed rollback result has an invalid active version")
    phase = result.get("executionPhase")
    if phase not in {"rollback", "record"}:
        raise UpdateError("failed rollback result phase is invalid")
    marker_consumed = result.get("rollbackMarkerConsumed")
    if not isinstance(marker_consumed, bool):
        raise UpdateError("failed rollback marker state is invalid")
    restored = active == current
    expected = {
        "schemaVersion": 1,
        "operation": "pixel-release-update-rollback-result",
        "status": "failed",
        "candidateId": activation.name,
        "product": "Pixel",
        "version": envelope["version"],
        "restoredVersion": current if restored else None,
        "activeVersion": active,
        "activationHash": activation_hash,
        "rollbackHash": rollback_hash,
        "rollbackClaimSha256": sha256(rollback_claim_bytes),
        "executionPhase": phase,
        "completedAt": completed_at,
        "trustedControllerExecuted": True,
        "activeDeploymentRestored": restored,
        "rollbackMarkerConsumed": marker_consumed,
        "recoveryRequired": True,
        "boundary": ROLLBACK_RESULT_BOUNDARY,
    }
    if result != expected:
        raise UpdateError("release rollback result is not an exact failed rollback receipt")
    return result, result_bytes, "failed"


def recovery_intent(
    candidate_id: str, envelope: dict[str, Any], current: str, active: str, activation_hash: str,
    action: str, activation_claim_bytes: bytes, activation_result_bytes: bytes | None,
    rollback_claim_bytes: bytes | None, marker_hash: str | None,
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "operation": "pixel-release-update-recovery",
        "candidateId": candidate_id,
        "product": "Pixel",
        "version": envelope["version"],
        "restoreVersion": current,
        "activeVersion": active,
        "activationHash": activation_hash,
        "action": action,
        "activationClaimSha256": sha256(activation_claim_bytes),
        "activationResultSha256": sha256(activation_result_bytes) if activation_result_bytes is not None else None,
        "rollbackClaimSha256": sha256(rollback_claim_bytes) if rollback_claim_bytes is not None else None,
        "rollbackMarkerSha256": marker_hash,
        "trustedControllerOnly": True,
        "candidateCodeWillExecute": False,
        "networkWillBeUsed": False,
    }


def recovery_preview(args: argparse.Namespace) -> dict[str, Any]:
    if sys.platform != "linux":
        raise UpdateError("release recovery is supported only on qualified Linux hosts")
    if not isinstance(args.activation_hash, str) or HASH.fullmatch(args.activation_hash) is None:
        raise UpdateError("release activation hash is invalid")
    if not args.staging_root.is_absolute() or not args.active_version_file.is_absolute() or not args.rollback_marker.is_absolute():
        raise UpdateError("release recovery paths must be absolute")
    with exclusive_stage_lock(args.staging_root):
        (
            _candidate, envelope, envelope_bytes, _artifacts, _signature, _manifest,
            stage_receipt_bytes, current, host,
        ) = prepared_candidate(args.staging_root, args.candidate_id, args.allowed_signers, args.identity)
        rehearsal_receipt_bytes, rehearsal_receipt_value = stored_rehearsal_receipt_for_rollback(
            args.staging_root, args.candidate_id, envelope, envelope_bytes, stage_receipt_bytes,
            args.identity, current, host,
        )
        activation_intent_value = activation_intent(
            envelope, envelope_bytes, stage_receipt_bytes, rehearsal_receipt_bytes,
            rehearsal_receipt_value, args.identity, current, host, args.candidate_id,
        )
        activation = args.staging_root / "activations" / args.candidate_id
        _activation_claim, activation_claim_bytes = validate_activation_claim(
            activation, activation_intent_value, args.activation_hash,
        )
        observed_active = active_version(args.active_version_file)
        if observed_active not in {current, envelope["version"]}:
            raise UpdateError("observed active version is outside the claimed update transition")
        marker_present = args.rollback_marker.exists() or args.rollback_marker.is_symlink()
        marker_hash = sha256(read_regular(args.rollback_marker, 4096, "activation rollback marker")) if marker_present else None
        activation_result_path = activation / "ACTIVATION-RESULT.json"
        rollback_claim_path = activation / "ROLLBACK.json"
        rollback_result_path = activation / "ROLLBACK-RESULT.json"
        action: str | None = None
        state = "manual-review"
        activation_result_bytes: bytes | None = None
        rollback_claim_bytes: bytes | None = None
        if not activation_result_path.exists() and not activation_result_path.is_symlink():
            if observed_active == envelope["version"] and marker_hash is not None:
                state = "activation-applied-result-missing"
                action = "finalize-activation-result"
            elif observed_active == current:
                state = "activation-claimed-before-active-change"
        else:
            activated, activation_result_bytes, activation_status = validate_activation_result(
                activation, envelope, current, args.activation_hash, activation_claim_bytes,
            )
            if activation_status == "failed":
                rollback_artifacts_present = (
                    rollback_claim_path.exists() or rollback_claim_path.is_symlink()
                    or rollback_result_path.exists() or rollback_result_path.is_symlink()
                )
                state_matches = observed_active == activated["activeVersion"]
                marker_matches = (
                    not activated["rollbackAvailable"]
                    or (
                        marker_hash is not None
                        and hmac.compare_digest(marker_hash, activated["rollbackMarkerSha256"])
                    )
                )
                if not rollback_artifacts_present and state_matches and marker_matches:
                    state = "activation-failed"
            elif not rollback_claim_path.exists() and not rollback_claim_path.is_symlink():
                if (
                    observed_active == envelope["version"] and marker_hash is not None
                    and hmac.compare_digest(marker_hash, activated["rollbackMarkerSha256"])
                ):
                    state = "activation-complete"
            else:
                rollback_intent_value = rollback_intent(
                    args.candidate_id, envelope, current, args.activation_hash,
                    activation_result_bytes, activated["rollbackMarkerSha256"],
                )
                exact_rollback_hash = sha256(canonical_json(rollback_intent_value))
                _rollback_claim, rollback_claim_bytes = validate_rollback_claim(
                    activation, rollback_intent_value, exact_rollback_hash,
                )
                if rollback_result_path.exists() or rollback_result_path.is_symlink():
                    rollback_result, _rollback_result_bytes, rollback_status = validate_rollback_result(
                        activation, envelope, current, args.activation_hash,
                        exact_rollback_hash, rollback_claim_bytes,
                    )
                    if rollback_status == "failed":
                        state_matches = observed_active == rollback_result["activeVersion"]
                        marker_matches = rollback_result["rollbackMarkerConsumed"] == (not marker_present)
                        if state_matches and marker_matches:
                            state = "rollback-failed"
                    elif observed_active == current and not marker_present:
                        state = "rollback-result-present"
                elif observed_active == current and not marker_present:
                    state = "rollback-restored-result-missing"
                    action = "finalize-rollback-result"
                elif observed_active == envelope["version"] and marker_hash is not None:
                    state = "rollback-claimed-before-restoration"
                else:
                    state = "rollback-interrupted-manual-review"
        base = {
            "schemaVersion": 1,
            "status": "recoverable" if action is not None else "complete" if state in {"activation-complete", "rollback-result-present"} else "manual-review",
            "candidateId": args.candidate_id,
            "product": "Pixel",
            "version": envelope["version"],
            "restoreVersion": current,
            "activeVersion": observed_active,
            "activationHash": args.activation_hash,
            "state": state,
            "safeAction": action,
            "confirmationAvailable": action is not None,
            "rollbackMarkerPresent": marker_present,
            "candidateCodeWillExecute": False,
            "networkWillBeUsed": False,
            "boundary": RECOVERY_BOUNDARY,
        }
        if action is None:
            return {**base, "recoveryHash": None}
        intent = recovery_intent(
            args.candidate_id, envelope, current, observed_active, args.activation_hash, action,
            activation_claim_bytes, activation_result_bytes, rollback_claim_bytes, marker_hash,
        )
        return {**base, "recoveryHash": sha256(canonical_json(intent))}


def finalize_recovery(args: argparse.Namespace) -> dict[str, Any]:
    if not args.confirm:
        raise UpdateError("release recovery requires --confirm")
    if not isinstance(args.recovery_hash, str) or HASH.fullmatch(args.recovery_hash) is None:
        raise UpdateError("release recovery hash is invalid")
    preview = recovery_preview(args)
    if preview["safeAction"] is None or preview["recoveryHash"] is None:
        raise UpdateError("release recovery has no safely finalizable receipt")
    if not hmac.compare_digest(preview["recoveryHash"], args.recovery_hash):
        raise UpdateError("release recovery hash differs from the current diagnosis")
    if preview["safeAction"] == "finalize-activation-result":
        with exclusive_stage_lock(args.staging_root):
            (
                _candidate, envelope, envelope_bytes, _artifacts, _signature, _manifest,
                stage_receipt_bytes, current, host,
            ) = prepared_candidate(args.staging_root, args.candidate_id, args.allowed_signers, args.identity)
            rehearsal_receipt_bytes, rehearsal_receipt_value = stored_rehearsal_receipt_for_rollback(
                args.staging_root, args.candidate_id, envelope, envelope_bytes, stage_receipt_bytes,
                args.identity, current, host,
            )
            activation_intent_value = activation_intent(
                envelope, envelope_bytes, stage_receipt_bytes, rehearsal_receipt_bytes,
                rehearsal_receipt_value, args.identity, current, host, args.candidate_id,
            )
            activation = args.staging_root / "activations" / args.candidate_id
            _claim, claim_bytes = validate_activation_claim(activation, activation_intent_value, args.activation_hash)
            observed_active = active_version(args.active_version_file)
            if observed_active != envelope["version"]:
                raise UpdateError("activation recovery no longer observes the claimed release as active")
            marker = read_regular(args.rollback_marker, 4096, "activation rollback marker")
            locked_intent = recovery_intent(
                args.candidate_id, envelope, current, observed_active, args.activation_hash,
                "finalize-activation-result", claim_bytes, None, None, sha256(marker),
            )
            if not hmac.compare_digest(sha256(canonical_json(locked_intent)), args.recovery_hash):
                raise UpdateError("release recovery hash differs from the locked diagnosis")
            return write_activation_result_receipt(
                activation, envelope, current, args.activation_hash, claim_bytes, "activated", "record",
                args.active_version_file, args.rollback_marker,
            )
    if preview["safeAction"] == "finalize-rollback-result":
        rollback_claim = parse_json(
            read_regular(
                args.staging_root / "activations" / args.candidate_id / "ROLLBACK.json",
                MAX_STAGE_RECEIPT, "release rollback claim",
            ),
            "release rollback claim",
        )
        rollback_hash = rollback_claim.get("rollbackHash")
        if not isinstance(rollback_hash, str) or HASH.fullmatch(rollback_hash) is None:
            raise UpdateError("release rollback claim has no valid exact hash")
        result_args = argparse.Namespace(
            candidate_id=args.candidate_id, allowed_signers=args.allowed_signers, identity=args.identity,
            staging_root=args.staging_root, activation_hash=args.activation_hash,
            rollback_hash=rollback_hash,
            active_version_file=args.active_version_file, rollback_marker=args.rollback_marker,
            outcome="rolled-back", phase="record", confirm=True,
        )
        return record_update_rollback(result_args)
    raise UpdateError("release recovery action is unsupported")


def archive_service_state() -> dict[str, str]:
    units = ("openclaw-gateway.service", "pixel-ops-broker.service", "pixel-web-courier.service")
    result = subprocess.run(
        ["/usr/bin/systemctl", "is-active", *units],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    observed = result.stdout.decode("utf-8", "replace").splitlines()
    if result.returncode or observed != ["active"] * len(units):
        raise UpdateError("release archive requires all production Pixel services to be active")
    return {unit: "active" for unit in units}


def archive_root_directory(staging_root: Path, archive_root: Path, *, create: bool) -> Path:
    expected = staging_root.parent / "update-archive"
    if archive_root != expected or not staging_root.is_absolute() or not archive_root.is_absolute():
        raise UpdateError("release archive root must be the fixed sibling of update staging")
    ensure_private_directory(staging_root, "release staging root")
    ensure_private_directory(staging_root.parent, "Pixel install root")
    if create:
        ensure_private_directory(archive_root, "release archive root", create=True)
        fsync_directory(archive_root.parent)
    elif archive_root.exists() or archive_root.is_symlink():
        ensure_private_directory(archive_root, "release archive root")
    comparison = archive_root if archive_root.exists() else archive_root.parent
    if comparison.stat().st_dev != staging_root.stat().st_dev:
        raise UpdateError("release archive root must share the staging filesystem")
    return archive_root


def staged_candidate_count(staging_root: Path) -> int:
    candidates = staging_root / "candidates"
    ensure_private_directory(candidates, "release candidate directory")
    pattern = re.compile(r"pixel-[0-9]{1,6}(?:\.[0-9]{1,6}){2}-[0-9a-f]{64}")
    try:
        entries = list(os.scandir(candidates))
    except OSError as exc:
        raise UpdateError("release candidate directory cannot be enumerated safely") from exc
    if any(not entry.is_dir(follow_symlinks=False) or pattern.fullmatch(entry.name) is None for entry in entries):
        raise UpdateError("release candidate directory contains an unrecognized entry")
    return len(entries)


def archive_entry_xattrs(path: Path) -> None:
    try:
        attributes = os.listxattr(path, follow_symlinks=False)
    except OSError as exc:
        raise UpdateError("release archive evidence extended attributes cannot be inspected") from exc
    if attributes:
        raise UpdateError("release archive evidence contains extended attributes")


def archive_regular_digest(path: Path, maximum_remaining: int) -> tuple[int, str]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise UpdateError("release archive evidence file is unavailable or unsafe") from exc
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_size > maximum_remaining
            or (os.name != "nt" and (
                info.st_uid != os.geteuid() or info.st_gid != os.getegid()
                or stat.S_IMODE(info.st_mode) & 0o7000
            ))
        ):
            raise UpdateError("release archive evidence contains a special, hard-linked, foreign, or oversized file")
        try:
            attributes = os.listxattr(descriptor)
        except OSError as exc:
            raise UpdateError("release archive evidence file extended attributes cannot be inspected") from exc
        if attributes:
            raise UpdateError("release archive evidence contains extended attributes")
        digest = hashlib.sha256()
        total = 0
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            total += len(block)
            if total > maximum_remaining:
                raise UpdateError("release archive evidence exceeds its byte bound")
            digest.update(block)
        current = path.lstat()
        if (
            current.st_dev != info.st_dev or current.st_ino != info.st_ino
            or current.st_size != info.st_size or current.st_mtime_ns != info.st_mtime_ns
            or not stat.S_ISREG(current.st_mode) or current.st_nlink != 1
        ):
            raise UpdateError("release archive evidence changed during hashing")
        return total, digest.hexdigest()
    finally:
        os.close(descriptor)


def archive_tree_manifest(root: Path) -> dict[str, Any]:
    ensure_private_directory(root, "release archive evidence root")
    root_info = root.lstat()
    if os.name != "nt" and (root_info.st_gid != os.getegid() or stat.S_IMODE(root_info.st_mode) & 0o077):
        raise UpdateError("release archive evidence root group or permissions are unsafe")
    archive_entry_xattrs(root)
    entries: list[dict[str, Any]] = [{
        "path": ".", "type": "directory", "mode": f"{stat.S_IMODE(root_info.st_mode):04o}",
    }]
    total_bytes = 0
    def walk_error(error: OSError) -> None:
        raise UpdateError("release archive evidence could not be enumerated completely") from error

    for directory, names, filenames in os.walk(root, topdown=True, onerror=walk_error, followlinks=False):
        names.sort()
        filenames.sort()
        directory_path = Path(directory)
        if directory_path != root:
            info = directory_path.lstat()
            relative = directory_path.relative_to(root).as_posix()
            if (
                not stat.S_ISDIR(info.st_mode)
                or (os.name != "nt" and (
                    info.st_uid != os.geteuid() or info.st_gid != os.getegid()
                    or stat.S_IMODE(info.st_mode) & 0o7000
                ))
            ):
                raise UpdateError("release archive evidence contains an unsafe directory")
            archive_entry_xattrs(directory_path)
            entries.append({
                "path": relative, "type": "directory", "mode": f"{stat.S_IMODE(info.st_mode):04o}",
            })
            if len(entries) > MAX_CLEANUP_ENTRIES:
                raise UpdateError("release archive evidence exceeds its entry bound")
        for name in names:
            path = directory_path / name
            info = path.lstat()
            relative = path.relative_to(root).as_posix()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise UpdateError("release archive evidence contains a symbolic link or special directory entry")
            if len(relative.encode("utf-8")) > 4096 or any(ord(char) < 32 for char in relative):
                raise UpdateError("release archive evidence path exceeds its bound")
        for name in filenames:
            path = directory_path / name
            info = path.lstat()
            relative = path.relative_to(root).as_posix()
            if stat.S_ISLNK(info.st_mode):
                raise UpdateError("release archive evidence contains a symbolic link")
            if len(relative.encode("utf-8")) > 4096 or any(ord(char) < 32 for char in relative):
                raise UpdateError("release archive evidence path exceeds its bound")
            size, digest = archive_regular_digest(path, MAX_CLEANUP_BYTES - total_bytes)
            total_bytes += size
            entries.append({
                "path": relative, "type": "regular", "mode": f"{stat.S_IMODE(info.st_mode):04o}",
                "size": size, "sha256": digest,
            })
            if len(entries) > MAX_CLEANUP_ENTRIES:
                raise UpdateError("release archive evidence exceeds its entry bound")
    value = {"entries": entries, "entryCount": len(entries), "regularBytes": total_bytes}
    value["manifestSha256"] = sha256(canonical_json(value))
    return value


def archive_manifest_value(candidate_id: str, roots: dict[str, Path]) -> dict[str, Any]:
    value = {
        "schemaVersion": 1,
        "operation": "pixel-release-update-archive-manifest",
        "candidateId": candidate_id,
        "trees": {name: archive_tree_manifest(path) for name, path in roots.items()},
    }
    payload = canonical_json(value)
    if len(payload) > MAX_ARCHIVE_MANIFEST:
        raise UpdateError("release archive manifest exceeds its byte bound")
    return value


def archive_manifest_summaries(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        name: {
            "manifestSha256": value["manifestSha256"],
            "entryCount": value["entryCount"],
            "regularBytes": value["regularBytes"],
        }
        for name, value in manifest["trees"].items()
    }


def archive_live_marker_hash(rollback_marker: Path) -> str | None:
    if not rollback_marker.exists() and not rollback_marker.is_symlink():
        return None
    return sha256(read_regular(rollback_marker, 4096, "live activation rollback marker"))


def exact_special_operator_source(root: Path) -> tuple[str, str]:
    if not root.is_absolute() or root != ROOT:
        raise UpdateError("release reactivation archive controller root must be the running exact source")
    try:
        root_info = root.lstat()
        resolved = root.resolve(strict=True)
        git_info = (root / ".git").lstat()
    except OSError as exc:
        raise UpdateError("release reactivation archive controller source is unavailable") from exc
    if (
        resolved != root or not stat.S_ISDIR(root_info.st_mode)
        or (root / ".git").is_symlink()
        or not (stat.S_ISDIR(git_info.st_mode) or stat.S_ISREG(git_info.st_mode))
        or os.name != "nt" and (
            root_info.st_uid != os.geteuid() or stat.S_IMODE(root_info.st_mode) & 0o022
            or git_info.st_uid != os.geteuid() or stat.S_IMODE(git_info.st_mode) & 0o022
        )
    ):
        raise UpdateError("release reactivation archive controller source custody is unsafe")
    environment = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
    observed: dict[str, str] = {}
    commands = {
        "commit": ["git", "-C", str(root), "rev-parse", "HEAD"],
        "tree": ["git", "-C", str(root), "rev-parse", "HEAD^{tree}"],
        "status": ["git", "-C", str(root), "status", "--porcelain=v1", "--untracked-files=all"],
    }
    for name, command in commands.items():
        result = subprocess.run(
            command, env=environment, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        if result.returncode or len(result.stdout) > 4096 or len(result.stderr) > 4096:
            raise UpdateError("release reactivation archive controller Git identity is unavailable")
        try:
            observed[name] = result.stdout.decode("ascii").strip()
            error = result.stderr.decode("ascii").strip()
        except UnicodeError as exc:
            raise UpdateError("release reactivation archive controller Git identity is malformed") from exc
        if error:
            raise UpdateError("release reactivation archive controller Git identity is unavailable")
    if observed["status"] or COMMIT.fullmatch(observed["commit"]) is None or COMMIT.fullmatch(observed["tree"]) is None:
        raise UpdateError("release reactivation archive controller source is not exact and clean")
    return observed["commit"], observed["tree"]


def special_operator_controller_context(args: argparse.Namespace) -> dict[str, Any]:
    if not args.controller_envelope.is_absolute():
        raise UpdateError("release reactivation archive controller envelope path must be absolute")
    source_commit, source_tree = exact_special_operator_source(args.controller_root)
    envelope, envelope_bytes, artifacts, _manifest, signature = load_signed_bundle(
        args.controller_envelope, args.allowed_signers, args.identity,
    )
    controller_version = current_version()
    if (
        envelope["version"] != controller_version
        or envelope["sourceCommit"] != source_commit
        or envelope["sourceTree"] != source_tree
    ):
        raise UpdateError("release reactivation archive controller differs from its signed exact source")
    prefix = f"pixel-{controller_version}"
    members = archive_members(
        artifacts["archive"], controller_version,
        additional_required={
            f"{prefix}/pixel": MAX_CODE_FILE,
            f"{prefix}/scripts/release-update.py": MAX_CODE_FILE,
            f"{prefix}/scripts/archive-reactivation-failure.sh": MAX_CODE_FILE,
        },
    )
    source_paths = {
        "dispatcher": args.controller_root / "pixel",
        "engine": args.controller_root / "scripts" / "release-update.py",
        "command": args.controller_root / "scripts" / "archive-reactivation-failure.sh",
    }
    local_payloads = {
        name: read_regular(path, MAX_CODE_FILE, f"release reactivation archive controller {name}", trust_anchor=True)
        for name, path in source_paths.items()
    }
    archive_payloads = {
        "dispatcher": members[f"{prefix}/pixel"],
        "engine": members[f"{prefix}/scripts/release-update.py"],
        "command": members[f"{prefix}/scripts/archive-reactivation-failure.sh"],
    }
    if any(
        len(local_payloads[name]) != len(archive_payloads[name])
        or not hmac.compare_digest(sha256(local_payloads[name]), sha256(archive_payloads[name]))
        for name in source_paths
    ):
        raise UpdateError("release reactivation archive command differs from its signed controller archive")
    return {
        "controllerVersion": controller_version,
        "controllerSourceCommit": source_commit,
        "controllerSourceTree": source_tree,
        "controllerArchiveSha256": sha256(artifacts["archive"]),
        "controllerEnvelopeSha256": sha256(envelope_bytes),
        "controllerSignatureSha256": sha256(signature),
        "controllerDispatcherSha256": sha256(local_payloads["dispatcher"]),
        "controllerEngineSha256": sha256(local_payloads["engine"]),
        "controllerCommandSha256": sha256(local_payloads["command"]),
        "controllerAuthority": "exact-production-signed-special-operator-only",
    }


def require_special_operator_binding(args: argparse.Namespace, claim: dict[str, Any]) -> None:
    observed = special_operator_controller_context(args)
    if any(claim.get(field) != observed[field] for field in SPECIAL_OPERATOR_BINDING_FIELDS):
        raise UpdateError("release reactivation archive controller changed after its exact preview")


def archive_refuse_reactivation(staging_root: Path, candidate_id: str) -> None:
    reactivations = staging_root / "reactivations"
    if reactivations.exists() or reactivations.is_symlink():
        ensure_private_directory(reactivations, "release reactivation directory")
    reactivation = reactivations / candidate_id
    if reactivation.exists() or reactivation.is_symlink():
        raise UpdateError("release archive refuses every reactivation-bearing journey")


def failed_rollback_archive_context(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any], dict[str, Path]]:
    archive_refuse_reactivation(args.staging_root, args.candidate_id)
    (
        _candidate, envelope, envelope_bytes, _artifacts, _signature, _manifest,
        stage_receipt_bytes, current, host,
    ) = historical_prepared_candidate(args.staging_root, args.candidate_id, args.allowed_signers, args.identity)
    rehearsal_receipt_bytes, rehearsal_receipt_value = stored_rehearsal_receipt_for_rollback(
        args.staging_root, args.candidate_id, envelope, envelope_bytes, stage_receipt_bytes,
        args.identity, current, host,
    )
    activation_intent_value = activation_intent(
        envelope, envelope_bytes, stage_receipt_bytes, rehearsal_receipt_bytes,
        rehearsal_receipt_value, args.identity, current, host, args.candidate_id,
    )
    activation = args.staging_root / "activations" / args.candidate_id
    _activation_claim, activation_claim_bytes = validate_activation_claim(
        activation, activation_intent_value, args.activation_hash,
    )
    activated, activation_result_bytes, activation_status = validate_activation_result(
        activation, envelope, current, args.activation_hash, activation_claim_bytes,
    )
    if activation_status != "activated":
        raise UpdateError("release archive requires an exact activated journey with a terminal failed rollback")
    rollback_intent_value = rollback_intent(
        args.candidate_id, envelope, current, args.activation_hash,
        activation_result_bytes, activated["rollbackMarkerSha256"],
    )
    rollback_hash = sha256(canonical_json(rollback_intent_value))
    _rollback_claim, rollback_claim_bytes = validate_rollback_claim(
        activation, rollback_intent_value, rollback_hash,
    )
    _rollback_result, rollback_result_bytes, rollback_status = validate_rollback_result(
        activation, envelope, current, args.activation_hash, rollback_hash, rollback_claim_bytes,
    )
    if rollback_status != "failed":
        raise UpdateError("release archive is reserved for a terminal failed rollback chain")
    active = active_version(args.active_version_file)
    if active == envelope["version"] or version_tuple(active) < version_tuple(current):
        raise UpdateError("release archive refuses an active candidate or a host older than its recorded predecessor")
    live_marker_hash = archive_live_marker_hash(args.rollback_marker)
    if live_marker_hash is not None and hmac.compare_digest(live_marker_hash, activated["rollbackMarkerSha256"]):
        raise UpdateError("release archive refuses the journey bound to the live rollback marker")
    if staged_candidate_count(args.staging_root) != MAX_STAGED_CANDIDATES:
        raise UpdateError("release archive is available only at the exact staging capacity boundary")
    roots = {
        "candidate": args.staging_root / "candidates" / args.candidate_id,
        "rehearsal": args.staging_root / "rehearsals" / args.candidate_id,
        "activation": activation,
    }
    manifest = archive_manifest_value(args.candidate_id, roots)
    manifest_bytes = canonical_json(manifest)
    services = archive_service_state()
    archive_root_directory(args.staging_root, args.archive_root, create=False)
    destination = args.archive_root / args.candidate_id
    intent = {
        "schemaVersion": 1,
        "operation": "pixel-release-update-archive",
        "candidateId": args.candidate_id,
        "product": "Pixel",
        "version": envelope["version"],
        "recordedPredecessorVersion": current,
        "activeVersionAtArchive": active,
        "activationHash": args.activation_hash,
        "rollbackHash": rollback_hash,
        "sourceCommit": envelope["sourceCommit"],
        "sourceTree": envelope["sourceTree"],
        "stageReceiptSha256": sha256(stage_receipt_bytes),
        "rehearsalReceiptSha256": sha256(rehearsal_receipt_bytes),
        "activationClaimSha256": sha256(activation_claim_bytes),
        "activationResultSha256": sha256(activation_result_bytes),
        "rollbackClaimSha256": sha256(rollback_claim_bytes),
        "rollbackResultSha256": sha256(rollback_result_bytes),
        "rollbackOutcome": "failed",
        "liveRollbackMarkerSha256": live_marker_hash,
        "manifestSha256": sha256(manifest_bytes),
        "trees": archive_manifest_summaries(manifest),
        "stagingRoot": str(args.staging_root),
        "archiveDestination": str(destination),
        "initialCandidateCount": MAX_STAGED_CANDIDATES,
        "finalCandidateCount": MAX_STAGED_CANDIDATES - 1,
        "services": services,
        "installedReleaseWillMove": False,
        "activeDeploymentWillChange": False,
        "failedReceiptsWillBePreserved": True,
    }
    return intent, manifest, roots


def reactivation_archive_root_directory(staging_root: Path, archive_root: Path, *, create: bool) -> Path:
    expected = staging_root.parent / "update-reactivation-archive"
    if archive_root != expected or not staging_root.is_absolute() or not archive_root.is_absolute():
        raise UpdateError("release reactivation archive root must be the fixed sibling of update staging")
    ensure_private_directory(staging_root, "release staging root")
    ensure_private_directory(staging_root.parent, "Pixel install root")
    if create:
        ensure_private_directory(archive_root, "release reactivation archive root", create=True)
        fsync_directory(archive_root.parent)
    elif archive_root.exists() or archive_root.is_symlink():
        ensure_private_directory(archive_root, "release reactivation archive root")
    comparison = archive_root if archive_root.exists() else archive_root.parent
    if comparison.stat().st_dev != staging_root.stat().st_dev:
        raise UpdateError("release reactivation archive root must share the staging filesystem")
    return archive_root


def validate_archivable_reactivation_result(
    reactivation: Path, envelope: dict[str, Any], current: str,
    reactivation_hash: str, claim_bytes: bytes, candidate_id: str,
) -> tuple[dict[str, Any], bytes]:
    result_path = reactivation / "REACTIVATION-RESULT.json"
    result_bytes = read_regular(
        result_path, MAX_STAGE_RECEIPT, "release reactivation archive result",
    )
    result_info = result_path.lstat()
    if os.name != "nt" and (
        result_info.st_uid != os.geteuid() or stat.S_IMODE(result_info.st_mode) & 0o077
    ):
        raise UpdateError("release reactivation archive result permissions are unsafe")
    result = parse_json(result_bytes, "release reactivation archive result")
    marker_fields = {"liveMutationStarted", "liveMutationMarkerSha256"}
    if marker_fields <= set(result):
        validated, validated_bytes, status_value = validate_reactivation_result(
            reactivation, envelope, current, reactivation_hash, claim_bytes, candidate_id,
        )
        if status_value != "failed" or (
            validated["activeVersion"] != current
            or validated["candidateCodeExecuted"] is not True
            or validated["activeDeploymentChanged"] is not False
            or validated["liveMutationStarted"] is not False
            or validated["liveMutationMarkerSha256"] is not None
            or validated["rollbackAvailable"] is not False
            or validated["rollbackMarkerSha256"] is not None
        ):
            raise UpdateError("release reactivation archive requires a terminal no-live-mutation failure")
        return validated, validated_bytes
    if marker_fields & set(result):
        raise UpdateError("release reactivation archive result has an ambiguous live-mutation shape")
    if any(envelope.get(key) != value for key, value in LEGACY_NO_MARKER_REACTIVATION_ARCHIVE_SOURCE.items()):
        raise UpdateError("release reactivation archive refuses an unrecognized legacy no-marker result")
    required = {
        "schemaVersion", "operation", "status", "candidateId", "product", "version",
        "previousVersion", "activeVersion", "reactivationHash", "reactivationClaimSha256",
        "executionPhase", "completedAt", "candidateCodeExecuted", "privateConfigurationRead",
        "activeDeploymentChanged", "networkMayHaveBeenUsed", "rollbackAvailable",
        "rollbackMarkerSha256", "boundary",
    }
    result = exact_keys(result, required, "legacy release reactivation archive result")
    completed_at = result.get("completedAt")
    expected = {
        "schemaVersion": 1,
        "operation": "pixel-release-reactivation-result",
        "status": "failed",
        "candidateId": candidate_id,
        "product": "Pixel",
        "version": envelope["version"],
        "previousVersion": current,
        "activeVersion": current,
        "reactivationHash": reactivation_hash,
        "reactivationClaimSha256": sha256(claim_bytes),
        "executionPhase": "apply",
        "completedAt": completed_at,
        "candidateCodeExecuted": True,
        "privateConfigurationRead": True,
        "activeDeploymentChanged": False,
        "networkMayHaveBeenUsed": True,
        "rollbackAvailable": False,
        "rollbackMarkerSha256": None,
        "boundary": REACTIVATION_RESULT_BOUNDARY,
    }
    if (
        not isinstance(completed_at, str)
        or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", completed_at) is None
        or result != expected or result_bytes != canonical_json(result)
    ):
        raise UpdateError("legacy release reactivation archive result is invalid")
    return result, result_bytes


def archivable_reactivation_intent(
    envelope: dict[str, Any], intent: dict[str, Any],
) -> dict[str, Any]:
    """Return the exact intent shape used by the archived reactivation claim.

    The retained 4.3.15 claim predates the three immutable deployment-record bindings.
    Only its exact signed source identity may use that historical shape.
    """
    if all(
        envelope.get(key) == value
        for key, value in LEGACY_NO_MARKER_REACTIVATION_ARCHIVE_SOURCE.items()
    ):
        legacy_only_absent = {
            "activationDeploymentRecordSha256",
            "retainedDeploymentInputsSha256",
            "retainedInstallManifestSha256",
        }
        if not legacy_only_absent <= set(intent):
            raise UpdateError("legacy release reactivation archive intent bridge is incomplete")
        return {key: value for key, value in intent.items() if key not in legacy_only_absent}
    return intent


def reactivation_failed_archive_context(
    args: argparse.Namespace,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Path], dict[str, Any]]:
    controller = special_operator_controller_context(args)
    reactivation_root = args.staging_root / "reactivations" / args.candidate_id
    if not reactivation_root.exists() or reactivation_root.is_symlink():
        raise UpdateError("release reactivation archive requires exactly one reactivation claim")
    attempt_store = args.staging_root / "reactivation-attempts" / args.candidate_id
    if attempt_store.exists() or attempt_store.is_symlink():
        raise UpdateError("release reactivation archive refuses journaled reactivation attempts")
    live_mutation_marker = reactivation_root / "LIVE-MUTATION-STARTED"
    if live_mutation_marker.exists() or live_mutation_marker.is_symlink():
        raise UpdateError("release reactivation archive requires a terminal no-live-mutation failure")
    observed_entries = {entry.name for entry in os.scandir(reactivation_root)}
    if observed_entries != {"source", "REACTIVATION.json", "REACTIVATION-RESULT.json"}:
        raise UpdateError("release reactivation archive requires a terminal no-live-mutation reactivation result")
    (
        _candidate, envelope, envelope_bytes, _artifacts, _signature, _manifest,
        stage_receipt_bytes, current, host,
    ) = historical_prepared_candidate(args.staging_root, args.candidate_id, args.allowed_signers, args.identity)
    rehearsal_receipt_bytes, rehearsal_receipt_value = stored_rehearsal_receipt_for_rollback(
        args.staging_root, args.candidate_id, envelope, envelope_bytes, stage_receipt_bytes,
        args.identity, current, host,
    )
    activation_intent_value = activation_intent(
        envelope, envelope_bytes, stage_receipt_bytes, rehearsal_receipt_bytes,
        rehearsal_receipt_value, args.identity, current, host, args.candidate_id,
    )
    activation = args.staging_root / "activations" / args.candidate_id
    _activation_claim, activation_claim_bytes = validate_activation_claim(
        activation, activation_intent_value, args.activation_hash,
    )
    activated, activation_result_bytes, activation_status = validate_activation_result(
        activation, envelope, current, args.activation_hash, activation_claim_bytes,
    )
    if activation_status != "activated":
        raise UpdateError("release reactivation archive requires a validated successful original activation")
    rollback_intent_value = rollback_intent(
        args.candidate_id, envelope, current, args.activation_hash,
        activation_result_bytes, activated["rollbackMarkerSha256"],
    )
    rollback_hash = sha256(canonical_json(rollback_intent_value))
    _rollback_claim, rollback_claim_bytes = validate_rollback_claim(
        activation, rollback_intent_value, rollback_hash,
    )
    _rollback_result, rollback_result_bytes, rollback_status = validate_rollback_result(
        activation, envelope, current, args.activation_hash, rollback_hash, rollback_claim_bytes,
    )
    if rollback_status != "rolled-back":
        raise UpdateError("release reactivation archive requires a validated successful original rollback")
    active = active_version(args.active_version_file)
    if active == envelope["version"]:
        raise UpdateError("release reactivation archive refuses the active candidate version")
    live_marker_hash = archive_live_marker_hash(args.rollback_marker)
    if active == current and live_marker_hash is not None:
        raise UpdateError("release reactivation archive requires the original rollback marker to remain consumed")
    reactivation_intent_value = archivable_reactivation_intent(envelope, reactivation_intent(
        args.candidate_id, envelope, envelope_bytes, current, host, args.identity,
        stage_receipt_bytes, rehearsal_receipt_bytes, rehearsal_receipt_value,
        args.activation_hash, activation_claim_bytes, activation_result_bytes,
        rollback_claim_bytes, rollback_result_bytes,
        sha256(read_regular(
            reactivation_root / "source" / ".generated" / "deployment.json",
            MAX_STAGE_RECEIPT, "release reactivation deployment record",
        )),
        sha256(read_regular(
            args.staging_root.parent / "releases" / envelope["version"] / "deployment-inputs.sha256",
            MAX_CODE_FILE, "retained release deployment inputs", trust_anchor=True,
        )),
        sha256(read_regular(
            args.staging_root.parent / "releases" / envelope["version"] / "install-manifest.sha256",
            MAX_CODE_FILE, "retained release install manifest", trust_anchor=True,
        )),
    ))
    reactivation_hash = sha256(canonical_json(reactivation_intent_value))
    reactivation_claim, reactivation_claim_bytes = validate_reactivation_claim(
        reactivation_root, reactivation_intent_value, reactivation_hash,
    )
    reactivation_result, reactivation_result_bytes = validate_archivable_reactivation_result(
        reactivation_root, envelope, current, reactivation_hash, reactivation_claim_bytes,
        args.candidate_id,
    )
    if (
        reactivation_result["activeDeploymentChanged"] is not False
        or reactivation_result["rollbackAvailable"] is not False
        or reactivation_result["rollbackMarkerSha256"] is not None
    ):
        raise UpdateError("release reactivation archive requires a no-deployment-change failure")
    if version_tuple(active) < version_tuple(current):
        raise UpdateError("release reactivation archive requires the active Pixel version to be restored or later")
    if staged_candidate_count(args.staging_root) != MAX_STAGED_CANDIDATES:
        raise UpdateError("release reactivation archive is available only at the exact staging capacity boundary")
    archive_service_state()
    roots = {
        "candidate": args.staging_root / "candidates" / args.candidate_id,
        "rehearsal": args.staging_root / "rehearsals" / args.candidate_id,
        "activation": activation,
        "reactivation": reactivation_root,
    }
    manifest = {
        "schemaVersion": 1,
        "operation": "pixel-release-update-reactivation-archive-manifest",
        "candidateId": args.candidate_id,
        "trees": {name: archive_tree_manifest(path) for name, path in roots.items()},
    }
    manifest_payload = canonical_json(manifest)
    if len(manifest_payload) > MAX_ARCHIVE_MANIFEST:
        raise UpdateError("release reactivation archive manifest exceeds its byte bound")
    reactivation_archive_root_directory(args.staging_root, args.archive_root, create=False)
    destination = args.archive_root / args.candidate_id
    intent = {
        "schemaVersion": 1,
        "operation": "pixel-release-update-reactivation-archive",
        "candidateId": args.candidate_id,
        "product": "Pixel",
        "version": envelope["version"],
        "restoreVersion": current,
        "activeVersionAtArchive": active,
        "originalActivationHash": args.activation_hash,
        "rollbackHash": rollback_hash,
        "reactivationHash": reactivation_hash,
        "sourceCommit": envelope["sourceCommit"],
        "sourceTree": envelope["sourceTree"],
        "stageReceiptSha256": sha256(stage_receipt_bytes),
        "rehearsalReceiptSha256": sha256(rehearsal_receipt_bytes),
        "activationClaimSha256": sha256(activation_claim_bytes),
        "activationResultSha256": sha256(activation_result_bytes),
        "rollbackClaimSha256": sha256(rollback_claim_bytes),
        "rollbackResultSha256": sha256(rollback_result_bytes),
        "reactivationClaimSha256": sha256(reactivation_claim_bytes),
        "reactivationResultSha256": sha256(reactivation_result_bytes),
        "failedExecutionPhase": reactivation_result["executionPhase"],
        "liveRollbackMarkerSha256": live_marker_hash,
        "manifestSha256": sha256(manifest_payload),
        "trees": archive_manifest_summaries(manifest),
        "stagingRoot": str(args.staging_root),
        "archiveDestination": str(destination),
        "initialCandidateCount": MAX_STAGED_CANDIDATES,
        "finalCandidateCount": MAX_STAGED_CANDIDATES - 1,
        "services": archive_service_state(),
        "installedReleaseWillMove": False,
        "activeDeploymentWillChange": False,
        "failedReceiptsWillBePreserved": True,
        **controller,
    }
    return intent, manifest, roots, reactivation_result


def reactivation_archive_claim_value(
    intent: dict[str, Any], archive_hash: str, claimed_at: str,
) -> dict[str, Any]:
    return {
        **intent,
        "status": "claimed",
        "archiveHash": archive_hash,
        "claimedAt": claimed_at,
        "claim": "pre-mutation-episode-four-root-atomic-rename-with-idempotent-recovery",
        "boundary": REACTIVATION_ARCHIVE_BOUNDARY,
    }


def read_reactivation_archive_claim(
    destination: Path, archive_hash: str | None = None, *, candidate_id: str | None = None,
) -> tuple[dict[str, Any], bytes]:
    payload = read_regular(destination / "ARCHIVE.json", MAX_STAGE_RECEIPT, "release reactivation archive claim")
    claim = parse_json(payload, "release reactivation archive claim")
    required = {
        "schemaVersion", "operation", "candidateId", "product", "version", "restoreVersion",
        "activeVersionAtArchive", "originalActivationHash", "rollbackHash", "reactivationHash",
        "sourceCommit", "sourceTree", "stageReceiptSha256", "rehearsalReceiptSha256",
        "activationClaimSha256", "activationResultSha256", "rollbackClaimSha256",
        "rollbackResultSha256", "reactivationClaimSha256", "reactivationResultSha256",
        "failedExecutionPhase", "liveRollbackMarkerSha256", "manifestSha256", "trees",
        "stagingRoot", "archiveDestination", "initialCandidateCount", "finalCandidateCount",
        "services", "installedReleaseWillMove", "activeDeploymentWillChange",
        "failedReceiptsWillBePreserved", "status", "archiveHash", "claimedAt", "claim", "boundary",
        *SPECIAL_OPERATOR_BINDING_FIELDS,
    }
    claim = exact_keys(claim, required, "release reactivation archive claim")
    observed_id = claim.get("candidateId")
    observed_hash = claim.get("archiveHash")
    intent = {key: value for key, value in claim.items() if key not in {
        "status", "archiveHash", "claimedAt", "claim", "boundary",
    }}
    hash_fields = (
        "originalActivationHash", "rollbackHash", "reactivationHash", "stageReceiptSha256",
        "rehearsalReceiptSha256", "activationClaimSha256", "activationResultSha256",
        "rollbackClaimSha256", "rollbackResultSha256", "reactivationClaimSha256",
        "reactivationResultSha256", "manifestSha256", "archiveHash",
        "controllerArchiveSha256", "controllerEnvelopeSha256", "controllerSignatureSha256",
        "controllerDispatcherSha256", "controllerEngineSha256", "controllerCommandSha256",
    )
    services = claim.get("services")
    trees = claim.get("trees")
    tree_shape_valid = isinstance(trees, dict) and set(trees) == {
        "candidate", "rehearsal", "activation", "reactivation",
    }
    if tree_shape_valid:
        for summary in trees.values():
            if not isinstance(summary, dict) or set(summary) != {"manifestSha256", "entryCount", "regularBytes"}:
                tree_shape_valid = False
                break
            if (
                not isinstance(summary.get("manifestSha256"), str)
                or HASH.fullmatch(summary["manifestSha256"]) is None
                or type(summary.get("entryCount")) is not int
                or not 1 <= summary["entryCount"] <= MAX_CLEANUP_ENTRIES
                or type(summary.get("regularBytes")) is not int
                or not 0 <= summary["regularBytes"] <= MAX_CLEANUP_BYTES
            ):
                tree_shape_valid = False
                break
    expected_staging_root = destination.parent.parent / "update-staging"
    candidate_match = re.fullmatch(
        r"pixel-([0-9]{1,6}(?:\.[0-9]{1,6}){2})-[0-9a-f]{64}",
        observed_id if isinstance(observed_id, str) else "",
    )
    if (
        claim.get("schemaVersion") != 1
        or claim.get("operation") != "pixel-release-update-reactivation-archive"
        or claim.get("product") != "Pixel"
        or not isinstance(observed_id, str)
        or candidate_id is not None and observed_id != candidate_id
        or candidate_match is None
        or not isinstance(claim.get("version"), str)
        or SEMVER.fullmatch(claim["version"]) is None
        or candidate_match.group(1) != claim["version"]
        or not isinstance(claim.get("restoreVersion"), str)
        or SEMVER.fullmatch(claim["restoreVersion"]) is None
        or not isinstance(claim.get("activeVersionAtArchive"), str)
        or SEMVER.fullmatch(claim["activeVersionAtArchive"]) is None
        or version_tuple(claim["activeVersionAtArchive"]) < version_tuple(claim["restoreVersion"])
        or not isinstance(claim.get("controllerVersion"), str)
        or SEMVER.fullmatch(claim["controllerVersion"]) is None
        or version_tuple(claim["controllerVersion"]) <= version_tuple(claim["activeVersionAtArchive"])
        or any(not isinstance(claim.get(field), str) or HASH.fullmatch(claim[field]) is None for field in hash_fields)
        or any(not isinstance(claim.get(field), str) or COMMIT.fullmatch(claim[field]) is None for field in (
            "sourceCommit", "sourceTree", "controllerSourceCommit", "controllerSourceTree",
        ))
        or claim.get("controllerAuthority") != "exact-production-signed-special-operator-only"
        or claim.get("failedExecutionPhase") not in {"configure", "bootstrap", "plan", "apply"}
        or claim.get("liveRollbackMarkerSha256") is not None and (
            not isinstance(claim["liveRollbackMarkerSha256"], str)
            or HASH.fullmatch(claim["liveRollbackMarkerSha256"]) is None
        )
        or not tree_shape_valid
        or services != {
            "openclaw-gateway.service": "active",
            "pixel-ops-broker.service": "active",
            "pixel-web-courier.service": "active",
        }
        or claim.get("stagingRoot") != str(expected_staging_root)
        or claim.get("initialCandidateCount") != MAX_STAGED_CANDIDATES
        or claim.get("finalCandidateCount") != MAX_STAGED_CANDIDATES - 1
        or claim.get("installedReleaseWillMove") is not False
        or claim.get("activeDeploymentWillChange") is not False
        or claim.get("failedReceiptsWillBePreserved") is not True
        or claim.get("status") != "claimed"
        or claim.get("claim") != "pre-mutation-episode-four-root-atomic-rename-with-idempotent-recovery"
        or claim.get("boundary") != REACTIVATION_ARCHIVE_BOUNDARY
        or not isinstance(observed_hash, str) or HASH.fullmatch(observed_hash) is None
        or archive_hash is not None and not hmac.compare_digest(observed_hash, archive_hash)
        or not hmac.compare_digest(observed_hash, sha256(canonical_json(intent)))
        or claim.get("archiveDestination") != str(destination)
        or not isinstance(claim.get("claimedAt"), str)
        or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", claim["claimedAt"]) is None
        or payload != canonical_json(claim)
    ):
        raise UpdateError("release reactivation archive claim is invalid")
    return claim, payload


def read_reactivation_archive_manifest(
    destination: Path, claim: dict[str, Any],
) -> tuple[dict[str, Any], bytes]:
    payload = read_regular(destination / "MANIFEST.json", MAX_ARCHIVE_MANIFEST, "release reactivation archive manifest")
    manifest = parse_json(payload, "release reactivation archive manifest")
    manifest = exact_keys(
        manifest, {"schemaVersion", "operation", "candidateId", "trees"},
        "release reactivation archive manifest",
    )
    trees = manifest.get("trees")
    summaries: dict[str, Any] | None = None
    if isinstance(trees, dict) and set(trees) == {"candidate", "rehearsal", "activation", "reactivation"}:
        valid = True
        for tree in trees.values():
            if not isinstance(tree, dict) or set(tree) != {"entries", "entryCount", "regularBytes", "manifestSha256"}:
                valid = False
                break
            entries = tree.get("entries")
            if (
                not isinstance(entries, list) or not 1 <= len(entries) <= MAX_CLEANUP_ENTRIES
                or tree.get("entryCount") != len(entries)
                or type(tree.get("regularBytes")) is not int
                or not 0 <= tree["regularBytes"] <= MAX_CLEANUP_BYTES
                or not isinstance(tree.get("manifestSha256"), str)
                or HASH.fullmatch(tree["manifestSha256"]) is None
            ):
                valid = False
                break
            seen: set[str] = set()
            regular_bytes = 0
            for entry in entries:
                if not isinstance(entry, dict) or entry.get("type") not in {"directory", "regular"}:
                    valid = False
                    break
                required = {"path", "type", "mode"} if entry["type"] == "directory" else {
                    "path", "type", "mode", "size", "sha256",
                }
                if set(entry) != required:
                    valid = False
                    break
                path = entry.get("path")
                if (
                    not isinstance(path, str) or path in seen or len(path.encode("utf-8")) > 4096
                    or any(ord(char) < 32 for char in path)
                    or not isinstance(entry.get("mode"), str)
                    or re.fullmatch(r"[0-7]{4}", entry["mode"]) is None
                ):
                    valid = False
                    break
                seen.add(path)
                if entry["type"] == "regular":
                    if (
                        type(entry.get("size")) is not int or not 0 <= entry["size"] <= MAX_CLEANUP_BYTES
                        or not isinstance(entry.get("sha256"), str) or HASH.fullmatch(entry["sha256"]) is None
                    ):
                        valid = False
                        break
                    regular_bytes += entry["size"]
            if not valid or regular_bytes != tree["regularBytes"] or entries[0] != {
                "path": ".", "type": "directory", "mode": entries[0].get("mode"),
            }:
                valid = False
                break
            unsigned_tree = {key: value for key, value in tree.items() if key != "manifestSha256"}
            if not hmac.compare_digest(tree["manifestSha256"], sha256(canonical_json(unsigned_tree))):
                valid = False
                break
        if valid:
            summaries = archive_manifest_summaries(manifest)
    if (
        manifest.get("schemaVersion") != 1
        or manifest.get("operation") != "pixel-release-update-reactivation-archive-manifest"
        or manifest.get("candidateId") != claim["candidateId"]
        or not hmac.compare_digest(sha256(payload), claim["manifestSha256"])
        or payload != canonical_json(manifest)
        or summaries != claim["trees"]
    ):
        raise UpdateError("release reactivation archive manifest is invalid")
    return manifest, payload


def reactivation_archive_result_value(
    claim: dict[str, Any], claim_bytes: bytes, archived_at: str,
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "operation": "pixel-release-update-reactivation-archive-result",
        "status": "archived",
        "candidateId": claim["candidateId"],
        "product": "Pixel",
        "version": claim["version"],
        "activeVersion": claim["activeVersionAtArchive"],
        "archiveHash": claim["archiveHash"],
        "archiveClaimSha256": sha256(claim_bytes),
        "manifestSha256": claim["manifestSha256"],
        "initialCandidateCount": claim["initialCandidateCount"],
        "finalCandidateCount": claim["finalCandidateCount"],
        "archivedAt": archived_at,
        "installedReleaseMoved": False,
        "activeDeploymentChanged": False,
        "failedReceiptsPreserved": True,
        "boundary": REACTIVATION_ARCHIVE_BOUNDARY,
    }


def read_reactivation_archive_result(
    destination: Path, claim: dict[str, Any], claim_bytes: bytes,
) -> tuple[dict[str, Any], bytes]:
    payload = read_regular(destination / "ARCHIVE-RESULT.json", MAX_STAGE_RECEIPT, "release reactivation archive result")
    result = parse_json(payload, "release reactivation archive result")
    archived_at = result.get("archivedAt")
    if (
        not isinstance(archived_at, str)
        or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", archived_at) is None
        or result != reactivation_archive_result_value(claim, claim_bytes, archived_at)
        or payload != canonical_json(result)
    ):
        raise UpdateError("release reactivation archive result is invalid")
    return result, payload


def publish_reactivation_archive_result(
    destination: Path, claim: dict[str, Any], claim_bytes: bytes,
) -> dict[str, Any]:
    pending = destination / ".ARCHIVE-RESULT.pending"
    final = destination / "ARCHIVE-RESULT.json"
    if pending.exists() or pending.is_symlink():
        payload = read_regular(pending, MAX_STAGE_RECEIPT, "pending release reactivation archive result")
        result = parse_json(payload, "pending release reactivation archive result")
        archived_at = result.get("archivedAt")
        info = pending.lstat()
        if (
            not isinstance(archived_at, str)
            or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", archived_at) is None
            or result != reactivation_archive_result_value(claim, claim_bytes, archived_at)
            or payload != canonical_json(result)
            or os.name != "nt" and (
                info.st_uid != os.geteuid() or info.st_gid != os.getegid()
                or stat.S_IMODE(info.st_mode) & 0o077
            )
        ):
            raise UpdateError("pending release reactivation archive result is invalid")
        archive_entry_xattrs(pending)
    else:
        archived_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        result = reactivation_archive_result_value(claim, claim_bytes, archived_at)
        payload = canonical_json(result)
        write_private(pending, payload)
        fsync_directory(destination)
    rename_noreplace(pending, final, "release reactivation archive result")
    fsync_directory(destination)
    return result


def reactivation_archive_validate_locations(
    args: argparse.Namespace, destination: Path, claim: dict[str, Any], manifest: dict[str, Any],
) -> bool:
    complete = True
    for name in ("candidate", "rehearsal", "activation", "reactivation"):
        source = args.staging_root / f"{name}s" / args.candidate_id
        archived = destination / name
        source_exists = source.exists() or source.is_symlink()
        archived_exists = archived.exists() or archived.is_symlink()
        if source_exists and archived_exists:
            raise UpdateError("release reactivation archive evidence exists in both live and archived locations")
        if not source_exists and not archived_exists:
            raise UpdateError("release reactivation archive evidence is missing from both custody locations")
        observed = archive_tree_manifest(archived if archived_exists else source)
        if observed != manifest["trees"][name]:
            raise UpdateError("release reactivation archive evidence differs from its exact manifest")
        complete = complete and archived_exists
    return complete


def require_reactivation_archive_live_binding(args: argparse.Namespace, claim: dict[str, Any]) -> None:
    if active_version(args.active_version_file) != claim["activeVersionAtArchive"]:
        raise UpdateError("active Pixel changed after the exact reactivation archive preview")
    if archive_live_marker_hash(args.rollback_marker) != claim["liveRollbackMarkerSha256"]:
        raise UpdateError("live rollback marker changed after the exact reactivation archive preview")
    require_special_operator_binding(args, claim)
    if archive_service_state() != claim["services"]:
        raise UpdateError("production Pixel service state changed after the exact reactivation archive preview")


def reactivation_archive_preview(args: argparse.Namespace) -> dict[str, Any]:
    if sys.platform != "linux":
        raise UpdateError("release reactivation archive is supported only on qualified Linux hosts")
    if not isinstance(args.candidate_id, str) or re.fullmatch(
        r"pixel-[0-9]{1,6}(?:\.[0-9]{1,6}){2}-[0-9a-f]{64}", args.candidate_id,
    ) is None:
        raise UpdateError("release reactivation archive candidate ID is invalid")
    if not isinstance(args.activation_hash, str) or HASH.fullmatch(args.activation_hash) is None:
        raise UpdateError("release activation hash is invalid")
    if not all(path.is_absolute() for path in (
        args.staging_root, args.archive_root, args.controller_root, args.controller_envelope,
        args.active_version_file, args.rollback_marker,
    )):
        raise UpdateError("release reactivation archive paths must be absolute")
    with exclusive_stage_lock(args.staging_root):
        reactivation_archive_root_directory(args.staging_root, args.archive_root, create=False)
        destination = args.archive_root / args.candidate_id
        if destination.exists() or destination.is_symlink():
            ensure_private_directory(destination, "release reactivation archive destination")
            claim, claim_bytes = read_reactivation_archive_claim(destination, candidate_id=args.candidate_id)
            require_special_operator_binding(args, claim)
            manifest, _manifest_bytes = read_reactivation_archive_manifest(destination, claim)
            complete = reactivation_archive_validate_locations(args, destination, claim, manifest)
            result_path = destination / "ARCHIVE-RESULT.json"
            if result_path.exists() or result_path.is_symlink():
                pending_result = destination / ".ARCHIVE-RESULT.pending"
                if pending_result.exists() or pending_result.is_symlink():
                    raise UpdateError("completed release reactivation archive conflicts with a pending result")
                result, _result_bytes = read_reactivation_archive_result(destination, claim, claim_bytes)
                if not complete or staged_candidate_count(args.staging_root) != claim["finalCandidateCount"]:
                    raise UpdateError("completed release reactivation archive conflicts with live staging state")
                return {**result, "status": "already-archived", "confirmationRequired": False}
            return {
                **{key: value for key, value in claim.items() if key not in {"status", "claimedAt", "claim"}},
                "status": "interrupted", "confirmationRequired": True,
            }
        intent, _manifest, _roots, _result = reactivation_failed_archive_context(args)
        return {
            **intent,
            "status": "ready",
            "archiveHash": sha256(canonical_json(intent)),
            "confirmationRequired": True,
            "boundary": REACTIVATION_ARCHIVE_BOUNDARY,
        }


def reactivation_archive_failed_update(args: argparse.Namespace) -> dict[str, Any]:
    if not args.confirm:
        raise UpdateError("release reactivation archive requires --confirm")
    if not isinstance(args.archive_hash, str) or HASH.fullmatch(args.archive_hash) is None:
        raise UpdateError("release reactivation archive hash is invalid")
    if not isinstance(args.candidate_id, str) or re.fullmatch(
        r"pixel-[0-9]{1,6}(?:\.[0-9]{1,6}){2}-[0-9a-f]{64}", args.candidate_id,
    ) is None:
        raise UpdateError("release reactivation archive candidate ID is invalid")
    if not isinstance(args.activation_hash, str) or HASH.fullmatch(args.activation_hash) is None:
        raise UpdateError("release activation hash is invalid")
    if sys.platform != "linux":
        raise UpdateError("release reactivation archive is supported only on qualified Linux hosts")
    if not all(path.is_absolute() for path in (
        args.staging_root, args.archive_root, args.controller_root, args.controller_envelope,
        args.active_version_file, args.rollback_marker,
    )):
        raise UpdateError("release reactivation archive paths must be absolute")
    with exclusive_stage_lock(args.staging_root):
        reactivation_archive_root_directory(args.staging_root, args.archive_root, create=False)
        destination = args.archive_root / args.candidate_id
        if destination.exists() or destination.is_symlink():
            ensure_private_directory(destination, "release reactivation archive destination")
            claim, claim_bytes = read_reactivation_archive_claim(destination, args.archive_hash, candidate_id=args.candidate_id)
            require_special_operator_binding(args, claim)
            manifest, _manifest_bytes = read_reactivation_archive_manifest(destination, claim)
        else:
            intent, manifest, _roots, _result = reactivation_failed_archive_context(args)
            expected_hash = sha256(canonical_json(intent))
            if not hmac.compare_digest(expected_hash, args.archive_hash):
                raise UpdateError("release reactivation archive hash differs from the current exact preview")
            reactivation_archive_root_directory(args.staging_root, args.archive_root, create=True)
            temporary = args.archive_root / f".{args.candidate_id}.reactivation-archive-{secrets.token_hex(8)}"
            os.mkdir(temporary, 0o700)
            try:
                manifest_bytes = canonical_json(manifest)
                write_private(temporary / "MANIFEST.json", manifest_bytes)
                claimed_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
                claim = reactivation_archive_claim_value(intent, expected_hash, claimed_at)
                claim_bytes = canonical_json(claim)
                write_private(temporary / "ARCHIVE.json", claim_bytes)
                fsync_directory(temporary)
                rename_directory_noreplace(temporary, destination)
                fsync_directory(args.archive_root)
            except BaseException:
                if temporary.exists() or temporary.is_symlink():
                    remove_private_tree(temporary)
                raise
        result_path = destination / "ARCHIVE-RESULT.json"
        if result_path.exists() or result_path.is_symlink():
            pending_result = destination / ".ARCHIVE-RESULT.pending"
            if pending_result.exists() or pending_result.is_symlink():
                raise UpdateError("completed release reactivation archive conflicts with a pending result")
            result, _result_bytes = read_reactivation_archive_result(destination, claim, claim_bytes)
            if not reactivation_archive_validate_locations(args, destination, claim, manifest):
                raise UpdateError("completed release reactivation archive is not fully archived")
            if staged_candidate_count(args.staging_root) != claim["finalCandidateCount"]:
                raise UpdateError("completed release reactivation archive candidate count is invalid")
            return {**result, "status": "already-archived"}
        require_reactivation_archive_live_binding(args, claim)
        for name in ("candidate", "rehearsal", "activation", "reactivation"):
            source = args.staging_root / f"{name}s" / args.candidate_id
            archived = destination / name
            source_exists = source.exists() or source.is_symlink()
            archived_exists = archived.exists() or archived.is_symlink()
            if source_exists and archived_exists:
                raise UpdateError("release reactivation archive evidence exists in both live and archived locations")
            if source_exists:
                if archive_tree_manifest(source) != manifest["trees"][name]:
                    raise UpdateError("release reactivation archive evidence changed after exact confirmation")
                rename_directory_noreplace(source, archived)
                fsync_directory(source.parent)
                fsync_directory(destination)
            elif archived_exists:
                if archive_tree_manifest(archived) != manifest["trees"][name]:
                    raise UpdateError("archived release reactivation evidence differs from its exact manifest")
            else:
                raise UpdateError("release reactivation archive evidence is missing during recovery")
            require_reactivation_archive_live_binding(args, claim)
        if not reactivation_archive_validate_locations(args, destination, claim, manifest):
            raise UpdateError("release reactivation archive did not move every bound evidence root")
        if staged_candidate_count(args.staging_root) != claim["finalCandidateCount"]:
            raise UpdateError("release reactivation archive did not free exactly one candidate slot")
        require_reactivation_archive_live_binding(args, claim)
        result = publish_reactivation_archive_result(destination, claim, claim_bytes)
        fsync_directory(args.archive_root)
    return result


def archive_claim_value(intent: dict[str, Any], archive_hash: str, claimed_at: str) -> dict[str, Any]:
    return {
        **intent,
        "status": "claimed",
        "archiveHash": archive_hash,
        "claimedAt": claimed_at,
        "claim": "pre-mutation-replay-tombstone-and-three-root-atomic-rename-with-idempotent-recovery",
        "boundary": ARCHIVE_BOUNDARY,
    }


def read_archive_claim(
    destination: Path, archive_hash: str | None = None, *, candidate_id: str | None = None,
) -> tuple[dict[str, Any], bytes]:
    payload = read_regular(destination / "ARCHIVE.json", MAX_STAGE_RECEIPT, "release archive claim")
    claim = parse_json(payload, "release archive claim")
    required = {
        "schemaVersion", "operation", "candidateId", "product", "version", "recordedPredecessorVersion",
        "activeVersionAtArchive", "activationHash", "rollbackHash", "sourceCommit", "sourceTree",
        "stageReceiptSha256", "rehearsalReceiptSha256", "activationClaimSha256", "activationResultSha256",
        "rollbackClaimSha256", "rollbackResultSha256", "rollbackOutcome", "liveRollbackMarkerSha256",
        "manifestSha256", "trees", "stagingRoot", "archiveDestination", "initialCandidateCount",
        "finalCandidateCount", "services", "installedReleaseWillMove", "activeDeploymentWillChange",
        "failedReceiptsWillBePreserved", "status", "archiveHash", "claimedAt", "claim", "boundary",
    }
    claim = exact_keys(claim, required, "release archive claim")
    observed_id = claim.get("candidateId")
    observed_hash = claim.get("archiveHash")
    intent = {key: value for key, value in claim.items() if key not in {
        "status", "archiveHash", "claimedAt", "claim", "boundary",
    }}
    hash_fields = (
        "activationHash", "rollbackHash", "stageReceiptSha256",
        "rehearsalReceiptSha256", "activationClaimSha256", "activationResultSha256",
        "rollbackClaimSha256", "rollbackResultSha256", "manifestSha256", "archiveHash",
    )
    services = claim.get("services")
    trees = claim.get("trees")
    tree_shape_valid = isinstance(trees, dict) and set(trees) == {"candidate", "rehearsal", "activation"}
    if tree_shape_valid:
        for summary in trees.values():
            if not isinstance(summary, dict) or set(summary) != {"manifestSha256", "entryCount", "regularBytes"}:
                tree_shape_valid = False
                break
            if (
                not isinstance(summary.get("manifestSha256"), str)
                or HASH.fullmatch(summary["manifestSha256"]) is None
                or type(summary.get("entryCount")) is not int
                or not 1 <= summary["entryCount"] <= MAX_CLEANUP_ENTRIES
                or type(summary.get("regularBytes")) is not int
                or not 0 <= summary["regularBytes"] <= MAX_CLEANUP_BYTES
            ):
                tree_shape_valid = False
                break
    expected_staging_root = destination.parent.parent / "update-staging"
    candidate_match = re.fullmatch(
        r"pixel-([0-9]{1,6}(?:\.[0-9]{1,6}){2})-[0-9a-f]{64}",
        observed_id if isinstance(observed_id, str) else "",
    )
    if (
        claim.get("schemaVersion") != 1
        or claim.get("operation") != "pixel-release-update-archive"
        or claim.get("product") != "Pixel"
        or not isinstance(observed_id, str)
        or candidate_id is not None and observed_id != candidate_id
        or candidate_match is None
        or not isinstance(claim.get("version"), str)
        or SEMVER.fullmatch(claim["version"]) is None
        or candidate_match.group(1) != claim["version"]
        or not isinstance(claim.get("recordedPredecessorVersion"), str)
        or SEMVER.fullmatch(claim["recordedPredecessorVersion"]) is None
        or not isinstance(claim.get("activeVersionAtArchive"), str)
        or SEMVER.fullmatch(claim["activeVersionAtArchive"]) is None
        or any(not isinstance(claim.get(field), str) or HASH.fullmatch(claim[field]) is None for field in hash_fields)
        or any(not isinstance(claim.get(field), str) or COMMIT.fullmatch(claim[field]) is None for field in ("sourceCommit", "sourceTree"))
        or claim.get("rollbackOutcome") != "failed"
        or claim.get("liveRollbackMarkerSha256") is not None and (
            not isinstance(claim["liveRollbackMarkerSha256"], str)
            or HASH.fullmatch(claim["liveRollbackMarkerSha256"]) is None
        )
        or not tree_shape_valid
        or services != {
            "openclaw-gateway.service": "active",
            "pixel-ops-broker.service": "active",
            "pixel-web-courier.service": "active",
        }
        or claim.get("stagingRoot") != str(expected_staging_root)
        or claim.get("initialCandidateCount") != MAX_STAGED_CANDIDATES
        or claim.get("finalCandidateCount") != MAX_STAGED_CANDIDATES - 1
        or claim.get("installedReleaseWillMove") is not False
        or claim.get("activeDeploymentWillChange") is not False
        or claim.get("failedReceiptsWillBePreserved") is not True
        or claim.get("status") != "claimed"
        or claim.get("claim") != "pre-mutation-replay-tombstone-and-three-root-atomic-rename-with-idempotent-recovery"
        or claim.get("boundary") != ARCHIVE_BOUNDARY
        or not isinstance(observed_hash, str) or HASH.fullmatch(observed_hash) is None
        or archive_hash is not None and not hmac.compare_digest(observed_hash, archive_hash)
        or not hmac.compare_digest(observed_hash, sha256(canonical_json(intent)))
        or claim.get("archiveDestination") != str(destination)
        or not isinstance(claim.get("claimedAt"), str)
        or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", claim["claimedAt"]) is None
        or payload != canonical_json(claim)
    ):
        raise UpdateError("release archive claim is invalid")
    return claim, payload


def read_archive_manifest(destination: Path, claim: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
    payload = read_regular(destination / "MANIFEST.json", MAX_ARCHIVE_MANIFEST, "release archive manifest")
    manifest = parse_json(payload, "release archive manifest")
    manifest = exact_keys(
        manifest, {"schemaVersion", "operation", "candidateId", "trees"},
        "release archive manifest",
    )
    trees = manifest.get("trees")
    summaries: dict[str, Any] | None = None
    if isinstance(trees, dict) and set(trees) == {"candidate", "rehearsal", "activation"}:
        valid = True
        for tree in trees.values():
            if not isinstance(tree, dict) or set(tree) != {"entries", "entryCount", "regularBytes", "manifestSha256"}:
                valid = False
                break
            entries = tree.get("entries")
            if (
                not isinstance(entries, list) or not 1 <= len(entries) <= MAX_CLEANUP_ENTRIES
                or tree.get("entryCount") != len(entries)
                or type(tree.get("regularBytes")) is not int
                or not 0 <= tree["regularBytes"] <= MAX_CLEANUP_BYTES
                or not isinstance(tree.get("manifestSha256"), str)
                or HASH.fullmatch(tree["manifestSha256"]) is None
            ):
                valid = False
                break
            seen: set[str] = set()
            regular_bytes = 0
            for entry in entries:
                if not isinstance(entry, dict) or entry.get("type") not in {"directory", "regular"}:
                    valid = False
                    break
                required = {"path", "type", "mode"} if entry["type"] == "directory" else {
                    "path", "type", "mode", "size", "sha256",
                }
                if set(entry) != required:
                    valid = False
                    break
                path = entry.get("path")
                if (
                    not isinstance(path, str) or path in seen or len(path.encode("utf-8")) > 4096
                    or any(ord(char) < 32 for char in path)
                    or not isinstance(entry.get("mode"), str)
                    or re.fullmatch(r"[0-7]{4}", entry["mode"]) is None
                ):
                    valid = False
                    break
                seen.add(path)
                if entry["type"] == "regular":
                    if (
                        type(entry.get("size")) is not int or not 0 <= entry["size"] <= MAX_CLEANUP_BYTES
                        or not isinstance(entry.get("sha256"), str) or HASH.fullmatch(entry["sha256"]) is None
                    ):
                        valid = False
                        break
                    regular_bytes += entry["size"]
            if not valid or regular_bytes != tree["regularBytes"] or entries[0] != {
                "path": ".", "type": "directory", "mode": entries[0].get("mode"),
            }:
                valid = False
                break
            unsigned_tree = {key: value for key, value in tree.items() if key != "manifestSha256"}
            if not hmac.compare_digest(tree["manifestSha256"], sha256(canonical_json(unsigned_tree))):
                valid = False
                break
        if valid:
            summaries = archive_manifest_summaries(manifest)
    if (
        manifest.get("schemaVersion") != 1
        or manifest.get("operation") != "pixel-release-update-archive-manifest"
        or manifest.get("candidateId") != claim["candidateId"]
        or not hmac.compare_digest(sha256(payload), claim["manifestSha256"])
        or payload != canonical_json(manifest)
        or summaries != claim["trees"]
    ):
        raise UpdateError("release archive manifest is invalid")
    return manifest, payload


def archive_result_value(claim: dict[str, Any], claim_bytes: bytes, archived_at: str) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "operation": "pixel-release-update-archive-result",
        "status": "archived",
        "candidateId": claim["candidateId"],
        "product": "Pixel",
        "version": claim["version"],
        "activeVersion": claim["activeVersionAtArchive"],
        "archiveHash": claim["archiveHash"],
        "archiveClaimSha256": sha256(claim_bytes),
        "manifestSha256": claim["manifestSha256"],
        "initialCandidateCount": claim["initialCandidateCount"],
        "finalCandidateCount": claim["finalCandidateCount"],
        "archivedAt": archived_at,
        "installedReleaseMoved": False,
        "activeDeploymentChanged": False,
        "failedReceiptsPreserved": True,
        "boundary": ARCHIVE_BOUNDARY,
    }


def read_archive_result(destination: Path, claim: dict[str, Any], claim_bytes: bytes) -> tuple[dict[str, Any], bytes]:
    payload = read_regular(destination / "ARCHIVE-RESULT.json", MAX_STAGE_RECEIPT, "release archive result")
    result = parse_json(payload, "release archive result")
    archived_at = result.get("archivedAt")
    if (
        not isinstance(archived_at, str)
        or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", archived_at) is None
        or result != archive_result_value(claim, claim_bytes, archived_at)
        or payload != canonical_json(result)
    ):
        raise UpdateError("release archive result is invalid")
    return result, payload


def publish_archive_result(destination: Path, claim: dict[str, Any], claim_bytes: bytes) -> dict[str, Any]:
    pending = destination / ".ARCHIVE-RESULT.pending"
    final = destination / "ARCHIVE-RESULT.json"
    if pending.exists() or pending.is_symlink():
        payload = read_regular(pending, MAX_STAGE_RECEIPT, "pending release archive result")
        result = parse_json(payload, "pending release archive result")
        archived_at = result.get("archivedAt")
        info = pending.lstat()
        if (
            not isinstance(archived_at, str)
            or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", archived_at) is None
            or result != archive_result_value(claim, claim_bytes, archived_at)
            or payload != canonical_json(result)
            or os.name != "nt" and (
                info.st_uid != os.geteuid() or info.st_gid != os.getegid()
                or stat.S_IMODE(info.st_mode) & 0o077
            )
        ):
            raise UpdateError("pending release archive result is invalid")
        archive_entry_xattrs(pending)
    else:
        archived_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        result = archive_result_value(claim, claim_bytes, archived_at)
        payload = canonical_json(result)
        write_private(pending, payload)
        fsync_directory(destination)
    rename_noreplace(pending, final, "release archive result")
    fsync_directory(destination)
    return result


def archive_validate_locations(
    args: argparse.Namespace, destination: Path, claim: dict[str, Any], manifest: dict[str, Any],
) -> bool:
    complete = True
    for name in ("candidate", "rehearsal", "activation"):
        source = args.staging_root / f"{name}s" / args.candidate_id
        archived = destination / name
        source_exists = source.exists() or source.is_symlink()
        archived_exists = archived.exists() or archived.is_symlink()
        if source_exists and archived_exists:
            raise UpdateError("release archive evidence exists in both live and archived locations")
        if not source_exists and not archived_exists:
            raise UpdateError("release archive evidence is missing from both custody locations")
        observed = archive_tree_manifest(archived if archived_exists else source)
        if observed != manifest["trees"][name]:
            raise UpdateError("release archive evidence differs from its exact manifest")
        complete = complete and archived_exists
    return complete


def archive_preview(args: argparse.Namespace) -> dict[str, Any]:
    if sys.platform != "linux":
        raise UpdateError("release archive is supported only on qualified Linux hosts")
    if not isinstance(args.candidate_id, str) or re.fullmatch(
        r"pixel-[0-9]{1,6}(?:\.[0-9]{1,6}){2}-[0-9a-f]{64}", args.candidate_id,
    ) is None:
        raise UpdateError("release archive candidate ID is invalid")
    if not isinstance(args.activation_hash, str) or HASH.fullmatch(args.activation_hash) is None:
        raise UpdateError("release activation hash is invalid")
    if not all(path.is_absolute() for path in (
        args.staging_root, args.archive_root, args.active_version_file, args.rollback_marker,
    )):
        raise UpdateError("release archive paths must be absolute")
    with exclusive_stage_lock(args.staging_root):
        archive_refuse_reactivation(args.staging_root, args.candidate_id)
        archive_root_directory(args.staging_root, args.archive_root, create=False)
        destination = args.archive_root / args.candidate_id
        if destination.exists() or destination.is_symlink():
            ensure_private_directory(destination, "release archive destination")
            claim, claim_bytes = read_archive_claim(destination, candidate_id=args.candidate_id)
            manifest, _manifest_bytes = read_archive_manifest(destination, claim)
            complete = archive_validate_locations(args, destination, claim, manifest)
            result_path = destination / "ARCHIVE-RESULT.json"
            if result_path.exists() or result_path.is_symlink():
                pending_result = destination / ".ARCHIVE-RESULT.pending"
                if pending_result.exists() or pending_result.is_symlink():
                    raise UpdateError("completed release archive conflicts with a pending result")
                result, _result_bytes = read_archive_result(destination, claim, claim_bytes)
                if not complete or staged_candidate_count(args.staging_root) != claim["finalCandidateCount"]:
                    raise UpdateError("completed release archive conflicts with live staging state")
                return {**result, "status": "already-archived", "confirmationRequired": False}
            return {
                **{key: value for key, value in claim.items() if key not in {"status", "claimedAt", "claim"}},
                "status": "interrupted", "confirmationRequired": True,
            }
        intent, _manifest, _roots = failed_rollback_archive_context(args)
        return {
            **intent,
            "status": "ready",
            "archiveHash": sha256(canonical_json(intent)),
            "confirmationRequired": True,
            "boundary": ARCHIVE_BOUNDARY,
        }


def archive_failed_update(args: argparse.Namespace) -> dict[str, Any]:
    if not args.confirm:
        raise UpdateError("release archive requires --confirm")
    if not isinstance(args.archive_hash, str) or HASH.fullmatch(args.archive_hash) is None:
        raise UpdateError("release archive hash is invalid")
    if sys.platform != "linux":
        raise UpdateError("release archive is supported only on qualified Linux hosts")
    with exclusive_stage_lock(args.staging_root):
        archive_refuse_reactivation(args.staging_root, args.candidate_id)
        archive_root_directory(args.staging_root, args.archive_root, create=False)
        destination = args.archive_root / args.candidate_id
        if destination.exists() or destination.is_symlink():
            ensure_private_directory(destination, "release archive destination")
            claim, claim_bytes = read_archive_claim(destination, args.archive_hash, candidate_id=args.candidate_id)
            manifest, _manifest_bytes = read_archive_manifest(destination, claim)
        else:
            intent, manifest, _roots = failed_rollback_archive_context(args)
            expected_hash = sha256(canonical_json(intent))
            if not hmac.compare_digest(expected_hash, args.archive_hash):
                raise UpdateError("release archive hash differs from the current exact preview")
            archive_root_directory(args.staging_root, args.archive_root, create=True)
            temporary = args.archive_root / f".{args.candidate_id}.archive-{secrets.token_hex(8)}"
            os.mkdir(temporary, 0o700)
            try:
                manifest_bytes = canonical_json(manifest)
                write_private(temporary / "MANIFEST.json", manifest_bytes)
                claimed_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
                claim = archive_claim_value(intent, expected_hash, claimed_at)
                claim_bytes = canonical_json(claim)
                write_private(temporary / "ARCHIVE.json", claim_bytes)
                fsync_directory(temporary)
                rename_directory_noreplace(temporary, destination)
                fsync_directory(args.archive_root)
            except BaseException:
                if temporary.exists() or temporary.is_symlink():
                    remove_private_tree(temporary)
                raise
        result_path = destination / "ARCHIVE-RESULT.json"
        if result_path.exists() or result_path.is_symlink():
            pending_result = destination / ".ARCHIVE-RESULT.pending"
            if pending_result.exists() or pending_result.is_symlink():
                raise UpdateError("completed release archive conflicts with a pending result")
            result, _result_bytes = read_archive_result(destination, claim, claim_bytes)
            if not archive_validate_locations(args, destination, claim, manifest):
                raise UpdateError("completed release archive is not fully archived")
            if staged_candidate_count(args.staging_root) != claim["finalCandidateCount"]:
                raise UpdateError("completed release archive candidate count is invalid")
            return {**result, "status": "already-archived"}
        if active_version(args.active_version_file) != claim["activeVersionAtArchive"]:
            raise UpdateError("active Pixel changed after the exact archive preview")
        if archive_live_marker_hash(args.rollback_marker) != claim["liveRollbackMarkerSha256"]:
            raise UpdateError("live rollback marker changed after the exact archive preview")
        archive_service_state()
        for name in ("candidate", "rehearsal", "activation"):
            source = args.staging_root / f"{name}s" / args.candidate_id
            archived = destination / name
            source_exists = source.exists() or source.is_symlink()
            archived_exists = archived.exists() or archived.is_symlink()
            if source_exists and archived_exists:
                raise UpdateError("release archive evidence exists in both live and archived locations")
            if source_exists:
                if archive_tree_manifest(source) != manifest["trees"][name]:
                    raise UpdateError("release archive evidence changed after exact confirmation")
                rename_directory_noreplace(source, archived)
                fsync_directory(source.parent)
                fsync_directory(destination)
            elif archived_exists:
                if archive_tree_manifest(archived) != manifest["trees"][name]:
                    raise UpdateError("archived release evidence differs from its exact manifest")
            else:
                raise UpdateError("release archive evidence is missing during recovery")
        if not archive_validate_locations(args, destination, claim, manifest):
            raise UpdateError("release archive did not move every bound evidence root")
        if staged_candidate_count(args.staging_root) != claim["finalCandidateCount"]:
            raise UpdateError("release archive did not free exactly one candidate slot")
        if active_version(args.active_version_file) != claim["activeVersionAtArchive"]:
            raise UpdateError("active Pixel changed during release archival")
        if archive_live_marker_hash(args.rollback_marker) != claim["liveRollbackMarkerSha256"]:
            raise UpdateError("live rollback marker changed during release archival")
        result = publish_archive_result(destination, claim, claim_bytes)
        fsync_directory(args.archive_root)
    return result


def ensure_owned_cleanup_directory(path: Path, label: str) -> None:
    try:
        info = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise UpdateError(f"{label} is unavailable") from exc
    if resolved != path or not stat.S_ISDIR(info.st_mode):
        raise UpdateError(f"{label} must be one real directory without symbolic-link components")
    if os.name != "nt" and info.st_uid != os.geteuid():
        raise UpdateError(f"{label} must be owned by the current user")


def cleanup_tree_shape(root: Path) -> dict[str, Any]:
    ensure_owned_cleanup_directory(root, "cleanup evidence root")
    digest = hashlib.sha256()
    total_bytes = 0
    entry_count = 0

    def record(relative: str, kind: str, size: int, target_hash: str = "") -> None:
        for field in (relative, kind, str(size), target_hash):
            digest.update(field.encode("utf-8"))
            digest.update(b"\0")

    for directory, names, filenames in os.walk(root, topdown=True, followlinks=False):
        names.sort()
        filenames.sort()
        directory_path = Path(directory)
        ensure_owned_cleanup_directory(directory_path, "cleanup evidence directory")
        for name in list(names):
            path = directory_path / name
            relative = path.relative_to(root).as_posix()
            info = path.lstat()
            entry_count += 1
            if entry_count > MAX_CLEANUP_ENTRIES or len(relative.encode("utf-8")) > 4096 or any(ord(char) < 32 for char in relative):
                raise UpdateError("cleanup evidence tree exceeds its entry or path bound")
            if stat.S_ISLNK(info.st_mode):
                names.remove(name)
                target = os.readlink(path)
                if len(os.fsencode(target)) > 4096:
                    raise UpdateError("cleanup evidence symbolic link exceeds its bound")
                record(relative, "symlink", len(os.fsencode(target)), sha256(os.fsencode(target)))
            elif stat.S_ISDIR(info.st_mode) and (os.name == "nt" or info.st_uid == os.geteuid()):
                record(relative, "directory", 0)
            else:
                raise UpdateError("cleanup evidence contains an unsafe directory entry")
        for name in filenames:
            path = directory_path / name
            relative = path.relative_to(root).as_posix()
            info = path.lstat()
            entry_count += 1
            if entry_count > MAX_CLEANUP_ENTRIES or len(relative.encode("utf-8")) > 4096 or any(ord(char) < 32 for char in relative):
                raise UpdateError("cleanup evidence tree exceeds its entry or path bound")
            if stat.S_ISLNK(info.st_mode):
                target = os.readlink(path)
                if len(os.fsencode(target)) > 4096:
                    raise UpdateError("cleanup evidence symbolic link exceeds its bound")
                record(relative, "symlink", len(os.fsencode(target)), sha256(os.fsencode(target)))
                continue
            if (
                not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or (os.name != "nt" and info.st_uid != os.geteuid())
            ):
                raise UpdateError("cleanup evidence contains a special, hard-linked, or foreign file")
            total_bytes += info.st_size
            if total_bytes > MAX_CLEANUP_BYTES:
                raise UpdateError("cleanup evidence tree exceeds its byte bound")
            record(relative, "regular", info.st_size)
    if entry_count < 1 or total_bytes < 1:
        raise UpdateError("cleanup evidence tree must contain bounded regular evidence")
    return {
        "shapeSha256": digest.hexdigest(),
        "entries": entry_count,
        "regularBytes": total_bytes,
    }


def safe_remove_cleanup_tree(root: Path) -> None:
    if not root.exists() and not root.is_symlink():
        return
    ensure_owned_cleanup_directory(root, "cleanup quarantine tree")
    count = 0
    for directory, names, filenames in os.walk(root, topdown=False, followlinks=False):
        directory_path = Path(directory)
        ensure_owned_cleanup_directory(directory_path, "cleanup quarantine directory")
        for name in filenames:
            path = directory_path / name
            info = path.lstat()
            count += 1
            if count > MAX_CLEANUP_ENTRIES:
                raise UpdateError("cleanup quarantine exceeds its entry bound")
            if stat.S_ISLNK(info.st_mode):
                path.unlink()
            elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and (os.name == "nt" or info.st_uid == os.geteuid()):
                path.unlink()
            else:
                raise UpdateError("cleanup quarantine contains a special, hard-linked, or foreign file")
        for name in names:
            path = directory_path / name
            info = path.lstat()
            count += 1
            if count > MAX_CLEANUP_ENTRIES:
                raise UpdateError("cleanup quarantine exceeds its entry bound")
            if stat.S_ISLNK(info.st_mode):
                path.unlink()
            elif stat.S_ISDIR(info.st_mode) and (os.name == "nt" or info.st_uid == os.geteuid()):
                path.rmdir()
            else:
                raise UpdateError("cleanup quarantine contains an unsafe directory entry")
    root.rmdir()


def cleanup_intent(
    candidate_id: str, envelope: dict[str, Any], current: str, activation_hash: str,
    stage_receipt_bytes: bytes, rehearsal_receipt_bytes: bytes, activation_claim_bytes: bytes,
    activation_result_bytes: bytes, rollback_claim_bytes: bytes | None, rollback_result_bytes: bytes | None,
    shapes: dict[str, Any], active_version_at_cleanup: str, terminal_outcome: str | None = None,
) -> dict[str, Any]:
    value = {
        "schemaVersion": 1,
        "operation": "pixel-release-update-cleanup",
        "candidateId": candidate_id,
        "product": "Pixel",
        "version": envelope["version"],
        "restoredVersion": current,
        "activeVersionAtCleanup": active_version_at_cleanup,
        "activationHash": activation_hash,
        "sourceCommit": envelope["sourceCommit"],
        "sourceTree": envelope["sourceTree"],
        "stageReceiptSha256": sha256(stage_receipt_bytes),
        "rehearsalReceiptSha256": sha256(rehearsal_receipt_bytes),
        "activationClaimSha256": sha256(activation_claim_bytes),
        "activationResultSha256": sha256(activation_result_bytes),
        "rollbackClaimSha256": sha256(rollback_claim_bytes) if rollback_claim_bytes is not None else None,
        "rollbackResultSha256": sha256(rollback_result_bytes) if rollback_result_bytes is not None else None,
        "trees": shapes,
        "deleteScope": ["verified-bundle-copy", "rehearsal-copy", "completed-activation-workspace"],
        "installedReleaseWillBeDeleted": False,
        "activeDeploymentWillChange": False,
        "auditTombstoneWillBePreserved": True,
    }
    if terminal_outcome is not None:
        value["terminalOutcome"] = terminal_outcome
    return value


def cleanup_boundary(value: dict[str, Any]) -> str:
    return (
        CLEANUP_FAILED_ACTIVATION_BOUNDARY
        if value.get("terminalOutcome") == "activation-failed"
        else CLEANUP_BOUNDARY
    )


def completed_cleanup_context(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Path]]:
    reactivation = args.staging_root / "reactivations" / args.candidate_id
    if reactivation.exists() or reactivation.is_symlink():
        raise UpdateError("completed reactivation cleanup requires a dedicated immutable receipt chain")
    (
        _candidate, envelope, envelope_bytes, _artifacts, _signature, _manifest,
        stage_receipt_bytes, current, host,
    ) = historical_prepared_candidate(args.staging_root, args.candidate_id, args.allowed_signers, args.identity)
    rehearsal_receipt_bytes, rehearsal_receipt_value = stored_rehearsal_receipt_for_rollback(
        args.staging_root, args.candidate_id, envelope, envelope_bytes, stage_receipt_bytes,
        args.identity, current, host,
    )
    activation_intent_value = activation_intent(
        envelope, envelope_bytes, stage_receipt_bytes, rehearsal_receipt_bytes,
        rehearsal_receipt_value, args.identity, current, host, args.candidate_id,
    )
    activation = args.staging_root / "activations" / args.candidate_id
    _activation_claim, activation_claim_bytes = validate_activation_claim(
        activation, activation_intent_value, args.activation_hash,
    )
    activated, activation_result_bytes, activation_status = validate_activation_result(
        activation, envelope, current, args.activation_hash, activation_claim_bytes,
    )
    roots = {
        "candidate": args.staging_root / "candidates" / args.candidate_id,
        "rehearsal": args.staging_root / "rehearsals" / args.candidate_id,
        "activation": activation,
    }
    active_version_at_cleanup = active_version(args.active_version_file)
    if active_version_at_cleanup == envelope["version"]:
        raise UpdateError("release cleanup refuses the active candidate version")
    if version_tuple(active_version_at_cleanup) < version_tuple(current):
        raise UpdateError("release cleanup requires the active Pixel version to be restored or later")
    if activation_status == "failed":
        observed = {entry.name for entry in os.scandir(activation)}
        if (
            activated["activeDeploymentChanged"] is not False
            or activated["rollbackAvailable"] is not False
            or activated["rollbackMarkerSha256"] is not None
            or observed != {"source", "ACTIVATION.json", "ACTIVATION-RESULT.json"}
        ):
            raise UpdateError("failed activation cleanup requires a terminal no-mutation journey")
        if args.rollback_marker.exists() or args.rollback_marker.is_symlink():
            read_regular(
                args.rollback_marker, 4096, "pre-existing activation rollback marker",
                trust_anchor=True,
            )
        shapes = {name: cleanup_tree_shape(path) for name, path in roots.items()}
        intent = cleanup_intent(
            args.candidate_id, envelope, current, args.activation_hash, stage_receipt_bytes,
            rehearsal_receipt_bytes, activation_claim_bytes, activation_result_bytes,
            None, None, shapes, active_version_at_cleanup, "activation-failed",
        )
        return intent, roots
    rollback_intent_value = rollback_intent(
        args.candidate_id, envelope, current, args.activation_hash,
        activation_result_bytes, activated["rollbackMarkerSha256"],
    )
    rollback_hash = sha256(canonical_json(rollback_intent_value))
    _rollback_claim, rollback_claim_bytes = validate_rollback_claim(activation, rollback_intent_value, rollback_hash)
    _rollback_result, rollback_result_bytes = validate_successful_rollback_result(
        activation, envelope, current, args.activation_hash, rollback_hash, rollback_claim_bytes,
    )
    if args.rollback_marker.exists() or args.rollback_marker.is_symlink():
        raise UpdateError("completed update cleanup requires the update rollback marker to remain consumed")
    shapes = {name: cleanup_tree_shape(path) for name, path in roots.items()}
    intent = cleanup_intent(
        args.candidate_id, envelope, current, args.activation_hash, stage_receipt_bytes,
        rehearsal_receipt_bytes, activation_claim_bytes, activation_result_bytes,
        rollback_claim_bytes, rollback_result_bytes, shapes, active_version_at_cleanup,
    )
    return intent, roots


def cleanup_claim_value(intent: dict[str, Any], cleanup_hash: str, claimed_at: str) -> dict[str, Any]:
    return {
        **intent,
        "status": "claimed",
        "cleanupHash": cleanup_hash,
        "claimedAt": claimed_at,
        "claim": "atomic-quarantine-before-delete",
        "boundary": cleanup_boundary(intent),
    }


def cleanup_history_value(claim: dict[str, Any], cleaned_at: str | None = None) -> dict[str, Any]:
    result = {key: value for key, value in claim.items() if key not in {"status", "claimedAt", "claim"}}
    complete = cleaned_at is not None
    return {
        **result,
        "status": "cleaned" if complete else "deleting",
        "cleanedAt": cleaned_at,
        "candidateEvidenceDeleted": complete,
        "rehearsalEvidenceDeleted": complete,
        "activationWorkspaceDeleted": complete,
        "boundary": cleanup_boundary(claim),
    }


def finalize_cleanup_history_value(checkpoint: dict[str, Any], cleaned_at: str) -> dict[str, Any]:
    result = {key: value for key, value in checkpoint.items() if key not in {
        "status", "cleanedAt", "candidateEvidenceDeleted", "rehearsalEvidenceDeleted",
        "activationWorkspaceDeleted",
    }}
    return {
        **result,
        "status": "cleaned",
        "cleanedAt": cleaned_at,
        "candidateEvidenceDeleted": True,
        "rehearsalEvidenceDeleted": True,
        "activationWorkspaceDeleted": True,
    }


def cleanup_directories(staging_root: Path) -> tuple[Path, Path]:
    quarantine = staging_root / "cleanup-quarantine"
    history = staging_root / "cleanup-history"
    ensure_private_directory(quarantine, "release cleanup quarantine", create=True)
    ensure_private_directory(history, "release cleanup history", create=True)
    return quarantine, history


def validate_cleanup_store(quarantine: Path, history: Path) -> list[os.DirEntry[str]]:
    try:
        quarantine_entries = list(os.scandir(quarantine))
        history_entries = list(os.scandir(history))
    except OSError as exc:
        raise UpdateError("release cleanup storage cannot be enumerated safely") from exc
    candidate_pattern = r"pixel-[0-9]{1,6}(?:\.[0-9]{1,6}){2}-[0-9a-f]{64}"
    temporary_pattern = rf"\.{candidate_pattern}\.cleanup-[0-9a-f]{{16}}"
    if len(quarantine_entries) > MAX_CLEANUP_HISTORY or any(
        not entry.is_dir(follow_symlinks=False)
        or re.fullmatch(candidate_pattern, entry.name) is None
        and re.fullmatch(temporary_pattern, entry.name) is None
        for entry in quarantine_entries
    ):
        raise UpdateError("release cleanup quarantine contains an unrecognized entry")
    for entry in quarantine_entries:
        ensure_private_directory(Path(entry.path), "release cleanup quarantine entry")
    if len(history_entries) > MAX_CLEANUP_HISTORY * 2:
        raise UpdateError("release cleanup history exceeds its total entry bound")
    final_history_entries: list[os.DirEntry[str]] = []
    for entry in history_entries:
        if (
            not entry.is_file(follow_symlinks=False)
            or re.fullmatch(
                rf"{candidate_pattern}\.json|\.{candidate_pattern}\.json\.replace-[0-9a-f]{{16}}",
                entry.name,
            ) is None
        ):
            raise UpdateError("release cleanup history contains an unrecognized entry")
        ensure_private_cleanup_receipt(Path(entry.path), "release cleanup history entry")
        if not entry.name.startswith("."):
            final_history_entries.append(entry)
    if len(final_history_entries) > MAX_CLEANUP_HISTORY:
        raise UpdateError("release cleanup history exceeds its retention limit")
    return final_history_entries


def remove_cleanup_history_temporaries(history: Path) -> None:
    pattern = r"\.pixel-[0-9]{1,6}(?:\.[0-9]{1,6}){2}-[0-9a-f]{64}\.json\.replace-[0-9a-f]{16}"
    try:
        entries = list(os.scandir(history))
    except OSError as exc:
        raise UpdateError("release cleanup history cannot be enumerated safely") from exc
    for entry in entries:
        if not entry.name.startswith("."):
            continue
        if not entry.is_file(follow_symlinks=False) or re.fullmatch(pattern, entry.name) is None:
            raise UpdateError("release cleanup history contains an unrecognized temporary entry")
        path = Path(entry.path)
        read_regular(path, MAX_STAGE_RECEIPT, "release cleanup history temporary")
        ensure_private_cleanup_receipt(path, "release cleanup history temporary")
        path.unlink()
    fsync_directory(history)


def replace_cleanup_history(path: Path, value: dict[str, Any]) -> None:
    temporary = path.parent / f".{path.name}.replace-{secrets.token_hex(8)}"
    try:
        write_private(temporary, canonical_json(value))
        os.replace(temporary, path)
        fsync_directory(path.parent)
    except BaseException:
        if temporary.exists() or temporary.is_symlink():
            temporary.unlink()
        raise


def validate_cleanup_intent(value: Any, candidate_id: str) -> dict[str, Any]:
    required = {
        "schemaVersion", "operation", "candidateId", "product", "version", "restoredVersion",
        "activationHash", "sourceCommit", "sourceTree", "stageReceiptSha256",
        "rehearsalReceiptSha256", "activationClaimSha256", "activationResultSha256",
        "rollbackClaimSha256", "rollbackResultSha256", "trees", "deleteScope",
        "installedReleaseWillBeDeleted", "activeDeploymentWillChange",
        "auditTombstoneWillBePreserved",
    }
    if isinstance(value, dict) and "terminalOutcome" in value:
        required.add("terminalOutcome")
    # New intents always carry this field. It remains optional only so an
    # already-hash-bound legacy tombstone or interrupted claim can be read and
    # completed without rewriting its immutable bytes.
    if isinstance(value, dict) and "activeVersionAtCleanup" in value:
        required.add("activeVersionAtCleanup")
    intent = exact_keys(value, required, "release cleanup intent")
    required_hashes = {
        "activationHash", "stageReceiptSha256", "rehearsalReceiptSha256", "activationClaimSha256",
        "activationResultSha256",
    }
    terminal_outcome = intent.get("terminalOutcome", "rolled-back")
    rollback_hashes = (intent.get("rollbackClaimSha256"), intent.get("rollbackResultSha256"))
    if (
        intent["schemaVersion"] != 1 or intent["operation"] != "pixel-release-update-cleanup"
        or intent["candidateId"] != candidate_id or intent["product"] != "Pixel"
        or intent["installedReleaseWillBeDeleted"] is not False
        or intent["activeDeploymentWillChange"] is not False
        or intent["auditTombstoneWillBePreserved"] is not True
        or intent["deleteScope"] != [
            "verified-bundle-copy", "rehearsal-copy", "completed-activation-workspace",
        ]
        or any(not isinstance(intent[name], str) or HASH.fullmatch(intent[name]) is None for name in required_hashes)
        or terminal_outcome not in {"rolled-back", "activation-failed"}
        or terminal_outcome == "rolled-back" and any(
            not isinstance(item, str) or HASH.fullmatch(item) is None for item in rollback_hashes
        )
        or terminal_outcome == "activation-failed" and rollback_hashes != (None, None)
        or not isinstance(intent["sourceCommit"], str) or COMMIT.fullmatch(intent["sourceCommit"]) is None
        or not isinstance(intent["sourceTree"], str) or COMMIT.fullmatch(intent["sourceTree"]) is None
    ):
        raise UpdateError("release cleanup intent is invalid")
    semantic_version(intent["version"], "release cleanup version")
    semantic_version(intent["restoredVersion"], "release cleanup restored version")
    active_version_at_cleanup = intent.get("activeVersionAtCleanup")
    if active_version_at_cleanup is not None:
        semantic_version(active_version_at_cleanup, "release cleanup active version")
        if (
            active_version_at_cleanup == intent["version"]
            or version_tuple(active_version_at_cleanup) < version_tuple(intent["restoredVersion"])
        ):
            raise UpdateError("release cleanup active version is invalid")
    trees = exact_keys(intent["trees"], {"candidate", "rehearsal", "activation"}, "release cleanup trees")
    for name, shape in trees.items():
        shape = exact_keys(shape, {"shapeSha256", "entries", "regularBytes"}, f"release cleanup {name} tree")
        if (
            not isinstance(shape["shapeSha256"], str) or HASH.fullmatch(shape["shapeSha256"]) is None
            or not isinstance(shape["entries"], int) or isinstance(shape["entries"], bool)
            or not 1 <= shape["entries"] <= MAX_CLEANUP_ENTRIES
            or not isinstance(shape["regularBytes"], int) or isinstance(shape["regularBytes"], bool)
            or not 1 <= shape["regularBytes"] <= MAX_CLEANUP_BYTES
        ):
            raise UpdateError(f"release cleanup {name} tree shape is invalid")
    return intent


def ensure_private_cleanup_receipt(path: Path, label: str) -> None:
    info = path.lstat()
    if os.name != "nt" and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077):
        raise UpdateError(f"{label} permissions are unsafe")


def read_cleanup_claim(path: Path, cleanup_hash: str | None = None) -> tuple[dict[str, Any], bytes]:
    payload = read_regular(path / "CLEANUP.json", MAX_STAGE_RECEIPT, "release cleanup claim")
    ensure_private_cleanup_receipt(path / "CLEANUP.json", "release cleanup claim")
    claim = parse_json(payload, "release cleanup claim")
    claim_keys = {
        "schemaVersion", "operation", "candidateId", "product", "version", "restoredVersion",
        "activationHash", "sourceCommit", "sourceTree", "stageReceiptSha256",
        "rehearsalReceiptSha256", "activationClaimSha256", "activationResultSha256",
        "rollbackClaimSha256", "rollbackResultSha256", "trees", "deleteScope",
        "installedReleaseWillBeDeleted", "activeDeploymentWillChange", "auditTombstoneWillBePreserved",
        "status", "cleanupHash", "claimedAt", "claim", "boundary",
    }
    if isinstance(claim, dict) and "terminalOutcome" in claim:
        claim_keys.add("terminalOutcome")
    if isinstance(claim, dict) and "activeVersionAtCleanup" in claim:
        claim_keys.add("activeVersionAtCleanup")
    claim = exact_keys(claim, claim_keys, "release cleanup claim")
    claimed_at = claim.get("claimedAt")
    observed_hash = claim.get("cleanupHash")
    if (
        not isinstance(claimed_at, str)
        or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", claimed_at) is None
        or not isinstance(observed_hash, str) or HASH.fullmatch(observed_hash) is None
        or cleanup_hash is not None and not hmac.compare_digest(observed_hash, cleanup_hash)
        or claim.get("status") != "claimed"
        or claim.get("claim") != "atomic-quarantine-before-delete"
        or claim.get("boundary") != cleanup_boundary(claim)
    ):
        raise UpdateError("release cleanup claim is invalid")
    intent = {key: value for key, value in claim.items() if key not in {"status", "cleanupHash", "claimedAt", "claim", "boundary"}}
    validate_cleanup_intent(intent, path.name)
    if not hmac.compare_digest(sha256(canonical_json(intent)), observed_hash):
        raise UpdateError("release cleanup claim differs from its exact hash")
    if payload != canonical_json(claim):
        raise UpdateError("release cleanup claim is not canonically encoded")
    return claim, payload


def read_cleanup_history(path: Path, candidate_id: str) -> tuple[dict[str, Any], bytes]:
    payload = read_regular(path, MAX_STAGE_RECEIPT, "release cleanup history")
    ensure_private_cleanup_receipt(path, "release cleanup history")
    history = parse_json(payload, "release cleanup history")
    history_keys = {
        "schemaVersion", "operation", "candidateId", "product", "version", "restoredVersion",
        "activationHash", "sourceCommit", "sourceTree", "stageReceiptSha256",
        "rehearsalReceiptSha256", "activationClaimSha256", "activationResultSha256",
        "rollbackClaimSha256", "rollbackResultSha256", "trees", "deleteScope",
        "installedReleaseWillBeDeleted", "activeDeploymentWillChange", "auditTombstoneWillBePreserved",
        "status", "cleanupHash", "cleanedAt", "candidateEvidenceDeleted",
        "rehearsalEvidenceDeleted", "activationWorkspaceDeleted", "boundary",
    }
    if isinstance(history, dict) and "terminalOutcome" in history:
        history_keys.add("terminalOutcome")
    if isinstance(history, dict) and "activeVersionAtCleanup" in history:
        history_keys.add("activeVersionAtCleanup")
    history = exact_keys(history, history_keys, "release cleanup history")
    status_value = history.get("status")
    cleaned_at = history.get("cleanedAt")
    cleanup_hash = history.get("cleanupHash")
    complete = status_value == "cleaned"
    if (
        status_value not in {"deleting", "cleaned"} or history.get("boundary") != cleanup_boundary(history)
        or history.get("candidateEvidenceDeleted") is not complete
        or history.get("rehearsalEvidenceDeleted") is not complete
        or history.get("activationWorkspaceDeleted") is not complete
        or complete and (
            not isinstance(cleaned_at, str)
            or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", cleaned_at) is None
        )
        or not complete and cleaned_at is not None
        or not isinstance(cleanup_hash, str) or HASH.fullmatch(cleanup_hash) is None
    ):
        raise UpdateError("release cleanup history is invalid")
    intent = {key: value for key, value in history.items() if key not in {
        "status", "cleanupHash", "cleanedAt", "candidateEvidenceDeleted", "rehearsalEvidenceDeleted",
        "activationWorkspaceDeleted", "boundary",
    }}
    validate_cleanup_intent(intent, candidate_id)
    if not hmac.compare_digest(sha256(canonical_json(intent)), cleanup_hash):
        raise UpdateError("release cleanup history differs from its exact hash")
    if payload != canonical_json(history):
        raise UpdateError("release cleanup history is not canonically encoded")
    return history, payload


def validate_cleanup_quarantine(path: Path, claim: dict[str, Any], *, verify_shapes: bool = True) -> None:
    try:
        observed = {entry.name for entry in os.scandir(path)}
    except OSError as exc:
        raise UpdateError("release cleanup quarantine claim cannot be enumerated safely") from exc
    allowed = {"CLEANUP.json", "candidate", "rehearsal", "activation"}
    if "CLEANUP.json" not in observed or not observed <= allowed:
        raise UpdateError("release cleanup quarantine claim has an invalid file set")
    for name in ("candidate", "rehearsal", "activation"):
        target = path / name
        if verify_shapes and (target.exists() or target.is_symlink()):
            if cleanup_tree_shape(target) != claim["trees"][name]:
                raise UpdateError("quarantined release cleanup evidence differs from its claim")


def cleanup_preview(args: argparse.Namespace) -> dict[str, Any]:
    if sys.platform != "linux":
        raise UpdateError("release cleanup is supported only on qualified Linux hosts")
    if not isinstance(args.candidate_id, str) or re.fullmatch(
        r"pixel-[0-9]{1,6}(?:\.[0-9]{1,6}){2}-[0-9a-f]{64}", args.candidate_id,
    ) is None:
        raise UpdateError("release cleanup candidate ID is invalid")
    if not isinstance(args.activation_hash, str) or HASH.fullmatch(args.activation_hash) is None:
        raise UpdateError("release activation hash is invalid")
    if not args.staging_root.is_absolute() or not args.active_version_file.is_absolute() or not args.rollback_marker.is_absolute():
        raise UpdateError("release cleanup paths must be absolute")
    with exclusive_stage_lock(args.staging_root):
        ensure_private_directory(args.staging_root, "release staging root")
        quarantine, history = cleanup_directories(args.staging_root)
        validate_cleanup_store(quarantine, history)
        final_quarantine = quarantine / args.candidate_id
        history_path = history / f"{args.candidate_id}.json"
        if history_path.exists() or history_path.is_symlink():
            history_value, _history_payload = read_cleanup_history(history_path, args.candidate_id)
            if not hmac.compare_digest(history_value["activationHash"], args.activation_hash):
                raise UpdateError("release cleanup history differs from the requested activation")
            live_roots = [
                args.staging_root / name / args.candidate_id
                for name in ("candidates", "rehearsals", "activations")
            ]
            if history_value["status"] == "cleaned" and any(path.exists() or path.is_symlink() for path in live_roots):
                raise UpdateError("cleaned release candidate ID was reused after its immutable tombstone")
            interrupted = history_value["status"] == "deleting"
            if final_quarantine.exists() or final_quarantine.is_symlink():
                ensure_private_directory(final_quarantine, "release cleanup quarantine claim")
                observed = {entry.name for entry in os.scandir(final_quarantine)}
                if observed:
                    claim, _claim_payload = read_cleanup_claim(final_quarantine, history_value["cleanupHash"])
                    validate_cleanup_quarantine(final_quarantine, claim, verify_shapes=False)
                    expected_history = cleanup_history_value(
                        claim, None if interrupted else history_value["cleanedAt"],
                    )
                    if expected_history != history_value:
                        raise UpdateError("release cleanup quarantine differs from its audit history")
            return {
                "schemaVersion": 1, "status": "interrupted" if interrupted else "already-cleaned",
                "candidateId": args.candidate_id,
                "product": "Pixel", "version": history_value.get("version"),
                "restoredVersion": history_value.get("restoredVersion"),
                "cleanupHash": history_value.get("cleanupHash"), "confirmationRequired": interrupted,
                "installedReleaseWillBeDeleted": False, "activeDeploymentWillChange": False,
                "auditTombstoneWillBePreserved": True,
                **({"activeVersionAtCleanup": history_value["activeVersionAtCleanup"]}
                   if "activeVersionAtCleanup" in history_value else {}),
                **({"terminalOutcome": history_value["terminalOutcome"]} if "terminalOutcome" in history_value else {}),
                "boundary": cleanup_boundary(history_value),
            }
        if final_quarantine.exists() or final_quarantine.is_symlink():
            ensure_private_directory(final_quarantine, "release cleanup quarantine claim")
            claim, _payload = read_cleanup_claim(final_quarantine)
            validate_cleanup_quarantine(final_quarantine, claim)
            if not hmac.compare_digest(claim["activationHash"], args.activation_hash):
                raise UpdateError("release cleanup claim differs from the requested activation")
            return {
                "schemaVersion": 1, "status": "interrupted", "candidateId": args.candidate_id,
                "product": "Pixel", "version": claim["version"], "restoredVersion": claim["restoredVersion"],
                "cleanupHash": claim["cleanupHash"], "confirmationRequired": True,
                "installedReleaseWillBeDeleted": False, "activeDeploymentWillChange": False,
                "auditTombstoneWillBePreserved": True,
                **({"activeVersionAtCleanup": claim["activeVersionAtCleanup"]}
                   if "activeVersionAtCleanup" in claim else {}),
                **({"terminalOutcome": claim["terminalOutcome"]} if "terminalOutcome" in claim else {}),
                "boundary": cleanup_boundary(claim),
            }
        intent, _roots = completed_cleanup_context(args)
        cleanup_hash = sha256(canonical_json(intent))
        return {
            **intent,
            "status": "ready",
            "cleanupHash": cleanup_hash,
            "confirmationRequired": True,
            "boundary": cleanup_boundary(intent),
        }


def cleanup_completed_update(args: argparse.Namespace) -> dict[str, Any]:
    if not args.confirm:
        raise UpdateError("release cleanup requires --confirm")
    if not isinstance(args.candidate_id, str) or re.fullmatch(
        r"pixel-[0-9]{1,6}(?:\.[0-9]{1,6}){2}-[0-9a-f]{64}", args.candidate_id,
    ) is None:
        raise UpdateError("release cleanup candidate ID is invalid")
    if not isinstance(args.cleanup_hash, str) or HASH.fullmatch(args.cleanup_hash) is None:
        raise UpdateError("release cleanup hash is invalid")
    if not isinstance(args.activation_hash, str) or HASH.fullmatch(args.activation_hash) is None:
        raise UpdateError("release activation hash is invalid")
    if sys.platform != "linux":
        raise UpdateError("release cleanup is supported only on qualified Linux hosts")
    if not args.staging_root.is_absolute() or not args.active_version_file.is_absolute() or not args.rollback_marker.is_absolute():
        raise UpdateError("release cleanup paths must be absolute")
    with exclusive_stage_lock(args.staging_root):
        ensure_private_directory(args.staging_root, "release staging root")
        quarantine, history = cleanup_directories(args.staging_root)
        history_entries = validate_cleanup_store(quarantine, history)
        remove_cleanup_history_temporaries(history)
        final_quarantine = quarantine / args.candidate_id
        history_path = history / f"{args.candidate_id}.json"
        source_roots = {
            "candidate": args.staging_root / "candidates" / args.candidate_id,
            "rehearsal": args.staging_root / "rehearsals" / args.candidate_id,
            "activation": args.staging_root / "activations" / args.candidate_id,
        }
        if len(history_entries) >= MAX_CLEANUP_HISTORY and not history_path.exists():
            raise UpdateError("release cleanup history retention limit is reached")
        if history_path.exists() or history_path.is_symlink():
            history_value, _history_payload = read_cleanup_history(history_path, args.candidate_id)
            if (
                not hmac.compare_digest(history_value["cleanupHash"], args.cleanup_hash)
                or not hmac.compare_digest(history_value["activationHash"], args.activation_hash)
            ):
                raise UpdateError("release cleanup history differs from the exact cleanup")
            deleting = history_value["status"] == "deleting"
            if not deleting and any(path.exists() or path.is_symlink() for path in source_roots.values()):
                raise UpdateError("cleaned release candidate ID was reused after its immutable tombstone")
            if deleting and any(path.exists() or path.is_symlink() for path in source_roots.values()):
                raise UpdateError("release cleanup checkpoint conflicts with live update evidence")
            if final_quarantine.exists() or final_quarantine.is_symlink():
                ensure_private_directory(final_quarantine, "release cleanup quarantine claim")
                observed = {entry.name for entry in os.scandir(final_quarantine)}
                if observed:
                    claim, _claim_payload = read_cleanup_claim(final_quarantine, args.cleanup_hash)
                    validate_cleanup_quarantine(final_quarantine, claim, verify_shapes=False)
                    if not hmac.compare_digest(claim["activationHash"], args.activation_hash):
                        raise UpdateError("release cleanup claim differs from the requested activation")
                    expected_history = cleanup_history_value(
                        claim, None if deleting else history_value["cleanedAt"],
                    )
                    if expected_history != history_value:
                        raise UpdateError("release cleanup quarantine differs from its audit history")
                    for name in ("candidate", "rehearsal", "activation"):
                        safe_remove_cleanup_tree(final_quarantine / name)
                    (final_quarantine / "CLEANUP.json").unlink()
                final_quarantine.rmdir()
                fsync_directory(quarantine)
            if not deleting:
                return {**history_value, "status": "already-cleaned"}
            cleaned_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            history_value = finalize_cleanup_history_value(history_value, cleaned_at)
            replace_cleanup_history(history_path, history_value)
            return history_value
        if final_quarantine.exists() or final_quarantine.is_symlink():
            ensure_private_directory(final_quarantine, "release cleanup quarantine claim")
            claim, _claim_payload = read_cleanup_claim(final_quarantine, args.cleanup_hash)
            validate_cleanup_quarantine(final_quarantine, claim)
            if not hmac.compare_digest(claim["activationHash"], args.activation_hash):
                raise UpdateError("release cleanup claim differs from the requested activation")
        else:
            intent, _roots = completed_cleanup_context(args)
            expected_hash = sha256(canonical_json(intent))
            if not hmac.compare_digest(expected_hash, args.cleanup_hash):
                raise UpdateError("release cleanup hash differs from the current completed update")
            temporary = quarantine / f".{args.candidate_id}.cleanup-{secrets.token_hex(8)}"
            os.mkdir(temporary, 0o700)
            try:
                claimed_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
                claim = cleanup_claim_value(intent, expected_hash, claimed_at)
                write_private(temporary / "CLEANUP.json", canonical_json(claim))
                fsync_directory(temporary)
                rename_directory_noreplace(temporary, final_quarantine)
                fsync_directory(quarantine)
            except BaseException:
                remove_private_tree(temporary)
                raise
        for name, source in source_roots.items():
            destination = final_quarantine / name
            source_exists = source.exists() or source.is_symlink()
            destination_exists = destination.exists() or destination.is_symlink()
            if source_exists and destination_exists:
                raise UpdateError("release cleanup evidence exists in both live and quarantine locations")
            if source_exists:
                if cleanup_tree_shape(source) != claim["trees"][name]:
                    raise UpdateError("release cleanup evidence changed after exact confirmation")
                rename_directory_noreplace(source, destination)
                fsync_directory(source.parent)
                fsync_directory(final_quarantine)
            elif destination_exists:
                if cleanup_tree_shape(destination) != claim["trees"][name]:
                    raise UpdateError("quarantined release cleanup evidence differs from its claim")
            else:
                raise UpdateError("release cleanup evidence is missing before audit history publication")
        history_value = cleanup_history_value(claim)
        write_private(history_path, canonical_json(history_value))
        fsync_directory(history)
        for name in ("candidate", "rehearsal", "activation"):
            safe_remove_cleanup_tree(final_quarantine / name)
        (final_quarantine / "CLEANUP.json").unlink()
        final_quarantine.rmdir()
        fsync_directory(quarantine)
        cleaned_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        history_value = finalize_cleanup_history_value(history_value, cleaned_at)
        replace_cleanup_history(history_path, history_value)
    return history_value


def recover_incomplete_rehearsals(rehearsals: Path) -> None:
    try:
        entries = list(os.scandir(rehearsals))
    except OSError as exc:
        raise UpdateError("release rehearsal directory cannot be enumerated safely") from exc
    for entry in entries:
        if not entry.name.startswith("."):
            continue
        if re.fullmatch(
            r"\.pixel-[0-9]{1,6}(?:\.[0-9]{1,6}){2}-[0-9a-f]{64}\.rehearsal-[0-9a-f]{16}",
            entry.name,
        ) is None:
            raise UpdateError("release rehearsal directory contains an unrecognized entry")
        remove_private_tree(Path(entry.path))


def rehearse(args: argparse.Namespace) -> dict[str, Any]:
    if not args.confirm:
        raise UpdateError("release rehearsal requires --confirm")
    if sys.platform != "linux":
        raise UpdateError("release rehearsal is supported only on qualified Linux hosts")
    if not args.staging_root.is_absolute():
        raise UpdateError("release staging root path must be absolute")
    with exclusive_stage_lock(args.staging_root):
        (
            _candidate, envelope, envelope_bytes, artifacts, _signature, manifest,
            stage_receipt_bytes, current, host,
        ) = prepared_candidate(args.staging_root, args.candidate_id, args.allowed_signers, args.identity)
        rehearsals = args.staging_root / "rehearsals"
        ensure_private_directory(rehearsals, "release rehearsal directory", create=True)
        fsync_directory(args.staging_root)
        recover_incomplete_rehearsals(rehearsals)
        final = rehearsals / args.candidate_id
        if final.exists() or final.is_symlink():
            return validate_existing_rehearsal(
                final, envelope, envelope_bytes, stage_receipt_bytes, args.identity, current, host,
                args.candidate_id, manifest,
            )
        try:
            entries = list(os.scandir(rehearsals))
        except OSError as exc:
            raise UpdateError("release rehearsal directory cannot be enumerated safely") from exc
        if any(
            not entry.is_dir(follow_symlinks=False)
            or re.fullmatch(r"pixel-[0-9]{1,6}(?:\.[0-9]{1,6}){2}-[0-9a-f]{64}", entry.name) is None
            for entry in entries
        ):
            raise UpdateError("release rehearsal directory contains an unrecognized entry")
        if len(entries) >= MAX_STAGED_CANDIDATES:
            raise UpdateError("release rehearsal retention limit is reached")
        temporary = rehearsals / f".{args.candidate_id}.rehearsal-{secrets.token_hex(8)}"
        os.mkdir(temporary, 0o700)
        source = temporary / "source"
        os.mkdir(source, 0o700)
        try:
            tree_hash, file_count, extracted_bytes = extract_archive_privately(
                artifacts["archive"], envelope["version"], source,
            )
            toolchain, checks = candidate_syntax_checks(source, manifest)
            rehearsed_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            receipt = rehearsal_receipt(
                envelope, envelope_bytes, stage_receipt_bytes, args.identity, current, host, args.candidate_id,
                toolchain, checks, tree_hash, file_count, extracted_bytes, rehearsed_at,
            )
            write_private(
                temporary / "REHEARSAL.json",
                (json.dumps(receipt, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8"),
            )
            fsync_directory(source)
            fsync_directory(temporary)
            rename_directory_noreplace(temporary, final)
            fsync_directory(rehearsals)
        except BaseException:
            remove_private_tree(temporary)
            raise
    return receipt


def signing_git(args: list[str], *, binary: bool = False, check: bool = True) -> bytes | str:
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    environment.update({"GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"})
    try:
        result = subprocess.run(
            ["git", *args], cwd=ROOT, env=environment,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateError("release signing Git proof is unavailable") from exc
    if check and result.returncode:
        raise UpdateError("release signing Git proof failed")
    if binary:
        return result.stdout
    try:
        return result.stdout.decode("utf-8", "strict").strip()
    except UnicodeError as exc:
        raise UpdateError("release signing Git proof is not UTF-8") from exc


def validate_signing_source(envelope: dict[str, Any]) -> None:
    try:
        repository = Path(str(signing_git(["rev-parse", "--show-toplevel"]))).resolve(strict=True)
        expected_repository = ROOT.resolve(strict=True)
    except OSError as exc:
        raise UpdateError("release signing repository identity is unavailable") from exc
    if repository != expected_repository:
        raise UpdateError("release signing must run from the exact Pixel repository")
    status = str(signing_git(["status", "--porcelain=v1", "--untracked-files=all"]))
    if status:
        raise UpdateError("release signing requires a clean Pixel worktree")
    source_commit = str(signing_git(["rev-parse", "--verify", "HEAD^{commit}"]))
    source_tree = str(signing_git(["rev-parse", "--verify", "HEAD^{tree}"]))
    if not hmac.compare_digest(source_commit, envelope["sourceCommit"]):
        raise UpdateError("release source commit is not the signing repository HEAD")
    if not hmac.compare_digest(source_tree, envelope["sourceTree"]):
        raise UpdateError("release source tree is not the signing repository HEAD tree")
    qualification = envelope["qualificationSourceCommit"]
    if hmac.compare_digest(qualification, source_commit):
        raise UpdateError("release qualification source must precede the evidence-only release commit")
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    environment.update({"GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"})
    try:
        exists = subprocess.run(
            ["git", "cat-file", "-e", f"{qualification}^{{commit}}"], cwd=ROOT, env=environment,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=15,
        )
        ancestor = subprocess.run(
            ["git", "merge-base", "--is-ancestor", qualification, source_commit], cwd=ROOT, env=environment,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateError("release qualification ancestry proof is unavailable") from exc
    if exists.returncode:
        raise UpdateError("release qualification source commit is unknown")
    if ancestor.returncode:
        raise UpdateError("release qualification source is not an ancestor of the release")
    changed_bytes = signing_git(
        ["diff", "--no-ext-diff", "--name-only", "-z", "--no-renames", "--diff-filter=ACDMRTUXB", qualification, source_commit, "--"],
        binary=True,
    )
    assert isinstance(changed_bytes, bytes)
    try:
        changed = {item.decode("utf-8", "strict") for item in changed_bytes.split(b"\0") if item}
    except UnicodeError as exc:
        raise UpdateError("release evidence-only path proof is not UTF-8") from exc
    if not changed:
        raise UpdateError("release evidence-only commit contains no evidence change")
    # The release and documentation generators deterministically mirror every
    # compatibility binding into the six required inert files below. A status
    # transition can also change the generated support-matrix summary; a pure
    # source-commit rebind does not. Keep every path explicit: a broad docs/
    # allowance would let an unqualified executable or operator procedure ride
    # an evidence-only release commit.
    required_evidence = {
        "OPENCLAW-COMPATIBILITY.json",
        "OPENCLAW-COMPATIBILITY.md",
        f"LIVE-AUDIT-{envelope['version']}.md",
        "docs/status.json",
        "docs/status.md",
        "docs/releases/evidence-index.md",
    }
    optional_generated_evidence = {
        "docs/reference/support-matrix.md",
    }
    allowed_evidence = required_evidence | optional_generated_evidence
    unexpected = changed - allowed_evidence
    if unexpected:
        raise UpdateError("release changed non-evidence files after qualification")
    if not required_evidence.issubset(changed):
        raise UpdateError("release evidence-only commit must update every required evidence file")
    for relative in sorted(changed):
        entry = signing_git(["ls-tree", "-z", source_commit, "--", relative], binary=True)
        assert isinstance(entry, bytes)
        expected_suffix = b"\t" + relative.encode("utf-8") + b"\0"
        if not entry.startswith(b"100644 blob ") or not entry.endswith(expected_suffix) or entry.count(b"\0") != 1:
            raise UpdateError("release evidence file is not an ordinary tracked file")


def normalized_release_mode(relative: str) -> int:
    executable = (
        relative == "pixel"
        or (relative.startswith("scripts/") and "/" not in relative[len("scripts/"):] and relative.endswith(".sh"))
        or (
            relative.startswith("workspace-template/scripts/")
            and "/" not in relative[len("workspace-template/scripts/"):]
            and relative.endswith(".sh")
        )
        or (relative.startswith("tests/") and "/" not in relative[len("tests/"):] and relative.endswith(".sh"))
        or (
            relative.startswith("tests/fixtures/bin/")
            and "/" not in relative[len("tests/fixtures/bin/"):]
        )
    )
    return 0o755 if executable else 0o644


def validate_signing_archive(envelope: dict[str, Any], archive_payload: bytes, sbom_payload: bytes) -> None:
    listing = signing_git(
        ["ls-tree", "-r", "-z", "--full-tree", envelope["sourceCommit"]], binary=True,
    )
    assert isinstance(listing, bytes)
    blobs: dict[str, str] = {}
    for raw in listing.split(b"\0"):
        if not raw:
            continue
        try:
            identity, path_bytes = raw.split(b"\t", 1)
            mode, kind, object_id = identity.split(b" ", 2)
            relative = path_bytes.decode("utf-8", "strict")
            pure = PurePosixPath(relative)
        except (ValueError, UnicodeError) as exc:
            raise UpdateError("release Git tree contains an unsafe entry") from exc
        if (
            mode not in {b"100644", b"100755"} or kind != b"blob" or not re.fullmatch(rb"[0-9a-f]{40}", object_id)
            or not relative or "\\" in relative or pure.is_absolute() or pure.as_posix() != relative
            or "." in pure.parts or ".." in pure.parts or any(ord(character) < 32 for character in relative)
            or relative in blobs
        ):
            raise UpdateError("release Git tree contains an unsafe entry")
        blobs[relative] = object_id.decode("ascii")
        if len(blobs) > MAX_ARCHIVE_MEMBERS:
            raise UpdateError("release Git tree has too many files")
    if not blobs or "SBOM.cdx.json" in blobs or "RELEASE-IDENTITY.json" in blobs:
        raise UpdateError("release Git tree conflicts with generated release metadata")

    prefix = f"pixel-{envelope['version']}"
    observed_files: dict[str, str] = {}
    observed_directories: set[str] = set()
    release_identity_payload: bytes | None = None
    total = 0
    count = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_payload), mode="r:gz") as archive:
            for member in archive:
                count += 1
                if count > MAX_ARCHIVE_MEMBERS:
                    raise UpdateError("release archive has too many members")
                pure = safe_archive_path(member.name, envelope["version"])
                relative = PurePosixPath(*pure.parts[1:]).as_posix() if len(pure.parts) > 1 else ""
                if member.isdir():
                    if stat.S_IMODE(member.mode) != 0o755:
                        raise UpdateError("release archive directory mode differs from packaging policy")
                    observed_directories.add(member.name.rstrip("/"))
                    continue
                if not member.isreg() or not relative or relative in observed_files:
                    raise UpdateError("release archive source tree is not exact")
                expected_mode = normalized_release_mode(relative)
                if stat.S_IMODE(member.mode) != expected_mode:
                    raise UpdateError("release archive file mode differs from packaging policy")
                total += member.size
                if member.size < 0 or member.size > MAX_ARCHIVE_MEMBER or total > MAX_ARCHIVE_UNPACKED:
                    raise UpdateError("release archive source tree exceeds its size bound")
                handle = archive.extractfile(member)
                if handle is None:
                    raise UpdateError("release archive source file is unreadable")
                payload = handle.read(member.size + 1)
                if len(payload) != member.size:
                    raise UpdateError("release archive source file is truncated")
                observed_files[relative] = sha256(payload)
                if relative == "RELEASE-IDENTITY.json":
                    release_identity_payload = payload
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise UpdateError("release archive source tree is malformed") from exc

    expected_files = set(blobs) | {"RELEASE-IDENTITY.json", "SBOM.cdx.json"}
    if set(observed_files) != expected_files:
        raise UpdateError("release archive file set differs from the exact Git source")
    if not hmac.compare_digest(observed_files["SBOM.cdx.json"], sha256(sbom_payload)):
        raise UpdateError("release archive generated SBOM differs from the signed SBOM")

    def git_blob(relative: str, label: str) -> bytes:
        object_id = blobs.get(relative)
        if object_id is None:
            raise UpdateError(f"release Git source has no {label}")
        payload = signing_git(["cat-file", "blob", object_id], binary=True)
        assert isinstance(payload, bytes)
        if len(payload) > MAX_ARCHIVE_MEMBER:
            raise UpdateError(f"release Git {label} exceeds its size bound")
        return payload

    manifest_payload = git_blob("RELEASE-MANIFEST.json", "release manifest")
    compatibility_payload = git_blob("OPENCLAW-COMPATIBILITY.json", "compatibility matrix")
    qualification_matrix_payload = git_blob("QUALIFICATION-MATRIX.json", "qualification matrix")
    if (
        not hmac.compare_digest(sha256(manifest_payload), envelope["releaseManifestSha256"])
        or not hmac.compare_digest(sha256(compatibility_payload), envelope["compatibilitySha256"])
    ):
        raise UpdateError("release identity source manifests differ from the signed envelope")
    manifest = parse_json(manifest_payload, "release manifest")
    compatibility = parse_json(compatibility_payload, "compatibility matrix")
    manifest_plugins = manifest.get("openclawPlugins")
    if (
        manifest.get("pixel") != envelope["version"]
        or not isinstance(manifest.get("openclaw"), str)
        or not isinstance(manifest_plugins, dict)
        or any(not isinstance(key, str) or not isinstance(value, str) for key, value in manifest_plugins.items())
    ):
        raise UpdateError("release identity manifest binding is invalid")
    combinations = compatibility.get("combinations")
    if not isinstance(combinations, list):
        raise UpdateError("release identity compatibility binding is invalid")
    matching = [
        item for item in combinations
        if isinstance(item, dict)
        and item.get("pixel") == envelope["version"]
        and item.get("openclaw") == manifest["openclaw"]
        and item.get("plugins") == manifest_plugins
    ]
    if len(matching) != 1:
        raise UpdateError("release identity has no unique compatibility binding")
    record = matching[0]
    evidence = record.get("evidence")
    if not isinstance(evidence, dict) or evidence.get("sourceCommit") != envelope["qualificationSourceCommit"]:
        raise UpdateError("release identity qualification source differs from the signed envelope")
    expected_release_identity = {
        "schemaVersion": 1,
        "kind": "pixel-release-source-identity",
        "pixel": envelope["version"],
        "source": {
            "state": "git-clean",
            "commit": envelope["sourceCommit"],
            "tree": envelope["sourceTree"],
        },
        "manifests": {
            "releaseSha256": envelope["releaseManifestSha256"],
            "compatibilitySha256": envelope["compatibilitySha256"],
            "qualificationMatrixSha256": sha256(qualification_matrix_payload),
        },
        "qualification": {
            "recordStatus": record.get("status"),
            "sourceCommit": evidence.get("sourceCommit"),
            "qualifiedAt": record.get("qualifiedAt"),
            "liveAudit": evidence.get("liveAudit"),
            "relationship": "qualified-ancestor",
        },
        "boundary": RELEASE_IDENTITY_BOUNDARY,
    }
    if release_identity_payload is None or not hmac.compare_digest(
        canonical_json(parse_json(release_identity_payload, "release identity")),
        canonical_json(expected_release_identity),
    ):
        raise UpdateError("release archive generated identity differs from the exact signed source")
    for relative, object_id in blobs.items():
        payload = signing_git(["cat-file", "blob", object_id], binary=True)
        assert isinstance(payload, bytes)
        if len(payload) > MAX_ARCHIVE_MEMBER or not hmac.compare_digest(observed_files[relative], sha256(payload)):
            raise UpdateError("release archive content differs from the exact Git source")

    expected_directories = {prefix}
    for relative in expected_files:
        parts = PurePosixPath(relative).parts[:-1]
        for index in range(1, len(parts) + 1):
            expected_directories.add(f"{prefix}/{'/'.join(parts[:index])}")
    if observed_directories != expected_directories:
        raise UpdateError("release archive directory set differs from the exact Git source")


def sign_bundle(
    args: argparse.Namespace, *, compatibility_status: str, namespace: str,
    signature_suffix: str, receipt_status: str, key_label: str,
) -> dict[str, Any]:
    if not args.confirm:
        raise UpdateError(f"{key_label} signing requires --confirm")
    if not args.envelope.is_absolute() or not args.signing_key.is_absolute():
        raise UpdateError(f"update envelope and {key_label} signing-key paths must be absolute")
    envelope, envelope_bytes, artifacts, _manifest = load_unsigned_bundle(
        args.envelope, compatibility_status=compatibility_status,
    )
    validate_signing_source(envelope)
    validate_signing_archive(envelope, artifacts["archive"], artifacts["sbom"])
    key = read_regular(args.signing_key, MAX_TRUST, f"{key_label} signing key", trust_anchor=True)
    if os.name != "nt" and stat.S_IMODE(args.signing_key.stat().st_mode) & 0o077:
        raise UpdateError(f"{key_label} signing key must not be group or world accessible")
    signature_path = Path(f"{args.envelope}{signature_suffix}")
    if signature_path.exists() or signature_path.is_symlink():
        raise UpdateError(f"{key_label} signature already exists")
    with tempfile.TemporaryDirectory(prefix="pixel-update-sign-") as temporary:
        temporary_root = Path(temporary)
        restrict_windows_private_directory(temporary_root)
        private_key = temporary_root / "release-key"
        unsigned_envelope = temporary_root / args.envelope.name
        write_private(private_key, key)
        write_private(unsigned_envelope, envelope_bytes)
        key_probe = subprocess.run(
            ["ssh-keygen", "-y", "-f", str(private_key)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        if key_probe.returncode or not key_probe.stdout.startswith((b"ssh-ed25519 ", b"sk-ssh-ed25519@openssh.com ")):
            raise UpdateError("release signing key must be an Ed25519 private key")
        source_key_probe = subprocess.run(
            ["ssh-keygen", "-y", "-f", str(args.signing_key)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        if source_key_probe.returncode or not hmac.compare_digest(source_key_probe.stdout.strip(), key_probe.stdout.strip()):
            raise UpdateError("release signing key source is unprotected or changed after validation")
        result = subprocess.run(
            ["ssh-keygen", "-q", "-Y", "sign", "-f", str(private_key), "-n", namespace, str(unsigned_envelope)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        if result.returncode:
            raise UpdateError(f"{key_label} signing failed")
        generated_signature = Path(f"{unsigned_envelope}.sig")
        signature = read_regular(generated_signature, MAX_SIGNATURE, "release signature")
        validate_signature_envelope(signature)
    write_private(signature_path, signature)
    return {
        "schemaVersion": 1,
        "status": receipt_status,
        "version": envelope["version"],
        "sourceCommit": envelope["sourceCommit"],
        "qualificationSourceCommit": envelope["qualificationSourceCommit"],
        "envelopeSha256": sha256(envelope_bytes),
        "signatureNamespace": namespace,
        "publicationAuthority": compatibility_status == "supported",
        "activationAuthority": compatibility_status == "supported",
    }


def sign(args: argparse.Namespace) -> dict[str, Any]:
    return sign_bundle(
        args,
        compatibility_status="supported",
        namespace=NAMESPACE,
        signature_suffix=".sig",
        receipt_status="signed",
        key_label="release",
    )


def qualification_sign(args: argparse.Namespace) -> dict[str, Any]:
    return sign_bundle(
        args,
        compatibility_status="candidate",
        namespace=QUALIFICATION_NAMESPACE,
        signature_suffix=".qualification.sig",
        receipt_status="qualification-signed",
        key_label="qualification",
    )


def qualification_authority_fields() -> dict[str, Any]:
    return {
        "publicationAuthority": False,
        "productionActivationAuthority": False,
        "compatibilityMutationAuthority": False,
        "productionStateChanged": False,
    }


def _qualification_relation(baseline: str, candidate: str, minimum: str) -> tuple[str, bool]:
    candidate_tuple = version_tuple(candidate)
    baseline_tuple = version_tuple(baseline)
    minimum_tuple = version_tuple(minimum)
    relation = "upgrade" if candidate_tuple > baseline_tuple else "same" if candidate_tuple == baseline_tuple else "downgrade"
    return relation, relation == "upgrade" and baseline_tuple >= minimum_tuple


def validate_qualification_root_separation(
    qualification_root: Path, production_install_root: Path, label: str = "qualification root",
) -> None:
    if not qualification_root.is_absolute():
        raise UpdateError(f"{label} path must be absolute")
    if not production_install_root.is_absolute():
        raise UpdateError("production install root must be absolute")
    ensure_private_directory(production_install_root, "production install root")
    qual_resolved = qualification_root.resolve()
    production_resolved = production_install_root.resolve(strict=True)
    if (
        qual_resolved == production_resolved
        or qual_resolved.is_relative_to(production_resolved)
        or production_resolved.is_relative_to(qual_resolved)
    ):
        raise UpdateError(f"{label} must not equal, contain, or be contained by the production install root")


def validate_utc_seconds_timestamp(value: Any, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", value,
    ):
        raise UpdateError(f"{label} timestamp is invalid")
    return value


QUALIFICATION_ROOT_TOP_LEVEL = {
    ".stage.lock", QUALIFICATION_ROOT_FILE, "candidates", "rehearsals", "activations", "runs",
}


def enumerate_qualification_root(root: Path) -> set[str]:
    try:
        entries = list(os.scandir(root))
    except OSError as exc:
        raise UpdateError("qualification root cannot be enumerated safely") from exc
    observed: set[str] = set()
    for entry in entries:
        if entry.name not in QUALIFICATION_ROOT_TOP_LEVEL:
            raise UpdateError("qualification root contains an unexpected entry")
        try:
            info = entry.stat(follow_symlinks=False)
        except OSError as exc:
            raise UpdateError("qualification root entry is unsafe") from exc
        if entry.name == QUALIFICATION_ROOT_FILE:
            if stat.S_ISLNK(info.st_mode):
                raise UpdateError("attestation is unavailable or unsafe")
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise UpdateError("qualification root attestation must be one bounded regular single-link file")
            if os.name != "nt" and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077):
                raise UpdateError("qualification root attestation ownership or permissions are unsafe")
        elif entry.name == ".stage.lock":
            if stat.S_ISLNK(info.st_mode):
                raise UpdateError("qualification root lock is unsafe")
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise UpdateError("qualification root lock must be one bounded regular single-link file")
            if os.name != "nt" and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077):
                raise UpdateError("qualification root lock ownership or permissions are unsafe")
        else:
            if stat.S_ISLNK(info.st_mode):
                raise UpdateError("qualification root must not contain symbolic links")
            if not stat.S_ISDIR(info.st_mode):
                raise UpdateError("qualification root state directory is not a real directory")
            ensure_private_directory(Path(entry.path), f"qualification root {entry.name}")
        observed.add(entry.name)
    return observed


def qualification_root_attestation(
    root: Path, baseline: str, envelope: dict[str, Any], envelope_bytes: bytes,
    production_install_root: Path, created_at: str,
) -> dict[str, Any]:
    validate_utc_seconds_timestamp(created_at, "qualification root attestation")
    return {
        "schemaVersion": 1,
        "operation": "pixel-release-qualification-root",
        "product": "Pixel",
        "root": str(root.resolve()),
        "productionInstallRoot": str(production_install_root.resolve()),
        "baselineVersion": baseline,
        "candidateVersion": envelope["version"],
        "candidateEnvelopeSha256": sha256(envelope_bytes),
        "createdAt": created_at,
        "boundary": QUALIFICATION_ROOT_BOUNDARY,
    }


def initialize_qualification_root(
    root: Path, baseline: str, envelope: dict[str, Any], envelope_bytes: bytes,
    production_install_root: Path,
) -> tuple[dict[str, Any], str]:
    validate_qualification_root_separation(root, production_install_root)
    ensure_private_directory(root, "qualification root")
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    attestation = qualification_root_attestation(
        root, baseline, envelope, envelope_bytes, production_install_root, created_at,
    )
    attestation_path = root / QUALIFICATION_ROOT_FILE
    observed = enumerate_qualification_root(root)
    if QUALIFICATION_ROOT_FILE in observed:
        existing_bytes = read_regular(attestation_path, MAX_STAGE_RECEIPT, "qualification root attestation")
        existing = parse_json(existing_bytes, "qualification root attestation")
        expected = qualification_root_attestation(
            root, baseline, envelope, envelope_bytes, production_install_root, existing.get("createdAt"),
        )
        if existing != expected:
            raise UpdateError("qualification root is not freshly initialized for this exact request")
        return existing, sha256(existing_bytes)
    if observed != {".stage.lock"}:
        raise UpdateError("qualification root must be freshly initialized with no unexpected state")
    payload = canonical_json(attestation)
    write_private(attestation_path, payload)
    fsync_directory(root)
    return attestation, sha256(payload)


def read_qualification_root(
    root: Path, baseline: str, envelope: dict[str, Any], envelope_bytes: bytes,
    production_install_root: Path,
) -> tuple[dict[str, Any], str]:
    validate_qualification_root_separation(root, production_install_root)
    ensure_private_directory(root, "qualification root")
    enumerate_qualification_root(root)
    attestation_path = root / QUALIFICATION_ROOT_FILE
    payload = read_regular(attestation_path, MAX_STAGE_RECEIPT, "qualification root attestation")
    value = parse_json(payload, "qualification root attestation")
    expected = qualification_root_attestation(
        root, baseline, envelope, envelope_bytes, production_install_root, value.get("createdAt"),
    )
    if value != expected:
        raise UpdateError("qualification root attestation differs from the verified bundle")
    return value, sha256(payload)


def qualification_stage_receipt(
    envelope: dict[str, Any], envelope_bytes: bytes, signature: bytes, identity: str,
    baseline: str, host: dict[str, Any], candidate_id: str, attestation_hash: str,
    prepared_at: str, status: str = "prepared",
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "status": status,
        "operation": "pixel-release-qualification-stage",
        "candidateId": candidate_id,
        "product": "Pixel",
        "version": envelope["version"],
        "baselineVersion": baseline,
        "relation": "upgrade",
        "channel": envelope["channel"],
        "qualifierIdentity": identity,
        "sourceCommit": envelope["sourceCommit"],
        "sourceTree": envelope["sourceTree"],
        "qualificationSourceCommit": envelope["qualificationSourceCommit"],
        "compatibilityStatus": "candidate",
        "signatureName": f"pixel-{envelope['version']}.update.json.qualification.sig",
        "host": host,
        "artifacts": envelope["artifacts"],
        "envelopeSha256": sha256(envelope_bytes),
        "signatureSha256": sha256(signature),
        "qualificationRootAttestationSha256": attestation_hash,
        "preparedAt": prepared_at,
        "candidateCodeExtracted": False,
        "candidateCodeExecuted": False,
        **qualification_authority_fields(),
        "boundary": QUALIFICATION_PREPARE_BOUNDARY,
    }


def qualification_validate_existing_stage(
    candidate: Path, envelope: dict[str, Any], envelope_bytes: bytes, artifacts: dict[str, bytes],
    signature: bytes, identity: str, baseline: str, host: dict[str, Any], candidate_id: str,
    attestation_hash: str,
) -> dict[str, Any]:
    ensure_private_directory(candidate, "qualification candidate")
    filenames = {
        "QUALIFICATION-STAGED.json",
        Path(envelope["artifacts"]["archive"]["name"]).name,
        Path(envelope["artifacts"]["sbom"]["name"]).name,
        Path(envelope["artifacts"]["provenance"]["name"]).name,
        f"pixel-{envelope['version']}.update.json",
        f"pixel-{envelope['version']}.update.json.qualification.sig",
    }
    try:
        observed = {entry.name for entry in os.scandir(candidate)}
    except OSError as exc:
        raise UpdateError("qualification candidate cannot be enumerated safely") from exc
    if observed != filenames:
        raise UpdateError("qualification candidate file set is invalid")
    expected_payloads = {
        f"pixel-{envelope['version']}.update.json": (envelope_bytes, MAX_ENVELOPE),
        f"pixel-{envelope['version']}.update.json.qualification.sig": (signature, MAX_SIGNATURE),
        **{
            envelope["artifacts"][kind]["name"]: (payload, ARTIFACT_LIMITS[kind])
            for kind, payload in artifacts.items()
        },
    }
    for name, (expected, maximum) in expected_payloads.items():
        path = candidate / name
        actual = read_regular(path, maximum, f"qualification {name}")
        info = path.lstat()
        if os.name != "nt" and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077):
            raise UpdateError("qualification candidate file permissions are unsafe")
        if len(actual) != len(expected) or not hmac.compare_digest(sha256(actual), sha256(expected)):
            raise UpdateError("qualification candidate differs from the verified bundle")
    receipt_path = candidate / "QUALIFICATION-STAGED.json"
    receipt_bytes = read_regular(receipt_path, MAX_STAGE_RECEIPT, "qualification stage receipt")
    receipt_info = receipt_path.lstat()
    if os.name != "nt" and (receipt_info.st_uid != os.geteuid() or stat.S_IMODE(receipt_info.st_mode) & 0o077):
        raise UpdateError("qualification stage receipt permissions are unsafe")
    receipt = parse_json(receipt_bytes, "qualification stage receipt")
    prepared_at = receipt.get("preparedAt")
    if not isinstance(prepared_at, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", prepared_at,
    ):
        raise UpdateError("qualification stage timestamp is invalid")
    expected = qualification_stage_receipt(
        envelope, envelope_bytes, signature, identity, baseline, host, candidate_id,
        attestation_hash, prepared_at,
    )
    if receipt != expected:
        raise UpdateError("qualification stage receipt differs from the verified bundle")
    return {**receipt, "status": "already-prepared"}


def recover_incomplete_qualification_stage(path: Path) -> None:
    match = re.fullmatch(
        r"\.pixel-([0-9]{1,6}(?:\.[0-9]{1,6}){2})-[0-9a-f]{64}\.stage-[0-9a-f]{16}",
        path.name,
    )
    if match is None:
        raise UpdateError("qualification candidate directory contains an unrecognized entry")
    ensure_private_directory(path, "incomplete qualification staging directory")
    version = match.group(1)
    allowed = {
        "QUALIFICATION-STAGED.json",
        f"pixel-{version}.tar.gz",
        f"pixel-{version}.cdx.json",
        f"pixel-{version}.intoto.jsonl",
        f"pixel-{version}.update.json",
        f"pixel-{version}.update.json.qualification.sig",
    }
    try:
        entries = list(os.scandir(path))
    except OSError as exc:
        raise UpdateError("incomplete qualification staging directory cannot be enumerated safely") from exc
    if len(entries) > len(allowed):
        raise UpdateError("incomplete qualification staging directory has too many files")
    for entry in entries:
        try:
            info = entry.stat(follow_symlinks=False)
        except OSError as exc:
            raise UpdateError("incomplete qualification staging entry is unsafe") from exc
        if (
            entry.name not in allowed or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or (os.name != "nt" and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077))
        ):
            raise UpdateError("incomplete qualification staging entry is unsafe")
    for entry in entries:
        os.unlink(entry.path)
    path.rmdir()


def qualification_prepare(args: argparse.Namespace) -> dict[str, Any]:
    if not args.confirm:
        raise UpdateError("qualification staging requires --confirm")
    if sys.platform != "linux":
        raise UpdateError("qualification staging is supported only on qualified Linux hosts")
    if not args.qualification_root.is_absolute():
        raise UpdateError("qualification root path must be absolute")
    envelope, envelope_bytes, artifacts, _manifest, signature = load_qualification_bundle(
        args.envelope, args.allowed_signers, args.identity,
    )
    baseline = semantic_version(args.baseline_version, "qualification baseline version")
    relation, eligible = _qualification_relation(baseline, envelope["version"], envelope["minimumUpgradablePixel"])
    if relation != "upgrade" or not eligible:
        raise UpdateError("only an exact forward qualification candidate can be prepared")
    host = qualified_host(envelope)
    validate_qualification_root_separation(
        args.qualification_root, args.production_install_root,
    )
    ensure_private_directory(args.qualification_root, "qualification root", create=True)
    envelope_hash = sha256(envelope_bytes)
    candidate_id = f"pixel-{envelope['version']}-{envelope_hash}"
    candidates = args.qualification_root / "candidates"
    with exclusive_stage_lock(args.qualification_root):
        attestation, attestation_hash = initialize_qualification_root(
            args.qualification_root, baseline, envelope, envelope_bytes, args.production_install_root,
        )
        ensure_private_directory(candidates, "qualification candidate directory", create=True)
        fsync_directory(args.qualification_root)
        final = candidates / candidate_id
        if final.exists() or final.is_symlink():
            return qualification_validate_existing_stage(
                final, envelope, envelope_bytes, artifacts, signature, args.identity, baseline,
                host, candidate_id, attestation_hash,
            )
        try:
            entries = list(os.scandir(candidates))
        except OSError as exc:
            raise UpdateError("qualification candidate directory cannot be enumerated safely") from exc
        for entry in entries:
            if entry.name.startswith("."):
                recover_incomplete_qualification_stage(Path(entry.path))
        try:
            entries = list(os.scandir(candidates))
        except OSError as exc:
            raise UpdateError("qualification candidate directory cannot be re-enumerated safely") from exc
        if any(
            not entry.is_dir(follow_symlinks=False)
            or not re.fullmatch(r"pixel-[0-9]{1,6}(?:\.[0-9]{1,6}){2}-[0-9a-f]{64}", entry.name)
            for entry in entries
        ):
            raise UpdateError("qualification candidate directory contains an unrecognized entry")
        if len(entries) >= MAX_STAGED_CANDIDATES:
            raise UpdateError("qualification candidate retention limit is reached")
        temporary = candidates / f".{candidate_id}.stage-{secrets.token_hex(8)}"
        os.mkdir(temporary, 0o700)
        filenames = {
            f"pixel-{envelope['version']}.update.json",
            f"pixel-{envelope['version']}.update.json.qualification.sig",
            *(specification["name"] for specification in envelope["artifacts"].values()),
            "QUALIFICATION-STAGED.json",
        }
        prepared_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        receipt = qualification_stage_receipt(
            envelope, envelope_bytes, signature, args.identity, baseline, host, candidate_id,
            attestation_hash, prepared_at,
        )
        try:
            write_private(temporary / f"pixel-{envelope['version']}.update.json", envelope_bytes)
            write_private(temporary / f"pixel-{envelope['version']}.update.json.qualification.sig", signature)
            for kind, payload in artifacts.items():
                write_private(temporary / envelope["artifacts"][kind]["name"], payload)
            write_private(temporary / "QUALIFICATION-STAGED.json", canonical_json(receipt))
            fsync_directory(temporary)
            rename_directory_noreplace(temporary, final)
            fsync_directory(candidates)
        except BaseException:
            remove_incomplete_stage(temporary, filenames)
            raise
    return receipt


def qualification_prepared_candidate(
    qualification_root: Path, candidate_id: str, allowed_signers: Path, identity: str,
    baseline: str, production_install_root: Path,
) -> tuple[Path, dict[str, Any], bytes, dict[str, bytes], bytes, dict[str, Any], bytes, dict[str, Any]]:
    match = re.fullmatch(r"pixel-([0-9]{1,6}(?:\.[0-9]{1,6}){2})-([0-9a-f]{64})", candidate_id)
    if match is None:
        raise UpdateError("qualification candidate ID is invalid")
    ensure_private_directory(qualification_root, "qualification root")
    candidates = qualification_root / "candidates"
    ensure_private_directory(candidates, "qualification candidate directory")
    candidate = candidates / candidate_id
    ensure_private_directory(candidate, "qualification candidate")
    version, expected_envelope_hash = match.groups()
    envelope_path = candidate / f"pixel-{version}.update.json"
    envelope, envelope_bytes, artifacts, manifest, signature = load_qualification_bundle(
        envelope_path, allowed_signers, identity,
    )
    if envelope["version"] != version or not hmac.compare_digest(sha256(envelope_bytes), expected_envelope_hash):
        raise UpdateError("qualification candidate ID differs from its qualified envelope")
    relation, eligible = _qualification_relation(baseline, envelope["version"], envelope["minimumUpgradablePixel"])
    if relation != "upgrade" or not eligible:
        raise UpdateError("qualification candidate is no longer an exact forward upgrade")
    host = qualified_host(envelope)
    _attestation, attestation_hash = read_qualification_root(
        qualification_root, baseline, envelope, envelope_bytes, production_install_root,
    )
    qualification_validate_existing_stage(
        candidate, envelope, envelope_bytes, artifacts, signature, identity, baseline, host,
        candidate_id, attestation_hash,
    )
    stage_receipt_bytes = read_regular(
        candidate / "QUALIFICATION-STAGED.json", MAX_STAGE_RECEIPT, "qualification stage receipt",
    )
    return candidate, envelope, envelope_bytes, artifacts, signature, manifest, stage_receipt_bytes, host


def qualification_rehearsal_receipt(
    envelope: dict[str, Any], envelope_bytes: bytes, stage_receipt_bytes: bytes, identity: str,
    baseline: str, host: dict[str, Any], candidate_id: str, toolchain: dict[str, Any],
    checks: dict[str, Any], tree_hash: str, file_count: int, extracted_bytes: int,
    attestation_hash: str, rehearsed_at: str, status: str = "rehearsed",
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "status": status,
        "operation": "pixel-release-qualification-rehearsal",
        "candidateId": candidate_id,
        "product": "Pixel",
        "version": envelope["version"],
        "baselineVersion": baseline,
        "relation": "upgrade",
        "channel": envelope["channel"],
        "qualifierIdentity": identity,
        "sourceCommit": envelope["sourceCommit"],
        "sourceTree": envelope["sourceTree"],
        "host": host,
        "toolchain": toolchain,
        "checks": checks,
        "envelopeSha256": sha256(envelope_bytes),
        "stageReceiptSha256": sha256(stage_receipt_bytes),
        "extractedTreeSha256": tree_hash,
        "extractedFileCount": file_count,
        "extractedBytes": extracted_bytes,
        "qualificationRootAttestationSha256": attestation_hash,
        "rehearsedAt": rehearsed_at,
        "candidateCodeExtracted": True,
        "candidateCodeParsed": True,
        "candidateCodeExecuted": False,
        "activeDeploymentChanged": False,
        "networkUsed": False,
        **qualification_authority_fields(),
        "boundary": QUALIFICATION_REHEARSAL_BOUNDARY,
    }


def qualification_validate_existing_rehearsal(
    rehearsal: Path, envelope: dict[str, Any], envelope_bytes: bytes, stage_receipt_bytes: bytes,
    identity: str, baseline: str, host: dict[str, Any], candidate_id: str, manifest: dict[str, Any],
    attestation_hash: str,
) -> dict[str, Any]:
    ensure_private_directory(rehearsal, "qualification rehearsal")
    try:
        observed = {entry.name for entry in os.scandir(rehearsal)}
    except OSError as exc:
        raise UpdateError("qualification rehearsal cannot be enumerated safely") from exc
    if observed != {"source", "QUALIFICATION-REHEARSAL.json"}:
        raise UpdateError("qualification rehearsal file set is invalid")
    source = rehearsal / "source"
    tree_hash, file_count, extracted_bytes = tree_identity(source)
    toolchain, checks = candidate_syntax_checks(source, manifest)
    receipt_path = rehearsal / "QUALIFICATION-REHEARSAL.json"
    receipt_bytes = read_regular(receipt_path, MAX_STAGE_RECEIPT, "qualification rehearsal receipt")
    receipt_info = receipt_path.lstat()
    if os.name != "nt" and (receipt_info.st_uid != os.geteuid() or stat.S_IMODE(receipt_info.st_mode) & 0o077):
        raise UpdateError("qualification rehearsal receipt permissions are unsafe")
    receipt = parse_json(receipt_bytes, "qualification rehearsal receipt")
    rehearsed_at = receipt.get("rehearsedAt")
    if not isinstance(rehearsed_at, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", rehearsed_at,
    ):
        raise UpdateError("qualification rehearsal timestamp is invalid")
    expected = qualification_rehearsal_receipt(
        envelope, envelope_bytes, stage_receipt_bytes, identity, baseline, host, candidate_id,
        toolchain, checks, tree_hash, file_count, extracted_bytes, attestation_hash, rehearsed_at,
    )
    if receipt != expected:
        raise UpdateError("qualification rehearsal receipt differs from the revalidated candidate")
    return {**receipt, "status": "already-rehearsed"}


def qualification_rehearse(args: argparse.Namespace) -> dict[str, Any]:
    if not args.confirm:
        raise UpdateError("qualification rehearsal requires --confirm")
    if sys.platform != "linux":
        raise UpdateError("qualification rehearsal is supported only on qualified Linux hosts")
    if not args.qualification_root.is_absolute():
        raise UpdateError("qualification root path must be absolute")
    with exclusive_stage_lock(args.qualification_root):
        (
            _candidate, envelope, envelope_bytes, artifacts, _signature, manifest,
            stage_receipt_bytes, host,
        ) = qualification_prepared_candidate(
            args.qualification_root, args.candidate_id, args.allowed_signers, args.identity,
            args.baseline_version, args.production_install_root,
        )
        baseline = semantic_version(args.baseline_version, "qualification baseline version")
        _attestation, attestation_hash = read_qualification_root(
            args.qualification_root, baseline, envelope, envelope_bytes, args.production_install_root,
        )
        rehearsals = args.qualification_root / "rehearsals"
        ensure_private_directory(rehearsals, "qualification rehearsal directory", create=True)
        fsync_directory(args.qualification_root)
        recover_incomplete_rehearsals(rehearsals)
        final = rehearsals / args.candidate_id
        if final.exists() or final.is_symlink():
            return qualification_validate_existing_rehearsal(
                final, envelope, envelope_bytes, stage_receipt_bytes, args.identity, baseline, host,
                args.candidate_id, manifest, attestation_hash,
            )
        try:
            entries = list(os.scandir(rehearsals))
        except OSError as exc:
            raise UpdateError("qualification rehearsal directory cannot be enumerated safely") from exc
        if any(
            not entry.is_dir(follow_symlinks=False)
            or re.fullmatch(r"pixel-[0-9]{1,6}(?:\.[0-9]{1,6}){2}-[0-9a-f]{64}", entry.name) is None
            for entry in entries
        ):
            raise UpdateError("qualification rehearsal directory contains an unrecognized entry")
        if len(entries) >= MAX_STAGED_CANDIDATES:
            raise UpdateError("qualification rehearsal retention limit is reached")
        temporary = rehearsals / f".{args.candidate_id}.rehearsal-{secrets.token_hex(8)}"
        os.mkdir(temporary, 0o700)
        source = temporary / "source"
        os.mkdir(source, 0o700)
        try:
            tree_hash, file_count, extracted_bytes = extract_archive_privately(
                artifacts["archive"], envelope["version"], source,
            )
            toolchain, checks = candidate_syntax_checks(source, manifest)
            rehearsed_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            receipt = qualification_rehearsal_receipt(
                envelope, envelope_bytes, stage_receipt_bytes, args.identity, baseline, host, args.candidate_id,
                toolchain, checks, tree_hash, file_count, extracted_bytes, attestation_hash, rehearsed_at,
            )
            write_private(temporary / "QUALIFICATION-REHEARSAL.json", canonical_json(receipt))
            fsync_directory(source)
            fsync_directory(temporary)
            rename_directory_noreplace(temporary, final)
            fsync_directory(rehearsals)
        except BaseException:
            remove_private_tree(temporary)
            raise
    return receipt


def qualification_rehearsed_candidate(
    qualification_root: Path, candidate_id: str, allowed_signers: Path, identity: str,
    baseline: str, production_install_root: Path,
) -> tuple[dict[str, Any], bytes, bytes, dict[str, Any], dict[str, Any], Path, bytes, dict[str, Any]]:
    (
        _candidate, envelope, envelope_bytes, _artifacts, _signature, manifest,
        stage_receipt_bytes, host,
    ) = qualification_prepared_candidate(
        qualification_root, candidate_id, allowed_signers, identity, baseline, production_install_root,
    )
    _attestation, attestation_hash = read_qualification_root(
        qualification_root, baseline, envelope, envelope_bytes, production_install_root,
    )
    rehearsals = qualification_root / "rehearsals"
    ensure_private_directory(rehearsals, "qualification rehearsal directory")
    rehearsal = rehearsals / candidate_id
    receipt = qualification_validate_existing_rehearsal(
        rehearsal, envelope, envelope_bytes, stage_receipt_bytes, identity, baseline, host,
        candidate_id, manifest, attestation_hash,
    )
    receipt.pop("status", None)
    receipt["status"] = "rehearsed"
    receipt_bytes = read_regular(
        rehearsal / "QUALIFICATION-REHEARSAL.json", MAX_STAGE_RECEIPT, "qualification rehearsal receipt",
    )
    return envelope, envelope_bytes, stage_receipt_bytes, host, manifest, rehearsal / "source", receipt_bytes, receipt


def qualification_activation_intent(
    envelope: dict[str, Any], envelope_bytes: bytes, stage_receipt_bytes: bytes,
    rehearsal_receipt_bytes: bytes, rehearsal_receipt_value: dict[str, Any], identity: str,
    baseline: str, host: dict[str, Any], candidate_id: str, attestation_hash: str,
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "operation": "pixel-release-qualification-activation",
        "candidateId": candidate_id,
        "product": "Pixel",
        "version": envelope["version"],
        "baselineVersion": baseline,
        "relation": "upgrade",
        "channel": envelope["channel"],
        "qualifierIdentity": identity,
        "sourceCommit": envelope["sourceCommit"],
        "sourceTree": envelope["sourceTree"],
        "host": host,
        "envelopeSha256": sha256(envelope_bytes),
        "stageReceiptSha256": sha256(stage_receipt_bytes),
        "rehearsalReceiptSha256": sha256(rehearsal_receipt_bytes),
        "extractedTreeSha256": rehearsal_receipt_value["extractedTreeSha256"],
        "qualificationRootAttestationSha256": attestation_hash,
        "candidateCodeWillExecute": False,
        "activeDeploymentWillChange": False,
        "networkMayBeUsed": False,
    }


def qualification_activation_preview_value(intent: dict[str, Any]) -> dict[str, Any]:
    activation_hash = sha256(canonical_json(intent))
    return {
        **intent,
        "status": "ready",
        "activationHash": activation_hash,
        "confirmationRequired": "repeat-the-full-activation-hash-with-confirm",
        "candidateCodeExecuted": False,
        "activeDeploymentChanged": False,
        "networkUsed": False,
        **qualification_authority_fields(),
        "boundary": QUALIFICATION_ACTIVATION_BOUNDARY,
    }


def qualification_activation_claim_value(
    intent: dict[str, Any], activation_hash: str, claimed_at: str,
) -> dict[str, Any]:
    return {
        **intent,
        "status": "claimed",
        "activationHash": activation_hash,
        "claimedAt": claimed_at,
        "claim": "single-use-atomic-no-replace",
        "candidateCodeCopied": True,
        "candidateCodeExecuted": False,
        "activeDeploymentChanged": False,
        "networkUsed": False,
        **qualification_authority_fields(),
        "boundary": QUALIFICATION_ACTIVATION_BOUNDARY,
    }


def qualification_activation_preview(args: argparse.Namespace) -> dict[str, Any]:
    if sys.platform != "linux":
        raise UpdateError("qualification activation is supported only on qualified Linux hosts")
    if not args.qualification_root.is_absolute():
        raise UpdateError("qualification root path must be absolute")
    baseline = semantic_version(args.baseline_version, "qualification baseline version")
    with exclusive_stage_lock(args.qualification_root):
        (
            envelope, envelope_bytes, stage_receipt_bytes, host, _manifest, _source,
            rehearsal_receipt_bytes, rehearsal_receipt_value,
        ) = qualification_rehearsed_candidate(
            args.qualification_root, args.candidate_id, args.allowed_signers, args.identity,
            baseline, args.production_install_root,
        )
        _attestation, attestation_hash = read_qualification_root(
            args.qualification_root, baseline, envelope, envelope_bytes,
            args.production_install_root,
        )
        final = args.qualification_root / "activations" / args.candidate_id
        if final.exists() or final.is_symlink():
            raise UpdateError("qualification activation has already been claimed")
        intent = qualification_activation_intent(
            envelope, envelope_bytes, stage_receipt_bytes, rehearsal_receipt_bytes,
            rehearsal_receipt_value, args.identity, baseline, host, args.candidate_id,
            attestation_hash,
        )
        return qualification_activation_preview_value(intent)


def qualification_claim_activation(args: argparse.Namespace) -> dict[str, Any]:
    if not args.confirm:
        raise UpdateError("qualification activation requires --confirm")
    if not isinstance(args.activation_hash, str) or HASH.fullmatch(args.activation_hash) is None:
        raise UpdateError("qualification activation hash is invalid")
    if not isinstance(args.candidate_id, str) or re.fullmatch(
        r"pixel-[0-9]{1,6}(?:\.[0-9]{1,6}){2}-[0-9a-f]{64}", args.candidate_id,
    ) is None:
        raise UpdateError("qualification candidate ID is invalid")
    if sys.platform != "linux":
        raise UpdateError("qualification activation is supported only on qualified Linux hosts")
    if not args.qualification_root.is_absolute():
        raise UpdateError("qualification root path must be absolute")
    baseline = semantic_version(args.baseline_version, "qualification baseline version")
    with exclusive_stage_lock(args.qualification_root):
        (
            envelope, envelope_bytes, stage_receipt_bytes, host, manifest, source,
            rehearsal_receipt_bytes, rehearsal_receipt_value,
        ) = qualification_rehearsed_candidate(
            args.qualification_root, args.candidate_id, args.allowed_signers, args.identity,
            baseline, args.production_install_root,
        )
        _attestation, attestation_hash = read_qualification_root(
            args.qualification_root, baseline, envelope, envelope_bytes,
            args.production_install_root,
        )
        intent = qualification_activation_intent(
            envelope, envelope_bytes, stage_receipt_bytes, rehearsal_receipt_bytes,
            rehearsal_receipt_value, args.identity, baseline, host, args.candidate_id,
            attestation_hash,
        )
        preview = qualification_activation_preview_value(intent)
        if not hmac.compare_digest(preview["activationHash"], args.activation_hash):
            raise UpdateError("qualification activation hash differs from the current verified preview")
        activations = args.qualification_root / "activations"
        ensure_private_directory(activations, "qualification activation directory", create=True)
        fsync_directory(args.qualification_root)
        recover_incomplete_activations(activations)
        final = activations / args.candidate_id
        if final.exists() or final.is_symlink():
            raise UpdateError("qualification activation has already been claimed")
        try:
            entries = list(os.scandir(activations))
        except OSError as exc:
            raise UpdateError("qualification activation directory cannot be enumerated safely") from exc
        if any(
            not entry.is_dir(follow_symlinks=False)
            or re.fullmatch(r"pixel-[0-9]{1,6}(?:\.[0-9]{1,6}){2}-[0-9a-f]{64}", entry.name) is None
            for entry in entries
        ):
            raise UpdateError("qualification activation directory contains an unrecognized entry")
        if len(entries) >= MAX_STAGED_CANDIDATES:
            raise UpdateError("qualification activation retention limit is reached")
        temporary = activations / f".{args.candidate_id}.activation-{secrets.token_hex(8)}"
        os.mkdir(temporary, 0o700)
        copied_source = temporary / "source"
        os.mkdir(copied_source, 0o700)
        try:
            copied_identity = copy_private_tree(source, copied_source)
            expected_identity = (
                rehearsal_receipt_value["extractedTreeSha256"],
                rehearsal_receipt_value["extractedFileCount"],
                rehearsal_receipt_value["extractedBytes"],
            )
            if copied_identity != expected_identity:
                raise UpdateError("private qualification copy differs from the verified rehearsal")
            copied_toolchain, copied_checks = candidate_syntax_checks(copied_source, manifest)
            if copied_toolchain != rehearsal_receipt_value["toolchain"] or copied_checks != rehearsal_receipt_value["checks"]:
                raise UpdateError("private qualification copy differs from the rehearsed compatibility result")
            claimed_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            claim = qualification_activation_claim_value(intent, preview["activationHash"], claimed_at)
            write_private(temporary / "QUALIFICATION-ACTIVATION.json", canonical_json(claim))
            fsync_directory(copied_source)
            fsync_directory(temporary)
            rename_directory_noreplace(temporary, final)
            fsync_directory(activations)
        except BaseException:
            remove_private_tree(temporary)
            raise
    return claim


def validate_qualification_activation_claim(
    activation: Path, intent: dict[str, Any], activation_hash: str,
    *, expected_files: set[str] | None = None,
) -> tuple[dict[str, Any], bytes]:
    ensure_private_directory(activation, "qualification activation")
    try:
        observed = {entry.name for entry in os.scandir(activation)}
    except OSError as exc:
        raise UpdateError("qualification activation cannot be enumerated safely") from exc
    expected = {"source", "QUALIFICATION-ACTIVATION.json"} if expected_files is None else set(expected_files)
    if observed != expected:
        raise UpdateError("qualification activation file set is invalid")
    receipt_path = activation / "QUALIFICATION-ACTIVATION.json"
    receipt_bytes = read_regular(receipt_path, MAX_STAGE_RECEIPT, "qualification activation claim")
    receipt_info = receipt_path.lstat()
    if os.name != "nt" and (receipt_info.st_uid != os.geteuid() or stat.S_IMODE(receipt_info.st_mode) & 0o077):
        raise UpdateError("qualification activation claim permissions are unsafe")
    claim = parse_json(receipt_bytes, "qualification activation claim")
    claimed_at = claim.get("claimedAt")
    if not isinstance(claimed_at, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", claimed_at,
    ):
        raise UpdateError("qualification activation timestamp is invalid")
    expected = qualification_activation_claim_value(intent, activation_hash, claimed_at)
    if claim != expected:
        raise UpdateError("qualification activation claim differs from the verified bundle")
    return claim, receipt_bytes


def recover_incomplete_runs(runs: Path) -> None:
    try:
        entries = list(os.scandir(runs))
    except OSError as exc:
        raise UpdateError("qualification run directory cannot be enumerated safely") from exc
    for entry in entries:
        if not entry.name.startswith("."):
            continue
        if re.fullmatch(
            r"\.pixel-[0-9]{1,6}(?:\.[0-9]{1,6}){2}-[0-9a-f]{64}\.run-[0-9a-f]{16}",
            entry.name,
        ) is None:
            raise UpdateError("qualification run directory contains an unrecognized entry")
        remove_private_tree(Path(entry.path))


def qualification_host_run_artifacts(
    envelope: dict[str, Any], envelope_bytes: bytes, stage_receipt_bytes: bytes,
    rehearsal_receipt_bytes: bytes, rehearsal_receipt_value: dict[str, Any],
    claim: dict[str, Any], claim_bytes: bytes, activation_hash: str, identity: str,
    baseline: str, host: dict[str, Any], candidate_id: str, attestation_hash: str,
    rehashed_identity: tuple[str, int, int], acquired_at: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    tree_hash, file_count, extracted_bytes = rehashed_identity
    harness = {
        "schemaVersion": 1,
        "status": "acquired",
        "operation": "pixel-release-qualification-host-run",
        "candidateId": candidate_id,
        "product": "Pixel",
        "version": envelope["version"],
        "baselineVersion": baseline,
        "relation": "upgrade",
        "channel": envelope["channel"],
        "qualifierIdentity": identity,
        "sourceCommit": envelope["sourceCommit"],
        "sourceTree": envelope["sourceTree"],
        "host": host,
        "envelopeSha256": sha256(envelope_bytes),
        "stageReceiptSha256": sha256(stage_receipt_bytes),
        "rehearsalReceiptSha256": sha256(rehearsal_receipt_bytes),
        "activationReceiptSha256": sha256(claim_bytes),
        "activationHash": activation_hash,
        "claimedAt": claim["claimedAt"],
        "extractedTreeSha256": rehearsal_receipt_value["extractedTreeSha256"],
        "rehashedTreeSha256": tree_hash,
        "rehashedFileCount": file_count,
        "rehashedBytes": extracted_bytes,
        "qualificationRootAttestationSha256": attestation_hash,
        "acquiredAt": acquired_at,
        "candidateCodeCopied": True,
        "candidateCodeExecuted": False,
        "activeDeploymentChanged": False,
        "networkUsed": False,
        "terminalPromotionEvidence": False,
        **qualification_authority_fields(),
        "boundary": QUALIFICATION_HOST_RUN_BOUNDARY,
    }
    tombstone = {
        "schemaVersion": 1,
        "operation": "pixel-release-qualification-execution-tombstone",
        "candidateId": candidate_id,
        "product": "Pixel",
        "version": envelope["version"],
        "baselineVersion": baseline,
        "sourceCommit": envelope["sourceCommit"],
        "sourceTree": envelope["sourceTree"],
        "activationHash": activation_hash,
        "claimedAt": claim["claimedAt"],
        "extractedTreeSha256": rehearsal_receipt_value["extractedTreeSha256"],
        "rehashedTreeSha256": tree_hash,
        "candidateCodeExecuted": False,
        "terminalPromotionEvidence": False,
        "executionObserved": False,
        "networkUsed": False,
        **qualification_authority_fields(),
        "boundary": QUALIFICATION_EXECUTION_TOMBSTONE_BOUNDARY,
    }
    return harness, tombstone


def qualification_acquisition_marker_value(
    harness: dict[str, Any], harness_bytes: bytes,
    tombstone: dict[str, Any], tombstone_bytes: bytes,
    claim: dict[str, Any], claim_bytes: bytes, activation_hash: str,
    rehashed_identity: tuple[str, int, int], acquired_at: str,
) -> dict[str, Any]:
    tree_hash, file_count, extracted_bytes = rehashed_identity
    return {
        "schemaVersion": 1,
        "status": "acquired",
        "operation": "pixel-release-qualification-acquisition-marker",
        "candidateId": harness["candidateId"],
        "product": harness["product"],
        "version": harness["version"],
        "baselineVersion": harness["baselineVersion"],
        "relation": harness["relation"],
        "channel": harness["channel"],
        "qualifierIdentity": harness["qualifierIdentity"],
        "sourceCommit": harness["sourceCommit"],
        "sourceTree": harness["sourceTree"],
        "host": harness["host"],
        "envelopeSha256": harness["envelopeSha256"],
        "stageReceiptSha256": harness["stageReceiptSha256"],
        "rehearsalReceiptSha256": harness["rehearsalReceiptSha256"],
        "activationReceiptSha256": sha256(claim_bytes),
        "claimedAt": claim["claimedAt"],
        "activationHash": activation_hash,
        "extractedTreeSha256": harness["extractedTreeSha256"],
        "rehashedTreeSha256": tree_hash,
        "rehashedFileCount": file_count,
        "rehashedBytes": extracted_bytes,
        "harnessRunSha256": sha256(harness_bytes),
        "executionTombstoneSha256": sha256(tombstone_bytes),
        "acquiredAt": acquired_at,
        "candidateCodeCopied": True,
        "candidateCodeExecuted": False,
        "activeDeploymentChanged": False,
        "networkUsed": False,
        "terminalPromotionEvidence": False,
        **qualification_authority_fields(),
        "boundary": QUALIFICATION_ACQUISITION_BOUNDARY,
    }


def qualification_host_run(args: argparse.Namespace) -> dict[str, Any]:
    if not args.confirm:
        raise UpdateError("qualification host-run acquisition requires --confirm")
    if not isinstance(args.candidate_id, str) or re.fullmatch(
        r"pixel-[0-9]{1,6}(?:\.[0-9]{1,6}){2}-[0-9a-f]{64}", args.candidate_id,
    ) is None:
        raise UpdateError("qualification candidate ID is invalid")
    if sys.platform != "linux":
        raise UpdateError("qualification host-run acquisition is supported only on qualified Linux hosts")
    if not args.qualification_root.is_absolute():
        raise UpdateError("qualification root path must be absolute")
    baseline = semantic_version(args.baseline_version, "qualification baseline version")
    with exclusive_stage_lock(args.qualification_root):
        (
            envelope, envelope_bytes, stage_receipt_bytes, host, _manifest, _source,
            rehearsal_receipt_bytes, rehearsal_receipt_value,
        ) = qualification_rehearsed_candidate(
            args.qualification_root, args.candidate_id, args.allowed_signers, args.identity,
            baseline, args.production_install_root,
        )
        _attestation, attestation_hash = read_qualification_root(
            args.qualification_root, baseline, envelope, envelope_bytes,
            args.production_install_root,
        )
        intent = qualification_activation_intent(
            envelope, envelope_bytes, stage_receipt_bytes, rehearsal_receipt_bytes,
            rehearsal_receipt_value, args.identity, baseline, host, args.candidate_id,
            attestation_hash,
        )
        activation_hash = qualification_activation_preview_value(intent)["activationHash"]
        activations = args.qualification_root / "activations"
        activation = activations / args.candidate_id
        acquisition_marker = activation / QUALIFICATION_ACQUISITION_FILE
        if acquisition_marker.exists() or acquisition_marker.is_symlink():
            raise UpdateError("qualification host run has already been acquired or requires recovery")
        claim, claim_bytes = validate_qualification_activation_claim(
            activation, intent, activation_hash,
        )
        rehashed_identity = tree_identity(activation / "source")
        expected_identity = (
            rehearsal_receipt_value["extractedTreeSha256"],
            rehearsal_receipt_value["extractedFileCount"],
            rehearsal_receipt_value["extractedBytes"],
        )
        if rehashed_identity != expected_identity:
            raise UpdateError("staged qualification source tree differs from the verified rehearsal")
        runs = args.qualification_root / "runs"
        ensure_private_directory(runs, "qualification run directory", create=True)
        fsync_directory(args.qualification_root)
        recover_incomplete_runs(runs)
        final = runs / args.candidate_id
        if final.exists() or final.is_symlink():
            raise UpdateError("qualification host run has already been acquired")
        try:
            entries = list(os.scandir(runs))
        except OSError as exc:
            raise UpdateError("qualification run directory cannot be enumerated safely") from exc
        if any(
            not entry.is_dir(follow_symlinks=False)
            or re.fullmatch(r"pixel-[0-9]{1,6}(?:\.[0-9]{1,6}){2}-[0-9a-f]{64}", entry.name) is None
            for entry in entries
        ):
            raise UpdateError("qualification run directory contains an unrecognized entry")
        if len(entries) >= MAX_STAGED_CANDIDATES:
            raise UpdateError("qualification run retention limit is reached")
        temporary = runs / f".{args.candidate_id}.run-{secrets.token_hex(8)}"
        os.mkdir(temporary, 0o700)
        marker_temporary = None
        try:
            acquired_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            harness, tombstone = qualification_host_run_artifacts(
                envelope, envelope_bytes, stage_receipt_bytes, rehearsal_receipt_bytes,
                rehearsal_receipt_value, claim, claim_bytes, activation_hash, args.identity,
                baseline, host, args.candidate_id, attestation_hash, rehashed_identity, acquired_at,
            )
            harness_bytes = canonical_json(harness)
            tombstone_bytes = canonical_json(tombstone)
            marker = qualification_acquisition_marker_value(
                harness, harness_bytes, tombstone, tombstone_bytes, claim, claim_bytes,
                activation_hash, rehashed_identity, acquired_at,
            )
            marker_temporary = activation / f".{args.candidate_id}.acquisition-{secrets.token_hex(8)}"
            write_private(marker_temporary, canonical_json(marker))
            fsync_directory(activation)
            rename_noreplace(marker_temporary, acquisition_marker, "qualification acquisition marker")
            marker_temporary = None
            fsync_directory(activation)
            write_private(temporary / "HARNESS-RUN.json", harness_bytes)
            write_private(temporary / "EXECUTION-TOMBSTONE.json", tombstone_bytes)
            fsync_directory(temporary)
            rename_directory_noreplace(temporary, final)
            fsync_directory(runs)
        except BaseException:
            remove_private_tree(temporary)
            if marker_temporary is not None and (marker_temporary.exists() or marker_temporary.is_symlink()):
                marker_info = marker_temporary.lstat()
                if (
                    not stat.S_ISREG(marker_info.st_mode) or marker_info.st_nlink != 1
                    or (os.name != "nt" and (marker_info.st_uid != os.geteuid() or stat.S_IMODE(marker_info.st_mode) & 0o077))
                ):
                    raise UpdateError("incomplete qualification acquisition marker is unsafe")
                marker_temporary.unlink()
            raise
    return {
        "status": "acquired",
        "candidateId": args.candidate_id,
        "activationHash": activation_hash,
        "candidateCodeExecuted": False,
        "terminalPromotionEvidence": False,
        **qualification_authority_fields(),
        "boundary": QUALIFICATION_HOST_RUN_BOUNDARY,
    }


def assert_private_single_link(path: Path, label: str) -> None:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise UpdateError(f"{label} must be one bounded regular single-link file")
    if os.name != "nt" and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077):
        raise UpdateError(f"{label} ownership or permissions are unsafe")


def _qualification_execution_state_locked(
    args: argparse.Namespace, *, activation_files: set[str], run_files: set[str],
) -> dict[str, Any]:
    if not isinstance(args.candidate_id, str) or re.fullmatch(
        r"pixel-[0-9]{1,6}(?:\.[0-9]{1,6}){2}-[0-9a-f]{64}", args.candidate_id,
    ) is None:
        raise UpdateError("qualification candidate ID is invalid")
    if sys.platform != "linux":
        raise UpdateError("qualification execution is supported only on qualified Linux hosts")
    if not args.qualification_root.is_absolute():
        raise UpdateError("qualification root path must be absolute")
    baseline = semantic_version(args.baseline_version, "qualification baseline version")
    (
        envelope, envelope_bytes, stage_receipt_bytes, host, _manifest, _source,
        rehearsal_receipt_bytes, rehearsal_receipt_value,
    ) = qualification_rehearsed_candidate(
        args.qualification_root, args.candidate_id, args.allowed_signers, args.identity,
        baseline, args.production_install_root,
    )
    _attestation, attestation_hash = read_qualification_root(
        args.qualification_root, baseline, envelope, envelope_bytes,
        args.production_install_root,
    )
    intent = qualification_activation_intent(
        envelope, envelope_bytes, stage_receipt_bytes, rehearsal_receipt_bytes,
        rehearsal_receipt_value, args.identity, baseline, host, args.candidate_id,
        attestation_hash,
    )
    activation_hash = qualification_activation_preview_value(intent)["activationHash"]
    activations = args.qualification_root / "activations"
    activation = activations / args.candidate_id
    acquisition_marker_path = activation / QUALIFICATION_ACQUISITION_FILE
    claim, claim_bytes = validate_qualification_activation_claim(
        activation, intent, activation_hash, expected_files=activation_files,
    )
    try:
        activation_observed = {entry.name for entry in os.scandir(activation)}
    except OSError as exc:
        raise UpdateError("qualification activation cannot be enumerated safely") from exc
    if activation_observed != activation_files:
        raise UpdateError("qualification activation file set is invalid for execution")
    marker_bytes = read_regular(
        acquisition_marker_path, MAX_STAGE_RECEIPT, "qualification acquisition marker",
    )
    assert_private_single_link(acquisition_marker_path, "qualification acquisition marker")
    marker = parse_json(marker_bytes, "qualification acquisition marker")
    runs = args.qualification_root / "runs"
    ensure_private_directory(runs, "qualification run directory")
    run_dir = runs / args.candidate_id
    ensure_private_directory(run_dir, "qualification host run")
    try:
        runs_observed = {entry.name for entry in os.scandir(runs)}
    except OSError as exc:
        raise UpdateError("qualification run directory cannot be enumerated safely") from exc
    if any(
        name != args.candidate_id
        and re.fullmatch(r"pixel-[0-9]{1,6}(?:\.[0-9]{1,6}){2}-[0-9a-f]{64}", name) is None
        for name in runs_observed
    ):
        raise UpdateError("qualification run directory contains an unrecognized entry")
    try:
        run_observed = {entry.name for entry in os.scandir(run_dir)}
    except OSError as exc:
        raise UpdateError("qualification host run cannot be enumerated safely") from exc
    if run_observed != run_files:
        raise UpdateError("qualification host run file set is invalid for execution")
    harness_bytes = read_regular(
        run_dir / "HARNESS-RUN.json", MAX_STAGE_RECEIPT, "qualification harness run",
    )
    tombstone_bytes = read_regular(
        run_dir / "EXECUTION-TOMBSTONE.json", MAX_STAGE_RECEIPT, "qualification execution tombstone",
    )
    assert_private_single_link(run_dir / "HARNESS-RUN.json", "qualification harness run")
    assert_private_single_link(run_dir / "EXECUTION-TOMBSTONE.json", "qualification execution tombstone")
    harness = parse_json(harness_bytes, "qualification harness run")
    tombstone = parse_json(tombstone_bytes, "qualification execution tombstone")
    rehashed_identity = tree_identity(activation / "source")
    expected_identity = (
        rehearsal_receipt_value["extractedTreeSha256"],
        rehearsal_receipt_value["extractedFileCount"],
        rehearsal_receipt_value["extractedBytes"],
    )
    if rehashed_identity != expected_identity:
        raise UpdateError("staged qualification source tree differs from the verified rehearsal")
    acquired_at = harness["acquiredAt"]
    expected_harness, expected_tombstone = qualification_host_run_artifacts(
        envelope, envelope_bytes, stage_receipt_bytes, rehearsal_receipt_bytes,
        rehearsal_receipt_value, claim, claim_bytes, activation_hash, args.identity,
        baseline, host, args.candidate_id, attestation_hash, rehashed_identity, acquired_at,
    )
    if harness != expected_harness or tombstone != expected_tombstone:
        raise UpdateError("qualification harness run or execution tombstone differs from the acquisition marker")
    if (
        sha256(harness_bytes) != marker["harnessRunSha256"]
        or sha256(tombstone_bytes) != marker["executionTombstoneSha256"]
    ):
        raise UpdateError("qualification harness run or tombstone does not match the acquisition marker hashes")
    expected_marker = qualification_acquisition_marker_value(
        harness, harness_bytes, tombstone, tombstone_bytes, claim, claim_bytes,
        activation_hash, rehashed_identity, acquired_at,
    )
    if marker != expected_marker:
        raise UpdateError("qualification acquisition marker differs from the revalidated host-run")
    return {
        "envelope": envelope,
        "envelope_bytes": envelope_bytes,
        "stage_receipt_bytes": stage_receipt_bytes,
        "rehearsal_receipt_bytes": rehearsal_receipt_bytes,
        "rehearsal_receipt_value": rehearsal_receipt_value,
        "claim": claim,
        "claim_bytes": claim_bytes,
        "activation_hash": activation_hash,
        "host": host,
        "candidate_id": args.candidate_id,
        "attestation_hash": attestation_hash,
        "rehashed_identity": rehashed_identity,
        "marker": marker,
        "marker_bytes": marker_bytes,
        "acquisition_marker_sha256": sha256(marker_bytes),
        "harness": harness,
        "harness_bytes": harness_bytes,
        "harness_run_sha256": sha256(harness_bytes),
        "tombstone": tombstone,
        "tombstone_bytes": tombstone_bytes,
        "execution_tombstone_sha256": sha256(tombstone_bytes),
        "runs": runs,
        "run_dir": run_dir,
        "activation": activation,
        "acquisition_marker_path": acquisition_marker_path,
    }


def qualification_execution_state(
    args: argparse.Namespace, *, activation_files: set[str], run_files: set[str],
) -> dict[str, Any]:
    with exclusive_stage_lock(args.qualification_root):
        return _qualification_execution_state_locked(
            args, activation_files=activation_files, run_files=run_files,
        )

def qualification_execution_container_name() -> str:
    return "pixel-qual-exec-" + secrets.token_hex(10)


def qualification_execution_probe(source_root: Path, probe_rel: str) -> Path:
    if not isinstance(probe_rel, str) or not probe_rel or ".." in Path(probe_rel).parts:
        raise UpdateError("qualification probe path is unsafe")
    candidate = source_root / probe_rel
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise UpdateError("qualification probe path is unavailable or unsafe") from exc
    if not resolved.is_relative_to(source_root.resolve()):
        raise UpdateError("qualification probe path must stay inside the verified activation source")
    info = resolved.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise UpdateError("qualification probe path must be a real regular file inside the verified source")
    return resolved


def qualification_execution_image() -> tuple[str, str]:
    """Load the generated release constants and derive the qualification sandbox
    image and its exact digest from the pinned baseImage at use time."""
    constants_path = ROOT / "scripts" / "generated" / "release-constants.json"
    try:
        payload = constants_path.read_bytes()
    except OSError as exc:
        raise UpdateError("generated release constants are unavailable") from exc
    if len(payload) > MAX_STAGE_RECEIPT:
        raise UpdateError("generated release constants exceed its size bound")
    data = parse_json(payload, "generated release constants")
    base_image = data.get("baseImage")
    if not isinstance(base_image, str):
        raise UpdateError("generated release constants baseImage is invalid")
    digest_match = re.fullmatch(r"[^@\s]+@(sha256:[0-9a-f]{64})", base_image)
    if digest_match is None:
        raise UpdateError("generated release constants baseImage is not digest pinned")
    return base_image, digest_match.group(1)


def qualification_execution_spec_value(
    state: dict[str, Any], probe_rel: str, probe_sha: str, timeout: float,
    container_name: str, uid: int, gid: int,
) -> dict[str, Any]:
    probe_container = "/candidate/" + "/".join(Path(probe_rel).parts)
    image, image_digest = qualification_execution_image()
    return {
        "schemaVersion": 1,
        "operation": "pixel-release-qualification-execution-spec",
        "candidateId": state["candidate_id"],
        "probe": probe_container,
        "probeRelativePath": probe_rel,
        "probeSha256": probe_sha,
        "entrypoint": "/usr/bin/env",
        "argv": [
            "-i",
            *[f"{key}={value}" for key, value in QUALIFICATION_EXECUTION_ENV.items()],
            "/bin/bash", probe_container,
        ],
        "timeoutSeconds": timeout,
        "workdir": "/scratch",
        "environment": QUALIFICATION_EXECUTION_ENV,
        "runtime": {
            "sandbox": "docker",
            "image": image,
            "imageDigest": image_digest,
            "containerName": container_name,
            "uid": uid,
            "gid": gid,
        },
        "network": "none",
        "readOnlyRoot": True,
        "sourceBindReadOnly": True,
        "capDropAll": True,
        "noNewPrivileges": True,
        "logDriver": "none",
        "pidsLimit": QUALIFICATION_EXECUTION_PIDS_LIMIT,
        "memory": QUALIFICATION_EXECUTION_MEMORY,
        "cpus": QUALIFICATION_EXECUTION_CPUS,
        "scratch": f"/scratch:rw,size={QUALIFICATION_EXECUTION_SCRATCH_SIZE},mode=0700,uid={uid},gid={gid}",
        "sourceTreeSha256": state["rehashed_identity"][0],
        "boundary": QUALIFICATION_EXECUTION_SPEC_BOUNDARY,
    }


def _read_execution_spec(state: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    spec_path = state["run_dir"] / QUALIFICATION_EXECUTION_SPEC_FILE
    spec_bytes = read_regular(spec_path, MAX_STAGE_RECEIPT, "qualification execution spec")
    assert_private_single_link(spec_path, "qualification execution spec")
    return spec_bytes, parse_json(spec_bytes, "qualification execution spec")


def qualification_validate_execution_spec(
    state: dict[str, Any], spec_bytes: bytes, claim: dict[str, Any],
) -> dict[str, Any]:
    spec = parse_json(spec_bytes, "qualification execution spec")
    probe_rel = claim.get("probe")
    probe_timeout = claim.get("probeTimeoutSeconds")
    if not isinstance(probe_rel, str) or not probe_rel:
        raise UpdateError("qualification execution claim probe is invalid")
    if not isinstance(probe_timeout, (int, float)) or probe_timeout is True:
        raise UpdateError("qualification execution claim probe timeout is invalid")
    probe_resolved = qualification_execution_probe(state["activation"] / "source", probe_rel)
    probe_sha = sha256(probe_resolved.read_bytes())
    container_name = claim.get("containerName")
    if not isinstance(container_name, str) or re.fullmatch(
        r"pixel-qual-exec-[0-9a-f]{20}", container_name,
    ) is None:
        raise UpdateError("qualification execution claim container name is invalid")
    expected = qualification_execution_spec_value(
        state, probe_rel, probe_sha, float(probe_timeout),
        container_name, os.geteuid(), os.getegid(),
    )
    if spec != expected:
        raise UpdateError("qualification execution spec differs from the revalidated host-run")
    if sha256(spec_bytes) != claim["executionSpecSha256"]:
        raise UpdateError("qualification execution spec does not match the execution claim")
    return spec


def qualification_execution_start_value(
    state: dict[str, Any], claim_bytes: bytes, spec_bytes: bytes,
    spec: dict[str, Any], attempt_id: str,
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "operation": "pixel-release-qualification-execution-start",
        "candidateId": state["candidate_id"],
        "executionClaimSha256": sha256(claim_bytes),
        "executionSpecSha256": sha256(spec_bytes),
        "containerName": spec["runtime"]["containerName"],
        "attemptId": attempt_id,
        "boundary": QUALIFICATION_EXECUTION_START_BOUNDARY,
    }


def qualification_validate_execution_start(
    state: dict[str, Any], start_bytes: bytes, claim: dict[str, Any],
    spec: dict[str, Any],
) -> dict[str, Any]:
    start = parse_json(start_bytes, "qualification execution start")
    required = {
        "schemaVersion", "operation", "candidateId", "executionClaimSha256",
        "executionSpecSha256", "containerName", "attemptId", "boundary",
    }
    exact_keys(start, required, "qualification execution start")
    if start["schemaVersion"] != 1 or start["operation"] != "pixel-release-qualification-execution-start":
        raise UpdateError("qualification execution start marker operation or schema is invalid")
    if start["candidateId"] != state["candidate_id"]:
        raise UpdateError("qualification execution start marker candidate differs")
    if start["executionClaimSha256"] != sha256(canonical_json(claim)):
        raise UpdateError("qualification execution start marker does not match the claim")
    if start["executionSpecSha256"] != sha256(canonical_json(spec)):
        raise UpdateError("qualification execution start marker does not match the spec")
    if start["containerName"] != spec["runtime"]["containerName"]:
        raise UpdateError("qualification execution start marker container name differs")
    return start


def qualification_execution_claim_value(
    harness: dict[str, Any], harness_bytes: bytes, tombstone: dict[str, Any],
    tombstone_bytes: bytes, marker_bytes: bytes, claim: dict[str, Any], claim_bytes: bytes,
    activation_hash: str, rehashed_identity: tuple[str, int, int], attestation_hash: str,
    host: dict[str, Any], claimed_at: str, execution_spec_sha256: str,
    container_name: str, probe: str, probe_timeout: float,
) -> dict[str, Any]:
    validate_utc_seconds_timestamp(claimed_at, "qualification execution claim")
    if not isinstance(container_name, str) or re.fullmatch(
        r"pixel-qual-exec-[0-9a-f]{20}", container_name,
    ) is None:
        raise UpdateError("qualification execution claim container name is invalid")
    tree_hash, file_count, extracted_bytes = rehashed_identity
    return {
        "schemaVersion": 1,
        "status": "executing",
        "operation": "pixel-release-qualification-execution-claim",
        "candidateId": harness["candidateId"],
        "product": harness["product"],
        "version": harness["version"],
        "baselineVersion": harness["baselineVersion"],
        "relation": harness["relation"],
        "channel": harness["channel"],
        "qualifierIdentity": harness["qualifierIdentity"],
        "sourceCommit": harness["sourceCommit"],
        "sourceTree": harness["sourceTree"],
        "host": host,
        "envelopeSha256": harness["envelopeSha256"],
        "stageReceiptSha256": harness["stageReceiptSha256"],
        "rehearsalReceiptSha256": harness["rehearsalReceiptSha256"],
        "activationReceiptSha256": sha256(claim_bytes),
        "activationHash": activation_hash,
        "activationClaimedAt": claim["claimedAt"],
        "extractedTreeSha256": harness["extractedTreeSha256"],
        "rehashedTreeSha256": tree_hash,
        "rehashedFileCount": file_count,
        "rehashedBytes": extracted_bytes,
        "qualificationRootAttestationSha256": attestation_hash,
        "acquisitionMarkerSha256": sha256(marker_bytes),
        "harnessRunSha256": sha256(harness_bytes),
        "executionTombstoneSha256": sha256(tombstone_bytes),
        "executionSpecSha256": execution_spec_sha256,
        "containerName": container_name,
        "probe": probe,
        "probeTimeoutSeconds": probe_timeout,
        "acquiredAt": harness["acquiredAt"],
        "claimedAt": claimed_at,
        "candidateCodeCopied": True,
        "candidateCodeExecuted": False,
        "candidateCodeWillExecute": True,
        "executionObserved": False,
        "activeDeploymentChanged": False,
        "networkUsed": False,
        "terminalPromotionEvidence": False,
        **qualification_authority_fields(),
        "boundary": QUALIFICATION_EXECUTION_CLAIM_BOUNDARY,
    }


def qualification_validate_execution_claim(
    state: dict[str, Any], claim_bytes: bytes,
) -> tuple[dict[str, Any], bytes, dict[str, Any]]:
    claim = parse_json(claim_bytes, "qualification execution claim")
    claimed_at = claim.get("claimedAt")
    if not isinstance(claimed_at, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", claimed_at,
    ):
        raise UpdateError("qualification execution claim timestamp is invalid")
    spec_bytes, spec = _read_execution_spec(state)
    expected = qualification_execution_claim_value(
        state["harness"], state["harness_bytes"], state["tombstone"], state["tombstone_bytes"],
        state["marker_bytes"], state["claim"], state["claim_bytes"], state["activation_hash"],
        state["rehashed_identity"], state["attestation_hash"], state["host"], claimed_at,
        sha256(spec_bytes), spec["runtime"]["containerName"],
        spec["probeRelativePath"], spec["timeoutSeconds"],
    )
    if claim != expected:
        raise UpdateError("qualification execution claim differs from the revalidated host-run")
    return claim, spec_bytes, spec


def qualification_execution_claim(args: argparse.Namespace) -> dict[str, Any]:
    if not args.confirm:
        raise UpdateError("qualification execution claim requires --confirm")
    if not args.qualification_root.is_absolute():
        raise UpdateError("qualification root path must be absolute")
    if not isinstance(getattr(args, "probe", None), str) or not args.probe:
        raise UpdateError("qualification execution claim requires a probe relative path")
    try:
        probe_timeout = float(getattr(args, "probe_timeout", 30.0))
    except (TypeError, ValueError) as exc:
        raise UpdateError("qualification execution claim probe timeout is invalid") from exc
    if not 1 <= probe_timeout <= 3600:
        raise UpdateError("qualification execution claim probe timeout is out of range")
    claim_path = (
        args.qualification_root / "activations" / args.candidate_id
    ) / QUALIFICATION_EXECUTION_CLAIM_FILE
    if claim_path.exists() or claim_path.is_symlink():
        raise UpdateError("qualification execution has already been claimed; automatic re-execution is forbidden")
    state = qualification_execution_state(
        args,
        activation_files=set(QUALIFICATION_ACTIVATION_ACQUIRED_FILES),
        run_files=set(QUALIFICATION_RUN_ACQUIRED_FILES),
    )
    if claim_path.exists() or claim_path.is_symlink():
        raise UpdateError("qualification execution has already been claimed; automatic re-execution is forbidden")
    spec_path = state["run_dir"] / QUALIFICATION_EXECUTION_SPEC_FILE
    if spec_path.exists() or spec_path.is_symlink():
        raise UpdateError("qualification execution spec already exists; automatic re-execution is forbidden")
    probe_resolved = qualification_execution_probe(state["activation"] / "source", args.probe)
    probe_sha = sha256(probe_resolved.read_bytes())
    uid = os.geteuid()
    gid = os.getegid()
    if uid == 0:
        raise UpdateError("qualification host execution cannot safely run as root; refusing non-root sandbox")
    container_name = qualification_execution_container_name()
    spec = qualification_execution_spec_value(
        state, args.probe, probe_sha, probe_timeout, container_name, uid, gid,
    )
    spec_bytes = canonical_json(spec)
    try:
        write_private(spec_path, spec_bytes)
    except FileExistsError as exc:
        raise UpdateError("qualification execution spec already exists; automatic re-execution is forbidden") from exc
    fsync_directory(state["run_dir"])
    claimed_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    claim = qualification_execution_claim_value(
        state["harness"], state["harness_bytes"], state["tombstone"], state["tombstone_bytes"],
        state["marker_bytes"], state["claim"], state["claim_bytes"], state["activation_hash"],
        state["rehashed_identity"], state["attestation_hash"], state["host"], claimed_at,
        sha256(spec_bytes), container_name, args.probe, probe_timeout,
    )
    claim_bytes = canonical_json(claim)
    try:
        write_private(claim_path, claim_bytes)
    except FileExistsError as exc:
        raise UpdateError("qualification execution has already been claimed; automatic re-execution is forbidden") from exc
    fsync_directory(state["activation"])
    return {
        "status": "executing",
        "candidateId": args.candidate_id,
        "activationHash": state["activation_hash"],
        "executionClaimSha256": sha256(claim_bytes),
        "executionSpecSha256": sha256(spec_bytes),
        "candidateCodeExecuted": False,
        "executionObserved": False,
        "terminalPromotionEvidence": False,
        **qualification_authority_fields(),
        "boundary": QUALIFICATION_EXECUTION_CLAIM_BOUNDARY,
    }


def validate_qualification_execution_observation(
    observation: dict[str, Any], candidate_id: str, candidate_version: str,
) -> None:
    required = {
        "schemaVersion", "operation", "candidateId", "outcome", "phase", "reason",
        "exitCode", "signal", "timedOut", "observedVersion", "candidateCodeExecuted",
        "executionObserved", "executionSpecSha256", "sandbox", "containerRemoved",
        "absenceProven", "networkUsed",
        "terminalPromotionEvidence", "publicationAuthority",
        "productionActivationAuthority", "compatibilityMutationAuthority",
        "productionStateChanged", "boundary",
    }
    exact_keys(observation, required, "qualification execution observation")
    if observation["schemaVersion"] != 1:
        raise UpdateError("qualification execution observation schema is invalid")
    if observation["operation"] != "pixel-release-qualification-execution-observation":
        raise UpdateError("qualification execution observation operation is invalid")
    if observation["candidateId"] != candidate_id:
        raise UpdateError("qualification execution observation candidate ID differs")
    spec_sha = observation["executionSpecSha256"]
    if not isinstance(spec_sha, str) or re.fullmatch(r"[0-9a-f]{64}", spec_sha) is None:
        raise UpdateError("qualification execution observation spec hash is invalid")
    sandbox = observation["sandbox"]
    if not isinstance(sandbox, dict) or sandbox.get("network") != "none":
        raise UpdateError("qualification execution observation sandbox must be network-isolated")
    outcome = observation["outcome"]
    if outcome not in QUALIFICATION_EXECUTION_OUTCOMES:
        raise UpdateError("qualification execution observation outcome is invalid")
    phase = observation["phase"]
    reason = observation["reason"]
    if phase is not None and phase not in QUALIFICATION_EXECUTION_PHASES:
        raise UpdateError("qualification execution observation phase is invalid")
    if reason is not None and reason not in QUALIFICATION_EXECUTION_REASONS:
        raise UpdateError("qualification execution observation reason is invalid")
    exit_code = observation["exitCode"]
    signal = observation["signal"]
    timed_out = observation["timedOut"]
    observed_version = observation["observedVersion"]
    if not isinstance(timed_out, bool):
        raise UpdateError("qualification execution observation timedOut is invalid")
    if exit_code is not None and (type(exit_code) is not int or exit_code < 0 or exit_code > 255):
        raise UpdateError("qualification execution observation exit code is invalid")
    if signal is not None and signal not in QUALIFICATION_EXECUTION_SIGNALS:
        raise UpdateError("qualification execution observation signal is invalid")
    if observed_version is not None:
        semantic_version(observed_version, "qualification execution observed version")
    if observation["candidateCodeExecuted"] is not True:
        raise UpdateError("qualification execution observation must accurately report candidate execution")
    if observation["executionObserved"] is not True:
        raise UpdateError("qualification execution observation must accurately report execution was observed")
    if observation["containerRemoved"] is not True or observation["absenceProven"] is not True:
        raise UpdateError("qualification execution observation must prove container removal and absence")
    if observation["networkUsed"] is not False or observation["terminalPromotionEvidence"] is not False:
        raise UpdateError("qualification execution observation network or promotion flags are invalid")
    for field in (
        "publicationAuthority", "productionActivationAuthority",
        "compatibilityMutationAuthority", "productionStateChanged",
    ):
        if observation[field] is not False:
            raise UpdateError("qualification execution observation grants authority")
    if observation["boundary"] != QUALIFICATION_EXECUTION_OBSERVATION_BOUNDARY:
        raise UpdateError("qualification execution observation boundary is invalid")
    if outcome == "success":
        if exit_code != 0 or timed_out or signal is not None or phase is not None or reason is not None:
            raise UpdateError("success requires observed exit 0 with no timeout, signal, phase, or reason")
        if observed_version != candidate_version:
            raise UpdateError("success requires the observed exact candidate version")
    elif outcome == "failure":
        if exit_code is None or exit_code == 0 or timed_out or signal is not None:
            raise UpdateError("failure requires a bounded non-zero exit code with no timeout or signal")
        if phase is None or reason is None:
            raise UpdateError("failure requires a closed phase and reason")
        if observed_version is not None and observed_version != candidate_version:
            raise UpdateError("failure observed version differs from the candidate version")
    elif outcome == "timeout":
        if not timed_out or signal is not None:
            raise UpdateError("timeout requires a bounded timeout with no signal")
        if exit_code is not None and exit_code == 0:
            raise UpdateError("timeout can never be success")
    elif outcome == "signal":
        if signal is None or timed_out:
            raise UpdateError("signal requires a bounded signal with no timeout")
        if exit_code is not None and exit_code == 0:
            raise UpdateError("signal can never be success")
    elif outcome == "deferred":
        if phase is None or reason is None or timed_out or signal is not None:
            raise UpdateError("deferred requires a closed phase and reason with no timeout or signal")
        if exit_code is not None and exit_code == 0:
            raise UpdateError("deferred can never be success")


def qualification_execution_result_marker_value(
    harness: dict[str, Any], claim_bytes: bytes, result_bytes: bytes, recorded_at: str,
) -> dict[str, Any]:
    validate_utc_seconds_timestamp(recorded_at, "qualification execution result marker")
    return {
        "schemaVersion": 1,
        "status": "terminal",
        "operation": "pixel-release-qualification-execution-result-marker",
        "candidateId": harness["candidateId"],
        "activationHash": harness["activationHash"],
        "executionClaimSha256": sha256(claim_bytes),
        "executionResultSha256": sha256(result_bytes),
        "recordedAt": recorded_at,
        "candidateCodeExecuted": True,
        "executionObserved": True,
        "terminalPromotionEvidence": False,
        **qualification_authority_fields(),
        "boundary": QUALIFICATION_EXECUTION_RESULT_MARKER_BOUNDARY,
    }


def qualification_execution_result_value(
    state: dict[str, Any], claim: dict[str, Any], claim_bytes: bytes,
    spec: dict[str, Any], spec_bytes: bytes, start: dict[str, Any], start_bytes: bytes,
    observation: dict[str, Any], observed_at: str,
) -> tuple[dict[str, Any], bytes, dict[str, Any], bytes]:
    validate_utc_seconds_timestamp(observed_at, "qualification execution result")
    candidate_version = state["envelope"]["version"]
    validate_qualification_execution_observation(
        observation, state["candidate_id"], candidate_version,
    )
    if observation["executionSpecSha256"] != sha256(spec_bytes):
        raise UpdateError("qualification execution observation does not close the execution spec hash")
    sandbox = observation["sandbox"]
    runtime = spec["runtime"]
    if (
        sandbox.get("containerName") != runtime["containerName"]
        or sandbox.get("imageDigest") != runtime["imageDigest"]
        or sandbox.get("network") != spec["network"]
        or sandbox.get("uid") != runtime["uid"]
        or sandbox.get("gid") != runtime["gid"]
        or sandbox.get("readOnlyRoot") is not True
        or sandbox.get("noNewPrivileges") is not True
        or sandbox.get("capDropAll") is not True
    ):
        raise UpdateError("qualification execution observation sandbox does not match the execution spec")
    success = observation["outcome"] == "success"
    tree_hash, file_count, extracted_bytes = state["rehashed_identity"]
    harness = state["harness"]
    result = {
        "schemaVersion": 1,
        "status": "terminal",
        "operation": "pixel-release-qualification-execution-result",
        "candidateId": harness["candidateId"],
        "product": harness["product"],
        "version": harness["version"],
        "baselineVersion": harness["baselineVersion"],
        "relation": harness["relation"],
        "channel": harness["channel"],
        "qualifierIdentity": harness["qualifierIdentity"],
        "sourceCommit": harness["sourceCommit"],
        "sourceTree": harness["sourceTree"],
        "host": harness["host"],
        "envelopeSha256": harness["envelopeSha256"],
        "stageReceiptSha256": harness["stageReceiptSha256"],
        "rehearsalReceiptSha256": harness["rehearsalReceiptSha256"],
        "activationReceiptSha256": harness["activationReceiptSha256"],
        "activationHash": harness["activationHash"],
        "activationClaimedAt": harness["claimedAt"],
        "extractedTreeSha256": harness["extractedTreeSha256"],
        "rehashedTreeSha256": tree_hash,
        "rehashedFileCount": file_count,
        "rehashedBytes": extracted_bytes,
        "qualificationRootAttestationSha256": harness["qualificationRootAttestationSha256"],
        "acquisitionMarkerSha256": state["acquisition_marker_sha256"],
        "harnessRunSha256": state["harness_run_sha256"],
        "executionTombstoneSha256": state["execution_tombstone_sha256"],
        "executionClaimSha256": sha256(claim_bytes),
        "executionSpecSha256": sha256(spec_bytes),
        "executionStartSha256": sha256(start_bytes),
        "acquiredAt": harness["acquiredAt"],
        "claimedAt": claim["claimedAt"],
        "observedAt": observed_at,
        "outcome": observation["outcome"],
        "phase": observation["phase"],
        "reason": observation["reason"],
        "exitCode": observation["exitCode"],
        "signal": observation["signal"],
        "timedOut": observation["timedOut"],
        "observedVersion": observation["observedVersion"],
        "success": success,
        "candidateCodeExecuted": True,
        "executionObserved": True,
        "activeDeploymentChanged": False,
        "networkUsed": False,
        "terminalPromotionEvidence": False,
        **qualification_authority_fields(),
        "boundary": QUALIFICATION_EXECUTION_RESULT_BOUNDARY,
    }
    result_bytes = canonical_json(result)
    marker = qualification_execution_result_marker_value(
        harness, claim_bytes, result_bytes, observed_at,
    )
    return result, result_bytes, marker, canonical_json(marker)


def _recover_execution_result_marker(
    state: dict[str, Any], result_path: Path, marker_path: Path,
) -> dict[str, Any]:
    claim_path = state["activation"] / QUALIFICATION_EXECUTION_CLAIM_FILE
    claim_bytes = read_regular(claim_path, MAX_STAGE_RECEIPT, "qualification execution claim")
    assert_private_single_link(claim_path, "qualification execution claim")
    claim, spec_bytes, spec = qualification_validate_execution_claim(state, claim_bytes)
    if marker_path.exists() or marker_path.is_symlink():
        raise UpdateError("qualification execution result has already been recorded; replay is forbidden")
    result_bytes = read_regular(result_path, MAX_STAGE_RECEIPT, "qualification execution result")
    assert_private_single_link(result_path, "qualification execution result")
    result = parse_json(result_bytes, "qualification execution result")
    start_path = state["run_dir"] / QUALIFICATION_EXECUTION_START_FILE
    start_bytes = read_regular(start_path, MAX_STAGE_RECEIPT, "qualification execution start")
    assert_private_single_link(start_path, "qualification execution start")
    start = qualification_validate_execution_start(state, start_bytes, claim, spec)
    if (
        result.get("candidateId") != state["candidate_id"]
        or result.get("activationHash") != state["activation_hash"]
        or result.get("executionClaimSha256") != sha256(claim_bytes)
        or result.get("executionSpecSha256") != sha256(spec_bytes)
        or result.get("executionStartSha256") != sha256(start_bytes)
    ):
        raise UpdateError("qualification execution result does not match the execution claim")
    recorded_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    marker = qualification_execution_result_marker_value(
        state["harness"], claim_bytes, result_bytes, recorded_at,
    )
    marker_bytes = canonical_json(marker)
    try:
        write_private(marker_path, marker_bytes)
    except FileExistsError as exc:
        raise UpdateError("qualification execution result has already been recorded; replay is forbidden") from exc
    fsync_directory(state["activation"])
    return {
        "status": "recovered",
        "candidateId": state["candidate_id"],
        "activationHash": state["activation_hash"],
        "executionResultSha256": sha256(result_bytes),
        "candidateCodeExecuted": True,
        "executionObserved": True,
        "terminalPromotionEvidence": False,
        **qualification_authority_fields(),
        "boundary": QUALIFICATION_EXECUTION_RESULT_BOUNDARY,
    }


def qualification_execution_result(args: argparse.Namespace) -> dict[str, Any]:
    if not args.confirm:
        raise UpdateError("qualification execution result requires --confirm")
    if not args.qualification_root.is_absolute():
        raise UpdateError("qualification root path must be absolute")
    runs = args.qualification_root / "runs"
    run_dir = runs / args.candidate_id
    result_path = run_dir / QUALIFICATION_EXECUTION_RESULT_FILE
    marker_path = (args.qualification_root / "activations" / args.candidate_id) / QUALIFICATION_EXECUTION_RESULT_MARKER
    if result_path.exists() or result_path.is_symlink():
        if marker_path.exists() or marker_path.is_symlink():
            qualification_execution_state(
                args,
                activation_files=set(QUALIFICATION_ACTIVATION_RESULT_FILES),
                run_files=set(QUALIFICATION_RUN_RESULT_FILES),
            )
            raise UpdateError("qualification execution result has already been recorded; replay is forbidden")
        state = qualification_execution_state(
            args,
            activation_files=set(QUALIFICATION_ACTIVATION_CLAIMED_FILES),
            run_files=set(QUALIFICATION_RUN_RESULT_FILES),
        )
        return _recover_execution_result_marker(state, result_path, marker_path)
    if marker_path.exists() or marker_path.is_symlink():
        raise UpdateError("qualification execution result marker exists without its result; recovery required")
    state = qualification_execution_state(
        args,
        activation_files=set(QUALIFICATION_ACTIVATION_CLAIMED_FILES),
        run_files=set(QUALIFICATION_RUN_OBSERVED_FILES),
    )
    claim_path = state["activation"] / QUALIFICATION_EXECUTION_CLAIM_FILE
    claim_bytes = read_regular(claim_path, MAX_STAGE_RECEIPT, "qualification execution claim")
    assert_private_single_link(claim_path, "qualification execution claim")
    claim, spec_bytes, spec = qualification_validate_execution_claim(state, claim_bytes)
    start_path = state["run_dir"] / QUALIFICATION_EXECUTION_START_FILE
    start_bytes = read_regular(start_path, MAX_STAGE_RECEIPT, "qualification execution start")
    assert_private_single_link(start_path, "qualification execution start")
    start = qualification_validate_execution_start(state, start_bytes, claim, spec)
    observation_path = state["run_dir"] / QUALIFICATION_EXECUTION_OBSERVATION_FILE
    observation_bytes = read_regular(
        observation_path, MAX_STAGE_RECEIPT, "qualification execution observation",
    )
    observation = parse_json(observation_bytes, "qualification execution observation")
    observed_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    result, result_bytes, marker, marker_bytes = qualification_execution_result_value(
        state, claim, claim_bytes, spec, spec_bytes, start, start_bytes, observation, observed_at,
    )
    try:
        write_private(result_path, result_bytes)
    except FileExistsError as exc:
        raise UpdateError("qualification execution result has already been recorded; replay is forbidden") from exc
    fsync_directory(state["run_dir"])
    try:
        write_private(marker_path, marker_bytes)
    except FileExistsError as exc:
        raise UpdateError("qualification execution result has already been recorded; replay is forbidden") from exc
    fsync_directory(state["activation"])
    return {
        "status": "recorded",
        "candidateId": args.candidate_id,
        "activationHash": state["activation_hash"],
        "executionClaimSha256": sha256(claim_bytes),
        "executionSpecSha256": sha256(spec_bytes),
        "executionStartSha256": sha256(start_bytes),
        "executionResultSha256": sha256(result_bytes),
        "outcome": observation["outcome"],
        "success": result["success"],
        "candidateCodeExecuted": True,
        "executionObserved": True,
        "terminalPromotionEvidence": False,
        **qualification_authority_fields(),
        "boundary": QUALIFICATION_EXECUTION_RESULT_BOUNDARY,
    }


def qualification_execution_interruption_intent_value(
    harness: dict[str, Any], claim_bytes: bytes, spec_bytes: bytes,
    start_bytes: bytes, spec: dict[str, Any], start: dict[str, Any],
) -> dict[str, Any]:
    """Deterministic content-free interruption confirmation payload.

    The confirmation hash binds the exact candidate, activation, host/source
    identities, claim/spec/start hashes, container name, and attempt to the
    fixed indeterminate post-start infrastructure-failure state. It carries no
    timestamp so preview and record derive an identical confirmation hash."""
    return {
        "operation": "pixel-release-qualification-execution-interruption",
        "candidateId": harness["candidateId"],
        "activationHash": harness["activationHash"],
        "qualifierIdentity": harness["qualifierIdentity"],
        "sourceCommit": harness["sourceCommit"],
        "sourceTree": harness["sourceTree"],
        "host": harness["host"],
        "executionClaimSha256": sha256(claim_bytes),
        "executionSpecSha256": sha256(spec_bytes),
        "executionStartSha256": sha256(start_bytes),
        "containerName": spec["runtime"]["containerName"],
        "attemptId": start["attemptId"],
        "candidateExecutionState": QUALIFICATION_EXECUTION_INTERRUPTION_STATE,
        "reason": QUALIFICATION_EXECUTION_INTERRUPTION_REASON,
        "terminalPromotionEvidence": False,
        **qualification_authority_fields(),
    }


def qualification_execution_interruption_value(
    harness: dict[str, Any], claim_bytes: bytes, spec_bytes: bytes,
    start_bytes: bytes, spec: dict[str, Any], start: dict[str, Any], recorded_at: str,
) -> dict[str, Any]:
    validate_utc_seconds_timestamp(recorded_at, "qualification execution interruption")
    return {
        "schemaVersion": 1,
        "status": "interrupted",
        **qualification_execution_interruption_intent_value(
            harness, claim_bytes, spec_bytes, start_bytes, spec, start,
        ),
        "recordedAt": recorded_at,
        "boundary": QUALIFICATION_EXECUTION_INTERRUPTION_BOUNDARY,
    }


def qualification_execution_interruption_marker_value(
    harness: dict[str, Any], claim_bytes: bytes, interruption_bytes: bytes, recorded_at: str,
) -> dict[str, Any]:
    validate_utc_seconds_timestamp(recorded_at, "qualification execution interruption marker")
    return {
        "schemaVersion": 1,
        "status": "terminal",
        "operation": "pixel-release-qualification-execution-interruption-marker",
        "candidateId": harness["candidateId"],
        "activationHash": harness["activationHash"],
        "executionClaimSha256": sha256(claim_bytes),
        "executionInterruptionSha256": sha256(interruption_bytes),
        "recordedAt": recorded_at,
        "candidateExecutionState": QUALIFICATION_EXECUTION_INTERRUPTION_STATE,
        "terminalPromotionEvidence": False,
        **qualification_authority_fields(),
        "boundary": QUALIFICATION_EXECUTION_INTERRUPTION_MARKER_BOUNDARY,
    }


def _qualification_interruption_docker(argv: list[str]) -> subprocess.CompletedProcess:
    """Bounded, shell-free, scrub-env runner for the tiny interruption-only
    Docker custody probes. Uses the fixed executable, a fixed timeout, and a
    hard byte ceiling on each stream; terminates and reaps the child on timeout
    or byte overflow and fails closed as an UpdateError."""
    if not argv or argv[0] != QUALIFICATION_EXECUTION_DOCKER:
        raise UpdateError("docker custody executable is fixed")
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    stdout_total = 0
    stderr_total = 0
    try:
        proc = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=QUALIFICATION_EXECUTION_SCRUBBED_ENV,
        )
    except OSError as exc:
        raise UpdateError("docker custody could not be determined") from exc
    deadline = time.monotonic() + QUALIFICATION_EXECUTION_INTERRUPTION_TIMEOUT

    def _overflow(stream_name: str) -> None:
        if proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        raise UpdateError(f"docker custody {stream_name} exceeded its bound")

    streams = [proc.stdout, proc.stderr]
    try:
        while proc.poll() is None or streams:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if proc.poll() is None:
                    try:
                        proc.terminate()
                    except OSError:
                        pass
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        try:
                            proc.kill()
                        except OSError:
                            pass
                        try:
                            proc.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            pass
                raise UpdateError("docker custody could not be determined")
            ready, _, _ = select.select(streams, [], [], max(0.0, min(remaining, 0.2)))
            if not ready:
                if proc.poll() is not None and not streams:
                    break
                continue
            for stream in ready:
                try:
                    chunk = os.read(stream.fileno(), 8192)
                except OSError:
                    chunk = b""
                if not chunk:
                    streams.remove(stream)
                    continue
                if stream is proc.stdout:
                    stdout_total += len(chunk)
                    if stdout_total > QUALIFICATION_EXECUTION_INTERRUPTION_STREAM_LIMIT:
                        _overflow("stdout")
                    stdout_chunks.append(chunk)
                else:
                    stderr_total += len(chunk)
                    if stderr_total > QUALIFICATION_EXECUTION_INTERRUPTION_STREAM_LIMIT:
                        _overflow("stderr")
                    stderr_chunks.append(chunk)
    finally:
        if proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        try:
            proc.stdout.close()
        except Exception:
            pass
        try:
            proc.stderr.close()
        except Exception:
            pass
    return subprocess.CompletedProcess(
        argv, proc.returncode,
        stdout=b"".join(stdout_chunks).decode("utf-8", "replace"),
        stderr=b"".join(stderr_chunks).decode("utf-8", "replace"),
    )


def _qualification_interruption_inspect_by_name(name: str) -> dict[str, Any] | None:
    """Inspect by the claim-bound name, returning the record or None when absent.

    On success exactly one JSON object must be present in a one-element array;
    null, a bare object, multiple records, or any other shape fails closed as an
    UpdateError. Absence is proven only via the exact Docker `No such container:
    <name>` contract. Ownership is not judged here; callers validate custody
    before any mutation."""
    result = _qualification_interruption_docker(
        [QUALIFICATION_EXECUTION_DOCKER, "container", "inspect", name],
    )
    if result.returncode == 0:
        try:
            payload = json.loads(result.stdout)
        except ValueError as exc:
            raise UpdateError("docker container inspect returned unparseable output") from exc
        if not isinstance(payload, list) or len(payload) != 1:
            raise UpdateError("docker container inspect returned an unexpected shape")
        record = payload[0]
        if not isinstance(record, dict):
            raise UpdateError("docker container inspect returned an invalid container record")
        return record
    if result.returncode == 1 and result.stdout.strip() == "[]" and (
        result.stderr.strip() == f"Error response from daemon: No such container: {name}"
    ):
        return None
    raise UpdateError(f"claim-bound container custody could not be determined for {name}")


def _qualification_interruption_validate_owned(
    record: dict[str, Any], name: str, expected: dict[str, Any],
) -> str:
    """Return the immutable 64-hex container ID only when the record is exactly
    our owned container: exact name, image, image digest, and every start/spec
    custody label must match. A foreign or mismatched record fails closed and is
    never mutated."""
    config = record.get("Config") or {}
    image_matches = (
        config.get("Image") == expected["image"]
        and record.get("Image") == expected["imageDigest"]
    )
    labels = config.get("Labels") or {}
    labels_match = all(labels.get(key) == value for key, value in expected["labels"].items())
    name_match = record.get("Name") == "/" + name
    if not (name_match and image_matches and labels_match):
        raise UpdateError("claim-bound container is foreign or mismatched; refusing to mutate it")
    container_id = record.get("Id") or ""
    if re.fullmatch(r"[0-9a-f]{64}", container_id) is None:
        raise UpdateError("owned container id does not match the 64-hex grammar")
    return container_id


def _qualification_interruption_remove_by_id(container_id: str) -> None:
    result = _qualification_interruption_docker(
        [QUALIFICATION_EXECUTION_DOCKER, "rm", "-f", container_id],
    )
    if result.returncode != 0:
        raise UpdateError("sandbox container removal could not be completed")


def _qualification_interruption_name_absent(name: str) -> bool:
    result = _qualification_interruption_docker(
        [QUALIFICATION_EXECUTION_DOCKER, "container", "inspect", name],
    )
    if result.returncode == 1 and result.stdout.strip() == "[]" and (
        result.stderr.strip() == f"Error response from daemon: No such container: {name}"
    ):
        return True
    return False


def _qualification_interruption_id_absent(container_id: str) -> bool:
    result = _qualification_interruption_docker(
        [QUALIFICATION_EXECUTION_DOCKER, "container", "inspect", container_id],
    )
    if result.returncode == 1 and result.stdout.strip() == "[]" and (
        result.stderr.strip() == f"Error response from daemon: No such container: {container_id}"
    ):
        return True
    return False


def qualification_execution_interruption_custody(
    state: dict[str, Any], start_bytes: bytes, spec: dict[str, Any],
) -> dict[str, bool]:
    """Prove the exact claim-bound container is absent, or remove only it by its
    immutable container ID after exact ownership/label/image/name verification.

    Never removes a foreign, mismatched, unprovable, or substituted container
    and never deletes any durable START/CLAIM/SPEC/HARNESS/TOMBSTONE artifact.
    Returns content-free booleans only; the immutable container ID is never
    exposed in command or artifact output."""
    name = spec["runtime"]["containerName"]
    start = parse_json(start_bytes, "qualification execution start")
    expected = {
        "image": spec["runtime"]["image"],
        "imageDigest": spec["runtime"]["imageDigest"],
        "labels": {
            "pixel.qualification.attemptId": start["attemptId"],
            "pixel.qualification.candidateId": start["candidateId"],
            "pixel.qualification.executionSpecSha256": start["executionSpecSha256"],
            "pixel.qualification.executionStartSha256": sha256(start_bytes),
        },
    }
    record = _qualification_interruption_inspect_by_name(name)
    if record is None:
        return {"containerAbsent": True, "containerRemoved": False}
    container_id = _qualification_interruption_validate_owned(record, name, expected)
    _qualification_interruption_remove_by_id(container_id)
    if not _qualification_interruption_name_absent(name):
        raise UpdateError("sandbox container could not be removed; absence not proven")
    if not _qualification_interruption_id_absent(container_id):
        raise UpdateError("sandbox container could not be removed; id absence not proven")
    return {"containerAbsent": True, "containerRemoved": True}


def _qualification_execution_interruption_bindings(
    state: dict[str, Any],
) -> tuple[dict[str, Any], bytes, dict[str, Any], bytes, dict[str, Any], bytes, str]:
    """Exactly revalidate the claim/spec/start bound to the host-run and derive
    the deterministic interruption confirmation hash. Mutates nothing."""
    claim_path = state["activation"] / QUALIFICATION_EXECUTION_CLAIM_FILE
    claim_bytes = read_regular(claim_path, MAX_STAGE_RECEIPT, "qualification execution claim")
    assert_private_single_link(claim_path, "qualification execution claim")
    claim, spec_bytes, spec = qualification_validate_execution_claim(state, claim_bytes)
    start_path = state["run_dir"] / QUALIFICATION_EXECUTION_START_FILE
    start_bytes = read_regular(start_path, MAX_STAGE_RECEIPT, "qualification execution start")
    assert_private_single_link(start_path, "qualification execution start")
    start = qualification_validate_execution_start(state, start_bytes, claim, spec)
    intent = qualification_execution_interruption_intent_value(
        state["harness"], claim_bytes, spec_bytes, start_bytes, spec, start,
    )
    return claim, claim_bytes, spec, spec_bytes, start, start_bytes, sha256(canonical_json(intent))


def qualification_execution_interruption_preview(args: argparse.Namespace) -> dict[str, Any]:
    if not isinstance(args.candidate_id, str) or re.fullmatch(
        r"pixel-[0-9]{1,6}(?:\.[0-9]{1,6}){2}-[0-9a-f]{64}", args.candidate_id,
    ) is None:
        raise UpdateError("qualification candidate ID is invalid")
    if sys.platform != "linux":
        raise UpdateError("qualification execution interruption is supported only on qualified Linux hosts")
    if not args.qualification_root.is_absolute():
        raise UpdateError("qualification root path must be absolute")
    semantic_version(args.baseline_version, "qualification baseline version")
    with exclusive_stage_lock(args.qualification_root):
        state = _qualification_execution_state_locked(
            args,
            activation_files=set(QUALIFICATION_ACTIVATION_CLAIMED_FILES),
            run_files=set(QUALIFICATION_RUN_STARTED_FILES),
        )
        _claim, _claim_bytes, _spec, _spec_bytes, _start, _start_bytes, intent_hash = (
            _qualification_execution_interruption_bindings(state)
        )
        return {
            "status": "preview",
            "candidateId": args.candidate_id,
            "activationHash": state["activation_hash"],
            "interruptionHash": intent_hash,
            "candidateExecutionState": QUALIFICATION_EXECUTION_INTERRUPTION_STATE,
            "reason": QUALIFICATION_EXECUTION_INTERRUPTION_REASON,
            "terminalPromotionEvidence": False,
            **qualification_authority_fields(),
            "boundary": QUALIFICATION_EXECUTION_INTERRUPTION_BOUNDARY,
        }


def _validate_interruption_artifacts(
    artifact_path: Path, marker_path: Path, *,
    harness: dict[str, Any], claim_bytes: bytes, spec_bytes: bytes,
    start_bytes: bytes, spec: dict[str, Any], start: dict[str, Any],
) -> tuple[bytes, str]:
    """Exactly revalidate the immutable interruption artifact (reconstructed
    from its recordedAt timestamp) and, when present, the marker reconstructed
    from the validated artifact bytes. Raises on tampering, wrong fields, or
    unsafe files and mutates nothing. Returns the validated artifact bytes and
    its recordedAt."""
    artifact_bytes = read_regular(
        artifact_path, MAX_STAGE_RECEIPT, "qualification execution interruption artifact",
    )
    assert_private_single_link(artifact_path, "qualification execution interruption artifact")
    artifact = parse_json(artifact_bytes, "qualification execution interruption artifact")
    recorded_at = artifact.get("recordedAt")
    validate_utc_seconds_timestamp(recorded_at, "qualification execution interruption artifact")
    expected = qualification_execution_interruption_value(
        harness, claim_bytes, spec_bytes, start_bytes, spec, start, recorded_at,
    )
    if artifact != expected:
        raise UpdateError("qualification execution interruption artifact does not match the revalidated host-run")
    if marker_path.exists() or marker_path.is_symlink():
        marker_bytes = read_regular(
            marker_path, MAX_STAGE_RECEIPT, "qualification execution interruption marker",
        )
        assert_private_single_link(marker_path, "qualification execution interruption marker")
        marker = parse_json(marker_bytes, "qualification execution interruption marker")
        expected_marker = qualification_execution_interruption_marker_value(
            harness, claim_bytes, artifact_bytes, recorded_at,
        )
        if marker != expected_marker:
            raise UpdateError("qualification execution interruption marker does not match the validated artifact")
    return artifact_bytes, recorded_at


def _repair_execution_interruption_marker(
    args: argparse.Namespace, artifact_path: Path, marker_path: Path,
) -> dict[str, Any]:
    state = _qualification_execution_state_locked(
        args,
        activation_files=set(QUALIFICATION_ACTIVATION_CLAIMED_FILES),
        run_files=set(QUALIFICATION_RUN_INTERRUPTED_FILES),
    )
    claim, claim_bytes, spec, spec_bytes, start, start_bytes, intent_hash = (
        _qualification_execution_interruption_bindings(state)
    )
    if not hmac.compare_digest(intent_hash, args.interruption_hash):
        raise UpdateError("qualification execution interruption hash differs from the revalidated host-run")
    artifact_bytes, recorded_at = _validate_interruption_artifacts(
        artifact_path, marker_path,
        harness=state["harness"], claim_bytes=claim_bytes, spec_bytes=spec_bytes,
        start_bytes=start_bytes, spec=spec, start=start,
    )
    qualification_execution_interruption_custody(state, start_bytes, spec)
    marker = qualification_execution_interruption_marker_value(
        state["harness"], claim_bytes, artifact_bytes, recorded_at,
    )
    marker_bytes = canonical_json(marker)
    try:
        write_private(marker_path, marker_bytes)
    except FileExistsError as exc:
        raise UpdateError("qualification execution interruption already recorded; replay is forbidden") from exc
    fsync_directory(state["activation"])
    return {
        "status": "recovered",
        "candidateId": args.candidate_id,
        "activationHash": state["activation_hash"],
        "executionInterruptionSha256": sha256(artifact_bytes),
        "candidateExecutionState": QUALIFICATION_EXECUTION_INTERRUPTION_STATE,
        "reason": QUALIFICATION_EXECUTION_INTERRUPTION_REASON,
        "terminalPromotionEvidence": False,
        **qualification_authority_fields(),
        "boundary": QUALIFICATION_EXECUTION_INTERRUPTION_BOUNDARY,
    }


def qualification_execution_interruption_record(args: argparse.Namespace) -> dict[str, Any]:
    if not args.confirm:
        raise UpdateError("qualification execution interruption record requires --confirm")
    if not isinstance(args.interruption_hash, str) or HASH.fullmatch(args.interruption_hash) is None:
        raise UpdateError("qualification execution interruption hash is invalid")
    if not isinstance(args.candidate_id, str) or re.fullmatch(
        r"pixel-[0-9]{1,6}(?:\.[0-9]{1,6}){2}-[0-9a-f]{64}", args.candidate_id,
    ) is None:
        raise UpdateError("qualification candidate ID is invalid")
    if sys.platform != "linux":
        raise UpdateError("qualification execution interruption is supported only on qualified Linux hosts")
    if not args.qualification_root.is_absolute():
        raise UpdateError("qualification root path must be absolute")
    semantic_version(args.baseline_version, "qualification baseline version")
    with exclusive_stage_lock(args.qualification_root):
        activation = args.qualification_root / "activations" / args.candidate_id
        run_dir = args.qualification_root / "runs" / args.candidate_id
        artifact_path = run_dir / QUALIFICATION_EXECUTION_INTERRUPTION_FILE
        marker_path = activation / QUALIFICATION_EXECUTION_INTERRUPTION_MARKER
        artifact_exists = artifact_path.exists() or artifact_path.is_symlink()
        marker_exists = marker_path.exists() or marker_path.is_symlink()
        if artifact_exists and marker_exists:
            state = _qualification_execution_state_locked(
                args,
                activation_files=set(QUALIFICATION_ACTIVATION_INTERRUPTED_FILES),
                run_files=set(QUALIFICATION_RUN_INTERRUPTED_FILES),
            )
            claim, claim_bytes, spec, spec_bytes, start, start_bytes, intent_hash = (
                _qualification_execution_interruption_bindings(state)
            )
            if not hmac.compare_digest(intent_hash, args.interruption_hash):
                raise UpdateError("qualification execution interruption hash differs from the revalidated host-run")
            _validate_interruption_artifacts(
                artifact_path, marker_path,
                harness=state["harness"], claim_bytes=claim_bytes, spec_bytes=spec_bytes,
                start_bytes=start_bytes, spec=spec, start=start,
            )
            raise UpdateError("qualification execution interruption already recorded; replay is forbidden")
        if marker_exists and not artifact_exists:
            raise UpdateError("qualification execution interruption marker exists without its artifact; recovery required")
        if artifact_exists:
            return _repair_execution_interruption_marker(args, artifact_path, marker_path)
        state = _qualification_execution_state_locked(
            args,
            activation_files=set(QUALIFICATION_ACTIVATION_CLAIMED_FILES),
            run_files=set(QUALIFICATION_RUN_STARTED_FILES),
        )
        claim, claim_bytes, spec, spec_bytes, start, start_bytes, intent_hash = (
            _qualification_execution_interruption_bindings(state)
        )
        if not hmac.compare_digest(intent_hash, args.interruption_hash):
            raise UpdateError("qualification execution interruption hash differs from the revalidated host-run")
        custody = qualification_execution_interruption_custody(state, start_bytes, spec)
        recorded_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        interruption = qualification_execution_interruption_value(
            state["harness"], claim_bytes, spec_bytes, start_bytes, spec, start, recorded_at,
        )
        interruption_bytes = canonical_json(interruption)
        marker = qualification_execution_interruption_marker_value(
            state["harness"], claim_bytes, interruption_bytes, recorded_at,
        )
        marker_bytes = canonical_json(marker)
        try:
            write_private(artifact_path, interruption_bytes)
        except FileExistsError as exc:
            raise UpdateError("qualification execution interruption already recorded; replay is forbidden") from exc
        fsync_directory(state["run_dir"])
        try:
            write_private(marker_path, marker_bytes)
        except FileExistsError as exc:
            raise UpdateError("qualification execution interruption already recorded; replay is forbidden") from exc
        fsync_directory(state["activation"])
        return {
            "status": "recorded",
            "candidateId": args.candidate_id,
            "activationHash": state["activation_hash"],
            "executionClaimSha256": sha256(claim_bytes),
            "executionSpecSha256": sha256(spec_bytes),
            "executionStartSha256": sha256(start_bytes),
            "executionInterruptionSha256": sha256(interruption_bytes),
            "candidateExecutionState": QUALIFICATION_EXECUTION_INTERRUPTION_STATE,
            "reason": QUALIFICATION_EXECUTION_INTERRUPTION_REASON,
            "containerAbsent": custody["containerAbsent"],
            "containerRemoved": custody["containerRemoved"],
            "terminalPromotionEvidence": False,
            **qualification_authority_fields(),
            "boundary": QUALIFICATION_EXECUTION_INTERRUPTION_BOUNDARY,
        }


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Sign, inspect, stage, rehearse, or activate an exact Pixel release update")
    commands = value.add_subparsers(dest="command", required=True)
    signer = commands.add_parser("sign", help="sign a fully validated local release bundle")
    signer.add_argument("--envelope", required=True, type=Path)
    signer.add_argument("--signing-key", required=True, type=Path)
    signer.add_argument("--confirm", action="store_true")
    qualification_signer = commands.add_parser(
        "qualification-sign", help="sign a Candidate bundle for non-production qualification only",
    )
    qualification_signer.add_argument("--envelope", required=True, type=Path)
    qualification_signer.add_argument("--signing-key", required=True, type=Path)
    qualification_signer.add_argument("--confirm", action="store_true")
    inspector = commands.add_parser("inspect", help="verify a signed bundle without extracting or executing it")
    inspector.add_argument("--envelope", required=True, type=Path)
    inspector.add_argument("--allowed-signers", required=True, type=Path)
    inspector.add_argument("--identity", required=True)
    qualification_inspector = commands.add_parser(
        "qualification-inspect", help="verify a Candidate qualification signature without granting update authority",
    )
    qualification_inspector.add_argument("--envelope", required=True, type=Path)
    qualification_inspector.add_argument("--allowed-signers", required=True, type=Path)
    qualification_inspector.add_argument("--identity", required=True)
    preparer = commands.add_parser("prepare", help="copy one verified forward release into private staging")
    preparer.add_argument("--envelope", required=True, type=Path)
    preparer.add_argument("--allowed-signers", required=True, type=Path)
    preparer.add_argument("--identity", required=True)
    preparer.add_argument("--staging-root", required=True, type=Path)
    preparer.add_argument("--confirm", action="store_true")
    rehearsal = commands.add_parser("rehearse", help="extract and parse one staged candidate without executing it")
    rehearsal.add_argument("--candidate-id", required=True)
    rehearsal.add_argument("--allowed-signers", required=True, type=Path)
    rehearsal.add_argument("--identity", required=True)
    rehearsal.add_argument("--staging-root", required=True, type=Path)
    rehearsal.add_argument("--confirm", action="store_true")
    activation_view = commands.add_parser("activation-preview", help="derive an exact activation hash without executing candidate code")
    activation_view.add_argument("--candidate-id", required=True)
    activation_view.add_argument("--allowed-signers", required=True, type=Path)
    activation_view.add_argument("--identity", required=True)
    activation_view.add_argument("--staging-root", required=True, type=Path)
    activation_claim = commands.add_parser("activation-claim", help="atomically claim one exact activation without executing candidate code")
    activation_claim.add_argument("--candidate-id", required=True)
    activation_claim.add_argument("--allowed-signers", required=True, type=Path)
    activation_claim.add_argument("--identity", required=True)
    activation_claim.add_argument("--staging-root", required=True, type=Path)
    activation_claim.add_argument("--activation-hash", required=True)
    activation_claim.add_argument("--confirm", action="store_true")
    activation_result = commands.add_parser("activation-result", help="record the content-free result of one claimed activation")
    activation_result.add_argument("--candidate-id", required=True)
    activation_result.add_argument("--allowed-signers", required=True, type=Path)
    activation_result.add_argument("--identity", required=True)
    activation_result.add_argument("--staging-root", required=True, type=Path)
    activation_result.add_argument("--activation-hash", required=True)
    activation_result.add_argument("--outcome", required=True)
    activation_result.add_argument("--phase", required=True)
    activation_result.add_argument("--active-version-file", required=True, type=Path)
    activation_result.add_argument("--rollback-marker", type=Path)
    activation_result.add_argument("--confirm", action="store_true")
    reactivation_view = commands.add_parser(
        "reactivation-preview", help="derive an exact post-rollback reactivation hash",
    )
    reactivation_view.add_argument("--candidate-id", required=True)
    reactivation_view.add_argument("--allowed-signers", required=True, type=Path)
    reactivation_view.add_argument("--identity", required=True)
    reactivation_view.add_argument("--staging-root", required=True, type=Path)
    reactivation_view.add_argument("--activation-hash", required=True)
    reactivation_view.add_argument("--active-version-file", required=True, type=Path)
    reactivation_view.add_argument("--rollback-marker", required=True, type=Path)
    reactivation_claim = commands.add_parser(
        "reactivation-claim", help="atomically claim one exact post-rollback reactivation",
    )
    reactivation_claim.add_argument("--candidate-id", required=True)
    reactivation_claim.add_argument("--allowed-signers", required=True, type=Path)
    reactivation_claim.add_argument("--identity", required=True)
    reactivation_claim.add_argument("--staging-root", required=True, type=Path)
    reactivation_claim.add_argument("--activation-hash", required=True)
    reactivation_claim.add_argument("--reactivation-hash", required=True)
    reactivation_claim.add_argument("--active-version-file", required=True, type=Path)
    reactivation_claim.add_argument("--rollback-marker", required=True, type=Path)
    reactivation_claim.add_argument("--confirm", action="store_true")
    reactivation_result = commands.add_parser(
        "reactivation-result", help="record the content-free result of one claimed reactivation",
    )
    reactivation_result.add_argument("--candidate-id", required=True)
    reactivation_result.add_argument("--allowed-signers", required=True, type=Path)
    reactivation_result.add_argument("--identity", required=True)
    reactivation_result.add_argument("--staging-root", required=True, type=Path)
    reactivation_result.add_argument("--activation-hash", required=True)
    reactivation_result.add_argument("--reactivation-hash", required=True)
    reactivation_result.add_argument("--active-version-file", required=True, type=Path)
    reactivation_result.add_argument("--rollback-marker", type=Path)
    reactivation_result.add_argument("--outcome", required=True)
    reactivation_result.add_argument("--phase", required=True)
    reactivation_result.add_argument("--confirm", action="store_true")
    reactivation_rollback_view = commands.add_parser(
        "reactivation-rollback-preview", help="derive an exact rollback hash for a reactivated release",
    )
    reactivation_rollback_view.add_argument("--candidate-id", required=True)
    reactivation_rollback_view.add_argument("--allowed-signers", required=True, type=Path)
    reactivation_rollback_view.add_argument("--identity", required=True)
    reactivation_rollback_view.add_argument("--staging-root", required=True, type=Path)
    reactivation_rollback_view.add_argument("--activation-hash", required=True)
    reactivation_rollback_view.add_argument("--reactivation-hash", required=True)
    reactivation_rollback_view.add_argument("--active-version-file", required=True, type=Path)
    reactivation_rollback_view.add_argument("--rollback-marker", required=True, type=Path)
    reactivation_rollback_claim = commands.add_parser(
        "reactivation-rollback-claim", help="claim one exact rollback of a reactivated release",
    )
    reactivation_rollback_claim.add_argument("--candidate-id", required=True)
    reactivation_rollback_claim.add_argument("--allowed-signers", required=True, type=Path)
    reactivation_rollback_claim.add_argument("--identity", required=True)
    reactivation_rollback_claim.add_argument("--staging-root", required=True, type=Path)
    reactivation_rollback_claim.add_argument("--activation-hash", required=True)
    reactivation_rollback_claim.add_argument("--reactivation-hash", required=True)
    reactivation_rollback_claim.add_argument("--rollback-hash", required=True)
    reactivation_rollback_claim.add_argument("--active-version-file", required=True, type=Path)
    reactivation_rollback_claim.add_argument("--rollback-marker", required=True, type=Path)
    reactivation_rollback_claim.add_argument("--confirm", action="store_true")
    reactivation_rollback_result = commands.add_parser(
        "reactivation-rollback-result", help="record trusted rollback of a reactivated release",
    )
    reactivation_rollback_result.add_argument("--candidate-id", required=True)
    reactivation_rollback_result.add_argument("--allowed-signers", required=True, type=Path)
    reactivation_rollback_result.add_argument("--identity", required=True)
    reactivation_rollback_result.add_argument("--staging-root", required=True, type=Path)
    reactivation_rollback_result.add_argument("--activation-hash", required=True)
    reactivation_rollback_result.add_argument("--reactivation-hash", required=True)
    reactivation_rollback_result.add_argument("--rollback-hash", required=True)
    reactivation_rollback_result.add_argument("--active-version-file", required=True, type=Path)
    reactivation_rollback_result.add_argument("--rollback-marker", required=True, type=Path)
    reactivation_rollback_result.add_argument("--outcome", required=True)
    reactivation_rollback_result.add_argument("--phase", required=True)
    reactivation_rollback_result.add_argument("--confirm", action="store_true")
    reactivation_recovery_view = commands.add_parser(
        "reactivation-recovery-preview", help="diagnose interrupted reactivation receipts",
    )
    reactivation_recovery_view.add_argument("--candidate-id", required=True)
    reactivation_recovery_view.add_argument("--allowed-signers", required=True, type=Path)
    reactivation_recovery_view.add_argument("--identity", required=True)
    reactivation_recovery_view.add_argument("--staging-root", required=True, type=Path)
    reactivation_recovery_view.add_argument("--activation-hash", required=True)
    reactivation_recovery_view.add_argument("--reactivation-hash", required=True)
    reactivation_recovery_view.add_argument("--active-version-file", required=True, type=Path)
    reactivation_recovery_view.add_argument("--rollback-marker", required=True, type=Path)
    reactivation_recovery_finalizer = commands.add_parser(
        "reactivation-recovery-finalize", help="write one exact missing reactivation receipt",
    )
    reactivation_recovery_finalizer.add_argument("--candidate-id", required=True)
    reactivation_recovery_finalizer.add_argument("--allowed-signers", required=True, type=Path)
    reactivation_recovery_finalizer.add_argument("--identity", required=True)
    reactivation_recovery_finalizer.add_argument("--staging-root", required=True, type=Path)
    reactivation_recovery_finalizer.add_argument("--activation-hash", required=True)
    reactivation_recovery_finalizer.add_argument("--reactivation-hash", required=True)
    reactivation_recovery_finalizer.add_argument("--active-version-file", required=True, type=Path)
    reactivation_recovery_finalizer.add_argument("--rollback-marker", required=True, type=Path)
    reactivation_recovery_finalizer.add_argument("--recovery-hash", required=True)
    reactivation_recovery_finalizer.add_argument("--confirm", action="store_true")
    rollback_view = commands.add_parser("rollback-preview", help="derive an exact update-bound rollback hash")
    rollback_view.add_argument("--candidate-id", required=True)
    rollback_view.add_argument("--allowed-signers", required=True, type=Path)
    rollback_view.add_argument("--identity", required=True)
    rollback_view.add_argument("--staging-root", required=True, type=Path)
    rollback_view.add_argument("--activation-hash", required=True)
    rollback_view.add_argument("--active-version-file", required=True, type=Path)
    rollback_view.add_argument("--rollback-marker", required=True, type=Path)
    rollback_claim = commands.add_parser("rollback-claim", help="claim one exact update-bound rollback")
    rollback_claim.add_argument("--candidate-id", required=True)
    rollback_claim.add_argument("--allowed-signers", required=True, type=Path)
    rollback_claim.add_argument("--identity", required=True)
    rollback_claim.add_argument("--staging-root", required=True, type=Path)
    rollback_claim.add_argument("--activation-hash", required=True)
    rollback_claim.add_argument("--rollback-hash", required=True)
    rollback_claim.add_argument("--active-version-file", required=True, type=Path)
    rollback_claim.add_argument("--rollback-marker", required=True, type=Path)
    rollback_claim.add_argument("--confirm", action="store_true")
    rollback_result = commands.add_parser("rollback-result", help="record the content-free result of trusted update rollback")
    rollback_result.add_argument("--candidate-id", required=True)
    rollback_result.add_argument("--allowed-signers", required=True, type=Path)
    rollback_result.add_argument("--identity", required=True)
    rollback_result.add_argument("--staging-root", required=True, type=Path)
    rollback_result.add_argument("--activation-hash", required=True)
    rollback_result.add_argument("--rollback-hash", required=True)
    rollback_result.add_argument("--active-version-file", required=True, type=Path)
    rollback_result.add_argument("--rollback-marker", required=True, type=Path)
    rollback_result.add_argument("--outcome", required=True)
    rollback_result.add_argument("--phase", required=True)
    rollback_result.add_argument("--confirm", action="store_true")
    recovery_view = commands.add_parser("recovery-preview", help="diagnose interrupted update receipt state without executing candidate code")
    recovery_view.add_argument("--candidate-id", required=True)
    recovery_view.add_argument("--allowed-signers", required=True, type=Path)
    recovery_view.add_argument("--identity", required=True)
    recovery_view.add_argument("--staging-root", required=True, type=Path)
    recovery_view.add_argument("--activation-hash", required=True)
    recovery_view.add_argument("--active-version-file", required=True, type=Path)
    recovery_view.add_argument("--rollback-marker", required=True, type=Path)
    recovery_finalizer = commands.add_parser("recovery-finalize", help="write one exact missing trusted update receipt")
    recovery_finalizer.add_argument("--candidate-id", required=True)
    recovery_finalizer.add_argument("--allowed-signers", required=True, type=Path)
    recovery_finalizer.add_argument("--identity", required=True)
    recovery_finalizer.add_argument("--staging-root", required=True, type=Path)
    recovery_finalizer.add_argument("--activation-hash", required=True)
    recovery_finalizer.add_argument("--active-version-file", required=True, type=Path)
    recovery_finalizer.add_argument("--rollback-marker", required=True, type=Path)
    recovery_finalizer.add_argument("--recovery-hash", required=True)
    recovery_finalizer.add_argument("--confirm", action="store_true")
    cleanup_view = commands.add_parser("cleanup-preview", help="preview exact cleanup of one completed rolled-back update")
    cleanup_view.add_argument("--candidate-id", required=True)
    cleanup_view.add_argument("--allowed-signers", required=True, type=Path)
    cleanup_view.add_argument("--identity", required=True)
    cleanup_view.add_argument("--staging-root", required=True, type=Path)
    cleanup_view.add_argument("--activation-hash", required=True)
    cleanup_view.add_argument("--active-version-file", required=True, type=Path)
    cleanup_view.add_argument("--rollback-marker", required=True, type=Path)
    cleanup_runner = commands.add_parser("cleanup", help="quarantine and delete exact completed update copies after preserving an audit tombstone")
    cleanup_runner.add_argument("--candidate-id", required=True)
    cleanup_runner.add_argument("--allowed-signers", required=True, type=Path)
    cleanup_runner.add_argument("--identity", required=True)
    cleanup_runner.add_argument("--staging-root", required=True, type=Path)
    cleanup_runner.add_argument("--activation-hash", required=True)
    cleanup_runner.add_argument("--active-version-file", required=True, type=Path)
    cleanup_runner.add_argument("--rollback-marker", required=True, type=Path)
    cleanup_runner.add_argument("--cleanup-hash", required=True)
    cleanup_runner.add_argument("--confirm", action="store_true")
    archive_view = commands.add_parser("archive-preview", help="preview exact preservation of one terminal failed rollback outside bounded staging")
    archive_view.add_argument("--candidate-id", required=True)
    archive_view.add_argument("--allowed-signers", required=True, type=Path)
    archive_view.add_argument("--identity", required=True)
    archive_view.add_argument("--staging-root", required=True, type=Path)
    archive_view.add_argument("--archive-root", required=True, type=Path)
    archive_view.add_argument("--activation-hash", required=True)
    archive_view.add_argument("--active-version-file", required=True, type=Path)
    archive_view.add_argument("--rollback-marker", required=True, type=Path)
    archive_runner = commands.add_parser("archive", help="atomically preserve one terminal failed rollback outside bounded staging")
    archive_runner.add_argument("--candidate-id", required=True)
    archive_runner.add_argument("--allowed-signers", required=True, type=Path)
    archive_runner.add_argument("--identity", required=True)
    archive_runner.add_argument("--staging-root", required=True, type=Path)
    archive_runner.add_argument("--archive-root", required=True, type=Path)
    archive_runner.add_argument("--activation-hash", required=True)
    archive_runner.add_argument("--active-version-file", required=True, type=Path)
    archive_runner.add_argument("--rollback-marker", required=True, type=Path)
    archive_runner.add_argument("--archive-hash", required=True)
    archive_runner.add_argument("--confirm", action="store_true")
    reactivation_archive_view = commands.add_parser(
        "reactivation-archive-preview",
        help="preview exact preservation of one terminal no-live-mutation reactivation failure",
    )
    reactivation_archive_view.add_argument("--candidate-id", required=True)
    reactivation_archive_view.add_argument("--allowed-signers", required=True, type=Path)
    reactivation_archive_view.add_argument("--identity", required=True)
    reactivation_archive_view.add_argument("--staging-root", required=True, type=Path)
    reactivation_archive_view.add_argument("--archive-root", required=True, type=Path)
    reactivation_archive_view.add_argument("--controller-root", required=True, type=Path)
    reactivation_archive_view.add_argument("--controller-envelope", required=True, type=Path)
    reactivation_archive_view.add_argument("--activation-hash", required=True)
    reactivation_archive_view.add_argument("--active-version-file", required=True, type=Path)
    reactivation_archive_view.add_argument("--rollback-marker", required=True, type=Path)
    reactivation_archive_runner = commands.add_parser(
        "reactivation-archive",
        help="atomically preserve one terminal no-live-mutation reactivation failure",
    )
    reactivation_archive_runner.add_argument("--candidate-id", required=True)
    reactivation_archive_runner.add_argument("--allowed-signers", required=True, type=Path)
    reactivation_archive_runner.add_argument("--identity", required=True)
    reactivation_archive_runner.add_argument("--staging-root", required=True, type=Path)
    reactivation_archive_runner.add_argument("--archive-root", required=True, type=Path)
    reactivation_archive_runner.add_argument("--controller-root", required=True, type=Path)
    reactivation_archive_runner.add_argument("--controller-envelope", required=True, type=Path)
    reactivation_archive_runner.add_argument("--activation-hash", required=True)
    reactivation_archive_runner.add_argument("--active-version-file", required=True, type=Path)
    reactivation_archive_runner.add_argument("--rollback-marker", required=True, type=Path)
    reactivation_archive_runner.add_argument("--archive-hash", required=True)
    reactivation_archive_runner.add_argument("--confirm", action="store_true")
    qualification_preparer = commands.add_parser(
        "qualification-prepare", help="copy one exact Candidate bundle into a disposable qualification root only",
    )
    qualification_preparer.add_argument("--envelope", required=True, type=Path)
    qualification_preparer.add_argument("--allowed-signers", required=True, type=Path)
    qualification_preparer.add_argument("--identity", required=True)
    qualification_preparer.add_argument("--qualification-root", required=True, type=Path)
    qualification_preparer.add_argument("--baseline-version", required=True)
    qualification_preparer.add_argument("--production-install-root", required=True, type=Path)
    qualification_preparer.add_argument("--confirm", action="store_true")
    qualification_rehearsal = commands.add_parser(
        "qualification-rehearse", help="extract and parse one qualification-staged candidate without executing it",
    )
    qualification_rehearsal.add_argument("--candidate-id", required=True)
    qualification_rehearsal.add_argument("--allowed-signers", required=True, type=Path)
    qualification_rehearsal.add_argument("--identity", required=True)
    qualification_rehearsal.add_argument("--qualification-root", required=True, type=Path)
    qualification_rehearsal.add_argument("--baseline-version", required=True)
    qualification_rehearsal.add_argument("--production-install-root", required=True, type=Path)
    qualification_rehearsal.add_argument("--confirm", action="store_true")
    qualification_activation_view = commands.add_parser(
        "qualification-activation-preview", help="derive an exact qualification activation hash without executing candidate code",
    )
    qualification_activation_view.add_argument("--candidate-id", required=True)
    qualification_activation_view.add_argument("--allowed-signers", required=True, type=Path)
    qualification_activation_view.add_argument("--identity", required=True)
    qualification_activation_view.add_argument("--qualification-root", required=True, type=Path)
    qualification_activation_view.add_argument("--baseline-version", required=True)
    qualification_activation_view.add_argument("--production-install-root", required=True, type=Path)
    qualification_activation_claim = commands.add_parser(
        "qualification-activation-claim", help="atomically claim one qualification-root-only activation without executing candidate code",
    )
    qualification_activation_claim.add_argument("--candidate-id", required=True)
    qualification_activation_claim.add_argument("--allowed-signers", required=True, type=Path)
    qualification_activation_claim.add_argument("--identity", required=True)
    qualification_activation_claim.add_argument("--qualification-root", required=True, type=Path)
    qualification_activation_claim.add_argument("--baseline-version", required=True)
    qualification_activation_claim.add_argument("--production-install-root", required=True, type=Path)
    qualification_activation_claim.add_argument("--activation-hash", required=True)
    qualification_activation_claim.add_argument("--confirm", action="store_true")
    qualification_host_run = commands.add_parser(
        "qualification-host-run", help="atomically acquire one private host-run for an already-claimed qualification activation without executing candidate code",
    )
    qualification_host_run.add_argument("--candidate-id", required=True)
    qualification_host_run.add_argument("--allowed-signers", required=True, type=Path)
    qualification_host_run.add_argument("--identity", required=True)
    qualification_host_run.add_argument("--qualification-root", required=True, type=Path)
    qualification_host_run.add_argument("--baseline-version", required=True)
    qualification_host_run.add_argument("--production-install-root", required=True, type=Path)
    qualification_host_run.add_argument("--confirm", action="store_true")
    qualification_execution_claim = commands.add_parser(
        "qualification-execution-claim", help="atomically write the durable qualification execution claim before any candidate process may start (requires --confirm)",
    )
    qualification_execution_claim.add_argument("--candidate-id", required=True)
    qualification_execution_claim.add_argument("--allowed-signers", required=True, type=Path)
    qualification_execution_claim.add_argument("--identity", required=True)
    qualification_execution_claim.add_argument("--qualification-root", required=True, type=Path)
    qualification_execution_claim.add_argument("--baseline-version", required=True)
    qualification_execution_claim.add_argument("--production-install-root", required=True, type=Path)
    qualification_execution_claim.add_argument("--probe", required=True, help="relative path to a bounded candidate command within the verified activation source")
    qualification_execution_claim.add_argument("--probe-timeout", type=float, default=30.0)
    qualification_execution_claim.add_argument("--confirm", action="store_true")
    qualification_execution_result = commands.add_parser(
        "qualification-execution-result", help="record the immutable content-free result of one claimed qualification execution (requires --confirm)",
    )
    qualification_execution_result.add_argument("--candidate-id", required=True)
    qualification_execution_result.add_argument("--allowed-signers", required=True, type=Path)
    qualification_execution_result.add_argument("--identity", required=True)
    qualification_execution_result.add_argument("--qualification-root", required=True, type=Path)
    qualification_execution_result.add_argument("--baseline-version", required=True)
    qualification_execution_result.add_argument("--production-install-root", required=True, type=Path)
    qualification_execution_result.add_argument("--confirm", action="store_true")
    qualification_execution_interruption_preview = commands.add_parser(
        "qualification-execution-interruption-preview", help="inertly derive the exact terminal interruption hash for one post-start indeterminate qualification execution without writing or mutating anything",
    )
    qualification_execution_interruption_preview.add_argument("--candidate-id", required=True)
    qualification_execution_interruption_preview.add_argument("--allowed-signers", required=True, type=Path)
    qualification_execution_interruption_preview.add_argument("--identity", required=True)
    qualification_execution_interruption_preview.add_argument("--qualification-root", required=True, type=Path)
    qualification_execution_interruption_preview.add_argument("--baseline-version", required=True)
    qualification_execution_interruption_preview.add_argument("--production-install-root", required=True, type=Path)
    qualification_execution_interruption_record = commands.add_parser(
        "qualification-execution-interruption-record", help="write the two private immutable terminal interruption artifacts for one post-start indeterminate qualification execution (requires --confirm plus the exact preview hash)",
    )
    qualification_execution_interruption_record.add_argument("--candidate-id", required=True)
    qualification_execution_interruption_record.add_argument("--allowed-signers", required=True, type=Path)
    qualification_execution_interruption_record.add_argument("--identity", required=True)
    qualification_execution_interruption_record.add_argument("--qualification-root", required=True, type=Path)
    qualification_execution_interruption_record.add_argument("--baseline-version", required=True)
    qualification_execution_interruption_record.add_argument("--production-install-root", required=True, type=Path)
    qualification_execution_interruption_record.add_argument("--interruption-hash", required=True)
    qualification_execution_interruption_record.add_argument("--confirm", action="store_true")
    return value


def main() -> int:
    args = parser().parse_args()
    try:
        operations = {
            "sign": sign, "qualification-sign": qualification_sign,
            "inspect": inspect, "qualification-inspect": qualification_inspect,
            "prepare": prepare, "rehearse": rehearse,
            "activation-preview": activation_preview, "activation-claim": claim_activation,
            "activation-result": record_activation_result,
            "reactivation-preview": reactivation_preview,
            "reactivation-claim": claim_reactivation,
            "reactivation-result": record_reactivation_result,
            "reactivation-rollback-preview": reactivation_rollback_preview,
            "reactivation-rollback-claim": claim_reactivation_rollback,
            "reactivation-rollback-result": record_reactivation_rollback,
            "reactivation-recovery-preview": reactivation_recovery_preview,
            "reactivation-recovery-finalize": finalize_reactivation_recovery,
            "rollback-preview": rollback_preview, "rollback-claim": claim_update_rollback,
            "rollback-result": record_update_rollback,
            "recovery-preview": recovery_preview, "recovery-finalize": finalize_recovery,
            "cleanup-preview": cleanup_preview, "cleanup": cleanup_completed_update,
            "archive-preview": archive_preview, "archive": archive_failed_update,
            "reactivation-archive-preview": reactivation_archive_preview,
            "reactivation-archive": reactivation_archive_failed_update,
            "qualification-prepare": qualification_prepare,
            "qualification-rehearse": qualification_rehearse,
            "qualification-activation-preview": qualification_activation_preview,
            "qualification-activation-claim": qualification_claim_activation,
            "qualification-host-run": qualification_host_run,
            "qualification-execution-claim": qualification_execution_claim,
            "qualification-execution-result": qualification_execution_result,
            "qualification-execution-interruption-preview": qualification_execution_interruption_preview,
            "qualification-execution-interruption-record": qualification_execution_interruption_record,
        }
        result = operations[args.command](args)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (UpdateError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(f"Pixel release update rejected: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
