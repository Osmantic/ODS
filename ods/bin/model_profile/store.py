"""``data/model-profiles.json``: measured model profiles, keyed per file x build x host.

A profile is a cache: any change to its key (every GGUF part's SHA-256, the
projector's, llama.cpp ``build_info``, the backend, the chat template's hash
and source, the probe-suite version, the host) means re-profiling at the next
switch (PLAN WP3.4). The host agent is the only writer. Writes are temp file +
fsync + atomic replace, retried briefly on a Windows sharing violation, the
same contract as ``model_switchboard.state.atomic_write_state``. Old code
that predates this file never reads it, so a rollback ignores it safely.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "ods.model-profiles.v1"
PROFILE_LIMIT = 64
FILE_MODE = 0o644
_KEY_FIELDS = (
    "ggufSha256", "projectorSha256", "buildInfo", "backend",
    "templateSha256", "templateSource", "suite", "hostId",
)
_HEX64 = re.compile(r"[0-9a-f]{64}")


class StoreError(ValueError):
    """The profile store is malformed or a profile is invalid."""


def host_id(machine_id_path: Path = Path("/etc/machine-id")) -> str:
    """A stable, non-identifying id for this machine (hash of its machine id)."""
    try:
        raw = machine_id_path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        raw = ""
    if not raw:
        raw = f"node-{uuid.getnode():012x}"
    return hashlib.sha256(("ods-model-profile:" + raw).encode("utf-8")).hexdigest()[:16]


def profile_key(
    *,
    gguf_sha256: list[str],
    projector_sha256: str | None,
    build_info: str | None,
    backend: str,
    template_sha256: str | None,
    template_source: str,
    suite: str,
    host: str,
) -> dict[str, Any]:
    return {
        "ggufSha256": list(gguf_sha256),
        "projectorSha256": projector_sha256,
        "buildInfo": build_info,
        "backend": backend,
        "templateSha256": template_sha256,
        "templateSource": template_source,
        "suite": suite,
        "hostId": host,
    }


def key_hash(key: dict[str, Any]) -> str:
    canonical = json.dumps({field: key.get(field) for field in _KEY_FIELDS}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def empty() -> dict[str, Any]:
    return {"schema": SCHEMA_VERSION, "profiles": [], "lastActivation": None}


def validate(doc: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(doc, dict):
        return ["store is not an object"]
    if doc.get("schema") != SCHEMA_VERSION:
        errors.append(f"schema is not {SCHEMA_VERSION}")
    profiles = doc.get("profiles")
    if not isinstance(profiles, list):
        return errors + ["profiles is not a list"]
    for index, profile in enumerate(profiles):
        if not isinstance(profile, dict):
            errors.append(f"profiles[{index}] is not an object")
            continue
        key = profile.get("key")
        if not isinstance(key, dict) or set(key) != set(_KEY_FIELDS):
            errors.append(f"profiles[{index}].key has the wrong fields")
        elif profile.get("keyHash") != key_hash(key):
            errors.append(f"profiles[{index}].keyHash does not match its key")
        for field in ("modelId", "ggufFile", "recordedAt"):
            if not isinstance(profile.get(field), str) or not profile[field]:
                errors.append(f"profiles[{index}].{field} is missing")
        result = profile.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("probes"), dict) \
                or not isinstance(result.get("summary"), dict):
            errors.append(f"profiles[{index}].result is malformed")
    last = doc.get("lastActivation")
    if last is not None and (not isinstance(last, dict) or not isinstance(last.get("modelId"), str)
                             or not _HEX64.fullmatch(str(last.get("keyHash") or ""))):
        errors.append("lastActivation is malformed")
    return errors


def load(path: os.PathLike | str) -> dict[str, Any]:
    """The store, or an empty one when the file does not exist yet.

    Raises StoreError for a file that exists but is not a valid store: the
    caller decides whether to re-profile over it.
    """
    target = Path(path)
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return empty()
    try:
        doc = json.loads(raw)
    except ValueError as exc:
        raise StoreError(f"model profile store is not JSON: {exc}") from exc
    errors = validate(doc)
    if errors:
        raise StoreError("model profile store is invalid: " + "; ".join(errors))
    return doc


def find(doc: dict[str, Any], key: dict[str, Any]) -> dict[str, Any] | None:
    """The stored profile for exactly this key, if any."""
    wanted = key_hash(key)
    for profile in reversed(doc["profiles"]):
        if profile["keyHash"] == wanted:
            return profile
    return None


def latest_for_model(doc: dict[str, Any], model_id: str) -> dict[str, Any] | None:
    """The newest profile recorded for a model on this host, whatever its key."""
    for profile in reversed(doc["profiles"]):
        if profile["modelId"] == model_id:
            return profile
    return None


def recorded_profile(
    key: dict[str, Any],
    *,
    model_id: str,
    gguf_file: str,
    result: dict[str, Any],
    product_version: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    return {
        "keyHash": key_hash(key),
        "key": key,
        "modelId": model_id,
        "ggufFile": gguf_file,
        "recordedAt": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "productVersion": product_version,
        "hostScope": key["hostId"],
        "result": result,
    }


def with_profile(doc: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    """A new store holding ``profile`` (replacing its key's old one), newest last, bounded."""
    kept = [entry for entry in doc["profiles"] if entry["keyHash"] != profile["keyHash"]]
    kept.append(profile)
    return {**doc, "profiles": kept[-PROFILE_LIMIT:]}


def with_last_activation(doc: dict[str, Any], model_id: str, profile_key_hash: str) -> dict[str, Any]:
    return {**doc, "lastActivation": {"modelId": model_id, "keyHash": profile_key_hash}}


def atomic_write(path: os.PathLike | str, doc: dict[str, Any]) -> None:
    """Persist ``doc`` atomically: same-directory temp file, fsync, replace."""
    errors = validate(doc)
    if errors:
        raise StoreError("refusing to write an invalid profile store: " + "; ".join(errors))
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(doc, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass  # Some filesystems (9p, SMB) cannot fsync; the replace is still atomic.
        try:
            os.chmod(tmp_name, FILE_MODE)
        except OSError:
            pass  # Windows ACLs and some mounts ignore POSIX modes.
        replace_error: OSError | None = None
        for _attempt in range(40):
            try:
                os.replace(tmp_name, target)
                replace_error = None
                break
            except PermissionError as exc:
                # Windows: a reader holding the destination open makes the
                # rename fail briefly with a sharing violation.
                replace_error = exc
                time.sleep(0.005)
        if replace_error is not None:
            raise replace_error
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass  # Already renamed into place or never created.
        raise
