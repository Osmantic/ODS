#!/usr/bin/env python3
"""Account for Compose-owned volumes before removing an ODS installation.

Compose only knows the currently selected files. This helper accounts for
selected and disabled-extension volumes before removing any of them.
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
# Volumes the AMD overlay declared for the retired Lemonade runtime. No current
# recipe declares them, so after the llama.cpp upgrade no container mounts
# them. The Lemonade migration records them in this installation's data
# directory; that record, the project label and the project-prefixed name
# stand in for the container mount that proves the other volumes.
RETIRED_VOLUME_KEYS = frozenset({"lemonade-cache", "lemonade-llama", "lemonade-recipe"})
RETIRED_VOLUME_RECORD = "data/lemonade-retired-volumes.json"


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


def trusted_plain_volume_keys(root: Path, disabled_only: bool = False,
                              only_file: Path | None = None) -> set[str]:
    """Read only plain top-level volume declarations in shipped recipes.

    Complex declarations are deliberately not inferred as owned. A disabled
    recipe must also leave a verified container mounting the volume.
    """
    paths = [only_file] if only_file is not None else (
        [] if disabled_only else list(root.glob("docker-compose*.yml"))
    )
    if only_file is None:
        for directory in ("extensions/services", "extensions/library/services"):
            suffixes = ("*.yaml.disabled", "*.yml.disabled") if disabled_only else (
                "*.yaml", "*.yml",
            )
            for suffix in suffixes:
                paths.extend((root / directory).glob(f"*/compose{suffix}"))
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


def disabled_volume_provenance(root: Path, trusted_root: Path) -> dict[str, set[tuple[str, str]]]:
    found: dict[str, set[tuple[str, str]]] = {}
    for directory in ("extensions/services", "extensions/library/services"):
        for suffix in ("*.yaml.disabled", "*.yml.disabled"):
            for path in (root / directory).glob(f"*/compose{suffix}"):
                enabled_name = path.name.removesuffix(".disabled")
                enabled_path = path.with_name(enabled_name)
                candidate_path = trusted_root / path.relative_to(root).with_name(enabled_name)
                for key in trusted_plain_volume_keys(root, only_file=path):
                    if (trusted_root != root and
                            key not in trusted_plain_volume_keys(
                                trusted_root, only_file=candidate_path)):
                        continue
                    found.setdefault(key, set()).add(
                        (path.parent.name, str(enabled_path.resolve()))
                    )
    return found


def disabled_volume_keys(root: Path, trusted_root: Path) -> set[str]:
    return set(disabled_volume_provenance(root, trusted_root))


def retired_volume_keys(root: Path) -> set[str]:
    """Retired Lemonade volume keys this installation's migration recorded."""
    path = root / RETIRED_VOLUME_RECORD
    if not os.path.lexists(path):
        return set()
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{RETIRED_VOLUME_RECORD} is not a regular file")
    record = json.loads(path.read_text(encoding="utf-8"))
    keys = record.get("volumeKeys") if isinstance(record, dict) else None
    if (not isinstance(record, dict) or record.get("schemaVersion") != 1 or
            record.get("installDir") != str(root) or not isinstance(keys, list) or
            any(not isinstance(key, str) or key not in RETIRED_VOLUME_KEYS for key in keys)):
        raise ValueError(
            f"{RETIRED_VOLUME_RECORD} does not belong to this installation; remove the "
            "retired Lemonade volumes yourself with 'docker volume rm', or rerun with --keep-data"
        )
    return set(keys)


def project_containers(
    root: Path, project: str,
) -> tuple[set[str], set[str], dict[str, set[tuple[str, str]]]]:
    ids = docker(root, "ps", "--all", "--quiet", "--no-trunc", "--filter",
                 f"label={PROJECT_LABEL}={project}").split()
    if any(not CONTAINER_RE.fullmatch(value) for value in ids):
        raise ValueError("Docker returned invalid container identity")
    mounted = set()
    provenance: dict[str, set[tuple[str, str]]] = {}
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
            service = labels.get("com.docker.compose.service")
            if (labels.get(PROJECT_LABEL) != project or
                    not isinstance(working_dir, str) or
                    not os.path.isabs(working_dir) or
                    os.path.realpath(working_dir) != str(root) or
                    not isinstance(files, str) or
                    not files.split(",") or
                    not os.path.isabs(files.split(",")[0]) or
                    os.path.realpath(files.split(",")[0]) not in base_files or
                    not isinstance(service, str) or
                    not PROJECT_RE.fullmatch(service)):
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
                    for config_file in files.split(","):
                        if os.path.isabs(config_file):
                            provenance.setdefault(name, set()).add(
                                (service, os.path.realpath(config_file))
                            )
    return set(ids), mounted, provenance


def install_root_containers(root: Path) -> set[str]:
    """Find Compose containers still tied to this tree across project renames."""
    # This scan only blocks removal; project_containers separately inspects
    # every resource whose ownership can authorize cleanup. Read path labels
    # from the list response so an uninspectable, unrelated Desktop record
    # cannot prevent retiring this installation. Never suppress inspect errors
    # for the selected project or ignore a renamed project using this tree.
    template = (
        '{"Id":{{json .ID}},'
        '"workingDir":{{json (.Label "com.docker.compose.project.working_dir")}},'
        '"configFiles":{{json (.Label "com.docker.compose.project.config_files")}}}'
    )
    listing = docker(root, "ps", "--all", "--no-trunc", "--filter",
                     f"label={PROJECT_LABEL}", "--format", template)
    base_files = {str((root / name).resolve()) for name in
                  ("docker-compose.base.yml", "docker-compose.yml")}
    found = set()
    for line in listing.splitlines():
        row = json.loads(line)
        if (not isinstance(row, dict) or set(row) != {"Id", "workingDir", "configFiles"}
                or not isinstance(row["Id"], str) or not CONTAINER_RE.fullmatch(row["Id"])
                or not isinstance(row["workingDir"], str)
                or not isinstance(row["configFiles"], str)):
            raise ValueError("Docker returned invalid container path listing")
        working_dir = row["workingDir"]
        first_file = row["configFiles"].split(",")[0]
        if ((os.path.isabs(working_dir) and os.path.realpath(working_dir) == str(root))
                or (os.path.isabs(first_file) and os.path.realpath(first_file) in base_files)):
            found.add(row["Id"])
    return found


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


# A purge preflight proves volume ownership through the containers that mount
# them, and Compose down then removes those containers. Keep the verified
# record in the retained installation tree so an uninstall interrupted after
# that point can finish: only volumes whose exact identity is unchanged since
# the proof are accepted, and foreign consumers are still refused.
RESUME_RECORD = ".ods-uninstall-custody.json"


def resume_record(root: Path, project: str) -> tuple[dict[str, dict], dict[str, dict]]:
    path = root / RESUME_RECORD
    if not os.path.lexists(path):
        return {}, {}
    if path.is_symlink() or not path.is_file():
        raise ValueError("Uninstall resume record is invalid")
    record = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(record, dict) or record.get("schemaVersion") != 1 or
            record.get("installDir") != str(root) or
            record.get("project") != project):
        raise ValueError("Uninstall resume record does not belong to this installation")
    volumes, anonymous = record.get("volumes"), record.get("anonymous", {})
    if (not isinstance(volumes, dict) or not isinstance(anonymous, dict) or
            any(not isinstance(name, str) or not VOLUME_RE.fullmatch(name) or
                not isinstance(value, dict) for name, value in volumes.items()) or
            any(not CONTAINER_RE.fullmatch(name) or not isinstance(value, dict)
                for name, value in anonymous.items())):
        raise ValueError("Uninstall resume record is invalid")
    return volumes, anonymous


def write_resume_record(root: Path, record: dict) -> None:
    temporary = root / (RESUME_RECORD + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, root / RESUME_RECORD)


def preflight(root: Path, snapshot: Path, flags: list[str], keep_data: bool = False,
              trusted_root: Path | None = None) -> None:
    trusted_root = trusted_root or root
    project, selected, external = project_config(root, flags)
    container_ids, mounted, mount_provenance = project_containers(root, project)
    other_project = install_root_containers(root) - container_ids
    if other_project:
        raise ValueError("Compose containers from another project still reference this installation: "
                         + ", ".join(sorted(other_project)[:3]))
    if keep_data:
        snapshot.write_text(json.dumps({
            "schemaVersion": 1, "installDir": str(root), "project": project,
            "trustedSource": str(trusted_root), "flags": flags,
            "volumes": {}, "external": {},
        }), encoding="utf-8")
        return
    volumes = project_volumes(root, project)
    resumed, resumed_anonymous = resume_record(root, project)
    disabled = disabled_volume_provenance(root, trusted_root)
    expected_names = set(selected) | {
        f"{project}_{key}" for key in trusted_plain_volume_keys(trusted_root)
    } | {
        f"{project}_{key}" for key in trusted_plain_volume_keys(root, disabled_only=True)
    }
    all_names = set(docker(root, "volume", "ls", "--quiet").split())
    unlabelled_expected = (all_names & expected_names) - set(volumes) - set(external)
    if unlabelled_expected:
        name = sorted(unlabelled_expected)[0]
        raise ValueError(f"Volume {name} matches an ODS recipe but lacks Compose ownership labels")
    def proven_earlier(name: str, row: dict) -> bool:
        # Unmounted now, but identical to what an interrupted uninstall proved.
        return name not in mounted and resumed.get(name) == fingerprint(row)

    if volumes and not container_ids and not all(
            name in external or proven_earlier(name, row) for name, row in volumes.items()):
        raise ValueError(
            "ODS project volumes remain but no container proves the installation path; "
            "an older 'ods stop' or an interrupted uninstall may have removed that proof. "
            "Rerun with --keep-data to remove the installation and keep these volumes, then "
            "review them with 'docker volume ls' before removing them yourself"
        )
    owned = {}
    anonymous = {}
    trusted_used = set()
    # Read only when a retired volume exists: without one the record is moot.
    retired = retired_volume_keys(root) if any(
        row["Labels"][VOLUME_LABEL] in RETIRED_VOLUME_KEYS for row in volumes.values()
    ) else set()
    for name, row in volumes.items():
        key = row["Labels"][VOLUME_LABEL]
        if name in external:
            if key != external[name]:
                raise ValueError(f"External volume {name} has a conflicting Compose label")
            continue
        if name in selected and key != selected[name]:
            raise ValueError(f"Selected volume {name} has a conflicting Compose label")
        if proven_earlier(name, row):
            owned[name] = fingerprint(row)
            continue
        if name in selected and name not in mounted:
            raise ValueError(
                f"Selected volume {name} has no verified ODS container mount; an interrupted "
                "uninstall may have removed that proof. Rerun with --keep-data to keep the "
                "volumes and remove the rest of the installation"
            )
        disabled_owned = (
            name in mounted and key in disabled and
            name == f"{project}_{key}" and
            bool(mount_provenance.get(name, set()) & disabled[key])
        )
        retired_owned = key in retired and name == f"{project}_{key}"
        if name in selected or disabled_owned or retired_owned:
            owned[name] = fingerprint(row)
            if name not in selected and disabled_owned:
                trusted_used.add(key)
        elif key in RETIRED_VOLUME_KEYS and name == f"{project}_{key}":
            raise ValueError(
                f"Volume {name} is a retired Lemonade volume this installation did not record; "
                "purge refused. Remove it yourself with 'docker volume rm' if it is yours, "
                "or rerun with --keep-data"
            )
        else:
            raise ValueError(f"Volume {name} is not linked to this installation; purge refused")
    other_mounts = mounted - set(volumes) - set(external)
    for name, row in inspect_volumes(root, other_mounts).items():
        labels = row.get("Labels") or {}
        if (not CONTAINER_RE.fullmatch(name) or
                labels != {"com.docker.volume.anonymous": ""} or
                row.get("Driver") != "local"):
            raise ValueError(f"Mounted volume {name} has unproven ownership; purge refused")
        anonymous[name] = fingerprint(row)
    # Anonymous volumes from the interrupted attempt lost their mount with the
    # removed containers; accept only an unchanged identity.
    leftover = (set(resumed_anonymous) & all_names) - set(anonymous)
    for name, row in inspect_volumes(root, leftover).items():
        if fingerprint(row) != resumed_anonymous[name]:
            raise ValueError(f"Anonymous volume {name} changed since the interrupted uninstall; purge refused")
        anonymous[name] = fingerprint(row)
    check_volume_consumers(root, set(owned) | set(anonymous), container_ids)
    record = {
        "schemaVersion": 1,
        "installDir": str(root),
        "trustedSource": str(trusted_root),
        "project": project,
        "volumes": owned,
        "anonymous": anonymous,
        "external": external,
        "selected": selected,
        "trustedUsed": sorted(trusted_used),
        "expectedNames": sorted(expected_names),
        "flags": flags,
    }
    snapshot.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
    write_resume_record(root, record)


def postflight_containers(root: Path, snapshot: Path,
                          trusted_root: Path | None = None) -> None:
    """Require every container from this exact installation to be gone."""
    trusted_root = trusted_root or root
    record = json.loads(snapshot.read_text(encoding="utf-8"))
    if (not isinstance(record, dict) or record.get("schemaVersion") != 1 or
            record.get("installDir") != str(root) or
            record.get("trustedSource") != str(trusted_root) or
            not isinstance(record.get("project"), str) or
            not isinstance(record.get("flags"), list) or
            any(not isinstance(flag, str) for flag in record["flags"])):
        raise ValueError("Uninstall container snapshot is invalid")
    project, _, _ = project_config(root, record["flags"])
    if project != record["project"]:
        raise ValueError("Compose project changed during uninstall; installation retained")
    remaining, _, _ = project_containers(root, project)
    remaining |= install_root_containers(root)
    if remaining:
        ids = ", ".join(sorted(remaining)[:3])
        raise ValueError(f"ODS containers remain after Compose cleanup: {ids}")


def complete(root: Path, snapshot: Path, trusted_root: Path | None = None) -> None:
    trusted_root = trusted_root or root
    record = json.loads(snapshot.read_text(encoding="utf-8"))
    if (not isinstance(record, dict) or record.get("schemaVersion") != 1 or
            record.get("installDir") != str(root) or
            record.get("trustedSource") != str(trusted_root) or
            not isinstance(record.get("project"), str) or
            not isinstance(record.get("volumes"), dict)):
        raise ValueError("Uninstall volume snapshot is invalid")
    project = record["project"]
    before = record["volumes"]
    expected_names = record.get("expectedNames")
    if (not isinstance(expected_names, list) or
            any(not isinstance(name, str) or not VOLUME_RE.fullmatch(name)
                for name in expected_names)):
        raise ValueError("Uninstall volume snapshot is invalid")
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
            not set(record.get("trustedUsed", [])).issubset(
                disabled_volume_keys(root, trusted_root))):
        raise ValueError("Compose ownership changed during uninstall; installation retained")
    current_expected = set(current_selected) | {
        f"{project}_{key}" for key in trusted_plain_volume_keys(trusted_root)
    } | {
        f"{project}_{key}" for key in trusted_plain_volume_keys(root, disabled_only=True)
    }
    if current_expected != set(expected_names):
        raise ValueError("ODS recipe volume inventory changed during uninstall; installation retained")
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
    if (all_names & set(expected_names)) - set(remaining) - set(external):
        raise ValueError("Unlabelled ODS-like volume appeared during uninstall; installation retained")
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
    final_names = set(docker(root, "volume", "ls", "--quiet").split())
    if set(anonymous) & final_names:
        raise ValueError("Anonymous ODS volumes remain after cleanup; installation retained")
    if (set(expected_names) & final_names) - set(external):
        raise ValueError("ODS recipe volumes remain after cleanup; installation retained")
    unverified = sorted(
        name for name in final_names
        if name not in external and
        (name.startswith(f"{project}_") or name.startswith(f"{project}-"))
    )
    if unverified:
        print("ODS volume custody: retained unverified same-prefix volumes: "
              + ", ".join(unverified[:20])
              + (" (additional names omitted)" if len(unverified) > 20 else ""),
              file=sys.stderr)


def main() -> int:
    if len(sys.argv) < 5 or sys.argv[1] not in ("preflight", "postflight-containers", "complete"):
        print("Usage: uninstall-compose-volumes.py preflight|postflight-containers|complete INSTALL_DIR SNAPSHOT TRUSTED_SOURCE [COMPOSE_FLAGS...]", file=sys.stderr)
        return 2
    mode, root_arg, snapshot_arg, trusted_arg, *flags = sys.argv[1:]
    try:
        root = Path(root_arg).resolve(strict=True)
        trusted_root = Path(trusted_arg).resolve(strict=True)
        snapshot = Path(snapshot_arg)
        if (not root.is_dir() or not trusted_root.is_dir() or
                not snapshot.is_file() or snapshot.is_symlink()):
            raise ValueError("Installation or volume snapshot is invalid")
        if mode == "preflight":
            keep_data = bool(flags and flags[0] == "--keep-data")
            preflight(root, snapshot, flags[1:] if keep_data else flags,
                      keep_data, trusted_root)
        elif flags:
            raise ValueError("Unexpected Compose arguments for completion")
        elif mode == "postflight-containers":
            postflight_containers(root, snapshot, trusted_root)
        else:
            complete(root, snapshot, trusted_root)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as error:
        print(f"ODS volume custody failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
