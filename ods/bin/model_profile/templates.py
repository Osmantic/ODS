"""Chat template overrides (PLAN WP5): ODS's curated index of fixed templates.

Layout of ``config/chat-templates/`` in the repository and in an installation::

    index.json                    {"schemaVersion": 1, "overrides": [entry, ...]}
    upstream-b<build>/LICENSE     llama.cpp's MIT license at that release tag
    upstream-b<build>/SHA256SUMS  "<sha256>  <name>.jinja" per vendored template

One entry::

    {"id": "short-name",
     "embeddedTemplateSha256": "<64 hex>",  # the model's own template as llama.cpp
                                             # /props reports it (probes.static_facts
                                             # templateSha256)
     "file": "upstream-b9014/<name>.jinja",
     "fileSha256": "<64 hex>",
     "builds": ["b9014"],                    # llama.cpp builds the fix is qualified on
     "reason": "short text"}

An override applies to a model only on an exact match: the SHA-256 that
llama.cpp reports for the model's own template is ``embeddedTemplateSha256``
and the running build is one of ``builds``. Only templates an entry uses are
vendored. Nothing here reads the network: the files ship with ODS, are
reviewed like code (scripts/validate-chat-templates.py runs in CI) and are
checked against their SHA-256 again before a runtime loads one.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA_VERSION = 1
INDEX_NAME = "index.json"
MANIFEST_NAME = "SHA256SUMS"
LICENSE_NAME = "LICENSE"
ENTRY_FIELDS = ("id", "embeddedTemplateSha256", "file", "fileSha256", "builds", "reason")
REASON_LIMIT = 200

ID_PATTERN = r"[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?"
_ID = re.compile(ID_PATTERN)
_HEX64 = re.compile(r"[0-9a-f]{64}")
_BUILD = re.compile(r"b[0-9]+")
_BUILD_INFO = re.compile(r"(b[0-9]+)(?:-|$)")
_VENDOR_DIR = re.compile(r"upstream-b[0-9]+")
_TEMPLATE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\.jinja")
_MANIFEST_LINE = re.compile(r"([0-9a-f]{64})  (\S+)")


class TemplateIndexError(ValueError):
    """The index, a manifest or a vendored template is malformed or has changed."""


def root_for(install_dir: Path | str) -> Path:
    """``config/chat-templates`` of an installation or of the repository's ods/ tree."""
    return Path(install_dir) / "config" / "chat-templates"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def valid_template_name(name: str) -> bool:
    return isinstance(name, str) and bool(_TEMPLATE_NAME.fullmatch(name))


def valid_build(tag: str) -> bool:
    return isinstance(tag, str) and bool(_BUILD.fullmatch(tag))


def parse_manifest(text: str) -> dict[str, str]:
    """``{template name: sha256}`` from SHA256SUMS text; empty text is an empty manifest."""
    entries: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        found = _MANIFEST_LINE.fullmatch(line)
        if not found or not valid_template_name(found.group(2)):
            raise TemplateIndexError(f"{MANIFEST_NAME} line {number} is not '<sha256>  <name>.jinja'")
        if found.group(2) in entries:
            raise TemplateIndexError(f"{MANIFEST_NAME} lists {found.group(2)} twice")
        entries[found.group(2)] = found.group(1)
    return entries


def render_manifest(entries: dict[str, str]) -> str:
    """SHA256SUMS text (``sha256sum -c`` format), sorted by name."""
    return "".join(f"{digest}  {name}\n" for name, digest in sorted(entries.items()))


def _file_errors(where: str, value: Any) -> list[str]:
    parts = value.split("/") if isinstance(value, str) else []
    if (len(parts) != 2 or not _VENDOR_DIR.fullmatch(parts[0]) or not valid_template_name(parts[1])):
        return [f"{where}.file must be upstream-b<build>/<name>.jinja inside chat-templates"]
    return []


def _entry_errors(index: int, entry: Any) -> list[str]:
    where = f"overrides[{index}]"
    if not isinstance(entry, dict):
        return [f"{where} is not an object"]
    if set(entry) != set(ENTRY_FIELDS):
        return [f"{where} must have exactly the fields {', '.join(ENTRY_FIELDS)}"]
    errors = []
    if not isinstance(entry["id"], str) or not _ID.fullmatch(entry["id"]):
        errors.append(f"{where}.id must be lower-case letters, digits, dots and hyphens")
    for field in ("embeddedTemplateSha256", "fileSha256"):
        if not isinstance(entry[field], str) or not _HEX64.fullmatch(entry[field]):
            errors.append(f"{where}.{field} must be 64 lower-case hex characters")
    errors.extend(_file_errors(where, entry["file"]))
    builds = entry["builds"]
    if (not isinstance(builds, list) or not builds or len(set(map(str, builds))) != len(builds)
            or not all(valid_build(build) for build in builds)):
        errors.append(f"{where}.builds must list distinct llama.cpp builds such as b9014")
    reason = entry["reason"]
    if not isinstance(reason, str) or not reason.strip() or len(reason) > REASON_LIMIT or "\n" in reason:
        errors.append(f"{where}.reason must be one line of at most {REASON_LIMIT} characters")
    return errors


def index_document_errors(document: Any) -> list[str]:
    """What is wrong with a parsed index.json; empty when it is valid."""
    if not isinstance(document, dict) or set(document) != {"schemaVersion", "overrides"}:
        return [f"{INDEX_NAME} must be an object with exactly schemaVersion and overrides"]
    if document["schemaVersion"] != SCHEMA_VERSION:
        return [f"{INDEX_NAME} schemaVersion must be {SCHEMA_VERSION}"]
    overrides = document["overrides"]
    if not isinstance(overrides, list):
        return [f"{INDEX_NAME} overrides must be a list"]
    errors: list[str] = []
    for index, entry in enumerate(overrides):
        errors.extend(_entry_errors(index, entry))
    if errors:
        return errors
    ids = [entry["id"] for entry in overrides]
    for duplicate in sorted({value for value in ids if ids.count(value) > 1}):
        errors.append(f"override id {duplicate} is used twice")
    claimed: dict[tuple[str, str], str] = {}
    for entry in overrides:
        for build in entry["builds"]:
            pair = (entry["embeddedTemplateSha256"], build)
            if pair in claimed:
                errors.append(f"{entry['id']} and {claimed[pair]} both fix the same template on {build}")
            claimed[pair] = entry["id"]
    return errors


def _read_index_document(root: Path | str) -> Any:
    text = (Path(root) / INDEX_NAME).read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except ValueError as exc:
        raise TemplateIndexError(f"{INDEX_NAME} is not JSON: {exc}") from exc


def load_index(root: Path | str) -> list[dict]:
    """The override entries of ``root``/index.json.

    Raises OSError when the file cannot be read and TemplateIndexError when it
    is malformed. Vendored files are checked only when one is used
    (``template_path``); CI checks the whole tree (``tree_errors``).
    """
    document = _read_index_document(root)
    errors = index_document_errors(document)
    if errors:
        raise TemplateIndexError("; ".join(errors))
    return document["overrides"]


def build_tag(build_info: Any) -> str | None:
    """``b9014`` from llama.cpp's ``build_info`` (``b9014-ad4b5c5f``), else None."""
    found = _BUILD_INFO.match(build_info) if isinstance(build_info, str) else None
    return found.group(1) if found else None


def match(entries: list[dict], embedded_sha256: Any, build_info: Any) -> dict | None:
    """The override for exactly this embedded template on this build, if the index has one."""
    build = build_tag(build_info)
    if build is None or not isinstance(embedded_sha256, str):
        return None
    for entry in entries:
        if entry["embeddedTemplateSha256"] == embedded_sha256 and build in entry["builds"]:
            return entry
    return None


def entry_by_id(entries: list[dict], override_id: Any) -> dict | None:
    return next((entry for entry in entries if entry["id"] == override_id), None)


def template_path(root: Path | str, entry: dict) -> Path:
    """The entry's vendored file, once it is proven to have the recorded SHA-256."""
    path = Path(root).joinpath(*PurePosixPath(entry["file"]).parts)
    if path.is_symlink() or not path.is_file():
        raise TemplateIndexError(f"{entry['file']} is missing from this installation")
    if sha256_file(path) != entry["fileSha256"]:
        raise TemplateIndexError(f"{entry['file']} does not match its recorded SHA-256")
    return path


def _vendor_dir_errors(directory: Path, manifests: dict[str, dict[str, str]]) -> list[str]:
    errors: list[str] = []
    name = directory.name
    license_path = directory / LICENSE_NAME
    if license_path.is_symlink() or not license_path.is_file():
        errors.append(f"{name}/{LICENSE_NAME} is missing: vendored llama.cpp files keep its MIT license")
    elif b"MIT License" not in license_path.read_bytes():
        errors.append(f"{name}/{LICENSE_NAME} is not llama.cpp's MIT license")
    listed: dict[str, str] = {}
    manifest_path = directory / MANIFEST_NAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        errors.append(f"{name}/{MANIFEST_NAME} is missing")
    else:
        try:
            listed = parse_manifest(manifest_path.read_bytes().decode("utf-8"))
        except (TemplateIndexError, UnicodeError) as exc:
            errors.append(f"{name}/{exc}")
    manifests[name] = listed
    for child in sorted(directory.iterdir()):
        if child.name in {LICENSE_NAME, MANIFEST_NAME}:
            continue
        if child.is_symlink() or not child.is_file():
            errors.append(f"{name}/{child.name} is not a regular file")
        elif child.name not in listed:
            errors.append(f"{name}/{child.name} is not listed in {MANIFEST_NAME}")
        elif sha256_file(child) != listed[child.name]:
            errors.append(f"{name}/{child.name} does not match its SHA-256 in {MANIFEST_NAME}")
    for template in sorted(listed):
        if not (directory / template).exists():
            errors.append(f"{name}/{MANIFEST_NAME} lists {template}, which is missing")
    return errors


def tree_errors(root: Path | str) -> list[str]:
    """Everything CI checks about a chat-templates directory (TEST-PLAN T-U-5 items 2 and 6)."""
    root = Path(root)
    if not root.is_dir():
        return [f"{root} is not a directory"]
    errors: list[str] = []
    entries: list[dict] = []
    try:
        document = _read_index_document(root)
    except FileNotFoundError:
        errors.append(f"{INDEX_NAME} is missing")
    except (OSError, UnicodeError, TemplateIndexError) as exc:
        errors.append(str(exc))
    else:
        document_errors = index_document_errors(document)
        errors.extend(document_errors)
        entries = [] if document_errors else document["overrides"]
    manifests: dict[str, dict[str, str]] = {}
    for child in sorted(root.iterdir()):
        if child.is_symlink():
            errors.append(f"{child.name} is a symbolic link")
        elif child.name == INDEX_NAME and child.is_file():
            continue
        elif child.is_dir() and _VENDOR_DIR.fullmatch(child.name):
            errors.extend(_vendor_dir_errors(child, manifests))
        else:
            errors.append(f"{child.name} does not belong here: only {INDEX_NAME} and upstream-b<build> directories")
    used: set[tuple[str, str]] = set()
    for entry in entries:
        directory, name = entry["file"].split("/")
        used.add((directory, name))
        recorded = manifests.get(directory, {}).get(name)
        if recorded is None:
            errors.append(f"{entry['id']}: {entry['file']} is not vendored in {directory}/{MANIFEST_NAME}")
        elif recorded != entry["fileSha256"]:
            errors.append(f"{entry['id']}: fileSha256 is not the SHA-256 {directory}/{MANIFEST_NAME} records")
    for directory, listed in sorted(manifests.items()):
        for name in sorted(listed):
            if (directory, name) not in used:
                errors.append(f"{directory}/{name} is vendored but no index entry uses it; "
                              "vendor only the templates the index references")
    return errors
