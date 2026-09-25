#!/usr/bin/env python3
"""Bounded GitHub creates with durable reconciliation instead of blind retries."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import re
import stat
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

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


API_HOST = "api.github.com"
API_VERSION = "2026-03-10"
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_PROPOSAL_BYTES = 256 * 1024
MAX_RECONCILE_PAGES = 10
BOUNDARY = "Exact operator-reviewed GitHub proposal only. It grants one bounded repository mutation and no merge, push, branch, credential, repository expansion, retry, or other provider authority."
RESULT_BOUNDARY = "Private provider-bound result for one exact GitHub action. It proves the observed object and journal settlement, not merge, deployment, semantic correctness, publication approval, or broader repository authority."
ACTION_ID = re.compile(r"^github-[0-9]{13}-[a-f0-9]{8,32}$")
def reject_outbound_secrets(cleaned: dict[str, Any]) -> None:
    # The proposal is owner-hash-confirmed so it cannot be redacted; a body carrying an unambiguous
    # vendor secret is REJECTED, because a leaked key in a (potentially public) GitHub issue/PR/
    # comment is catastrophic. Detection is the shared, high-precision vendor-format set.
    found = find_vendor_secret(*(cleaned.get(field, "") for field in ("title", "body")))
    if found:
        raise GitHubBrokerError(f"GitHub proposal body contains what looks like a {found}; a credential must not be published")
REPO_PART = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")
OPERATIONS = {"create-issue", "create-pull-request", "comment"}


class GitHubBrokerError(RuntimeError):
    pass


class GitHubHTTPError(GitHubBrokerError):
    def __init__(self, code: int, response_sha256: str) -> None:
        super().__init__(f"GitHub request failed with HTTP {code}")
        self.code = code
        self.response_sha256 = response_sha256


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise GitHubBrokerError(f"JSON contains duplicate key: {key}")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise GitHubBrokerError(f"JSON contains non-finite number: {value}")


def _read_private(path: Path, maximum: int, label: str) -> bytes:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > maximum:
        raise GitHubBrokerError(f"{label} is not a bounded singly linked regular file")
    if os.name != "nt" and info.st_mode & 0o077:
        raise GitHubBrokerError(f"{label} is not owner-private")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino, opened.st_size) != (info.st_dev, info.st_ino, info.st_size):
            raise GitHubBrokerError(f"{label} changed during secure open")
        payload = bytearray()
        while True:
            chunk = os.read(descriptor, min(65536, maximum + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
            if len(payload) > maximum:
                raise GitHubBrokerError(f"{label} exceeds its byte limit")
        return bytes(payload)
    finally:
        os.close(descriptor)


def _json(path: Path, maximum: int, label: str) -> tuple[dict[str, Any], bytes]:
    payload = _read_private(path, maximum, label)
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (UnicodeError, ValueError) as error:
        raise GitHubBrokerError(f"{label} is not strict UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise GitHubBrokerError(f"{label} is not an object")
    return value, payload


def _string(value: Any, label: str, *, minimum: int = 1, maximum: int) -> str:
    if not isinstance(value, str) or not minimum <= len(value) <= maximum or "\x00" in value:
        raise GitHubBrokerError(f"{label} is invalid")
    return value


def validate_policy(value: dict[str, Any]) -> dict[str, Any]:
    keys = {"schemaVersion", "enabled", "allowedRepositories", "allowedOperations", "maxBodyBytes", "maxReconcilePages", "apiVersion", "boundary"}
    if set(value) != keys or value.get("schemaVersion") != 1 or value.get("enabled") is not True:
        raise GitHubBrokerError("GitHub broker policy is invalid or disabled")
    repositories = value.get("allowedRepositories")
    operations = value.get("allowedOperations")
    if not isinstance(repositories, list) or not 1 <= len(repositories) <= 100 or len(set(repositories)) != len(repositories):
        raise GitHubBrokerError("GitHub repository allowlist is invalid")
    for repository in repositories:
        parts = repository.split("/") if isinstance(repository, str) else []
        if len(parts) != 2 or not all(REPO_PART.fullmatch(part) for part in parts):
            raise GitHubBrokerError("GitHub repository allowlist contains an invalid repository")
    if not isinstance(operations, list) or not operations or len(set(operations)) != len(operations) or not set(operations) <= OPERATIONS:
        raise GitHubBrokerError("GitHub operation allowlist is invalid")
    if not isinstance(value.get("maxBodyBytes"), int) or not 0 <= value["maxBodyBytes"] <= 65536:
        raise GitHubBrokerError("GitHub body limit is invalid")
    if not isinstance(value.get("maxReconcilePages"), int) or not 1 <= value["maxReconcilePages"] <= MAX_RECONCILE_PAGES:
        raise GitHubBrokerError("GitHub reconciliation page limit is invalid")
    if value.get("apiVersion") != API_VERSION or value.get("boundary") != "Owner-private allowlist only. Policy grants no credentials, proposal approval, retries, merges, pushes, branch mutation, or repositories not named here.":
        raise GitHubBrokerError("GitHub policy boundary or API version is invalid")
    return value


def validate_proposal(value: dict[str, Any], policy: dict[str, Any], expected_action_id: str) -> tuple[str, str, dict[str, Any], str]:
    keys = {"schemaVersion", "actionId", "source", "operation", "status", "createdAt", "repository", "values", "boundary"}
    if set(value) != keys or value.get("schemaVersion") != 1 or value.get("actionId") != expected_action_id or not ACTION_ID.fullmatch(expected_action_id):
        raise GitHubBrokerError("GitHub proposal identity is invalid")
    if value.get("source") != "pixel-owner-conversation" or value.get("status") != "pending-operator-approval" or value.get("boundary") != BOUNDARY:
        raise GitHubBrokerError("GitHub proposal approval boundary is invalid")
    try:
        created = datetime.fromisoformat(str(value.get("createdAt")).replace("Z", "+00:00"))
    except ValueError as error:
        raise GitHubBrokerError("GitHub proposal creation time is invalid") from error
    if created.tzinfo is None:
        raise GitHubBrokerError("GitHub proposal creation time is not timezone-aware")
    operation = value.get("operation")
    repository = value.get("repository")
    if operation not in policy["allowedOperations"] or repository not in policy["allowedRepositories"]:
        raise GitHubBrokerError("GitHub proposal exceeds the repository or operation allowlist")
    values = value.get("values")
    if not isinstance(values, dict):
        raise GitHubBrokerError("GitHub proposal values are invalid")
    maximum = policy["maxBodyBytes"]
    cleaned: dict[str, Any]
    if operation == "create-issue":
        if not set(values) <= {"title", "body", "labels"} or "title" not in values:
            raise GitHubBrokerError("GitHub issue proposal shape is invalid")
        labels = values.get("labels", [])
        if not isinstance(labels, list) or len(labels) > 20 or any(not isinstance(item, str) or not 1 <= len(item) <= 100 for item in labels):
            raise GitHubBrokerError("GitHub issue labels are invalid")
        cleaned = {"title": _string(values["title"], "GitHub issue title", maximum=256), "body": _string(values.get("body", ""), "GitHub issue body", minimum=0, maximum=maximum), "labels": labels}
    elif operation == "create-pull-request":
        required = {"title", "body", "head", "base", "draft"}
        if set(values) != required or not isinstance(values.get("draft"), bool):
            raise GitHubBrokerError("GitHub pull request proposal shape is invalid")
        cleaned = {
            "title": _string(values["title"], "GitHub pull request title", maximum=256),
            "body": _string(values["body"], "GitHub pull request body", minimum=0, maximum=maximum),
            "head": _string(values["head"], "GitHub pull request head", maximum=255),
            "base": _string(values["base"], "GitHub pull request base", maximum=255),
            "draft": values["draft"],
        }
    elif operation == "comment":
        if set(values) != {"issueNumber", "body"} or not isinstance(values.get("issueNumber"), int) or not 1 <= values["issueNumber"] <= 2_147_483_647:
            raise GitHubBrokerError("GitHub comment proposal shape is invalid")
        cleaned = {"issueNumber": values["issueNumber"], "body": _string(values["body"], "GitHub comment body", maximum=maximum)}
    else:
        raise GitHubBrokerError("GitHub proposal operation is unsupported")
    reject_outbound_secrets(cleaned)
    return operation, repository, cleaned, created.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def marker(action_id: str, proposal_sha256: str) -> str:
    identity = hashlib.sha256(f"{action_id}:{proposal_sha256}".encode("ascii")).hexdigest()
    return f"<!-- pixel-action:{identity} -->"


def marked_body(body: str, action_marker: str) -> str:
    return f"{body.rstrip()}\n\n{action_marker}\n" if body.rstrip() else f"{action_marker}\n"


def _token(path: Path) -> str:
    token = _read_private(path, 4096, "GitHub credential").decode("utf-8").strip()
    if not re.fullmatch(r"(?:github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,})", token):
        raise GitHubBrokerError("GitHub credential format is invalid")
    return token


def github_request(token: str, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[Any, dict[str, str]]:
    if not path.startswith("/") or "//" in path or "\r" in path or "\n" in path:
        raise GitHubBrokerError("GitHub API path is invalid")
    payload = None if body is None else json.dumps(body, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    headers = {
        "Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": API_VERSION, "User-Agent": "Pixel-Personal-Helper/4.1",
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
    connection = http.client.HTTPSConnection(API_HOST, timeout=30)
    try:
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        raw = response.read(MAX_JSON_BYTES + 1)
        response_headers = {key.lower(): value for key, value in response.getheaders()}
    finally:
        connection.close()
    if len(raw) > MAX_JSON_BYTES:
        raise GitHubBrokerError("GitHub response exceeds its byte limit")
    if response.status < 200 or response.status >= 300:
        raise GitHubHTTPError(response.status, journal_sha256(raw))
    try:
        value = json.loads(raw.decode("utf-8")) if raw else None
    except (UnicodeError, ValueError) as error:
        raise GitHubBrokerError("GitHub response is not valid UTF-8 JSON") from error
    return value, response_headers


def _paths(repository: str, operation: str, values: dict[str, Any]) -> tuple[str, str]:
    owner, repo = (urllib.parse.quote(part, safe="") for part in repository.split("/"))
    base = f"/repos/{owner}/{repo}"
    if operation == "create-issue":
        return f"{base}/issues", f"{base}/issues"
    if operation == "create-pull-request":
        return f"{base}/pulls", f"{base}/pulls"
    number = values["issueNumber"]
    return f"{base}/issues/{number}/comments", f"{base}/issues/{number}/comments"


def _request_body(operation: str, values: dict[str, Any], action_marker: str) -> dict[str, Any]:
    body = marked_body(values.get("body", ""), action_marker)
    if operation == "create-issue":
        return {"title": values["title"], "body": body, "labels": values["labels"]}
    if operation == "create-pull-request":
        return {"title": values["title"], "body": body, "head": values["head"], "base": values["base"], "draft": values["draft"]}
    return {"body": body}


def _observation(value: Any) -> str:
    return journal_sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True))


def _result(action_id: str, proposal_hash: str, operation: str, repository: str, observed: dict[str, Any], reconciliation: str) -> dict[str, Any]:
    number = observed.get("number") if operation != "comment" else observed.get("id")
    if not isinstance(number, int) or number < 1:
        raise GitHubBrokerError("GitHub response has no valid provider object identifier")
    url = observed.get("html_url")
    if not isinstance(url, str) or not url.startswith("https://github.com/") or len(url) > 2048:
        raise GitHubBrokerError("GitHub response URL is invalid")
    return {
        "schemaVersion": 1, "actionId": action_id, "status": "applied", "operation": operation,
        "repository": repository, "providerObjectId": number, "providerUrl": url,
        "proposalSha256": proposal_hash, "observationSha256": provider_observation_sha256(operation, observed),
        "reconciliation": reconciliation, "settledAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "boundary": RESULT_BOUNDARY,
    }


def validate_result(value: dict[str, Any], *, action_id: str, proposal_hash: str, operation: str, repository: str) -> dict[str, Any]:
    keys = {"schemaVersion", "actionId", "status", "operation", "repository", "providerObjectId", "providerUrl", "proposalSha256", "observationSha256", "reconciliation", "settledAt", "boundary"}
    if set(value) != keys or value.get("schemaVersion") != 1 or value.get("actionId") != action_id or value.get("proposalSha256") != proposal_hash:
        raise GitHubBrokerError("existing GitHub result differs from the exact proposal")
    if value.get("status") != "applied" or value.get("operation") != operation or value.get("repository") != repository or value.get("boundary") != RESULT_BOUNDARY:
        raise GitHubBrokerError("existing GitHub result has an invalid action boundary")
    if not isinstance(value.get("providerObjectId"), int) or value["providerObjectId"] < 1 or not SHA256.fullmatch(str(value.get("observationSha256") or "")):
        raise GitHubBrokerError("existing GitHub result has an invalid provider identity")
    if not isinstance(value.get("providerUrl"), str) or not value["providerUrl"].startswith(f"https://github.com/{repository}/") or len(value["providerUrl"]) > 2048:
        raise GitHubBrokerError("existing GitHub result has an invalid provider URL")
    if value.get("reconciliation") not in {"synchronous-provider-response", "provider-list-after-indeterminate-write"}:
        raise GitHubBrokerError("existing GitHub result has an invalid reconciliation mode")
    try:
        settled = datetime.fromisoformat(str(value.get("settledAt")).replace("Z", "+00:00"))
    except ValueError as error:
        raise GitHubBrokerError("existing GitHub result has an invalid settlement time") from error
    if settled.tzinfo is None:
        raise GitHubBrokerError("existing GitHub result settlement time is not timezone-aware")
    return value


def _write_result(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent_info = path.parent.lstat()
    if not stat.S_ISDIR(parent_info.st_mode) or stat.S_ISLNK(parent_info.st_mode) or (os.name != "nt" and parent_info.st_mode & 0o077):
        raise GitHubBrokerError("GitHub result directory is not owner-private")
    if os.path.lexists(path):
        raise GitHubBrokerError("GitHub action result already exists")
    payload = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode("utf-8")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise
    if os.name != "nt":
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def _load(proposal_path: Path, policy_path: Path, action_id: str, expected_proposal_sha256: str | None = None) -> tuple[dict[str, Any], str, str, dict[str, Any], str, str]:
    policy, _policy_bytes = _json(policy_path, 65536, "GitHub policy")
    validate_policy(policy)
    proposal, proposal_bytes = _json(proposal_path, MAX_PROPOSAL_BYTES, "GitHub proposal")
    proposal_hash = journal_sha256(proposal_bytes)
    # The bytes that are journaled and acted upon must be the exact bytes the operator
    # confirmed. main() pre-checks the sha on an earlier read; enforcing it here on the
    # operative read closes the TOCTOU window where a concurrent local writer could swap
    # the proposal file between the confirmation read and this one.
    if expected_proposal_sha256 is not None and proposal_hash != expected_proposal_sha256:
        raise GitHubBrokerError("GitHub proposal hash differs from exact confirmation")
    operation, repository, values, created_at = validate_proposal(proposal, policy, action_id)
    return policy, operation, repository, values, created_at, proposal_hash


def apply_action(
    *, action_id: str, proposal_path: Path, policy_path: Path, token_path: Path,
    journal_root: Path, result_path: Path, request: Callable[..., tuple[Any, dict[str, str]]] = github_request,
    expected_proposal_sha256: str | None = None,
) -> dict[str, Any]:
    policy, operation, repository, values, _created_at, proposal_hash = _load(proposal_path, policy_path, action_id, expected_proposal_sha256)
    if os.path.lexists(result_path):
        raise GitHubBrokerError("GitHub action result already exists; the provider was not called")
    action_marker = marker(action_id, proposal_hash)
    journal = ExternalActionJournal(journal_root)
    state = journal.propose(
        action_id=action_id, connector="github", operation=operation, proposal_sha256=proposal_hash,
        idempotency_key_sha256=journal_sha256(action_marker), idempotency_mode="provider-reconciliation-marker",
        provider_target_sha256=journal_sha256(repository),
    )
    if state["state"] != "proposed":
        raise GitHubBrokerError("GitHub action is already processing or requires reconciliation; it was not retried")
    token = _token(token_path)
    submit_path, _reconcile_path = _paths(repository, operation, values)
    body = _request_body(operation, values, action_marker)
    if len(body["body"].encode("utf-8")) > policy["maxBodyBytes"]:
        raise GitHubBrokerError("GitHub marked body exceeds the policy byte limit")
    provider_call_started = False
    try:
        journal.begin_submit(action_id)
        provider_call_started = True
        observed, _headers = request(token, "POST", submit_path, body)
        if not isinstance(observed, dict) or not _matches(operation, values, body, observed):
            raise GitHubBrokerError("GitHub create response differs from the exact marked proposal")
        receipt = _result(action_id, proposal_hash, operation, repository, observed, "synchronous-provider-response")
        journal.succeed(action_id, receipt["observationSha256"])
    except Exception as error:
        try:
            head = journal.status(action_id)["state"]
            if head in {"submitting", "reconciling"}:
                observation = error.response_sha256 if isinstance(error, GitHubHTTPError) else _observation({"errorType": type(error).__name__, "httpCode": getattr(error, "code", None)})
                if not provider_call_started or isinstance(error, GitHubHTTPError) and error.code in {400, 401, 403, 404, 410, 422}:
                    journal.fail(action_id, "provider-definite-rejection" if provider_call_started else "local-before-provider-call", observation)
                else:
                    journal.mark_unknown(action_id, "provider-outcome-unknown", observation)
        except JournalError:
            pass
        raise
    _write_result(result_path, receipt)
    return receipt


def _matches(operation: str, values: dict[str, Any], expected_body: dict[str, Any], item: Any) -> bool:
    if not isinstance(item, dict) or item.get("body") != expected_body["body"]:
        return False
    if operation == "create-issue":
        labels = item.get("labels")
        observed_labels = [label.get("name") for label in labels] if isinstance(labels, list) and all(isinstance(label, dict) for label in labels) else []
        return "pull_request" not in item and item.get("title") == values["title"] and {str(label).casefold() for label in observed_labels} == {label.casefold() for label in values["labels"]}
    if operation == "create-pull-request":
        head, base = item.get("head"), item.get("base")
        return item.get("title") == values["title"] and isinstance(head, dict) and isinstance(base, dict) and (head.get("label") == values["head"] or head.get("ref") == values["head"].split(":")[-1]) and base.get("ref") == values["base"] and bool(item.get("draft")) is values["draft"]
    return isinstance(item.get("id"), int)


def provider_observation_sha256(operation: str, observed: dict[str, Any]) -> str:
    """Bind stable provider identity and exact semantic fields, excluding volatile counters."""
    projection: dict[str, Any] = {
        "id": observed.get("id"), "number": observed.get("number"), "htmlUrl": observed.get("html_url"),
        "title": observed.get("title"), "bodySha256": journal_sha256(str(observed.get("body") or "")),
    }
    if operation == "create-issue":
        labels = observed.get("labels")
        projection["labels"] = sorted(str(item.get("name")).casefold() for item in labels if isinstance(item, dict)) if isinstance(labels, list) else []
    elif operation == "create-pull-request":
        projection.update({
            "head": observed.get("head", {}).get("label") if isinstance(observed.get("head"), dict) else None,
            "headRef": observed.get("head", {}).get("ref") if isinstance(observed.get("head"), dict) else None,
            "baseRef": observed.get("base", {}).get("ref") if isinstance(observed.get("base"), dict) else None,
            "draft": observed.get("draft"),
        })
    return _observation(projection)


def reconcile_action(
    *, action_id: str, proposal_path: Path, policy_path: Path, token_path: Path,
    journal_root: Path, result_path: Path, request: Callable[..., tuple[Any, dict[str, str]]] = github_request,
    expected_proposal_sha256: str | None = None,
) -> dict[str, Any]:
    policy, operation, repository, values, created_at, proposal_hash = _load(proposal_path, policy_path, action_id, expected_proposal_sha256)
    journal = ExternalActionJournal(journal_root)
    if os.path.lexists(result_path):
        result, _bytes = _json(result_path, 65536, "GitHub result")
        validate_result(result, action_id=action_id, proposal_hash=proposal_hash, operation=operation, repository=repository)
        try:
            status = journal.status(action_id)
        except (JournalError, FileNotFoundError) as error:
            raise GitHubBrokerError("existing GitHub result has no valid shared action journal") from error
        if status["state"] != "succeeded" or status["proposalSha256"] != proposal_hash or status["observationSha256"] != result["observationSha256"]:
            raise GitHubBrokerError("existing GitHub result differs from the terminal action journal")
        return result
    action_marker = marker(action_id, proposal_hash)
    state = journal.propose(
        action_id=action_id, connector="github", operation=operation, proposal_sha256=proposal_hash,
        idempotency_key_sha256=journal_sha256(action_marker), idempotency_mode="provider-reconciliation-marker",
        provider_target_sha256=journal_sha256(repository),
    )
    if state["state"] == "proposed":
        raise GitHubBrokerError("GitHub action was never submitted and has nothing to reconcile")
    journal_already_succeeded = state["state"] == "succeeded"
    if not journal_already_succeeded:
        journal.begin_reconcile(action_id)
    token = _token(token_path)
    _submit_path, list_path = _paths(repository, operation, values)
    expected = _request_body(operation, values, action_marker)
    if len(expected["body"].encode("utf-8")) > policy["maxBodyBytes"]:
        raise GitHubBrokerError("GitHub marked body exceeds the policy byte limit")
    since = (datetime.fromisoformat(created_at.replace("Z", "+00:00")) - timedelta(minutes=5)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    matches: list[dict[str, Any]] = []
    try:
        for page in range(1, policy["maxReconcilePages"] + 1):
            parameters = {"per_page": "100", "page": str(page)}
            if operation != "create-pull-request":
                parameters["since"] = since
            else:
                parameters.update({"state": "all", "sort": "created", "direction": "desc"})
            observed, _headers = request(token, "GET", f"{list_path}?{urllib.parse.urlencode(parameters)}", None)
            if not isinstance(observed, list):
                raise GitHubBrokerError("GitHub reconciliation response is not a list")
            matches.extend(item for item in observed if _matches(operation, values, expected, item))
            if len(observed) < 100:
                break
        if len(matches) != 1:
            if not journal_already_succeeded:
                journal.mark_unknown(action_id, "provider-not-observable" if not matches else "provider-identity-conflict", _observation({"matchCount": len(matches)}))
            raise GitHubBrokerError("GitHub action remains indeterminate; the exact reconciliation marker was not uniquely observable")
        receipt = _result(action_id, proposal_hash, operation, repository, matches[0], "provider-list-after-indeterminate-write")
        if journal_already_succeeded:
            if state.get("observationSha256") != receipt["observationSha256"]:
                raise GitHubBrokerError("GitHub provider object differs from the response already bound in the journal")
        else:
            journal.succeed(action_id, receipt["observationSha256"], "provider-reconciliation-match")
    except Exception as error:
        try:
            if journal.status(action_id)["state"] == "reconciling":
                journal.mark_unknown(action_id, "provider-reconcile-error", _observation({"errorType": type(error).__name__, "httpCode": getattr(error, "code", None)}))
        except JournalError:
            pass
        raise
    _write_result(result_path, receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("apply", "reconcile"))
    parser.add_argument("--action-id", required=True)
    parser.add_argument("--proposal", type=Path, required=True)
    parser.add_argument("--proposal-sha256", required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--token", type=Path, required=True)
    parser.add_argument("--journal-root", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--confirm", action="store_true")
    arguments = parser.parse_args()
    if not arguments.confirm or not SHA256.fullmatch(arguments.proposal_sha256):
        parser.error("exact --proposal-sha256 and --confirm are required")
    observed = journal_sha256(_read_private(arguments.proposal, MAX_PROPOSAL_BYTES, "GitHub proposal"))
    if observed != arguments.proposal_sha256:
        raise GitHubBrokerError("GitHub proposal hash differs from exact confirmation")
    function = apply_action if arguments.command == "apply" else reconcile_action
    result = function(
        action_id=arguments.action_id, proposal_path=arguments.proposal, policy_path=arguments.policy,
        token_path=arguments.token, journal_root=arguments.journal_root, result_path=arguments.result,
        expected_proposal_sha256=arguments.proposal_sha256,
    )
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
