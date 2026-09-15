"""Host-owned, read-only Docker CLI transport for data quiescence observations.

The observer still cannot prove that non-Docker processes are stopped or that
an external writer will not race a transition.  This transport only prevents
client context/environment drift and refuses a changed local socket/daemon.
It is not a production restore selector.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

from extension_lifecycle_work import LifecycleWorkExecutionError


_SERVICE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_CONTAINER_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_OUTPUT = 2 * 1024 * 1024
_INFO_ARGS = ("info", "--format", "{{json .ID}}")


class LocalDockerRunnerError(LifecycleWorkExecutionError):
    """Value-free refusal of a drifting or untrusted local Docker client."""


def _fail(cause: BaseException | None = None) -> None:
    if cause is None:
        raise LocalDockerRunnerError("lifecycle-work-data-quiescence-docker-unavailable") from None
    raise LocalDockerRunnerError("lifecycle-work-data-quiescence-docker-unavailable") from cause


def _identity(path: Path, *, socket: bool) -> tuple[int, int, int, int, int]:
    """Refuse links and writable ancestors; compare exact local inode state."""
    if not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts:
        _fail()
    current = Path("/")
    try:
        for component in path.parts[1:]:
            parent = os.lstat(current)
            if (
                not stat.S_ISDIR(parent.st_mode)
                or parent.st_uid not in {0, os.geteuid()}
                or stat.S_IMODE(parent.st_mode) & 0o022
            ):
                _fail()
            current /= component
        info = os.lstat(current)
        if (
            info.st_uid not in {0, os.geteuid()}
            or (socket and not stat.S_ISSOCK(info.st_mode))
            or (not socket and not stat.S_ISREG(info.st_mode))
            or (not socket and stat.S_IMODE(info.st_mode) & 0o022)
            or info.st_nlink != 1
        ):
            _fail()
        return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid)
    except OSError as exc:
        _fail(exc)


def _read_only(argv: list[str]) -> bool:
    if not isinstance(argv, list) or not all(type(item) is str for item in argv):
        return False
    if argv == ["container", "ls", "--no-trunc", "--format", "{{.ID}}"]:
        return True
    if (
        len(argv) == 7 and argv[:4] == ["container", "ls", "--all", "--filter"]
        and argv[5:] == ["--format", "{{.State}}"]
        and argv[4].startswith("label=com.docker.compose.service=")
    ):
        service = argv[4].removeprefix("label=com.docker.compose.service=")
        return _SERVICE_RE.fullmatch(service) is not None
    return (
        len(argv) >= 6 and argv[:5] == [
            "inspect", "--type", "container", "--format", "{{json .Mounts}}",
        ]
        and len(argv[5:]) <= 512
        and len(argv[5:]) == len(set(argv[5:]))
        and all(_CONTAINER_RE.fullmatch(item) is not None for item in argv[5:])
    )


class PinnedLocalDockerRunner:
    """Run only the observer's queries against one fixed local Docker daemon."""

    def __init__(self, *, socket: Path = Path("/run/docker.sock"),
                 executable: Path = Path("/usr/bin/docker")) -> None:
        if sys.platform != "linux":
            _fail()
        self._socket = socket
        self._executable = executable
        self._socket_identity = _identity(socket, socket=True)
        self._binary_identity = _identity(executable, socket=False)
        self._host = f"unix://{socket}"
        # Never inherit a remote Docker context, proxy, TLS setting, or
        # caller-specific client configuration from the service environment.
        self._environment = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"}
        self._daemon_id = self._info_id()

    def _check_files(self) -> None:
        if (
            _identity(self._socket, socket=True) != self._socket_identity
            or _identity(self._executable, socket=False) != self._binary_identity
        ):
            _fail()

    def _invoke(self, argv: tuple[str, ...] | list[str]) -> subprocess.CompletedProcess[bytes]:
        self._check_files()
        try:
            result = subprocess.run(
                [str(self._executable), "--host", self._host, *argv],
                env=self._environment, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=15, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            _fail(exc)
        self._check_files()
        if (
            type(result.returncode) is not int or result.returncode != 0
            or not isinstance(result.stdout, bytes) or len(result.stdout) > _MAX_OUTPUT
            or not isinstance(result.stderr, bytes) or len(result.stderr) > 128 * 1024
        ):
            _fail()
        return result

    def _info_id(self) -> str:
        result = self._invoke(_INFO_ARGS)
        try:
            value = json.loads(result.stdout.decode("ascii", "strict"))
        except (UnicodeError, ValueError, RecursionError) as exc:
            _fail(exc)
        if (
            not isinstance(value, str) or not 1 <= len(value) <= 128
            or not value.isascii() or any(character.isspace() for character in value)
        ):
            _fail()
        return value

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[bytes]:
        if not _read_only(argv):
            _fail()
        if self._info_id() != self._daemon_id:
            _fail()
        result = self._invoke(argv)
        if self._info_id() != self._daemon_id:
            _fail()
        return result


__all__ = ["LocalDockerRunnerError", "PinnedLocalDockerRunner"]
