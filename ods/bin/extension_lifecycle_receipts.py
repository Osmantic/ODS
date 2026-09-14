"""Durable, immutable host-side lifecycle operation receipt store.

Records that the Dashboard transaction state machine *started* or *terminally
finished* an operation.  This module does not duplicate the state machine, call
lifecycle or lease APIs, import routes, perform network I/O, replay, clean up,
archive, or mutate services.

Publication protocol
--------------------
Each receipt file is created in a unique same-directory temp file
(``O_CREAT | O_EXCL | O_WRONLY`` plus safe flags where available) matching
exactly ``tmp-<32 lowercase hex>.receipt.json``, written in full (a zero-byte
``os.write`` result is an I/O error), fsynced, chmod'd read-only on POSIX and
fsynced again so the mode change is durable, then published by hard-linking
the temp file onto its final name (``os.link``) — *never* by replace/rename.
The temp fd stays open through link publication and its fstat identity is
captured: before linking, the temp path must still hold that inode/device;
after a successful link, the final path must hold the same inode/device.
Only a path that still shows the inode opened by this process is unlinked; an
attacker-substituted temp path is never unlinked.  Failure to remove the
authentic temp after successful publication is a stable ``receipt-io-error``.

``EEXIST`` from the publication link is authoritative: the winner is read back
and converged on if identical, otherwise a ``conflict`` or ``integrity`` error
is raised.  If hard links are unsupported the store fails closed with the
stable ``link-unsupported`` error rather than falling back to rename.

Correctness deliberately does not rely on a process-local lock or an existence
precheck: concurrent writers race the link and the loser converges or
conflicts.  Existence probes in this module are optimizations only.

Reading
-------
Readers use lstat / open / fstat checks to reject symlinks, non-regular
files, oversize files, inode substitution, and on POSIX anything but exact
mode 0444, the current owner, or ``st_nlink != 1`` (after the publication temp
link is removed; a transient second link during publication is retried,
bounded).  Every read used by ``snapshot`` also verifies path/content
binding: a valid receipt stored under a filename other than the one derived
from its own transaction/operation binding is ``integrity``.  Orphan
terminals (a terminal receipt without its started receipt) are never
trusted: both ``snapshot`` and ``begin`` raise ``integrity``.  Temp leftovers
are never promoted, trusted, or deleted by readers or ``health()``;
``health()`` counts them and deletes nothing.

Root directory
--------------
The root is validated with a single lstat that never follows the final path
component and must be a real directory owned by the current user with
exactly POSIX mode 0700.  Its device/inode identity is recorded and
revalidated at entry to every public method; replacement is rejected.  This
is a bounded identity check: it detects a replacement that has already
happened but cannot prevent a malicious replacement after any given check.
On POSIX, directory fsync after publication is an acceptance requirement:
open/fsync failures are stable ``receipt-io-error``, not best-effort.

Windows/NTFS limitation
-----------------------
This phase relies on Python ``os.link``.  POSIX metadata checks (owner, mode
bits, ``st_nlink``, directory fsync) cannot be fully mirrored on
Windows/NTFS; no unsafe fallback is provided.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import time
import uuid
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1
RECEIPT_KIND_STARTED = "started"
RECEIPT_KIND_TERMINAL = "terminal"

MAX_RECEIPT_BYTES = 4096
_MAX_SERVICE_ID_COUNT = 64
_MAX_SERVICE_ID_LENGTH = 128
_MAX_NLINK_RETRIES = 4
_NLINK_RETRY_SECONDS = 0.005

_ROOT_MODE = 0o700
_PUBLISHED_MODE = 0o444
_TEMP_MODE = 0o600

# Canonical JSON: sorted keys, compact separators, ensure_ascii false.
_CANONICAL_JSON_KWARGS = {
    "sort_keys": True,
    "separators": (",", ":"),
    "ensure_ascii": False,
}

# Closed set of batch operation keys.
_BATCH_OPERATION_KEYS = frozenset({
    "download-and-verify",
    "stage",
    "backup",
    "configure",
    "verify",
    "restore",
    "release",
    "observe",
})

_PER_SERVICE_OPERATION_PREFIXES = ("reserve:", "apply:", "compensate:")

# Domain separation for receipt file names: the fixed hex digest of this
# prefix plus the binding is the file name, so no caller text ever appears in
# a final or temp file name.
_FILE_NAME_DOMAIN = "ods-extension-lifecycle-receipt-v1"

_TEMP_PREFIX = "tmp-"
_TEMP_SUFFIX = ".receipt.json"

# ---------------------------------------------------------------------------
# Validation patterns
# ---------------------------------------------------------------------------

_TRANSACTION_ID_RE = re.compile(r"txn-[0-9a-f]{24}")
_HASH64_RE = re.compile(r"[0-9a-f]{64}")
# Conservative service identifiers: lowercase alphanumerics with internal
# separators only, anchored on both ends.
_SERVICE_ID_RE = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class LifecycleReceiptError(RuntimeError):
    """Base error carrying a stable, non-secret string ``code``.

    ``code`` values are stable protocol identifiers suitable for matching;
    ``detail`` is free-form and safe for logs.
    """

    def __init__(self, code: str, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if detail is None else f"{code}: {detail}")


class LifecycleConflictError(LifecycleReceiptError):
    """Requested binding diverges from durable data.  Code ``conflict``."""

    def __init__(self, detail: str | None = None) -> None:
        super().__init__("conflict", detail)


class LifecycleIntegrityError(LifecycleReceiptError):
    """Durable data is corrupt or unsafe to trust.  Code ``integrity``.

    Corrupt data is never overwritten or repaired by this module.
    """

    def __init__(self, detail: str | None = None) -> None:
        super().__init__("integrity", detail)


class LifecycleLinkUnsupportedError(LifecycleReceiptError):
    """Hard-link publication is unavailable here.  Code
    ``link-unsupported``.  The store fails closed instead of renaming."""

    def __init__(self, detail: str | None = None) -> None:
        super().__init__("link-unsupported", detail)


# ---------------------------------------------------------------------------
# Immutable return records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StartedReceipt:
    """Immutable started receipt."""

    transaction_id: str
    plan_hash: str
    operation_key: str
    request_hash: str
    service_ids: tuple[str, ...]
    event_hash: str


@dataclass(frozen=True)
class TerminalReceipt:
    """Immutable terminal receipt."""

    transaction_id: str
    plan_hash: str
    operation_key: str
    request_hash: str
    service_ids: tuple[str, ...]
    outcome: str  # "completed" or "failed"
    evidence_hash: str
    started_event_hash: str
    event_hash: str


@dataclass(frozen=True)
class LifecycleSnapshot:
    """Bounded immutable state snapshot for one operation."""

    transaction_id: str
    operation_key: str
    state: str  # "absent" | "started" | "completed" | "failed"
    started_receipt: StartedReceipt | None
    terminal_receipt: TerminalReceipt | None


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def _validate_transaction_id(transaction_id: Any) -> str:
    if not isinstance(transaction_id, str):
        raise LifecycleReceiptError("invalid-transaction-id", "must be a string")
    if _TRANSACTION_ID_RE.fullmatch(transaction_id) is None:
        raise LifecycleReceiptError(
            "invalid-transaction-id",
            "must be exactly 'txn-' plus 24 lowercase hex characters",
        )
    return transaction_id


def _validate_hash64(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise LifecycleReceiptError(f"invalid-{label}", "must be a string")
    if _HASH64_RE.fullmatch(value) is None:
        raise LifecycleReceiptError(
            f"invalid-{label}", "must be exactly 64 lowercase hex characters"
        )
    return value


def _validate_service_ids(service_ids: Any) -> tuple[str, ...]:
    """Validate a bounded, non-empty, ordered, unique service-ID sequence.

    Accepts only list or tuple.  Rejects sets, generators, mappings, strings,
    bytes, booleans, and any value that would rely on coercion.
    """
    if not isinstance(service_ids, (list, tuple)):
        raise LifecycleReceiptError(
            "invalid-service-ids",
            "must be an ordered list or tuple of unique strings",
        )
    items = list(service_ids)  # copy the list/tuple to preserve order
    if not items:
        raise LifecycleReceiptError(
            "invalid-service-ids", "must contain at least one service"
        )
    if len(items) > _MAX_SERVICE_ID_COUNT:
        raise LifecycleReceiptError(
            "invalid-service-ids",
            f"too many services (maximum {_MAX_SERVICE_ID_COUNT})",
        )
    seen: set[str] = set()
    for index, item in enumerate(items):
        if isinstance(item, bool) or not isinstance(item, str):
            raise LifecycleReceiptError(
                "invalid-service-id",
                f"service at index {index} must be a string, not "
                f"{type(item).__name__}",
            )
        if not item or len(item) > _MAX_SERVICE_ID_LENGTH:
            raise LifecycleReceiptError(
                "invalid-service-id",
                f"service at index {index} must be 1..{_MAX_SERVICE_ID_LENGTH} "
                "characters",
            )
        if _SERVICE_ID_RE.fullmatch(item) is None:
            raise LifecycleReceiptError(
                "invalid-service-id",
                f"service at index {index} is not a conservative identifier",
            )
        if item in seen:
            raise LifecycleReceiptError(
                "invalid-service-ids",
                f"service {item!r} appears more than once",
            )
        seen.add(item)
    return tuple(items)


def _validate_operation_key(
    operation_key: Any, service_ids: tuple[str, ...] | None
) -> str:
    """Validate against the closed operation-key set.

    When ``service_ids`` is given, per-service suffixes must be one of them;
    when it is ``None`` (the snapshot path, which has no service list), the
    suffix is validated structurally only.
    """
    if not isinstance(operation_key, str):
        raise LifecycleReceiptError("invalid-operation-key", "must be a string")
    if operation_key in _BATCH_OPERATION_KEYS:
        return operation_key
    for prefix in _PER_SERVICE_OPERATION_PREFIXES:
        if operation_key.startswith(prefix):
            suffix = operation_key[len(prefix):]
            if service_ids is not None:
                if suffix not in service_ids:
                    raise LifecycleReceiptError(
                        "invalid-operation-key",
                        f"per-service suffix {suffix!r} is not one of service_ids",
                    )
            else:
                if _SERVICE_ID_RE.fullmatch(suffix) is None:
                    raise LifecycleReceiptError(
                        "invalid-operation-key",
                        "per-service suffix is not a conservative identifier",
                    )
            return operation_key
    raise LifecycleReceiptError(
        "invalid-operation-key",
        "operation_key is not in the closed key set",
    )


# ---------------------------------------------------------------------------
# Canonical JSON
# ---------------------------------------------------------------------------


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    """Canonical JSON bytes: sorted keys, compact separators, ensure_ascii
    false, one final newline."""
    text = json.dumps(payload, **_CANONICAL_JSON_KWARGS)
    return text.encode("utf-8") + b"\n"


def _compute_event_hash(payload: dict[str, Any]) -> str:
    """SHA-256 over the canonical UTF-8 JSON object holding every field
    except ``eventHash``."""
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


# ---------------------------------------------------------------------------
# Strict receipt parser
# ---------------------------------------------------------------------------

_STARTED_KEYS = frozenset({
    "schemaVersion", "kind", "transactionId", "planHash", "operationKey",
    "requestHash", "serviceIds", "eventHash",
})
_TERMINAL_KEYS = frozenset({
    "schemaVersion", "kind", "transactionId", "planHash", "operationKey",
    "requestHash", "serviceIds", "outcome", "evidenceHash",
    "startedEventHash", "eventHash",
})


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-numeric constant not permitted: {name}")


def _reject_float(text: str) -> Any:
    raise ValueError(f"floating-point number not permitted: {text!r}")


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


class _StrictParser:
    """Strict receipt parser.

    Rejects duplicate JSON keys, floats, NaN/Infinity/constants, booleans in
    integer positions, unknown or missing keys, non-canonical bytes, invalid
    bindings, invalid event hashes and chains, invalid UTF-8, and trailing
    bytes.  Callers additionally verify canonical re-encoding and hash chains.
    """

    def __init__(self, kind: str, keys: frozenset[str]) -> None:
        self._kind = kind
        self._keys = keys
        self._decoder = json.JSONDecoder(
            object_pairs_hook=_no_duplicate_keys,
            parse_constant=_reject_constant,
            parse_float=_reject_float,
        )

    def parse(self, raw: bytes) -> dict[str, Any]:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise LifecycleIntegrityError("receipt bytes are not valid UTF-8")
        return self.parse_text(text)

    def parse_text(self, text: str) -> dict[str, Any]:
        try:
            value, end = self._raw_decode(text)
        except (ValueError, RecursionError) as exc:
            raise LifecycleIntegrityError(f"invalid receipt JSON: {exc}") from None
        remainder = text[end:]
        if remainder.strip():
            raise LifecycleIntegrityError("trailing bytes after JSON document")
        if remainder != "\n":
            raise LifecycleIntegrityError(
                "receipt bytes are not canonical (exactly one final newline)"
            )
        if not isinstance(value, dict):
            raise LifecycleIntegrityError("receipt root must be a JSON object")
        self._check_keys(value)
        self._check_envelope(value)
        self._check_bindings(value)
        return value

    # -- internals --

    def _raw_decode(self, text: str) -> tuple[Any, int]:
        if text != text.lstrip():
            raise LifecycleIntegrityError(
                "receipt bytes are not canonical (leading whitespace)"
            )
        return self._decoder.raw_decode(text)

    def _check_keys(self, value: dict[str, Any]) -> None:
        keys = set(value)
        unknown = keys - self._keys
        missing = self._keys - keys
        if unknown:
            raise LifecycleIntegrityError(f"unknown receipt keys: {sorted(unknown)!r}")
        if missing:
            raise LifecycleIntegrityError(f"missing receipt keys: {sorted(missing)!r}")

    def _check_envelope(self, value: dict[str, Any]) -> None:
        kind = value.get("kind")
        if not isinstance(kind, str) or kind != self._kind:
            raise LifecycleIntegrityError(f"receipt kind must be {self._kind!r}")
        version = value.get("schemaVersion")
        if isinstance(version, bool) or not isinstance(version, int):
            raise LifecycleIntegrityError("receipt schemaVersion must be an integer")
        if version != SCHEMA_VERSION:
            raise LifecycleIntegrityError(
                f"receipt schemaVersion must be {SCHEMA_VERSION}"
            )

    def _check_bindings(self, value: dict[str, Any]) -> None:
        transaction_id = value.get("transactionId")
        if not isinstance(transaction_id, str) or (
            _TRANSACTION_ID_RE.fullmatch(transaction_id) is None
        ):
            raise LifecycleIntegrityError("receipt transactionId is invalid")
        for label in ("planHash", "requestHash"):
            item = value.get(label)
            if not isinstance(item, str) or _HASH64_RE.fullmatch(item) is None:
                raise LifecycleIntegrityError(f"receipt {label} is invalid")
        if self._kind == RECEIPT_KIND_TERMINAL:
            evidence = value.get("evidenceHash")
            if not isinstance(evidence, str) or _HASH64_RE.fullmatch(evidence) is None:
                raise LifecycleIntegrityError("receipt evidenceHash is invalid")
            outcome = value.get("outcome")
            if outcome not in ("completed", "failed"):
                raise LifecycleIntegrityError(
                    "receipt outcome must be 'completed' or 'failed'"
                )
            started_event_hash = value.get("startedEventHash")
            if not isinstance(started_event_hash, str) or (
                _HASH64_RE.fullmatch(started_event_hash) is None
            ):
                raise LifecycleIntegrityError("receipt startedEventHash is invalid")
        service_ids = value.get("serviceIds")
        try:
            _validate_service_ids(service_ids)
        except LifecycleReceiptError as exc:
            raise LifecycleIntegrityError(
                f"receipt serviceIds invalid: {exc.detail}"
            ) from None
        operation_key = value.get("operationKey")
        try:
            _validate_operation_key(operation_key, tuple(service_ids or ()))
        except LifecycleReceiptError as exc:
            raise LifecycleIntegrityError(
                f"receipt operationKey invalid: {exc.detail}"
            ) from None

    def verify_event_hash(self, value: dict[str, Any]) -> str:
        """Verify ``eventHash`` equals the SHA-256 of the canonical JSON of
        every other field, and return it."""
        event_hash = value.get("eventHash")
        if not isinstance(event_hash, str) or _HASH64_RE.fullmatch(event_hash) is None:
            raise LifecycleIntegrityError("receipt eventHash is invalid")
        binding = {key: item for key, item in value.items() if key != "eventHash"}
        if _compute_event_hash(binding) != event_hash:
            raise LifecycleIntegrityError("receipt eventHash does not match content")
        return event_hash


def _parse_and_verify(raw: bytes, kind: str) -> tuple[dict[str, Any], str]:
    """Parse a receipt of ``kind``, verify canonical re-encoding reproduces
    the stored bytes exactly, and verify its event hash.  Returns
    ``(payload, event_hash)``."""
    parser = _StrictParser(
        kind, _STARTED_KEYS if kind == RECEIPT_KIND_STARTED else _TERMINAL_KEYS
    )
    payload = parser.parse(raw)
    if _canonical_bytes(payload) != raw:
        raise LifecycleIntegrityError("receipt bytes are not canonical")
    event_hash = parser.verify_event_hash(payload)
    return payload, event_hash


def parse_started_and_verify(raw: bytes) -> tuple[dict[str, Any], str]:
    return _parse_and_verify(raw, RECEIPT_KIND_STARTED)


def parse_terminal_and_verify(raw: bytes) -> tuple[dict[str, Any], str]:
    return _parse_and_verify(raw, RECEIPT_KIND_TERMINAL)


# ---------------------------------------------------------------------------
# File name derivation
# ---------------------------------------------------------------------------


def _derive_receipt_file_names(
    transaction_id: str, operation_key: str
) -> tuple[str, str]:
    """Fixed domain-separated file names derived from the transaction and
    operation binding.  No caller text is embedded in any name."""
    digest = hashlib.sha256(
        f"{_FILE_NAME_DOMAIN}\x00{transaction_id}\x00{operation_key}".encode("utf-8")
    ).hexdigest()
    return f"{digest}.started.json", f"{digest}.terminal.json"


def _derived_filename_from_receipt(
    payload: dict[str, Any], kind: str
) -> str:
    """Recompute the expected canonical filename from a parsed receipt's
    binding fields and the declared ``kind``.

    Returns the expected filename (e.g. ``<hex64>.started.json``) so callers
    can verify the receipt is stored under its correct derived name.
    """
    transaction_id = payload["transactionId"]
    operation_key = payload["operationKey"]
    started_name, terminal_name = _derive_receipt_file_names(
        transaction_id, operation_key
    )
    if kind == RECEIPT_KIND_STARTED:
        return started_name
    return terminal_name


# ---------------------------------------------------------------------------
# Filesystem primitives
# ---------------------------------------------------------------------------


def _on_posix() -> bool:
    return os.name == "posix"


def _close_fd_best_effort(fd: int) -> None:
    """Close an fd without replacing an exception already being reported."""

    try:
        os.close(fd)
    except OSError:
        pass


def _close_fd_strict(fd: int, *, code: str, detail: str) -> None:
    """Close an fd or raise the caller's stable error classification."""

    try:
        os.close(fd)
    except OSError as exc:
        raise LifecycleReceiptError(code, f"{detail}: {exc}") from None


def _fsync_directory(path: str) -> None:
    """fsync a directory so a freshly published link survives a crash.

    On POSIX this is an acceptance requirement: open/fsync failures are
    converted to ``receipt-io-error``.  On non-POSIX platforms directory
    fsync is not available; this is a documented limitation.
    """
    if not _on_posix():
        return
    fd = -1
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError as exc:
        raise LifecycleReceiptError(
            "receipt-io-error", f"cannot open directory for fsync: {exc}"
        ) from None
    try:
        os.fsync(fd)
    except OSError as exc:
        _close_fd_best_effort(fd)
        raise LifecycleReceiptError(
            "receipt-io-error", f"cannot fsync directory: {exc}"
        ) from None
    _close_fd_strict(
        fd,
        code="receipt-io-error",
        detail="cannot close directory after fsync",
    )


def _check_published_posix_metadata(path: str, lst: os.stat_result) -> None:
    """POSIX-only metadata checks for a published receipt.

    Requires exact mode 0444, current owner, regular file, and nlink 1.
    """
    if lst.st_uid != os.geteuid():
        raise LifecycleIntegrityError(
            f"receipt is not owned by the current user: {path}"
        )
    # Require exact permission bits 0444 (S_ISREG verified by caller);
    # strip file-type bits and compare the permission bits directly.
    if lst.st_mode & 0o7777 != _PUBLISHED_MODE:
        raise LifecycleIntegrityError(
            f"receipt must have exact mode 0444: {path}"
        )
    if lst.st_nlink != 1:
        raise LifecycleIntegrityError(
            f"receipt must have exactly one hard link after publication: {path}"
        )
    if not stat.S_ISREG(lst.st_mode):
        raise LifecycleIntegrityError(
            f"receipt is not a regular file: {path}"
        )


# ---------------------------------------------------------------------------
# Filename validation patterns
# ---------------------------------------------------------------------------

# Receipt names: <64 lowercase hex>.started.json or <64 lowercase hex>.terminal.json
_RECEIPT_STARTED_RE = re.compile(r"^[0-9a-f]{64}\.started\.json$")
_RECEIPT_TERMINAL_RE = re.compile(r"^[0-9a-f]{64}\.terminal\.json$")

# Temp names: tmp-<32 lowercase hex>.receipt.json
_TEMP_NAME_RE = re.compile(r"^tmp-[0-9a-f]{32}\.receipt\.json$")


def _classify_name(name: str) -> str | None:
    """Classify a directory entry name.

    Returns ``'started'``, ``'terminal'``, ``'temp'``, or ``None`` for
    unrecognised names.
    """
    if _RECEIPT_STARTED_RE.fullmatch(name) is not None:
        return "started"
    if _RECEIPT_TERMINAL_RE.fullmatch(name) is not None:
        return "terminal"
    if _TEMP_NAME_RE.fullmatch(name) is not None:
        return "temp"
    return None


def _looks_like_receipt_name(name: str) -> bool:
    """Loose 'receipt-looking' test: recognises the canonical suffix even
    when the hex portion is malformed, so ``health()`` can count
    noncanonical receipt names as corrupt."""
    return name.endswith(".started.json") or name.endswith(".terminal.json")


def _looks_like_temp_name(name: str) -> bool:
    """Loose 'temp-looking' test: recognises the temp prefix/suffix even
    when the hex portion is malformed."""
    return name.startswith(_TEMP_PREFIX) and name.endswith(_TEMP_SUFFIX)


def _check_path_binding(
    payload: dict[str, Any],
    kind: str,
    actual_filename: str,
) -> None:
    """Reject a receipt whose derived canonical filename does not match
    the actual filename it is stored under.

    This enforces path/content binding: a valid receipt stored under a
    wrong name is treated as corrupt.  Because ``actual_filename`` is
    always the canonical name derived from the requested binding, this
    single comparison also rejects a receipt whose stored
    ``transactionId``/``operationKey`` do not match the requested path.
    """
    expected = _derived_filename_from_receipt(payload, kind)
    if expected != actual_filename:
        raise LifecycleIntegrityError(
            f"receipt stored under wrong filename: expected {expected!r}, "
            f"got {actual_filename!r}"
        )


# ---------------------------------------------------------------------------
# Health helpers
# ---------------------------------------------------------------------------

_MAX_HEALTH_ENTRIES = 4096


def _list_root_bounded(root: str) -> tuple[list[str], bool]:
    """Iterate at most ``_MAX_HEALTH_ENTRIES`` directory entries without
    materialising or sorting the full directory (lazy ``os.scandir``
    iteration, stopped at the bound).

    Returns ``(entries, truncated)``.
    """
    entries: list[str] = []
    truncated = False
    try:
        with os.scandir(root) as it:
            for entry in it:
                if len(entries) >= _MAX_HEALTH_ENTRIES:
                    truncated = True
                    break
                entries.append(entry.name)
    except OSError as exc:
        raise LifecycleReceiptError(
            "receipt-io-error", f"cannot list receipt root: {exc}"
        ) from None
    return entries, truncated


# ---------------------------------------------------------------------------
# Root identity helpers
# ---------------------------------------------------------------------------


def _get_root_identity(path: str) -> tuple[int, int]:
    """Return (dev, ino) for the root path using lstat (no symlink follow).
    Raises ``integrity`` on any problem."""
    try:
        lst = os.lstat(path)
    except FileNotFoundError:
        raise LifecycleIntegrityError(
            f"receipt root does not exist: {path}"
        ) from None
    except OSError as exc:
        raise LifecycleIntegrityError(
            f"cannot lstat receipt root: {exc}"
        ) from None
    if not stat.S_ISDIR(lst.st_mode):
        raise LifecycleIntegrityError(
            f"receipt root is not a directory: {path}"
        )
    return lst.st_dev, lst.st_ino


def _require_root_identity(
    path: str, expected_dev: int, expected_ino: int
) -> None:
    """Revalidate root directory identity at entry to every public method.

    Rejects a replacement root (different inode or device).
    """
    try:
        lst = os.lstat(path)
    except FileNotFoundError:
        raise LifecycleIntegrityError(
            f"receipt root does not exist: {path}"
        ) from None
    except OSError as exc:
        raise LifecycleIntegrityError(
            f"cannot lstat receipt root: {exc}"
        ) from None
    if not stat.S_ISDIR(lst.st_mode):
        raise LifecycleIntegrityError(
            f"receipt root is not a directory: {path}"
        )
    if (lst.st_dev, lst.st_ino) != (expected_dev, expected_ino):
        raise LifecycleIntegrityError(
            f"receipt root identity changed (possible replacement): {path}"
        )
    if _on_posix():
        if lst.st_uid != os.geteuid():
            raise LifecycleIntegrityError(
                f"receipt root is not owned by the current user: {path}"
            )
        if lst.st_mode & 0o077:
            raise LifecycleIntegrityError(
                f"receipt root must not grant group or other any access: {path}"
            )
        # Verify exact permission bits are 0700 (S_ISDIR already verified)
        if lst.st_mode & 0o7777 != _ROOT_MODE:
            raise LifecycleIntegrityError(
                f"receipt root must have exact mode 0700: {path}"
            )


# ---------------------------------------------------------------------------
# Filesystem primitives  (re-declared above)
# ---------------------------------------------------------------------------


def _unlink_temp_if_ours(
    temp_path: str, temp_dev: int, temp_ino: int
) -> bool:
    """Unlink ``temp_path`` only if lstat still shows the inode opened by
    this process.  Never unlinks an attacker-substituted path.

    Returns True when the path is gone (unlinked or already absent), False
    when a foreign entry occupies the path (left in place).
    Raises ``receipt-io-error`` when cleanup of the authentic temp fails.
    """
    try:
        lst = os.lstat(temp_path)
    except FileNotFoundError:
        return True  # already gone; nothing to do
    except OSError as exc:
        raise LifecycleReceiptError(
            "receipt-io-error", f"cannot lstat temp for cleanup: {exc}"
        ) from None
    if (lst.st_dev, lst.st_ino) != (temp_dev, temp_ino):
        return False  # foreign/substituted entry: never unlink
    try:
        os.unlink(temp_path)
    except FileNotFoundError:
        return True
    except OSError as exc:
        raise LifecycleReceiptError(
            "receipt-io-error", f"cannot remove temp file: {exc}"
        ) from None
    return True


def _write_and_publish_receipt(directory: str, final_name: str, data: bytes) -> None:
    """Create a unique same-directory temp file, write all bytes, fsync the
    file, chmod read-only on POSIX, publish via ``os.link(temp, final)``,
    unlink the temp, and fsync the parent directory on POSIX.

    Raises ``FileExistsError`` when the final name is already taken; the
    caller reads the winner and converges.  Never replaces or renames.

    Publication keeps the temp fd open through link, verifies inode identity
    before and after link, and only unlinks the authentic temp.
    """
    if len(data) > MAX_RECEIPT_BYTES:
        raise LifecycleReceiptError(
            "receipt-oversize", f"receipt would exceed {MAX_RECEIPT_BYTES} bytes"
        )
    temp_name = f"{_TEMP_PREFIX}{uuid.uuid4().hex}{_TEMP_SUFFIX}"
    temp_path = os.path.join(directory, temp_name)
    final_path = os.path.join(directory, final_name)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    for attr in ("O_CLOEXEC", "O_NOFOLLOW"):
        flags |= getattr(os, attr, 0)
    try:
        fd = os.open(temp_path, flags, _TEMP_MODE)
    except FileExistsError:  # pragma: no cover - uuid4 collision
        raise LifecycleReceiptError("receipt-temp-collision") from None
    except OSError as exc:
        raise LifecycleReceiptError(
            "receipt-io-error", f"cannot create temp receipt: {exc}"
        ) from None

    # Capture the opened temp fd's identity
    try:
        temp_fstat = os.fstat(fd)
        temp_dev = temp_fstat.st_dev
        temp_ino = temp_fstat.st_ino
    except OSError as exc:
        # Without a proven identity this temp is never unlinked (fail closed;
        # a leftover is counted by health() but never trusted or deleted).
        try:
            os.close(fd)
        except OSError:
            pass
        raise LifecycleReceiptError(
            "receipt-io-error", f"cannot stat temp fd: {exc}"
        ) from None

    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            if written == 0:
                raise LifecycleReceiptError(
                    "receipt-io-error",
                    "zero-byte write (I/O error) on temp receipt",
                )
            view = view[written:]
        os.fsync(fd)
        if _on_posix():
            os.fchmod(fd, _PUBLISHED_MODE)
            # fsync again after fchmod so the mode change is durable on POSIX
            os.fsync(fd)
    except BaseException:
        # Cleanup the failed temp only if the path still holds the inode this
        # process opened; never unlink an attacker-substituted path.
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            _unlink_temp_if_ours(temp_path, temp_dev, temp_ino)
        except LifecycleReceiptError:
            pass
        raise

    # Keep the temp fd open through link publication: the open fd pins the
    # inode this process created, so identity comparisons remain meaningful
    # even if the path is attacked.

    # Before linking: lstat temp path and verify it is still our inode/device
    try:
        pre_link_lst = os.lstat(temp_path)
    except OSError as exc:
        _close_fd_best_effort(fd)
        raise LifecycleReceiptError(
            "receipt-io-error", f"cannot lstat temp before link: {exc}"
        ) from None
    if (pre_link_lst.st_dev, pre_link_lst.st_ino) != (temp_dev, temp_ino):
        _close_fd_best_effort(fd)
        raise LifecycleIntegrityError(
            f"temp file inode changed before link (possible rebinding): "
            f"{temp_path}"
        )
    if not stat.S_ISREG(pre_link_lst.st_mode):
        _close_fd_best_effort(fd)
        raise LifecycleIntegrityError(
            f"temp is not a regular file before link: {temp_path}"
        )

    try:
        _publish_via_link(directory, temp_name, final_name)
    except BaseException:
        # On EEXIST (lost publication race) and on any other failure, remove
        # our temp link (guarded by identity) before propagating.
        try:
            _unlink_temp_if_ours(temp_path, temp_dev, temp_ino)
        except LifecycleReceiptError:
            pass
        _close_fd_best_effort(fd)
        raise

    # After successful link: lstat final path and require the same inode/device
    try:
        final_lst = os.lstat(final_path)
    except OSError as exc:
        try:
            _unlink_temp_if_ours(temp_path, temp_dev, temp_ino)
        except LifecycleReceiptError:
            pass
        _close_fd_best_effort(fd)
        raise LifecycleReceiptError(
            "receipt-io-error", f"cannot lstat final path after link: {exc}"
        ) from None
    if (final_lst.st_dev, final_lst.st_ino) != (temp_dev, temp_ino):
        try:
            _unlink_temp_if_ours(temp_path, temp_dev, temp_ino)
        except LifecycleReceiptError:
            pass
        _close_fd_best_effort(fd)
        raise LifecycleIntegrityError(
            f"final path is not the published temp inode: {final_path}"
        )
    if not stat.S_ISREG(final_lst.st_mode):
        try:
            _unlink_temp_if_ours(temp_path, temp_dev, temp_ino)
        except LifecycleReceiptError:
            pass
        _close_fd_best_effort(fd)
        raise LifecycleIntegrityError(
            f"final path is not a regular file after link: {final_path}"
        )

    # Remove the authentic temp after successful publication.  Fail closed
    # with a stable I/O error if cleanup of the authentic temp fails.
    try:
        substituted = not _unlink_temp_if_ours(temp_path, temp_dev, temp_ino)
    except BaseException:
        _close_fd_best_effort(fd)
        raise
    if substituted:
        _close_fd_best_effort(fd)
        raise LifecycleIntegrityError(
            f"temp path was substituted before cleanup; refusing to unlink "
            f"foreign path: {temp_path}"
        )
    _close_fd_strict(
        fd,
        code="receipt-io-error",
        detail="cannot close published temp receipt",
    )

    _fsync_directory(directory)


def _read_published_receipt(path: str, max_bytes: int = MAX_RECEIPT_BYTES) -> bytes:
    """Read a published receipt with lstat/open/fstat safety checks.

    Rejects symlinks, non-regular files, oversize files, inode substitution,
    and on POSIX the wrong owner, group/other-writable modes, and
    ``st_nlink != 1``.  A transient ``st_nlink == 2`` during another
    writer's publication window (link published, temp not yet removed) is
    retried a bounded number of times, then treated as integrity failure.
    """
    expected_identity: tuple[int, int] | None = None
    for attempt in range(_MAX_NLINK_RETRIES):
        last_attempt = attempt + 1 == _MAX_NLINK_RETRIES
        try:
            lst = os.lstat(path)
        except FileNotFoundError:
            raise LifecycleReceiptError(
                "receipt-missing", f"no receipt at {path}"
            ) from None
        except OSError as exc:
            raise LifecycleIntegrityError(
                f"cannot lstat receipt {path}: {exc}"
            ) from None
        if stat.S_ISLNK(lst.st_mode):
            raise LifecycleIntegrityError(f"receipt must not be a symlink: {path}")
        if not stat.S_ISREG(lst.st_mode):
            raise LifecycleIntegrityError(f"receipt is not a regular file: {path}")
        if lst.st_size <= 0 or lst.st_size > max_bytes:
            raise LifecycleIntegrityError(
                f"receipt size is outside the permitted range: {path}"
            )
        identity = (lst.st_dev, lst.st_ino)
        if expected_identity is None:
            expected_identity = identity
        elif identity != expected_identity:
            raise LifecycleIntegrityError(
                f"receipt inode changed while waiting for publication: {path}"
            )
        if _on_posix() and lst.st_nlink != 1:
            if last_attempt:
                raise LifecycleIntegrityError(
                    f"receipt must have exactly one hard link: {path}"
                )
            time.sleep(_NLINK_RETRY_SECONDS)
            continue

        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except OSError as exc:
            raise LifecycleIntegrityError(
                f"cannot open receipt {path}: {exc}"
            ) from None
        read_failure: BaseException | None = None
        try:
            fst = os.fstat(fd)
            if not stat.S_ISREG(fst.st_mode):
                raise LifecycleIntegrityError(
                    f"receipt is not a regular file after open: {path}"
                )
            if (fst.st_ino, fst.st_dev) != (lst.st_ino, lst.st_dev):
                raise LifecycleIntegrityError(
                    f"receipt inode changed between lstat and open: {path}"
                )
            if _on_posix() and fst.st_nlink != 1:
                if last_attempt:
                    raise LifecycleIntegrityError(
                        f"receipt must have exactly one hard link: {path}"
                    )
                time.sleep(_NLINK_RETRY_SECONDS)
                continue
            if _on_posix():
                _check_published_posix_metadata(path, fst)
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(fd, max_bytes + 1 - total)
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > max_bytes:
                    raise LifecycleIntegrityError(
                        f"receipt exceeds the maximum size: {path}"
                    )
        except BaseException as exc:
            read_failure = exc
            raise
        finally:
            if read_failure is None:
                _close_fd_strict(
                    fd,
                    code="receipt-io-error",
                    detail="cannot close published receipt",
                )
            else:
                _close_fd_best_effort(fd)
        return b"".join(chunks)
    raise LifecycleIntegrityError(f"receipt hard-link count did not settle: {path}")


def _publish_via_link(directory: str, temp_name: str, final_name: str) -> None:
    """Publish the temp file at ``final_name`` by hard link only.

    ``FileExistsError`` propagates to the caller, which converges by reading
    the winner.  Replace/rename is never used.
    """
    temp_path = os.path.join(directory, temp_name)
    final_path = os.path.join(directory, final_name)
    try:
        os.link(temp_path, final_path)
    except FileExistsError:
        raise
    except NotImplementedError as exc:
        raise LifecycleLinkUnsupportedError(
            f"hard links are not supported on this filesystem: {exc}"
        ) from None
    except OSError as exc:
        # e.g. EPERM, EXDEV, EOPNOTSUPP: fail closed rather than rename.
        raise LifecycleLinkUnsupportedError(
            f"hard-link publication failed ({exc}); refusing to fall back to "
            "replace/rename"
        ) from None


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class LifecycleReceiptStore:
    """Owner-private, append-only receipt store rooted at ``root``.

    ``root`` is created with POSIX mode 0700 and validated with a single
    lstat that never follows the final path component: it must be a real
    directory (not a symlink), owned by the current user, with exactly mode
    0700.  The directory's device/inode identity is recorded at construction
    and revalidated at entry to every public method; a replacement of the
    root (different device/inode, wrong type, changed owner or mode) is
    rejected as ``integrity``.  This is a bounded identity check: it detects
    a replacement that has already happened but cannot prevent a malicious
    replacement occurring after any given check.

    Publication is by hard link from a same-directory temp file only; there
    is no rename/replace fallback.
    """

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self._root = os.fspath(root)
        if not self._root:
            raise LifecycleReceiptError(
                "invalid-receipt-root", "root path must not be empty"
            )
        # Single lstat decides existence: never follows the final path
        # component, so a symlinked root is seen as a symlink (rejected),
        # and a dangling symlink is seen as a symlink too.
        try:
            os.lstat(self._root)
            exists = True
        except FileNotFoundError:
            exists = False
        except OSError as exc:
            raise LifecycleReceiptError(
                "invalid-receipt-root", f"cannot lstat root: {exc}"
            ) from None
        if exists:
            self._validate_existing_root()
        else:
            self._create_root()
        # Record and bind to root directory identity
        self._root_dev, self._root_ino = _get_root_identity(self._root)

    # -- construction and validation --

    def _validate_existing_root(self) -> None:
        """Validate an existing root with a single lstat (no symlink follow).

        Requires an exact owner-private POSIX directory mode 0700 and current
        owner.  A symlink root is rejected.
        """
        try:
            lst = os.lstat(self._root)
        except FileNotFoundError:
            raise LifecycleReceiptError(
                "invalid-receipt-root", f"root path does not exist: {self._root}"
            ) from None
        except OSError as exc:
            raise LifecycleReceiptError(
                "invalid-receipt-root", f"cannot lstat root: {exc}"
            ) from None

        if stat.S_ISLNK(lst.st_mode):
            raise LifecycleReceiptError(
                "invalid-receipt-root", "root must not be a symlink"
            )
        if not stat.S_ISDIR(lst.st_mode):
            raise LifecycleReceiptError(
                "invalid-receipt-root", "root must be a directory"
            )
        if _on_posix():
            if lst.st_uid != os.geteuid():
                raise LifecycleReceiptError(
                    "invalid-receipt-root",
                    f"root is owned by uid {lst.st_uid}, not the current user",
                )
            # S_ISDIR was already verified; require exact permission bits 0700.
            if lst.st_mode & 0o7777 != _ROOT_MODE:
                raise LifecycleReceiptError(
                    "invalid-receipt-root",
                    "root must have exact mode 0700",
                )

    def _create_root(self) -> None:
        """Create root directory, avoiding chmod-follow-symlink race.

        On POSIX, open the newly created directory with O_DIRECTORY and
        O_NOFOLLOW where available, fchmod the fd to 0700, fsync it, then
        validate fstat/lstat identity.
        """
        parent = os.path.dirname(os.path.abspath(self._root))
        if not os.path.isdir(parent):
            raise LifecycleReceiptError(
                "invalid-receipt-root", f"parent directory does not exist: {parent}"
            )
        try:
            os.mkdir(self._root, _ROOT_MODE)
        except FileExistsError:
            self._validate_existing_root()
            return
        except OSError as exc:
            raise LifecycleReceiptError(
                "invalid-receipt-root", f"cannot create root: {exc}"
            ) from None

        if _on_posix():
            # Open via fd with O_DIRECTORY | O_NOFOLLOW where available, then
            # fchmod the fd so a swapped-in symlink is never followed by the
            # chmod.  The fstat/lstat identity validation below bounds the
            # window: it detects replacement that has already happened but
            # cannot prevent a malicious replacement after this final check.
            flags = os.O_RDONLY
            for attr in ("O_DIRECTORY", "O_NOFOLLOW"):
                flags |= getattr(os, attr, 0)
            try:
                fd = os.open(self._root, flags)
            except OSError as exc:
                raise LifecycleReceiptError(
                    "invalid-receipt-root",
                    f"cannot open root directory fd: {exc}",
                ) from None
            try:
                fst = os.fstat(fd)
                if not stat.S_ISDIR(fst.st_mode):
                    raise LifecycleReceiptError(
                        "invalid-receipt-root",
                        "root is not a directory after open",
                    )
                os.fchmod(fd, _ROOT_MODE)
                os.fsync(fd)
                lst = os.lstat(self._root)
                if (lst.st_dev, lst.st_ino) != (fst.st_dev, fst.st_ino):
                    raise LifecycleReceiptError(
                        "invalid-receipt-root",
                        "root directory was replaced between open and lstat",
                    )
            except OSError as exc:
                _close_fd_best_effort(fd)
                raise LifecycleReceiptError(
                    "invalid-receipt-root",
                    f"cannot secure root directory: {exc}",
                ) from None
            except BaseException:
                _close_fd_best_effort(fd)
                raise
            _close_fd_strict(
                fd,
                code="invalid-receipt-root",
                detail="cannot close secured root directory",
            )
        else:
            try:
                os.chmod(self._root, _ROOT_MODE)
            except OSError as exc:
                raise LifecycleReceiptError(
                    "invalid-receipt-root",
                    f"cannot set root permissions: {exc}",
                ) from None
        self._validate_existing_root()

    def _require_root(self) -> None:
        """Revalidate root identity at entry to every public method."""
        _require_root_identity(self._root, self._root_dev, self._root_ino)

    # -- helpers --

    @property
    def root(self) -> str:
        return self._root

    def _path_for(self, name: str) -> str:
        return os.path.join(self._root, name)

    def _final_exists(self, name: str) -> bool:
        """Existence probe that never follows symlinks and acknowledges only
        regular files.  A symlink or other non-regular entry occupying a
        receipt name is tampering and raises ``integrity``: reporting the
        receipt ``absent`` could invite a replay.  Publication correctness
        never depends on this probe; it merely avoids work when the file is
        known present."""
        try:
            lst = os.lstat(self._path_for(name))
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise LifecycleIntegrityError(
                f"cannot lstat receipt name {name}: {exc}"
            ) from None
        if stat.S_ISLNK(lst.st_mode) or not stat.S_ISREG(lst.st_mode):
            raise LifecycleIntegrityError(
                f"a non-regular entry occupies a receipt name: {name}"
            )
        return True

    def _read_started(self, name: str) -> tuple[dict[str, Any], str]:
        return parse_started_and_verify(_read_published_receipt(self._path_for(name)))

    def _read_terminal(self, name: str) -> tuple[dict[str, Any], str]:
        return parse_terminal_and_verify(_read_published_receipt(self._path_for(name)))

    @staticmethod
    def _bindings_match(
        payload: dict[str, Any],
        transaction_id: str,
        plan_hash: str,
        operation_key: str,
        request_hash: str,
        service_ids: tuple[str, ...],
    ) -> bool:
        return (
            payload["transactionId"] == transaction_id
            and payload["planHash"] == plan_hash
            and payload["operationKey"] == operation_key
            and payload["requestHash"] == request_hash
            and tuple(payload["serviceIds"]) == service_ids
        )

    @staticmethod
    def _started_record(payload: dict[str, Any]) -> StartedReceipt:
        return StartedReceipt(
            transaction_id=payload["transactionId"],
            plan_hash=payload["planHash"],
            operation_key=payload["operationKey"],
            request_hash=payload["requestHash"],
            service_ids=tuple(payload["serviceIds"]),
            event_hash=payload["eventHash"],
        )

    @staticmethod
    def _terminal_record(payload: dict[str, Any]) -> TerminalReceipt:
        return TerminalReceipt(
            transaction_id=payload["transactionId"],
            plan_hash=payload["planHash"],
            operation_key=payload["operationKey"],
            request_hash=payload["requestHash"],
            service_ids=tuple(payload["serviceIds"]),
            outcome=payload["outcome"],
            evidence_hash=payload["evidenceHash"],
            started_event_hash=payload["startedEventHash"],
            event_hash=payload["eventHash"],
        )

    # -- public API --

    def begin(
        self,
        transaction_id: str,
        plan_hash: str,
        operation_key: str,
        request_hash: str,
        service_ids: Any,
    ) -> StartedReceipt | TerminalReceipt:
        """Create or converge on the one immutable started receipt.

        * Exact repeat of an existing started receipt is idempotent.
        * If a matching terminal receipt already exists, the terminal
          snapshot is returned: the operation is terminally finished.
        * A terminal without its matching started receipt is integrity
          failure: orphan terminals are never trusted.
        * ``begin()`` seeing a terminal requires/reads the matching started
          receipt, verifies all transaction/operation/plan/request/service
          bindings and the ``startedEventHash`` chain, then returns the
          terminal.
        * Divergent existing data raises ``conflict``.
        * Corrupt existing data raises ``integrity`` and is never overwritten
          or repaired.
        """
        self._require_root()
        transaction_id = _validate_transaction_id(transaction_id)
        plan_hash = _validate_hash64(plan_hash, "plan-hash")
        request_hash = _validate_hash64(request_hash, "request-hash")
        service_ids = _validate_service_ids(service_ids)
        operation_key = _validate_operation_key(operation_key, service_ids)

        started_name, terminal_name = _derive_receipt_file_names(
            transaction_id, operation_key
        )

        if self._final_exists(terminal_name):
            terminal_payload, _ = self._read_terminal(terminal_name)
            _check_path_binding(
                terminal_payload, RECEIPT_KIND_TERMINAL, terminal_name
            )
            # Orphan terminal: must have a started receipt
            if not self._final_exists(started_name):
                raise LifecycleIntegrityError(
                    "terminal receipt exists without its started receipt; "
                    "orphan terminal is not trusted"
                )
            started_payload, started_event_hash = self._read_started(
                started_name
            )
            _check_path_binding(
                started_payload, RECEIPT_KIND_STARTED, started_name
            )
            # Verify startedEventHash chain
            if terminal_payload["startedEventHash"] != started_event_hash:
                raise LifecycleIntegrityError(
                    "terminal receipt chains a different started event"
                )
            # Durable-vs-durable: if the two stored files contradict each
            # other, the store is inconsistent -> integrity, regardless of
            # what the caller sent.
            if not self._bindings_match(
                started_payload,
                terminal_payload["transactionId"],
                terminal_payload["planHash"],
                terminal_payload["operationKey"],
                terminal_payload["requestHash"],
                tuple(terminal_payload["serviceIds"]),
            ):
                raise LifecycleIntegrityError(
                    "started and terminal receipts bind different operations; "
                    "durable files contradict each other"
                )
            # Durable-vs-caller: a terminal whose binding diverges from the
            # caller's request is a conflict.
            if not self._bindings_match(
                terminal_payload, transaction_id, plan_hash, operation_key,
                request_hash, service_ids,
            ):
                raise LifecycleConflictError(
                    "existing terminal receipt binds a different operation"
                )
            return self._terminal_record(terminal_payload)

        if self._final_exists(started_name):
            payload, _ = self._read_started(started_name)
            if not self._bindings_match(
                payload, transaction_id, plan_hash, operation_key,
                request_hash, service_ids,
            ):
                raise LifecycleConflictError(
                    "existing started receipt binds a different operation"
                )
            return self._started_record(payload)

        payload = {
            "schemaVersion": SCHEMA_VERSION,
            "kind": RECEIPT_KIND_STARTED,
            "transactionId": transaction_id,
            "planHash": plan_hash,
            "operationKey": operation_key,
            "requestHash": request_hash,
            "serviceIds": list(service_ids),
        }
        payload["eventHash"] = _compute_event_hash(payload)
        data = _canonical_bytes(payload)

        try:
            _write_and_publish_receipt(self._root, started_name, data)
        except FileExistsError:
            # EEXIST from the publication link is authoritative: converge on
            # the winner or conflict.
            winner, _ = self._read_started(started_name)
            if not self._bindings_match(
                winner, transaction_id, plan_hash, operation_key,
                request_hash, service_ids,
            ):
                raise LifecycleConflictError(
                    "a divergent started receipt was published concurrently"
                ) from None
            return self._started_record(winner)

        return self._started_record(payload)

    def finish(
        self,
        transaction_id: str,
        plan_hash: str,
        operation_key: str,
        request_hash: str,
        service_ids: Any,
        outcome: str,
        evidence_hash: str,
    ) -> TerminalReceipt:
        """Create or converge on the one immutable terminal receipt.

        Requires the exact valid started receipt and chains its
        ``eventHash`` as ``startedEventHash``.  Exact repeat is idempotent;
        divergent terminal data raises ``conflict``; a stored terminal whose
        chain does not match the started receipt raises ``integrity``;
        creating a terminal without a valid matching started receipt is
        rejected.
        """
        self._require_root()
        transaction_id = _validate_transaction_id(transaction_id)
        plan_hash = _validate_hash64(plan_hash, "plan-hash")
        request_hash = _validate_hash64(request_hash, "request-hash")
        service_ids = _validate_service_ids(service_ids)
        operation_key = _validate_operation_key(operation_key, service_ids)
        evidence_hash = _validate_hash64(evidence_hash, "evidence-hash")
        if outcome not in ("completed", "failed"):
            raise LifecycleReceiptError(
                "invalid-outcome", "outcome must be 'completed' or 'failed'"
            )

        started_name, terminal_name = _derive_receipt_file_names(
            transaction_id, operation_key
        )

        if not self._final_exists(started_name):
            raise LifecycleReceiptError(
                "started-receipt-required",
                "cannot finish an operation that has no started receipt",
            )
        started_payload, started_event_hash = self._read_started(started_name)
        _check_path_binding(started_payload, RECEIPT_KIND_STARTED, started_name)
        if not self._bindings_match(
            started_payload, transaction_id, plan_hash, operation_key,
            request_hash, service_ids,
        ):
            raise LifecycleConflictError("started receipt binds a different operation")

        if self._final_exists(terminal_name):
            payload, _ = self._read_terminal(terminal_name)
            _check_path_binding(payload, RECEIPT_KIND_TERMINAL, terminal_name)
            if payload["startedEventHash"] != started_event_hash:
                raise LifecycleIntegrityError(
                    "existing terminal receipt chains a different started event"
                )
            if not self._terminal_matches(
                payload, transaction_id, plan_hash, operation_key,
                request_hash, service_ids, outcome, evidence_hash,
            ):
                raise LifecycleConflictError(
                    "existing terminal receipt binds a different outcome"
                )
            return self._terminal_record(payload)

        payload = {
            "schemaVersion": SCHEMA_VERSION,
            "kind": RECEIPT_KIND_TERMINAL,
            "transactionId": transaction_id,
            "planHash": plan_hash,
            "operationKey": operation_key,
            "requestHash": request_hash,
            "serviceIds": list(service_ids),
            "outcome": outcome,
            "evidenceHash": evidence_hash,
            "startedEventHash": started_event_hash,
        }
        payload["eventHash"] = _compute_event_hash(payload)
        data = _canonical_bytes(payload)

        try:
            _write_and_publish_receipt(self._root, terminal_name, data)
        except FileExistsError:
            winner, _ = self._read_terminal(terminal_name)
            _check_path_binding(winner, RECEIPT_KIND_TERMINAL, terminal_name)
            if winner["startedEventHash"] != started_event_hash:
                raise LifecycleIntegrityError(
                    "a terminal receipt chaining a different started event was "
                    "published concurrently"
                ) from None
            if not self._terminal_matches(
                winner, transaction_id, plan_hash, operation_key,
                request_hash, service_ids, outcome, evidence_hash,
            ):
                raise LifecycleConflictError(
                    "a divergent terminal receipt was published concurrently"
                ) from None
            return self._terminal_record(winner)

        return self._terminal_record(payload)

    def snapshot(
        self,
        transaction_id: str,
        operation_key: str,
    ) -> LifecycleSnapshot:
        """Return a bounded immutable snapshot of one operation's state.

        The state is exactly one of ``absent``, ``started``, ``completed``,
        or ``failed``.  ``started`` alone is explicitly *unresolved*: this
        store never replays, fails, or succeeds an operation on its own.
        Temp leftovers are ignored.

        Orphan terminals (terminal without its started receipt) raise
        ``integrity``.  Path/content binding is verified: stored
        ``transactionId``/``operationKey`` must match the requested path,
        and terminal and started binding fields must match.
        """
        self._require_root()
        transaction_id = _validate_transaction_id(transaction_id)
        operation_key = _validate_operation_key(operation_key, None)

        started_name, terminal_name = _derive_receipt_file_names(
            transaction_id, operation_key
        )

        started: StartedReceipt | None = None
        started_payload: dict[str, Any] | None = None
        if self._final_exists(started_name):
            started_payload, _ = self._read_started(started_name)
            # Path/content binding: the stored transactionId/operationKey
            # must match the requested path (the name was derived from it)
            # and the receipt's own binding must match its filename.
            _check_path_binding(started_payload, RECEIPT_KIND_STARTED, started_name)
            started = self._started_record(started_payload)

        if self._final_exists(terminal_name):
            # Orphan terminal: must have started receipt
            if started_payload is None:
                if not self._final_exists(started_name):
                    raise LifecycleIntegrityError(
                        "terminal receipt exists without its started receipt; "
                        "orphan terminal is not trusted"
                    )
                started_payload, _ = self._read_started(started_name)
                _check_path_binding(
                    started_payload, RECEIPT_KIND_STARTED, started_name
                )
                started = self._started_record(started_payload)

            terminal_payload, _ = self._read_terminal(terminal_name)
            _check_path_binding(
                terminal_payload, RECEIPT_KIND_TERMINAL, terminal_name
            )
            if terminal_payload["startedEventHash"] != started.event_hash:
                raise LifecycleIntegrityError(
                    "terminal receipt chains a different started event"
                )
            # Durable-vs-durable: terminal and started must agree on every
            # binding field, not only the hash chain.
            if not self._bindings_match(
                started_payload,
                terminal_payload["transactionId"],
                terminal_payload["planHash"],
                terminal_payload["operationKey"],
                terminal_payload["requestHash"],
                tuple(terminal_payload["serviceIds"]),
            ):
                raise LifecycleIntegrityError(
                    "terminal and started receipts have divergent bindings"
                )
            terminal = self._terminal_record(terminal_payload)
            return LifecycleSnapshot(
                transaction_id=transaction_id,
                operation_key=operation_key,
                state=terminal.outcome,
                started_receipt=started,
                terminal_receipt=terminal,
            )

        if started is not None:
            return LifecycleSnapshot(
                transaction_id=transaction_id,
                operation_key=operation_key,
                state="started",
                started_receipt=started,
                terminal_receipt=None,
            )
        return LifecycleSnapshot(
            transaction_id=transaction_id,
            operation_key=operation_key,
            state="absent",
            started_receipt=None,
            terminal_receipt=None,
        )

    def health(self) -> dict[str, Any]:
        """Bounded health report: receipt, temp, corrupt counts plus
        scan metadata.

        Scans at most ``_MAX_HEALTH_ENTRIES`` (4096) directory entries without
        materialising or sorting the full directory.  Returns
        ``scannedEntryCount`` and ``truncated`` alongside the three existing
        counts.

        Receipt names must match exactly ``<64 lowercase hex>.started.json``
        or ``<64 lowercase hex>.terminal.json``.  Temp names must match
        exactly ``tmp-<32 lowercase hex>.receipt.json``.  Noncanonical names
        or valid receipt content whose derived filename does not equal the
        actual filename are treated as corrupt.

        Deletes nothing.  Temp leftovers are counted here but ignored by
        ``snapshot()``.  Count lstat failures for receipt-looking names as
        corrupt.

        ``receiptCount`` counts canonical receipt-name entries encountered;
        ``corruptCount`` is an overlapping integrity count, not a disjoint
        category. ``status == 'ok'`` means the bounded scan completed, not
        that ``corruptCount`` is zero.
        """
        self._require_root()
        receipt_count = 0
        temp_count = 0
        corrupt_count = 0
        entries, truncated = _list_root_bounded(self._root)
        scanned = len(entries)
        for name in entries:
            kind = _classify_name(name)
            if kind is None:
                if _looks_like_receipt_name(name) or _looks_like_temp_name(name):
                    # A receipt- or temp-looking name that does not match the
                    # exact canonical pattern is noncanonical: corrupt.
                    corrupt_count += 1
                # Other unrecognised names are ignored.
                continue
            if kind == "temp":
                temp_count += 1
                continue

            path = os.path.join(self._root, name)
            try:
                lst = os.lstat(path)
            except OSError:
                # lstat failure for a receipt-looking name counts as corrupt
                corrupt_count += 1
                continue
            if stat.S_ISLNK(lst.st_mode) or not stat.S_ISREG(lst.st_mode):
                corrupt_count += 1
                continue
            is_started = kind == "started"
            receipt_count += 1
            try:
                raw = _read_published_receipt(path)
                if is_started:
                    payload, _ = parse_started_and_verify(raw)
                else:
                    payload, _ = parse_terminal_and_verify(raw)
                # Verify path/content binding: derived filename must match
                expected = _derived_filename_from_receipt(
                    payload, RECEIPT_KIND_STARTED if is_started else RECEIPT_KIND_TERMINAL
                )
                if expected != name:
                    corrupt_count += 1
            except LifecycleReceiptError:
                corrupt_count += 1
        return {
            "status": "ok",
            "receiptCount": receipt_count,
            "tempCount": temp_count,
            "corruptCount": corrupt_count,
            "scannedEntryCount": scanned,
            "truncated": truncated,
        }

    # -- convergence helper --

    @classmethod
    def _terminal_matches(
        cls,
        payload: dict[str, Any],
        transaction_id: str,
        plan_hash: str,
        operation_key: str,
        request_hash: str,
        service_ids: tuple[str, ...],
        outcome: str,
        evidence_hash: str,
    ) -> bool:
        return (
            cls._bindings_match(
                payload, transaction_id, plan_hash, operation_key,
                request_hash, service_ids,
            )
            and payload["outcome"] == outcome
            and payload["evidenceHash"] == evidence_hash
        )
