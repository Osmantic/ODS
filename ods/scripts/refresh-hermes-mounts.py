#!/usr/bin/env python3
"""Rebind only this installation's Hermes when its reviewed file mounts are stale."""
import argparse
import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
import time
from pathlib import Path

FILES = {
    "/opt/hermes/cli-config.yaml.example": "extensions/services/hermes/cli-config.yaml.template",
    "/opt/hermes/ods-auth-bootstrap.py": "extensions/services/dashboard-api/hermes_auth.py",
    "/opt/hermes/docker/SOUL.md": "data/persona/SOUL.md",
}
LIVE_CONFIG = "/opt/data/config.yaml"
MAX_BYTES = 1024 * 1024
DESKTOP_FILE_SOURCE = re.compile(
    r"/run/desktop/mnt/host/wsl/docker-desktop-bind-mounts/[A-Za-z0-9_.-]{1,128}/[a-f0-9]{64}"
)
DESKTOP_VISIBLE_ROOT = Path("/mnt/wsl/docker-desktop-bind-mounts")
# Hash inside the container: never return credential-bearing file contents.
CONTAINER_DIGESTS = """import hashlib,json,os,stat,sys
result={}
for path in sys.argv[1:]:
    try:
        fd=os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        if path == '/opt/data/config.yaml':
            result[path]=None
            continue
        raise
    with os.fdopen(fd,'rb') as source:
        info=os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 1048576:
            raise ValueError('unsupported file')
        raw=source.read(1048577)
        if len(raw)>1048576: raise ValueError('oversized file')
        result[path]=hashlib.sha256(raw).hexdigest()
print(json.dumps(result))
"""


class Refused(RuntimeError):
    pass


def execute(argv, cwd, timeout=30):
    try:
        result = subprocess.run(argv, cwd=cwd, capture_output=True, timeout=timeout, check=True)
        return result.stdout.decode("utf-8")
    except (OSError, UnicodeError, subprocess.SubprocessError) as exc:
        # Docker/config errors may contain secrets. Keep them out of installer logs.
        raise Refused("Hermes mount verification command failed") from exc


def desktop_data_source_matches(source, expected):
    """Prove a Desktop directory proxy aliases this installation's live data."""
    if not isinstance(source, str) or not DESKTOP_FILE_SOURCE.fullmatch(source):
        return False
    distro, digest = source.rsplit("/", 2)[1:]
    if distro in {".", ".."}:
        return False
    visible = DESKTOP_VISIBLE_ROOT / distro / digest
    try:
        # Resolution alone follows links; equality rejects links in any ancestor.
        if visible.resolve(strict=True) != visible or expected.resolve(strict=True) != expected:
            return False
        actual = os.stat(visible, follow_symlinks=False)
        wanted = os.stat(expected, follow_symlinks=False)
        return (stat.S_ISDIR(actual.st_mode) and stat.S_ISDIR(wanted.st_mode)
                and (actual.st_dev, actual.st_ino) == (wanted.st_dev, wanted.st_ino))
    except (OSError, RuntimeError):
        return False


def host_digests(root):
    result = {}
    for destination, relative in FILES.items():
        path = root
        for component in Path(relative).parts:
            path = path / component
            if path.is_symlink():
                raise Refused("Hermes mount source contains a symlink")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
                raise Refused("Hermes mount source is not a bounded regular file")
            raw = source.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise Refused("Hermes mount source is too large")
            result[destination] = hashlib.sha256(raw).hexdigest()
    return result


def refresh(root, docker, compose, run=execute):
    root = Path(root).absolute()
    if root != root.resolve() or not root.is_dir():
        raise Refused("Hermes installation root is not a real directory")
    config = json.loads(run(compose + ["config", "--format", "json"], root))
    service = config["services"].get("hermes")
    if service is None:
        return "not-selected"
    project = config["name"]
    if not isinstance(project, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project):
        raise Refused("Hermes Compose project is invalid")
    volumes = service.get("volumes", [])
    for destination, relative in {**FILES, "/opt/data": "data/hermes"}.items():
        matches = [item for item in volumes if item.get("target") == destination]
        if (len(matches) != 1 or matches[0].get("type") != "bind"
                or matches[0].get("source") != str(root / relative)
                or bool(matches[0].get("read_only")) != (destination != "/opt/data")):
            raise Refused("Hermes Compose mount is outside the installed contract")

    def container():
        ids = run(compose + ["ps", "--all", "--quiet", "hermes"], root).split()
        if len(ids) != 1 or not re.fullmatch(r"[a-f0-9]{64}", ids[0]):
            raise Refused("Hermes container identity is unavailable or ambiguous")
        inspected = json.loads(run(docker + ["inspect", ids[0]], root))
        if not isinstance(inspected, list) or len(inspected) != 1:
            raise Refused("Hermes container inspection is invalid")
        item = inspected[0]
        labels = item.get("Config", {}).get("Labels", {}) or {}
        if (item.get("Id") != ids[0] or item.get("State", {}).get("Running") is not True
                or labels.get("com.docker.compose.project") != project
                or labels.get("com.docker.compose.service") != "hermes"
                or labels.get("com.docker.compose.project.working_dir") != str(root)
                or labels.get("com.docker.compose.oneoff", "").lower() != "false"):
            raise Refused("Hermes running-container ownership could not be verified")
        if item["State"].get("Health", {}).get("Status") not in {"starting", "healthy", "unhealthy"}:
            raise Refused("Hermes container health observation is unavailable")
        for destination in [*FILES, "/opt/data"]:
            mounts = [mount for mount in item.get("Mounts", []) if mount.get("Destination") == destination]
            if (len(mounts) != 1 or mounts[0].get("Type") != "bind"
                    or mounts[0].get("RW") is not (destination == "/opt/data")):
                raise Refused("Hermes running mount is outside the installed contract")
            relative = "data/hermes" if destination == "/opt/data" else FILES[destination]
            source = mounts[0].get("Source")
            # Desktop substitutes individual WSL file binds with a proxy inode.
            # Desktop can proxy directories too; require filesystem identity
            # with this installation's live data, never merely a matching prefix.
            if source != str(root / relative) and not (
                    destination in FILES and isinstance(source, str) and DESKTOP_FILE_SOURCE.fullmatch(source)) and not (
                    destination == "/opt/data" and desktop_data_source_matches(source, root / relative)):
                raise Refused("Hermes running mount has an unexplained source")
        return item

    def observed(identity):
        values = json.loads(run(docker + ["exec", identity, "python3", "-c", CONTAINER_DIGESTS,
                                         *FILES, LIVE_CONFIG], root))
        if (not isinstance(values, dict) or set(values) != {*FILES, LIVE_CONFIG}
                or any(not (key == LIVE_CONFIG and value is None)
                       and (not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value))
                       for key, value in values.items())):
            raise Refused("Hermes file hashes could not be verified")
        return values

    expected = host_digests(root)
    identity = container()["Id"]
    before = observed(identity)
    if all(before[path] == value for path, value in expected.items()):
        return "current"
    # Recheck the installation and selected container immediately before the
    # only mutation. Never repair an unreadable file or a changed ownership claim.
    if (host_digests(root) != expected or container()["Id"] != identity
            or json.loads(run(compose + ["config", "--format", "json"], root)) != config):
        raise Refused("Hermes installation changed during mount verification")
    run(compose + ["up", "-d", "--no-deps", "--force-recreate", "--no-build", "--pull", "never", "hermes"],
        root, timeout=120)
    # The real entrypoint can seed/migrate live state during startup. Compare
    # config and file hashes only after its configured health probe succeeds.
    deadline = time.monotonic() + 180
    refreshed = container()
    new_identity = refreshed["Id"]
    if new_identity == identity:
        raise Refused("Hermes recreation did not replace the selected container")
    while refreshed["State"]["Health"]["Status"] != "healthy":
        if refreshed["State"]["Health"]["Status"] == "unhealthy" or time.monotonic() >= deadline:
            raise Refused("Hermes did not become healthy after one recreation")
        time.sleep(2)
        refreshed = container()
        if refreshed["Id"] != new_identity:
            raise Refused("Hermes container changed while awaiting startup")
    after = observed(new_identity)
    if host_digests(root) != expected or any(after[path] != value for path, value in expected.items()):
        raise Refused("Hermes mounted files still differ after one recreation")
    if before[LIVE_CONFIG] is not None and before[LIVE_CONFIG] != after[LIVE_CONFIG]:
        raise Refused("Hermes live configuration changed during recreation; review before continuing")
    return "refreshed"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-dir", type=Path, required=True)
    parser.add_argument("--docker-command", default="docker")
    parser.add_argument("--compose-command", default="docker compose")
    parser.add_argument("--compose-arg", action="append", default=[])
    args = parser.parse_args()
    try:
        result = refresh(args.install_dir, shlex.split(args.docker_command),
                         shlex.split(args.compose_command) + args.compose_arg)
    except Refused as exc:
        print(str(exc) + "; no further automatic recreation.")
        return 1
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        print("Hermes mounted-file verification failed; no further automatic recreation. Review the installation before retrying.")
        return 1
    print("Hermes mounted files: " + result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
