#!/usr/bin/env python3
"""Collect exact private Assistant runtime custody from the real Pixel control path.

This adapter does not execute a model and does not grade an answer.  It consumes one
already-settled ``ControlState.chat_turn`` record, its trusted portal tool bundle, and only
the action-journal chains named by that turn.  The returned envelope remains private and is
intended for ``portal_outcome_assistant_evidence.validate_and_emit``.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Iterable

import portal_outcome_assistant_evidence as assistant_evidence
import portal_outcome_evaluation as evaluation


ACTION_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,63}-[0-9]{13}-[a-f0-9]{8,24}$")
EVENT_RE = re.compile(r"^[0-9]{6}\.json$")
SHA_RE = re.compile(r"^[a-f0-9]{64}$")
MAX_JOURNAL_ROOTS = 8
MAX_ACTIONS_PER_ROOT = 128
MAX_EVENTS_PER_ACTION = 1000
MAX_EVENT_BYTES = 64 * 1024


def _private_directory(path: Path, label: str) -> Path:
    if not path.is_absolute() or Path(os.path.abspath(path)) != path or path == Path(path.anchor):
        raise evaluation.OutcomeError(f"{label} is not an exact absolute directory")
    try:
        info = path.lstat()
        actual = path.resolve(strict=True)
    except OSError as exc:
        raise evaluation.OutcomeError(f"{label} is unavailable") from exc
    if actual != path or not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise evaluation.OutcomeError(f"{label} is linked or not a directory")
    if os.name != "nt" and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077):
        raise evaluation.OutcomeError(f"{label} is not owner-private")
    return path


def _private_json(path: Path, label: str, ceiling: int) -> tuple[dict[str, Any], bytes]:
    try:
        before = path.lstat()
    except OSError as exc:
        raise evaluation.OutcomeError(f"{label} is unavailable") from exc
    if (
        not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode) or before.st_nlink != 1
        or not 1 <= before.st_size <= ceiling
    ):
        raise evaluation.OutcomeError(f"{label} is not a singular bounded file")
    if os.name != "nt" and (before.st_uid != os.geteuid() or stat.S_IMODE(before.st_mode) & 0o077):
        raise evaluation.OutcomeError(f"{label} is not owner-private")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if opened.st_dev != before.st_dev or opened.st_ino != before.st_ino:
            raise evaluation.OutcomeError(f"{label} changed while opened")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            payload = handle.read(ceiling + 1)
    finally:
        os.close(descriptor)
    if len(payload) != before.st_size:
        raise evaluation.OutcomeError(f"{label} changed while read")
    value = evaluation.parse_json(payload, label)
    if not isinstance(value, dict):
        raise evaluation.OutcomeError(f"{label} is not an object")
    return value, payload


def _journal_chain(action: Path) -> dict[str, Any]:
    action = _private_directory(action, "Assistant action journal")
    if ACTION_ID_RE.fullmatch(action.name) is None:
        raise evaluation.OutcomeError("Assistant action journal identity is unsafe")
    entries = sorted(action.iterdir(), key=lambda item: item.name)
    unexpected = [entry.name for entry in entries if entry.name not in {".head", ".lock"} and EVENT_RE.fullmatch(entry.name) is None]
    if unexpected:
        raise evaluation.OutcomeError("Assistant action journal contains an unexpected entry")
    event_paths = [entry for entry in entries if EVENT_RE.fullmatch(entry.name)]
    if not 2 <= len(event_paths) <= MAX_EVENTS_PER_ACTION:
        raise evaluation.OutcomeError("Assistant action journal is incomplete or excessive")
    events = []
    prior = None
    for sequence, path in enumerate(event_paths):
        if path.name != f"{sequence:06d}.json":
            raise evaluation.OutcomeError("Assistant action journal has a sequence gap")
        event, payload = _private_json(path, "Assistant action journal event", MAX_EVENT_BYTES)
        canonical = (json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("utf-8")
        if payload != canonical or event.get("sequence") != sequence or event.get("previousEventSha256") != prior:
            raise evaluation.OutcomeError("Assistant action journal event is noncanonical or unchained")
        prior = hashlib.sha256(payload).hexdigest()
        events.append(event)
    head, head_payload = _private_json(action / ".head", "Assistant action journal head", 1024)
    expected_head = {"eventSha256": prior, "sequence": len(events) - 1}
    canonical_head = (json.dumps(expected_head, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("utf-8")
    if head != expected_head or head_payload != canonical_head:
        raise evaluation.OutcomeError("Assistant action journal durable head differs from its chain")
    return {"actionId": action.name, "events": events, "headSha256": prior}


def _required_journal_heads(bundle: dict[str, Any] | None) -> set[str]:
    heads: set[str] = set()
    if bundle is None:
        return heads
    for receipt in bundle["receipts"]:
        action = receipt.get("outcome", {}).get("evidence", {}).get("action")
        head = action.get("actionJournalHeadSha256") if isinstance(action, dict) else None
        if head is not None:
            if not isinstance(head, str) or SHA_RE.fullmatch(head) is None:
                raise evaluation.OutcomeError("Assistant tool custody contains an invalid action journal head")
            heads.add(head)
    return heads


def _select_journal_chains(roots: Iterable[Path], required_heads: set[str]) -> list[dict[str, Any]]:
    roots = [Path(root) for root in roots]
    if len(roots) > MAX_JOURNAL_ROOTS or len({str(root) for root in roots}) != len(roots):
        raise evaluation.OutcomeError("Assistant action journal roots are duplicated or excessive")
    selected: dict[str, dict[str, Any]] = {}
    for root in roots:
        root = _private_directory(root, "Assistant action journal root")
        actions = sorted(root.iterdir(), key=lambda item: item.name)
        if len(actions) > MAX_ACTIONS_PER_ROOT:
            raise evaluation.OutcomeError("Assistant action journal root is excessive")
        for action in actions:
            chain = _journal_chain(action)
            head = chain["headSha256"]
            if head not in required_heads:
                continue
            if head in selected:
                raise evaluation.OutcomeError("Assistant action journal head is duplicated across roots")
            selected[head] = chain
    if set(selected) != required_heads:
        raise evaluation.OutcomeError("Assistant action journal custody is missing for a trusted tool result")
    return [selected[head] for head in sorted(selected)]


def collect_private_turn(
    *, control_state: Any, request_payload: bytes, request_sha256: str,
    model_proxy_initial_receipt: dict[str, Any], model_proxy_final_receipt: dict[str, Any],
    action_journal_roots: Iterable[Path] = (),
) -> dict[str, Any]:
    """Collect one exact succeeded ControlState turn without exporting private content."""
    if not isinstance(request_payload, bytes) or evaluation.sha256(request_payload) != request_sha256:
        raise evaluation.OutcomeError("Assistant runtime request binding is invalid")
    try:
        request_text = request_payload.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise evaluation.OutcomeError("Assistant runtime request is not strict UTF-8") from exc
    control_module = assistant_evidence._load_control(Path(control_state.root))
    try:
        request_text = control_module.bounded_multiline_text(
            request_text, "Assistant admitted request", control_module.MAX_CHAT_MESSAGE_BYTES,
        )
    except Exception as exc:
        raise evaluation.OutcomeError("Assistant runtime request cannot cross the exact portal text boundary") from exc
    try:
        records = control_state._chat_records()
    except Exception as exc:
        raise evaluation.OutcomeError("Assistant private conversation custody is unavailable") from exc
    matches = [
        (record, turn) for record in records for turn in record["value"]["turns"]
        if turn.get("userText") == request_text and turn.get("state") == "succeeded"
    ]
    if len(matches) != 1:
        raise evaluation.OutcomeError("Assistant runtime request does not select one succeeded private turn")
    record, turn = matches[0]
    conversation = record["value"]
    bundle_path = control_state.chat_tool_receipts / f"{turn['turnId']}.json"
    if bundle_path.exists():
        bundle, _payload = _private_json(
            bundle_path, "Assistant private tool receipt bundle", control_module.MAX_CHAT_TOOL_RECEIPT_BUNDLE_BYTES,
        )
    else:
        bundle = None
    bundle_sha256 = None if bundle is None else control_module.digest(bundle)
    required_heads = _required_journal_heads(bundle)
    chains = _select_journal_chains(action_journal_roots, required_heads)
    return {
        "schemaVersion": 1, "operation": "pixel-portal-assistant-private-evidence",
        "conversation": conversation, "conversationSha256": control_module.digest(conversation),
        "turnId": turn["turnId"], "toolReceiptBundle": bundle,
        "toolReceiptBundleSha256": bundle_sha256, "actionJournalChains": chains,
        "modelProxyInitialReceipt": model_proxy_initial_receipt,
        "modelProxyFinalReceipt": model_proxy_final_receipt,
        "handoffReceipt": None, "independentVerification": None,
        "privacy": {
            "conversationTextExported": False, "toolNamesExported": False, "argumentsExported": False,
            "resultsExported": False, "pathsExported": False, "credentialsExported": False,
            "providerIdentifiersExported": False,
        },
        "boundary": assistant_evidence.ASSISTANT_EVIDENCE_BOUNDARY,
    }
