#!/usr/bin/env python3
"""Least-privilege Pixel Operations runner actions.

All mutable configuration is operator-owned. Model supplied values select only named
repositories, recipes, services, collection sources, and paths below the dedicated
runner job root.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path
from typing import Any


TESTING = os.environ.get("PIXEL_OPS_ACTION_TESTING") == "1" and getattr(os, "geteuid", lambda: 1)() != 0
CONFIG = Path(os.environ.get("PIXEL_OPS_ACTION_CONFIG", "/etc/pixel-ops-runner/actions.json")) if TESTING else Path("/etc/pixel-ops-runner/actions.json")
SAFE_ID = re.compile(r"[a-z][a-z0-9_.-]{0,63}")
SAFE_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}")
SAFE_RELATIVE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,255}")
SAFE_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}")
MAX_CONFIG = 1024 * 1024
MAX_OUTPUT = 256 * 1024


class ActionError(RuntimeError):
    pass


def write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise ActionError("file write made no progress")
        view = view[written:]


def secure_json(path: Path) -> dict[str, Any]:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_CONFIG:
        raise ActionError("action configuration must be a bounded regular file")
    if not TESTING:
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise ActionError("action configuration must be root-owned and not group/world writable")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode) or opened.st_size > MAX_CONFIG
            or (opened.st_dev, opened.st_ino, opened.st_size) != (info.st_dev, info.st_ino, info.st_size)
        ):
            raise ActionError("action configuration changed during secure open")
        chunks, total = [], 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, MAX_CONFIG + 1 - total))
            if not chunk:
                break
            chunks.append(chunk); total += len(chunk)
            if total > MAX_CONFIG:
                raise ActionError("action configuration exceeds its size limit")
        payload = b"".join(chunks)
    finally:
        os.close(descriptor)
    if len(payload) > MAX_CONFIG:
        raise ActionError("action configuration exceeds its size limit")
    value = json.loads(payload)
    if not isinstance(value, dict) or value.get("schemaVersion") != 1:
        raise ActionError("action configuration must use schemaVersion 1")
    return value


def safe_id(value: str, label: str) -> str:
    if not SAFE_ID.fullmatch(value):
        raise ActionError(f"unsafe {label}")
    return value


def bounded_integer(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ActionError(f"{label} must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as error:
        raise ActionError(f"{label} must be an integer") from error
    if isinstance(value, float) and not value.is_integer():
        raise ActionError(f"{label} must be an integer")
    if not minimum <= number <= maximum:
        raise ActionError(f"{label} is outside its safe range")
    return number


def absolute_root(value: Any, label: str) -> Path:
    root = Path(str(value))
    if not root.is_absolute() or root == Path("/"):
        raise ActionError(f"{label} must be an absolute non-root path")
    return root.resolve()


def under(root: Path, relative: str, *, must_exist: bool = True) -> Path:
    if not SAFE_RELATIVE.fullmatch(relative) or relative.startswith("/") or ".." in Path(relative).parts:
        raise ActionError("unsafe relative path")
    candidate = (root / relative).resolve(strict=must_exist)
    if candidate != root and root not in candidate.parents:
        raise ActionError("path escaped its configured root")
    return candidate


def bounded_run(argv: list[str], *, cwd: Path | None = None, timeout: int = 3600) -> dict[str, Any]:
    if not argv or any(not isinstance(item, str) or "\x00" in item for item in argv):
        raise ActionError("configured argv is invalid")
    deadline = time.monotonic() + min(max(timeout, 1), 3600)
    process = subprocess.Popen(
        argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
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
        raise ActionError(f"configured command {Path(argv[0]).name} exceeded its hard timeout")
    stdout = captured["stdout"].decode("utf-8", "replace")
    stderr = captured["stderr"].decode("utf-8", "replace")
    result = {
        "argv0": Path(argv[0]).name,
        "exitCode": process.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "outputTruncated": truncated["stdout"] or truncated["stderr"],
    }
    if process.returncode != 0:
        raise ActionError(f"configured command {Path(argv[0]).name} exited with {process.returncode}: {stderr[:1000]}")
    return result


def host_action(configuration: dict[str, Any], arguments: list[str]) -> dict[str, Any]:
    operation = arguments[0] if arguments else ""
    if operation == "summary" and len(arguments) == 1:
        usage = shutil.disk_usage(absolute_root(configuration.get("jobRoot", "/var/lib/pixel-runner/jobs"), "jobRoot"))
        load = os.getloadavg() if hasattr(os, "getloadavg") else (0.0, 0.0, 0.0)
        return {
            "operation": "host.summary", "hostname": os.uname().nodename, "kernel": os.uname().release,
            "architecture": os.uname().machine, "cpuCount": os.cpu_count(), "loadAverage": load,
            "jobDisk": {"total": usage.total, "used": usage.used, "free": usage.free},
        }
    if operation == "processes" and len(arguments) == 1:
        return {"operation": "host.processes", "evidence": bounded_run([
            "/bin/ps", "-eo", "pid=,ppid=,stat=,etimes=,comm=", "--sort=-etimes",
        ], timeout=30)}
    if operation == "gpu" and len(arguments) == 1:
        binary = Path("/usr/bin/nvidia-smi")
        if not binary.exists():
            return {"operation": "host.gpu", "available": False}
        evidence = bounded_run([
            str(binary), "--query-gpu=index,name,uuid,driver_version,memory.total,memory.used,temperature.gpu,power.draw",
            "--format=csv,noheader,nounits",
        ], timeout=30)
        return {"operation": "host.gpu", "available": True, "evidence": evidence}
    if operation == "service-status" and len(arguments) == 2:
        service_id = safe_id(arguments[1], "service id")
        service = configuration.get("services", {}).get(service_id)
        if not isinstance(service, dict) or not SAFE_ID.fullmatch(str(service.get("unit", "")).removesuffix(".service")):
            raise ActionError("service is not configured")
        unit = str(service["unit"])
        evidence = bounded_run([
            "/usr/bin/systemctl", "show", unit, "--no-pager",
            "--property=Id,LoadState,ActiveState,SubState,UnitFileState,MainPID,ExecMainStatus,ActiveEnterTimestamp",
        ], timeout=30)
        return {"operation": "host.service-status", "service": service_id, "evidence": evidence}
    raise ActionError("unknown or malformed host action")


def repository(configuration: dict[str, Any], repository_id: str) -> tuple[dict[str, Any], Path]:
    repository_id = safe_id(repository_id, "repository id")
    item = configuration.get("repositories", {}).get(repository_id)
    if not isinstance(item, dict):
        raise ActionError("repository is not configured")
    path = absolute_root(item.get("path"), "repository path")
    if not (path / ".git").exists():
        raise ActionError("configured repository is not a Git checkout")
    return item, path


def validate_configuration(configuration: dict[str, Any]) -> None:
    absolute_root(configuration.get("jobRoot", "/var/lib/pixel-runner/jobs"), "jobRoot")
    bounded_integer(configuration.get("maxArtifactBytes", 1024 * 1024 * 1024), "maxArtifactBytes", 1, 2 * 1024 * 1024 * 1024)
    services = configuration.get("services", {})
    repositories = configuration.get("repositories", {})
    sources = configuration.get("collectSources", {})
    if not all(isinstance(value, dict) for value in (services, repositories, sources)):
        raise ActionError("services, repositories, and collectSources must be objects")
    for service_id, item in services.items():
        safe_id(str(service_id), "service id")
        if not isinstance(item, dict) or not SAFE_ID.fullmatch(str(item.get("unit", "")).removesuffix(".service")):
            raise ActionError("configured service is invalid")
    for repository_id, item in repositories.items():
        repository({"repositories": {str(repository_id): item}}, str(repository_id))
        patterns = item.get("allowedRefs", []) if isinstance(item, dict) else None
        recipes = item.get("recipes", {}) if isinstance(item, dict) else None
        if not isinstance(patterns, list) or any(not isinstance(pattern, str) or not pattern or len(pattern) > 200 for pattern in patterns):
            raise ActionError("repository allowedRefs must be bounded strings")
        if not isinstance(recipes, dict):
            raise ActionError("repository recipes must be an object")
        for recipe_id, recipe in recipes.items():
            safe_id(str(recipe_id), "recipe id")
            if not isinstance(recipe, dict):
                raise ActionError("repository recipe must be an object")
            argv = recipe.get("argv")
            if (
                not isinstance(argv, list) or not 1 <= len(argv) <= 128
                or any(not isinstance(value, str) or "\x00" in value or len(value) > 16_384 for value in argv)
                or not Path(argv[0]).is_absolute()
            ):
                raise ActionError("repository recipe must use a fixed argv with an absolute executable")
            bounded_integer(recipe.get("timeoutSeconds", 1800), "repository recipe timeout", 1, 3600)
    for source_id, source_value in sources.items():
        safe_id(str(source_id), "collection source id")
        source = Path(str(source_value))
        if not source.is_absolute() or source == Path("/"):
            raise ActionError("collection source must be an absolute non-root path")
        info = source.lstat()
        if source.is_symlink() or not stat.S_ISREG(info.st_mode):
            raise ActionError("collection source must be a regular non-symlink file")


def repo_action(configuration: dict[str, Any], arguments: list[str]) -> dict[str, Any]:
    if len(arguments) < 2:
        raise ActionError("repository action requires an operation and repository id")
    operation, repository_id = arguments[0], arguments[1]
    item, path = repository(configuration, repository_id)
    if operation == "status" and len(arguments) == 2:
        evidence = bounded_run(["/usr/bin/git", "status", "--short", "--branch"], cwd=path, timeout=30)
    elif operation == "fetch" and len(arguments) == 2:
        evidence = bounded_run(["/usr/bin/git", "fetch", "--prune", "--tags", "origin"], cwd=path, timeout=600)
    elif operation == "checkout" and len(arguments) == 3:
        ref = arguments[2]
        patterns = item.get("allowedRefs", [])
        if not SAFE_REF.fullmatch(ref) or ref.startswith("-") or not isinstance(patterns, list) or not any(fnmatch.fnmatchcase(ref, str(pattern)) for pattern in patterns):
            raise ActionError("repository ref is outside the configured allowlist")
        bounded_run(["/usr/bin/git", "check-ref-format", "--allow-onelevel", ref], cwd=path, timeout=30)
        evidence = bounded_run(["/usr/bin/git", "checkout", "--detach", ref], cwd=path, timeout=120)
    elif operation == "recipe" and len(arguments) == 3:
        recipe_id = safe_id(arguments[2], "recipe id")
        recipe = item.get("recipes", {}).get(recipe_id)
        if not isinstance(recipe, dict) or not isinstance(recipe.get("argv"), list):
            raise ActionError("repository recipe is not configured")
        evidence = bounded_run(recipe["argv"], cwd=path, timeout=int(recipe.get("timeoutSeconds", 1800)))
    else:
        raise ActionError("unknown or malformed repository action")
    return {"operation": f"repo.{operation}", "repository": repository_id, "evidence": evidence}


def checksum_file(path: Path, maximum: int) -> tuple[str, int]:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_size > maximum:
        raise ActionError("artifact must be a bounded regular non-symlink file")
    checksum = hashlib.sha256()
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino) or opened.st_size > maximum:
            raise ActionError("artifact changed during secure open")
        total = 0
        while chunk := os.read(descriptor, min(1024 * 1024, maximum + 1 - total)):
            total += len(chunk)
            if total > maximum:
                raise ActionError("artifact grew beyond its byte limit while hashing")
            checksum.update(chunk)
        final = os.fstat(descriptor)
        if (
            total != opened.st_size
            or (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns, final.st_ctime_ns)
            != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)
        ):
            raise ActionError("artifact changed while it was being hashed")
    finally:
        os.close(descriptor)
    return checksum.hexdigest(), total


def secure_child_directory(root: Path, name: str) -> tuple[int, int]:
    root_descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
    try:
        try:
            os.mkdir(name, 0o700, dir_fd=root_descriptor)
        except FileExistsError:
            pass
        child_descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0), dir_fd=root_descriptor)
    except Exception:
        os.close(root_descriptor)
        raise
    return root_descriptor, child_descriptor


def secure_archive(source: Path, destination_descriptor: int, destination_name: str, maximum: int) -> tuple[int, int, str, int]:
    """Create a tar.gz without following a path after it has been checked."""
    entries, total = 0, 0
    source_descriptor = os.open(source, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
    temporary_name = f".pixel-archive-{secrets.token_hex(12)}"
    temporary_descriptor = os.open(
        temporary_name, os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600,
        dir_fd=destination_descriptor,
    )

    def add_directory(archive: tarfile.TarFile, descriptor: int, archive_path: str) -> None:
        nonlocal entries, total
        directory_info = os.fstat(descriptor)
        header = tarfile.TarInfo(archive_path)
        header.type = tarfile.DIRTYPE
        header.mode = directory_info.st_mode & 0o777
        header.mtime = int(directory_info.st_mtime)
        header.uid = header.gid = 0
        header.uname = header.gname = ""
        archive.addfile(header)
        for name in sorted(os.listdir(descriptor)):
            info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            entries += 1
            if entries > 10_000:
                raise ActionError("archive source exceeds its entry limit")
            child_path = f"{archive_path}/{name}"
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0), dir_fd=descriptor)
                try:
                    add_directory(archive, child, child_path)
                finally:
                    os.close(child)
            elif stat.S_ISREG(info.st_mode):
                total += info.st_size
                if total > maximum:
                    raise ActionError("archive source exceeds its byte limit")
                child = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=descriptor)
                try:
                    opened = os.fstat(child)
                    if (opened.st_dev, opened.st_ino, opened.st_size) != (info.st_dev, info.st_ino, info.st_size):
                        raise ActionError("archive source changed during secure open")
                    header = tarfile.TarInfo(child_path)
                    header.size = opened.st_size
                    header.mode = opened.st_mode & 0o777
                    header.mtime = int(opened.st_mtime)
                    header.uid = header.gid = 0
                    header.uname = header.gname = ""
                    with os.fdopen(os.dup(child), "rb") as handle:
                        archive.addfile(header, handle)
                    final = os.fstat(child)
                    if (
                        (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns, final.st_ctime_ns)
                        != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)
                    ):
                        raise ActionError("archive source changed while it was being read")
                finally:
                    os.close(child)
            else:
                raise ActionError("archive source contains a symlink or special file")

    try:
        with os.fdopen(os.dup(temporary_descriptor), "w+b") as handle:
            with tarfile.open(fileobj=handle, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
                add_directory(archive, source_descriptor, source.name)
            handle.flush(); os.fsync(handle.fileno())
        os.link(
            temporary_name, destination_name, src_dir_fd=destination_descriptor,
            dst_dir_fd=destination_descriptor, follow_symlinks=False,
        )
        checksum = hashlib.sha256()
        os.lseek(temporary_descriptor, 0, os.SEEK_SET)
        while chunk := os.read(temporary_descriptor, 1024 * 1024):
            checksum.update(chunk)
        archive_size = os.fstat(temporary_descriptor).st_size
    finally:
        os.close(temporary_descriptor)
        os.close(source_descriptor)
        try:
            os.unlink(temporary_name, dir_fd=destination_descriptor)
        except FileNotFoundError:
            pass
    return entries, total, checksum.hexdigest(), archive_size


def artifact_action(configuration: dict[str, Any], arguments: list[str]) -> dict[str, Any]:
    if not arguments:
        raise ActionError("artifact action requires an operation")
    operation = arguments[0]
    root = absolute_root(configuration.get("jobRoot", "/var/lib/pixel-runner/jobs"), "jobRoot")
    maximum = bounded_integer(configuration.get("maxArtifactBytes", 1024 * 1024 * 1024), "maxArtifactBytes", 1, 2 * 1024 * 1024 * 1024)
    if operation == "checksum" and len(arguments) == 2:
        path = under(root, arguments[1])
        checksum, size = checksum_file(path, maximum)
        return {"operation": "artifact.checksum", "path": arguments[1], "sha256": checksum, "bytes": size}
    if operation == "compare" and len(arguments) == 3:
        left, right = under(root, arguments[1]), under(root, arguments[2])
        left_hash, left_size = checksum_file(left, maximum)
        right_hash, right_size = checksum_file(right, maximum)
        return {
            "operation": "artifact.compare", "left": {"sha256": left_hash, "bytes": left_size},
            "right": {"sha256": right_hash, "bytes": right_size}, "equal": left_hash == right_hash and left_size == right_size,
        }
    if operation == "collect" and len(arguments) == 3:
        source_id, destination_name = safe_id(arguments[1], "collection source id"), arguments[2]
        if not SAFE_FILENAME.fullmatch(destination_name):
            raise ActionError("collection destination must be one safe filename")
        source_value = configuration.get("collectSources", {}).get(source_id)
        if not isinstance(source_value, str):
            raise ActionError("collection source is not configured")
        source = Path(source_value)
        source_info = source.lstat()
        if source.is_symlink() or not stat.S_ISREG(source_info.st_mode) or source_info.st_size > maximum:
            raise ActionError("collection source is not a bounded regular non-symlink file")
        source_descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        root_descriptor, collection_descriptor = secure_child_directory(root, "collections")
        destination_descriptor = os.open(
            destination_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600,
            dir_fd=collection_descriptor,
        )
        checksum, size = hashlib.sha256(), 0
        try:
            opened = os.fstat(source_descriptor)
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (source_info.st_dev, source_info.st_ino):
                raise ActionError("collection source changed during secure open")
            while chunk := os.read(source_descriptor, 1024 * 1024):
                size += len(chunk)
                if size > maximum:
                    raise ActionError("collection source exceeds its byte limit")
                checksum.update(chunk); write_all(destination_descriptor, chunk)
            os.fsync(destination_descriptor)
        except Exception:
            try: os.unlink(destination_name, dir_fd=collection_descriptor)
            except FileNotFoundError: pass
            raise
        finally:
            os.close(destination_descriptor); os.close(source_descriptor)
            os.close(collection_descriptor); os.close(root_descriptor)
        return {"operation": "artifact.collect", "source": source_id, "path": f"collections/{destination_name}", "sha256": checksum.hexdigest(), "bytes": size}
    if operation == "archive" and len(arguments) == 3:
        source = under(root, arguments[1])
        destination_name = arguments[2]
        if not SAFE_FILENAME.fullmatch(destination_name) or source == root:
            raise ActionError("unsafe archive source or destination")
        root_descriptor, archive_descriptor = secure_child_directory(root, "archives")
        try:
            entries, _total, checksum, size = secure_archive(source, archive_descriptor, destination_name, maximum)
        finally:
            os.close(archive_descriptor); os.close(root_descriptor)
        return {"operation": "artifact.archive", "path": f"archives/{destination_name}", "sha256": checksum, "bytes": size, "entries": entries}
    if operation == "cleanup" and len(arguments) == 2:
        target = under(root, arguments[1])
        if target == root or target.is_symlink() or not target.is_dir():
            raise ActionError("cleanup target must be a real directory below jobRoot")
        if not getattr(shutil.rmtree, "avoids_symlink_attacks", False):
            raise ActionError("this platform cannot safely remove runner directories")
        shutil.rmtree(target)
        return {"operation": "artifact.cleanup", "path": arguments[1], "removed": True}
    raise ActionError("unknown or malformed artifact action")


def main() -> int:
    if sys.argv[1:] == ["--validate-config"]:
        validate_configuration(secure_json(CONFIG))
        print(json.dumps({"schemaVersion": 1, "valid": True}))
        return 0
    if len(sys.argv) < 3:
        raise ActionError("Usage: pixel-ops-action host|repo|artifact OPERATION [ARGS]")
    configuration = secure_json(CONFIG)
    family, arguments = sys.argv[1], sys.argv[2:]
    if family == "host":
        result = host_action(configuration, arguments)
    elif family == "repo":
        result = repo_action(configuration, arguments)
    elif family == "artifact":
        result = artifact_action(configuration, arguments)
    else:
        raise ActionError("unknown action family")
    print(json.dumps({"schemaVersion": 1, **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ActionError, OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as error:
        print(f"pixel-ops-action: {error}", file=sys.stderr)
        raise SystemExit(1)
