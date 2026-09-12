"""Durable transaction store for assistant-first plans."""

from datetime import datetime
import hashlib
import json
import os
import re
import stat
import threading
from pathlib import Path
from typing import Any


# ── Exceptions ───────────────────────────────────────────────────────────────
class TransactionError(Exception):
    """Base transaction error."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}{': ' + detail if detail else ''}")


class ValidationRejected(TransactionError):
    pass


class IdempotencyConflict(TransactionError):
    pass


class TransitionError(TransactionError):
    pass


class ApprovalError(TransactionError):
    pass


class IntegrityError(TransactionError):
    pass


# ── Constants ────────────────────────────────────────────────────────────────
SCHEMA = "ods.assistant-first.plan-envelope.v1"
INNER_SCHEMA = "ods.assistant-first.plan.v1"
OUTER_KEYS = frozenset(
    [
        "schema",
        "planId",
        "catalogRevision",
        "observedStateRevision",
        "policyRevision",
        "planHash",
        "plan",
    ]
)
INNER_KEYS = frozenset(
    [
        "schema",
        "requestedAction",
        "catalogRevision",
        "observedStateRevision",
        "policyRevision",
        "validUntil",
        "requestedServices",
        "requestedCapabilities",
        "selectedServices",
        "operations",
        "providerBindings",
        "optionalCapabilities",
        "definitions",
        "resourceDelta",
        "requiredConfigKeys",
        "requiredSecretKeys",
        "missingRequiredConfigKeys",
        "missingRequiredSecretKeys",
        "missingConfiguration",
        "dataEffects",
        "rollbackEffects",
        "warnings",
        "blockers",
        "approval",
    ]
)
OP_KEYS = frozenset(["serviceId", "action"])
# requestedAction must be exactly "ensure"; operation actions are install/enable/repair/update/noop
REQUESTED_ACTION = "ensure"
VALID_ACTIONS = frozenset(["install", "enable", "repair", "update", "noop"])
SECRET_PATTERNS = (
    "password",
    "secret",
    "token",
    "key",
    "credential",
    "passwd",
    "private_key",
    "api_key",
    "apikey",
)
SUCCESS_PATH = (
    "planned",
    "awaiting_approval",
    "approved",
    "reserved",
    "downloading",
    "staged",
    "configuring",
    "applying",
    "verifying",
    "committed",
)
TERMINAL_STATES = frozenset(["committed", "rolled_back", "manual_recovery_required"])
TRANSITIONS = {
    "planned": {"awaiting_approval", "failed"},
    "awaiting_approval": {"approved", "failed"},
    "approved": {"reserved", "failed"},
    "reserved": {"downloading", "failed"},
    "downloading": {"staged", "failed"},
    "staged": {"configuring", "failed"},
    "configuring": {"applying", "failed"},
    "applying": {"verifying", "failed"},
    "verifying": {"committed", "failed"},
    "failed": {"reconciling"},
    "reconciling": {"rolled_back", "manual_recovery_required"},
}
MAX_RECORD_BYTES, MAX_JOURNAL_RECORDS, MAX_LIST_RESULTS = 65536, 256, 500
MAX_INPUT_BYTES = 2 * 1024 * 1024
MAX_JSON_DEPTH = 12
MAX_STRING_LEN = 65536
MAX_DICT_KEYS = 256
MAX_LIST_ITEMS = 4096
MAX_STEP_SCALAR_LEN = 1024
IDEMPOTENCY_DIR, TRANSACTIONS_DIR = "_idempotency", "transactions"
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
SERVICE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
PLAN_ID_RE = re.compile(r"^plan-[0-9a-f]{24}$")
TXN_ID_RE = re.compile(r"^txn-[0-9a-f]{24}$")
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
ACTOR_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")
ASSISTANT_PREFIXES = ("assistant", "model", "agent", "ai", "bot")
BINDING_KEYS = frozenset(
    {
        "actor",
        "catalogRevision",
        "createdAt",
        "idempotencyKey",
        "observedStateRevision",
        "planHash",
        "policyRevision",
    }
)
INDEX_KEYS = BINDING_KEYS | {"transactionId"}
APPROVAL_KEYS = frozenset(
    {
        "actor",
        "approvedAt",
        "approvedBy",
        "catalogRevision",
        "idempotencyKey",
        "observedStateRevision",
        "planHash",
        "policyRevision",
        "transactionId",
        "validUntil",
    }
)


# ── Helpers ──────────────────────────────────────────────────────────────────
def _json_safe(v, depth=0):
    """Reject non-JSON-safe types: tuples, surrogates, excessive sizes.

    Allows bools as valid JSON booleans; rejects bool-as-int at schema
    validation sites where integers are explicitly expected.
    """
    if depth > MAX_JSON_DEPTH:
        raise ValidationRejected("json-too-deep")
    if v is None or isinstance(v, bool):
        return
    if isinstance(v, int):
        if v < 0 or v > 2**53:
            raise ValidationRejected("integer-out-of-range")
        return
    if isinstance(v, float):
        raise ValidationRejected("float-value")
    if isinstance(v, str):
        if len(v) > MAX_STRING_LEN:
            raise ValidationRejected("string-too-long")
        for ch in v:
            cp = ord(ch)
            if 0xD800 <= cp <= 0xDFFF:
                raise ValidationRejected("surrogate-in-string")
        return
    if isinstance(v, dict):
        if len(v) > MAX_DICT_KEYS:
            raise ValidationRejected("dict-too-large")
        for k, val in v.items():
            if not isinstance(k, str):
                raise ValidationRejected("non-string-key")
            _json_safe(k, depth + 1)
            _json_safe(val, depth + 1)
        return
    if isinstance(v, list):
        if len(v) > MAX_LIST_ITEMS:
            raise ValidationRejected("list-too-large")
        for item in v:
            _json_safe(item, depth + 1)
        return
    raise ValidationRejected("non-json-type", type(v).__name__)


def canonical_json_bytes(value: Any) -> bytes:
    _json_safe(value)
    text = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (text + "\n").encode("utf-8", errors="strict")


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _validate_timestamp(ts: str) -> str:
    """Validate using datetime.strptime for exact UTC calendar time; Feb 31 must fail."""
    if not isinstance(ts, str):
        raise ValidationRejected("invalid-timestamp")
    if not TIMESTAMP_RE.match(ts):
        raise ValidationRejected("invalid-timestamp", "must match YYYY-MM-DDTHH:MM:SSZ")
    try:
        dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise ValidationRejected("invalid-timestamp", "not a real calendar time")
    if dt.year < 2020 or dt.year > 2100:
        raise ValidationRejected("invalid-timestamp", "year out of range")
    return ts


def _is_secret_like(name: str) -> bool:
    low = name.lower()
    return any(p in low for p in SECRET_PATTERNS)


def _check_no_secrets_in_unknown_keys(outer, inner):
    for k in set(outer.keys()) - OUTER_KEYS:
        if _is_secret_like(k):
            raise ValidationRejected("secret-unknown-key", f"outer:{k}")
    for k in set(inner.keys()) - INNER_KEYS:
        if _is_secret_like(k):
            raise ValidationRejected("secret-unknown-key", f"inner:{k}")


def _key_error(keys, allowed, prefix):
    extra = keys - allowed
    missing = allowed - keys
    parts = []
    if missing:
        parts.append(f"missing:{','.join(sorted(missing))}")
    if extra:
        parts.append(f"extra:{','.join(sorted(extra))}")
    raise ValidationRejected(prefix, "; ".join(parts))


def _is_assistant_actor(name: str) -> bool:
    low = name.lower()
    for p in ASSISTANT_PREFIXES:
        if low == p or low.startswith(p + "-") or low.startswith(p + "_"):
            return True
    return False


def _validate_actor_id(actor):
    """Validate an operational actor ID recorded in bindings and journals."""
    if not isinstance(actor, str):
        raise ValidationRejected("invalid-actor", "must be string")
    if not ACTOR_ID_RE.match(actor):
        raise ValidationRejected("invalid-actor", actor)


def _validate_approver_id(actor):
    """Approval identities must be valid and must never represent an assistant."""
    _validate_actor_id(actor)
    if _is_assistant_actor(actor):
        raise ValidationRejected("assistant-approver-rejected", actor)


def _json_safe_int(v):
    """Validate that v is an integer and not a bool (bool is subclass of int)."""
    if isinstance(v, bool):
        raise ValidationRejected("bool-not-json-safe")
    if not isinstance(v, int):
        raise ValidationRejected("integer-required")
    if v < 0 or v > 2**53:
        raise ValidationRejected("integer-out-of-range")


def _reject_dup_keys(pairs):
    """object_pairs_hook that rejects duplicate JSON keys."""
    seen = set()
    result = {}
    for k, v in pairs:
        if k in seen:
            raise ValidationRejected("duplicate-json-key", k)
        seen.add(k)
        result[k] = v
    return result


# ── Validation ───────────────────────────────────────────────────────────────
def _validate_envelope(envelope):
    if not isinstance(envelope, dict):
        raise ValidationRejected("invalid-envelope")
    keys = set(envelope.keys())
    if keys != OUTER_KEYS:
        _key_error(keys, OUTER_KEYS, "invalid-envelope-keys")
    if envelope["schema"] != SCHEMA:
        raise ValidationRejected("invalid-schema")
    if not isinstance(envelope["planId"], str) or not PLAN_ID_RE.fullmatch(
        envelope["planId"]
    ):
        raise ValidationRejected("invalid-planid")
    if not isinstance(envelope["planHash"], str) or len(envelope["planHash"]) != 64:
        raise ValidationRejected("invalid-planhash")
    if not HEX64_RE.match(envelope["planHash"]):
        raise ValidationRejected("invalid-planhash")
    for rk in ("catalogRevision", "observedStateRevision", "policyRevision"):
        val = envelope[rk]
        if not isinstance(val, str) or not HEX64_RE.match(val):
            raise ValidationRejected(f"invalid-{rk}")
    if not isinstance(envelope["plan"], dict):
        raise ValidationRejected("invalid-plan")


def _validate_inner_plan(plan):
    keys = set(plan.keys())
    if keys != INNER_KEYS:
        _key_error(keys, INNER_KEYS, "invalid-plan-keys")
    if plan["schema"] != INNER_SCHEMA:
        raise ValidationRejected("invalid-plan-schema")

    # requestedAction must be exactly "ensure"
    action = plan["requestedAction"]
    if action != REQUESTED_ACTION:
        raise ValidationRejected("invalid-action", action)

    if plan["blockers"]:
        raise ValidationRejected("blockers-not-empty")

    approval = plan.get("approval")
    if not isinstance(approval, dict):
        raise ValidationRejected("invalid-approval")
    akeys = set(approval.keys())
    if akeys != {"required", "scope"}:
        raise ValidationRejected("invalid-approval-keys")
    if approval.get("required") is not True:
        raise ValidationRejected("invalid-approval", "required must be true")
    if approval.get("scope") != "exact-plan-hash":
        raise ValidationRejected("invalid-approval-scope")

    # Validate operations and collect service order
    ops = plan.get("operations", [])
    if not isinstance(ops, list):
        raise ValidationRejected("invalid-operations")
    seen_services = set()
    seen_service_order = []
    for op in ops:
        _validate_operation(op)
        sid = op["serviceId"]
        if sid in seen_services:
            raise ValidationRejected("duplicate-service", sid)
        seen_services.add(sid)
        seen_service_order.append(sid)

    # selectedServices must be a list of unique valid IDs, exactly equal in order to ops
    sel = plan.get("selectedServices", [])
    if not isinstance(sel, list):
        raise ValidationRejected("invalid-selectedServices", "must be a list")
    if len(sel) != len(seen_services):
        raise ValidationRejected("selectedServices-count")
    for s in sel:
        if not isinstance(s, str) or not SERVICE_ID_RE.match(s):
            raise ValidationRejected("invalid-selectedServiceId", s)
    if seen_service_order != sel:
        raise ValidationRejected("service-order-mismatch")

    # Validate requestedServices is a list of valid IDs
    rs = plan.get("requestedServices", [])
    if not isinstance(rs, list):
        raise ValidationRejected("invalid-requestedServices")
    for x in rs:
        if not isinstance(x, str) or not SERVICE_ID_RE.match(x):
            raise ValidationRejected("invalid-requestedServiceId", x)

    # Validate requestedCapabilities is a list
    rc = plan.get("requestedCapabilities", [])
    if not isinstance(rc, list):
        raise ValidationRejected("invalid-requestedCapabilities")

    if not isinstance(plan.get("validUntil"), str):
        raise ValidationRejected("invalid-valid-until")
    _validate_timestamp(plan["validUntil"])


def _validate_operation(op):
    if not isinstance(op, dict):
        raise ValidationRejected("invalid-operation")
    okeys = set(op.keys())
    if okeys != OP_KEYS:
        _key_error(okeys, OP_KEYS, "invalid-operation-keys")
    sid = op["serviceId"]
    if not isinstance(sid, str) or not SERVICE_ID_RE.match(sid):
        raise ValidationRejected("invalid-service-id", sid)
    action = op["action"]
    if not isinstance(action, str) or action not in VALID_ACTIONS:
        raise ValidationRejected("invalid-operation-action")


def _validate_plan_id(envelope):
    expected_hash = _sha256(canonical_json_bytes(envelope["plan"]))
    if envelope["planHash"] != expected_hash:
        raise ValidationRejected("plan-hash-mismatch")
    if envelope["planId"] != f"plan-{expected_hash[:24]}":
        raise ValidationRejected("plan-id-mismatch")


def _validate_secret_keys(plan):
    for field in ("requiredSecretKeys", "missingRequiredSecretKeys"):
        val = plan.get(field, [])
        if not isinstance(val, list):
            raise ValidationRejected(f"invalid-{field}")
        for item in val:
            if not isinstance(item, str):
                raise ValidationRejected(f"invalid-{field}", "items must be strings")
            if "=" in item or ":" in item or "{" in item:
                raise ValidationRejected(f"secret-value-in-{field}", "names only")


def _validate_revisions(envelope, plan):
    for f in ("catalogRevision", "observedStateRevision", "policyRevision"):
        if envelope[f] != plan[f]:
            raise ValidationRejected(f"revision-mismatch-{f}")


def _derive_transaction_id(envelope, actor, idempotency_key, created_at) -> str:
    """Derive transaction ID including actor, idempotencyKey, and createdAt."""
    binding_input = {
        "actor": actor,
        "catalogRevision": envelope["catalogRevision"],
        "createdAt": created_at,
        "idempotencyKey": idempotency_key,
        "observedStateRevision": envelope["observedStateRevision"],
        "planHash": envelope["planHash"],
        "policyRevision": envelope["policyRevision"],
    }
    return "txn-" + _sha256(canonical_json_bytes(binding_input))[:24]


def _validate_envelope_full(envelope):
    raw = canonical_json_bytes(envelope)
    if len(raw) > MAX_INPUT_BYTES:
        raise ValidationRejected("input-too-large")
    _validate_envelope(envelope)
    plan = envelope["plan"]
    _validate_inner_plan(plan)
    _validate_plan_id(envelope)
    _validate_secret_keys(plan)
    _validate_revisions(envelope, plan)
    _check_no_secrets_in_unknown_keys(envelope, plan)
    return raw


# ── Path security helpers ────────────────────────────────────────────────────
def _require_injected_root(root: Path):
    """Require an absolute non-root injected path; lstat each existing component.

    Reject root symlinks, non-directories, wrong owner, and enforce 0700
    only on existing components that are part of the requested root itself
    (not on filesystem ancestors like /tmp which the caller controls).
    Do not resolve a symlink and silently accept its target.
    """
    if not root.is_absolute():
        raise TransactionError("root-not-absolute", str(root))
    parts = root.parts
    if len(parts) < 2 or (len(parts) == 1 and parts[0] == "/"):
        raise TransactionError("root-is-filesystem-root", str(root))
    acc = Path("/")
    for part in parts:
        acc = acc / part
        if acc == Path("/"):
            continue
        try:
            st = os.lstat(str(acc))
        except OSError:
            continue  # will be created later; validate at creation time
        if stat.S_ISLNK(st.st_mode):
            raise TransactionError("symlink-in-root", str(acc))
        if stat.S_ISDIR(st.st_mode):
            # Only enforce mode/owner on the root itself and its descendants,
            # not on ancestors that the caller does not control.
            if acc == root:
                mode = st.st_mode & 0o777
                if mode != 0o700:
                    raise TransactionError("bad-dir-mode", f"{acc}: {oct(mode)}")
                if st.st_uid != os.getuid():
                    raise TransactionError("dir-wrong-owner", str(acc))


def _safe_file_open_read(path: Path):
    """Open with O_NOFOLLOW, fstat the descriptor, compare inode to lstat.

    Enforce owner, 0600, nlink=1, and size before/during read.
    """
    O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
    try:
        lstat_result = os.lstat(str(path))
    except OSError as e:
        raise TransactionError("stat-failed", f"{path}: {e}")
    if stat.S_ISLNK(lstat_result.st_mode):
        raise IntegrityError("symlink-read-blocked", str(path))
    if not stat.S_ISREG(lstat_result.st_mode):
        raise IntegrityError("not-regular-file", str(path))
    fd = os.open(str(path), os.O_RDONLY | O_NOFOLLOW)
    try:
        fstat_result = os.fstat(fd)
        if fstat_result.st_ino != lstat_result.st_ino:
            os.close(fd)
            raise IntegrityError("inode-mismatch", str(path))
        mode = fstat_result.st_mode & 0o777
        if mode != 0o600:
            os.close(fd)
            raise IntegrityError("bad-file-mode", f"{path}: {oct(mode)}")
        if fstat_result.st_uid != os.getuid():
            os.close(fd)
            raise IntegrityError("file-wrong-owner", str(path))
        if fstat_result.st_nlink != 1:
            os.close(fd)
            raise IntegrityError("link-count", f"{path}: {fstat_result.st_nlink}")
    except IntegrityError:
        raise
    except OSError:
        os.close(fd)
        raise
    return fd, fstat_result.st_size


def _read_fd(fd, max_bytes=None):
    """Read all data from an open file descriptor; caller owns the close."""
    result = b""
    while True:
        chunk = os.read(fd, 65536)
        if not chunk:
            break
        if max_bytes and len(result) + len(chunk) > max_bytes:
            raise IntegrityError("file-too-large")
        result += chunk
    return result


# ── FS helpers ───────────────────────────────────────────────────────────────
def _check_path_under_root(target: Path, root: Path):
    """Verify target is under root without following symlinks."""
    try:
        target.relative_to(root)
    except ValueError:
        raise TransactionError("path-outside-root", str(target))


def _check_dir_mode(p: Path):
    try:
        st = os.lstat(str(p))
    except OSError as e:
        raise TransactionError("stat-failed", f"{p}: {e}")
    if stat.S_ISLNK(st.st_mode):
        raise TransactionError("symlink-detected", str(p))
    if not stat.S_ISDIR(st.st_mode):
        raise TransactionError("not-directory", str(p))
    mode = st.st_mode & 0o777
    if mode != 0o700:
        raise TransactionError("bad-dir-mode", f"{p}: {oct(mode)}")
    if st.st_uid != os.getuid():
        raise TransactionError("dir-wrong-owner", str(p))
    if st.st_nlink < 2:
        raise TransactionError("dir-bad-nlink", f"{p}: {st.st_nlink}")


def _check_file_mode(p: Path, expected_mode: int):
    try:
        st = os.lstat(str(p))
    except OSError as e:
        raise TransactionError("stat-failed", f"{p}: {e}")
    if stat.S_ISLNK(st.st_mode):
        raise TransactionError("symlink-detected", str(p))
    if not stat.S_ISREG(st.st_mode):
        raise TransactionError("not-regular-file", str(p))
    mode = st.st_mode & 0o777
    if mode != expected_mode:
        raise TransactionError(
            "bad-file-mode", f"{p}: {oct(mode)} expected {oct(expected_mode)}"
        )
    if st.st_nlink != 1:
        raise TransactionError("link-count", f"{p}: {st.st_nlink}")
    return st.st_size


def _safe_mkdir(path, mode=0o700):
    """Create a directory or verify it exists; reject symlinks.

    Does not resolve the path first. An existing symlink at *path* is
    rejected immediately. New directories are created as a single
    component under an already-verified parent.
    """
    p = Path(path)
    # Reject symlinks at the original (unresolved) path
    try:
        lstat = os.lstat(str(p))
    except OSError:
        # Does not exist yet; create below
        lstat = None
    if lstat is not None:
        if stat.S_ISLNK(lstat.st_mode):
            raise TransactionError("symlink-in-mkdir", str(p))
        if stat.S_ISDIR(lstat.st_mode):
            _check_dir_mode(p)
            return
        raise TransactionError("not-directory", str(p))
    parent = p.parent
    if not parent.exists():
        raise TransactionError("parent-missing", str(parent))
    _check_dir_mode(parent)
    p.mkdir(mode=mode, parents=False, exist_ok=False)
    _check_dir_mode(p)


def _write_immutable(path, data, mode=0o600):
    O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
    p = Path(path)
    if p.exists() and not p.is_symlink():
        raise TransactionError("file-exists", str(path))
    if p.is_symlink():
        raise TransactionError("symlink-detected", str(path))
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | O_NOFOLLOW, mode)
    try:
        written = 0
        while written < len(data):
            n = os.write(fd, data[written:])
            if n == 0:
                raise TransactionError("write-failed")
            written += n
        os.fsync(fd)
    finally:
        os.close(fd)
    dir_fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    _check_file_mode(Path(path), mode)


def _journal_append(path, line, expected_actor=None):
    """Append to journal with full verification.

    First record: O_CREAT|O_EXCL. Later: require existing checked file, O_APPEND.
    After append: re-read, validate each line as canonical JSONL, require final LF,
    reject duplicates, validate actor binding.
    """
    if len(line) > MAX_RECORD_BYTES:
        raise TransactionError("record-too-large")
    try:
        if not line.endswith(b"\n") or line.endswith(b"\n\n"):
            raise IntegrityError("journal-new-record-framing")
        raw_record = line[:-1]
        if not raw_record or b"\r" in raw_record:
            raise IntegrityError("journal-new-record-framing")
        record = json.loads(
            raw_record.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_dup_keys,
        )
    except (json.JSONDecodeError, UnicodeDecodeError, ValidationRejected) as exc:
        raise IntegrityError("journal-new-record-invalid") from exc
    if not isinstance(record, dict) or canonical_json_bytes(record) != line:
        raise IntegrityError("journal-new-record-noncanonical")
    if expected_actor is not None and record.get("actor") != expected_actor:
        raise IntegrityError("journal-actor-mismatch")
    O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
    p = Path(path)
    pre_inode = None
    if p.exists():
        prior = _read_journal(p.parent, expected_actor=expected_actor)
        _validate_journal_records(
            prior + [record],
            record.get("transactionId"),
            record.get("planHash"),
            expected_actor=expected_actor,
        )
        st = os.lstat(str(p))
        if stat.S_ISLNK(st.st_mode):
            raise IntegrityError("symlink-journal")
        if not stat.S_ISREG(st.st_mode):
            raise IntegrityError("journal-not-regular")
        mode = st.st_mode & 0o777
        if mode != 0o600:
            raise IntegrityError("bad-journal-mode")
        if st.st_uid != os.getuid():
            raise IntegrityError("journal-wrong-owner")
        if st.st_nlink != 1:
            raise IntegrityError("journal-link-count")
        pre_inode = st.st_ino
        flags = os.O_WRONLY | os.O_APPEND | O_NOFOLLOW
    else:
        _validate_journal_records(
            [record],
            record.get("transactionId"),
            record.get("planHash"),
            expected_actor=expected_actor,
        )
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | O_NOFOLLOW
    fd = os.open(str(path), flags, 0o600)
    try:
        # Guard against TOCTOU: re-check inode after open
        fd_st = os.fstat(fd)
        if pre_inode is not None and (
            fd_st.st_ino != pre_inode or fd_st.st_dev != st.st_dev
        ):
            raise IntegrityError("journal-replaced")
        written = 0
        while written < len(line):
            n = os.write(fd, line[written:])
            if n == 0:
                raise TransactionError("journal-write-failed")
            written += n
        os.fsync(fd)
    finally:
        os.close(fd)

    verified = _read_journal(p.parent, expected_actor=expected_actor)
    _validate_journal_records(
        verified,
        record["transactionId"],
        record["planHash"],
        expected_actor=expected_actor,
    )
    if verified[-1] != record:
        raise IntegrityError("journal-append-verify-failed")

    # fsync parent directory
    dir_fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def _read_checked(path, max_bytes=None):
    """Read with O_NOFOLLOW, fstat inode compare, owner/mode/nlink enforcement."""
    fd, sz = _safe_file_open_read(path)
    if max_bytes and sz > max_bytes:
        os.close(fd)
        raise IntegrityError("file-too-large", str(path))
    try:
        return _read_fd(fd, max_bytes)
    finally:
        os.close(fd)


def _decode_canonical_object(path, expected_keys, error_prefix):
    raw = _read_checked(path, MAX_INPUT_BYTES)
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_dup_keys,
        )
    except (json.JSONDecodeError, UnicodeDecodeError, ValidationRejected) as exc:
        raise IntegrityError(f"{error_prefix}-parse-error") from exc
    if not isinstance(value, dict):
        raise IntegrityError(f"{error_prefix}-not-object")
    if set(value) != expected_keys:
        raise IntegrityError(f"{error_prefix}-keys")
    try:
        encoded = canonical_json_bytes(value)
    except ValidationRejected as exc:
        raise IntegrityError(f"{error_prefix}-invalid-json") from exc
    if raw != encoded:
        raise IntegrityError(f"{error_prefix}-noncanonical")
    return value


# ── Lock helpers ─────────────────────────────────────────────────────────────
_HAS_FCNTL = False
try:
    import fcntl as _fcntl

    _HAS_FCNTL = True
except ImportError:
    pass

_HAS_MSVCRT = False
try:
    import msvcrt as _msvcrt

    _HAS_MSVCRT = True
except ImportError:
    pass


class _RootLock:
    """Advisory lock on root; serializes mutations.

    Linux: fcntl flock.
    Windows: msvcrt byte-range lock or fail closed.
    No primitive: fail closed (PID file alone is not a lock).
    """

    def __init__(self, root: Path):
        self._lock_path = root / ".lock"
        self._fd = None
        self._thread_lock = threading.Lock()

    def acquire(self):
        self._thread_lock.acquire()
        O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
        flags = os.O_RDWR | os.O_CREAT | O_NOFOLLOW
        try:
            self._fd = os.open(str(self._lock_path), flags, 0o600)

            # Verify the opened descriptor and the directory entry still name
            # the same regular, single-link, owner-only file.
            lstat_result = os.lstat(str(self._lock_path))
            fd_result = os.fstat(self._fd)
            if stat.S_ISLNK(lstat_result.st_mode):
                raise TransactionError("symlink-lock", str(self._lock_path))
            if (
                fd_result.st_dev != lstat_result.st_dev
                or fd_result.st_ino != lstat_result.st_ino
            ):
                raise TransactionError("lock-replaced", str(self._lock_path))
            if not stat.S_ISREG(fd_result.st_mode):
                raise TransactionError("lock-not-regular", str(self._lock_path))
            mode = fd_result.st_mode & 0o777
            if mode != 0o600:
                raise TransactionError("bad-lock-mode", str(self._lock_path))
            if fd_result.st_uid != os.getuid():
                raise TransactionError("lock-wrong-owner", str(self._lock_path))
            if fd_result.st_nlink != 1:
                raise TransactionError("lock-link-count", str(self._lock_path))

            if _HAS_FCNTL:
                _fcntl.flock(self._fd, _fcntl.LOCK_EX)
            elif _HAS_MSVCRT:
                # Windows: real byte-range lock
                try:
                    _msvcrt.locking(self._fd, _msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    raise TransactionError("lock-acquire-failed") from exc
            else:
                raise TransactionError("no-lock-primitive")
        except BaseException:
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None
            self._thread_lock.release()
            raise

    def release(self):
        if self._fd is not None:
            fd = self._fd
            self._fd = None
            try:
                if _HAS_FCNTL:
                    _fcntl.flock(fd, _fcntl.LOCK_UN)
                elif _HAS_MSVCRT:
                    try:
                        _msvcrt.locking(fd, _msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass
            finally:
                try:
                    os.close(fd)
                except OSError:
                    pass
                self._thread_lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *a):
        self.release()


# ── Journal helpers ──────────────────────────────────────────────────────────
JOURNAL_SCHEMA = "ods.journal-record.v1"
STEP_ALLOWED_KEYS = frozenset(["step", "status", "detail", "serviceId"])
STEP_DENY_PREFIXES = ("secret", "config", "env", "credential", "arg", "options")


def _validate_metadata(metadata):
    if metadata is None:
        return
    if not isinstance(metadata, dict):
        raise ValidationRejected("invalid-metadata")
    for k in metadata:
        if not isinstance(k, str):
            raise ValidationRejected("non-string-key")
    if set(metadata) != {"step"}:
        raise ValidationRejected("invalid-metadata-keys")
    step = metadata["step"]
    if not isinstance(step, dict):
        raise ValidationRejected("invalid-step", "must be object")
    for k in step:
        if not isinstance(k, str):
            raise ValidationRejected("non-string-key")
        low = k.lower()
        for p in STEP_DENY_PREFIXES:
            if low == p or low.startswith(p + "_") or low.startswith(p + "-"):
                raise ValidationRejected("step-secret-key", k)
        if _is_secret_like(k):
            raise ValidationRejected("step-secret-key", k)
        if k not in STEP_ALLOWED_KEYS:
            raise ValidationRejected("step-extra-key", k)
        if not isinstance(step[k], (str, int, bool)):
            raise ValidationRejected("step-non-scalar", k)
        if isinstance(step[k], str) and len(step[k]) > MAX_STEP_SCALAR_LEN:
            raise ValidationRejected("step-scalar-too-long", k)


def _journal_entry(txn_id, sequence, state, plan_hash, timestamp, actor, step=None):
    entry = {
        "schema": JOURNAL_SCHEMA,
        "transactionId": txn_id,
        "sequence": sequence,
        "state": state,
        "planHash": plan_hash,
        "timestamp": timestamp,
        "actor": actor,
    }
    if step is not None:
        entry["step"] = step
    _validate_metadata(entry.get("step") and {"step": entry["step"]})
    return canonical_json_bytes(entry)


def _read_journal(tx_dir, expected_actor=None):
    jp = tx_dir / "journal.jsonl"
    if not jp.exists():
        return []
    fd, sz = _safe_file_open_read(jp)
    if sz > MAX_RECORD_BYTES * MAX_JOURNAL_RECORDS:
        os.close(fd)
        raise IntegrityError("journal-too-large")
    try:
        data = _read_fd(fd)
    finally:
        os.close(fd)

    if not data.endswith(b"\n"):
        raise IntegrityError("journal-missing-final-lf")
    raw_lines = data.split(b"\n")
    if raw_lines[-1] != b"":
        raise IntegrityError("journal-missing-final-lf")
    raw_lines = raw_lines[:-1]
    records = []
    for raw_line in raw_lines:
        if len(raw_line) == 0:
            raise IntegrityError("journal-blank-record")
        try:
            decoded = raw_line.decode("utf-8", errors="strict")
            if decoded != decoded.strip() or "\r" in decoded:
                raise IntegrityError("journal-whitespace-record")
            obj = json.loads(decoded, object_pairs_hook=_reject_dup_keys)
        except (json.JSONDecodeError, UnicodeDecodeError, ValidationRejected) as exc:
            raise IntegrityError("journal-parse-error") from exc
        except IntegrityError:
            raise
        if not isinstance(obj, dict):
            raise IntegrityError("journal-record-not-object")
        # Verify byte-identical canonical JSON
        if canonical_json_bytes(obj) != raw_line + b"\n":
            raise IntegrityError("journal-noncanonical-record")
        if expected_actor is not None and obj.get("actor") != expected_actor:
            raise IntegrityError("journal-actor-mismatch")
        records.append(obj)
    if not records:
        raise IntegrityError("empty-journal")
    if len(records) > MAX_JOURNAL_RECORDS:
        raise IntegrityError("journal-too-many-records")
    return records


def _validate_journal_records(records, txn_id, plan_hash, expected_actor=None):
    if not records:
        raise IntegrityError("empty-journal")
    expected_seq = 1
    prev_state = None
    prev_ts = None
    first_record = True
    second_record = True  # True = still need to validate second record
    for rec in records:
        required = {
            "schema",
            "transactionId",
            "sequence",
            "state",
            "planHash",
            "timestamp",
            "actor",
        }
        rkeys = set(rec.keys())
        allowed = required | {"step"}
        if rkeys - allowed:
            raise IntegrityError("journal-extra-keys")
        if not required.issubset(rkeys):
            raise IntegrityError("journal-missing-keys")
        if rec["schema"] != JOURNAL_SCHEMA:
            raise IntegrityError("journal-schema")
        if rec["transactionId"] != txn_id:
            raise IntegrityError("journal-txn-mismatch")
        if rec["planHash"] != plan_hash:
            raise IntegrityError("journal-planhash-mismatch")
        if rec["state"] not in set(TRANSITIONS) | TERMINAL_STATES:
            raise IntegrityError("journal-unknown-state")
        try:
            _validate_actor_id(rec.get("actor", ""))
            _validate_timestamp(rec["timestamp"])
            if "step" in rec:
                _validate_metadata({"step": rec["step"]})
        except ValidationRejected as exc:
            raise IntegrityError("journal-record-invalid") from exc

        # Validate sequence is an integer (not bool), matches expected
        seq = rec["sequence"]
        if isinstance(seq, bool) or not isinstance(seq, int):
            raise IntegrityError("sequence-not-integer")
        if seq != expected_seq:
            raise IntegrityError("sequence-gap")

        if prev_ts is not None and rec["timestamp"] < prev_ts:
            raise IntegrityError("timestamp-order")
        if prev_state is not None:
            allowed_next = TRANSITIONS.get(prev_state, set())
            if rec["state"] not in allowed_next:
                raise IntegrityError("illegal-transition")

        # First record must be "planned", second must be "awaiting_approval"
        if first_record:
            if rec["state"] != "planned":
                raise IntegrityError("journal-must-start-planned")
            first_record = False
        elif second_record:
            if rec["state"] != "awaiting_approval":
                raise IntegrityError("journal-second-must-awaiting")
            second_record = False

        # Validate actor matches the immutable binding actor
        if expected_actor is not None:
            if rec.get("actor") != expected_actor:
                raise IntegrityError("journal-actor-mismatch")

        prev_state = rec["state"]
        prev_ts = rec["timestamp"]
        expected_seq += 1


def _validate_plan_integrity(tx_dir):
    plan_path = tx_dir / "plan.json"
    try:
        envelope = _decode_canonical_object(plan_path, OUTER_KEYS, "plan")
        _validate_envelope_full(envelope)
    except ValidationRejected as exc:
        raise IntegrityError("plan-invalid") from exc
    return envelope


# ── TransactionStore ─────────────────────────────────────────────────────────
class TransactionStore:
    """Stdlib-only durable transaction store."""

    def __init__(self, root):
        root_path = Path(root)
        _require_injected_root(root_path)
        self.root = root_path
        self._tx_dir = self.root / TRANSACTIONS_DIR
        self._idem_dir = self.root / IDEMPOTENCY_DIR
        _check_path_under_root(self._tx_dir, self.root)
        _check_path_under_root(self._idem_dir, self.root)
        _safe_mkdir(self.root, 0o700)
        _safe_mkdir(self._tx_dir, 0o700)
        _safe_mkdir(self._idem_dir, 0o700)
        self._lock = _RootLock(self.root)

    def _tx_path(self, txn_id):
        if not isinstance(txn_id, str) or not TXN_ID_RE.fullmatch(txn_id):
            raise ValidationRejected("invalid-txn-id")
        p = self._tx_dir / txn_id
        try:
            st = os.lstat(str(p))
        except OSError:
            pass  # does not exist yet
        else:
            if stat.S_ISLNK(st.st_mode):
                raise TransactionError("symlink-detected", str(p))
        _check_path_under_root(p, self.root)
        return p

    def _idem_path(self, key):
        p = self._idem_dir / (key + ".bind")
        try:
            st = os.lstat(str(p))
        except OSError:
            pass  # does not exist yet
        else:
            if stat.S_ISLNK(st.st_mode):
                raise TransactionError("symlink-detected", str(p))
        _check_path_under_root(p, self.root)
        return p

    @staticmethod
    def _entry_exists(path):
        try:
            os.lstat(str(path))
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise IntegrityError("entry-stat-failed") from exc
        return True

    def _load_binding(self, tx_dir, transaction_id=None, envelope=None):
        binding_path = tx_dir / "binding.json"
        if not self._entry_exists(binding_path):
            raise IntegrityError("missing-binding")
        binding = _decode_canonical_object(binding_path, BINDING_KEYS, "binding")
        try:
            _validate_actor_id(binding["actor"])
            _validate_timestamp(binding["createdAt"])
        except ValidationRejected as exc:
            raise IntegrityError("binding-invalid") from exc
        if not isinstance(binding["idempotencyKey"], str) or not HEX64_RE.match(
            binding["idempotencyKey"]
        ):
            raise IntegrityError("binding-idempotency-key")
        for field in (
            "planHash",
            "catalogRevision",
            "observedStateRevision",
            "policyRevision",
        ):
            if not isinstance(binding[field], str) or not HEX64_RE.match(
                binding[field]
            ):
                raise IntegrityError("binding-digest")
        if envelope is not None:
            for field in (
                "planHash",
                "catalogRevision",
                "observedStateRevision",
                "policyRevision",
            ):
                if binding[field] != envelope[field]:
                    raise IntegrityError("binding-plan-mismatch")
        if transaction_id is not None:
            source = envelope or {
                "planHash": binding["planHash"],
                "catalogRevision": binding["catalogRevision"],
                "observedStateRevision": binding["observedStateRevision"],
                "policyRevision": binding["policyRevision"],
            }
            expected = _derive_transaction_id(
                source,
                binding["actor"],
                binding["idempotencyKey"],
                binding["createdAt"],
            )
            if transaction_id != expected:
                raise IntegrityError("transaction-id-binding-mismatch")
        return binding

    def _load_index(self, path, transaction_id, binding):
        if not self._entry_exists(path):
            raise IntegrityError("missing-idempotency-index")
        index = _decode_canonical_object(path, INDEX_KEYS, "idempotency-index")
        expected = dict(binding)
        expected["transactionId"] = transaction_id
        if index != expected:
            raise IntegrityError("idempotency-index-mismatch")
        return index

    def _load_approval(self, tx_dir, transaction_id, envelope, binding):
        path = tx_dir / "approval.json"
        if not self._entry_exists(path):
            raise IntegrityError("missing-approval")
        approval = _decode_canonical_object(path, APPROVAL_KEYS, "approval")
        try:
            _validate_actor_id(approval["actor"])
            _validate_approver_id(approval["approvedBy"])
            _validate_timestamp(approval["approvedAt"])
            _validate_timestamp(approval["validUntil"])
        except ValidationRejected as exc:
            raise IntegrityError("approval-invalid") from exc
        if _is_assistant_actor(approval["approvedBy"]):
            raise IntegrityError("approval-assistant-actor")
        expected = {
            "actor": binding["actor"],
            "catalogRevision": envelope["catalogRevision"],
            "idempotencyKey": binding["idempotencyKey"],
            "observedStateRevision": envelope["observedStateRevision"],
            "planHash": envelope["planHash"],
            "policyRevision": envelope["policyRevision"],
            "transactionId": transaction_id,
            "validUntil": envelope["plan"]["validUntil"],
        }
        if any(approval[key] != value for key, value in expected.items()):
            raise IntegrityError("approval-binding-mismatch")
        if not binding["createdAt"] <= approval["approvedAt"] < approval["validUntil"]:
            raise IntegrityError("approval-time-invalid")
        return approval

    def _read_transaction(
        self,
        transaction_id,
        require_index=True,
        allow_unjournaled_approval=False,
    ):
        tx_dir = self._tx_path(transaction_id)
        if not self._entry_exists(tx_dir):
            raise TransactionError("not-found", transaction_id)
        _check_dir_mode(tx_dir)
        envelope = _validate_plan_integrity(tx_dir)
        binding = self._load_binding(tx_dir, transaction_id, envelope)
        records = _read_journal(tx_dir, expected_actor=binding["actor"])
        if len(records) < 2:
            raise IntegrityError("incomplete-initial-journal")
        _validate_journal_records(
            records,
            transaction_id,
            envelope["planHash"],
            expected_actor=binding["actor"],
        )
        if (
            records[0]["timestamp"] != binding["createdAt"]
            or records[1]["timestamp"] != binding["createdAt"]
        ):
            raise IntegrityError("initial-journal-timestamp-mismatch")
        index_path = self._idem_path(binding["idempotencyKey"])
        if require_index or self._entry_exists(index_path):
            self._load_index(index_path, transaction_id, binding)
        has_approved = any(record["state"] == "approved" for record in records)
        approval_path = tx_dir / "approval.json"
        if has_approved:
            approval = self._load_approval(tx_dir, transaction_id, envelope, binding)
        else:
            if self._entry_exists(approval_path):
                if not allow_unjournaled_approval:
                    raise IntegrityError("unexpected-approval")
                approval = self._load_approval(
                    tx_dir, transaction_id, envelope, binding
                )
            else:
                approval = None
        return {
            "transactionId": transaction_id,
            "txDir": tx_dir,
            "envelope": envelope,
            "binding": binding,
            "journal": records,
            "approval": approval,
            "state": records[-1]["state"],
        }

    def _recover_create(
        self, tx_dir, transaction_id, envelope, binding_data, index_data
    ):
        """Finish only an exact prefix of create's durable write sequence."""
        _check_dir_mode(tx_dir)
        try:
            names = {entry.name for entry in os.scandir(str(tx_dir))}
        except OSError as exc:
            raise IntegrityError("transaction-dir-read-failed") from exc
        allowed = {"binding.json", "plan.json", "journal.jsonl"}
        if names - allowed:
            raise IntegrityError("incomplete-transaction-extra-entry")

        binding_path = tx_dir / "binding.json"
        if self._entry_exists(binding_path):
            binding = self._load_binding(tx_dir, transaction_id, envelope)
            if binding != binding_data:
                raise IdempotencyConflict("tx-dir-collision")
        else:
            if names:
                raise IntegrityError("create-prefix-missing-binding")
            _write_immutable(binding_path, canonical_json_bytes(binding_data), 0o600)
            names.add("binding.json")

        plan_path = tx_dir / "plan.json"
        if self._entry_exists(plan_path):
            stored_envelope = _validate_plan_integrity(tx_dir)
            if stored_envelope != envelope:
                raise IdempotencyConflict("tx-dir-collision")
        else:
            if names != {"binding.json"}:
                raise IntegrityError("create-prefix-missing-plan")
            _write_immutable(plan_path, canonical_json_bytes(envelope), 0o600)
            names.add("plan.json")

        journal_path = tx_dir / "journal.jsonl"
        expected_records = []
        for sequence, state_name in ((1, "planned"), (2, "awaiting_approval")):
            expected_records.append(
                json.loads(
                    _journal_entry(
                        transaction_id,
                        sequence,
                        state_name,
                        envelope["planHash"],
                        binding_data["createdAt"],
                        binding_data["actor"],
                    ).decode("utf-8")
                )
            )
        if self._entry_exists(journal_path):
            records = _read_journal(tx_dir, expected_actor=binding_data["actor"])
            _validate_journal_records(
                records,
                transaction_id,
                envelope["planHash"],
                expected_actor=binding_data["actor"],
            )
            if records not in (expected_records[:1], expected_records):
                raise IntegrityError("create-prefix-journal-mismatch")
        else:
            if names != {"binding.json", "plan.json"}:
                raise IntegrityError("create-prefix-missing-journal")
            records = []
        for record in expected_records[len(records) :]:
            _journal_append(
                journal_path,
                canonical_json_bytes(record),
                expected_actor=binding_data["actor"],
            )

        idem_path = self._idem_path(binding_data["idempotencyKey"])
        _write_immutable(idem_path, canonical_json_bytes(index_data), 0o600)
        return self._read_transaction(transaction_id)

    def create(self, envelope, actor, idempotency_key, timestamp, current_time):
        _validate_timestamp(timestamp)
        _validate_timestamp(current_time)
        if timestamp > current_time:
            raise ValidationRejected("future-timestamp")
        _validate_actor_id(actor)
        if not isinstance(idempotency_key, str) or not HEX64_RE.match(idempotency_key):
            raise ValidationRejected("invalid-idempotency-key")
        _validate_envelope_full(envelope)
        plan = envelope["plan"]
        vu = plan.get("validUntil")
        if vu and vu <= current_time:
            raise ValidationRejected("expired")

        txn_id = _derive_transaction_id(envelope, actor, idempotency_key, timestamp)
        binding_data = {
            "actor": actor,
            "catalogRevision": envelope["catalogRevision"],
            "createdAt": timestamp,
            "idempotencyKey": idempotency_key,
            "observedStateRevision": envelope["observedStateRevision"],
            "planHash": envelope["planHash"],
            "policyRevision": envelope["policyRevision"],
        }
        index_data = dict(binding_data)
        index_data["transactionId"] = txn_id

        with self._lock:
            idem_path = self._idem_path(idempotency_key)
            if self._entry_exists(idem_path):
                try:
                    self._load_index(idem_path, txn_id, binding_data)
                    existing = self._read_transaction(txn_id)
                except (IntegrityError, TransactionError) as exc:
                    raise IdempotencyConflict("idempotency-drift") from exc
                if existing["envelope"] != envelope:
                    raise IdempotencyConflict("idempotency-drift")
                return self._existing_descriptor(txn_id, existing)

            tx_dir = self._tx_path(txn_id)
            if self._entry_exists(tx_dir):
                try:
                    repaired = self._recover_create(
                        tx_dir, txn_id, envelope, binding_data, index_data
                    )
                except IdempotencyConflict:
                    raise
                except TransactionError as exc:
                    raise IntegrityError("incomplete-transaction") from exc
                return self._existing_descriptor(txn_id, repaired)

            _safe_mkdir(tx_dir, 0o700)

            _write_immutable(
                tx_dir / "binding.json", canonical_json_bytes(binding_data), 0o600
            )

            _write_immutable(
                tx_dir / "plan.json", canonical_json_bytes(envelope), 0o600
            )

            jp = tx_dir / "journal.jsonl"
            seq = 0
            for st in ("planned", "awaiting_approval"):
                seq += 1
                line = _journal_entry(
                    txn_id, seq, st, envelope["planHash"], timestamp, actor
                )
                _journal_append(jp, line, expected_actor=actor)

            _write_immutable(idem_path, canonical_json_bytes(index_data), 0o600)

            return {
                "transactionId": txn_id,
                "planHash": envelope["planHash"],
                "state": "awaiting_approval",
                "sequence": seq,
                "duplicate": False,
            }

    def _existing_descriptor(self, txn_id, loaded=None):
        loaded = loaded or self._read_transaction(txn_id)
        return {
            "transactionId": txn_id,
            "planHash": loaded["envelope"]["planHash"],
            "state": loaded["state"],
            "sequence": len(loaded["journal"]),
            "duplicate": True,
        }

    def transition(
        self,
        transaction_id,
        target_state,
        actor,
        timestamp,
        current_time,
        metadata=None,
    ):
        _validate_timestamp(timestamp)
        _validate_timestamp(current_time)
        if not isinstance(target_state, str):
            raise ValidationRejected("invalid-target-state")
        if timestamp > current_time:
            raise ValidationRejected("future-timestamp")
        _validate_actor_id(actor)
        _validate_metadata(metadata)

        with self._lock:
            loaded = self._read_transaction(transaction_id)
            tx_dir = loaded["txDir"]
            envelope = loaded["envelope"]
            binding = loaded["binding"]
            records = loaded["journal"]
            if actor != binding["actor"]:
                raise TransitionError(
                    "actor-mismatch", f"expected {binding['actor']}, got {actor}"
                )

            current_state = loaded["state"]
            if current_state in TERMINAL_STATES:
                if target_state == current_state:
                    return {
                        "transactionId": transaction_id,
                        "state": current_state,
                        "sequence": len(records),
                        "noop": True,
                    }
                raise TransitionError("terminal-state", current_state)

            allowed = TRANSITIONS.get(current_state, set())

            # Reject expiry when entering reserved (execution start) if plan expired
            if target_state == "reserved":
                plan_vu = envelope["plan"].get("validUntil", "")
                if plan_vu and current_time >= plan_vu:
                    raise TransitionError("expired-at-reserve")

            # The full transaction read above requires and validates the exact
            # immutable approval before an approved transaction can continue.
            if current_state == "approved" and target_state == "reserved":
                if loaded["approval"] is None:
                    raise TransitionError("missing-approval-for-reserve")

            if target_state not in allowed:
                raise TransitionError(
                    "illegal-transition", f"{current_state} -> {target_state}"
                )

            jp = tx_dir / "journal.jsonl"
            if len(records) >= MAX_JOURNAL_RECORDS:
                raise TransactionError("journal-full")
            next_seq = len(records) + 1
            line = _journal_entry(
                transaction_id,
                next_seq,
                target_state,
                envelope["planHash"],
                timestamp,
                actor,
                metadata["step"] if metadata is not None else None,
            )
            _journal_append(jp, line, expected_actor=actor)
            return {
                "transactionId": transaction_id,
                "state": target_state,
                "sequence": next_seq,
            }

    def approve(self, transaction_id, approval_data, current_time):
        _validate_timestamp(current_time)
        if not isinstance(approval_data, dict):
            raise ApprovalError("invalid-approval-data")
        try:
            canonical_json_bytes(approval_data)
        except ValidationRejected as exc:
            raise ApprovalError("invalid-approval-data", exc.code) from exc
        required_keys = APPROVAL_KEYS
        akeys = set(approval_data.keys())
        if not required_keys.issubset(akeys):
            raise ApprovalError(
                "missing-approval-fields",
                f"missing:{','.join(sorted(required_keys - akeys))}",
            )
        if akeys - required_keys:
            raise ApprovalError(
                "extra-approval-fields",
                f"unexpected:{','.join(sorted(akeys - required_keys))}",
            )

        actor = approval_data["actor"]
        approved_by = approval_data["approvedBy"]
        try:
            _validate_actor_id(actor)
            _validate_approver_id(approved_by)
            _validate_timestamp(approval_data["approvedAt"])
            _validate_timestamp(approval_data["validUntil"])
        except ValidationRejected as exc:
            raise ApprovalError("invalid-approval-data", exc.code) from exc
        if approval_data["approvedAt"] > current_time:
            raise ApprovalError("future-approval")
        if not isinstance(approval_data["idempotencyKey"], str) or not HEX64_RE.match(
            approval_data["idempotencyKey"]
        ):
            raise ApprovalError("invalid-idempotency-key")
        if not approval_data["approvedAt"] < approval_data["validUntil"]:
            raise ApprovalError("approval-expired")
        if not current_time < approval_data["validUntil"]:
            raise ApprovalError("approval-expired")

        with self._lock:
            try:
                loaded = self._read_transaction(
                    transaction_id, allow_unjournaled_approval=True
                )
            except TransactionError as exc:
                raise ApprovalError(exc.code, exc.detail) from exc
            tx_dir = loaded["txDir"]
            envelope = loaded["envelope"]
            binding = loaded["binding"]
            records = loaded["journal"]
            if actor != binding["actor"]:
                raise ApprovalError("actor-mismatch")
            expected = {
                "actor": binding["actor"],
                "approvedAt": approval_data["approvedAt"],
                "approvedBy": approved_by,
                "catalogRevision": envelope["catalogRevision"],
                "idempotencyKey": binding["idempotencyKey"],
                "observedStateRevision": envelope["observedStateRevision"],
                "planHash": envelope["planHash"],
                "policyRevision": envelope["policyRevision"],
                "transactionId": transaction_id,
                "validUntil": envelope["plan"]["validUntil"],
            }
            if approval_data != expected:
                raise ApprovalError("approval-binding-mismatch")
            if not binding["createdAt"] <= approval_data["approvedAt"]:
                raise ApprovalError("approval-before-transaction")

            approval_record = dict(approval_data)
            current_state = loaded["state"]
            if current_state == "approved":
                if loaded["approval"] == approval_record:
                    return {
                        "transactionId": transaction_id,
                        "state": "approved",
                        "sequence": len(records),
                        "noop": True,
                    }
                raise ApprovalError("approval-conflict")
            if current_state != "awaiting_approval":
                raise ApprovalError("wrong-state", current_state)

            approval_path = tx_dir / "approval.json"
            if loaded["approval"] is not None:
                if loaded["approval"] != approval_record:
                    raise ApprovalError("approval-conflict")
            else:
                _write_immutable(
                    approval_path, canonical_json_bytes(approval_record), 0o600
                )
                if (
                    self._load_approval(tx_dir, transaction_id, envelope, binding)
                    != approval_record
                ):
                    raise IntegrityError("approval-write-verify-failed")

            jp = tx_dir / "journal.jsonl"
            next_seq = len(records) + 1
            line = _journal_entry(
                transaction_id,
                next_seq,
                "approved",
                envelope["planHash"],
                approval_data["approvedAt"],
                actor,
            )
            _journal_append(jp, line, expected_actor=actor)
            return {
                "transactionId": transaction_id,
                "state": "approved",
                "sequence": next_seq,
            }

    def read(self, transaction_id):
        with self._lock:
            loaded = self._read_transaction(transaction_id)
            return {
                "transactionId": transaction_id,
                "envelope": loaded["envelope"],
                "state": loaded["state"],
                "sequence": len(loaded["journal"]),
                "journal": loaded["journal"],
                "approval": loaded["approval"],
            }

    def list_transactions(self):
        with self._lock:
            return self._list_transactions_locked()

    def _list_transactions_locked(self):
        results = []
        if not self._tx_dir.exists():
            return results
        for entry in sorted(self._tx_dir.iterdir()):
            # Skip non-tx entries
            if entry.name.startswith("_"):
                continue
            if not TXN_ID_RE.match(entry.name):
                continue

            # Reject symlink dirs; never follow Path.is_dir on attacker links
            try:
                st = os.lstat(str(entry))
                if stat.S_ISLNK(st.st_mode):
                    results.append(
                        {"transactionId": entry.name, "error": "symlink-dir"}
                    )
                    continue
                if not stat.S_ISDIR(st.st_mode):
                    continue
                mode = st.st_mode & 0o777
                if mode != 0o700:
                    results.append(
                        {"transactionId": entry.name, "error": "bad-mode-dir"}
                    )
                    continue
            except OSError:
                continue

            try:
                loaded = self._read_transaction(entry.name)
                results.append(
                    {
                        "transactionId": entry.name,
                        "planHash": loaded["envelope"]["planHash"],
                        "state": loaded["state"],
                        "sequence": len(loaded["journal"]),
                    }
                )
            except (
                json.JSONDecodeError,
                OSError,
                IntegrityError,
                TransactionError,
                ValidationRejected,
            ) as e:
                results.append({"transactionId": entry.name, "error": str(e)})
            if len(results) >= MAX_LIST_RESULTS:
                break
        return results

    def inspect_recovery(self, transaction_id, observed_evidence, current_time):
        """Write-free recovery inspection. Uses full read, strict evidence bounds."""
        _validate_timestamp(current_time)
        if not isinstance(observed_evidence, dict):
            raise ValidationRejected("invalid-evidence")
        ev_keys = set(observed_evidence.keys())
        ev_allowed = {"observedState", "completedStep"}
        if ev_keys - ev_allowed:
            raise ValidationRejected("evidence-extra-keys")

        # Strictly bound observedState
        if "observedState" in observed_evidence:
            valid_states = set(TRANSITIONS.keys()) | TERMINAL_STATES
            if observed_evidence["observedState"] not in valid_states:
                raise ValidationRejected("invalid-observed-state")

        # Strictly bound completedStep
        if "completedStep" in observed_evidence:
            cs = observed_evidence["completedStep"]
            if cs is not None:
                if not isinstance(cs, str):
                    raise ValidationRejected("invalid-completed-step")
                if len(cs) > MAX_STEP_SCALAR_LEN:
                    raise ValidationRejected("completedStep-too-long")

        with self._lock:
            loaded = self._read_transaction(transaction_id)
        records = loaded["journal"]
        current_state = loaded["state"]
        seq = len(records)

        if current_state in TERMINAL_STATES:
            return {
                "transactionId": transaction_id,
                "currentState": current_state,
                "terminal": True,
                "nextState": None,
                "reason": f"{current_state} is terminal",
                "sequence": seq,
            }

        if current_state == "failed":
            next_state = self._recovery_for_failed(records, observed_evidence)
        elif current_state == "reconciling":
            next_state = self._recovery_for_reconciling(observed_evidence)
        else:
            # Unknown/contradictory state → manual recovery
            if current_state not in TRANSITIONS:
                return {
                    "transactionId": transaction_id,
                    "currentState": current_state,
                    "terminal": False,
                    "nextState": "manual_recovery_required",
                    "reason": "unknown-state",
                    "sequence": seq,
                }
            idx = (
                SUCCESS_PATH.index(current_state)
                if current_state in SUCCESS_PATH
                else -1
            )
            if 0 <= idx < len(SUCCESS_PATH) - 1:
                next_state = SUCCESS_PATH[idx + 1]
            else:
                next_state = "failed"
            return {
                "transactionId": transaction_id,
                "currentState": current_state,
                "terminal": False,
                "nextState": next_state,
                "reason": f"resume to {next_state}",
                "sequence": seq,
            }

        return {
            "transactionId": transaction_id,
            "currentState": current_state,
            "terminal": False,
            "nextState": next_state,
            "reason": f"recovery for {current_state}",
            "sequence": seq,
        }

    def _recovery_for_failed(self, records, evidence):
        # Every failure enters the durable reconciliation state first.  Only a
        # subsequent evidence-backed reconciliation decision may terminate as
        # rolled back or manual recovery; this prevents bypassing compensation.
        return "reconciling"

    def _recovery_for_reconciling(self, evidence):
        completed = evidence.get("completedStep")
        observed = evidence.get("observedState")
        if completed == "rollback-complete" or observed == "rolled_back":
            return "rolled_back"
        return "manual_recovery_required"
