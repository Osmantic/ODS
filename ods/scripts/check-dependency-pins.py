#!/usr/bin/env python3
"""Validate pinned runtime dependency references.

This is intentionally narrow: it tracks runtime container images shipped by
ODS Compose files and extension Dockerfiles. The goal is to make image
drift explicit in review instead of discovering it during an install.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "config" / "dependency-lock.json"

IMAGE_RE = re.compile(r"^\s*image:\s*(?P<value>\S+)")
ARG_RE = re.compile(r"^\s*ARG\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?:=(?P<value>\S+))?")
VAR_RE = re.compile(
    r"\$\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)(?:(?P<op>:-|-)(?P<default>[^}]+))?\}"
    r"|\$(?P<plain>[A-Za-z_][A-Za-z0-9_]*)"
)
EPHEMERAL_SHA_TAG_RE = re.compile(r"^sha-[0-9a-f]{7,64}$", re.IGNORECASE)


@dataclass(frozen=True)
class ImageRef:
    path: str
    line: int
    raw: str
    value: str
    source: str


def _strip_inline_comment(line: str) -> str:
    in_single = False
    in_double = False
    for idx, char in enumerate(line):
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == "#" and not in_single and not in_double:
            return line[:idx]
    return line


def _clean_value(value: str) -> str:
    value = value.strip().strip(",")
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        value = value[1:-1]
    return value.strip()


def _rel(path: Path, root: Path = ROOT) -> str:
    return path.relative_to(root).as_posix()


def _resolve_vars(value: str, defaults: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group("braced") or match.group("plain") or ""
        default = match.group("default")
        if default is not None:
            return default
        if name in defaults:
            return defaults[name]
        return match.group(0)

    return VAR_RE.sub(replace, value)


def _compose_image_refs(path: Path, root: Path = ROOT) -> list[ImageRef]:
    refs: list[ImageRef] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        match = IMAGE_RE.match(_strip_inline_comment(line))
        if not match:
            continue
        raw = _clean_value(match.group("value"))
        refs.append(
            ImageRef(
                path=_rel(path, root),
                line=line_no,
                raw=raw,
                value=_resolve_vars(raw, {}),
                source="compose image",
            )
        )
    return refs


def _dockerfile_from_value(line: str) -> str | None:
    line = _strip_inline_comment(line).strip()
    if not line.upper().startswith("FROM "):
        return None
    tokens = line.split()[1:]
    while tokens and tokens[0].startswith("--"):
        tokens.pop(0)
    if not tokens:
        return None
    return _clean_value(tokens[0])


def _dockerfile_image_refs(path: Path, root: Path = ROOT) -> list[ImageRef]:
    refs: list[ImageRef] = []
    defaults: dict[str, str] = {}
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        arg_match = ARG_RE.match(_strip_inline_comment(line))
        if arg_match and arg_match.group("value") is not None:
            defaults[arg_match.group("name")] = _clean_value(arg_match.group("value"))

        raw = _dockerfile_from_value(line)
        if raw is None:
            continue
        refs.append(
            ImageRef(
                path=_rel(path, root),
                line=line_no,
                raw=raw,
                value=_resolve_vars(raw, defaults),
                source="dockerfile from",
            )
        )
    return refs


def _image_ref_files(bases: Iterable[Path]) -> set[Path]:
    files: set[Path] = set()
    for base in bases:
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            name = path.name
            if name.startswith("Dockerfile") or name.startswith("compose.") or name.startswith(
                "docker-compose"
            ):
                files.add(path)
    return files


def candidate_files(root: Path = ROOT) -> list[Path]:
    files: set[Path] = set(root.glob("docker-compose*.yml"))
    files.update(root.glob("docker-compose*.yaml"))
    files.update(_image_ref_files((root / "installers", root / "extensions" / "services")))
    return sorted(files)


def extension_library_candidate_files(root: Path = ROOT) -> list[Path]:
    files = _image_ref_files((root / "extensions" / "library" / "services",))
    return sorted(files)


def _image_refs_from_files(files: Iterable[Path], root: Path) -> list[ImageRef]:
    refs: list[ImageRef] = []
    for path in files:
        if path.name.startswith("Dockerfile"):
            refs.extend(_dockerfile_image_refs(path, root))
        else:
            refs.extend(_compose_image_refs(path, root))
    return refs


def discover_image_refs(root: Path = ROOT) -> list[ImageRef]:
    return _image_refs_from_files(candidate_files(root), root)


def discover_extension_library_image_refs(root: Path = ROOT) -> list[ImageRef]:
    return _image_refs_from_files(extension_library_candidate_files(root), root)


def _key(item: dict[str, object]) -> tuple[str, str]:
    return str(item.get("path", "")), str(item.get("value", ""))


def _image_tag(value: str) -> str | None:
    without_digest = value.split("@", 1)[0]
    last_segment = without_digest.rsplit("/", 1)[-1]
    if ":" not in last_segment:
        return None
    return last_segment.rsplit(":", 1)[1]


def _has_digest(value: str) -> bool:
    return "@sha256:" in value


def _has_tag_or_digest(value: str) -> bool:
    return _has_digest(value) or _image_tag(value) is not None


def _is_variable_ref(value: str) -> bool:
    return "$" in value


def _is_ephemeral_sha_tag(value: str) -> bool:
    tag = _image_tag(value)
    return tag is not None and EPHEMERAL_SHA_TAG_RE.match(tag) is not None


def _ephemeral_sha_tag_error(ref: ImageRef) -> str:
    location = f"{ref.path}:{ref.line}"
    return (
        f"{location}: ephemeral sha-* image tags are not release-stable; "
        f"pin to a retained version tag or @sha256 digest: {ref.value}"
    )


def _arg_default_present(path: Path, arg: str, default: str) -> bool:
    expected = f"ARG {arg}={default}"
    return expected in path.read_text(encoding="utf-8")


def _validate_lock_shape(lock: dict[str, object], root: Path) -> list[str]:
    errors: list[str] = []
    if lock.get("version") != 1:
        errors.append("dependency-lock.json version must be 1")

    ids: set[str] = set()
    entry_keys: set[tuple[str, str]] = set()
    for entry in lock.get("entries", []):
        if not isinstance(entry, dict):
            errors.append("lock entries must be objects")
            continue
        entry_id = str(entry.get("id", ""))
        path = str(entry.get("path", ""))
        value = str(entry.get("value", ""))
        raw = str(entry.get("raw", value))
        if not entry_id or not path or not value:
            errors.append(f"lock entry is missing id/path/value: {entry!r}")
            continue
        if entry_id in ids:
            errors.append(f"duplicate lock entry id: {entry_id}")
        ids.add(entry_id)
        if (path, value) in entry_keys:
            errors.append(f"duplicate lock entry path/value: {path} -> {value}")
        entry_keys.add((path, value))
        file_path = root / path
        if not file_path.exists():
            errors.append(f"lock entry points at missing file: {path}")
            continue
        text = file_path.read_text(encoding="utf-8")
        if raw not in text and value not in text:
            errors.append(f"lock entry value is not present in {path}: {value}")

    for list_name in ("allow_latest", "allow_local_images", "allow_variable_refs"):
        seen: set[tuple[str, str]] = set()
        for item in lock.get(list_name, []):
            if not isinstance(item, dict):
                errors.append(f"{list_name} entries must be objects")
                continue
            path, value = _key(item)
            if not path or not value:
                errors.append(f"{list_name} entry is missing path/value: {item!r}")
                continue
            if (path, value) in seen:
                errors.append(f"duplicate {list_name} entry: {path} -> {value}")
            seen.add((path, value))
            file_path = root / path
            if not file_path.exists():
                errors.append(f"{list_name} entry points at missing file: {path}")
                continue
            if value not in file_path.read_text(encoding="utf-8"):
                errors.append(f"{list_name} value is not present in {path}: {value}")
            arg = item.get("arg")
            default = item.get("default")
            if isinstance(arg, str) and isinstance(default, str):
                if not _arg_default_present(file_path, arg, default):
                    errors.append(
                        f"{list_name} entry for {path} expects ARG {arg}={default}"
                    )

    return errors


def validate_refs(refs: Iterable[ImageRef], lock: dict[str, object], root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    entry_keys = {_key(entry) for entry in lock.get("entries", []) if isinstance(entry, dict)}
    latest_allow = {
        _key(entry) for entry in lock.get("allow_latest", []) if isinstance(entry, dict)
    }
    local_allow = {
        _key(entry) for entry in lock.get("allow_local_images", []) if isinstance(entry, dict)
    }
    variable_allow = {
        _key(entry) for entry in lock.get("allow_variable_refs", []) if isinstance(entry, dict)
    }

    discovered_keys: set[tuple[str, str]] = set()
    for ref in refs:
        discovered_keys.add((ref.path, ref.value))
        location = f"{ref.path}:{ref.line}"
        if _is_variable_ref(ref.raw):
            if (ref.path, ref.raw) not in variable_allow:
                errors.append(f"{location}: variable image ref is not documented: {ref.raw}")
            if _is_variable_ref(ref.value):
                errors.append(f"{location}: variable image ref does not resolve to a default: {ref.raw}")
                continue

        if (ref.path, ref.value) in local_allow:
            continue

        if not _has_tag_or_digest(ref.value):
            errors.append(f"{location}: image ref must include a tag or digest: {ref.value}")

        if _image_tag(ref.value) == "latest" and not _has_digest(ref.value):
            if (ref.path, ref.value) not in latest_allow:
                errors.append(f"{location}: latest tag requires allow_latest: {ref.value}")

        if _is_ephemeral_sha_tag(ref.value) and not _has_digest(ref.value):
            errors.append(_ephemeral_sha_tag_error(ref))

        if (ref.path, ref.value) not in entry_keys and (ref.path, ref.value) not in latest_allow:
            errors.append(f"{location}: image ref is not recorded in dependency-lock.json: {ref.value}")

    for path, value in entry_keys:
        if (path, value) not in discovered_keys:
            errors.append(f"lock entry is stale or undiscovered: {path} -> {value}")

    return errors


def validate_ephemeral_sha_tags(refs: Iterable[ImageRef]) -> list[str]:
    errors: list[str] = []
    for ref in refs:
        if _is_ephemeral_sha_tag(ref.value) and not _has_digest(ref.value):
            errors.append(_ephemeral_sha_tag_error(ref))
    return errors


def load_lock(path: Path = LOCK_PATH) -> dict[str, object]:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("dependency-lock.json must contain a JSON object")
    return data


def check(path: Path = LOCK_PATH, root: Path = ROOT) -> list[str]:
    lock = load_lock(path)
    errors = _validate_lock_shape(lock, root)
    errors.extend(validate_refs(discover_image_refs(root), lock, root))
    errors.extend(validate_ephemeral_sha_tags(discover_extension_library_image_refs(root)))
    return errors


def main() -> int:
    errors = check()
    if errors:
        for error in errors:
            print(f"[FAIL] {error}", file=sys.stderr)
        return 1
    print("[PASS] dependency pins are documented and enforceable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
