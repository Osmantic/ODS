#!/usr/bin/env python3
"""Bounded native-Windows publications in Dashboard's Linux lock domain.

The caller mounts the installation at /ods and this candidate at /candidate.
All state reads and publications occur in this process while holding flock;
no host callback is permitted to write after a lock-owner transport failure.
"""

from __future__ import annotations

import contextlib
import fcntl
import importlib.util
import json
import os
from pathlib import Path, PureWindowsPath
import stat
import sys
import tempfile
import time

SERVICES = ("remote-provider-egress", "remote-provider-ssh-tunnel")
LIMIT = 1024 * 1024


def directory(path: Path) -> Path:
    if not path.is_absolute():
        raise ValueError("Absolute directory required")
    for part in (path, *path.parents):
        if not stat.S_ISDIR(part.lstat().st_mode):
            raise ValueError("Unsafe transaction directory")
    return path


def read_file(path: Path) -> tuple[bytes, int]:
    directory(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        details = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_nlink != 1
            or details.st_size > LIMIT
        ):
            raise ValueError("Unsafe transaction file")
        data = stream.read(LIMIT + 1)
        if len(data) > LIMIT:
            raise ValueError("Oversized transaction file")
        return data, stat.S_IMODE(details.st_mode)


def exists(path: Path) -> bool:
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False


def service_dir(root: Path, service: str) -> Path:
    return directory(root / "extensions" / "services" / service)


@contextlib.contextmanager
def graph_lock(root: Path, timeout: float):
    lock = directory(root / "data") / ".extensions-lock"
    # Phase 06 creates the shared lock and grants the Dashboard runtime access.
    # A root-owned replacement here would strand that runtime after install.
    fd = os.open(lock, os.O_RDWR | os.O_NOFOLLOW)
    try:
        details = os.fstat(fd)
        if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
            raise ValueError("Unsafe extensions lock")
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ValueError("Timed out waiting for extensions lock") from None
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def choices(root: Path, source: Path) -> dict[str, str]:
    # Reuse the existing strict route/marker contract with no copied-marker
    # exception: native robocopy excludes both marker names entirely.
    helper = source / "scripts" / "remote-provider-compose-selection.py"
    read_file(helper)
    spec = importlib.util.spec_from_file_location("_remote_choices", helper)
    if spec is None or spec.loader is None:
        raise ValueError("Selection helper unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for service in SERVICES:
        folder = service_dir(root, service)
        for name in ("compose.yaml", "compose.yaml.disabled"):
            path = folder / name
            if exists(path):
                read_file(path)
    route_dir = root / "data" / "remote-provider"
    if exists(route_dir):
        directory(route_dir)
    return module.inspect(root)["selection"]


def publish(root: Path, path: Path, data: bytes, mode: int) -> None:
    allowed = {root / ".compose-flags"}
    for service in SERVICES:
        allowed.update(
            root / "extensions" / "services" / service / name
            for name in ("compose.yaml", "compose.yaml.disabled")
        )
    if path not in allowed or len(data) > LIMIT:
        raise ValueError("Publication outside transaction scope")
    directory(path.parent)
    if exists(path):
        _, mode = read_file(path)
    fd, temporary = tempfile.mkstemp(prefix=".ods-remote-recipe-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def sync_recipes(root: Path, source: Path) -> dict:
    selection = choices(root, source)
    publications = []
    for service in SERVICES:
        source_dir = service_dir(source, service)
        if exists(source_dir / "compose.yaml"):
            raise ValueError("Canonical remote recipe must be disabled")
        data, mode = read_file(source_dir / "compose.yaml.disabled")
        if not data:
            raise ValueError("Empty canonical remote recipe")
        name = (
            "compose.yaml"
            if selection[service] == "enabled"
            else "compose.yaml.disabled"
        )
        publications.append((service_dir(root, service) / name, data, mode))
    for path, data, mode in publications:
        publish(root, path, data, mode)
    for path, data, _ in publications:
        if read_file(path)[0] != data:
            raise ValueError("Remote recipe publication mismatch")
    return {"selection": selection}


def relative_file(value: str) -> str:
    normalized = value.replace("\\", "/")
    if (
        not normalized
        or PureWindowsPath(value).drive
        or normalized.startswith("/")
        or any(part in ("", ".", "..") for part in normalized.split("/"))
        or any(char.isspace() for char in normalized)
    ):
        raise ValueError("Invalid relative Compose path")
    return normalized


def publish_flags(root: Path, source: Path, flags: object) -> dict:
    if (
        not isinstance(flags, list)
        or not flags
        or len(flags) % 2
        or not all(isinstance(item, str) for item in flags)
    ):
        raise ValueError("Invalid Compose flags")
    remote = {
        f"extensions/services/{service}/compose.yaml": service for service in SERVICES
    }
    retained = []
    for index in range(0, len(flags), 2):
        if flags[index] != "-f":
            raise ValueError("Invalid Compose file option")
        normalized = relative_file(flags[index + 1])
        if normalized not in remote:
            retained.append(normalized)
    selection = choices(root, source)
    if "docker-compose.base.yml" not in retained:
        raise ValueError("Compose base file is required")
    fresh = [
        path for path, service in remote.items() if selection[service] == "enabled"
    ]
    insert = next(
        (
            i
            for i, path in enumerate(retained)
            if path in {"docker-compose.tier0.yml", "docker-compose.override.yml"}
        ),
        len(retained),
    )
    retained[insert:insert] = fresh
    for path in retained:
        read_file(root / path)
    output = [token for path in retained for token in ("-f", path)]
    data = " ".join(output).encode("utf-8")
    publish(root, root / ".compose-flags", data, 0o644)
    return {"flags": output, "selection": selection}


def transact(root: Path, source: Path, request: dict) -> dict:
    if set(request) - {"operation", "flags", "lockTimeout"}:
        raise ValueError("Unknown transaction field")
    operation = request.get("operation")
    if operation not in {"sync", "flags"}:
        raise ValueError("Unknown transaction operation")
    timeout = request.get("lockTimeout", 15.0)
    if type(timeout) not in (int, float) or not 0 <= timeout <= 15:
        raise ValueError("Invalid lock timeout")
    directory(root)
    directory(source)
    with graph_lock(root, float(timeout)):
        result = (
            sync_recipes(root, source)
            if operation == "sync"
            else publish_flags(root, source, request.get("flags"))
        )
        return {
            "schema": "ods.windows-remote-transaction.v1",
            "operation": operation,
            **result,
        }


def main() -> int:
    try:
        if sys.argv[1:] == ["--request-file"]:
            raw, _ = read_file(Path("/transaction/request.json"))
        elif sys.argv[1:]:
            raise ValueError("Unknown transaction arguments")
        else:
            raw = sys.stdin.buffer.read(LIMIT + 1)
        if len(raw) > LIMIT:
            raise ValueError("Oversized transaction request")
        request = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(request, dict):
            raise ValueError("Invalid transaction request")
        result = transact(Path("/ods"), Path("/candidate"), request)
        print(json.dumps(result, separators=(",", ":")), flush=True)
        return 0
    except (OSError, ValueError):
        print(
            "Remote-provider transaction failed; installation must not continue.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
