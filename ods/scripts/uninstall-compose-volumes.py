#!/usr/bin/env python3
"""Account for Compose-owned volumes before removing an ODS installation.

Compose only knows the currently selected files. This helper accounts for
selected and unselected-extension volumes before removing any of them.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
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


def unselected_volume_provenance(root: Path, trusted_root: Path) -> dict[str, set[tuple[str, str]]]:
    """Bind leftover mounts to installed recipes even when selection failed.

    A failed native activation can leave an enabled extension running while
    uninstall falls back to base Compose files. Its recipe path and service
    must still match the mounted container, just as for disabled extensions.
    """
    found: dict[str, set[tuple[str, str]]] = {}
    enabled_keys = trusted_plain_volume_keys(root)
    trusted_enabled_keys = trusted_plain_volume_keys(trusted_root)
    for directory in ("extensions/services", "extensions/library/services"):
        for suffix in ("*.yaml.disabled", "*.yml.disabled", "*.yaml", "*.yml"):
            for path in (root / directory).glob(f"*/compose{suffix}"):
                enabled_name = path.name.removesuffix(".disabled")
                enabled_path = path.with_name(enabled_name)
                candidate_path = trusted_root / path.relative_to(root).with_name(enabled_name)
                for key in trusted_plain_volume_keys(root, only_file=path):
                    if (not path.name.endswith(".disabled") and
                            (key not in enabled_keys or key not in trusted_enabled_keys)):
                        # An enabled override declaring this key external or
                        # custom-named must not be bypassed by the base recipe.
                        continue
                    if (trusted_root != root and
                            key not in trusted_plain_volume_keys(
                                trusted_root, only_file=candidate_path)):
                        continue
                    found.setdefault(key, set()).add(
                        (path.parent.name, str(enabled_path.resolve()))
                    )
    for key, owners in native_stack_volume_provenance(root, trusted_root).items():
        found.setdefault(key, set()).update(owners)
    return found


# install-macos.sh runs the native-Pixel stack with these ODS-shipped recipes
# in their .disabled form, including the shared edge transition-state volume.
# A native install that stops after starting the stack never adds these files
# to .compose-flags, so ownership must also use the containers' recipe paths.
NATIVE_STACK_RECIPES = (
    "installers/macos/dashboard-private-state.compose.yaml",
    "installers/macos/pixel-native.compose.yaml.disabled",
    "extensions/services/pixel-edge/compose.yaml.disabled",
)
SERVICE_DECLARATION_RE = re.compile(r"^  ([a-z0-9][a-z0-9_-]*):\s*$")


def compose_service_names(path: Path) -> set[str]:
    """Top-level service names declared in a plain Compose recipe."""
    names: set[str] = set()
    in_services = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line == "services:":
            in_services = True
            continue
        if in_services and line and not line[0].isspace():
            break
        match = SERVICE_DECLARATION_RE.fullmatch(line) if in_services else None
        if match:
            names.add(match.group(1))
    return names


def native_stack_volume_provenance(root: Path, trusted_root: Path) -> dict[str, set[tuple[str, str]]]:
    found: dict[str, set[tuple[str, str]]] = {}
    for relative in NATIVE_STACK_RECIPES:
        path = root / relative
        if path.is_symlink() or not path.is_file():
            continue
        services = compose_service_names(path)
        for key in trusted_plain_volume_keys(root, only_file=path):
            if (trusted_root != root and
                    key not in trusted_plain_volume_keys(trusted_root, only_file=trusted_root / relative)):
                continue
            found.setdefault(key, set()).update(
                (service, str(path.resolve())) for service in services
            )
    return found


def unselected_volume_keys(root: Path, trusted_root: Path) -> set[str]:
    return set(unselected_volume_provenance(root, trusted_root))


def private_dashboard_volumes(root: Path, trusted_root: Path, project: str,
                              volumes: dict[str, dict]) -> set[str]:
    """A failed first launch may have prepared volumes before any API mount.

    Only these two fixed stores have owner-private creation receipts plus a
    random token bound to the Docker daemon and installation. Never infer
    ownership from their names or Compose labels alone.
    """
    recipe = "installers/macos/dashboard-private-state.compose.yaml"
    trusted = trusted_plain_volume_keys(trusted_root, only_file=trusted_root / recipe)
    installed = trusted_plain_volume_keys(root, only_file=root / recipe)
    names = {f"{project}_dashboard-{kind}": kind for kind in ("pixel-images", "pixel-chat-results")}
    candidates = set(volumes) & set(names)
    if not candidates:
        return set()
    directory = root / "data/.dashboard-private-stores"
    if not directory.exists():
        return set()
    info = directory.lstat()
    if (directory.resolve() != directory or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid() or info.st_mode & 0o077):
        raise ValueError("Private dashboard migration custody is invalid; retain volumes")
    daemon = docker(root, "info", "--format", "{{.ID}}").strip()
    install = hashlib.sha256(str(root).encode()).hexdigest()
    proven = set()
    for name in candidates:
        kind = names[name]
        key = "dashboard-" + kind
        path = directory / (kind + ".json")
        if not path.exists():
            continue
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_size > 16384):
            raise ValueError("Private dashboard migration receipt is invalid; retain volumes")
        record = json.loads(path.read_text())
        if not isinstance(record, dict):
            raise ValueError("Private dashboard migration receipt is invalid; retain volumes")
        row = volumes[name]
        labels = row.get("Labels") or {}
        token = record.get("token", "")
        if (key in trusted & installed and isinstance(token, str) and re.fullmatch("[a-f0-9]{32}", token)
                and record.get("kind") == kind and record.get("volume") == name
                and record.get("install") == install and record.get("daemon") == daemon
                and row.get("Driver") == "local" and not row.get("Options")
                and labels.get(PROJECT_LABEL) == project and labels.get(VOLUME_LABEL) == key
                and labels.get("io.ods.private-store.token") == token
                and labels.get("io.ods.private-store.install") == install):
            proven.add(name)
    return proven


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


def native_custody_document(root: Path, selected: Path, limit: int, failure: str, *, private=True) -> bytes:
    owner = root.stat().st_uid
    # All ancestors within this install must retain owner custody. Never
    # follow a moved journal/recipe through a symlink, even inside root.
    for parent in (root, *(root / value for value in
                          reversed(selected.parent.relative_to(root).parents) if value != Path("."))):
        info = parent.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != owner or info.st_mode & 0o022):
            raise ValueError(failure)
    info = selected.parent.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != owner or info.st_mode & 0o022 or
            (private and stat.S_IMODE(info.st_mode) != 0o700) or selected.resolve() != selected):
        raise ValueError(failure)
    fd = os.open(selected, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != owner or
                before.st_nlink != 1 or (stat.S_IMODE(before.st_mode) != 0o600 if private else before.st_mode & 0o022)
                or before.st_size > limit):
            raise ValueError(failure)
        body = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    if (len(body) > limit or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) !=
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
        raise ValueError(failure)
    return body


def native_ingress_rollback_provenance(root: Path, project: str, row: dict, files: str) -> str:
    """Prove the terminal frozen rollback recipe without merging current config."""
    service = "pixel-native-ingress"
    labels = row["Config"]["Labels"]
    prefix = root / "data/pixel-native/preparation"
    path = Path(files)
    if (labels.get("com.docker.compose.service") != service or "," in files or
            path.parent.parent != prefix or path.name != "rollback.compose.json" or
            not re.fullmatch(r"ingress-refresh-[a-f0-9]{32}", path.parent.name)):
        raise ValueError("Compose project contains a container from another installation")
    failure = "Native ingress rollback ownership could not be proven"
    def private_document(selected: Path, limit: int) -> bytes:
        return native_custody_document(root, selected, limit, failure)

    journal = path.parent / "refresh.json"
    try:
        journal_body, body = private_document(journal, 65536), private_document(path, 4 * 1024 * 1024)
        record, recipe = json.loads(journal_body), json.loads(body)
        saved = record["rollback"]
        definition = recipe["services"][service]
        native = root / "installers/macos/pixel-native.compose.yaml.disabled"
        if (record.get("schemaVersion") != 1 or record.get("phase") != "rolled-back" or
                record.get("project") != project or
                record.get("source") != str(root / "data/pixel-native/home/.openclaw/openclaw.json") or
                saved["path"] != str(path) or saved["sha256"] != hashlib.sha256(body).hexdigest() or
                saved["image"] != row["Image"] or saved["user"] != row["Config"]["User"] or
                not CONTAINER_RE.fullmatch(saved["previousId"]) or
                not CONTAINER_RE.fullmatch(saved["configHash"]) or
                set(recipe) != {"name", "services", "volumes", "networks"} or
                recipe["name"] != project or set(recipe["services"]) != {service} or
                definition["image"] != row["Image"] or definition["user"] != saved["user"] or
                any(key in definition for key in ("build", "depends_on", "pull_policy")) or
                native.is_symlink() or not native.is_file() or service not in compose_service_names(native)):
            raise ValueError(failure)
        # The snapshot externalizes only existing named volumes. Preserve their
        # shipped logical-key provenance; never bless arbitrary external data.
        keys = trusted_plain_volume_keys(root, only_file=native)
        for key, value in recipe["volumes"].items():
            if key not in keys or value != {"name": f"{project}_{key}", "external": True}:
                raise ValueError(failure)
        mounts = row["Mounts"]
        declared = definition["volumes"]
        if not isinstance(mounts, list) or not isinstance(declared, list) or len(mounts) != len(declared):
            raise ValueError(failure)
        targets = set()
        for mount in declared:
            target = mount["target"]
            actual = [value for value in mounts if value.get("Destination") == target]
            if (target in targets or len(actual) != 1 or actual[0]["Type"] != mount["type"] or
                    actual[0]["RW"] is not (not mount.get("read_only", False))):
                raise ValueError(failure)
            targets.add(target)
            if mount["type"] == "volume":
                if actual[0]["Name"] != recipe["volumes"][mount["source"]]["name"]:
                    raise ValueError(failure)
            elif (mount["type"] != "bind" or
                  actual[0]["Source"] != mount["source"].replace("$$", "$")):
                raise ValueError(failure)
        expected = labels.get("com.docker.compose.config-hash", "")
        if not CONTAINER_RE.fullmatch(expected):
            raise ValueError(failure)
        # Hash only the recorded immutable definition with an empty env file.
        # Prepending today's base file would change the recovery definition.
        hashed = docker(root, "compose", "--project-directory", str(root), "--project-name", project,
                        "--env-file", "/dev/null", "-f", str(path), "config", "--hash", service).strip()
        if (hashed != service + " " + expected or private_document(journal, 65536) != journal_body or
                private_document(path, 4 * 1024 * 1024) != body):
            raise ValueError(failure)
    except (OSError, KeyError, TypeError, AttributeError, ValueError):
        # Neither the frozen environment nor journal admission credentials may
        # reach an uninstall log through parser or subprocess diagnostics.
        raise ValueError(failure) from None
    return str(native.resolve())


def native_lifecycle_provenance(root: Path, project: str, row: dict, files: str) -> list[str]:
    """Recover original recipe provenance from a completed public CLI operation.

    Restart/start/update run a frozen whole-stack recipe, not just ingress.
    Verify the container-bound historical journal; a later config refresh may
    legitimately replace the current pointer, environment or native config.
    """
    failure = "Native lifecycle ownership could not be proven"
    path = Path(files)
    if ("," in files or path.parent.parent != root / "data/pixel-native/preparation"
            or path.name != "lifecycle.compose.json"
            or not re.fullmatch(r"ingress-refresh-[a-f0-9]{32}", path.parent.name)):
        raise ValueError(failure)
    journal = path.parent / "refresh.json"
    owner = root.stat().st_uid
    checked = {}
    absent_aliases = []

    def read(selected, limit=16 * 1024 * 1024, *, private=False):
        value = native_custody_document(root, selected, limit, failure, private=private)
        checked[selected] = (value, limit, private)
        return value

    try:
        record = json.loads(read(journal, private=True))
        body = read(path, private=True)
        recipe = json.loads(body)
        saved = record["lifecycleStack"]
        authority = record["childAuthority"]
        source = authority["source"]
        action, selection = saved["action"], saved["service"]
        service = row["Config"]["Labels"]["com.docker.compose.service"]
        source_key = hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()
        if (record.get("schemaVersion") != 1 or record.get("phase") != "ready"
                or record.get("project") != project
                or record.get("source") != str(root / "data/pixel-native/home/.openclaw/openclaw.json")
                or action not in ("start", "restart", "update")
                or selection not in ("", "pixel-native-ingress") or action == "update" and selection
                or saved["phase"] != "selected" or saved["project"] != project
                or saved["installDir"] != str(root) or saved["path"] != str(path)
                or saved["sha256"] != hashlib.sha256(body).hexdigest()
                or record["child"].get("outcome") != "completed" or record["child"].get("exitCode") != 0
                or source["action"] != action or source["service"] != selection
                or authority["sourceKey"] != source_key
                or record["operation"] != {"kind": "cli-" + action, "key": source_key}
                or recipe["name"] != project or service not in saved["services"]
                or len(saved["services"]) != len(set(saved["services"]))
                or set(saved["images"]) != set(saved["services"])):
            raise ValueError(failure)
        # The completed child attests exactly these installed CLI sources. Do
        # not execute an installed helper or accept an arbitrary journal file map.
        library = root / "installers/macos/lib"
        sources = {root / "installers/macos/ods-macos.sh", *library.glob("*.py"), *library.glob("*.sh"),
                   *(root / "lib").glob("*.sh"), *(root / "scripts").glob("*.sh"),
                   *(root / "scripts").glob("*.py")}
        if set(source["files"]) != {str(value) for value in sources}:
            raise ValueError(failure)
        for selected in sources:
            if hashlib.sha256(read(selected)).hexdigest() != source["files"][str(selected)]:
                raise ValueError(failure)
        if (not os.path.isabs(source["bash"]) or not CONTAINER_RE.fullmatch(source["bashSHA256"])):
            raise ValueError(failure)
        inputs = saved["inputs"]["files"]
        if (str(root / ".env") not in inputs or str(root / ".compose-flags") not in inputs
                or not CONTAINER_RE.fullmatch(saved["inputs"]["definitionSHA256"])):
            raise ValueError(failure)
        original_files = []
        for name, identity in inputs.items():
            selected = Path(name)
            if not selected.is_absolute() or not selected.is_relative_to(root) or selected != selected.resolve():
                raise ValueError(failure)
            relative = selected.relative_to(root).as_posix()
            extension_recipe = re.fullmatch(
                r"extensions/(?:library/)?services/[a-zA-Z0-9_-]+/compose(?:\.[a-zA-Z0-9_-]+)?\.ya?ml(?:\.disabled)?", relative)
            if extension_recipe:
                alias = selected.with_name(selected.name.removesuffix(".disabled")
                    if selected.name.endswith(".disabled") else selected.name + ".disabled")
                if os.path.lexists(alias):
                    if os.path.lexists(selected):
                        raise ValueError(failure)
                    # Public enable/disable renames exactly this recipe. Its
                    # bytes and full recorded identity still have to match.
                    absent_aliases.append(selected)
                    selected = alias
            if identity is None and selected == root / ".compose-flags":
                continue
            if (set(identity) != {"sha256", "uid", "gid", "mode"}
                    or not CONTAINER_RE.fullmatch(identity["sha256"]) or type(identity["uid"]) is not int or identity["uid"] != owner
                    or type(identity["gid"]) is not int or type(identity["mode"]) is not int
                    or not 0 <= identity["mode"] <= 0o777 or identity["mode"] & 0o022):
                raise ValueError(failure)
            # These are historical interpolation/selection inputs, not today's
            # authority. Never expand the frozen definition with today's .env.
            if selected in (root / ".env", root / ".compose-flags"):
                continue
            value = read(selected)
            info = selected.stat()
            if identity != {"sha256": hashlib.sha256(value).hexdigest(), "uid": info.st_uid,
                            "gid": info.st_gid, "mode": stat.S_IMODE(info.st_mode)}:
                raise ValueError(failure)
            if (re.fullmatch(r"docker-compose(?:\.[a-zA-Z0-9_-]+)?\.yml", relative)
                    or relative in NATIVE_STACK_RECIPES
                    or re.fullmatch(r"installers/macos/docker-compose\.[a-zA-Z0-9_-]+\.yml", relative)
                    or extension_recipe):
                if extension_recipe and selected.relative_to(root).as_posix() in NATIVE_STACK_RECIPES:
                    original_files.append(str(selected))
                original_files.append(str(selected.with_name(selected.name.removesuffix(".disabled")))
                    if extension_recipe else str(selected))
        if not any(str(root / name) in original_files for name in ("docker-compose.base.yml", "docker-compose.yml")):
            raise ValueError(failure)
        definition = recipe["services"][service]
        image = saved["images"][service]
        if (not re.fullmatch(r"sha256:[a-f0-9]{64}", image)
                or definition["image"] != image or row["Image"] != image):
            raise ValueError(failure)
        expected_user = definition.get("user")
        if expected_user is None:
            expected_user = json.loads(docker(root, "image", "inspect", "--format", "{{json .Config.User}}", image))
        if row["Config"].get("User", "") != (expected_user or ""):
            raise ValueError(failure)
        mounts, declared = row.get("Mounts", []), definition.get("volumes", [])
        if not isinstance(mounts, list) or not isinstance(declared, list) or len(mounts) != len(declared):
            raise ValueError(failure)
        targets = set()
        for mount in declared:
            target = mount["target"]
            actual = [value for value in mounts if value.get("Destination") == target]
            if (target in targets or len(actual) != 1 or actual[0]["Type"] != mount["type"]
                    or actual[0]["RW"] is not (not mount.get("read_only", False))):
                raise ValueError(failure)
            targets.add(target)
            if mount["type"] == "volume":
                if actual[0]["Name"] != recipe["volumes"][mount["source"]]["name"]:
                    raise ValueError(failure)
            elif mount["type"] != "bind" or actual[0]["Source"] != mount["source"].replace("$$", "$"):
                raise ValueError(failure)
        expected = row["Config"]["Labels"].get("com.docker.compose.config-hash", "")
        if not CONTAINER_RE.fullmatch(expected):
            raise ValueError(failure)
        hashed = docker(root, "compose", "--project-directory", str(root), "--project-name", project,
                        "--env-file", "/dev/null", "-f", str(path), "config", "--hash", service).strip()
        if hashed != service + " " + expected:
            raise ValueError(failure)
        for selected, (value, limit, private) in checked.items():
            if native_custody_document(root, selected, limit, failure, private=private) != value:
                raise ValueError(failure)
        if any(os.path.lexists(selected) for selected in absent_aliases):
            raise ValueError(failure)
    except (OSError, KeyError, TypeError, AttributeError, ValueError):
        raise ValueError(failure) from None
    return original_files


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
                    not isinstance(service, str) or
                    not PROJECT_RE.fullmatch(service)):
                raise ValueError("Compose project contains a container from another installation")
            provenance_files = files.split(",")
            if os.path.realpath(provenance_files[0]) not in base_files:
                provenance_files = (native_lifecycle_provenance(root, project, row, files)
                    if Path(files).name == "lifecycle.compose.json" else
                    [native_ingress_rollback_provenance(root, project, row, files)])
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
                    for config_file in provenance_files:
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


def native_config_volume_provenance(root: Path, project: str, volumes: dict[str, dict]) -> dict[str, dict]:
    """Consume only completed producer custody, including after an old-bind rollback."""
    path = root / "data/pixel-native/preparation/ingress-config-volume.json"
    if not os.path.lexists(path):
        return {}
    failure = "Native config volume creation requires review; purge refused"
    owner = root.stat().st_uid
    for parent in (root, root / "data", root / "data/pixel-native", path.parent):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != owner or info.st_mode & 0o022:
            raise ValueError(failure)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != owner or
                stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1 or before.st_size > 16384):
            raise ValueError(failure)
        body = stream.read(16385)
        after = os.fstat(stream.fileno())
    if (len(body) > 16384 or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
        raise ValueError(failure)
    record = json.loads(body)
    key = "pixel-native-config"
    name = f"{project}_{key}"
    if (not isinstance(record, dict) or set(record) !=
            {"schemaVersion", "installDir", "project", "key", "state", "nonce", "preexisting", "fingerprint"}
            or record["schemaVersion"] != 1 or record["installDir"] != str(root)
            or record["project"] != project or record["key"] != key or record["state"] != "ready"
            or record["preexisting"] is not False or not isinstance(record["nonce"], str)
            or not re.fullmatch("[a-f0-9]{32}", record["nonce"])):
        raise ValueError(failure)
    row = volumes.get(name)
    if row is None:
        # Completed uninstall can have removed it before an interruption. Never
        # reinterpret an absent/replaced volume as fresh producer custody.
        return {}
    if (row.get("Driver") != "local" or row.get("Scope") != "local" or row.get("Options") not in (None, {})
            or (row.get("Labels") or {}).get("org.osmantic.ods.native-config-creation") != record["nonce"]
            or record["fingerprint"] != fingerprint(row)):
        raise ValueError(failure)
    return {name: fingerprint(row)}


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
    private_prepared = private_dashboard_volumes(root, trusted_root, project, volumes)
    produced = native_config_volume_provenance(root, project, volumes)
    resumed, resumed_anonymous = resume_record(root, project)
    unselected = unselected_volume_provenance(root, trusted_root)
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
        return name not in mounted and (resumed.get(name) == fingerprint(row)
            or produced.get(name) == fingerprint(row))

    if volumes and not container_ids and not all(
            name in external or name in private_prepared or proven_earlier(name, row) for name, row in volumes.items()):
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
        if name in selected and name not in mounted and name not in private_prepared:
            raise ValueError(
                f"Selected volume {name} has no verified ODS container mount; an interrupted "
                "uninstall may have removed that proof. Rerun with --keep-data to keep the "
                "volumes and remove the rest of the installation"
            )
        unselected_owned = (
            name in mounted and key in unselected and
            name == f"{project}_{key}" and
            bool(mount_provenance.get(name, set()) & unselected[key])
        )
        retired_owned = key in retired and name == f"{project}_{key}"
        if name in selected or unselected_owned or retired_owned or name in private_prepared:
            owned[name] = fingerprint(row)
            if name not in selected and unselected_owned:
                trusted_used.add(key)
            if name in private_prepared:
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
                unselected_volume_keys(root, trusted_root))):
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
