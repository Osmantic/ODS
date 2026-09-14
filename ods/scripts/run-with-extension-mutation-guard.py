#!/usr/bin/env python3
"""Exec one host mutation while holding ODS's canonical extension guard."""

from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_API_DIR = ROOT / "extensions" / "services" / "dashboard-api"
if str(DASHBOARD_API_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_API_DIR))

from extension_operation_locks import (  # noqa: E402
    ServiceLockError,
    ServiceLockTimeout,
    exclusive_file_lock,
    mutation_guard_path,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Hold the canonical ODS mutation guard and exec a command."
    )
    parser.add_argument("--lock-parent", required=True)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--verify-held-fd", type=int)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def _verify_held_guard(lock_parent: Path, descriptor: int) -> bool:
    """Bind an inherited descriptor to the canonical inode and its live lock."""
    if os.name == "nt" or descriptor < 0:
        return False
    try:
        import fcntl

        guard_path = mutation_guard_path(lock_parent)
        descriptor_info = os.fstat(descriptor)
        path_info = guard_path.lstat()
        if (
            not stat.S_ISREG(descriptor_info.st_mode)
            or not stat.S_ISREG(path_info.st_mode)
            or descriptor_info.st_nlink != 1
            or path_info.st_nlink != 1
            or descriptor_info.st_uid != os.geteuid()
            or stat.S_IMODE(descriptor_info.st_mode) & 0o077
            or (descriptor_info.st_dev, descriptor_info.st_ino)
            != (path_info.st_dev, path_info.st_ino)
        ):
            return False
        # A genuine wrapper descriptor already owns this flock. If a caller
        # merely opened the canonical file and forged the environment marker,
        # acquire the lock on that inherited open-file description now; the
        # parent Bash retains it after this verifier exits.
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, ServiceLockError):
        return False
    return True


def main() -> int:
    args = _parser().parse_args()
    command = list(args.command)
    if command and command[0] == "--":
        command.pop(0)
    if args.verify_held_fd is not None:
        if command:
            return 64
        try:
            lock_parent = Path(args.lock_parent).resolve(strict=True)
        except OSError:
            return 1
        return 0 if _verify_held_guard(lock_parent, args.verify_held_fd) else 1
    if not command:
        print("ERROR: mutation guard command is required", file=sys.stderr)
        return 64
    if os.name == "nt":
        print(
            "ERROR: Assistant First mutation coordination is not yet qualified "
            "for native Windows source updates.",
            file=sys.stderr,
        )
        return 69

    try:
        lock_parent = Path(args.lock_parent).resolve(strict=True)
        if not lock_parent.is_dir():
            raise ServiceLockError("mutation-guard-parent-not-directory")
        guard_path = mutation_guard_path(lock_parent, repair_mode=True)
        with exclusive_file_lock(guard_path, timeout=args.timeout) as lockfile:
            descriptor = lockfile.fileno()
            os.set_inheritable(descriptor, True)
            environment = dict(os.environ)
            environment["ODS_MUTATION_GUARD_FD"] = str(descriptor)
            os.execvpe(command[0], command, environment)
    except ServiceLockTimeout:
        print(
            "ERROR: Another ODS update or extension mutation is in progress. "
            "Wait for it to finish, then retry.",
            file=sys.stderr,
        )
        return 75
    except (OSError, ServiceLockError) as exc:
        code = str(exc) or type(exc).__name__
        print(f"ERROR: Could not acquire the safe ODS mutation guard: {code}", file=sys.stderr)
        return 74

    return 70


if __name__ == "__main__":
    raise SystemExit(main())
