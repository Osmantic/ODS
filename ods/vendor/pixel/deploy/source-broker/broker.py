#!/usr/bin/env python3
"""Build bounded, non-instructional projections from untrusted external sources."""

from __future__ import annotations

import base64
import argparse
import hashlib
import json
import os
import re
import stat
import tempfile
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

# <<<PIXEL-BUNDLED-JOURNAL-VENDOR-SECRETS-BEGIN>>>
# Bundled from deploy/action_journal/__init__.py and deploy/vendor_secrets.py by
# scripts/lib/bundle-brokers.py. Do not edit by hand; regenerate with the bundler.
import types as _pixel_bundled_types

_PIXEL_BUNDLED_JOURNAL_VENDOR = r'''"""Durable, content-free exactly-once journal for bounded external actions."""

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


"""Shared high-precision vendor credential detection for OUTBOUND content in the owner-confirmed
external-action brokers (GitHub, Calendar).

Only specific, near-zero-false-positive vendor secret FORMATS are matched -- NOT generic
credential assignments, long hexadecimal (commit SHAs), or word-like values -- so legitimate prose,
credential documentation, commit references, and placeholders (e.g. "Set your API_KEY in .env",
"Bearer YOUR_TOKEN_HERE", "rotate DEPLOY_TOKEN") are never flagged. These brokers submit
owner-hash-confirmed proposals that cannot be redacted, so a match must REJECT the proposal (the
caller raises), keeping an injected or accidental credential off the (possibly public) destination.

Single source of truth so the detection cannot drift between the two brokers.
"""


import re

VENDOR_SECRET_PATTERNS = (
    ("private key", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----", re.I)),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("OpenAI-style key", re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("GitLab token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b")),
    ("Google API key", re.compile(r"\bAIza[A-Za-z0-9_-]{35}\b")),
    ("npm token", re.compile(r"\bnpm_[A-Za-z0-9]{20,}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{16,}\b", re.I)),
    ("Stripe live key", re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{16,}\b")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("bearer token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}\b")),
)


def find_vendor_secret(*texts: str) -> str | None:
    """Return the label of the first vendor credential found across the given texts, else None."""
    joined = "\n".join(text for text in texts if isinstance(text, str))
    for name, pattern in VENDOR_SECRET_PATTERNS:
        if pattern.search(joined):
            return name
    return None
'''
_pixel_bundled = _pixel_bundled_types.ModuleType('_pixel_bundled_journal_vendor')
exec(compile(_PIXEL_BUNDLED_JOURNAL_VENDOR, '<pixel-bundled-journal-vendor>', 'exec'), _pixel_bundled.__dict__)
ExternalActionJournal = _pixel_bundled.ExternalActionJournal
JournalError = _pixel_bundled.JournalError
journal_sha256 = _pixel_bundled.sha256
find_vendor_secret = _pixel_bundled.find_vendor_secret
del _pixel_bundled_types
# <<<PIXEL-BUNDLED-JOURNAL-VENDOR-SECRETS-END>>>

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows unit-test compatibility
    fcntl = None


SCHEMA_VERSION = 1
SANITIZER_REVISION = 2
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"
SAFE_TEXT_LIMIT = 800
MAX_SOURCE_CHARS = 200_000
DEFINITE_REJECTION_CODES = {400, 401, 403, 412, 422}

SIGNAL_PATTERNS = {
    "agent-targeting": re.compile(r"\b(system prompt|developer message|system\s+(?:agent|assistant))\b|\b(pixel|assistant|agent)\b.{0,100}\b(ignore|instruction|rule|policy|prompt|command|tool|must|should|run|execute|memory)\b|\b(ignore|override)\b.{0,100}\b(pixel|assistant|agent)\b", re.I),
    "instruction-override": re.compile(r"\b(ignore|disregard|override|replace)\b.{0,80}\b(instruction|rule|policy|prompt)\b", re.I),
    "command-execution": re.compile(r"\b(run|execute|launch|invoke)\b.{0,80}\b(command|script|shell|bash|powershell|cmd)\b|\b(bash|powershell|cmd\.exe|curl|wget|ssh)\b", re.I),
    "file-access": re.compile(r"\b(read|open|write|copy|upload|send)\b.{0,80}\b(file|secret|token|credential|memory)\b|(?:^|\s)(?:/workspace|/home/|media/|\.ssh/)", re.I),
    "concealment": re.compile(r"\b(do not|don't|never)\b.{0,50}\b(tell|mention|disclose|show|ask)\b|\b(silently|invisible|without confirmation)\b", re.I),
    "persistence": re.compile(r"\b(durable memory|permanent rule|pixel-runbook|remember this|future messages)\b", re.I),
    "encoded-instruction": re.compile(r"\b(base64|rot13|hex(?:adecimal)?|url[- ]?encoded)\b|\b(decode|decrypt|deobfuscate|transform|reconstruct)\b.{0,100}\b(payload|text|note|line|continuation|procedure|result)\b|(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{24,}={0,2}(?![A-Za-z0-9+/=])|(?:[A-Za-z0-9_.~-]*%[0-9a-f]{2}){8,}|(?:&#(?:x[0-9a-f]+|\d+);){4,}|\b[0-9a-f]{24,}\b", re.I),
    "encoded-header": re.compile(r"=\?[^?\s]{1,30}\?[bq]\?[^?]{2,500}\?=", re.I),
    "indirect-instruction": re.compile(r"\b(follow|carry out|perform|apply|use)\b.{0,100}\b(procedure|next step|instruction|workflow|calendar notes?|decoded|resulting)\b", re.I),
    "source-authority": re.compile(r"\b(owner approval|owner-approved|pre-approved|standing preference|already approved|authorized by (?:michael|the owner|an? admin(?:istrator)?))\b", re.I),
    "cross-source": re.compile(r"\b(calendar|inbox|email|message|social (?:post|record))\b.{0,140}\b(confirm|authorize|approval|follow|procedure|notes?)\b|\b(companion|linked)\b.{0,100}\b(calendar|inbox|email|message|record)\b", re.I),
    "memory-write": re.compile(r"\b(add|put|record|save|store|keep|adopt)\b.{0,100}\b(memory(?:\.md)?|for later|convention|standing rule|preference)\b", re.I),
    "calendar-mutation": re.compile(r"\b(reserve|block|schedule|book|add|move|delete|cancel)\b.{0,120}\b(calendar|event|meeting|minutes?|tomorrow|am|pm)\b", re.I),
    "link-following": re.compile(r"\b(open|visit|follow|fetch|retrieve|browse)\b.{0,100}\b(?:https?://|url|link|page|site)\b|https?://\S+.{0,100}\b(next step|procedure|instruction|workflow)\b", re.I),
}
COMMAND_SHAPE = re.compile(r"(?:^|\s)(?:sudo\s+)?(?:bash|sh|python\d*|node|curl|wget|ssh|scp|rsync)\s+|`[^`]{2,}`|```", re.I)
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
PROJECTION_PLACEHOLDER = re.compile(r"^\[(?:quarantined|withheld)[^\]]*\]$", re.I)
MAX_PROPOSAL_BYTES = 256 * 1024
PROPOSAL_BOUNDARY = "This file is a proposal only. No Calendar mutation has occurred."
ETAG_PATTERN = re.compile(r'^(?:W/)?"(?:[^"\\\r\n]|\\.){1,200}"$')
EMAIL_PATTERN = re.compile(r"^[^\s@<>\x00-\x1f\x7f]+@[^\s@<>\x00-\x1f\x7f]+$")
RECURRENCE_PATTERN = re.compile(r"^(?:RRULE|RDATE|EXDATE):[A-Z0-9,;=:+._/-]{1,990}$")
CONFUSABLES = str.maketrans({
    "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K", "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X",
    "α": "a", "β": "b", "ε": "e", "ι": "i", "κ": "k", "ο": "o", "ρ": "p", "τ": "t", "υ": "y", "χ": "x",
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T", "Х": "X",
    "а": "a", "е": "e", "і": "i", "ј": "j", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
})
LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"})


class UpstreamHTTPError(RuntimeError):
    def __init__(self, code: int) -> None:
        super().__init__(f"upstream request failed with HTTP {code}")
        self.code = code


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def valid_iso_timestamp(value: Any) -> bool:
    """A timestamp must be a UTC ISO-8601 instant of the same shape the broker writes."""
    if not isinstance(value, str):
        return False
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?Z", value):
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0)


def env_enabled(name: str, fallback: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return fallback
    return value.strip().lower() not in {"0", "false", "off", "no"}


def normalized_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = "".join(character for character in text if unicodedata.category(character) != "Cf")
    return CONTROL_CHARS.sub("", text)


def bounded_text(value: Any, limit: int = SAFE_TEXT_LIMIT) -> str:
    text = normalized_text(value)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def html_to_text(value: str) -> str:
    parser = TextExtractor()
    try:
        parser.feed(value)
        parser.close()
    except Exception:
        return ""
    return bounded_text(" ".join(parser.parts), MAX_SOURCE_CHARS)


def risk_signals(value: str) -> list[str]:
    source = bounded_text(value, MAX_SOURCE_CHARS)
    normalized = source.translate(CONFUSABLES).translate(LEET)
    normalized = re.sub(
        r"\b(?:[A-Za-z][\s._-]+){3,}[A-Za-z]\b",
        lambda match: re.sub(r"[\s._-]+", "", match.group(0)),
        normalized,
    )
    signals = [name for name, pattern in SIGNAL_PATTERNS.items() if pattern.search(source) or pattern.search(normalized)]
    if (COMMAND_SHAPE.search(source) or COMMAND_SHAPE.search(normalized)) and "command-execution" not in signals:
        signals.append("command-execution")
    return sorted(signals)


def safe_fragment(value: str) -> bool:
    return not risk_signals(value) and not COMMAND_SHAPE.search(value)


def safe_summary(value: str, signals: list[str]) -> str:
    source = bounded_text(value, MAX_SOURCE_CHARS)
    fragments = re.split(r"(?<=[.!?])\s+|[\r\n]+", source)
    safe = [bounded_text(fragment, 320) for fragment in fragments if len(fragment.strip()) >= 4 and safe_fragment(fragment)]
    prefix = "Potential instruction-targeting content was quarantined. " if signals else ""
    summary = prefix + " ".join(safe[:3])
    if not summary.strip():
        summary = "Content withheld because it consisted of operational instructions or unsafe markup."
    return bounded_text(summary, SAFE_TEXT_LIMIT)


def safe_header(value: str, replacement: str) -> tuple[str, list[str]]:
    text = bounded_text(value, 500)
    signals = risk_signals(text)
    return (replacement if signals else text), signals


def safe_address(value: str) -> tuple[str, list[str]]:
    name, address = parseaddr(value or "")
    display, signals = safe_header(name, "[quarantined sender name]")
    address = bounded_text(address, 320)
    address_signals = risk_signals(address)
    if address_signals:
        address = "[quarantined sender address]"
        signals = sorted(set(signals + address_signals))
    return (f"{display} <{address}>" if display and address else address or display), signals


def safe_email_field(value: Any, replacement: str) -> tuple[str, list[str]]:
    raw = str(value or "")
    address = bounded_text(raw, 320)
    if address != raw.strip() or not EMAIL_PATTERN.fullmatch(address):
        return replacement, ["field-integrity"]
    return address, []


def projected_event_time(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, str] = {}
    for key, limit in (("date", 32), ("dateTime", 64), ("timeZone", 100)):
        if isinstance(value.get(key), str):
            result[key] = bounded_text(value[key], limit)
    return result or None


def base64url_decode(value: str) -> str:
    if not value:
        return ""
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode((value + padding).encode("ascii")).decode("utf-8", "replace")
    except (ValueError, UnicodeError):
        return ""


def collect_message_parts(part: dict[str, Any], output: dict[str, list[Any]]) -> None:
    if not isinstance(part, dict):
        return
    mime_type = str(part.get("mimeType") or "")
    filename = str(part.get("filename") or "")
    body = part.get("body") or {}
    if not isinstance(body, dict):
        body = {}
    if filename:
        try:
            size = max(0, int(body.get("size") or 0))
        except (TypeError, ValueError):
            size = 0
        output["attachments"].append({
            "filename": bounded_text(filename, 200),
            "mimeType": bounded_text(mime_type, 100),
            "size": size,
        })
    data = body.get("data")
    if data and mime_type in {"text/plain", "text/html"}:
        output["plain" if mime_type == "text/plain" else "html"].append(base64url_decode(str(data)))
    children = part.get("parts") or []
    if isinstance(children, list):
        for child in children:
            if isinstance(child, dict):
                collect_message_parts(child, output)


def email_projection(message: dict[str, Any], *, requested_mailbox: str = "inbox", include_content: bool = True) -> dict[str, Any]:
    payload = message.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    raw_headers = payload.get("headers") or []
    if not isinstance(raw_headers, list):
        raw_headers = []
    headers = {
        str(item.get("name") or "").lower(): str(item.get("value") or "")
        for item in raw_headers if isinstance(item, dict)
    }
    parts: dict[str, list[Any]] = {"plain": [], "html": [], "attachments": []}
    if include_content:
        collect_message_parts(payload, parts)
    raw_text = "\n\n".join(parts["plain"]) or html_to_text("\n\n".join(parts["html"]))
    raw_text = raw_text[:MAX_SOURCE_CHARS]
    subject, subject_signals = safe_header(headers.get("subject", ""), "[quarantined subject]")
    sender, sender_signals = safe_address(headers.get("from", ""))
    recipient, recipient_signals = safe_header(headers.get("to", ""), "[quarantined recipient header]")
    copied, copied_signals = safe_header(headers.get("cc", ""), "[quarantined recipient header]")
    date, date_signals = safe_header(headers.get("date", ""), "[quarantined date header]")
    safe_attachments = []
    attachment_signals: list[str] = []
    for attachment in parts["attachments"]:
        filename, found = safe_header(str(attachment.get("filename") or ""), "[quarantined attachment name]")
        attachment_signals.extend(found)
        mime_type = str(attachment.get("mimeType") or "")
        mime_signals = risk_signals(mime_type)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,63}/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,127}", mime_type) or mime_signals:
            mime_type = "[quarantined MIME type]"
            attachment_signals.extend(mime_signals or ["field-integrity"])
        safe_attachments.append({**attachment, "filename": filename, "mimeType": mime_type})
    signals = sorted(set(
        (risk_signals(raw_text) if include_content else [])
        + subject_signals + sender_signals + recipient_signals + copied_signals
        + date_signals + attachment_signals
    ))
    try:
        internal_ms = int(message.get("internalDate") or 0)
    except (TypeError, ValueError):
        internal_ms = 0
    message_at = iso(datetime.fromtimestamp(internal_ms / 1000, tz=timezone.utc)) if internal_ms else ""
    label_ids = [bounded_text(item, 80) for item in message.get("labelIds") or []]
    mailboxes = []
    if "INBOX" in label_ids:
        mailboxes.append("inbox")
    if "SENT" in label_ids:
        mailboxes.append("sent")
    if requested_mailbox in {"inbox", "sent"} and requested_mailbox not in mailboxes:
        mailboxes.append(requested_mailbox)
    source_material = raw_text if include_content else json.dumps({
        "id": bounded_text(message.get("id"), 256),
        "threadId": bounded_text(message.get("threadId"), 256),
        "internalDate": internal_ms,
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "cc": headers.get("cc", ""),
        "subject": headers.get("subject", ""),
        "date": headers.get("date", ""),
    }, sort_keys=True, separators=(",", ":"))
    return {
        "id": bounded_text(message.get("id"), 256),
        "threadId": bounded_text(message.get("threadId"), 256),
        "labelIds": label_ids,
        "mailboxes": mailboxes,
        "direction": "outbound" if "sent" in mailboxes else "inbound",
        "from": sender,
        "to": recipient,
        "cc": copied,
        "subject": subject,
        "date": date,
        "messageAt": message_at,
        "receivedAt": message_at if "inbox" in mailboxes else "",
        "sentAt": message_at if "sent" in mailboxes else "",
        "summary": safe_summary(raw_text, signals) if include_content else "",
        "attachments": safe_attachments[:25],
        "contentProjection": "sanitized-summary" if include_content else "metadata-only",
        "bodyFetchedByBroker": include_content,
        "risk": {"quarantined": bool(signals), "signals": signals},
        "trust": "untrusted-source-record",
        "rawContentAvailableToPixel": False,
        "sourceHash": hashlib.sha256(source_material.encode("utf-8", "replace")).hexdigest(),
    }


def calendar_projection(event: dict[str, Any]) -> dict[str, Any]:
    raw = "\n".join(str(event.get(key) or "") for key in ("summary", "description", "location"))
    signals = risk_signals(raw)
    title, title_signals = safe_header(str(event.get("summary") or ""), "[quarantined event title]")
    signals = sorted(set(signals + title_signals))
    location = bounded_text(event.get("location"), 400)
    if not safe_fragment(location):
        location = "[quarantined location]"
    attendees = []
    attendee_signals: list[str] = []
    raw_attendees = event.get("attendees") or []
    if not isinstance(raw_attendees, list):
        raw_attendees = []
        attendee_signals.append("field-integrity")
    for item in raw_attendees[:100]:
        if not isinstance(item, dict):
            attendee_signals.append("field-integrity")
            continue
        display_name, found = safe_header(str(item.get("displayName") or ""), "[quarantined attendee name]")
        attendee_signals.extend(found)
        attendee_email, found = safe_email_field(item.get("email"), "[quarantined attendee address]")
        attendee_signals.extend(found)
        attendees.append({
            "email": attendee_email,
            "displayName": display_name,
            "responseStatus": bounded_text(item.get("responseStatus"), 80),
            "organizer": bool(item.get("organizer")),
            "self": bool(item.get("self")),
        })
    recurrence = []
    recurrence_signals: list[str] = []
    raw_recurrence = event.get("recurrence") or []
    if not isinstance(raw_recurrence, list):
        raw_recurrence = []
        recurrence_signals.append("field-integrity")
    for item in raw_recurrence[:50]:
        text = bounded_text(item, 1000)
        if not RECURRENCE_PATTERN.fullmatch(text):
            recurrence.append("[withheld recurrence]")
            recurrence_signals.append("field-integrity")
        else:
            recurrence.append(text)
    signals = sorted(set(signals + attendee_signals + recurrence_signals))
    organizer = event.get("organizer") or {}
    organizer_email = ""
    if organizer:
        if isinstance(organizer, dict):
            organizer_email, found = safe_email_field(organizer.get("email"), "[quarantined organizer address]")
            signals = sorted(set(signals + found))
        else:
            signals = sorted(set(signals + ["field-integrity"]))
            organizer = {}
    return {
        "id": bounded_text(event.get("id"), 1024),
        "etag": bounded_text(event.get("etag"), 256),
        "status": bounded_text(event.get("status"), 80),
        "title": title,
        "notesSummary": safe_summary(str(event.get("description") or ""), signals),
        "location": location,
        "start": projected_event_time(event.get("start")),
        "end": projected_event_time(event.get("end")),
        "attendees": attendees,
        "organizer": {"email": organizer_email, "self": bool(organizer.get("self"))} if organizer else None,
        "recurrence": recurrence,
        "updated": bounded_text(event.get("updated"), 100),
        "risk": {"quarantined": bool(signals), "signals": signals},
        "trust": "untrusted-source-record",
        "rawDescriptionAvailableToPixel": False,
        "sourceHash": hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest(),
    }


def social_projection(item: dict[str, Any]) -> dict[str, Any]:
    raw = str(item.get("text") or "")
    signals = risk_signals(raw)
    author, author_signals = safe_header(str(item.get("author") or ""), "[quarantined author]")
    signals = sorted(set(signals + author_signals))
    raw_url = bounded_text(item.get("url"), 1000)
    try:
        parsed = urllib.parse.urlsplit(raw_url)
        url_signals = risk_signals(urllib.parse.unquote(raw_url))
        safe_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", "")) if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password and not url_signals else ""
        signals = sorted(set(signals + url_signals + ([] if safe_url or not raw_url else ["field-integrity"])))
    except ValueError:
        safe_url = ""
        signals = sorted(set(signals + ["field-integrity"]))
    return {
        "id": bounded_text(item.get("id"), 256),
        "author": author,
        "publishedAt": bounded_text(item.get("publishedAt"), 100),
        "url": bounded_text(safe_url, 1000),
        "summary": safe_summary(raw, signals),
        "risk": {"quarantined": bool(signals), "signals": signals},
        "trust": "untrusted-source-record",
        "rawContentAvailableToPixel": False,
        "sourceHash": hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest(),
    }


def request_json(url: str, *, method: str = "GET", headers: dict[str, str] | None = None, body: bytes | None = None) -> dict[str, Any]:
    request = urllib.request.Request(url, method=method, headers=headers or {}, data=body)
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as error:
        raise UpstreamHTTPError(error.code) from error


def access_token(token_path: Path) -> str:
    credentials = json.loads(token_path.read_text(encoding="utf-8"))
    for key in ("client_id", "client_secret", "refresh_token"):
        if not credentials.get(key):
            raise RuntimeError(f"OAuth credential is missing {key}")
    body = urllib.parse.urlencode({
        "client_id": credentials["client_id"],
        "client_secret": credentials["client_secret"],
        "refresh_token": credentials["refresh_token"],
        "grant_type": "refresh_token",
    }).encode("ascii")
    response = request_json(
        str(credentials.get("token_uri") or "https://oauth2.googleapis.com/token"),
        method="POST",
        headers={"content-type": "application/x-www-form-urlencoded"},
        body=body,
    )
    token = str(response.get("access_token") or "")
    if not token:
        raise RuntimeError("OAuth refresh returned no access token")
    return token


def google_get(base: str, path: str, token: str, params: dict[str, Any]) -> dict[str, Any]:
    filtered = {key: value for key, value in params.items() if value not in (None, "")}
    url = f"{base}{path}?{urllib.parse.urlencode(filtered, doseq=True)}"
    return request_json(url, headers={"authorization": f"Bearer {token}"})


def google_call(base: str, path: str, token: str, *, method: str, params: dict[str, Any], value: dict[str, Any] | None = None, extra_headers: dict[str, str] | None = None) -> dict[str, Any]:
    filtered = {key: item for key, item in params.items() if item not in (None, "")}
    url = f"{base}{path}?{urllib.parse.urlencode(filtered, doseq=True)}"
    body = json.dumps(value).encode("utf-8") if value is not None else None
    headers = {"authorization": f"Bearer {token}"}
    headers.update(extra_headers or {})
    if body is not None:
        headers["content-type"] = "application/json"
    return request_json(url, method=method, headers=headers, body=body)


def gmail_listing(token: str, query: str, page_size: int, max_pages: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_page_tokens: set[str] = set()
    page_token = ""
    pages_fetched = 0
    estimate = 0
    truncated = False
    truncation_reason = ""
    while pages_fetched < max_pages:
        params: dict[str, Any] = {"q": query, "maxResults": page_size}
        if page_token:
            params["pageToken"] = page_token
        listing = google_get(GMAIL_API, "/messages", token, params)
        pages_fetched += 1
        for item in listing.get("messages") or []:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or "")
            if not item_id or item_id in seen_ids:
                continue
            seen_ids.add(item_id)
            items.append(item)
        try:
            estimate = max(estimate, max(0, int(listing.get("resultSizeEstimate") or 0)))
        except (TypeError, ValueError):
            pass
        next_page_token = str(listing.get("nextPageToken") or "")
        if not next_page_token:
            break
        if next_page_token in seen_page_tokens:
            truncated = True
            truncation_reason = "repeated-page-token"
            break
        if pages_fetched >= max_pages:
            truncated = True
            truncation_reason = "safety-page-limit"
            break
        seen_page_tokens.add(next_page_token)
        page_token = next_page_token
    return items, {
        "query": bounded_text(query, 1000),
        "pageSize": page_size,
        "maxPages": max_pages,
        "pagesFetched": pages_fetched,
        "fetched": len(items),
        "resultSizeEstimate": estimate,
        "truncated": truncated,
        "truncationReason": truncation_reason,
        "completeWithinQuery": not truncated,
    }


def gmail_records(token: str, cached_records: list[dict[str, Any]] | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    inbox_query = os.environ.get("PIXEL_SOURCE_GMAIL_QUERY", "in:inbox")
    inbox_page_size = min(max(int(os.environ.get("PIXEL_SOURCE_GMAIL_PAGE_SIZE", os.environ.get("PIXEL_SOURCE_GMAIL_MAX", "100"))), 1), 100)
    inbox_max_pages = min(max(int(os.environ.get("PIXEL_SOURCE_GMAIL_MAX_PAGES", "1000")), 1), 10000)
    sent_query = os.environ.get("PIXEL_SOURCE_GMAIL_SENT_QUERY", "in:sent")
    sent_page_size = min(max(int(os.environ.get("PIXEL_SOURCE_GMAIL_SENT_PAGE_SIZE", os.environ.get("PIXEL_SOURCE_GMAIL_SENT_MAX", "100"))), 1), 100)
    sent_max_pages = min(max(int(os.environ.get("PIXEL_SOURCE_GMAIL_SENT_MAX_PAGES", "1000")), 1), 10000)
    inbox_items, inbox_coverage = gmail_listing(token, inbox_query, inbox_page_size, inbox_max_pages)
    sent_items, sent_coverage = gmail_listing(token, sent_query, sent_page_size, sent_max_pages)
    unread_items, unread_coverage = gmail_listing(token, f"{inbox_query} is:unread", inbox_page_size, inbox_max_pages)
    unread_ids = {str(item.get("id") or "") for item in unread_items}
    inbox_ids = {str(item.get("id") or "") for item in inbox_items}
    sent_ids = {str(item.get("id") or "") for item in sent_items}
    cache = {
        str(item.get("id")): item for item in (cached_records or [])
        if isinstance(item, dict) and item.get("id")
    }
    records: dict[str, dict[str, Any]] = {}
    cache_reused = 0
    detail_fetched = 0
    metadata_params = {
        "format": "metadata",
        "metadataHeaders": ["From", "To", "Cc", "Subject", "Date"],
    }

    # Resolve the Sent listing first so a message that Gmail labels as both
    # Inbox and Sent can never inherit the richer Inbox projection.
    for requested_mailbox, items in (
        ("sent", sent_items),
        ("inbox", inbox_items),
    ):
        for item in items:
            raw_id = str(item.get("id") or "")
            message_id = urllib.parse.quote(raw_id, safe="")
            if not message_id:
                continue
            if raw_id in records:
                if requested_mailbox not in records[raw_id]["mailboxes"]:
                    records[raw_id]["mailboxes"].append(requested_mailbox)
                if requested_mailbox == "sent":
                    records[raw_id]["direction"] = "outbound"
                    records[raw_id]["sentAt"] = records[raw_id]["messageAt"]
                continue
            cached = cache.get(raw_id)
            reusable = (
                cached
                and (
                    requested_mailbox == "sent" and cached.get("contentProjection") == "metadata-only"
                    or requested_mailbox == "inbox" and cached.get("contentProjection") == "sanitized-summary"
                )
            )
            if reusable:
                record = dict(cached)
                labels = [item for item in record.get("labelIds", []) if item not in {"INBOX", "SENT", "UNREAD"}]
                if raw_id in inbox_ids:
                    labels.append("INBOX")
                if raw_id in sent_ids:
                    labels.append("SENT")
                if raw_id in unread_ids:
                    labels.append("UNREAD")
                record["labelIds"] = labels
                record["mailboxes"] = [mailbox for mailbox, ids in (("inbox", inbox_ids), ("sent", sent_ids)) if raw_id in ids]
                record["direction"] = "outbound" if raw_id in sent_ids else "inbound"
                record["receivedAt"] = record.get("messageAt", "") if raw_id in inbox_ids else ""
                record["sentAt"] = record.get("messageAt", "") if raw_id in sent_ids else ""
                records[raw_id] = record
                cache_reused += 1
                continue
            message = google_get(GMAIL_API, f"/messages/{message_id}", token, metadata_params)
            detail_fetched += 1
            metadata_record = email_projection(
                message,
                requested_mailbox=requested_mailbox,
                include_content=False,
            )
            if requested_mailbox == "sent" or "sent" in metadata_record["mailboxes"]:
                records[raw_id] = metadata_record
                continue
            full_message = google_get(GMAIL_API, f"/messages/{message_id}", token, {"format": "full"})
            detail_fetched += 1
            records[raw_id] = email_projection(
                full_message,
                requested_mailbox="inbox",
                include_content=True,
            )
    return sorted(records.values(), key=lambda item: item.get("messageAt", ""), reverse=True), {
        "bounded": True,
        "folders": {"inbox": inbox_coverage, "sent": sent_coverage, "unread": unread_coverage},
        "cache": {"sanitizerRevision": SANITIZER_REVISION, "reused": cache_reused, "detailRequests": detail_fetched},
    }


def calendar_records(token: str) -> list[dict[str, Any]]:
    now = utc_now()
    past_days = min(max(int(os.environ.get("PIXEL_SOURCE_CALENDAR_PAST_DAYS", "7")), 0), 365)
    future_days = min(max(int(os.environ.get("PIXEL_SOURCE_CALENDAR_FUTURE_DAYS", "90")), 1), 730)
    calendar_id = urllib.parse.quote(os.environ.get("PIXEL_SOURCE_CALENDAR_ID", "primary"), safe="")
    result = google_get(CALENDAR_API, f"/calendars/{calendar_id}/events", token, {
        "timeMin": iso(now - timedelta(days=past_days)),
        "timeMax": iso(now + timedelta(days=future_days)),
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": 250,
    })
    return [calendar_projection(item) for item in result.get("items") or []]


def social_records(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise RuntimeError("social input must be a JSON array")
    return [social_projection(item) for item in value[:500] if isinstance(item, dict)]


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o640)
        os.replace(temporary, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


def protected_proposal(path: Path) -> tuple[dict[str, Any], str]:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_PROPOSAL_BYTES or info.st_nlink != 1:
        raise RuntimeError("approved proposal snapshot is not a bounded regular file")
    if os.name != "nt" and info.st_mode & 0o022:
        raise RuntimeError("approved proposal snapshot is writable by group or other")
    if os.name != "nt" and hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        raise RuntimeError("approved proposal snapshot is not owned by the actuator identity")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_size > MAX_PROPOSAL_BYTES:
            raise RuntimeError("approved proposal snapshot changed during secure open")
        payload = bytearray()
        while True:
            chunk = os.read(descriptor, min(64 * 1024, MAX_PROPOSAL_BYTES + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
            if len(payload) > MAX_PROPOSAL_BYTES:
                raise RuntimeError("approved proposal snapshot exceeds its size limit")
    finally:
        os.close(descriptor)
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise RuntimeError(f"approved proposal contains duplicate key: {key}")
            value[key] = item
        return value

    def reject_constant(value: str) -> None:
        raise RuntimeError(f"approved proposal contains non-finite number: {value}")

    value = json.loads(payload.decode("utf-8"), object_pairs_hook=unique_object, parse_constant=reject_constant)
    if not isinstance(value, dict):
        raise RuntimeError("approved proposal snapshot must be a JSON object")
    return value, hashlib.sha256(payload).hexdigest()


def proposal_string(fields: dict[str, Any], key: str, maximum: int, *, required: bool = False) -> str | None:
    value = fields.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > maximum:
        raise RuntimeError(f"proposal field {key} is invalid")
    if CONTROL_CHARS.search(value):
        raise RuntimeError(f"proposal field {key} contains control characters")
    return value


def validated_event_times(fields: dict[str, Any], *, required: bool) -> dict[str, Any]:
    start = proposal_string(fields, "start", 64, required=required)
    end = proposal_string(fields, "end", 64, required=required)
    if bool(start) != bool(end):
        raise RuntimeError("proposal time requires both start and end")
    all_day = fields.get("allDay", False)
    if not isinstance(all_day, bool):
        raise RuntimeError("proposal field allDay must be boolean")
    time_zone = proposal_string(fields, "timeZone", 100)
    if time_zone and not re.fullmatch(r"[A-Za-z0-9._+-]+(?:/[A-Za-z0-9._+-]+)*", time_zone):
        raise RuntimeError("proposal timeZone is invalid")
    if not start:
        return {}
    try:
        if all_day:
            start_value = datetime.strptime(start, "%Y-%m-%d").date()
            end_value = datetime.strptime(end, "%Y-%m-%d").date()
        else:
            start_value = datetime.fromisoformat(start.replace("Z", "+00:00"))
            end_value = datetime.fromisoformat(end.replace("Z", "+00:00"))
            if start_value.tzinfo is None or end_value.tzinfo is None:
                raise ValueError("timezone offset required")
    except ValueError as exc:
        raise RuntimeError("proposal start/end are not valid ISO calendar times") from exc
    if end_value <= start_value:
        raise RuntimeError("proposal end must be after start")
    return {"start": start, "end": end, "allDay": all_day, "timeZone": time_zone}


def validated_attendees(fields: dict[str, Any]) -> list[dict[str, str]] | None:
    attendees = fields.get("attendees")
    if attendees is None:
        return None
    if not isinstance(attendees, list) or len(attendees) > 100:
        raise RuntimeError("proposal attendees must be an array of at most 100 entries")
    result = []
    observed = set()
    for item in attendees:
        if not isinstance(item, dict) or set(item) != {"email"}:
            raise RuntimeError("proposal attendee entries may contain only email")
        email = proposal_string(item, "email", 320, required=True)
        if not EMAIL_PATTERN.fullmatch(email):
            raise RuntimeError("proposal attendee email is invalid")
        identity = email.casefold()
        if identity in observed:
            raise RuntimeError("proposal attendees contain a duplicate email")
        observed.add(identity)
        result.append({"email": email})
    return result


def validate_proposal(
    value: dict[str, Any],
    proposal_id: str,
    *,
    required_status: str = "pending-operator-approval",
) -> tuple[str, dict[str, Any]]:
    required_top = {"schemaVersion", "proposalId", "source", "action", "status", "createdAt", "values", "boundary"}
    if set(value) != required_top:
        raise RuntimeError("proposal contains missing or unknown top-level fields")
    if value.get("schemaVersion") != 1 or value.get("proposalId") != proposal_id:
        raise RuntimeError("proposal schema or identity is invalid")
    if value.get("source") != "pixel-owner-conversation" or value.get("status") != required_status:
        raise RuntimeError("proposal source or status is invalid")
    if value.get("boundary") != PROPOSAL_BOUNDARY or not isinstance(value.get("createdAt"), str):
        raise RuntimeError("proposal boundary or creation time is invalid")
    action = value.get("action")
    fields = value.get("values")
    if action not in {"create", "update", "delete"} or not isinstance(fields, dict):
        raise RuntimeError("proposal action or values are invalid")
    common = {"summary", "start", "end", "timeZone", "allDay", "description", "location", "attendees", "sendUpdates"}
    allowed = common if action == "create" else ({"eventId", "expectedEtag"} | common if action == "update" else {"eventId", "expectedEtag", "sendUpdates"})
    if not set(fields) <= allowed:
        raise RuntimeError("proposal contains fields not allowed for its action")
    cleaned: dict[str, Any] = {}
    send_updates = fields.get("sendUpdates", "none")
    if send_updates not in {"none", "all"}:
        raise RuntimeError("proposal sendUpdates must be none or all")
    cleaned["sendUpdates"] = send_updates
    if action in {"update", "delete"}:
        cleaned["eventId"] = proposal_string(fields, "eventId", 1024, required=True)
        etag = proposal_string(fields, "expectedEtag", 256, required=True)
        if not ETAG_PATTERN.fullmatch(etag):
            raise RuntimeError("proposal expectedEtag is invalid")
        cleaned["expectedEtag"] = etag
    if action != "delete":
        summary = proposal_string(fields, "summary", 1000, required=action == "create")
        if summary is not None:
            cleaned["summary"] = summary
        for key, maximum in (("description", 20_000), ("location", 1_000)):
            item = proposal_string(fields, key, maximum)
            if item is not None:
                cleaned[key] = item
        attendees = validated_attendees(fields)
        if attendees is not None:
            cleaned["attendees"] = attendees
        cleaned.update(validated_event_times(fields, required=action == "create"))
        if action == "update" and set(cleaned) <= {"eventId", "expectedEtag", "sendUpdates"}:
            raise RuntimeError("update proposal contains no changes")
    placeholders = projection_placeholders(cleaned)
    if placeholders:
        raise RuntimeError(f"proposal contains projection placeholders: {', '.join(placeholders)}")
    # Defense-in-depth: a calendar event created/updated with sendUpdates:all and attendees egresses
    # its title/description/location to those attendees. An injected or accidental vendor credential
    # in that content is rejected before the proposal is confirmed (shared high-precision detection).
    found = find_vendor_secret(*(cleaned.get(field, "") for field in ("summary", "description", "location")))
    if found:
        raise RuntimeError(f"proposal event content contains what looks like a {found}; a credential must not be published")
    return action, cleaned


def direct_calendar_eligible(action: str, fields: dict[str, Any]) -> bool:
    """Keep routine direct execution narrow, deterministic, and broker-enforced."""
    if action == "create":
        return not fields.get("attendees") and fields.get("sendUpdates", "none") == "none"
    if action != "update" or not fields.get("start") or not fields.get("end"):
        return False
    allowed = {"eventId", "expectedEtag", "start", "end", "timeZone", "allDay", "sendUpdates"}
    return set(fields) <= allowed and fields.get("sendUpdates", "none") == "none"


@contextmanager
def direct_action_lock(result_dir: Path):
    lock_path = result_dir / ".direct-calendar.lock"
    descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def enforce_direct_rate_limit(result_dir: Path) -> None:
    maximum = min(max(int(os.environ.get("PIXEL_CALENDAR_DIRECT_MAX_PER_HOUR", "20")), 1), 100)
    cutoff = utc_now() - timedelta(hours=1)
    observed = 0
    for path in result_dir.glob("calendar-*.json"):
        if path.name.endswith((".approved.json", ".processing.json")):
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            applied_at = datetime.fromisoformat(str(value.get("appliedAt") or "").replace("Z", "+00:00"))
        except (OSError, ValueError, TypeError):
            continue
        if value.get("approvalBinding") == "bounded-direct-policy" and applied_at.astimezone(timezone.utc) >= cutoff:
            observed += 1
    if observed >= maximum:
        raise RuntimeError("bounded direct Calendar hourly rate limit reached")


def snapshot_direct_proposal(source: Path, target: Path, proposal_id: str) -> None:
    info = source.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_PROPOSAL_BYTES or info.st_nlink != 1:
        raise RuntimeError("direct proposal is not a bounded regular file")
    if os.name != "nt" and info.st_mode & 0o022:
        raise RuntimeError("direct proposal is writable by group or other")
    descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_size > MAX_PROPOSAL_BYTES:
            raise RuntimeError("direct proposal changed during secure open")
        payload = bytearray()
        while True:
            chunk = os.read(descriptor, min(64 * 1024, MAX_PROPOSAL_BYTES + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
            if len(payload) > MAX_PROPOSAL_BYTES:
                raise RuntimeError("direct proposal exceeds its size limit")
    finally:
        os.close(descriptor)

    # Parse and validate before copying the exact bytes into the broker-owned boundary.
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise RuntimeError(f"direct proposal contains duplicate key: {key}")
            value[key] = item
        return value

    def reject_constant(value: str) -> None:
        raise RuntimeError(f"direct proposal contains non-finite number: {value}")

    value = json.loads(payload.decode("utf-8"), object_pairs_hook=unique_object, parse_constant=reject_constant)
    action, fields = validate_proposal(value, proposal_id, required_status="pending-bounded-direct")
    if not direct_calendar_eligible(action, fields):
        raise RuntimeError("proposal exceeds bounded direct Calendar policy")

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    output = os.open(target, flags, 0o600)
    try:
        with os.fdopen(output, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        target.unlink(missing_ok=True)
        raise


def calendar_create_event_id(proposal_id: str, proposal_hash: str) -> str:
    """Derive a stable Google base32hex-compatible ID before any create I/O."""
    if not re.fullmatch(r"calendar-[0-9]{13}-[a-f0-9]{8}", proposal_id) or not re.fullmatch(r"[a-f0-9]{64}", proposal_hash):
        raise RuntimeError("Calendar create identity inputs are invalid")
    # Hex is a strict subset of Google's lowercase base32hex alphabet (a-v, 0-9).
    return "p1e1" + hashlib.sha256(f"{proposal_id}:{proposal_hash}".encode("ascii")).hexdigest()


def claim_proposal(path: Path, proposal_id: str, proposal_hash: str, *, expected_event_id: str | None = None) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise RuntimeError("proposal is already processing or requires operator recovery") from exc
    record = {
        "schemaVersion": 1,
        "proposalId": proposal_id,
        "proposalSha256": proposal_hash,
        "status": "processing",
        "startedAt": iso(utc_now()),
        "recovery": "Do not retry automatically; inspect Calendar and the actuator journal.",
    }
    if expected_event_id is not None:
        record["expectedEventId"] = expected_event_id
        record["recovery"] = "Do not retry automatically; reconcile the exact provider event ID from the actuator journal."
    payload = (json.dumps(record, sort_keys=True) + "\n").encode("utf-8")
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def calendar_action_journal(
    result_dir: Path, proposal_id: str, proposal_hash: str, action: str,
    calendar_id: str, expected_event_id: str | None,
) -> tuple[ExternalActionJournal, dict[str, Any]]:
    """Bind one exact proposal to the shared content-free action state machine."""
    journal = ExternalActionJournal(result_dir / ".journal")
    idempotency_key = expected_event_id or f"{proposal_id}:{proposal_hash}"
    state = journal.propose(
        action_id=proposal_id,
        connector="google-calendar",
        operation=action,
        proposal_sha256=proposal_hash,
        idempotency_key_sha256=journal_sha256(idempotency_key),
        idempotency_mode="deterministic-provider-object-id" if action == "create" else "internal-nonreplay",
        provider_target_sha256=journal_sha256(calendar_id),
    )
    return journal, state


def calendar_observation_sha256(value: Any) -> str:
    """Digest a provider observation without retaining its possibly private content."""
    return journal_sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True))


def settle_calendar_journal_error(
    journal: ExternalActionJournal, proposal_id: str, error: Exception, *, definitely_before_provider: bool = False,
) -> None:
    """Record a definite rejection or an ambiguous post-submit failure without replay."""
    try:
        state = journal.status(proposal_id)["state"]
    except JournalError:
        return
    if state not in {"submitting", "reconciling"}:
        return
    observation = calendar_observation_sha256({"errorType": type(error).__name__, "httpCode": getattr(error, "code", None)})
    definite_http = _definite_provider_rejection(error)
    if definitely_before_provider:
        journal.fail(proposal_id, "local-before-provider-call", observation)
    elif definite_http:
        journal.fail(proposal_id, "provider-definite-rejection", observation)
    else:
        journal.mark_unknown(proposal_id, "provider-outcome-unknown", observation)


def _definite_provider_rejection(error: Exception) -> bool:
    return isinstance(error, UpstreamHTTPError) and error.code in DEFINITE_REJECTION_CODES


def projection(
    source: str,
    records: list[dict[str, Any]],
    generated_at: str,
    enabled: bool = True,
    coverage: dict[str, Any] | None = None,
    *,
    started_at: str | None = None,
) -> dict[str, Any]:
    value = {
        "schemaVersion": SCHEMA_VERSION,
        "source": source,
        "enabled": enabled,
        "generatedAt": generated_at,
        "startedAt": started_at or generated_at,
        "recordCount": len(records),
        "records": records,
        "boundary": {
            "rawContentStored": False,
            "instructionsAuthorized": False,
            "projectionOnly": True,
        },
    }
    if source == "gmail":
        value["sanitizerRevision"] = SANITIZER_REVISION
    if coverage is not None:
        value["coverage"] = coverage
    return value


def refresh() -> int:
    token_path = Path(os.environ.get("PIXEL_SOURCE_TOKEN_PATH", "/var/lib/pixel-source-broker/private/google-token.json"))
    output_dir = Path(os.environ.get("PIXEL_SOURCE_PROJECTION_DIR", "/var/lib/pixel-source-broker/projection"))
    social_path_value = os.environ.get("PIXEL_SOURCE_SOCIAL_INPUT", "")
    email_enabled = env_enabled("PIXEL_LIMB_EMAIL_ENABLED", True)
    calendar_enabled = env_enabled("PIXEL_LIMB_CALENDAR_ENABLED", True)
    social_enabled = env_enabled("PIXEL_LIMB_SOCIAL_ENABLED", False)
    token = access_token(token_path) if email_enabled or calendar_enabled else ""
    started_at = iso(utc_now())
    cached_email: list[dict[str, Any]] = []
    email_path = output_dir / "email.json"
    try:
        previous = json.loads(email_path.read_text(encoding="utf-8"))
        if (
            previous.get("schemaVersion") == SCHEMA_VERSION
            and previous.get("sanitizerRevision") == SANITIZER_REVISION
            and previous.get("boundary", {}).get("projectionOnly") is True
            and isinstance(previous.get("records"), list)
        ):
            cached_email = previous["records"]
    except (OSError, ValueError, TypeError):
        pass
    email, email_coverage = gmail_records(token, cached_email) if email_enabled else ([], {"bounded": True, "folders": {}})
    calendar = calendar_records(token) if calendar_enabled else []
    social = social_records(Path(social_path_value) if social_path_value else None) if social_enabled else []
    generated_at = iso(utc_now())
    atomic_json(output_dir / "email.json", projection("gmail", email, generated_at, email_enabled, email_coverage, started_at=started_at))
    atomic_json(output_dir / "calendar.json", projection("google-calendar", calendar, generated_at, calendar_enabled, started_at=started_at))
    atomic_json(output_dir / "social.json", projection("social", social, generated_at, social_enabled, started_at=started_at))
    print(json.dumps({"startedAt": started_at, "generatedAt": generated_at, "limbs": {"email": email_enabled, "calendar": calendar_enabled, "social": social_enabled}, "email": len(email), "calendar": len(calendar), "social": len(social)}))
    return 0


def event_time(value: str, time_zone: str | None, all_day: bool) -> dict[str, str]:
    if all_day:
        return {"date": value}
    result = {"dateTime": value}
    if time_zone:
        result["timeZone"] = time_zone
    return result


def calendar_create_body(fields: dict[str, Any], event_id: str, proposal_hash: str) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": event_id,
        "summary": fields["summary"],
        "start": event_time(str(fields["start"]), fields.get("timeZone"), bool(fields.get("allDay"))),
        "end": event_time(str(fields["end"]), fields.get("timeZone"), bool(fields.get("allDay"))),
        "extendedProperties": {"private": {"pixelProposalSha256": proposal_hash}},
    }
    for key in ("description", "location", "attendees"):
        if fields.get(key) is not None:
            body[key] = fields[key]
    return body


def calendar_event_matches_create(observed: dict[str, Any], expected: dict[str, Any]) -> bool:
    if observed.get("id") != expected.get("id") or observed.get("status", "confirmed") == "cancelled":
        return False
    for key in ("summary", "start", "end", "extendedProperties"):
        if observed.get(key) != expected.get(key):
            return False
    for key in ("description", "location", "attendees"):
        if observed.get(key) != expected.get(key):
            return False
    return True


LEGACY_UPDATE_RECONCILIATIONS = {
    "legacy-provider-state-match-after-indeterminate-write",
    "legacy-provider-etag-proves-not-applied",
}


def calendar_event_matches_update(observed: dict[str, Any], fields: dict[str, Any]) -> bool:
    """Compare only fields the exact approved PATCH was meant to change."""
    if observed.get("id") != fields["eventId"] or observed.get("status", "confirmed") == "cancelled":
        return False
    for key in ("summary", "description", "location"):
        if key in fields and observed.get(key, "") != fields[key]:
            return False
    if "attendees" in fields:
        observed_attendees = observed.get("attendees", [])
        if not isinstance(observed_attendees, list):
            return False
        approved_emails = sorted(item["email"].casefold() for item in fields["attendees"])
        provider_emails: list[str] = []
        for item in observed_attendees:
            if not isinstance(item, dict) or not isinstance(item.get("email"), str):
                return False
            provider_emails.append(item["email"].casefold())
        if sorted(provider_emails) != approved_emails:
            return False
    if "start" in fields:
        expected_start = event_time(str(fields["start"]), fields.get("timeZone"), bool(fields.get("allDay")))
        expected_end = event_time(str(fields["end"]), fields.get("timeZone"), bool(fields.get("allDay")))
        if observed.get("start") != expected_start or observed.get("end") != expected_end:
            return False
    return True


def protected_legacy_update_claim(path: Path, proposal_id: str, proposal_hash: str) -> tuple[dict[str, Any], str]:
    """Bind a pre-action-journal processing claim without inventing missing history."""
    claim, claim_hash = protected_proposal(path)
    required = {"schemaVersion", "proposalId", "proposalSha256", "status", "startedAt", "recovery"}
    if (
        set(claim) != required
        or claim.get("schemaVersion") != 1
        or claim.get("proposalId") != proposal_id
        or claim.get("proposalSha256") != proposal_hash
        or claim.get("status") != "processing"
        or not valid_iso_timestamp(claim.get("startedAt"))
        or not isinstance(claim.get("recovery"), str)
    ):
        raise RuntimeError("legacy Calendar update processing claim is invalid or belongs to another proposal")
    return claim, claim_hash


def legacy_update_reconciliation_result(
    proposal_id: str,
    proposal_hash: str,
    claim_hash: str,
    fields: dict[str, Any],
    provider_observation_sha256: str,
    *,
    desired_state_observed: bool,
) -> dict[str, Any]:
    applied = desired_state_observed
    return {
        "schemaVersion": 1,
        "proposalId": proposal_id,
        "status": "desired-state-observed" if applied else "not-applied",
        "action": "update",
        "affectedEventId": bounded_text(fields["eventId"], 1024),
        "attendeeNotifications": fields["sendUpdates"],
        "eventPrecondition": "if-match",
        "retryAllowed": False,
        "nextAction": "none" if applied else "fresh-proposal-required",
        "reasonCode": "desired-state-observed" if applied else "provider-precondition-unchanged",
        "proposalSha256": proposal_hash,
        "legacyProcessingClaimSha256": claim_hash,
        "providerObservationSha256": provider_observation_sha256,
        "approvalBinding": "sha256-protected-proposal-snapshot",
        "reconciliation": (
            "legacy-provider-state-match-after-indeterminate-write"
            if applied else "legacy-provider-etag-proves-not-applied"
        ),
        "desiredStateObserved": applied,
        "causationAsserted": False,
        "handledAt": iso(utc_now()),
        "boundary": (
            "The exact desired provider state was observed after an indeterminate legacy update; "
            "causation is not asserted and the approved write must not be retried."
            if applied else
            "The provider still has the exact precondition ETag, proving the approved legacy update did not apply; "
            "the old approval must not be retried and any new write requires a fresh proposal."
        ),
    }


def calendar_completed_result(
    proposal_id: str,
    proposal_hash: str,
    action: str,
    affected_id: str,
    required_status: str,
    *,
    reconciliation: str,
    attendee_notifications: str,
    provider_observation_sha256: str,
    action_journal_head_sha256: str,
    rollback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    completed = {
        "schemaVersion": 1,
        "proposalId": proposal_id,
        "status": "applied",
        "action": action,
        "affectedEventId": bounded_text(affected_id, 1024),
        "attendeeNotifications": attendee_notifications,
        "eventPrecondition": "if-match" if action in {"update", "delete"} else "deterministic-provider-event-id",
        "proposalSha256": proposal_hash,
        "providerObservationSha256": provider_observation_sha256,
        "actionJournalHeadSha256": action_journal_head_sha256,
        "approvalBinding": "bounded-direct-policy" if required_status == "pending-bounded-direct" else "sha256-protected-proposal-snapshot",
        "reconciliation": reconciliation,
        "appliedAt": iso(utc_now()),
    }
    if rollback is not None:
        completed["rollback"] = rollback
    return completed


def calendar_rejected_result(
    proposal_id: str,
    proposal_hash: str,
    action: str,
    affected_id: str,
    required_status: str,
    *,
    attendee_notifications: str,
    reason_code: str,
    provider_observation_sha256: str,
    action_journal_head_sha256: str,
) -> dict[str, Any]:
    """A terminal, sanitized, non-retryable result for a definitive provider rejection."""
    return {
        "schemaVersion": 1,
        "proposalId": proposal_id,
        "status": "rejected",
        "action": action,
        "affectedEventId": bounded_text(affected_id, 1024),
        "attendeeNotifications": attendee_notifications,
        "eventPrecondition": "if-match" if action in {"update", "delete"} else "deterministic-provider-event-id",
        "retryAllowed": False,
        "nextAction": "none",
        "reasonCode": reason_code,
        "proposalSha256": proposal_hash,
        "providerObservationSha256": provider_observation_sha256,
        "actionJournalHeadSha256": action_journal_head_sha256,
        "approvalBinding": "bounded-direct-policy" if required_status == "pending-bounded-direct" else "sha256-protected-proposal-snapshot",
        "reconciliation": "definitive-provider-rejection",
        "handledAt": iso(utc_now()),
        "boundary": "The provider definitively rejected the exact approved Calendar write; no mutation was applied and it must not be retried.",
    }


def validate_terminal_result(
    result_dir: Path,
    proposal_id: str,
    proposal_hash: str,
    action: str,
    required_status: str,
    *,
    affected_id: str,
    event_precondition: str,
    attendee_notifications: str,
) -> dict[str, Any] | None:
    """Return an existing terminal result only when it is bound to the exact approved proposal."""
    result_path = result_dir / f"{proposal_id}.json"
    if not os.path.lexists(result_path):
        return None
    settled, _settled_hash = protected_proposal(result_path)
    expected_binding = "bounded-direct-policy" if required_status == "pending-bounded-direct" else "sha256-protected-proposal-snapshot"
    if (
        settled.get("schemaVersion") != 1
        or settled.get("proposalId") != proposal_id
        or settled.get("proposalSha256") != proposal_hash
        or settled.get("action") != action
        or settled.get("affectedEventId") != bounded_text(affected_id, 1024)
        or settled.get("attendeeNotifications") != attendee_notifications
        or settled.get("eventPrecondition") != event_precondition
        or settled.get("approvalBinding") != expected_binding
    ):
        raise RuntimeError("existing Calendar result is not bound to the exact approved proposal")
    reconciliation = settled.get("reconciliation")
    if reconciliation in LEGACY_UPDATE_RECONCILIATIONS:
        claim_path = result_dir / f"{proposal_id}.processing.json"
        if (
            action != "update"
            or required_status != "pending-operator-approval"
            or os.path.lexists(result_dir / ".journal" / proposal_id)
            or not os.path.lexists(claim_path)
        ):
            raise RuntimeError("legacy Calendar update result has invalid recovery provenance")
        _claim, claim_hash = protected_legacy_update_claim(claim_path, proposal_id, proposal_hash)
        applied = reconciliation == "legacy-provider-state-match-after-indeterminate-write"
        if (
            settled.get("status") != ("desired-state-observed" if applied else "not-applied")
            or settled.get("retryAllowed") is not False
            or settled.get("nextAction") != ("none" if applied else "fresh-proposal-required")
            or settled.get("reasonCode") != ("desired-state-observed" if applied else "provider-precondition-unchanged")
            or settled.get("desiredStateObserved") is not applied
            or settled.get("causationAsserted") is not False
            or settled.get("legacyProcessingClaimSha256") != claim_hash
            or not re.fullmatch(r"[a-f0-9]{64}", str(settled.get("providerObservationSha256") or ""))
            or not valid_iso_timestamp(settled.get("handledAt"))
            or not isinstance(settled.get("boundary"), str)
        ):
            raise RuntimeError("legacy Calendar update result is not bound to the exact recovery observation")
        return settled
    if settled.get("status") not in {"applied", "rejected"}:
        raise RuntimeError("existing Calendar result is not bound to the exact approved proposal")
    # Fail closed: an existing terminal result is admissible only when the exact
    # proposal has a valid terminal shared action journal to bind it to. A missing
    # or non-terminal journal is a recovery anomaly, not a replayable outcome.
    status = ExternalActionJournal(result_dir / ".journal").status(proposal_id)
    if (
        not status["terminal"]
        or status["proposalSha256"] != proposal_hash
        or settled.get("providerObservationSha256") != status["observationSha256"]
        or settled.get("actionJournalHeadSha256") != status["headSha256"]
    ):
        raise RuntimeError("existing Calendar result differs from the terminal shared action journal")
    result_status = settled.get("status")
    if result_status == "applied":
        valid_reconciliation = (
            {"synchronous-provider-response", "provider-get-after-indeterminate-write", "journal-success-after-result-crash"}
            if action == "create"
            else {"synchronous-provider-response"}
        )
        if (
            status["state"] != "succeeded"
            or settled.get("reconciliation") not in valid_reconciliation
            or settled.get("retryAllowed") is True
            or not valid_iso_timestamp(settled.get("appliedAt"))
        ):
            raise RuntimeError("existing Calendar result status does not match the terminal shared action journal")
    else:
        if (
            status["state"] != "failed"
            or status["reasonCode"] != "provider-definite-rejection"
            or settled.get("retryAllowed") is not False
            or settled.get("nextAction") != "none"
            or settled.get("reasonCode") != "provider-definite-rejection"
            or settled.get("reconciliation") != "definitive-provider-rejection"
            or not valid_iso_timestamp(settled.get("handledAt"))
        ):
            raise RuntimeError("existing Calendar result status does not match the terminal shared action journal")
    return settled


def projection_placeholders(value: Any, path: str = "values") -> list[str]:
    if isinstance(value, str):
        return [path] if PROJECTION_PLACEHOLDER.fullmatch(value.strip()) else []
    if isinstance(value, list):
        return [found for index, item in enumerate(value) for found in projection_placeholders(item, f"{path}[{index}]")]
    if isinstance(value, dict):
        return [found for key, item in value.items() for found in projection_placeholders(item, f"{path}.{key}")]
    return []


def approve(proposal_id: str, *, required_status: str = "pending-operator-approval") -> int:
    if not env_enabled("PIXEL_LIMB_CALENDAR_ENABLED", True):
        raise RuntimeError("Calendar limb is disabled")
    if not re.fullmatch(r"calendar-[0-9]{13}-[a-f0-9]{8}", proposal_id):
        raise RuntimeError("unsafe proposal ID")
    result_dir = Path(os.environ.get("PIXEL_ACTION_RESULT_DIR", "/var/lib/pixel-source-broker/results"))
    proposal_path = result_dir / f"{proposal_id}.approved.json"
    result_path = result_dir / f"{proposal_id}.json"
    claim_path = result_dir / f"{proposal_id}.processing.json"
    value, proposal_hash = protected_proposal(proposal_path)
    action, fields = validate_proposal(value, proposal_id, required_status=required_status)
    expected_event_id = calendar_create_event_id(proposal_id, proposal_hash) if action == "create" else None
    affected_id = expected_event_id if action == "create" else str(fields.get("eventId") or "")
    event_precondition = "if-match" if action in {"update", "delete"} else "deterministic-provider-event-id"
    settled = validate_terminal_result(
        result_dir, proposal_id, proposal_hash, action, required_status,
        affected_id=affected_id, event_precondition=event_precondition,
        attendee_notifications=fields["sendUpdates"],
    )
    if settled is not None:
        # A correctly bound terminal result already exists. Validate and report it
        # without another provider call so an already-applied action cannot leave a
        # failed approval unit, while substituted or mismatched files are rejected.
        if settled.get("reconciliation") in LEGACY_UPDATE_RECONCILIATIONS:
            # The retained pre-action-journal claim is the only durable provenance
            # available for this legacy recovery. Keep it as terminal evidence.
            print(json.dumps(settled))
            return 0
        if os.path.lexists(claim_path):
            claim, _claim_hash = protected_proposal(claim_path)
            if (
                not isinstance(claim, dict)
                or claim.get("schemaVersion") != 1
                or claim.get("proposalId") != proposal_id
                or claim.get("proposalSha256") != proposal_hash
            ):
                raise RuntimeError("Calendar processing claim does not match the terminal result")
            claim_path.unlink(missing_ok=True)
        print(json.dumps(settled))
        return 0
    # A retained claim is durable evidence that an earlier actuator may already
    # have contacted the provider. This also covers pre-action-journal Pixel
    # releases: refuse before calendar_action_journal() can create or advance a
    # new journal for an outcome that is already indeterminate.
    if os.path.lexists(claim_path):
        raise RuntimeError(
            "pre-existing Calendar processing claim requires operator recovery; "
            "no new action-journal state was written and the proposal was not retried"
        )
    calendar_id_raw = os.environ.get("PIXEL_SOURCE_CALENDAR_ID", "primary")
    journal, journal_state = calendar_action_journal(
        result_dir, proposal_id, proposal_hash, action, calendar_id_raw, expected_event_id,
    )
    if journal_state["state"] != "proposed":
        raise RuntimeError("proposal is already processing or requires reconciliation; it was not retried")
    token_path = Path(os.environ.get("PIXEL_SOURCE_TOKEN_PATH", "/var/lib/pixel-source-broker/private/google-token.json"))
    token = access_token(token_path)
    calendar_id = urllib.parse.quote(calendar_id_raw, safe="")
    event_id = urllib.parse.quote(str(fields.get("eventId") or ""), safe="")
    rollback: dict[str, Any] | None = None
    method: str
    path: str
    params = {"sendUpdates": fields["sendUpdates"]}
    headers: dict[str, str] | None = None
    body: dict[str, Any] | None = None
    if action == "create":
        assert expected_event_id is not None
        body = calendar_create_body(fields, expected_event_id, proposal_hash)
        method, path = "POST", f"/calendars/{calendar_id}/events"
    elif action == "update":
        if required_status == "pending-bounded-direct":
            current = google_get(CALENDAR_API, f"/calendars/{calendar_id}/events/{event_id}", token, {
                "fields": "id,etag,start,end",
            })
            if current.get("etag") != fields["expectedEtag"]:
                raise RuntimeError("direct Calendar event changed before execution")
            rollback = {
                "kind": "time-only",
                "expectedCurrentEtag": bounded_text(current.get("etag"), 256),
                "start": projected_event_time(current.get("start")),
                "end": projected_event_time(current.get("end")),
            }
        body = {}
        for key in ("summary", "description", "location", "attendees"):
            if fields.get(key) is not None:
                body[key] = fields[key]
        if fields.get("start"):
            body["start"] = event_time(str(fields["start"]), fields.get("timeZone"), bool(fields.get("allDay")))
            body["end"] = event_time(str(fields["end"]), fields.get("timeZone"), bool(fields.get("allDay")))
        method, path, headers = "PATCH", f"/calendars/{calendar_id}/events/{event_id}", {"if-match": fields["expectedEtag"]}
    elif action == "delete":
        method, path, headers = "DELETE", f"/calendars/{calendar_id}/events/{event_id}", {"if-match": fields["expectedEtag"]}
    else:
        raise RuntimeError("unsupported proposal action")
    # Token refresh and bounded direct preflight reads are not mutations. The shared
    # state and legacy recovery claim are committed immediately before provider I/O.
    submission_started = False
    provider_call_started = False
    try:
        try:
            journal.begin_submit(proposal_id)
        except JournalError as error:
            raise RuntimeError("proposal is already processing or requires reconciliation; it was not retried") from error
        submission_started = True
        claim_proposal(claim_path, proposal_id, proposal_hash, expected_event_id=expected_event_id)
        provider_call_started = True
        response = google_call(CALENDAR_API, path, token, method=method, params=params, value=body, extra_headers=headers)
        affected_id = str(response.get("id") or fields.get("eventId") or "")
        if action == "create" and affected_id != expected_event_id:
            raise RuntimeError("Calendar create returned an unexpected provider event identity")
        provider_observation_sha256 = calendar_observation_sha256(response)
        journal.succeed(proposal_id, provider_observation_sha256)
    except Exception as error:
        if submission_started:
            settle_calendar_journal_error(journal, proposal_id, error, definitely_before_provider=not provider_call_started)
            if provider_call_started and _definite_provider_rejection(error):
                status = journal.status(proposal_id)
                rejected = calendar_rejected_result(
                    proposal_id, proposal_hash, action, affected_id, required_status,
                    attendee_notifications=fields["sendUpdates"], reason_code="provider-definite-rejection",
                    provider_observation_sha256=status["observationSha256"],
                    action_journal_head_sha256=status["headSha256"],
                )
                atomic_json(result_path, rejected)
                try:
                    claim_path.unlink()
                except OSError:
                    pass
                print(json.dumps(rejected))
                return 0
        raise
    completed = calendar_completed_result(
        proposal_id, proposal_hash, action, affected_id, required_status,
        reconciliation="synchronous-provider-response", attendee_notifications=fields["sendUpdates"],
        provider_observation_sha256=provider_observation_sha256,
        action_journal_head_sha256=journal.status(proposal_id)["headSha256"], rollback=rollback,
    )
    atomic_json(result_path, completed)
    try:
        claim_path.unlink()
    except OSError:
        pass
    print(json.dumps(completed))
    return 0


def reconcile_create(proposal_id: str) -> int:
    """Settle an indeterminate create by reading its precommitted provider identity."""
    if not env_enabled("PIXEL_LIMB_CALENDAR_ENABLED", True):
        raise RuntimeError("Calendar limb is disabled")
    if not re.fullmatch(r"calendar-[0-9]{13}-[a-f0-9]{8}", proposal_id):
        raise RuntimeError("unsafe proposal ID")
    result_dir = Path(os.environ.get("PIXEL_ACTION_RESULT_DIR", "/var/lib/pixel-source-broker/results"))
    proposal_path = result_dir / f"{proposal_id}.approved.json"
    result_path = result_dir / f"{proposal_id}.json"
    claim_path = result_dir / f"{proposal_id}.processing.json"
    value, proposal_hash = protected_proposal(proposal_path)
    action, fields = validate_proposal(value, proposal_id, required_status=str(value.get("status") or ""))
    if action != "create" or value.get("status") not in {"pending-operator-approval", "pending-bounded-direct"}:
        raise RuntimeError("only an indeterminate Calendar create can be reconciled")
    expected_event_id = calendar_create_event_id(proposal_id, proposal_hash)
    calendar_id_raw = os.environ.get("PIXEL_SOURCE_CALENDAR_ID", "primary")
    if os.path.lexists(result_path):
        settled, _settled_hash = protected_proposal(result_path)
        expected_binding = "bounded-direct-policy" if value["status"] == "pending-bounded-direct" else "sha256-protected-proposal-snapshot"
        if (
            settled.get("schemaVersion") != 1
            or settled.get("proposalId") != proposal_id
            or settled.get("proposalSha256") != proposal_hash
            or settled.get("status") != "applied"
            or settled.get("action") != "create"
            or settled.get("affectedEventId") != expected_event_id
            or settled.get("attendeeNotifications") != fields["sendUpdates"]
            or settled.get("eventPrecondition") != "deterministic-provider-event-id"
            or settled.get("approvalBinding") != expected_binding
            or settled.get("reconciliation") not in {"synchronous-provider-response", "provider-get-after-indeterminate-write", "journal-success-after-result-crash"}
        ):
            raise RuntimeError("existing Calendar result is not bound to the exact approved create")
        status = ExternalActionJournal(result_dir / ".journal").status(proposal_id)
        if (
            status["state"] != "succeeded"
            or status["proposalSha256"] != proposal_hash
            or settled.get("providerObservationSha256") != status["observationSha256"]
            or settled.get("actionJournalHeadSha256") != status["headSha256"]
            or settled.get("retryAllowed") is True
            or not valid_iso_timestamp(settled.get("appliedAt"))
        ):
            raise RuntimeError("existing Calendar result differs from the terminal shared action journal")
        claim_path.unlink(missing_ok=True)
        print(json.dumps(settled))
        return 0
    action_journal_path = result_dir / ".journal" / proposal_id
    if os.path.lexists(claim_path):
        # Pixel 3.2.2 claims predate both the shared action journal and the
        # expectedEventId binding. Validate that provenance before touching the
        # journal so a prior partial 4.2 attempt cannot make a legacy claim look
        # safe to migrate.
        preflight_claim, _preflight_claim_hash = protected_proposal(claim_path)
        if (
            not isinstance(preflight_claim, dict)
            or preflight_claim.get("schemaVersion") != 1
            or preflight_claim.get("proposalId") != proposal_id
            or preflight_claim.get("proposalSha256") != proposal_hash
            or preflight_claim.get("status") != "processing"
        ):
            raise RuntimeError("Calendar actuator journal is invalid or belongs to another create")
        if "expectedEventId" not in preflight_claim:
            raise RuntimeError(
                "pre-4.2 Calendar processing claim requires operator recovery; "
                "it was not migrated or reconciled"
            )
        if preflight_claim.get("expectedEventId") != expected_event_id:
            raise RuntimeError("Calendar actuator journal is invalid or belongs to another create")
        # A 4.2 create claim is written only after its action-specific journal
        # exists. Never synthesize that journal from a retained claim.
        if not os.path.lexists(action_journal_path):
            raise RuntimeError(
                "pre-existing Calendar processing claim has no shared action journal; "
                "it requires operator recovery and was not reconciled"
            )
    journal, journal_state = calendar_action_journal(
        result_dir, proposal_id, proposal_hash, action, calendar_id_raw, expected_event_id,
    )
    if os.path.lexists(claim_path):
        claim, _claim_hash = protected_proposal(claim_path)
        if (
            not isinstance(claim, dict)
            or claim.get("schemaVersion") != 1
            or claim.get("proposalId") != proposal_id
            or claim.get("proposalSha256") != proposal_hash
            or claim.get("status") != "processing"
            or claim.get("expectedEventId") != expected_event_id
        ):
            raise RuntimeError("Calendar actuator journal is invalid or belongs to another create")
    elif journal_state["state"] not in {"submitting", "unknown", "reconciling", "succeeded"}:
        raise RuntimeError("Calendar create has no durable submitted action journal")
    if journal_state["state"] == "proposed":  # Conservatively migrate a legacy processing claim.
        journal.begin_submit(proposal_id)
    current_state = journal.status(proposal_id)["state"]
    if current_state == "succeeded":
        status = journal.status(proposal_id)
        completed = calendar_completed_result(
            proposal_id, proposal_hash, action, expected_event_id, str(value["status"]),
            reconciliation="journal-success-after-result-crash", attendee_notifications=fields["sendUpdates"],
            provider_observation_sha256=status["observationSha256"], action_journal_head_sha256=status["headSha256"],
        )
        atomic_json(result_path, completed)
        claim_path.unlink(missing_ok=True)
        print(json.dumps(completed))
        return 0
    journal.begin_reconcile(proposal_id)
    token_path = Path(os.environ.get("PIXEL_SOURCE_TOKEN_PATH", "/var/lib/pixel-source-broker/private/google-token.json"))
    token = access_token(token_path)
    calendar_id = urllib.parse.quote(calendar_id_raw, safe="")
    event_id = urllib.parse.quote(expected_event_id, safe="")
    try:
        observed = google_get(CALENDAR_API, f"/calendars/{calendar_id}/events/{event_id}", token, {
            "fields": "id,status,summary,description,location,attendees(email),start,end,extendedProperties",
        })
    except UpstreamHTTPError as error:
        if error.code == 404:
            journal.mark_unknown(proposal_id, "provider-not-observable", calendar_observation_sha256({"httpCode": 404}))
            pending = {
                "schemaVersion": 1,
                "proposalId": proposal_id,
                "status": "unknown",
                "action": "create",
                "retryAllowed": False,
                "nextAction": "reconcile-later",
                "checkedAt": iso(utc_now()),
                "boundary": "The provider event is not yet observable. The original write remains indeterminate and must not be retried.",
            }
            print(json.dumps(pending))
            return 3
        settle_calendar_journal_error(journal, proposal_id, error)
        raise
    expected = calendar_create_body(fields, expected_event_id, proposal_hash)
    if not calendar_event_matches_create(observed, expected):
        journal.mark_unknown(proposal_id, "provider-identity-conflict", calendar_observation_sha256(observed))
        raise RuntimeError("Calendar provider identity exists but its event differs from the exact approved create")
    provider_observation_sha256 = calendar_observation_sha256(observed)
    journal.succeed(proposal_id, provider_observation_sha256, "provider-reconciliation-match")
    completed = calendar_completed_result(
        proposal_id, proposal_hash, action, expected_event_id, str(value["status"]),
        reconciliation="provider-get-after-indeterminate-write", attendee_notifications=fields["sendUpdates"],
        provider_observation_sha256=provider_observation_sha256,
        action_journal_head_sha256=journal.status(proposal_id)["headSha256"],
    )
    atomic_json(result_path, completed)
    claim_path.unlink(missing_ok=True)
    print(json.dumps(completed))
    return 0


def reconcile_update(proposal_id: str) -> int:
    """Classify a legacy indeterminate update using one read and no replay."""
    if not env_enabled("PIXEL_LIMB_CALENDAR_ENABLED", True):
        raise RuntimeError("Calendar limb is disabled")
    if not re.fullmatch(r"calendar-[0-9]{13}-[a-f0-9]{8}", proposal_id):
        raise RuntimeError("unsafe proposal ID")
    result_dir = Path(os.environ.get("PIXEL_ACTION_RESULT_DIR", "/var/lib/pixel-source-broker/results"))
    proposal_path = result_dir / f"{proposal_id}.approved.json"
    result_path = result_dir / f"{proposal_id}.json"
    claim_path = result_dir / f"{proposal_id}.processing.json"
    value, proposal_hash = protected_proposal(proposal_path)
    action, fields = validate_proposal(value, proposal_id, required_status=str(value.get("status") or ""))
    if action != "update" or value.get("status") != "pending-operator-approval":
        raise RuntimeError("only an indeterminate operator-approved Calendar update can be reconciled")
    settled = validate_terminal_result(
        result_dir, proposal_id, proposal_hash, action, str(value["status"]),
        affected_id=fields["eventId"], event_precondition="if-match",
        attendee_notifications=fields["sendUpdates"],
    )
    if settled is not None:
        print(json.dumps(settled))
        return 0
    if not os.path.lexists(claim_path):
        raise RuntimeError("legacy Calendar update has no durable processing claim")
    _claim, claim_hash = protected_legacy_update_claim(claim_path, proposal_id, proposal_hash)
    if os.path.lexists(result_dir / ".journal" / proposal_id):
        raise RuntimeError("Calendar update has a shared action journal and requires journal-aware reconciliation")

    def unknown(reason_code: str, observation_hash: str) -> int:
        pending = {
            "schemaVersion": 1,
            "proposalId": proposal_id,
            "status": "unknown",
            "action": "update",
            "retryAllowed": False,
            "nextAction": "operator-review",
            "reasonCode": reason_code,
            "proposalSha256": proposal_hash,
            "legacyProcessingClaimSha256": claim_hash,
            "providerObservationSha256": observation_hash,
            "checkedAt": iso(utc_now()),
            "boundary": "The legacy Calendar update remains indeterminate and must not be retried.",
        }
        print(json.dumps(pending))
        return 3

    token_path = Path(os.environ.get("PIXEL_SOURCE_TOKEN_PATH", "/var/lib/pixel-source-broker/private/google-token.json"))
    calendar_id_raw = os.environ.get("PIXEL_SOURCE_CALENDAR_ID", "primary")
    calendar_id = urllib.parse.quote(calendar_id_raw, safe="")
    event_id = urllib.parse.quote(fields["eventId"], safe="")
    try:
        token = access_token(token_path)
        observed = google_get(CALENDAR_API, f"/calendars/{calendar_id}/events/{event_id}", token, {
            "fields": "id,etag,status,summary,description,location,attendees(email),start,end",
        })
    except Exception as error:
        observation_hash = calendar_observation_sha256({
            "errorType": type(error).__name__,
            "httpCode": getattr(error, "code", None),
        })
        return unknown("provider-not-observable" if isinstance(error, UpstreamHTTPError) and error.code == 404 else "provider-read-unavailable", observation_hash)

    observation_hash = calendar_observation_sha256(observed)
    if (
        not isinstance(observed, dict)
        or observed.get("id") != fields["eventId"]
        or observed.get("status", "confirmed") == "cancelled"
        or not ETAG_PATTERN.fullmatch(str(observed.get("etag") or ""))
    ):
        return unknown("provider-observation-conflict", observation_hash)
    if calendar_event_matches_update(observed, fields):
        completed = legacy_update_reconciliation_result(
            proposal_id, proposal_hash, claim_hash, fields, observation_hash,
            desired_state_observed=True,
        )
        atomic_json(result_path, completed)
        print(json.dumps(completed))
        return 0
    if observed.get("etag") == fields["expectedEtag"]:
        completed = legacy_update_reconciliation_result(
            proposal_id, proposal_hash, claim_hash, fields, observation_hash,
            desired_state_observed=False,
        )
        atomic_json(result_path, completed)
        print(json.dumps(completed))
        return 0
    return unknown("provider-state-conflict", observation_hash)


def reconcile(proposal_id: str) -> int:
    """Dispatch recovery by the exact protected proposal action."""
    if not re.fullmatch(r"calendar-[0-9]{13}-[a-f0-9]{8}", proposal_id):
        raise RuntimeError("unsafe proposal ID")
    result_dir = Path(os.environ.get("PIXEL_ACTION_RESULT_DIR", "/var/lib/pixel-source-broker/results"))
    value, _proposal_hash = protected_proposal(result_dir / f"{proposal_id}.approved.json")
    action = value.get("action")
    if action == "create":
        return reconcile_create(proposal_id)
    if action == "update":
        return reconcile_update(proposal_id)
    raise RuntimeError("only an indeterminate Calendar create or update can be reconciled")


def direct(proposal_id: str) -> int:
    if not env_enabled("PIXEL_CALENDAR_DIRECT_ENABLED", False):
        raise RuntimeError("bounded direct Calendar execution is disabled")
    if not re.fullmatch(r"calendar-[0-9]{13}-[a-f0-9]{8}", proposal_id):
        raise RuntimeError("unsafe proposal ID")
    proposal_dir = Path(os.environ.get("PIXEL_ACTION_PROPOSAL_DIR", "/var/lib/pixel-source-broker/proposals"))
    result_dir = Path(os.environ.get("PIXEL_ACTION_RESULT_DIR", "/var/lib/pixel-source-broker/results"))
    source = proposal_dir / f"{proposal_id}.json"
    approved = result_dir / f"{proposal_id}.approved.json"
    with direct_action_lock(result_dir):
        enforce_direct_rate_limit(result_dir)
        if os.path.lexists(result_dir / f"{proposal_id}.json"):
            raise RuntimeError("proposal has already been processed")
        snapshot_direct_proposal(source, approved, proposal_id)
        return approve(proposal_id, required_status="pending-bounded-direct")


def drain_direct() -> int:
    proposal_dir = Path(os.environ.get("PIXEL_ACTION_PROPOSAL_DIR", "/var/lib/pixel-source-broker/proposals"))
    result_dir = Path(os.environ.get("PIXEL_ACTION_RESULT_DIR", "/var/lib/pixel-source-broker/results"))
    failures: list[str] = []
    for path in sorted(proposal_dir.glob("calendar-*.json")):
        proposal_id = path.stem
        if not re.fullmatch(r"calendar-[0-9]{13}-[a-f0-9]{8}", proposal_id):
            continue
        if os.path.lexists(result_dir / f"{proposal_id}.json") or os.path.lexists(result_dir / f"{proposal_id}.processing.json"):
            continue
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_PROPOSAL_BYTES:
                continue
            try:
                routing_payload = path.read_text(encoding="utf-8")
            except PermissionError:
                # Pre-boundary legacy proposals can have owner-only permissions. They
                # cannot be bounded-direct work, so leave them for the operator path.
                continue
            routing = json.loads(routing_payload)
            if not isinstance(routing, dict) or routing.get("status") != "pending-bounded-direct":
                continue
            direct(proposal_id)
        except Exception as error:
            failures.append(f"{proposal_id}: {bounded_text(error, 500)}")
    if failures:
        raise RuntimeError(f"bounded direct Calendar drain failed for {len(failures)} proposal(s): {'; '.join(failures)}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--approve", metavar="PROPOSAL_ID")
    parser.add_argument("--direct", metavar="PROPOSAL_ID")
    parser.add_argument("--reconcile", metavar="PROPOSAL_ID")
    parser.add_argument("--reconcile-create", metavar="PROPOSAL_ID")
    parser.add_argument("--reconcile-update", metavar="PROPOSAL_ID")
    parser.add_argument("--drain-direct", action="store_true")
    arguments = parser.parse_args()
    if sum(bool(item) for item in (arguments.approve, arguments.direct, arguments.reconcile, arguments.reconcile_create, arguments.reconcile_update, arguments.drain_direct)) > 1:
        parser.error("choose exactly one action")
    raise SystemExit(drain_direct() if arguments.drain_direct else reconcile(arguments.reconcile) if arguments.reconcile else reconcile_update(arguments.reconcile_update) if arguments.reconcile_update else reconcile_create(arguments.reconcile_create) if arguments.reconcile_create else direct(arguments.direct) if arguments.direct else approve(arguments.approve) if arguments.approve else refresh())
