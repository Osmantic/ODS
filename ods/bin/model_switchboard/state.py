"""Versioned model-state record for the ODS Model Switchboard (PR 1).

Contract highlights (see ods/docs/MODEL-SWITCHBOARD.md, section 3.2):

- The host agent is the only writer. Writes are temp-file + flush + fsync
  where available + atomic replace; a reader can never observe partial JSON.
- ``seq`` increments on every mutation; ``routeSeq`` increments only when the
  active route changes.
- ``active`` always remains the last proven route. In observe mode this module
  is called only after the existing activation transaction has already proved
  success, so a failed activation never touches the record.
- ``history`` keeps the last ``HISTORY_LIMIT`` verified active routes.
- Startup reconstruction from ``.env`` is permitted only when no v1 state has
  ever been committed, and is marked ``reconstructed`` with an unproven
  completion so it can never masquerade as a verified proof.

Stdlib only: the standalone host agent imports this from the installed tree.
"""

from __future__ import annotations

import errno
import json
import os
import stat
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "ods.model-state.v1"
HISTORY_LIMIT = 10
PUBLIC_MODEL_DEFAULT = "ods/current"
STATE_FILE_MODE = 0o644

_WRITABLE_BACKEND_KINDS = {"llama-server", "hipfire", "unknown"}
# Readable for one release only: records written before round F carry the
# retired Lemonade route until the host agent rewrites and re-proves them.
_LEGACY_BACKEND_KINDS = {"lemonade"}
_LEGACY_ENDPOINT_IDS = {"lemonade-default"}
_BACKEND_KINDS = _WRITABLE_BACKEND_KINDS | _LEGACY_BACKEND_KINDS
DEFAULT_ENDPOINT_ID = "llama-server-default"
_OPERATION_PHASES = {
    "requested", "staging", "verifying", "publishing",
    "flipping", "serving", "failed", "rolling_back",
}
_AVAILABILITY_MODES = {"serve_active", "queue"}

_TOP_LEVEL_KEYS = {
    "schema", "seq", "routeSeq", "operation", "desired", "active",
    "history", "availability",
}
_OPERATION_KEYS = {"id", "phase", "requestedModelId", "startedAt", "error"}
_ACTIVE_KEYS = {
    "routeSeq", "catalogId", "runtimeModelId", "publicModel", "backend",
    "contextLength", "capabilities", "verifiedAt", "reconstructed", "proof",
}
_BACKEND_KEYS = {"kind", "endpointId", "nativeRoute"}
_CAPABILITY_KEYS = {"chat", "tools", "vision", "agentViable"}
_PROOF_KEYS = {"identity", "completion"}
_HISTORY_KEYS = {"routeSeq", "catalogId", "runtimeModelId", "verifiedAt"}
_AVAILABILITY_KEYS = {"mode", "queueDeadline"}

_WRITE_LOCK = threading.RLock()
_HELD_WRITE_LOCKS: set[str] = set()
_LAST_GOOD: dict[str, dict[str, Any]] = {}


class StateError(RuntimeError):
    """Raised for unrecoverable state-record violations (writer side)."""


@contextmanager
def write_transaction(path: os.PathLike | str, *, timeout: float = 5):
    """Serialize route publication and a host participant's first snapshot.

    The persistent sidecar is never replaced or unlinked. Reentry permits a
    caller to check its durable journal and publish through record_verified_route
    in one critical section. Runtime probes and controller calls belong outside.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.parent.resolve() / (target.name + '.lock')
    key = str(lock_path)
    with _WRITE_LOCK:
        if key in _HELD_WRITE_LOCKS:
            yield
            return
        # Refuse links, shared files and foreign owners; do not repair custody.
        try:
            prior = lock_path.lstat()
        except FileNotFoundError:
            prior = None
        if prior is not None and (not stat.S_ISREG(prior.st_mode) or prior.st_nlink != 1):
            raise StateError('model state write lock is unsafe')
        flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
        fd = os.open(lock_path, flags, 0o600)
        try:
            actual = os.fstat(fd)
            named = lock_path.lstat()
            if (not stat.S_ISREG(actual.st_mode) or actual.st_nlink != 1
                    or (actual.st_dev, actual.st_ino) != (named.st_dev, named.st_ino)
                    or (os.name != 'nt' and (actual.st_uid != os.geteuid()
                                             or stat.S_IMODE(actual.st_mode) & 0o077))):
                raise StateError('model state write lock is unsafe')
            if os.name == 'nt':
                import msvcrt
                if actual.st_size == 0:
                    os.write(fd, b'\0')
                def acquire():
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                def release():
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                def acquire():
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                def release():
                    fcntl.flock(fd, fcntl.LOCK_UN)
            deadline = time.monotonic() + timeout
            while True:
                try:
                    acquire()
                    break
                except OSError as exc:
                    if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                        raise
                    if time.monotonic() >= deadline:
                        raise StateError('model state write lock is busy') from exc
                    time.sleep(.025)
            _HELD_WRITE_LOCKS.add(key)
            try:
                yield
            finally:
                _HELD_WRITE_LOCKS.remove(key)
                release()
        finally:
            os.close(fd)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _check_keys(
    value: dict[str, Any],
    *,
    allowed: set[str] | None,
    required: set[str],
    label: str,
    errors: list[str],
) -> None:
    missing = required - set(value)
    unexpected = set(value) - allowed if allowed is not None else set()
    if missing:
        errors.append(f"{label} is missing required keys: {', '.join(sorted(missing))}")
    if unexpected:
        errors.append(f"{label} has unexpected keys: {', '.join(sorted(unexpected))}")


def initial_state() -> dict[str, Any]:
    return {
        "schema": SCHEMA_VERSION,
        "seq": 0,
        "routeSeq": 0,
        "operation": None,
        "desired": None,
        "active": None,
        "history": [],
        "availability": {"mode": "serve_active", "queueDeadline": None},
    }


def validate_state(doc: Any) -> list[str]:
    """Structural validation without third-party dependencies.

    Returns a list of human-readable problems; empty means valid. The JSON
    Schema at ods/config/model-state.schema.v1.json is the authoritative
    published contract; this validator must stay in agreement with it.
    """
    errors: list[str] = []
    if not isinstance(doc, dict):
        return ["state root must be an object"]
    _check_keys(
        doc,
        allowed=_TOP_LEVEL_KEYS,
        required=_TOP_LEVEL_KEYS - {"operation"},
        label="state",
        errors=errors,
    )
    if doc.get("schema") != SCHEMA_VERSION:
        errors.append(f"schema must be {SCHEMA_VERSION!r}")
    for key in ("seq", "routeSeq"):
        value = doc.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            errors.append(f"{key} must be a non-negative integer")

    operation = doc.get("operation")
    if operation is not None:
        if not isinstance(operation, dict):
            errors.append("operation must be null or an object")
        else:
            _check_keys(
                operation,
                allowed=_OPERATION_KEYS,
                required={"id", "phase", "requestedModelId", "startedAt"},
                label="operation",
                errors=errors,
            )
            if not isinstance(operation.get("id"), str) or not operation.get("id"):
                errors.append("operation.id must be a non-empty string")
            if operation.get("phase") not in _OPERATION_PHASES:
                errors.append("operation.phase is not a known phase")
            if not isinstance(operation.get("requestedModelId"), str):
                errors.append("operation.requestedModelId must be a string")
            if not isinstance(operation.get("startedAt"), str):
                errors.append("operation.startedAt must be a string")
            if operation.get("error") is not None and not isinstance(
                operation.get("error"), str
            ):
                errors.append("operation.error must be a string or null")

    desired = doc.get("desired")
    if desired is not None:
        if not isinstance(desired, dict):
            errors.append("desired must be null or {catalogId: non-empty string}")
        else:
            _check_keys(
                desired,
                allowed={"catalogId"},
                required={"catalogId"},
                label="desired",
                errors=errors,
            )
            if (
                not isinstance(desired.get("catalogId"), str)
                or not desired.get("catalogId")
            ):
                errors.append("desired.catalogId must be a non-empty string")

    active = doc.get("active")
    if active is not None:
        if not isinstance(active, dict):
            errors.append("active must be null or an object")
        else:
            _check_keys(
                active,
                allowed=_ACTIVE_KEYS,
                required=_ACTIVE_KEYS - {"reconstructed"},
                label="active",
                errors=errors,
            )
            active_route_seq = active.get("routeSeq")
            if (
                not isinstance(active_route_seq, int)
                or isinstance(active_route_seq, bool)
                or active_route_seq < 0
            ):
                errors.append("active.routeSeq must be a non-negative integer")
            for key in ("catalogId", "runtimeModelId", "publicModel"):
                if not isinstance(active.get(key), str) or not active.get(key):
                    errors.append(f"active.{key} must be a non-empty string")
            backend = active.get("backend")
            if not isinstance(backend, dict):
                errors.append("active.backend must be an object")
            else:
                _check_keys(
                    backend,
                    allowed=_BACKEND_KEYS,
                    required={"kind", "endpointId"},
                    label="active.backend",
                    errors=errors,
                )
                if backend.get("kind") not in _BACKEND_KINDS:
                    errors.append("active.backend.kind is not a known backend kind")
                if (
                    not isinstance(backend.get("endpointId"), str)
                    or not backend.get("endpointId")
                ):
                    errors.append("active.backend.endpointId must be a non-empty string")
                native_route = backend.get("nativeRoute")
                if native_route is not None and not isinstance(native_route, str):
                    errors.append("active.backend.nativeRoute must be a string or null")
            context_length = active.get("contextLength")
            if not isinstance(context_length, int) or isinstance(context_length, bool) or context_length < 0:
                errors.append("active.contextLength must be a non-negative integer")
            capabilities = active.get("capabilities")
            if not isinstance(capabilities, dict):
                errors.append("active.capabilities must be an object")
            else:
                _check_keys(
                    capabilities,
                    allowed=_CAPABILITY_KEYS,
                    required=_CAPABILITY_KEYS,
                    label="active.capabilities",
                    errors=errors,
                )
                for key in ("chat", "tools", "vision", "agentViable"):
                    if not isinstance(capabilities.get(key), bool):
                        errors.append(f"active.capabilities.{key} must be a boolean")
            verified_at = active.get("verifiedAt")
            if verified_at is not None and not isinstance(verified_at, str):
                errors.append("active.verifiedAt must be a string or null")
            if "reconstructed" in active and not isinstance(active.get("reconstructed"), bool):
                errors.append("active.reconstructed must be a boolean")
            proof = active.get("proof")
            if not isinstance(proof, dict):
                errors.append("active.proof must be {identity, completion: bool}")
            else:
                _check_keys(
                    proof,
                    allowed=_PROOF_KEYS,
                    required=_PROOF_KEYS,
                    label="active.proof",
                    errors=errors,
                )
                if proof.get("identity") is not None and not isinstance(
                    proof.get("identity"), str
                ):
                    errors.append("active.proof.identity must be a string or null")
                if not isinstance(proof.get("completion"), bool):
                    errors.append("active.proof.completion must be a boolean")

    history = doc.get("history")
    if not isinstance(history, list):
        errors.append("history must be an array")
    else:
        if len(history) > HISTORY_LIMIT:
            errors.append(f"history exceeds {HISTORY_LIMIT} entries")
        for index, entry in enumerate(history):
            if not isinstance(entry, dict):
                errors.append(f"history[{index}] must be an object")
                continue
            _check_keys(
                entry,
                allowed=None,
                required=_HISTORY_KEYS,
                label=f"history[{index}]",
                errors=errors,
            )
            history_route_seq = entry.get("routeSeq")
            if (
                not isinstance(history_route_seq, int)
                or isinstance(history_route_seq, bool)
                or history_route_seq < 0
            ):
                errors.append(f"history[{index}].routeSeq must be a non-negative integer")
            for key in ("catalogId", "runtimeModelId"):
                if not isinstance(entry.get(key), str):
                    errors.append(f"history[{index}].{key} must be a string")
            if entry.get("verifiedAt") is not None and not isinstance(
                entry.get("verifiedAt"), str
            ):
                errors.append(f"history[{index}].verifiedAt must be a string or null")

    availability = doc.get("availability")
    if not isinstance(availability, dict):
        errors.append("availability.mode must be serve_active or queue")
    else:
        _check_keys(
            availability,
            allowed=_AVAILABILITY_KEYS,
            required=_AVAILABILITY_KEYS,
            label="availability",
            errors=errors,
        )
        if availability.get("mode") not in _AVAILABILITY_MODES:
            errors.append("availability.mode must be serve_active or queue")
        if availability.get("queueDeadline") is not None and not isinstance(
            availability.get("queueDeadline"), str
        ):
            errors.append("availability.queueDeadline must be a string or null")

    return errors


def read_state(path: os.PathLike | str) -> tuple[dict[str, Any] | None, list[str]]:
    """Read and validate the state record.

    Returns ``(doc, [])`` on success. On a missing file returns
    ``(None, [])``. On malformed or invalid content returns
    ``(last_known_good_or_None, errors)`` — malformed state never promotes
    anything and never raises out of a reader.
    """
    key = str(Path(path))
    raw = None
    last_exc: OSError | None = None
    for _attempt in range(4):
        try:
            raw = Path(path).read_text(encoding="utf-8")
            break
        except FileNotFoundError:
            return None, []
        except PermissionError as exc:
            # Windows: os.replace briefly locks the destination; a concurrent
            # reader can hit a transient sharing violation. Retry, then fall
            # back to the last known-good snapshot.
            last_exc = exc
            time.sleep(0.005)
        except OSError as exc:
            return _LAST_GOOD.get(key), [f"state read failed: {exc}"]
    if raw is None:
        return _LAST_GOOD.get(key), [f"state read failed: {last_exc}"]
    try:
        doc = json.loads(raw)
    except ValueError as exc:
        return _LAST_GOOD.get(key), [f"state is not valid JSON: {exc}"]
    errors = validate_state(doc)
    if errors:
        return _LAST_GOOD.get(key), errors
    _LAST_GOOD[key] = doc
    return doc, []


def atomic_write_state(path: os.PathLike | str, doc: dict[str, Any]) -> None:
    """Atomically persist ``doc``: same-directory temp file, fsync, replace."""
    errors = validate_state(doc)
    if errors:
        raise StateError("refusing to write invalid state: " + "; ".join(errors))
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(doc, indent=2, sort_keys=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=target.name + ".", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        try:
            os.chmod(tmp_name, STATE_FILE_MODE)
        except OSError:
            pass
        replace_error: OSError | None = None
        for _attempt in range(40):
            try:
                os.replace(tmp_name, target)
                replace_error = None
                break
            except PermissionError as exc:
                # Windows: a concurrent reader holding the destination open can
                # make the atomic rename transiently fail with a sharing
                # violation. Retry briefly; the writer is the single mutator.
                replace_error = exc
                time.sleep(0.005)
        if replace_error is not None:
            raise replace_error
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    try:  # best-effort directory durability on POSIX
        dir_fd = os.open(str(target.parent), getattr(os, "O_DIRECTORY", os.O_RDONLY))
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass
    _LAST_GOOD[str(target)] = doc


def record_verified_route(
    path: os.PathLike | str,
    *,
    catalog_id: str,
    runtime_model_id: str,
    backend_kind: str,
    endpoint_id: str,
    context_length: int,
    capabilities: dict[str, bool],
    proof_identity: str | None,
    proof_completion: bool = True,
    native_route: str | None = None,
    public_model: str = PUBLIC_MODEL_DEFAULT,
    reconstructed: bool = False,
) -> dict[str, Any]:
    """Record a proven active route (observe mode: call only after success).

    Reload-modify-write under the shared file lock. If the on-disk record is valid
    and newer than our cache, the on-disk record wins as the base — a writer
    can never regress ``seq``.
    """
    if backend_kind in _LEGACY_BACKEND_KINDS or endpoint_id in _LEGACY_ENDPOINT_IDS:
        raise StateError(f"legacy route {backend_kind}/{endpoint_id} is readable only")
    if backend_kind not in _WRITABLE_BACKEND_KINDS:
        backend_kind = "unknown"
    with write_transaction(path):
        base, errors = read_state(path)
        if base is None:
            if errors:
                # Malformed state on disk: do not guess; start a fresh record
                # but never delete the malformed file's information silently.
                raise StateError(
                    "existing state is malformed; refusing blind overwrite: "
                    + "; ".join(errors)
                )
            base = initial_state()
        doc = json.loads(json.dumps(base))  # deep copy, stdlib only

        previous = doc.get("active")
        route_changed = not (
            isinstance(previous, dict)
            and previous.get("runtimeModelId") == runtime_model_id
            and isinstance(previous.get("backend"), dict)
            and previous["backend"].get("kind") == backend_kind
            and previous["backend"].get("endpointId") == endpoint_id
        )

        doc["seq"] = int(doc["seq"]) + 1
        if route_changed:
            doc["routeSeq"] = int(doc["routeSeq"]) + 1
            previous_proof = previous.get("proof") if isinstance(previous, dict) else None
            previous_is_verified = bool(
                isinstance(previous, dict)
                and isinstance(previous_proof, dict)
                and previous_proof.get("completion") is True
                and isinstance(previous.get("verifiedAt"), str)
                and previous.get("verifiedAt")
                and previous.get("reconstructed") is not True
            )
            if previous_is_verified:
                history_entry = {
                    "routeSeq": previous.get("routeSeq", 0),
                    "catalogId": previous.get("catalogId", ""),
                    "runtimeModelId": previous.get("runtimeModelId", ""),
                    "verifiedAt": previous.get("verifiedAt"),
                }
                doc["history"] = ([history_entry] + list(doc.get("history") or []))[:HISTORY_LIMIT]

        doc["desired"] = {"catalogId": catalog_id}
        active: dict[str, Any] = {
            "routeSeq": doc["routeSeq"],
            "catalogId": catalog_id,
            "runtimeModelId": runtime_model_id,
            "publicModel": public_model,
            "backend": {
                "kind": backend_kind,
                "endpointId": endpoint_id,
                "nativeRoute": native_route,
            },
            "contextLength": int(context_length),
            "capabilities": {
                "chat": bool(capabilities.get("chat", True)),
                "tools": bool(capabilities.get("tools", False)),
                "vision": bool(capabilities.get("vision", False)),
                "agentViable": bool(capabilities.get("agentViable", False)),
            },
            "verifiedAt": None if reconstructed else _utcnow_iso(),
            "proof": {"identity": proof_identity, "completion": bool(proof_completion)},
        }
        if reconstructed:
            active["reconstructed"] = True
        doc["active"] = active
        doc["operation"] = None
        doc["availability"] = {"mode": "serve_active", "queueDeadline": None}

        atomic_write_state(path, doc)
        return doc


def is_legacy_route(active: Any) -> bool:
    """Whether ``active`` still names the retired Lemonade route (one release)."""
    backend = active.get("backend") if isinstance(active, dict) else None
    return isinstance(backend, dict) and (
        backend.get("kind") in _LEGACY_BACKEND_KINDS
        or backend.get("endpointId") in _LEGACY_ENDPOINT_IDS
    )


def migrate_env_identity(env: dict[str, str]) -> dict[str, Any] | None:
    """Derive a best-effort runtime identity from ``.env`` values.

    Every managed runtime is upstream llama-server serving ``--alias
    <GGUF_FILE>``, so the runtime identity is the GGUF filename (or the model
    name when no file is recorded). A retired ``LEMONADE_MODEL`` line is never
    read: a leftover key must not relabel the route. Returns ``None`` when the
    environment carries no local model identity (e.g. cloud-only).
    """
    if str(env.get("ODS_MODE") or "").strip().casefold() == "cloud":
        return None

    gguf = str(env.get("GGUF_FILE") or "").strip()
    llm_model = str(env.get("LLM_MODEL") or "").strip()

    runtime_id = gguf or llm_model
    if not runtime_id:
        return None

    backend = str(env.get("LLM_BACKEND") or "").strip().lower()
    if backend in {"", "lemonade"}:
        # A pre-migration LLM_BACKEND=lemonade install now serves through
        # upstream llama-server; the retired kind is never derived again.
        backend = "llama-server"
    if backend not in _WRITABLE_BACKEND_KINDS:
        backend = "unknown"

    catalog_guess = llm_model
    if not catalog_guess:
        stem = runtime_id
        if stem.lower().endswith(".gguf"):
            stem = stem[: -len(".gguf")]
        catalog_guess = stem

    context = 0
    for key in ("CTX_SIZE", "MAX_CONTEXT"):
        try:
            candidate = int(str(env.get(key) or "0").strip() or 0)
        except (TypeError, ValueError):
            continue
        if candidate > 0:
            context = candidate
            break

    return {
        "catalogId": catalog_guess,
        "runtimeModelId": runtime_id,
        "backendKind": backend,
        "contextLength": max(0, context),
    }


def initialize_if_missing(
    path: os.PathLike | str,
    env: dict[str, str],
    *,
    endpoint_id: str | None = None,
) -> dict[str, Any] | None:
    """One-time startup reconstruction when no v1 state was ever committed.

    Existing files — valid or malformed — are never overwritten here. Returns
    the written doc, or ``None`` when nothing was written.
    """
    if Path(path).exists():
        return None
    identity = migrate_env_identity(env)
    if identity is None:
        return None
    if endpoint_id is None:
        endpoint_id = DEFAULT_ENDPOINT_ID
    return record_verified_route(
        path,
        catalog_id=identity["catalogId"],
        runtime_model_id=identity["runtimeModelId"],
        backend_kind=identity["backendKind"],
        endpoint_id=endpoint_id,
        context_length=identity["contextLength"],
        capabilities={
            "chat": True,
            "tools": False,
            "vision": False,
            "agentViable": identity["contextLength"] >= 65536,
        },
        proof_identity=identity["runtimeModelId"],
        proof_completion=False,
        reconstructed=True,
    )
