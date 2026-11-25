"""One synthetic Portal task, with retained result and exact-request recovery.

API receipt latency is not browser or engine latency. Tool requests do not prove
execution. Chat idle does not prove owned process cleanup. DNS resolution is not
bounded by the socket watchdog. No automatic submission retries are performed.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import socket
import ssl
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import probe

MAX_BYTES = 8 * 1024 * 1024
MAX_LINE = 1024 * 1024
SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
STATES = {"active", "complete", "interrupted", "cancelled", "rejected", "unresolved", "unknown"}
TERMINAL = {"complete", "cancelled", "rejected"}


def strict_json(text):
    return json.loads(text, parse_constant=probe._reject_constant,
                      object_pairs_hook=probe._unique_object)


def validate_manifest(value):
    required = {"schema_version", "base_url", "api_key_env", "timeout_s", "recovery_timeout_s", "task"}
    if not isinstance(value, dict) or set(value) != required or type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("invalid manifest fields")
    # Also rejects non-finite expected values passed through the Python API.
    json.dumps(value, allow_nan=False)
    probe._validate_endpoint(value["base_url"].rstrip("/") + "/chat/completions")
    parsed = urlsplit(value["base_url"])
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("base_url must be an origin")
    if not isinstance(value["api_key_env"], str) or not value["api_key_env"]:
        raise ValueError("missing credential selector")
    for name, limit in (("timeout_s", 600), ("recovery_timeout_s", 60)):
        number = value[name]
        if type(number) not in (int, float) or not 1 <= number <= limit:
            raise ValueError("invalid deadline")
    task = value["task"]
    if not isinstance(task, dict) or not {"id", "messages", "validator"} <= set(task) or set(task) - {"id", "messages", "validator", "required_tools"}:
        raise ValueError("invalid task fields")
    if not isinstance(task["id"], str) or not SAFE_ID.fullmatch(task["id"]):
        raise ValueError("invalid task id")
    probe._validate_messages(task["messages"])
    if len(task["messages"]) > 50 or any(len(m["content"]) > 16384 for m in task["messages"]) or sum(len(m["content"].encode()) for m in task["messages"]) > 256 * 1024:
        raise ValueError("messages too large")
    probe._validate_validator(task["validator"])
    names = task.get("required_tools", [])
    if not isinstance(names, list) or any(not isinstance(n, str) or not SAFE_ID.fullmatch(n) for n in names):
        raise ValueError("invalid required tools")
    return value


def _post(base_url, key, endpoint, payload, deadline, *, stream=False):
    """One direct HTTP request; absolute deadline also covers trickle headers/body."""
    result = {"status": None, "error": None, "body": b""}
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return {**result, "error": "timeout"}
    parts = urlsplit(base_url)
    args = (parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
    conn = (http.client.HTTPSConnection(*args, timeout=remaining, context=ssl.create_default_context())
            if parts.scheme == "https" else http.client.HTTPConnection(*args, timeout=remaining))
    stop = threading.Event()
    sockets = []

    def expire():
        if not stop.wait(max(0, deadline - time.monotonic())):
            for sock in sockets:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    watchdog = threading.Thread(target=expire, daemon=True)
    watchdog.start()
    try:
        conn.connect()
        sockets.append(conn.sock)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            result["error"] = "timeout"
            return result
        conn.sock.settimeout(remaining)
        conn.request("POST", "/api/pixel/chat/" + endpoint,
                     body=json.dumps(payload).encode(),
                     headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                              "Accept": "text/event-stream" if stream else "application/json"})
        response = conn.getresponse()
        if time.monotonic() >= deadline:
            result["error"] = "timeout"
            return result
        result["status"] = response.status
        if response.status != 200:
            result["error"] = "http_rejected"
            return result
        mime = (response.getheader("Content-Type") or "").split(";", 1)[0].strip().lower()
        if mime != ("text/event-stream" if stream else "application/json"):
            result["error"] = "bad_content_type"
            return result
        chunks, size = [], 0
        while time.monotonic() < deadline:
            chunk = response.read1(65536)
            if time.monotonic() >= deadline:
                result["error"] = "timeout"
                return result
            if not chunk:
                result["body"] = b"".join(chunks)
                return result
            size += len(chunk)
            if size > MAX_BYTES:
                result["error"] = "body_too_large"
                return result
            chunks.append(chunk)
        result["error"] = "timeout"
    except (Exception, KeyboardInterrupt):
        result["error"] = "timeout" if time.monotonic() >= deadline else "transport_error"
    finally:
        stop.set()
        conn.close()
        watchdog.join(timeout=0.2)
    return result


def parse_events(raw):
    """Parse canonical retained SSE; never return server text in public errors."""
    out = {"content": "", "done": False, "requested_tools": [], "error": None}
    try:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        if not isinstance(text, str) or len(text.encode()) > MAX_BYTES:
            raise ValueError()
        data = []
        for line in text.replace("\r\n", "\n").split("\n") + [""]:
            if len(line.encode()) > MAX_LINE:
                raise ValueError()
            if line:
                if line.startswith("data:"):
                    data.append(line[5:].lstrip(" "))
                continue
            if not data:
                continue
            payload, data = "\n".join(data), []
            if out["done"]:
                raise ValueError()  # No answer or error frames may hide after DONE.
            if payload == "[DONE]":
                out["done"] = True
                continue
            event = strict_json(payload)
            if not isinstance(event, dict) or "error" in event:
                raise ValueError()
            choices = event.get("choices", [])
            if not isinstance(choices, list) or len(choices) > 1:
                raise ValueError()
            for choice in choices:
                if not isinstance(choice, dict) or type(choice.get("index", 0)) is not int or choice.get("index", 0) != 0:
                    raise ValueError()
                if "delta" in choice and "message" in choice:
                    raise ValueError()
                source = choice.get("delta", choice.get("message", {}))
                if not isinstance(source, dict):
                    raise ValueError()
                content = source.get("content")
                if content is not None and not isinstance(content, str):
                    raise ValueError()
                out["content"] += content or ""
                for call in source.get("tool_calls", []):
                    name = call.get("function", {}).get("name")
                    if isinstance(name, str) and SAFE_ID.fullmatch(name):
                        out["requested_tools"].append(name)
        if not out["done"] or not out["content"]:
            raise ValueError()
    except Exception:
        out["error"] = "invalid_sse"
        out["content"] = ""
        out["requested_tools"] = []
    return out


def _read_result(base, key, identity, deadline):
    response = _post(base, key, "result", identity, deadline)
    receipt = {"state": "unknown", "events": "", "error": response["error"]}
    if response["error"]:
        return receipt
    try:
        data = strict_json(response["body"].decode("utf-8"))
        if not isinstance(data, dict) or set(data) != {"state", "events"} or data["state"] not in STATES or not isinstance(data["events"], str):
            raise ValueError()
        return {**data, "error": None}
    except Exception:
        return {**receipt, "error": "invalid_result"}


def run(manifest, *, chat_id=None, request_id=None, native_sessions_dir=None,
        native_allow_continuation=False):
    """Return a redacted receipt. Unknown activity must stop later admission."""
    if type(native_allow_continuation) is not bool:
        raise ValueError("invalid_native_allow_continuation")
    if native_allow_continuation and native_sessions_dir is None:
        raise ValueError("native_allow_continuation_requires_sessions_dir")
    manifest = validate_manifest(manifest)
    identity = {"chat_id": uuid.uuid4().hex if chat_id is None else chat_id,
                "request_id": uuid.uuid4().hex if request_id is None else request_id}
    if any(not isinstance(v, str) or not SAFE_ID.fullmatch(v) for v in identity.values()):
        raise ValueError("invalid request identity")
    task = manifest["task"]
    record = {"schema_version": 1, **identity, "task_id": task["id"],
              "started_utc": datetime.now(timezone.utc).isoformat(),
              "task_sha256": probe.sha256_text(probe.canonical_json(task)),
              "submission_attempted": False, "http_status": None, "stream_error": None,
              "transport_complete": False, "result_state": "unknown", "result_error": None,
              "answer_sha256": None, "answer_quality_pass": False, "quality_pass": False,
              "success": False, "censored": False, "task_latency_ms": None,
              "elapsed_ms": None, "recovery_elapsed_ms": None,
              "required_tools": task.get("required_tools", []), "requested_tools": [],
              "tool_execution_proven": None, "owned_cleanup_proven": None,
              "selected_model": None, "model_calls": None,
              "cancel_attempted": False, "cancel_ack": False, "cancel_error": None,
              "activity": "unknown", "admission_safe": False, "error": None}
    key = os.environ.get(manifest["api_key_env"])
    if not key:
        return {**record, "error": "missing_credential"}
    witness = None
    worker_start = time.monotonic()
    if native_sessions_dir is not None:
        from native_receipt_join import NativeReceiptWitness
        try:
            witness = NativeReceiptWitness(
                native_sessions_dir, manifest, **identity,
                allow_existing_session=native_allow_continuation)
        except Exception:
            return {**record, "error": "native_witness_preflight_failed"}
        record["native_preflight_ms"] = round((time.monotonic() - worker_start) * 1000, 3)
    base, start = manifest["base_url"], time.monotonic()
    deadline = start + manifest["timeout_s"]
    record["submission_attempted"] = True
    submitted = _post(base, key, "stream", {**identity, "messages": task["messages"]}, deadline, stream=True)
    record["http_status"] = submitted["status"]
    stream = parse_events(submitted["body"])
    record["stream_error"] = submitted["error"] or stream["error"]
    record["transport_complete"] = submitted["error"] is None and stream["error"] is None
    receipt = _read_result(base, key, identity, deadline)
    while receipt["state"] == "active" and not record["stream_error"] and time.monotonic() < deadline:
        time.sleep(min(0.2, max(0, deadline - time.monotonic())))
        receipt = _read_result(base, key, identity, deadline)
    observed = time.monotonic()
    timely_complete = receipt["state"] == "complete" and observed < deadline
    if timely_complete:
        record["task_latency_ms"] = round((observed - start) * 1000, 3)
    record["censored"] = not timely_complete
    recovery_start = time.monotonic()
    recovery_deadline = recovery_start + manifest["recovery_timeout_s"]
    if receipt["state"] not in TERMINAL:
        record["cancel_attempted"] = True
        cancelled = _post(base, key, "cancel", identity, recovery_deadline)
        record["cancel_error"] = cancelled["error"]
        if not cancelled["error"]:
            try:
                data = strict_json(cancelled["body"].decode())
                record["cancel_ack"] = isinstance(data, dict) and set(data) == {"aborted"} and data["aborted"] is True
            except Exception:
                record["cancel_error"] = "invalid_cancel"
        receipt = _read_result(base, key, identity, recovery_deadline)
    activity = _post(base, key, "activity", identity, recovery_deadline)
    if not activity["error"]:
        try:
            data = strict_json(activity["body"].decode())
            if isinstance(data, dict) and set(data) == {"state"} and data["state"] in {"active", "terminal", "unknown"}:
                record["activity"] = data["state"]
        except Exception:
            pass
    record["result_state"], record["result_error"] = receipt["state"], receipt["error"]
    if receipt["state"] == "complete":
        final = parse_events(receipt["events"])
        record["result_error"] = final["error"]
        # Only publish task-declared names; arbitrary server strings stay private.
        record["requested_tools"] = sorted(set(final["requested_tools"]) & set(record["required_tools"]))
        record["answer_sha256"] = probe.sha256_text(final["content"])
        record["answer_quality_pass"] = final["error"] is None and probe.validate_answer(task["validator"], final["content"])[0]
        record["quality_pass"] = record["answer_quality_pass"] and not record["required_tools"]
    record["admission_safe"] = record["activity"] == "terminal"
    record["success"] = (record["quality_pass"] and timely_complete
                         and submitted["status"] == 200 and record["admission_safe"])
    record["recovery_elapsed_ms"] = round((time.monotonic() - recovery_start) * 1000, 3)
    record["elapsed_ms"] = round((time.monotonic() - start) * 1000, 3)
    if witness is not None:
        observation_start = time.monotonic()
        native = witness.finish(record)
        record["native_observation_ms"] = round((time.monotonic() - observation_start) * 1000, 3)
        record["native_receipt"] = native
        record["quality_pass"] = native["quality_pass"] if native["binding_proven"] else False
        record["success"] = native["success"] if native["binding_proven"] else False
        if native["binding_proven"]:
            record["tool_execution_proven"] = native["tool_execution_proven"]
            if native["model_calls_observed"] == native["model_calls_completed"]:
                record["model_calls"] = native["model_calls_completed"]
    record["worker_elapsed_ms"] = round((time.monotonic() - worker_start) * 1000, 3)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--native-sessions-dir", help="Read-only native receipt witness on the serving host")
    args = parser.parse_args()
    output = Path(args.output)
    identity = {"chat_id": uuid.uuid4().hex, "request_id": uuid.uuid4().hex}
    pending = {"schema_version": 1, **identity, "success": False,
               "submission_attempted": None, "admission_safe": False,
               "error": "observer_outcome_unknown"}
    # Persist recovery identity before any HTTP submission. Never overwrite a
    # prior run. A killed observer leaves this conservative pending receipt.
    with output.open("x", encoding="utf-8") as stream:
        json.dump(pending, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        record = run(strict_json(Path(args.manifest).read_text(encoding="utf-8")),
                     native_sessions_dir=args.native_sessions_dir, **identity)
    except Exception:
        record = {**pending, "runner_error": "invalid_manifest_or_runner_failure"}
    temporary = output.with_name(output.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, output)
    return 0 if record["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
