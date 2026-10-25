"""Outgoing-history tool-call ID compatibility for strict-9 templates.

Pure, no I/O. Applied only to the request body the router forwards; never to
the response, the audit transcript, tool names, arguments, or schemas.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

# Conservative family detection for the confirmed Mistral-Nemo token.
# We accept a bounded token "Mistral-Nemo" (case-insensitive) that is not
# immediately preceded or followed by additional alphanumeric characters,
# so that e.g. "FakeMistral-Nemo" or "Mistral-Nemotron" are rejected.
_NEMO_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])Mistral-Nemo(?![A-Za-z0-9])", re.IGNORECASE)
_STRICT9_RE = re.compile(r"^[A-Za-z0-9]{9}$")
_MAX_ATTEMPTS = 4096
_MAX_IDS = 4096


def route_requires_strict9_tool_ids(runtime_model_id: str, backend_kind: str) -> bool:
    """True only for the confirmed Mistral-Nemo runtime on llama-server."""
    if backend_kind != "llama-server":
        return False
    if not isinstance(runtime_model_id, str):
        return False
    if not runtime_model_id:
        return False
    return _NEMO_TOKEN_RE.search(runtime_model_id) is not None


def _digest9(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:9]


def _is_valid9(value: Any) -> bool:
    return isinstance(value, str) and _STRICT9_RE.fullmatch(value) is not None


def _scan(messages: list[Any]) -> tuple[bool, list[str], list[str]]:
    """Single ordered pass over history.

    Returns (ok, assistant_ids_in_order, tool_ids_in_order).
    ok is False if any malformed entry is found or the ID cap is exceeded.
    """
    assistant_ids: list[str] = []
    tool_ids: list[str] = []
    total = 0
    for msg in messages:
        if not isinstance(msg, dict):
            return False, [], []
        role = msg.get("role")
        if role == "assistant":
            calls = msg.get("tool_calls")
            if calls is None:
                continue
            if not isinstance(calls, list):
                return False, [], []
            for call in calls:
                if not isinstance(call, dict):
                    return False, [], []
                cid = call.get("id")
                if not isinstance(cid, str) or not cid:
                    return False, [], []
                # Reject invalid unicode surrogates that cannot be UTF-8 encoded.
                try:
                    cid.encode("utf-8")
                except UnicodeEncodeError:
                    return False, [], []
                assistant_ids.append(cid)
                total += 1
                if total > _MAX_IDS:
                    return False, [], []
        elif role == "tool":
            tid = msg.get("tool_call_id")
            if not isinstance(tid, str) or not tid:
                return False, [], []
            try:
                tid.encode("utf-8")
            except UnicodeEncodeError:
                return False, [], []
            tool_ids.append(tid)
            total += 1
            if total > _MAX_IDS:
                return False, [], []
    return True, assistant_ids, tool_ids


def _validate_ordered(messages: list[Any]) -> bool:
    """Single ordered pass validating call/result chronology and uniqueness."""
    seen_calls: set[str] = set()
    seen_results: set[str] = set()
    for msg in messages:
        if not isinstance(msg, dict):
            return False
        role = msg.get("role")
        if role == "assistant":
            calls = msg.get("tool_calls")
            if calls is None:
                continue
            if not isinstance(calls, list):
                return False
            for call in calls:
                if not isinstance(call, dict):
                    return False
                cid = call.get("id")
                if not isinstance(cid, str) or not cid:
                    return False
                if cid in seen_calls:
                    return False
                seen_calls.add(cid)
        elif role == "tool":
            tid = msg.get("tool_call_id")
            if not isinstance(tid, str) or not tid:
                return False
            if tid in seen_results:
                return False
            if tid not in seen_calls:
                return False
            seen_results.add(tid)
    return True


def _allocate(original_id: str, reserved: set[str]) -> str | None:
    for attempt in range(_MAX_ATTEMPTS):
        candidate = _digest9(f"{original_id}\x00{attempt}")
        if candidate not in reserved:
            reserved.add(candidate)
            return candidate
    return None


def normalize_history_tool_ids(messages: Any) -> tuple[Any, bool]:
    """Return (possibly rewritten messages, changed). Never mutates input.

    Leaves the entire request unchanged for any ambiguous history: malformed
    call/result IDs, duplicate assistant IDs, duplicate results, orphan
    results, results before their call, or allocation exhaustion.
    """
    if not isinstance(messages, list) or not messages:
        return messages, False

    ok, assistant_ids, tool_ids = _scan(messages)
    if not ok:
        return messages, False
    if not assistant_ids and not tool_ids:
        return messages, False

    if not _validate_ordered(messages):
        return messages, False

    # Reserve every existing valid9 ID so allocation cannot steal one.
    reserved: set[str] = set()
    for cid in assistant_ids:
        if _is_valid9(cid):
            reserved.add(cid)
    for tid in tool_ids:
        if _is_valid9(tid):
            reserved.add(tid)

    # Build mapping for distinct invalid IDs.
    mapping: dict[str, str] = {}
    for cid in assistant_ids:
        if _is_valid9(cid):
            continue
        if cid in mapping:
            continue
        wire = _allocate(cid, reserved)
        if wire is None:
            return messages, False
        mapping[cid] = wire

    if not mapping:
        return messages, False

    out: list[Any] = []
    changed = False
    for msg in messages:
        if not isinstance(msg, dict):
            out.append(msg)
            continue
        role = msg.get("role")
        if role == "assistant":
            calls = msg.get("tool_calls")
            if not isinstance(calls, list):
                out.append(msg)
                continue
            new_calls = []
            msg_changed = False
            for call in calls:
                if isinstance(call, dict) and isinstance(call.get("id"), str) \
                        and call["id"] in mapping:
                    new_calls.append({**call, "id": mapping[call["id"]]})
                    msg_changed = True
                else:
                    new_calls.append(call)
            if msg_changed:
                changed = True
                out.append({**msg, "tool_calls": new_calls})
            else:
                out.append(msg)
        elif role == "tool":
            tid = msg.get("tool_call_id")
            if isinstance(tid, str) and tid in mapping:
                changed = True
                out.append({**msg, "tool_call_id": mapping[tid]})
            else:
                out.append(msg)
        else:
            out.append(msg)

    if not changed:
        return messages, False
    return out, True
