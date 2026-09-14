#!/usr/bin/env python3
"""Promote one verified Pixel Operations download into the owner workspace.

The service is deliberately narrower than a file-copy API.  Its source is
derived from one successful broker result, its destination is one validated
relative path beneath the configured Pixel workspace, and publication is
create-only.  Remote bytes remain untrusted and non-executable.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import pwd
import re
import secrets
import select
import socket
import stat
import struct
import sys
import urllib.parse
from typing import Any, Callable


SCHEMA_VERSION = 1
KIND = "ods-pixel-download-promotion"
JOB_ID = re.compile(r"^ops-[0-9]{13}-[a-f0-9]{12}$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")
FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
PATH_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MAX_REQUEST_BYTES = 4096
MAX_RESPONSE_BYTES = 8192
MAX_RESULT_BYTES = 1024 * 1024
MAX_ARTIFACT_BYTES = 512 * 1024 * 1024
RESULTS_ROOT = pathlib.Path("/var/lib/pixel-ops-broker/results")
ARTIFACTS_ROOT = pathlib.Path("/var/lib/pixel-ops-broker/artifacts")
SOCKET_PATH = pathlib.Path("/run/ods-pixel-artifact-promoter/promoter.sock")
BOUNDARY = (
    "Verified create-only promotion from Pixel Operations quarantine into the "
    "configured owner workspace; no arbitrary source, overwrite, execution, or "
    "path traversal authority."
)


class PromotionError(RuntimeError):
    """A promotion failure whose details must not cross the service boundary."""


def _secure_fd_for_owner(
    descriptor: int, mode: int, owner_uid: int, owner_gid: int
) -> None:
    # CAP_FOWNER is deliberately absent. Seal the mode while the service still
    # owns the inode, then transfer ownership as the final custody operation.
    os.fchmod(descriptor, mode)
    os.fchown(descriptor, owner_uid, owner_gid)


def _secure_path_for_owner(
    path: pathlib.Path, mode: int, owner_uid: int, owner_gid: int
) -> None:
    os.chmod(path, mode)
    os.chown(path, owner_uid, owner_gid)


def _exact_object(value: Any, required: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != required:
        raise PromotionError("invalid promotion request")
    return value


def _relative_parts(value: object, filename: str) -> tuple[str, ...]:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 512
        or value.startswith("/")
        or "\\" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise PromotionError("invalid workspace destination")
    parts = tuple(value.split("/"))
    if (
        not 1 <= len(parts) <= 16
        or parts[-1] != filename
        or any(
            part in {"", ".", ".."} or PATH_COMPONENT.fullmatch(part) is None
            for part in parts
        )
    ):
        raise PromotionError("invalid workspace destination")
    return parts


def parse_request(payload: bytes) -> dict[str, Any]:
    if not payload or len(payload) > MAX_REQUEST_BYTES or b"\0" in payload:
        raise PromotionError("invalid promotion request")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PromotionError("invalid promotion request") from exc
    value = _exact_object(
        value,
        {
            "schemaVersion",
            "action",
            "jobId",
            "filename",
            "relativePath",
            "sha256",
            "sourceUrl",
        },
    )
    job_id = value.get("jobId")
    filename = value.get("filename")
    digest = value.get("sha256")
    if (
        value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("action") != "promote"
        or not isinstance(job_id, str)
        or JOB_ID.fullmatch(job_id) is None
        or not isinstance(filename, str)
        or FILENAME.fullmatch(filename) is None
        or not isinstance(digest, str)
        or SHA256.fullmatch(digest) is None
    ):
        raise PromotionError("invalid promotion request")
    return {
        "jobId": job_id,
        "filename": filename,
        "relativePath": "/".join(_relative_parts(value.get("relativePath"), filename)),
        "sha256": digest,
        "sourceUrl": _https_url(value.get("sourceUrl")),
    }


def is_health_request(payload: bytes) -> bool:
    if not payload or len(payload) > MAX_REQUEST_BYTES or b"\0" in payload:
        return False
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return value == {"schemaVersion": SCHEMA_VERSION, "action": "health"}


def _directory_fd(path: pathlib.Path, expected_uid: int) -> int:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(path, flags)
    info = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != expected_uid
        or info.st_mode & 0o022
    ):
        os.close(descriptor)
        raise PromotionError("unsafe promotion directory")
    return descriptor


def _read_result(results_root: pathlib.Path, broker_uid: int, job_id: str) -> dict[str, Any]:
    directory = _directory_fd(results_root, broker_uid)
    descriptor = -1
    try:
        descriptor = os.open(
            f"{job_id}.json",
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory,
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != broker_uid
            or before.st_mode & 0o022
            or not 1 <= before.st_size <= MAX_RESULT_BYTES
        ):
            raise PromotionError("unsafe broker result")
        payload = bytearray()
        while len(payload) < before.st_size:
            piece = os.read(descriptor, before.st_size - len(payload))
            if not piece:
                raise PromotionError("incomplete broker result")
            payload.extend(piece)
        if os.read(descriptor, 1):
            raise PromotionError("oversized broker result")
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise PromotionError("unstable broker result")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(directory)
    try:
        value = json.loads(bytes(payload).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PromotionError("invalid broker result") from exc
    if not isinstance(value, dict):
        raise PromotionError("invalid broker result")
    return value


def _https_url(value: object) -> str:
    if (
        not isinstance(value, str)
        or not 8 <= len(value) <= 4096
        or any(ord(character) <= 32 or ord(character) == 127 for character in value)
    ):
        raise PromotionError("invalid artifact source")
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise PromotionError("invalid artifact source")
    return value


def _verified_artifact(
    result: dict[str, Any],
    *,
    job_id: str,
    filename: str,
    expected_sha256: str,
    expected_source_url: str,
    artifacts_root: pathlib.Path,
) -> dict[str, Any]:
    steps = result.get("steps")
    if (
        result.get("schemaVersion") != 2
        or result.get("jobId") != job_id
        or result.get("status") != "succeeded"
        or not isinstance(steps, list)
        or len(steps) != 1
    ):
        raise PromotionError("download job did not succeed")
    step = steps[0]
    artifact = step.get("artifact") if isinstance(step, dict) else None
    expected_path = artifacts_root / job_id / filename
    if (
        not isinstance(step, dict)
        or step.get("target") != "broker"
        or step.get("action") != "download.stage"
        or step.get("exitCode") != 0
        or not isinstance(artifact, dict)
        or artifact.get("path") != str(expected_path)
        or artifact.get("filename") != filename
        or artifact.get("sha256") != expected_sha256
        or not isinstance(artifact.get("bytes"), int)
        or not 0 <= artifact["bytes"] <= MAX_ARTIFACT_BYTES
        or artifact.get("executable") is not False
    ):
        raise PromotionError("invalid staged artifact evidence")
    source = _https_url(artifact.get("source"))
    redirects = artifact.get("redirects")
    if not isinstance(redirects, list) or len(redirects) > 6:
        raise PromotionError("invalid staged artifact redirects")
    for redirect in redirects:
        _https_url(redirect)
    safe_requested_source = expected_source_url.split("?", 1)[0]
    if (
        (not redirects and source != safe_requested_source)
        or (redirects and redirects[0] != safe_requested_source)
    ):
        raise PromotionError("staged artifact source does not match the owner request")
    return {
        "path": expected_path,
        "bytes": artifact["bytes"],
        "sha256": expected_sha256,
        "source": source,
    }


def _open_artifact(
    artifacts_root: pathlib.Path,
    broker_uid: int,
    job_id: str,
    filename: str,
    expected_bytes: int,
) -> tuple[int, os.stat_result]:
    root = _directory_fd(artifacts_root, broker_uid)
    job = -1
    descriptor = -1
    try:
        job = os.open(
            job_id,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=root,
        )
        job_info = os.fstat(job)
        if (
            not stat.S_ISDIR(job_info.st_mode)
            or job_info.st_uid != broker_uid
            or job_info.st_mode & 0o022
        ):
            raise PromotionError("unsafe artifact job directory")
        descriptor = os.open(
            filename,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=job,
        )
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != broker_uid
            or info.st_mode & 0o022
            or info.st_size != expected_bytes
            or info.st_size > MAX_ARTIFACT_BYTES
        ):
            raise PromotionError("unsafe staged artifact")
        return descriptor, info
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    finally:
        if job >= 0:
            os.close(job)
        os.close(root)


def _workspace_parent(
    workspace: pathlib.Path,
    owner_uid: int,
    owner_gid: int,
    parent_parts: tuple[str, ...],
) -> int:
    current = _directory_fd(workspace, owner_uid)
    for component in parent_parts:
        child = -1
        try:
            try:
                child = os.open(
                    component,
                    os.O_RDONLY
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=current,
                )
            except FileNotFoundError:
                os.mkdir(component, 0o700, dir_fd=current)
                child = os.open(
                    component,
                    os.O_RDONLY
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=current,
                )
                _secure_fd_for_owner(child, 0o700, owner_uid, owner_gid)
                os.fsync(current)
            info = os.fstat(child)
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != owner_uid
                or info.st_mode & 0o022
            ):
                raise PromotionError("unsafe workspace directory")
        except Exception:
            if child >= 0:
                os.close(child)
            os.close(current)
            raise
        os.close(current)
        current = child
    return current


def _copy_create_only(
    source: int,
    source_info: os.stat_result,
    parent: int,
    filename: str,
    owner_uid: int,
    owner_gid: int,
    expected_sha256: str,
    cancel_requested: Callable[[], bool] | None = None,
) -> tuple[int, str]:
    temporary = f".pixel-download-{secrets.token_hex(12)}"
    target = -1
    linked = False
    published = False
    temporary_exists = False
    try:
        target = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent,
        )
        temporary_exists = True
        digest = hashlib.sha256()
        total = 0
        os.lseek(source, 0, os.SEEK_SET)
        while True:
            if cancel_requested is not None and cancel_requested():
                raise PromotionError("promotion requester disconnected")
            chunk = os.read(source, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_ARTIFACT_BYTES:
                raise PromotionError("artifact exceeds promotion limit")
            digest.update(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(target, view)
                if written <= 0:
                    raise PromotionError("incomplete workspace write")
                view = view[written:]
        observed = digest.hexdigest()
        after = os.fstat(source)
        if (
            total != source_info.st_size
            or observed != expected_sha256
            or (source_info.st_dev, source_info.st_ino, source_info.st_size, source_info.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        ):
            raise PromotionError("staged artifact changed during promotion")
        os.fchmod(target, 0o600)
        os.fsync(target)
        if cancel_requested is not None and cancel_requested():
            raise PromotionError("promotion requester disconnected")
        os.link(
            temporary,
            filename,
            src_dir_fd=parent,
            dst_dir_fd=parent,
            follow_symlinks=False,
        )
        linked = True
        _secure_fd_for_owner(target, 0o600, owner_uid, owner_gid)
        os.fsync(target)
        if cancel_requested is not None and cancel_requested():
            raise PromotionError("promotion requester disconnected")
        os.unlink(temporary, dir_fd=parent)
        temporary_exists = False
        os.fsync(parent)
        published = True
        return total, observed
    except FileExistsError as exc:
        raise PromotionError("workspace destination already exists") from exc
    finally:
        if linked and not published:
            try:
                linked_info = os.stat(filename, dir_fd=parent, follow_symlinks=False)
                target_info = os.fstat(target)
                if (linked_info.st_dev, linked_info.st_ino) == (
                    target_info.st_dev,
                    target_info.st_ino,
                ):
                    os.unlink(filename, dir_fd=parent)
                    os.fsync(parent)
            except (FileNotFoundError, OSError):
                pass
        if target >= 0:
            os.close(target)
        if temporary_exists:
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass


def promote(
    request: dict[str, Any],
    *,
    results_root: pathlib.Path,
    artifacts_root: pathlib.Path,
    workspace: pathlib.Path,
    broker_uid: int,
    owner_uid: int,
    owner_gid: int,
    cancel_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    job_id = request["jobId"]
    filename = request["filename"]
    relative_path = request["relativePath"]
    expected_sha256 = request["sha256"]
    expected_source_url = request["sourceUrl"]
    parts = _relative_parts(relative_path, filename)
    result = _read_result(results_root, broker_uid, job_id)
    artifact = _verified_artifact(
        result,
        job_id=job_id,
        filename=filename,
        expected_sha256=expected_sha256,
        expected_source_url=expected_source_url,
        artifacts_root=artifacts_root,
    )
    source, source_info = _open_artifact(
        artifacts_root, broker_uid, job_id, filename, artifact["bytes"]
    )
    parent = -1
    try:
        parent = _workspace_parent(workspace, owner_uid, owner_gid, parts[:-1])
        total, observed = _copy_create_only(
            source,
            source_info,
            parent,
            filename,
            owner_uid,
            owner_gid,
            expected_sha256,
            cancel_requested,
        )
    finally:
        if parent >= 0:
            os.close(parent)
        os.close(source)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": KIND,
        "status": "succeeded",
        "jobId": job_id,
        "filename": filename,
        "relativePath": relative_path,
        "bytes": total,
        "sha256": observed,
        "source": artifact["source"],
        "requestedSource": expected_source_url,
        "executable": False,
        "overwritten": False,
        "boundary": BOUNDARY,
    }


def _error_result() -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": KIND,
        "status": "failed",
        "error": "ODS exact-download promotion failed",
        "boundary": BOUNDARY,
    }


def _connection_closed(connection: socket.socket) -> bool:
    try:
        readable, _writable, _exceptional = select.select([connection], [], [], 0)
        if not readable:
            return False
        return connection.recv(1, socket.MSG_PEEK) == b""
    except (BlockingIOError, InterruptedError):
        return False
    except OSError:
        return True


def _serve_connection(
    connection: socket.socket,
    *,
    owner_uid: int,
    owner_gid: int,
    broker_uid: int,
    results_root: pathlib.Path,
    artifacts_root: pathlib.Path,
    workspace: pathlib.Path,
) -> None:
    def requester_disconnected() -> bool:
        return _connection_closed(connection)

    response: dict[str, Any]
    try:
        peer = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        _pid, uid, _gid = struct.unpack("3i", peer)
        if uid != owner_uid:
            raise PromotionError("unauthorized promotion peer")
        connection.settimeout(10)
        payload = bytearray()
        while len(payload) <= MAX_REQUEST_BYTES:
            piece = connection.recv(min(1024, MAX_REQUEST_BYTES + 1 - len(payload)))
            if not piece:
                break
            payload.extend(piece)
            if b"\n" in piece:
                break
        if b"\n" not in payload or payload.count(b"\n") != 1 or not payload.endswith(b"\n"):
            raise PromotionError("invalid promotion framing")
        request_payload = bytes(payload[:-1])
        if is_health_request(request_payload):
            response = {
                "schemaVersion": SCHEMA_VERSION,
                "kind": KIND,
                "status": "ok",
                "boundary": BOUNDARY,
            }
        else:
            request = parse_request(request_payload)
            response = promote(
                request,
                results_root=results_root,
                artifacts_root=artifacts_root,
                workspace=workspace,
                broker_uid=broker_uid,
                owner_uid=owner_uid,
                owner_gid=owner_gid,
                cancel_requested=requester_disconnected,
            )
    except (PromotionError, OSError, ValueError, TypeError):
        response = _error_result()
    encoded = (json.dumps(response, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(encoded) > MAX_RESPONSE_BYTES:
        encoded = (json.dumps(_error_result(), sort_keys=True, separators=(",", ":")) + "\n").encode()
    try:
        connection.sendall(encoded)
    except OSError:
        # A disconnected owner must not terminate the long-running service.
        return


def serve(
    socket_path: pathlib.Path,
    results_root: pathlib.Path,
    artifacts_root: pathlib.Path,
    workspace: pathlib.Path,
    owner: str,
) -> int:
    if (
        socket_path != SOCKET_PATH
        or results_root != RESULTS_ROOT
        or artifacts_root != ARTIFACTS_ROOT
        or not workspace.is_absolute()
        or workspace == pathlib.Path("/")
        or len(str(workspace)) > 1024
        or re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", owner) is None
    ):
        raise PromotionError("invalid promotion service configuration")
    owner_entry = pwd.getpwnam(owner)
    broker_uid = pwd.getpwnam("pixel-ops-broker").pw_uid
    parent = socket_path.parent
    parent_info = parent.lstat()
    if (
        not stat.S_ISDIR(parent_info.st_mode)
        or stat.S_ISLNK(parent_info.st_mode)
        or parent_info.st_uid != 0
        or parent_info.st_mode & 0o022
    ):
        raise PromotionError("unsafe promotion socket directory")
    # systemd creates the runtime directory as root. Give only the owner's
    # primary group traversal; the socket itself remains mode 0600 and peer
    # credentials still require the exact owner UID.
    _secure_path_for_owner(parent, 0o710, 0, owner_entry.pw_gid)
    if socket_path.exists() or socket_path.is_symlink():
        info = socket_path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != owner_entry.pw_uid:
            raise PromotionError("unsafe existing promotion socket")
        socket_path.unlink()
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind(str(socket_path))
        _secure_path_for_owner(
            socket_path, 0o600, owner_entry.pw_uid, owner_entry.pw_gid
        )
        listener.listen(4)
        while True:
            connection, _address = listener.accept()
            with connection:
                _serve_connection(
                    connection,
                    owner_uid=owner_entry.pw_uid,
                    owner_gid=owner_entry.pw_gid,
                    broker_uid=broker_uid,
                    results_root=results_root,
                    artifacts_root=artifacts_root,
                    workspace=workspace,
                )
    finally:
        listener.close()
        try:
            socket_path.unlink()
        except FileNotFoundError:
            pass


def client(socket_path: pathlib.Path, payload: dict[str, Any]) -> dict[str, Any]:
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(15)
    try:
        connection.connect(str(socket_path))
        connection.sendall((json.dumps(payload, separators=(",", ":")) + "\n").encode())
        chunks = bytearray()
        while len(chunks) <= MAX_RESPONSE_BYTES:
            piece = connection.recv(min(4096, MAX_RESPONSE_BYTES + 1 - len(chunks)))
            if not piece:
                break
            chunks.extend(piece)
    finally:
        connection.close()
    if len(chunks) > MAX_RESPONSE_BYTES or chunks.count(b"\n") != 1 or not chunks.endswith(b"\n"):
        raise PromotionError("invalid promotion response")
    try:
        value = json.loads(bytes(chunks[:-1]).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PromotionError("invalid promotion response") from exc
    if not isinstance(value, dict) or value.get("schemaVersion") != SCHEMA_VERSION:
        raise PromotionError("invalid promotion response")
    return value


def main(argv: list[str]) -> int:
    try:
        if len(argv) == 7 and argv[1] == "serve":
            return serve(
                pathlib.Path(argv[2]),
                pathlib.Path(argv[3]),
                pathlib.Path(argv[4]),
                pathlib.Path(argv[5]),
                argv[6],
            )
        if len(argv) == 3 and argv[1] == "health":
            value = client(pathlib.Path(argv[2]), {"schemaVersion": SCHEMA_VERSION, "action": "health"})
            if value != {
                "schemaVersion": SCHEMA_VERSION,
                "kind": KIND,
                "status": "ok",
                "boundary": BOUNDARY,
            }:
                raise PromotionError("invalid promotion health response")
            sys.stdout.write(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
            return 0
        raise PromotionError("invalid promotion command")
    except (PromotionError, OSError, ValueError, KeyError):
        sys.stderr.write("ODS exact-download promotion failed\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
