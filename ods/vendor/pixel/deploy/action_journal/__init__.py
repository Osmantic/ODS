"""Durable, content-free exactly-once journal for bounded external actions."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows compatibility
    fcntl = None


BOUNDARY = (
    "Content-free append-only custody for one bounded external action. It proves local "
    "state transitions and retry suppression, not provider acceptance, semantic correctness, "
    "operator approval, or completion without a terminal provider-bound observation."
)
UNCERTAIN_OUTCOME_POLICY = "reconcile-before-retry-no-false-success"
ACTION_ID = re.compile(r"^[a-z][a-z0-9-]{1,31}-[0-9]{13}-[a-f0-9]{8,32}$")
NAME = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")
EVENT_NAME = re.compile(r"^([0-9]{6})\.json$")
EVENT_TEMP = re.compile(r"^\.[0-9]{6}\.[0-9]+\.tmp$")
STATES = {"proposed", "submitting", "unknown", "reconciling", "succeeded", "failed", "canceled"}
MODES = {"provider-idempotency-key", "deterministic-provider-object-id", "provider-reconciliation-marker", "internal-nonreplay"}
TERMINAL = {"succeeded", "failed", "canceled"}
TRANSITIONS = {
    "proposed": {"submitting", "failed", "canceled"},
    "submitting": {"unknown", "succeeded", "failed"},
    "unknown": {"reconciling"},
    "reconciling": {"unknown", "succeeded", "failed"},
    "succeeded": set(),
    "failed": set(),
    "canceled": set(),
}
EVENT_KEYS = {
    "schemaVersion", "kind", "actionId", "sequence", "previousEventSha256", "state",
    "recordedAt", "connector", "operation", "proposalSha256", "idempotencyKeySha256",
    "idempotencyMode", "providerTargetSha256", "attempt", "reasonCode",
    "observationSha256", "retryAllowed", "boundary",
}
_thread_locks: dict[str, threading.Lock] = {}
_thread_locks_guard = threading.Lock()


class JournalError(RuntimeError):
    """The journal is malformed or a requested transition is unsafe."""


def sha256(value: bytes | str) -> str:
    payload = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(payload).hexdigest()


def _instant(now: datetime | None = None) -> str:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise JournalError("journal time must include a timezone")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("utf-8")


def _sync_directory(path: Path) -> None:
    if os.name == "nt":  # Windows cannot fsync a directory descriptor.
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _secure_directory(path: Path, *, create: bool) -> None:
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise JournalError(f"journal directory is not a real directory: {path}")
    if os.name != "nt" and info.st_mode & 0o077:
        raise JournalError(f"journal directory is not owner-private: {path}")


def _read_event(path: Path) -> tuple[dict[str, Any], bytes]:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 65536:
        raise JournalError("journal event is not a bounded singly linked regular file")
    if os.name != "nt" and info.st_mode & 0o077:
        raise JournalError("journal event is not owner-private")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino, opened.st_size) != (info.st_dev, info.st_ino, info.st_size):
            raise JournalError("journal event changed during secure open")
        payload = bytearray()
        while True:
            chunk = os.read(descriptor, min(65537 - len(payload), 16384))
            if not chunk:
                break
            payload.extend(chunk)
            if len(payload) > 65536:
                raise JournalError("journal event exceeds its size limit")
        after = os.fstat(descriptor)
        if (
            (after.st_dev, after.st_ino, after.st_size) != (opened.st_dev, opened.st_ino, opened.st_size)
            or getattr(after, "st_mtime_ns", None) != getattr(opened, "st_mtime_ns", None)
            or len(payload) != opened.st_size
        ):
            raise JournalError("journal event changed while it was read")
    finally:
        os.close(descriptor)
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (UnicodeError, ValueError) as error:
        raise JournalError("journal event is not strict UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise JournalError("journal event is not an object")
    return value, bytes(payload)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise JournalError(f"journal event contains duplicate key: {key}")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise JournalError(f"journal event contains non-finite number: {value}")


def _validate_event(value: dict[str, Any], *, action_id: str, sequence: int, previous: str | None) -> None:
    if set(value) != EVENT_KEYS:
        raise JournalError("journal event has missing or unknown fields")
    if value["schemaVersion"] != 1 or value["kind"] != "pixel-external-action-journal-event" or value["boundary"] != BOUNDARY:
        raise JournalError("journal event header is invalid")
    if value["actionId"] != action_id or value["sequence"] != sequence or value["previousEventSha256"] != previous:
        raise JournalError("journal event identity or hash chain is invalid")
    if value["state"] not in STATES or not NAME.fullmatch(value["connector"] or "") or not NAME.fullmatch(value["operation"] or ""):
        raise JournalError("journal event action metadata is invalid")
    if value["idempotencyMode"] not in MODES or value["attempt"] != 1:
        raise JournalError("journal idempotency contract is invalid")
    for key in ("proposalSha256", "idempotencyKeySha256", "providerTargetSha256"):
        if not isinstance(value[key], str) or not SHA256.fullmatch(value[key]):
            raise JournalError(f"journal {key} is invalid")
    if value["observationSha256"] is not None and (not isinstance(value["observationSha256"], str) or not SHA256.fullmatch(value["observationSha256"])):
        raise JournalError("journal observation identity is invalid")
    if not isinstance(value["reasonCode"], str) or not NAME.fullmatch(value["reasonCode"]):
        raise JournalError("journal reason code is invalid")
    try:
        timestamp = datetime.fromisoformat(str(value["recordedAt"]).replace("Z", "+00:00"))
    except ValueError as error:
        raise JournalError("journal event time is invalid") from error
    if timestamp.tzinfo is None:
        raise JournalError("journal event time is not timezone-aware")
    expected_retry = value["state"] == "proposed"
    if value["retryAllowed"] is not expected_retry:
        raise JournalError("journal retry decision contradicts its state")


class ExternalActionJournal:
    """Append-only action state machine whose ambiguous states never permit replay."""

    def __init__(self, root: str | Path, *, clock: Callable[[], datetime] | None = None) -> None:
        self.root = Path(root)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        existed = self.root.exists()
        _secure_directory(self.root, create=True)
        if not existed:
            _sync_directory(self.root.parent)

    @contextmanager
    def _locked(self, action_id: str, *, create: bool) -> Iterator[Path]:
        if not ACTION_ID.fullmatch(action_id):
            raise JournalError("external action ID is unsafe")
        action = self.root / action_id
        if create:
            existed = action.exists()
            action.mkdir(mode=0o700, exist_ok=True)
            if not existed:
                _sync_directory(self.root)
        _secure_directory(action, create=False)
        lock_path = action / ".lock"
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        lock_info = os.fstat(descriptor)
        if not stat.S_ISREG(lock_info.st_mode) or lock_info.st_nlink != 1 or (os.name != "nt" and lock_info.st_mode & 0o077):
            os.close(descriptor)
            raise JournalError("action journal lock is not a private singly linked regular file")
        key = str(action.resolve())
        with _thread_locks_guard:
            thread_lock = _thread_locks.setdefault(key, threading.Lock())
        thread_lock.acquire()
        try:
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield action
        finally:
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
            thread_lock.release()

    def _events(self, action: Path, action_id: str) -> list[tuple[dict[str, Any], str]]:
        files: list[tuple[int, Path]] = []
        for entry in action.iterdir():
            if entry.name in (".lock", ".head") or entry.name.startswith(".head.") or EVENT_TEMP.fullmatch(entry.name):
                continue
            match = EVENT_NAME.fullmatch(entry.name)
            if match is None:
                raise JournalError("action journal contains an unexpected entry")
            files.append((int(match.group(1)), entry))
        files.sort()
        events: list[tuple[dict[str, Any], str]] = []
        previous: str | None = None
        metadata: tuple[Any, ...] | None = None
        prior_state: str | None = None
        for expected, (sequence, path) in enumerate(files):
            if sequence != expected:
                raise JournalError("action journal sequence has a gap or duplicate")
            value, payload = _read_event(path)
            _validate_event(value, action_id=action_id, sequence=sequence, previous=previous)
            current_metadata = tuple(value[key] for key in (
                "connector", "operation", "proposalSha256", "idempotencyKeySha256",
                "idempotencyMode", "providerTargetSha256", "attempt",
            ))
            if metadata is None:
                if value["state"] != "proposed":
                    raise JournalError("action journal does not begin with proposed")
                metadata = current_metadata
            elif current_metadata != metadata:
                raise JournalError("action journal metadata changed after proposal")
            if prior_state is not None and value["state"] not in TRANSITIONS[prior_state]:
                raise JournalError("action journal contains an invalid state transition")
            previous = sha256(payload)
            prior_state = value["state"]
            events.append((value, previous))
        # Durable head anchor: the present chain must reach the highest sequence ever committed.
        # Removing tail events leaves a hash-chain-valid prefix that would otherwise read as an
        # earlier, retryable state (a duplicate-effect path); a shorter chain fails closed. A
        # present chain AHEAD of the head is a crash between event write and head update and is
        # accepted, since that last event is itself durable and chain-valid.
        head = self._read_head(action)
        if events:
            if head is None:
                raise JournalError("action journal head marker is missing")
            head_sequence, head_hash = head
            last_sequence = events[-1][0]["sequence"]
            last_hash = events[-1][1]
            if last_sequence < head_sequence or (last_sequence == head_sequence and last_hash != head_hash):
                raise JournalError("action journal is truncated or tampered: present events do not reach the recorded head")
        elif head is not None:
            raise JournalError("action journal head marker exists without any events")
        return events

    def _append(self, action: Path, value: dict[str, Any]) -> dict[str, Any]:
        # Atomic event materialization: write the bytes into a private temp, fsync them, then
        # rename the temp into its committed NNNNNN.json name. A crash mid-write can therefore
        # only ever leave a skipped .tmp file, never a partial NNNNNN.json — a partial committed
        # event would make the whole chain unreadable and wedge the action's status forever. The
        # rename is the commit point; if it completes but the head write below does not, recovery
        # accepts the durable, chain-valid event as the head-ahead case. Mirrors _write_head.
        path = action / f"{value['sequence']:06d}.json"
        temporary = action / f".{value['sequence']:06d}.{os.getpid()}.tmp"
        payload = _canonical(value)
        digest = sha256(payload)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            if path.exists():
                raise JournalError("action journal event already exists for this sequence")
            os.rename(temporary, path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        _sync_directory(action)
        self._write_head(action, value["sequence"], digest)
        return {**value, "eventSha256": digest}

    def _write_head(self, action: Path, sequence: int, event_sha256: str) -> None:
        payload = _canonical({"eventSha256": event_sha256, "sequence": sequence})
        temporary = action / f".head.{os.getpid()}.tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, action / ".head")
        finally:
            temporary.unlink(missing_ok=True)
        _sync_directory(action)

    def _read_head(self, action: Path) -> tuple[int, str] | None:
        try:
            raw = (action / ".head").read_bytes()
        except FileNotFoundError:
            return None
        try:
            value = json.loads(raw)
        except ValueError as error:
            raise JournalError("action journal head marker is not valid JSON") from error
        if (
            not isinstance(value, dict) or set(value) != {"eventSha256", "sequence"}
            or type(value["sequence"]) is not int or value["sequence"] < 0
            or not isinstance(value["eventSha256"], str) or re.fullmatch(r"[a-f0-9]{64}", value["eventSha256"]) is None
        ):
            raise JournalError("action journal head marker is malformed")
        return value["sequence"], value["eventSha256"]

    def propose(
        self, *, action_id: str, connector: str, operation: str, proposal_sha256: str,
        idempotency_key_sha256: str, idempotency_mode: str, provider_target_sha256: str,
    ) -> dict[str, Any]:
        with self._locked(action_id, create=True) as action:
            events = self._events(action, action_id)
            wanted = (connector, operation, proposal_sha256, idempotency_key_sha256, idempotency_mode, provider_target_sha256, 1)
            if events:
                observed = tuple(events[0][0][key] for key in (
                    "connector", "operation", "proposalSha256", "idempotencyKeySha256",
                    "idempotencyMode", "providerTargetSha256", "attempt",
                ))
                if observed != wanted:
                    raise JournalError("existing action journal belongs to different exact inputs")
                return {**events[-1][0], "eventSha256": events[-1][1]}
            value = self._value(
                action_id=action_id, sequence=0, previous=None, state="proposed", reason="proposal-bound",
                connector=connector, operation=operation, proposal_sha256=proposal_sha256,
                idempotency_key_sha256=idempotency_key_sha256, idempotency_mode=idempotency_mode,
                provider_target_sha256=provider_target_sha256, observation_sha256=None,
            )
            _validate_event(value, action_id=action_id, sequence=0, previous=None)
            return self._append(action, value)

    def _value(
        self, *, action_id: str, sequence: int, previous: str | None, state: str, reason: str,
        connector: str, operation: str, proposal_sha256: str, idempotency_key_sha256: str,
        idempotency_mode: str, provider_target_sha256: str, observation_sha256: str | None,
    ) -> dict[str, Any]:
        return {
            "schemaVersion": 1, "kind": "pixel-external-action-journal-event", "actionId": action_id,
            "sequence": sequence, "previousEventSha256": previous, "state": state,
            "recordedAt": _instant(self.clock()), "connector": connector, "operation": operation,
            "proposalSha256": proposal_sha256, "idempotencyKeySha256": idempotency_key_sha256,
            "idempotencyMode": idempotency_mode, "providerTargetSha256": provider_target_sha256,
            "attempt": 1, "reasonCode": reason, "observationSha256": observation_sha256,
            "retryAllowed": state == "proposed", "boundary": BOUNDARY,
        }

    def _transition(self, action_id: str, state: str, reason: str, observation_sha256: str | None = None) -> dict[str, Any]:
        if state not in STATES or not NAME.fullmatch(reason):
            raise JournalError("requested journal transition is invalid")
        if observation_sha256 is not None and not SHA256.fullmatch(observation_sha256):
            raise JournalError("requested journal observation is invalid")
        with self._locked(action_id, create=False) as action:
            events = self._events(action, action_id)
            if not events:
                raise JournalError("external action has no proposal journal")
            head, head_hash = events[-1]
            if state not in TRANSITIONS[head["state"]]:
                raise JournalError(f"external action cannot transition from {head['state']} to {state}")
            value = self._value(
                action_id=action_id, sequence=len(events), previous=head_hash, state=state, reason=reason,
                connector=head["connector"], operation=head["operation"], proposal_sha256=head["proposalSha256"],
                idempotency_key_sha256=head["idempotencyKeySha256"], idempotency_mode=head["idempotencyMode"],
                provider_target_sha256=head["providerTargetSha256"], observation_sha256=observation_sha256,
            )
            _validate_event(value, action_id=action_id, sequence=len(events), previous=head_hash)
            return self._append(action, value)

    def begin_submit(self, action_id: str) -> dict[str, Any]:
        return self._transition(action_id, "submitting", "provider-submit-begun")

    def mark_unknown(self, action_id: str, reason: str = "provider-outcome-unknown", observation_sha256: str | None = None) -> dict[str, Any]:
        return self._transition(action_id, "unknown", reason, observation_sha256)

    def begin_reconcile(self, action_id: str) -> dict[str, Any]:
        status = self.status(action_id)
        if status["state"] == "submitting":
            self.mark_unknown(action_id, "process-interrupted-after-submit")
        elif status["state"] == "reconciling":
            self.mark_unknown(action_id, "process-interrupted-reconcile")
        return self._transition(action_id, "reconciling", "provider-reconciliation-begun")

    def succeed(self, action_id: str, observation_sha256: str, reason: str = "provider-result-bound") -> dict[str, Any]:
        return self._transition(action_id, "succeeded", reason, observation_sha256)

    def fail(self, action_id: str, reason: str, observation_sha256: str | None = None) -> dict[str, Any]:
        return self._transition(action_id, "failed", reason, observation_sha256)

    def cancel(self, action_id: str, reason: str = "canceled-before-submit") -> dict[str, Any]:
        return self._transition(action_id, "canceled", reason)

    def status(self, action_id: str) -> dict[str, Any]:
        with self._locked(action_id, create=False) as action:
            events = self._events(action, action_id)
            if not events:
                raise JournalError("external action has no proposal journal")
            head, head_hash = events[-1]
            effective = "unknown" if head["state"] in {"submitting", "reconciling"} else head["state"]
            return {
                "actionId": action_id, "state": head["state"], "effectiveState": effective,
                "retryAllowed": head["retryAllowed"], "reasonCode": head["reasonCode"],
                "terminal": head["state"] in TERMINAL,
                "eventCount": len(events), "headSha256": head_hash,
                "connector": head["connector"], "operation": head["operation"],
                "proposalSha256": head["proposalSha256"], "idempotencyKeySha256": head["idempotencyKeySha256"],
                "idempotencyMode": head["idempotencyMode"], "providerTargetSha256": head["providerTargetSha256"],
                "observationSha256": head["observationSha256"], "boundary": BOUNDARY,
            }
