#!/usr/bin/env python3
"""Fail-closed reconciliation of declared and observed tool-call accounting."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any


MAX_DECLARED_BYTES = 4 * 1024 * 1024
MAX_TRANSCRIPT_BYTES = 64 * 1024 * 1024
MAX_TRANSCRIPT_LINE_BYTES = 256 * 1024
MAX_TRANSCRIPT_LINES = 100_000
MAX_NAME_BYTES = 4 * 1024
NAME_FIELDS = ("tool", "name", "toolName")
BOUNDARY = (
    "This receipt contains only bounded counts and digests. It carries no tool "
    "arguments, results, external-effect authority, deployment authority, or acceptance authority."
)


class ReconcileError(ValueError):
    """An input or custody condition failed closed."""


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _reject_constant(value: str) -> None:
    raise ReconcileError(f"non-finite JSON value is forbidden: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReconcileError("duplicate JSON object key is forbidden")
        result[key] = value
    return result


def parse_json(payload: bytes, label: str) -> Any:
    try:
        text = payload.decode("utf-8", errors="strict")
        return json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReconcileError(f"{label} must be strict UTF-8 JSON") from error


def read_bounded(path: Path, *, limit: int, label: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ReconcileError(f"{label} is unavailable") from error
    try:
        observed = os.fstat(descriptor)
        if not stat.S_ISREG(observed.st_mode) or observed.st_size > limit:
            raise ReconcileError(f"{label} violates its file boundary")
        chunks: list[bytes] = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > limit:
            raise ReconcileError(f"{label} exceeds its byte ceiling")
        return payload
    finally:
        os.close(descriptor)


def _count(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ReconcileError(f"{label} must be a nonnegative integer")
    return value


def _name(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > MAX_NAME_BYTES:
        raise ReconcileError(f"{label} must be a bounded nonempty string")
    return value


def normalize_accounting(value: Any) -> tuple[str, int, dict[str, int] | None]:
    if type(value) is int:
        return "scalar", _count(value, "declared count"), None

    if isinstance(value, dict):
        if set(value) == {"count"}:
            return "count", _count(value["count"], "declared count"), None
        if set(value) != {"calls"}:
            raise ReconcileError("declared object has an unadmitted or ambiguous shape")
        calls = value["calls"]
        if type(calls) is int:
            return "calls", _count(calls, "declared calls"), None
        if not isinstance(calls, dict):
            raise ReconcileError("declared calls must be an integer or name-to-count object")
        counts: dict[str, int] = {}
        for name, count in calls.items():
            normalized = _name(name, "declared tool name")
            counts[normalized] = _count(count, "declared named count")
        return "calls-map", sum(counts.values()), counts

    if isinstance(value, list):
        counts: dict[str, int] = {}
        for index, record in enumerate(value):
            if not isinstance(record, dict):
                raise ReconcileError(f"declared record {index} must be an object")
            present = [field for field in NAME_FIELDS if field in record]
            if len(present) != 1 or set(record) not in ({present[0]}, {present[0], "count"}):
                raise ReconcileError(f"declared record {index} has an unadmitted shape")
            name = _name(record[present[0]], f"declared record {index} tool name")
            count = _count(record.get("count", 1), f"declared record {index} count")
            counts[name] = counts.get(name, 0) + count
        return "records", sum(counts.values()), counts

    raise ReconcileError("declared accounting has an unadmitted shape")


def normalize_checkpoint(value: Any) -> tuple[str, int, dict[str, int] | None]:
    if not isinstance(value, dict) or "toolCalls" not in value:
        raise ReconcileError("checkpoint must contain toolCalls")
    tool_calls = value["toolCalls"]
    if type(tool_calls) is int:
        return "checkpoint-scalar", _count(tool_calls, "checkpoint toolCalls"), None

    if isinstance(tool_calls, list):
        counts: dict[str, int] = {}
        for index, record in enumerate(tool_calls):
            if not isinstance(record, dict) or "tool" not in record:
                raise ReconcileError(f"checkpoint toolCalls record {index} has an unadmitted shape")
            if any(field in record for field in ("name", "toolName")):
                raise ReconcileError(f"checkpoint toolCalls record {index} has ambiguous name fields")
            name = _name(record["tool"], f"checkpoint toolCalls record {index} name")
            count = _count(record.get("count", 1), f"checkpoint toolCalls record {index} count")
            counts[name] = counts.get(name, 0) + count
        return "checkpoint-records", sum(counts.values()), counts

    if not isinstance(tool_calls, dict):
        raise ReconcileError("checkpoint toolCalls has an unadmitted shape")
    if "counts" in tool_calls:
        source = tool_calls["counts"]
        shape = "checkpoint-counts"
    else:
        source = tool_calls
        shape = "checkpoint-map"
    if not isinstance(source, dict):
        raise ReconcileError("checkpoint toolCalls count map must be an object")

    counts = {}
    declared_total = source.get("total")
    for name, value in source.items():
        if name == "total":
            continue
        normalized_name = _name(name, "checkpoint tool name")
        if type(value) is int:
            count = _count(value, "checkpoint tool count")
        elif isinstance(value, dict):
            admitted = [field for field in ("calls", "count") if type(value.get(field)) is int]
            if len(admitted) != 1:
                raise ReconcileError("checkpoint nested tool count is missing or ambiguous")
            count = _count(value[admitted[0]], "checkpoint nested tool count")
        else:
            raise ReconcileError("checkpoint tool count has an unadmitted shape")
        counts[normalized_name] = count
    total = sum(counts.values())
    if declared_total is not None and _count(declared_total, "checkpoint total") != total:
        raise ReconcileError("checkpoint total disagrees with its named counts")
    return shape, total, counts


def count_transcript(
    payload: bytes,
    event_type: str,
    transcript_format: str = "events",
) -> tuple[int, dict[str, int]]:
    _name(event_type, "event type")
    if transcript_format not in {"events", "openclaw"}:
        raise ReconcileError("transcript format is unsupported")
    if transcript_format == "openclaw" and event_type != "tool-call":
        raise ReconcileError("openclaw transcript format uses its fixed assistant toolCall selector")
    lines = payload.splitlines()
    if len(lines) > MAX_TRANSCRIPT_LINES:
        raise ReconcileError("transcript exceeds its line ceiling")
    counts: dict[str, int] = {}
    for index, line in enumerate(lines, 1):
        if not line.strip():
            raise ReconcileError(f"transcript line {index} is blank")
        if len(line) > MAX_TRANSCRIPT_LINE_BYTES:
            raise ReconcileError(f"transcript line {index} exceeds its byte ceiling")
        event = parse_json(line, f"transcript line {index}")
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            raise ReconcileError(f"transcript line {index} must be an object with a string type")
        if transcript_format == "events":
            if event["type"] != event_type:
                continue
            present = [field for field in NAME_FIELDS if field in event]
            if len(present) != 1:
                raise ReconcileError(f"selected transcript line {index} must contain exactly one tool-name field")
            name = _name(event[present[0]], f"selected transcript line {index} tool name")
            counts[name] = counts.get(name, 0) + 1
            continue

        if event["type"] != "message":
            continue
        message = event.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("role"), str):
            raise ReconcileError(f"openclaw message line {index} has invalid message custody")
        if message["role"] != "assistant":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            raise ReconcileError(f"openclaw assistant line {index} has invalid content custody")
        for item_index, item in enumerate(content):
            if not isinstance(item, dict) or not isinstance(item.get("type"), str):
                raise ReconcileError(f"openclaw assistant line {index} content {item_index} is invalid")
            if item["type"] != "toolCall":
                continue
            name = _name(item.get("name"), f"openclaw assistant line {index} tool name")
            counts[name] = counts.get(name, 0) + 1
    return sum(counts.values()), counts


def _digest(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def reconcile(
    declared_payload: bytes,
    transcript_payload: bytes,
    event_type: str = "tool-call",
    declared_format: str = "accounting",
    transcript_format: str = "events",
) -> tuple[int, bytes]:
    declared_value = parse_json(declared_payload, "declared input")
    if declared_format == "accounting":
        shape, declared_total, declared_names = normalize_accounting(declared_value)
    elif declared_format == "checkpoint":
        shape, declared_total, declared_names = normalize_checkpoint(declared_value)
    else:
        raise ReconcileError("declared format is unsupported")
    transcript_total, transcript_names = count_transcript(transcript_payload, event_type, transcript_format)

    mismatches: set[str] = set()
    if declared_total != transcript_total:
        mismatches.add("total-mismatch")
    if declared_names is not None:
        if declared_names != transcript_names:
            mismatches.add("name-distribution-mismatch")
        if set(declared_names) - set(transcript_names):
            mismatches.add("declared-only-name")
        if set(transcript_names) - set(declared_names):
            mismatches.add("transcript-only-name")

    names = set(transcript_names)
    if declared_names is not None:
        names.update(declared_names)
    per_name = [
        {
            "declaredCount": None if declared_names is None else declared_names.get(name),
            "toolNameSha256": _digest(name),
            "transcriptCount": transcript_names.get(name, 0),
        }
        for name in names
    ]
    per_name.sort(key=lambda item: item["toolNameSha256"])
    accepted = not mismatches
    receipt = {
        "boundary": BOUNDARY,
        "decision": "accept" if accepted else "reject",
        "declaredInputSha256": _digest(declared_payload),
        "declaredFormat": declared_format,
        "declaredShape": shape,
        "declaredTotal": declared_total,
        "eventTypeSha256": _digest(event_type),
        "mismatchCodes": sorted(mismatches),
        "operation": "pixel-tool-accounting-reconcile-v1",
        "perName": per_name,
        "schemaVersion": 1,
        "transcriptInputSha256": _digest(transcript_payload),
        "transcriptFormat": transcript_format,
        "transcriptTotal": transcript_total,
    }
    return (0 if accepted else 1), canonical_json(receipt)


def write_create_once(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    created = False
    try:
        descriptor = os.open(path, flags, 0o600)
        created = True
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = None
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as error:
        if descriptor is not None:
            os.close(descriptor)
        try:
            if created and path.exists():
                path.unlink()
        except OSError:
            pass
        raise ReconcileError("receipt output could not be created once") from error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--declared", required=True, type=Path)
    parser.add_argument("--declared-format", choices=("accounting", "checkpoint"), default="accounting")
    parser.add_argument("--transcript", required=True, type=Path)
    parser.add_argument("--event-type", default="tool-call")
    parser.add_argument("--transcript-format", choices=("events", "openclaw"), default="events")
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        declared = read_bounded(arguments.declared, limit=MAX_DECLARED_BYTES, label="declared input")
        transcript = read_bounded(arguments.transcript, limit=MAX_TRANSCRIPT_BYTES, label="transcript input")
        result, receipt = reconcile(
            declared,
            transcript,
            arguments.event_type,
            arguments.declared_format,
            arguments.transcript_format,
        )
        write_create_once(arguments.output, receipt)
        return result
    except ReconcileError as error:
        sys.stderr.write(f"tool accounting reconciliation failed closed: {error}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
