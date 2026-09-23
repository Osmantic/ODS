#!/usr/bin/env python3
"""Shared strict loader for the versioned reviewed historical-blob policy.

The policy (security-evals/historical-secret-closure/reviewed-blobs.json) binds
every reviewed blob by exact blob SHA-1, raw payload SHA-256 over the exact git
blob bytes, exact path, human review labels/reason, and the exact sorted
audit_source scanner labels (empty when the source auditor does not flag the
blob). It is the single source of truth shared by scan-history-secrets,
audit_source, audit_remote_refs, and upstream-attestation. This module is
dependency-free, parses JSON strictly (rejecting duplicate decoded keys at every
depth), requires the exact v2 document shape and constants, and never emits
matched literal values.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REVIEWED_BLOBS_PATH = ROOT / "security-evals" / "historical-secret-closure" / "reviewed-blobs.json"

EXACT_BLOB = re.compile(r"[0-9a-f]{40}")
EXACT_SHA256 = re.compile(r"[0-9a-f]{64}")

SCHEMA_VERSION = 2
SCHEMA_URL = "https://osmantic.com/pixel/schemas/historical-secret-review-v2.schema.json"
OPERATION = "pixel-historical-secret-review"
REPOSITORY = "Osmantic/Pixel"
TOP_LEVEL_KEYS = frozenset({"$schema", "schemaVersion", "operation", "repository", "note", "reviewedBlobs"})
ENTRY_KEYS = frozenset({"blobSha1", "payloadSha256", "path", "labels", "auditSourceLabels", "reason"})


class ReviewedPolicyError(RuntimeError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ReviewedPolicyError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def strict_json_loads(text: str) -> object:
    """Dependency-free strict JSON parse that rejects duplicate keys at every depth.

    Every JSON object (including nested ones) is decoded through a hook that fails
    on a repeated decoded key. Any malformed input is surfaced as a controlled
    ReviewedPolicyError rather than a raw decode exception.
    """
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except ReviewedPolicyError:
        raise
    except (json.JSONDecodeError, ValueError) as exc:
        raise ReviewedPolicyError(f"invalid JSON: {exc}") from exc


def canonical(value: object) -> bytes:
    """Canonical JSON used for the semantic policy digest (matches remote-ref policy)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _validate_v2(value: object) -> dict[str, object]:
    """Validate the exact v2 reviewed-blob policy shape and constants."""
    if not isinstance(value, dict):
        raise ReviewedPolicyError("reviewed-blob policy is malformed")
    if set(value) != TOP_LEVEL_KEYS:
        raise ReviewedPolicyError("reviewed-blob policy has an unexpected top-level shape")
    if value["schemaVersion"] != SCHEMA_VERSION:
        raise ReviewedPolicyError("reviewed-blob policy schema is unsupported")
    if value["$schema"] != SCHEMA_URL:
        raise ReviewedPolicyError("reviewed-blob policy $schema is invalid")
    if value["operation"] != OPERATION:
        raise ReviewedPolicyError("reviewed-blob policy operation is invalid")
    if value["repository"] != REPOSITORY:
        raise ReviewedPolicyError("reviewed-blob policy repository identity is invalid")
    note = value["note"]
    if not isinstance(note, str) or not (40 <= len(note) <= 2000):
        raise ReviewedPolicyError("reviewed-blob policy note is invalid")
    entries = value["reviewedBlobs"]
    if not isinstance(entries, list) or not entries or len(entries) > 256:
        raise ReviewedPolicyError("reviewed-blob policy has no reviewed entries or exceeds the bound")
    result: list[dict[str, object]] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ReviewedPolicyError("reviewed-blob policy entry is malformed")
        if set(entry) != ENTRY_KEYS:
            raise ReviewedPolicyError("reviewed-blob policy entry has an unexpected shape")
        blob = entry["blobSha1"]
        if not isinstance(blob, str) or not EXACT_BLOB.fullmatch(blob):
            raise ReviewedPolicyError("reviewed-blob policy entry lacks an exact blob SHA-1")
        path_value = entry["path"]
        if not isinstance(path_value, str) or not path_value.strip() or not (1 <= len(path_value) <= 400):
            raise ReviewedPolicyError("reviewed-blob policy entry lacks an exact path")
        labels = entry["labels"]
        if (
            not isinstance(labels, list) or not labels or len(labels) > 16
            or any(
                not isinstance(item, str) or not item.strip() or not (3 <= len(item) <= 64)
                for item in labels
            )
            or len(set(labels)) != len(labels)
        ):
            raise ReviewedPolicyError("reviewed-blob policy labels are invalid")
        reason = entry["reason"]
        if not isinstance(reason, str) or not (12 <= len(reason) <= 600):
            raise ReviewedPolicyError("reviewed-blob policy entry lacks a review reason")
        digest = entry["payloadSha256"]
        if not isinstance(digest, str) or not EXACT_SHA256.fullmatch(digest):
            raise ReviewedPolicyError("reviewed-blob policy entry lacks an exact payload SHA-256")
        audit_labels = entry["auditSourceLabels"]
        if (
            not isinstance(audit_labels, list) or len(audit_labels) > 16
            or any(
                not isinstance(item, str) or not item.strip() or not (3 <= len(item) <= 64)
                for item in audit_labels
            )
            or list(audit_labels) != sorted(audit_labels)
            or len(set(audit_labels)) != len(audit_labels)
        ):
            raise ReviewedPolicyError("reviewed-blob policy auditSourceLabels are invalid")
        if blob in seen:
            raise ReviewedPolicyError(f"duplicate reviewed-blob policy entry: {blob}")
        seen.add(blob)
        result.append(entry)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "repository": REPOSITORY,
        "reviewedBlobs": result,
        "byBlob": {entry["blobSha1"]: entry for entry in result},
    }


def _read_text(path: Path) -> str:
    """Read a policy file, converting file-level failures into ReviewedPolicyError.

    Missing, unreadable, and invalid UTF-8 inputs all fail closed with a
    content-free ReviewedPolicyError instead of surfacing a raw
    FileNotFoundError/UnicodeDecodeError/OSError at the security boundary.
    """
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ReviewedPolicyError(f"cannot read reviewed-blob policy: {path.name}") from exc


def policy_sha256(path: Path | None = None) -> str:
    """Strict-parse, validate, then hash the reviewed-blob policy document."""
    path = Path(path) if path is not None else REVIEWED_BLOBS_PATH
    text = _read_text(path)
    value = strict_json_loads(text)
    _validate_v2(value)
    return canonical_sha256(value)


def load(path: Path | None = None) -> dict[str, object]:
    """Load and strictly validate the v2 reviewed-blob policy.

    Returns {"schemaVersion", "repository", "reviewedBlobs", "byBlob",
    "policySha256"} with every entry bound to the exact fields the schema
    requires. Raises ReviewedPolicyError on any malformed, duplicate, extra, or
    under-specified field. v1 documents are rejected: every current assurance
    caller requires the exact v2 contract.
    """
    path = Path(path) if path is not None else REVIEWED_BLOBS_PATH
    text = _read_text(path)
    value = strict_json_loads(text)
    validated = _validate_v2(value)
    validated["policySha256"] = canonical_sha256(value)
    return validated


def acknowledged_entries(policy: dict[str, object]) -> list[dict[str, object]]:
    """Entries the audit_source scanner actually flags (nonempty auditSourceLabels)."""
    return [entry for entry in policy["reviewedBlobs"] if entry.get("auditSourceLabels")]
