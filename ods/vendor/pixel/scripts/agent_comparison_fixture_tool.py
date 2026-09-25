#!/usr/bin/env python3
"""Deterministic typed-tool fixture for non-promotional agent rehearsals.

The comparison materializer copies this file and an exact fixture into a protected
workspace prefix.  Calls are stateful and append content-free receipts.  This is a
test double: it exercises planning, typed calls, fault handling, privacy routing,
and reconciliation, but it is never evidence that a live provider or product
integration worked.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any


MAX_INPUT_BYTES = 64 * 1024
MAX_STATE_BYTES = 2 * 1024 * 1024


class FixtureError(Exception):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def read_json(path: Path, label: str) -> Any:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise FixtureError(f"{label} is unavailable") from exc
    if not raw or len(raw) > MAX_STATE_BYTES:
        raise FixtureError(f"{label} is empty or oversized")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise FixtureError(f"{label} is invalid JSON") from exc


def write_atomic(path: Path, value: Any) -> None:
    payload = canonical(value) + b"\n"
    if len(payload) > MAX_STATE_BYTES:
        raise FixtureError("fixture state is oversized")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def append_receipt(path: Path, value: Any) -> None:
    payload = canonical(value) + b"\n"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, "ab") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def exact_fixture(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"schemaVersion", "operation", "actions", "boundary"}:
        raise FixtureError("fixture contract fields are invalid")
    if value["schemaVersion"] != 1 or value["operation"] != "pixel-agent-comparison-fixture":
        raise FixtureError("fixture contract identity is invalid")
    if value["boundary"] != "Deterministic non-promotional comparison rehearsal only; no live provider, credential, network, external effect, product proof, acceptance, or promotion authority.":
        raise FixtureError("fixture boundary is invalid")
    actions = value["actions"]
    if not isinstance(actions, dict) or not actions:
        raise FixtureError("fixture actions are unavailable")
    for name, action in actions.items():
        if not isinstance(name, str) or not name or not isinstance(action, dict):
            raise FixtureError("fixture action is invalid")
        allowed = {"response", "responses", "exitCode", "stderr", "committed", "once", "rejectInputsContaining"}
        if set(action) - allowed or ("response" in action) == ("responses" in action):
            raise FixtureError(f"fixture action {name} fields are invalid")
        if "responses" in action and (not isinstance(action["responses"], list) or not action["responses"]):
            raise FixtureError(f"fixture action {name} response sequence is invalid")
    return value


def selected_response(action: dict[str, Any], call_index: int) -> dict[str, Any]:
    selected: Any
    if "responses" in action:
        sequence = action["responses"]
        selected = sequence[min(call_index, len(sequence) - 1)]
        if not isinstance(selected, dict) or set(selected) - {"response", "exitCode", "stderr", "committed"} or "response" not in selected:
            raise FixtureError("fixture sequenced response is invalid")
    else:
        selected = {
            "response": action["response"],
            "exitCode": action.get("exitCode", 0),
            "stderr": action.get("stderr", ""),
            "committed": action.get("committed", False),
        }
    exit_code = selected.get("exitCode", action.get("exitCode", 0))
    stderr = selected.get("stderr", action.get("stderr", ""))
    committed = selected.get("committed", action.get("committed", False))
    if type(exit_code) is not int or not 0 <= exit_code <= 125 or not isinstance(stderr, str) or type(committed) is not bool:
        raise FixtureError("fixture response controls are invalid")
    return {"response": selected["response"], "exitCode": exit_code, "stderr": stderr, "committed": committed}


def main(argv: list[str]) -> int:
    if len(argv) not in {2, 3}:
        raise FixtureError("usage: tool.py ACTION [JSON_INPUT]")
    action_name = argv[1]
    encoded = argv[2].encode("utf-8") if len(argv) == 3 else b"{}"
    if not encoded or len(encoded) > MAX_INPUT_BYTES:
        raise FixtureError("tool input is empty or oversized")
    try:
        tool_input = json.loads(encoded.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise FixtureError("tool input is invalid JSON") from exc
    if not isinstance(tool_input, dict):
        raise FixtureError("tool input must be a JSON object")

    fixture_root = Path(__file__).resolve().parent
    workspace = fixture_root.parent
    state_root = workspace / ".pixel-state"
    fixture = exact_fixture(read_json(fixture_root / "fixture.json", "fixture"))
    if action_name not in fixture["actions"]:
        raise FixtureError("tool action is not admitted")
    action = fixture["actions"][action_name]
    state_path = state_root / "state.json"
    state = read_json(state_path, "fixture state") if state_path.exists() else {"schemaVersion": 1, "calls": {}, "sequence": 0}
    if not isinstance(state, dict) or set(state) != {"schemaVersion", "calls", "sequence"} or state["schemaVersion"] != 1:
        raise FixtureError("fixture state identity is invalid")
    calls = state["calls"].get(action_name, 0)
    if type(calls) is not int or calls < 0 or type(state["sequence"]) is not int or state["sequence"] < 0:
        raise FixtureError("fixture state counters are invalid")

    rejected = False
    raw_input = canonical(tool_input).decode("utf-8")
    for forbidden in action.get("rejectInputsContaining", []):
        if not isinstance(forbidden, str) or not forbidden:
            raise FixtureError("fixture rejection rule is invalid")
        if forbidden in raw_input:
            rejected = True
    duplicate = action.get("once", False) is True and calls > 0
    result = selected_response(action, calls)
    if rejected:
        result = {"response": {"error": "sensitive-input-rejected"}, "exitCode": 77, "stderr": "sensitive input rejected", "committed": False}
    elif duplicate:
        result = {"response": {"error": "duplicate-call-rejected"}, "exitCode": 78, "stderr": "duplicate call rejected", "committed": False}

    state["calls"][action_name] = calls + 1
    state["sequence"] += 1
    write_atomic(state_path, state)
    response_payload = canonical(result["response"])
    receipt = {
        "schemaVersion": 1,
        "sequence": state["sequence"],
        "action": action_name,
        "callIndex": calls,
        "inputSha256": sha(canonical(tool_input)),
        "responseSha256": sha(response_payload),
        "status": "rejected" if rejected else "duplicate" if duplicate else "ok" if result["exitCode"] == 0 else "unknown-outcome",
        "committed": result["committed"],
        "exitCode": result["exitCode"],
        "liveProvider": False,
        "externalEffects": False,
    }
    append_receipt(state_root / "journal.jsonl", receipt)
    sys.stdout.buffer.write(response_payload + b"\n")
    if result["stderr"]:
        print(result["stderr"], file=sys.stderr)
    return result["exitCode"]


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except FixtureError as exc:
        print(f"pixel-fixture-tool: {exc}", file=sys.stderr)
        raise SystemExit(2)
