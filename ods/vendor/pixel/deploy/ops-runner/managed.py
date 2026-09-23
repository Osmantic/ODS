#!/usr/bin/env python3
"""Transactional root-side actions for the Pixel Operations runner.

This helper is intended to be root-owned and callable only through an explicit sudoers
entry for the dedicated pixel-runner identity. Every unit, deployment, command vector,
artifact root, and rollback path comes from a root-owned configuration file.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any


TESTING = os.environ.get("PIXEL_OPS_MANAGED_TESTING") == "1" and getattr(os, "geteuid", lambda: 1)() != 0
CONFIG = Path(os.environ.get("PIXEL_OPS_MANAGED_CONFIG", "/etc/pixel-ops-runner/managed.json")) if TESTING else Path("/etc/pixel-ops-runner/managed.json")
SAFE_ID = re.compile(r"[a-z][a-z0-9_.-]{0,63}")
SAFE_RELEASE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
SHA256 = re.compile(r"[a-f0-9]{64}")
MAX_CONFIG = 1024 * 1024
MAX_OUTPUT = 128 * 1024


class ManagedError(RuntimeError):
    pass


def safe_id(value: str, label: str) -> str:
    if not SAFE_ID.fullmatch(value):
        raise ManagedError(f"unsafe {label}")
    return value


def secure_json(path: Path) -> dict[str, Any]:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_CONFIG:
        raise ManagedError("managed configuration must be a bounded regular file")
    if not TESTING and (info.st_uid != 0 or info.st_mode & 0o022):
        raise ManagedError("managed configuration must be root-owned and not group/world writable")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode) or opened.st_size > MAX_CONFIG
            or (opened.st_dev, opened.st_ino, opened.st_size) != (info.st_dev, info.st_ino, info.st_size)
        ):
            raise ManagedError("managed configuration changed during secure open")
        chunks, total = [], 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, MAX_CONFIG + 1 - total))
            if not chunk:
                break
            chunks.append(chunk); total += len(chunk)
            if total > MAX_CONFIG:
                raise ManagedError("managed configuration exceeds its size limit")
        payload = b"".join(chunks)
    finally:
        os.close(descriptor)
    if not stat.S_ISREG(opened.st_mode) or len(payload) > MAX_CONFIG:
        raise ManagedError("managed configuration changed or exceeds its limit")
    value = json.loads(payload)
    if not isinstance(value, dict) or value.get("schemaVersion") != 1:
        raise ManagedError("managed configuration must use schemaVersion 1")
    return value


def configured_argv(value: Any, substitutions: dict[str, str] | None = None) -> list[str]:
    if (
        not isinstance(value, list) or not 1 <= len(value) <= 128
        or any(not isinstance(item, str) or "\x00" in item or len(item) > 16_384 for item in value)
    ):
        raise ManagedError("managed command must be a fixed argv array")
    substitutions = substitutions or {}
    result = []
    for argument in value:
        rendered = argument
        for name, replacement in substitutions.items():
            rendered = rendered.replace("{" + name + "}", replacement)
        if re.search(r"\{[A-Za-z][A-Za-z0-9]*\}", rendered):
            raise ManagedError("managed command contains an unknown placeholder")
        result.append(rendered)
    if not Path(result[0]).is_absolute():
        raise ManagedError("managed command executable must use an absolute path")
    return result


def bounded_integer(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ManagedError(f"{label} must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as error:
        raise ManagedError(f"{label} must be an integer") from error
    if isinstance(value, float) and not value.is_integer():
        raise ManagedError(f"{label} must be an integer")
    if not minimum <= number <= maximum:
        raise ManagedError(f"{label} is outside its safe range")
    return number


def run(argv: list[str], timeout: int = 300, *, check: bool = True) -> dict[str, Any]:
    deadline = time.monotonic() + min(max(timeout, 1), 3600)
    process = subprocess.Popen(
        argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True,
        env={"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"},
    )
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    truncated = {"stdout": False, "stderr": False}

    def drain(name: str, stream) -> None:
        while True:
            try:
                chunk = stream.read(8192)
            except (OSError, ValueError):
                return
            if not chunk:
                return
            remaining = MAX_OUTPUT - len(captured[name])
            if remaining > 0:
                captured[name].extend(chunk[:remaining])
            if len(chunk) > max(remaining, 0):
                truncated[name] = True

    assert process.stdout is not None and process.stderr is not None
    readers = [threading.Thread(target=drain, args=(name, stream), daemon=True) for name, stream in (("stdout", process.stdout), ("stderr", process.stderr))]
    for reader in readers:
        reader.start()
    timed_out = False
    while process.poll() is None:
        if time.monotonic() >= deadline:
            timed_out = True
            try: os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError: pass
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try: os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                process.wait(timeout=5)
            break
        time.sleep(0.05)
    try: os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError: pass
    for stream in (process.stdout, process.stderr):
        try: stream.close()
        except OSError: pass
    for reader in readers:
        reader.join(timeout=2)
    if timed_out:
        raise ManagedError(f"configured command {Path(argv[0]).name} exceeded its hard timeout")
    result = {
        "argv0": Path(argv[0]).name, "exitCode": process.returncode,
        "stdout": captured["stdout"].decode("utf-8", "replace"),
        "stderr": captured["stderr"].decode("utf-8", "replace"),
        "outputTruncated": truncated["stdout"] or truncated["stderr"],
    }
    if check and process.returncode != 0:
        raise ManagedError(f"configured command {Path(argv[0]).name} exited with {process.returncode}: {result['stderr'][:1000]}")
    return result


def write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise ManagedError("artifact write made no progress")
        view = view[written:]


def service(configuration: dict[str, Any], service_id: str) -> dict[str, Any]:
    service_id = safe_id(service_id, "service id")
    item = configuration.get("services", {}).get(service_id)
    if not isinstance(item, dict):
        raise ManagedError("service is not configured")
    unit = str(item.get("unit", ""))
    if not SAFE_ID.fullmatch(unit.removesuffix(".service")):
        raise ManagedError("configured service unit is unsafe")
    return {**item, "id": service_id, "unit": unit}


def systemctl(*arguments: str, check: bool = True) -> dict[str, Any]:
    return run(["/usr/bin/systemctl", *arguments], timeout=120, check=check)


def active_state(unit: str) -> str:
    result = systemctl("is-active", unit, check=False)
    return result["stdout"].strip() or "unknown"


def verify_service(item: dict[str, Any]) -> dict[str, Any]:
    if item.get("verifyCommand"):
        return run(configured_argv(item["verifyCommand"]), timeout=int(item.get("verifyTimeoutSeconds", 120)))
    result = systemctl("is-active", item["unit"], check=False)
    if result["stdout"].strip() != "active":
        raise ManagedError(f"service {item['id']} is not active")
    return result


def restore_service(item: dict[str, Any], before: str) -> dict[str, Any]:
    if before == "active":
        systemctl("restart", item["unit"])
    else:
        systemctl("stop", item["unit"])
    return {"restoredState": active_state(item["unit"]), "expectedState": before}


def service_action(configuration: dict[str, Any], arguments: list[str]) -> dict[str, Any]:
    if len(arguments) != 2:
        raise ManagedError("service action requires operation and service id")
    operation, service_id = arguments
    item = service(configuration, service_id)
    if operation == "verify":
        return {"operation": "service.verify", "service": service_id, "evidence": verify_service(item)}
    if operation == "restart":
        before = active_state(item["unit"])
        try:
            apply_result = systemctl("restart", item["unit"])
            verify_result = verify_service(item)
            return {
                "operation": "service.restart", "service": service_id, "before": before,
                "after": active_state(item["unit"]), "applied": apply_result, "verified": verify_result,
                "rolledBack": False,
            }
        except Exception as error:
            rollback = restore_service(item, before)
            raise ManagedError(json.dumps({
                "operation": "service.restart", "service": service_id, "error": str(error),
                "rolledBack": True, "rollback": rollback,
            }, sort_keys=True)) from error
    if operation == "rollback":
        desired = str(item.get("rollbackState", "active"))
        if desired not in {"active", "inactive"}:
            raise ManagedError("configured rollback state is invalid")
        rollback = restore_service(item, desired)
        return {"operation": "service.rollback", "service": service_id, "rollback": rollback}
    raise ManagedError("unknown service action")


def deployment(configuration: dict[str, Any], deployment_id: str) -> dict[str, Any]:
    deployment_id = safe_id(deployment_id, "deployment id")
    item = configuration.get("deployments", {}).get(deployment_id)
    if not isinstance(item, dict):
        raise ManagedError("deployment is not configured")
    releases = Path(str(item.get("releasesRoot", "")))
    current = Path(str(item.get("currentLink", "")))
    previous = Path(str(item.get("previousLink", current.parent / ".pixel-previous")))
    if not releases.is_absolute() or releases == Path("/") or not current.is_absolute() or not previous.is_absolute():
        raise ManagedError("deployment paths must be absolute and non-root")
    releases = releases.resolve()
    if current.parent.resolve() != previous.parent.resolve() or current == previous:
        raise ManagedError("deployment current and previous links must share a parent and be distinct")
    if not TESTING:
        for path, label in ((releases, "releasesRoot"), (current.parent.resolve(), "deployment pointer directory")):
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise ManagedError(f"{label} must be a root-owned non-writable directory")
    return {**item, "id": deployment_id, "releasesRoot": releases, "currentLink": current, "previousLink": previous}


def release_path(item: dict[str, Any], release_id: str) -> Path:
    if not SAFE_RELEASE.fullmatch(release_id) or release_id in {".", ".."}:
        raise ManagedError("unsafe release id")
    unresolved = item["releasesRoot"] / release_id
    raw_info = unresolved.lstat()
    if unresolved.is_symlink() or not stat.S_ISDIR(raw_info.st_mode):
        raise ManagedError("release is not a real directory")
    candidate = unresolved.resolve(strict=True)
    if item["releasesRoot"] not in candidate.parents or not candidate.is_dir():
        raise ManagedError("release is outside the configured root or is not a real directory")
    if not TESTING:
        info = candidate.lstat()
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise ManagedError("release must be root-owned and not group/world writable")
    return candidate


def read_link_inside(link: Path, root: Path) -> Path | None:
    if not link.exists() and not link.is_symlink():
        return None
    if not link.is_symlink():
        raise ManagedError("deployment pointer is not a symbolic link")
    target = (link.parent / os.readlink(link)).resolve(strict=True)
    if root not in target.parents or not target.is_dir():
        raise ManagedError("deployment pointer escaped releasesRoot")
    return target


def replace_link(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{link.name}.", dir=link.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    temporary.unlink()
    try:
        os.symlink(str(target), temporary)
        os.replace(temporary, link)
    finally:
        temporary.unlink(missing_ok=True)


def verify_deployment(item: dict[str, Any]) -> dict[str, Any]:
    current = read_link_inside(item["currentLink"], item["releasesRoot"])
    if current is None:
        raise ManagedError("deployment has no current release")
    evidence: dict[str, Any] = {"currentRelease": current.name}
    if item.get("verifyCommand"):
        evidence["command"] = run(configured_argv(item["verifyCommand"], {"release": str(current)}), timeout=int(item.get("verifyTimeoutSeconds", 180)))
    if item.get("service"):
        evidence["service"] = verify_service(service({"services": configuration_services(item)}, str(item["service"])))
    return evidence


def configuration_services(item: dict[str, Any]) -> dict[str, Any]:
    embedded = item.get("serviceDefinition")
    if not isinstance(embedded, dict):
        raise ManagedError("deployment serviceDefinition is missing")
    return {str(item.get("service")): embedded}


def restart_deployment_service(item: dict[str, Any]) -> None:
    if item.get("service"):
        service_item = service({"services": configuration_services(item)}, str(item["service"]))
        systemctl("restart", service_item["unit"])


def validate_configuration(configuration: dict[str, Any]) -> None:
    if not isinstance(configuration.get("allowReboot", False), bool):
        raise ManagedError("allowReboot must be boolean")
    roots = configuration.get("artifactRoots", [])
    if not isinstance(roots, list):
        raise ManagedError("artifactRoots must be an array")
    for value in roots:
        root = Path(str(value))
        if not root.is_absolute() or root == Path("/"):
            raise ManagedError("artifactRoots must contain absolute non-root paths")
    verified_root = Path(str(configuration.get("verifiedArtifactRoot", "/var/lib/pixel-ops-managed/verified")))
    if not verified_root.is_absolute() or verified_root == Path("/"):
        raise ManagedError("verifiedArtifactRoot must be an absolute non-root path")
    bounded_integer(configuration.get("maxPackageBytes", 2 * 1024 * 1024 * 1024), "maxPackageBytes", 1, 4 * 1024 * 1024 * 1024)
    for section in ("services", "deployments", "packages"):
        if not isinstance(configuration.get(section, {}), dict):
            raise ManagedError(f"{section} must be an object")
    for service_id, item in configuration.get("services", {}).items():
        value = service(configuration, str(service_id))
        if value.get("rollbackState", "active") not in {"active", "inactive"}:
            raise ManagedError("configured rollback state is invalid")
        if value.get("verifyCommand"):
            configured_argv(value["verifyCommand"])
        bounded_integer(value.get("verifyTimeoutSeconds", 120), "service verify timeout", 1, 3600)
    for deployment_id, item in configuration.get("deployments", {}).items():
        value = deployment(configuration, str(deployment_id))
        if value.get("verifyCommand"):
            configured_argv(value["verifyCommand"], {"release": "/opt/pixel-release"})
        bounded_integer(value.get("verifyTimeoutSeconds", 180), "deployment verify timeout", 1, 3600)
        if value.get("service"):
            service({"services": configuration_services(value)}, str(value["service"]))
    required = ("signatureCommand", "installCommand", "verifyCommand", "rollbackCommand", "rollbackVerifyCommand")
    for package_id, item in configuration.get("packages", {}).items():
        safe_id(str(package_id), "package id")
        if not isinstance(item, dict) or any(not item.get(name) for name in required):
            raise ManagedError("package configuration requires signature, install, verify, rollback, and rollback verification commands")
        for name in required:
            configured_argv(item[name], {"artifact": "/var/lib/pixel-ops-managed/verified/example.artifact"})
        bounded_integer(item.get("installTimeoutSeconds", 1800), "package install timeout", 1, 3600)


def deployment_action(configuration: dict[str, Any], arguments: list[str]) -> dict[str, Any]:
    if len(arguments) < 2:
        raise ManagedError("deployment action requires operation and deployment id")
    operation, deployment_id = arguments[0], arguments[1]
    item = deployment(configuration, deployment_id)
    if operation == "verify" and len(arguments) == 2:
        return {"operation": "deploy.verify", "deployment": deployment_id, "evidence": verify_deployment(item)}
    if operation == "activate" and len(arguments) == 3:
        candidate = release_path(item, arguments[2])
        before = read_link_inside(item["currentLink"], item["releasesRoot"])
        if before == candidate:
            # current already points at the candidate. Because the previous-release pointer is
            # written BEFORE the current pointer below, reaching this state means the previous
            # pointer was already durably set for this activation, so returning here cannot cement
            # a current swap whose previous-pointer update was lost to a crash.
            return {"operation": "deploy.activate", "deployment": deployment_id, "release": candidate.name, "idempotent": True, "rolledBack": False}
        prior_previous = read_link_inside(item["previousLink"], item["releasesRoot"])
        try:
            # Record the rollback target (the release being superseded) BEFORE swapping the current
            # pointer. If a crash interrupts between these two non-atomic link swaps, a retry still
            # sees current != candidate and re-runs both, so it can never cement a current swap
            # whose previous-pointer update was lost — the failure that otherwise makes a later
            # `rollback` silently restore the wrong (older) release.
            if before is not None:
                replace_link(item["previousLink"], before)
            replace_link(item["currentLink"], candidate)
            restart_deployment_service(item)
            evidence = verify_deployment(item)
            return {
                "operation": "deploy.activate", "deployment": deployment_id, "previous": before.name if before else None,
                "release": candidate.name, "verified": evidence, "rolledBack": False,
            }
        except Exception as error:
            if before is not None:
                replace_link(item["currentLink"], before)
                if prior_previous is not None:
                    replace_link(item["previousLink"], prior_previous)
                else:
                    item["previousLink"].unlink(missing_ok=True)
                restart_deployment_service(item)
                rollback_evidence = verify_deployment(item)
            else:
                item["currentLink"].unlink(missing_ok=True)
                rollback_evidence = {"currentRelease": None}
            raise ManagedError(json.dumps({
                "operation": "deploy.activate", "deployment": deployment_id, "error": str(error),
                "rolledBack": True, "rollback": rollback_evidence,
            }, sort_keys=True)) from error
    if operation == "rollback" and len(arguments) == 2:
        previous = read_link_inside(item["previousLink"], item["releasesRoot"])
        current = read_link_inside(item["currentLink"], item["releasesRoot"])
        if previous is None:
            raise ManagedError("deployment has no previous release")
        replace_link(item["currentLink"], previous)
        if current is not None:
            replace_link(item["previousLink"], current)
        restart_deployment_service(item)
        return {"operation": "deploy.rollback", "deployment": deployment_id, "evidence": verify_deployment(item)}
    raise ManagedError("unknown or malformed deployment action")


def staged_verified_artifact(
    configuration: dict[str, Any], package_id: str, path_value: str, expected_hash: str,
) -> tuple[Path, int]:
    if not SHA256.fullmatch(expected_hash):
        raise ManagedError("artifact hash must be lowercase SHA-256")
    path = Path(path_value)
    if not path.is_absolute():
        raise ManagedError("artifact path must be absolute")
    original = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(original.st_mode):
        raise ManagedError("artifact is not a regular non-symlink file")
    resolved = path.resolve(strict=True)
    roots = [Path(str(item)).resolve() for item in configuration.get("artifactRoots", [])]
    if not any(root != Path("/") and root in resolved.parents for root in roots):
        raise ManagedError("artifact is outside configured roots")
    info = resolved.lstat()
    maximum = min(int(configuration.get("maxPackageBytes", 2 * 1024 * 1024 * 1024)), 4 * 1024 * 1024 * 1024)
    if not stat.S_ISREG(info.st_mode) or resolved.is_symlink() or info.st_size > maximum:
        raise ManagedError("artifact is not a bounded regular non-symlink file")
    verified_root = Path(str(configuration.get("verifiedArtifactRoot", "/var/lib/pixel-ops-managed/verified")))
    if not verified_root.is_absolute() or verified_root == Path("/"):
        raise ManagedError("verifiedArtifactRoot must be an absolute non-root path")
    verified_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    verified_root = verified_root.resolve()
    root_info = verified_root.lstat()
    if not stat.S_ISDIR(root_info.st_mode) or (not TESTING and (root_info.st_uid != 0 or root_info.st_mode & 0o077)):
        raise ManagedError("verifiedArtifactRoot must be a root-owned private directory")
    source_descriptor = os.open(resolved, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    temporary_descriptor, temporary_name = tempfile.mkstemp(prefix=f".{package_id}-", dir=verified_root)
    temporary = Path(temporary_name)
    checksum, total = hashlib.sha256(), 0
    try:
        try:
            opened = os.fstat(source_descriptor)
            if (opened.st_dev, opened.st_ino, opened.st_size) != (info.st_dev, info.st_ino, info.st_size):
                raise ManagedError("artifact changed during secure open")
            while chunk := os.read(source_descriptor, 1024 * 1024):
                total += len(chunk)
                if total > maximum:
                    raise ManagedError("artifact exceeds its configured byte limit")
                checksum.update(chunk); write_all(temporary_descriptor, chunk)
            final = os.fstat(source_descriptor)
            if (
                total != opened.st_size
                or (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns, final.st_ctime_ns)
                != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)
            ):
                raise ManagedError("artifact changed while it was being staged")
            os.fsync(temporary_descriptor)
        finally:
            os.close(source_descriptor); os.close(temporary_descriptor)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    if checksum.hexdigest() != expected_hash:
        temporary.unlink(missing_ok=True)
        raise ManagedError("artifact SHA-256 does not match")
    os.chmod(temporary, 0o400)
    destination = verified_root / f"{package_id}-{expected_hash}-{temporary.name.rsplit('-', 1)[-1]}.artifact"
    try:
        os.link(temporary, destination, follow_symlinks=False)
    finally:
        temporary.unlink(missing_ok=True)
    return destination, total


def package_action(configuration: dict[str, Any], arguments: list[str]) -> dict[str, Any]:
    if len(arguments) != 4:
        raise ManagedError("package action requires operation, package id, artifact path, and SHA-256")
    operation, package_id, path_value, expected_hash = arguments
    package_id = safe_id(package_id, "package id")
    item = configuration.get("packages", {}).get(package_id)
    if not isinstance(item, dict):
        raise ManagedError("package is not configured")
    artifact, size = staged_verified_artifact(configuration, package_id, path_value, expected_hash)
    try:
        substitutions = {"artifact": str(artifact)}
        signature = run(configured_argv(item["signatureCommand"], substitutions), timeout=300) if item.get("signatureCommand") else None
        if operation == "verify":
            return {"operation": "package.verify", "package": package_id, "sha256": expected_hash, "bytes": size, "signature": signature}
        if operation == "install-verify":
            if not item.get("verifyCommand"):
                raise ManagedError("package install verification is not configured")
            verified = run(configured_argv(item["verifyCommand"], substitutions), timeout=300)
            return {"operation": "package.install.verify", "package": package_id, "sha256": expected_hash, "bytes": size, "verified": verified}
        if operation == "rollback-verify":
            if not item.get("rollbackVerifyCommand"):
                raise ManagedError("package rollback verification is not configured")
            verified = run(configured_argv(item["rollbackVerifyCommand"], substitutions), timeout=300)
            return {"operation": "package.rollback.verify", "package": package_id, "sha256": expected_hash, "bytes": size, "verified": verified}
        if operation == "install":
            for required in ("installCommand", "verifyCommand", "rollbackCommand", "rollbackVerifyCommand"):
                if not item.get(required):
                    raise ManagedError(f"package install requires configured {required}")
            try:
                installed = run(configured_argv(item["installCommand"], substitutions), timeout=int(item.get("installTimeoutSeconds", 1800)))
                verified = run(configured_argv(item["verifyCommand"], substitutions), timeout=300)
                return {
                    "operation": "package.install", "package": package_id, "sha256": expected_hash,
                    "bytes": size, "installed": installed, "verified": verified, "rolledBack": False,
                }
            except Exception as error:
                rollback = run(configured_argv(item["rollbackCommand"], substitutions), timeout=600, check=False)
                try:
                    rollback_verified = run(configured_argv(item["rollbackVerifyCommand"], substitutions), timeout=300)
                    rollback_error = None
                except Exception as rollback_failure:
                    rollback_verified = None
                    rollback_error = str(rollback_failure)
                raise ManagedError(json.dumps({
                    "operation": "package.install", "package": package_id, "error": str(error),
                    "rolledBack": rollback_error is None, "rollback": rollback,
                    "rollbackVerified": rollback_verified, "rollbackError": rollback_error,
                }, sort_keys=True)) from error
        if operation == "rollback":
            if not item.get("rollbackCommand") or not item.get("rollbackVerifyCommand"):
                raise ManagedError("package rollback requires configured rollbackCommand and rollbackVerifyCommand")
            rollback = run(configured_argv(item["rollbackCommand"], substitutions), timeout=600)
            verified = run(configured_argv(item["rollbackVerifyCommand"], substitutions), timeout=300)
            return {
                "operation": "package.rollback", "package": package_id, "sha256": expected_hash,
                "bytes": size, "rollback": rollback, "verified": verified,
            }
        raise ManagedError("unknown package action")
    finally:
        artifact.unlink(missing_ok=True)


def main() -> int:
    if getattr(os, "geteuid", lambda: 1)() != 0 and not TESTING:
        raise ManagedError("managed actions must run as root")
    if sys.argv[1:] == ["--validate-config"]:
        validate_configuration(secure_json(CONFIG))
        print(json.dumps({"schemaVersion": 1, "valid": True}))
        return 0
    if len(sys.argv) < 4:
        raise ManagedError("Usage: pixel-ops-managed service|deploy|package OPERATION ID [ARGS]")
    configuration = secure_json(CONFIG)
    family, arguments = sys.argv[1], sys.argv[2:]
    if family == "service":
        result = service_action(configuration, arguments)
    elif family == "deploy":
        result = deployment_action(configuration, arguments)
    elif family == "package":
        result = package_action(configuration, arguments)
    elif family == "host" and arguments == ["reboot", "approved"] and configuration.get("allowReboot") is True:
        result = {"operation": "host.reboot", "requested": True}
        print(json.dumps({"schemaVersion": 1, **result}, sort_keys=True), flush=True)
        systemctl("reboot")
        return 0
    else:
        raise ManagedError("unknown managed action family")
    print(json.dumps({"schemaVersion": 1, **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ManagedError, OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as error:
        print(f"pixel-ops-managed: {error}", file=sys.stderr)
        raise SystemExit(1)
