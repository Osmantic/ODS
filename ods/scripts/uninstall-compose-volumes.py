#!/usr/bin/env python3
"""Account for Compose-owned volumes before removing an ODS installation.

Compose down -v only knows the currently selected files. Disabled extensions
can leave volumes behind, so a purge must prove their ownership or stop.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys


PROJECT_RE = re.compile(r"[a-z0-9][a-z0-9_-]*\Z")
VOLUME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
CONTAINER_RE = re.compile(r"[a-f0-9]{64}\Z")
PROJECT_LABEL = "com.docker.compose.project"
VOLUME_LABEL = "com.docker.compose.volume"


def docker(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["docker", *args], cwd=root, capture_output=True, text=True,
        encoding="utf-8", timeout=120, check=False,
    )
    if result.returncode:
        # Compose config can expand private environment values. Do not echo
        # command output into an uninstall log or terminal.
        raise ValueError(f"Docker {args[0]} inspection failed (exit {result.returncode})")
    return result.stdout


def rows(raw: str, kind: str) -> list[dict]:
    result = json.loads(raw)
    if not isinstance(result, list) or any(not isinstance(row, dict) for row in result):
        raise ValueError(f"Docker returned invalid {kind} inspection")
    return result


def project_config(root: Path, flags: list[str]) -> tuple[str, dict[str, str], dict[str, str]]:
    config = json.loads(docker(root, "compose", *flags, "config", "--format", "json"))
    if not isinstance(config, dict):
        raise ValueError("Docker returned invalid Compose configuration")
    project = config.get("name")
    if not isinstance(project, str) or not PROJECT_RE.fullmatch(project):
        raise ValueError("Compose project identity is invalid")
    definitions = config.get("volumes") or {}
    if not isinstance(definitions, dict):
        raise ValueError("Compose volume definitions are invalid")
    selected = {}
    external = {}
    for key, value in definitions.items():
        if not isinstance(key, str) or not VOLUME_RE.fullmatch(key) or not isinstance(value, dict):
            raise ValueError("Compose volume definition is invalid")
        name = value.get("name", f"{project}_{key}")
        if not isinstance(name, str) or not VOLUME_RE.fullmatch(name):
            raise ValueError("Compose volume name is invalid")
        if name in selected or name in external:
            raise ValueError("Compose volume name is declared more than once")
        if value.get("external") is True:
            external[name] = key
        else:
            selected[name] = key
    return project, selected, external


def trusted_plain_volume_keys(root: Path) -> set[str]:
    """Read only plain top-level volume declarations in shipped recipes.

    Complex declarations are deliberately not inferred as owned. A disabled
    recipe must also leave a verified container mounting the volume.
    """
    paths = list(root.glob("docker-compose*.yml"))
    for directory in ("extensions/services", "extensions/library/services"):
        paths.extend((root / directory).glob("*/compose*.yaml"))
        paths.extend((root / directory).glob("*/compose*.yml"))
    states: dict[str, set[str]] = {}
    declaration = re.compile(r"^  ([A-Za-z0-9][A-Za-z0-9_.-]*):\s*(?:\{\})?\s*$")
    for path in paths:
        if (path.is_symlink() or not path.is_file() or
                not path.resolve().is_relative_to(root)):
            continue
        in_volumes = False
        key = None
        state = "owned"
        for line in path.read_text(encoding="utf-8").splitlines() + ["__end__"]:
            if line == "volumes:":
                in_volumes = True
                continue
            if not in_volumes:
                continue
            if line and not line[0].isspace():
                if key is not None:
                    states.setdefault(key, set()).add(state)
                key = None
                in_volumes = False
                continue
            match = declaration.fullmatch(line)
            if match:
                if key is not None:
                    states.setdefault(key, set()).add(state)
                key, state = match.group(1), "owned"
            elif key is not None and line.strip() and not line.lstrip().startswith("#"):
                # Any child property, including alternate YAML spellings of
                # external/name, makes the declaration too complex to infer.
                state = "complex"
    return {key for key, values in states.items() if values == {"owned"}}


def project_containers(root: Path, project: str) -> tuple[set[str], set[str]]:
    ids = docker(root, "ps", "--all", "--quiet", "--no-trunc", "--filter",
                 f"label={PROJECT_LABEL}={project}").split()
    if any(not CONTAINER_RE.fullmatch(value) for value in ids):
        raise ValueError("Docker returned invalid container identity")
    mounted = set()
    base_files = {str((root / name).resolve()) for name in
                  ("docker-compose.base.yml", "docker-compose.yml")}
    for offset in range(0, len(ids), 100):
        batch = ids[offset:offset + 100]
        inspected = rows(docker(root, "inspect", *batch), "container")
        if len(inspected) != len(batch) or {row.get("Id") for row in inspected} != set(batch):
            raise ValueError("Docker container inspection changed during preflight")
        for row in inspected:
            config = row.get("Config") or {}
            labels = config.get("Labels") or {}
            if not isinstance(labels, dict):
                raise ValueError("Docker returned invalid container labels")
            working_dir = labels.get("com.docker.compose.project.working_dir")
            files = labels.get("com.docker.compose.project.config_files")
            if (labels.get(PROJECT_LABEL) != project or
                    not isinstance(working_dir, str) or
                    not os.path.isabs(working_dir) or
                    os.path.realpath(working_dir) != str(root) or
                    not isinstance(files, str) or
                    not files.split(",") or
                    not os.path.isabs(files.split(",")[0]) or
                    os.path.realpath(files.split(",")[0]) not in base_files):
                raise ValueError("Compose project contains a container from another installation")
            mounts = row.get("Mounts") or []
            if not isinstance(mounts, list):
                raise ValueError("Docker returned invalid container mounts")
            for mount in mounts:
                if not isinstance(mount, dict):
                    raise ValueError("Docker returned invalid container mount")
                if mount.get("Type") == "volume":
                    name = mount.get("Name")
                    if not isinstance(name, str) or not VOLUME_RE.fullmatch(name):
                        raise ValueError("Docker returned invalid mounted volume")
                    mounted.add(name)
    return set(ids), mounted


def check_volume_consumers(root: Path, names: set[str], owned_ids: set[str]) -> None:
    for name in sorted(names):
        ids = docker(root, "ps", "--all", "--quiet", "--no-trunc", "--filter",
                     f"volume={name}").split()
        if any(not CONTAINER_RE.fullmatch(value) for value in ids):
            raise ValueError("Docker returned invalid volume consumer identity")
        if set(ids) - owned_ids:
            raise ValueError(f"Volume {name} is mounted by a container outside this installation")


def project_volumes(root: Path, project: str) -> dict[str, dict]:
    names = docker(root, "volume", "ls", "--quiet", "--filter",
                   f"label={PROJECT_LABEL}={project}").split()
    if len(names) != len(set(names)) or any(not VOLUME_RE.fullmatch(name) for name in names):
        raise ValueError("Docker returned invalid volume identity")
    found = {}
    for offset in range(0, len(names), 100):
        batch = names[offset:offset + 100]
        inspected = rows(docker(root, "volume", "inspect", *batch), "volume")
        if len(inspected) != len(batch) or {row.get("Name") for row in inspected} != set(batch):
            raise ValueError("Docker volume inspection changed during preflight")
        for row in inspected:
            name = row["Name"]
            labels = row.get("Labels") or {}
            if not isinstance(labels, dict) or labels.get(PROJECT_LABEL) != project:
                raise ValueError(f"Volume {name} has ambiguous Compose ownership")
            volume_key = labels.get(VOLUME_LABEL)
            if not isinstance(volume_key, str) or not VOLUME_RE.fullmatch(volume_key):
                raise ValueError(f"Volume {name} has no valid Compose volume label")
            found[name] = row
    return found


def inspect_volumes(root: Path, names: set[str]) -> dict[str, dict]:
    found = {}
    for offset in range(0, len(names), 100):
        batch = sorted(names)[offset:offset + 100]
        inspected = rows(docker(root, "volume", "inspect", *batch), "volume")
        if len(inspected) != len(batch) or {row.get("Name") for row in inspected} != set(batch):
            raise ValueError("Docker volume inspection changed during preflight")
        found.update({row["Name"]: row for row in inspected})
    return found


def fingerprint(row: dict) -> dict:
    return {key: row.get(key) for key in ("Name", "Labels", "CreatedAt", "Driver")}


def preflight(root: Path, snapshot: Path, flags: list[str], keep_data: bool = False) -> None:
    project, selected, external = project_config(root, flags)
    container_ids, mounted = project_containers(root, project)
    if keep_data:
        snapshot.write_text(json.dumps({
            "schemaVersion": 1, "installDir": str(root), "project": project,
            "volumes": {}, "external": {},
        }), encoding="utf-8")
        return
    volumes = project_volumes(root, project)
    trusted = trusted_plain_volume_keys(root)
    if volumes and not container_ids:
        raise ValueError("ODS project volumes remain but no container proves the installation path")
    owned = {}
    anonymous = {}
    trusted_used = set()
    for name, row in volumes.items():
        key = row["Labels"][VOLUME_LABEL]
        if name in external:
            if key != external[name]:
                raise ValueError(f"External volume {name} has a conflicting Compose label")
            continue
        if name in selected and key != selected[name]:
            raise ValueError(f"Selected volume {name} has a conflicting Compose label")
        if name in selected or (name in mounted and key in trusted and
                                name == f"{project}_{key}"):
            owned[name] = fingerprint(row)
            if name not in selected:
                trusted_used.add(key)
        else:
            raise ValueError(f"Volume {name} is not linked to this installation; purge refused")
    other_mounts = mounted - set(volumes) - set(external)
    for name, row in inspect_volumes(root, other_mounts).items():
        labels = row.get("Labels") or {}
        if (not CONTAINER_RE.fullmatch(name) or labels or
                row.get("Driver") != "local"):
            raise ValueError(f"Mounted volume {name} has unproven ownership; purge refused")
        anonymous[name] = fingerprint(row)
    check_volume_consumers(root, set(owned) | set(anonymous), container_ids)
    record = {
        "schemaVersion": 1,
        "installDir": str(root),
        "project": project,
        "volumes": owned,
        "anonymous": anonymous,
        "external": external,
        "selected": selected,
        "trustedUsed": sorted(trusted_used),
        "flags": flags,
    }
    snapshot.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")


def complete(root: Path, snapshot: Path) -> None:
    record = json.loads(snapshot.read_text(encoding="utf-8"))
    if (not isinstance(record, dict) or record.get("schemaVersion") != 1 or
            record.get("installDir") != str(root) or
            not isinstance(record.get("project"), str) or
            not isinstance(record.get("volumes"), dict)):
        raise ValueError("Uninstall volume snapshot is invalid")
    project = record["project"]
    before = record["volumes"]
    anonymous = record.get("anonymous")
    if not isinstance(anonymous, dict) or any(
            not CONTAINER_RE.fullmatch(name) or not isinstance(value, dict)
            for name, value in anonymous.items()):
        raise ValueError("Uninstall volume snapshot is invalid")
    flags = record.get("flags")
    if not isinstance(flags, list) or any(not isinstance(flag, str) for flag in flags):
        raise ValueError("Uninstall volume snapshot is invalid")
    current_project, current_selected, current_external = project_config(root, flags)
    if (current_project != project or
            current_selected != record.get("selected") or
            current_external != record.get("external") or
            not set(record.get("trustedUsed", [])).issubset(trusted_plain_volume_keys(root))):
        raise ValueError("Compose ownership changed during uninstall; installation retained")
    remaining = project_volumes(root, project)
    external = record.get("external")
    if not isinstance(external, dict) or any(
            not isinstance(name, str) or not isinstance(key, str)
            for name, key in external.items()):
        raise ValueError("Uninstall volume snapshot is invalid")
    remaining = {name: row for name, row in remaining.items() if name not in external}
    if set(remaining) - set(before):
        raise ValueError("New Compose volumes appeared during uninstall; installation retained")
    for name, row in remaining.items():
        if fingerprint(row) != before[name]:
            raise ValueError(f"Volume {name} changed during uninstall; installation retained")
    check_volume_consumers(root, set(remaining), set())
    all_names = set(docker(root, "volume", "ls", "--quiet").split())
    anonymous_remaining = inspect_volumes(root, set(anonymous) & all_names)
    for name, row in anonymous_remaining.items():
        if fingerprint(row) != anonymous[name]:
            raise ValueError(f"Anonymous volume {name} changed during uninstall; installation retained")
    check_volume_consumers(root, set(anonymous_remaining), set())
    for name in sorted(remaining):
        docker(root, "volume", "rm", name)
    for name in sorted(anonymous_remaining):
        docker(root, "volume", "rm", name)
    if set(project_volumes(root, project)) - set(external):
        raise ValueError("Compose volumes remain after cleanup; installation retained")
    if set(anonymous) & set(docker(root, "volume", "ls", "--quiet").split()):
        raise ValueError("Anonymous ODS volumes remain after cleanup; installation retained")


def main() -> int:
    if len(sys.argv) < 4 or sys.argv[1] not in ("preflight", "complete"):
        print("Usage: uninstall-compose-volumes.py preflight|complete INSTALL_DIR SNAPSHOT [COMPOSE_FLAGS...]", file=sys.stderr)
        return 2
    mode, root_arg, snapshot_arg, *flags = sys.argv[1:]
    try:
        root = Path(root_arg).resolve(strict=True)
        snapshot = Path(snapshot_arg)
        if not root.is_dir() or not snapshot.is_file() or snapshot.is_symlink():
            raise ValueError("Installation or volume snapshot is invalid")
        if mode == "preflight":
            keep_data = bool(flags and flags[0] == "--keep-data")
            preflight(root, snapshot, flags[1:] if keep_data else flags, keep_data)
        elif flags:
            raise ValueError("Unexpected Compose arguments for completion")
        else:
            complete(root, snapshot)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as error:
        print(f"ODS volume custody failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
