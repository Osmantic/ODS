#!/usr/bin/env python3
"""Owner-side client for the narrow Pixel release operator.

Builds an explicit-argv localhost SSH command against the dedicated forced-command
transport account. There is no shell interpolation, no password, no agent forwarding, and
no generic SSH session; the only command the transport account may run is the fixed
release-operator helper through its forced command. Host-key verification is strict and
pinned to the single root-owned known-hosts file provisioned from the exact local Ed25519
host key; no fallback known-host stores are consulted. The transport is fixed to the
loopback address 127.0.0.1 on the fixed port 22, matching the port-22-only known_hosts pin.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys

from pixel_release_grammar import validate_operation

HELPER = "/usr/local/libexec/pixel-release-managed"
KNOWN_HOSTS = "/etc/pixel-release-operator/known_hosts"
HOST = "127.0.0.1"
PORT = "22"
SAFE_USER = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
OPERATOR_SSH = "/usr/bin/ssh"
MAX_OUTPUT = 4 * 1024 * 1024
CLIENT_TIMEOUT = 60


class ClientError(RuntimeError):
    pass


def required_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise ClientError(f"release-operator requires {name}")
    return value


def validate_key(path: str) -> None:
    try:
        raw = os.lstat(path)
    except FileNotFoundError as error:
        raise ClientError("release-operator private key is missing") from error
    if stat.S_ISLNK(raw.st_mode):
        raise ClientError("release-operator private key must not be a symlink")
    if not stat.S_ISREG(raw.st_mode):
        raise ClientError("release-operator private key must be a regular file")
    if raw.st_uid != os.getuid():
        raise ClientError("release-operator private key must be owned by the deployment owner")
    if raw.st_mode & 0o077:
        raise ClientError("release-operator private key must be mode 0600")


def validate_known_hosts(path: str) -> None:
    try:
        raw = os.lstat(path)
    except FileNotFoundError as error:
        raise ClientError("release-operator known_hosts is missing; run provisioning") from error
    if stat.S_ISLNK(raw.st_mode):
        raise ClientError("release-operator known_hosts must not be a symlink")
    if not stat.S_ISREG(raw.st_mode):
        raise ClientError("release-operator known_hosts must be a regular file")
    if raw.st_uid != 0:
        raise ClientError("release-operator known_hosts must be root-owned")
    if raw.st_mode & 0o022:
        raise ClientError("release-operator known_hosts must not be group or world writable")


def build_argv(user: str, key: str, port: str, operation: list[str]) -> list[str]:
    if not SAFE_USER.fullmatch(user):
        raise ClientError("release-operator transport user is unsafe")
    validate_key(key)
    validate_known_hosts(KNOWN_HOSTS)
    if port != PORT:
        raise ClientError("release-operator SSH port must be the fixed port 22")
    return [
        OPERATOR_SSH,
        "-F", "/dev/null",
        "-i", key,
        "-o", "IdentitiesOnly=yes",
        "-o", "BatchMode=yes",
        "-o", "ForwardAgent=no",
        "-o", "ForwardX11=no",
        "-o", "ControlMaster=no",
        "-o", "ControlPath=none",
        "-o", "ProxyCommand=none",
        "-o", "ProxyJump=none",
        "-o", "LocalCommand=none",
        "-o", "PermitLocalCommand=no",
        "-o", "UpdateHostKeys=no",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "UserKnownHostsFile=" + KNOWN_HOSTS,
        "-o", "GlobalKnownHostsFile=" + KNOWN_HOSTS,
        "-o", "LogLevel=ERROR",
        "-o", "ConnectTimeout=15",
        "-p", PORT,
        f"{user}@{HOST}",
        HELPER,
        *operation,
    ]


def run_ssh(argv: list[str]) -> int:
    """Run the bounded SSH command, treating a hard timeout as failure."""
    try:
        completed = subprocess.run(
            argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=CLIENT_TIMEOUT
        )
    except subprocess.TimeoutExpired as error:
        raise ClientError("release-operator ssh exceeded its hard timeout") from error
    if completed.stdout:
        sys.stdout.buffer.write(completed.stdout[:MAX_OUTPUT])
    if completed.stderr:
        sys.stderr.buffer.write(completed.stderr[:MAX_OUTPUT])
    return completed.returncode


def main() -> int:
    operation = validate_operation(sys.argv[1:])
    user = required_env("PIXEL_RELEASE_OPERATOR_USER")
    key = required_env("PIXEL_RELEASE_OPERATOR_KEY")
    argv = build_argv(user, key, PORT, operation)
    return run_ssh(argv)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ClientError, ValueError, OSError) as error:
        print(f"pixel-release-client: {error}", file=sys.stderr)
        raise SystemExit(1)
