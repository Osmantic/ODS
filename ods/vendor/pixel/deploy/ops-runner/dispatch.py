#!/usr/bin/env python3
"""Forced-command SSH boundary for a dedicated Pixel Operations runner."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import stat
import sys


SAFE_ID = re.compile(r"[a-z][a-z0-9_.-]{0,63}")
SAFE_SUITE = re.compile(r"[a-z][a-z0-9_-]{0,63}")
SAFE_JOB = re.compile(r"ops-[0-9]{13}-[a-f0-9]{12}")
SAFE_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}")
SHA256 = re.compile(r"[a-f0-9]{64}")
MAX_COMMAND = 32 * 1024
MAX_ARGUMENTS = 32
MAX_ARGUMENT = 4096
WRAPPERS = {
    "/usr/local/libexec/pixel-ops-action",
    "/usr/local/libexec/pixel-ops-managed",
    "/usr/local/libexec/pixel-ops-run-test",
}


class DispatchError(RuntimeError):
    pass


def bounded_arguments(arguments: list[str]) -> None:
    if not 1 <= len(arguments) <= MAX_ARGUMENTS:
        raise DispatchError("command has an unsafe argument count")
    if any(not item or "\x00" in item or len(item) > MAX_ARGUMENT for item in arguments):
        raise DispatchError("command has an unsafe argument")


def validate_command(arguments: list[str], *, allow_managed: bool = True) -> list[str]:
    bounded_arguments(arguments)
    executable = arguments[0]
    if executable in {"/bin/hostname", "/usr/bin/hostname"} and len(arguments) == 1:
        return arguments
    if executable == "/usr/bin/uname" and arguments == ["/usr/bin/uname", "-a"]:
        return arguments
    if executable == "/usr/bin/uptime" and arguments == ["/usr/bin/uptime"]:
        return arguments
    if executable == "/usr/local/libexec/pixel-ops-run-test":
        if len(arguments) != 2 or not SAFE_SUITE.fullmatch(arguments[1]):
            raise DispatchError("named test command is malformed")
        return arguments
    if executable == "/usr/local/libexec/pixel-ops-action":
        if len(arguments) < 3 or arguments[1] not in {"host", "repo", "artifact"}:
            raise DispatchError("runner action command is malformed")
        return arguments
    if executable == "/usr/local/libexec/pixel-ops-receive-artifact":
        if (
            len(arguments) != 4 or not SAFE_JOB.fullmatch(arguments[1])
            or not SAFE_FILENAME.fullmatch(arguments[2]) or not SHA256.fullmatch(arguments[3])
        ):
            raise DispatchError("artifact receiver command is malformed")
        return arguments
    if executable == "/usr/bin/sudo":
        if not allow_managed:
            raise DispatchError("managed commands cannot run under the workload identity")
        expected = ["/usr/bin/sudo", "--non-interactive", "/usr/local/libexec/pixel-ops-managed"]
        if arguments[:3] != expected or len(arguments) < 4:
            raise DispatchError("managed command is malformed")
        return arguments
    raise DispatchError("executable is outside the forced-command allowlist")


def lexical_job_directory(value: str, root: Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise DispatchError("working directory is not absolute")
    normalized = Path(os.path.normpath(value))
    if str(normalized) != value or (normalized != root and root not in normalized.parents):
        raise DispatchError("working directory escaped the runner job root")
    return normalized


def job_directory(value: str, root: Path, root_descriptor: int) -> tuple[Path, int]:
    normalized = lexical_job_directory(value, root)
    try:
        parts = normalized.relative_to(root).parts
    except ValueError as error:
        raise DispatchError("working directory escaped the runner job root") from error
    descriptor = os.dup(root_descriptor)
    try:
        for part in parts:
            next_descriptor = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise DispatchError("working directory is not a real directory")
        return normalized, descriptor
    except Exception:
        os.close(descriptor)
        raise


def parse_original(original: str, root: Path) -> tuple[Path, list[str]]:
    if not original or len(original) > MAX_COMMAND or "\x00" in original or any(character in original for character in "\r\n"):
        raise DispatchError("SSH command is empty, oversized, or contains controls")
    if original == "hostname":
        return root, ["/bin/hostname"]
    try:
        tokens = shlex.split(original, posix=True)
    except ValueError as error:
        raise DispatchError("SSH command quoting is invalid") from error
    if tokens[:1] == ["/usr/local/libexec/pixel-ops-receive-artifact"]:
        return root, validate_command(tokens)
    if len(tokens) < 6 or tokens[:2] != ["cd", "--"] or tokens[3:5] != ["&&", "exec"]:
        raise DispatchError("SSH command does not match Pixel's typed runner grammar")
    cwd = lexical_job_directory(tokens[2], root)
    resolved = cwd.resolve(strict=True)
    if resolved != cwd or (resolved != root and root not in resolved.parents):
        raise DispatchError("working directory contains a symlink or escaped the runner job root")
    return cwd, validate_command(tokens[5:])


def clean_environment(home: str) -> dict[str, str]:
    return {
        "HOME": home,
        "LANG": "C.UTF-8",
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    }


def workload_main(root: Path, cwd_value: str, command: list[str], workload_user: str, testing: bool) -> int:
    if command[:1] == ["--"]:
        command = command[1:]
    command = validate_command(command, allow_managed=False)
    if not testing:
        account = pwd.getpwnam(workload_user)
        if os.geteuid() != account.pw_uid:
            raise DispatchError("workload dispatcher is not running as the dedicated workload user")
        home = account.pw_dir
    else:
        home = str(root.parent)
    unresolved = root.lstat()
    if not stat.S_ISDIR(unresolved.st_mode) or root.is_symlink():
        raise DispatchError("job root must be a real directory")
    root = root.resolve(strict=True)
    raw = root.lstat()
    root_descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(root_descriptor)
        if (opened.st_dev, opened.st_ino) != (raw.st_dev, raw.st_ino):
            raise DispatchError("job root changed during secure open")
        cwd, cwd_descriptor = job_directory(cwd_value, root, root_descriptor)
    finally:
        os.close(root_descriptor)
    try:
        if testing:
            print(json.dumps({
                "cwd": str(cwd), "argv": command, "shell": False, "directoryHandle": True,
                "executionIdentity": workload_user,
            }, sort_keys=True))
            return 0
        os.fchdir(cwd_descriptor)
        os.close(cwd_descriptor)
        cwd_descriptor = -1
        os.execve(command[0], command, clean_environment(home))
        return 1
    finally:
        if cwd_descriptor >= 0:
            os.close(cwd_descriptor)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-root", required=True, type=Path)
    parser.add_argument("--workload-user", default="pixel-runner")
    parser.add_argument("--workload", action="store_true")
    parser.add_argument("--cwd")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    root = args.job_root
    if not root.is_absolute() or root == Path("/"):
        raise DispatchError("job root must be an absolute non-root path")
    testing = os.environ.get("PIXEL_OPS_DISPATCH_TESTING") == "1" and getattr(os, "geteuid", lambda: 0)() != 0
    if args.workload:
        if not args.cwd or not args.command:
            raise DispatchError("workload dispatch requires a working directory and command")
        return workload_main(root, args.cwd, args.command, args.workload_user, testing)
    if args.cwd is not None or args.command:
        raise DispatchError("transport dispatch accepts commands only from SSH_ORIGINAL_COMMAND")
    unresolved = root.lstat()
    if not stat.S_ISDIR(unresolved.st_mode) or root.is_symlink():
        raise DispatchError("job root must be a real directory")
    root = root.resolve(strict=True)
    cwd, command = parse_original(os.environ.get("SSH_ORIGINAL_COMMAND", ""), root)
    managed = command[0] == "/usr/bin/sudo"
    if managed:
        routed = ["/usr/bin/sudo", "--non-interactive", "--user", "root", "--", *command[2:]]
        identity = "root"
    else:
        routed = [
            "/usr/bin/sudo", "--non-interactive", "--user", args.workload_user, "--",
            "/usr/local/libexec/pixel-ops-dispatch", "--workload", "--job-root", str(root),
            "--workload-user", args.workload_user, "--cwd", str(cwd), "--", *command,
        ]
        identity = args.workload_user
    if testing:
        print(json.dumps({
            "cwd": str(cwd), "argv": command, "shell": False, "directoryHandle": not managed,
            "executionIdentity": identity, "transportIdentitySeparated": True, "routedArgv": routed,
        }, sort_keys=True))
        return 0
    os.chdir("/")
    os.execve(routed[0], routed, clean_environment(str(root.parent)))
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DispatchError, OSError) as error:
        print(f"pixel-ops-dispatch: {error}", file=sys.stderr)
        raise SystemExit(126)
