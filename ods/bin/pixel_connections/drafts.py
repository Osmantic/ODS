"""Gmail drafts Portal writes, created only after the owner approves them in the dashboard.

Pixel's draft tool only writes a proposal. The owner approves the exact draft,
as with a calendar change (approvals.py): root freezes the proposal into a
snapshot only the Source Broker owns, the approval carries that snapshot's
SHA-256, and the broker's action unit asks Gmail to create the draft once.
Pixel has no tool that sends email; the owner sends the draft from Gmail.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import re
from typing import Any, Iterator

from . import approvals
from .approvals import (CONSIDERED, CONTROL, EMAIL, MAX_PROJECTION_BYTES, OPERATOR, ApprovalError, Ids, Run,
                        _clear_stale_snapshots, _frozen, _json, _names, _placeholder, _read, _state, _text, fit)

DRAFT_ID = re.compile(r"^gmail-draft-[0-9]{13}-[a-f0-9]{8}$")
DRAFT_FILE = re.compile(r"^(gmail-draft-[0-9]{13}-[a-f0-9]{8})\.json$")
# The broker's own limits and patterns (vendor/pixel/deploy/source-broker/broker.py).
BOUNDARY = "This file is a proposal only. No Gmail draft has been created."
FIELDS = frozenset({"to", "cc", "subject", "body", "replyToMessageId"})
MAX_RECIPIENTS = 50
MAX_BODY = 100_000
GMAIL_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


@contextmanager
def _named_as_drafts() -> Iterator[None]:
    """The shared snapshot and result checks name a calendar change; for a draft they name the draft."""
    try:
        yield
    except ApprovalError as error:
        raise ApprovalError(error.code.replace("calendar-change-", "gmail-draft-", 1)) from None


# ---------------------------------------------------------------- the broker's draft rules

def _recipients(fields: dict[str, Any], key: str, *, required: bool) -> list[str]:
    value = fields.get(key)
    if value is None and not required:
        return []
    if type(value) is not list or required and not value or len(value) > MAX_RECIPIENTS:
        raise ValueError(key)
    seen: set[str] = set()
    emails = []
    for item in value:
        if type(item) is not dict or set(item) != {"email"}:
            raise ValueError(key)
        email = _text(item, "email", 320, required=True)
        if email is None or not EMAIL.fullmatch(email) or email.casefold() in seen:
            raise ValueError(key)
        seen.add(email.casefold())
        emails.append(email)
    return emails


def validate(value: Any, proposal_id: str) -> dict[str, Any]:
    """The broker's validate_draft_proposal; ValueError where the broker refuses.

    The broker also refuses a draft whose text looks like a vendor credential.
    That rule stays in the broker, so such a draft fails when approved and is never created.
    """
    if (type(value) is not dict or set(value) != {"schemaVersion", "proposalId", "source", "action", "status",
                                                  "createdAt", "values", "boundary"}
            or value["schemaVersion"] != 1 or value["proposalId"] != proposal_id
            or not DRAFT_ID.fullmatch(proposal_id) or value["source"] != "pixel-owner-conversation"
            or value["status"] != OPERATOR or value["boundary"] != BOUNDARY
            or type(value["createdAt"]) is not str or value["action"] != "create-draft"
            or type(value["values"]) is not dict or not set(value["values"]) <= FIELDS):
        raise ValueError("proposal")
    fields = value["values"]
    to = _recipients(fields, "to", required=True)
    cc = _recipients(fields, "cc", required=False)
    if len(to) + len(cc) > MAX_RECIPIENTS or {item.casefold() for item in to} & {item.casefold() for item in cc}:
        raise ValueError("cc")
    subject = _text(fields, "subject", 998) or ""
    if "\r" in subject or "\n" in subject:
        raise ValueError("subject")
    body = _text(fields, "body", MAX_BODY, required=True)
    reply = fields.get("replyToMessageId")
    if reply is not None and (type(reply) is not str or not GMAIL_ID.fullmatch(reply)):
        raise ValueError("replyToMessageId")
    cleaned = {"to": to, "cc": cc, "subject": subject, "body": body, "replyToMessageId": reply}
    if _placeholder(cleaned):
        raise ValueError("values")
    return cleaned


# ---------------------------------------------------------------- what the owner sees

def _shown(value: Any, limit: int) -> str | None:
    return CONTROL.sub("", value)[:limit] if type(value) is str else None


def _originals(paths, owner: int, wanted: set[str]) -> dict[str, dict[str, Any]]:
    """Who sent the messages these drafts answer, and their subjects, from the broker's email summary.

    An unreadable summary names no message; the draft still shows that it is a reply.
    """
    if not wanted:
        return {}
    try:
        raw = _read(paths.projection / "email.json", owner=owner, limit=MAX_PROJECTION_BYTES)
        value = _json(raw) if raw is not None else None
    except (ApprovalError, UnicodeError, ValueError, RecursionError):
        return {}
    records = value.get("records") if type(value) is dict else None
    found: dict[str, dict[str, Any]] = {}
    for record in records if type(records) is list else []:
        message_id = record.get("id") if type(record) is dict else None
        if type(message_id) is str and message_id in wanted and message_id not in found:
            found[message_id] = {"from": _shown(record.get("from"), 320), "subject": _shown(record.get("subject"), 998)}
    return found


def _unreadable(proposal_id: str) -> dict[str, Any]:
    return {"proposalId": proposal_id, "sha256": None, "state": "invalid", "createdAt": None, "draft": None,
            "tooLarge": False}


def _entry(paths, ids: Ids, proposal_id: str) -> tuple[dict[str, Any], str | None] | None:
    """One draft as the owner sees it, and the message it answers; None when it is not the owner's to approve."""
    try:
        raw = _frozen(paths, ids, proposal_id)
    except ApprovalError:
        return _unreadable(proposal_id), None
    if raw is None:
        return None
    try:
        value = _json(raw)
        fields = validate(value, proposal_id)
    except (UnicodeError, ValueError, RecursionError):
        return _unreadable(proposal_id), None
    sha256 = hashlib.sha256(raw).hexdigest()
    try:
        state = _state(paths, proposal_id, sha256, ids.broker_uid)
    except ApprovalError:
        return _unreadable(proposal_id), None
    created = value["createdAt"] if len(value["createdAt"]) <= 64 and not CONTROL.search(value["createdAt"]) else None
    draft = {"to": fields["to"], "cc": fields["cc"], "subject": fields["subject"], "body": fields["body"],
             "replyTo": None}
    return ({"proposalId": proposal_id, "sha256": sha256, "state": state, "createdAt": created, "draft": draft,
             "tooLarge": False}, fields["replyToMessageId"])


def listing(paths, ids: Ids) -> dict[str, Any]:
    """The drafts that wait for the owner, oldest first, each with the hash that approves it."""
    names = _names(paths, DRAFT_FILE)
    if not names:
        return {"drafts": [], "more": 0}
    _clear_stale_snapshots(paths)
    built = [entry for entry in (_entry(paths, ids, name) for name in reversed(names[:CONSIDERED])) if entry]
    originals = _originals(paths, ids.broker_uid, {message for _entry_value, message in built if message})
    for entry, message in built:
        if message:
            # A reply; the summary may not hold the message it answers.
            entry["draft"]["replyTo"] = originals.get(message, {"from": None, "subject": None})
    shown, more = fit([entry for entry, _message in built], max(0, len(names) - CONSIDERED),
                      lambda entry: dict(entry, sha256=None, draft=None, tooLarge=True))
    return {"drafts": shown, "more": more}


def pending_count(paths) -> int:
    """How many drafts wait for the owner; for status, like approvals.pending_count."""
    return approvals.pending_count(paths, DRAFT_FILE)


# ---------------------------------------------------------------- the owner's actions

def approve(paths, run: Run, ids: Ids, request: dict[str, Any]) -> dict[str, Any]:
    """Have Gmail create exactly the draft the owner approved, once. Nothing is sent."""
    with _named_as_drafts():
        return approvals.approve(paths, run, ids, request, rules=validate)


def reconcile(paths, run: Run, ids: Ids, request: dict[str, Any]) -> dict[str, Any]:
    """Ask Gmail whether an unconfirmed draft exists, by the Message-ID the broker fixed; nothing is created twice."""
    with _named_as_drafts():
        return approvals.reconcile(paths, run, ids, request, checkable=frozenset({"create-draft"}))
