"""ODS engine/gateway chat transport performance pilot runner.

Scope: measures engine/gateway chat transport only. Never full Pixel agent
or UI certification. Stdlib only. No shell execution, downloads, retries,
or production configuration changes.

CLI: probe.py --manifest manifest.json --out NEW_DIRECTORY
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import random
import socket
import ssl
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlsplit


with open(__file__, "rb") as _source:
    RUNNER_SHA256 = hashlib.sha256(_source.read()).hexdigest()

SCHEMA_VERSION = 1
MAX_TOTAL_REQUESTS = 1000
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_LINE_BYTES = 1024 * 1024
ALLOWED_SAMPLING_KEYS = {"temperature", "top_p", "max_tokens", "seed", "chat_template_kwargs"}
RESERVED_SAMPLING_KEYS = {"model", "messages", "stream", "stream_options", "tools"}
ALLOWED_ROLES = {"system", "user", "assistant"}
ALLOWED_VALIDATOR_KINDS = {"exact", "json_equal"}
ALLOWED_LAYERS = {"engine", "gateway"}
PROVENANCE_KEYS = [
    "engine", "engine_version", "backend", "model_revision",
    "quantization", "tokenizer_revision", "ods_commit",
]
MANIFEST_TOP_KEYS = {
    "schema_version", "run_label", "target", "layer", "endpoint",
    "api_key_env", "model", "provenance", "cache_condition",
    "cache_evidence", "sampling", "concurrency", "repetitions",
    "timeout_s", "slo_ms", "tasks", "schedule_seed",
}
TASK_KEYS = {"id", "messages", "validator"}


class ManifestError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Canonical JSON / hashing
# ---------------------------------------------------------------------------

def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_hex(text.encode("utf-8"))


# ---------------------------------------------------------------------------
# Manifest validation
# ---------------------------------------------------------------------------

def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise ManifestError(msg)


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and v not in (float("inf"), float("-inf"))


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-finite JSON constant {name} not allowed")


def _validate_endpoint(endpoint: Any) -> None:
    _require(isinstance(endpoint, str) and endpoint, "endpoint must be non-empty string")
    parts = urlsplit(endpoint)
    _require(parts.scheme in ("https", "http"), "endpoint scheme must be https or http")
    _require(parts.username is None and parts.password is None, "endpoint must not contain userinfo")
    _require(parts.query == "", "endpoint must not contain query")
    _require(parts.fragment == "", "endpoint must not contain fragment")
    _require(parts.path.endswith("/chat/completions"), "endpoint path must end with /chat/completions")
    _require(parts.hostname is not None, "endpoint must have host")
    try:
        _require(parts.port is None or 1 <= parts.port <= 65535, "invalid endpoint port")
    except ValueError as exc:
        raise ManifestError("invalid endpoint port") from exc
    if parts.scheme == "http":
        host = parts.hostname or ""
        _require(host in ("127.0.0.1", "localhost", "::1"), "http endpoint only allowed for loopback")


def _validate_messages(messages: Any) -> None:
    _require(isinstance(messages, list) and messages, "messages must be non-empty list")
    for i, m in enumerate(messages):
        _require(isinstance(m, dict), f"messages[{i}] must be object")
        _require(set(m.keys()) == {"role", "content"}, f"messages[{i}] must have only role and content")
        _require(m["role"] in ALLOWED_ROLES, f"messages[{i}].role unsupported")
        _require(isinstance(m["content"], str), f"messages[{i}].content must be string")


def _validate_validator(v: Any) -> None:
    _require(isinstance(v, dict), "validator must be object")
    _require(set(v.keys()) == {"kind", "expected"}, "validator must have exactly kind and expected")
    _require(v["kind"] in ALLOWED_VALIDATOR_KINDS, "validator.kind unsupported")
    if v["kind"] == "exact":
        _require(isinstance(v["expected"], str), "exact validator expected must be string")
    else:
        _require(isinstance(v["expected"], (dict, list, str, int, float, bool)) or v["expected"] is None,
                 "json_equal validator expected must be JSON value")


def _validate_sampling(sampling: Any) -> dict:
    _require(isinstance(sampling, dict), "sampling must be object")
    for k in sampling:
        _require(k not in RESERVED_SAMPLING_KEYS, f"sampling key {k} is reserved")
        _require(k in ALLOWED_SAMPLING_KEYS, f"sampling key {k} unsupported")
    out = dict(sampling)
    if "max_tokens" in out:
        mt = out["max_tokens"]
        _require(isinstance(mt, int) and not isinstance(mt, bool) and 1 <= mt <= 4096,
                 "sampling.max_tokens must be int 1..4096")
    else:
        out["max_tokens"] = 128
    if "chat_template_kwargs" in out:
        _require(isinstance(out["chat_template_kwargs"], dict), "chat_template_kwargs must be object")
    if "temperature" in out:
        t = out["temperature"]
        _require(_is_number(t) and 0 <= t <= 2, "sampling.temperature must be number 0..2")
    if "top_p" in out:
        p = out["top_p"]
        _require(_is_number(p) and 0 <= p <= 1, "sampling.top_p must be number 0..1")
    if "seed" in out:
        s = out["seed"]
        _require(isinstance(s, int) and not isinstance(s, bool), "sampling.seed must be int")
    return out


def validate_manifest(manifest: Any) -> dict:
    """Validate manifest strictly. Returns normalized copy. Raises ManifestError."""
    _require(isinstance(manifest, dict), "manifest must be object")
    unknown = set(manifest.keys()) - MANIFEST_TOP_KEYS
    _require(not unknown, f"unknown manifest fields: {sorted(unknown)}")
    _require(type(manifest.get("schema_version")) is int and manifest["schema_version"] == SCHEMA_VERSION, "schema_version must be 1")
    _require(isinstance(manifest.get("run_label"), str) and manifest["run_label"], "run_label required")
    _require(isinstance(manifest.get("target"), str) and manifest["target"], "target required")
    _require(manifest.get("layer") in ALLOWED_LAYERS, "layer must be engine or gateway")
    _validate_endpoint(manifest.get("endpoint"))
    _require(isinstance(manifest.get("model"), str) and manifest["model"], "model required")

    prov = manifest.get("provenance")
    _require(isinstance(prov, dict), "provenance must be object")
    _require(set(prov.keys()) == set(PROVENANCE_KEYS), "provenance must have exactly required keys")
    for k in PROVENANCE_KEYS:
        v = prov[k]
        _require(v is None or isinstance(v, str), f"provenance.{k} must be string or null")

    cache_condition = manifest.get("cache_condition", "uncontrolled")
    _require(cache_condition == "uncontrolled", "cache_condition must be 'uncontrolled'")
    cache_evidence = manifest.get("cache_evidence")
    if cache_evidence is not None:
        _require(isinstance(cache_evidence, list) and all(isinstance(x, str) for x in cache_evidence),
                 "cache_evidence must be list of strings")

    sampling = _validate_sampling(manifest.get("sampling", {}))

    conc = manifest.get("concurrency")
    _require(isinstance(conc, int) and not isinstance(conc, bool) and 1 <= conc <= 8,
             "concurrency must be int 1..8")
    reps = manifest.get("repetitions")
    _require(isinstance(reps, int) and not isinstance(reps, bool) and 1 <= reps <= 100,
             "repetitions must be int 1..100")
    timeout_s = manifest.get("timeout_s")
    _require(_is_number(timeout_s) and 1 <= timeout_s <= 300, "timeout_s must be number 1..300")
    slo_ms = manifest.get("slo_ms")
    _require(_is_number(slo_ms) and slo_ms > 0, "slo_ms must be number > 0")

    tasks = manifest.get("tasks")
    _require(isinstance(tasks, list) and tasks, "tasks must be non-empty list")
    seen_ids = set()
    norm_tasks = []
    for i, t in enumerate(tasks):
        _require(isinstance(t, dict), f"tasks[{i}] must be object")
        unknown_t = set(t.keys()) - TASK_KEYS
        _require(not unknown_t, f"tasks[{i}] unsupported fields: {sorted(unknown_t)}")
        _require(set(t.keys()) == TASK_KEYS, f"tasks[{i}] must have id, messages, validator")
        tid = t["id"]
        _require(isinstance(tid, str) and tid, f"tasks[{i}].id required")
        _require(tid not in seen_ids, f"duplicate task id {tid}")
        seen_ids.add(tid)
        _validate_messages(t["messages"])
        _validate_validator(t["validator"])
        norm_tasks.append({"id": tid, "messages": t["messages"], "validator": t["validator"]})

    total = len(norm_tasks) * reps
    _require(total <= MAX_TOTAL_REQUESTS, f"total requests {total} exceeds {MAX_TOTAL_REQUESTS}")

    seed = manifest.get("schedule_seed", 17)
    _require(isinstance(seed, int) and not isinstance(seed, bool), "schedule_seed must be int")

    api_key_env = manifest.get("api_key_env")
    if api_key_env is not None:
        _require(isinstance(api_key_env, str) and api_key_env, "api_key_env must be non-empty string")

    try:
        canonical_json(manifest)
    except (ValueError, TypeError) as exc:
        raise ManifestError("manifest must contain finite JSON values") from exc
    return {
        "schema_version": SCHEMA_VERSION,
        "run_label": manifest["run_label"],
        "target": manifest["target"],
        "layer": manifest["layer"],
        "endpoint": manifest["endpoint"],
        "api_key_env": api_key_env,
        "model": manifest["model"],
        "provenance": {k: prov[k] for k in PROVENANCE_KEYS},
        "cache_condition": cache_condition,
        "cache_evidence": cache_evidence,
        "sampling": sampling,
        "concurrency": conc,
        "repetitions": reps,
        "timeout_s": float(timeout_s),
        "slo_ms": float(slo_ms),
        "tasks": norm_tasks,
        "schedule_seed": seed,
    }


# ---------------------------------------------------------------------------
# Answer validation
# ---------------------------------------------------------------------------

def _json_strict_equal(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        if set(a.keys()) != set(b.keys()):
            return False
        return all(_json_strict_equal(a[k], b[k]) for k in a)
    if isinstance(a, list):
        if len(a) != len(b):
            return False
        return all(_json_strict_equal(x, y) for x, y in zip(a, b))
    return a == b


def _unique_object(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate JSON key")
        out[key] = value
    return out


def validate_answer(validator: dict, text: str) -> tuple[bool, Optional[str]]:
    """Return (pass, error_kind)."""
    kind = validator["kind"]
    expected = validator["expected"]
    if kind == "exact":
        if text.strip() == expected.strip():
            return True, None
        return False, "exact_mismatch"
    if kind == "json_equal":
        try:
            got = json.loads(text.strip(), parse_constant=_reject_constant, object_pairs_hook=_unique_object)
        except Exception:
            return False, "json_parse_error"
        if _json_strict_equal(got, expected):
            return True, None
        return False, "json_mismatch"
    return False, "unsupported_validator"


# ---------------------------------------------------------------------------
# SSE parsing
# ---------------------------------------------------------------------------

@dataclass
class StreamResult:
    content: str = ""
    content_chunk_count: int = 0
    content_chunk_gaps_ms: list = field(default_factory=list)
    first_content_ms: Optional[float] = None
    first_reasoning_ms: Optional[float] = None
    last_content_ms: Optional[float] = None
    finish_reason: Optional[str] = None
    response_model: Optional[str] = None
    response_upstream: Optional[str] = None
    usage: Optional[dict] = None
    saw_done: bool = False
    error: Optional[str] = None


def _parse_usage(u: Any) -> Optional[dict]:
    if not isinstance(u, dict):
        return None
    pt = u.get("prompt_tokens")
    ct = u.get("completion_tokens")
    if not (isinstance(pt, int) and not isinstance(pt, bool) and pt >= 0):
        return None
    if not (isinstance(ct, int) and not isinstance(ct, bool) and ct >= 0):
        return None
    out = {"prompt_tokens": pt, "completion_tokens": ct}
    for k in ("prompt_tokens_details", "completion_tokens_details"):
        if k in u and isinstance(u[k], dict):
            allowed = {}
            for nk in ("cached_tokens", "reasoning_tokens"):
                nv = u[k].get(nk)
                if isinstance(nv, int) and not isinstance(nv, bool) and nv >= 0:
                    allowed[nk] = nv
            if allowed:
                out[k] = allowed
    return out


def _process_sse_event(event_lines: list, result: StreamResult, t0: float) -> None:
    data_parts = []
    for line in event_lines:
        if line.startswith("data:"):
            data_parts.append(line[5:].lstrip(" "))
    if not data_parts:
        return
    payload = "\n".join(data_parts)
    if payload.strip() == "[DONE]":
        result.saw_done = True
        return
    try:
        obj = json.loads(payload, parse_constant=_reject_constant, object_pairs_hook=_unique_object)
    except Exception:
        result.error = "sse_malformed_json"
        return
    if not isinstance(obj, dict):
        result.error = "sse_non_object"
        return
    if "error" in obj:
        result.error = "sse_error_event"
        return
    if result.response_model is None and isinstance(obj.get("model"), str):
        result.response_model = obj["model"]
    elif isinstance(obj.get("model"), str) and obj["model"] != result.response_model:
        result.error = "sse_model_changed"
        return
    if "usage" in obj:
        usage = _parse_usage(obj["usage"])
        if usage is not None:
            result.usage = usage  # Some engines report cumulative usage on each chunk.
    choices = obj.get("choices")
    if not isinstance(choices, list):
        return
    seen_idx = set()
    for ch in choices:
        if not isinstance(ch, dict):
            result.error = "sse_bad_choice"
            return
        idx = ch.get("index")
        if type(idx) is not int or idx != 0:
            result.error = "sse_nonzero_choice_index"
            return
        if idx in seen_idx:
            result.error = "sse_duplicate_choice_index"
            return
        seen_idx.add(idx)
        fr = ch.get("finish_reason")
        if isinstance(fr, str) and fr:
            if fr not in ("stop", "length"):
                result.error = "sse_unknown_finish_reason"
                return
            result.finish_reason = fr
        delta = ch.get("delta")
        if not isinstance(delta, dict):
            continue
        rc = delta.get("reasoning_content")
        if isinstance(rc, str) and rc and result.first_reasoning_ms is None:
            result.first_reasoning_ms = (time.perf_counter() - t0) * 1000.0
        c = delta.get("content")
        if isinstance(c, str) and c:
            now = time.perf_counter()
            content_ms = (now - t0) * 1000.0
            if result.first_content_ms is None:
                result.first_content_ms = content_ms
            else:
                result.content_chunk_gaps_ms.append(round(content_ms - result.last_content_ms, 3))
            result.last_content_ms = content_ms
            result.content += c
            result.content_chunk_count += 1


def _read_stream(resp: http.client.HTTPResponse, deadline: float, t0: float) -> StreamResult:
    result = StreamResult()
    buf = b""
    event_lines: list = []
    total = 0
    while True:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            result.error = "timeout"
            return result
        try:
            chunk = resp.read1(4096)
        except socket.timeout:
            if time.perf_counter() >= deadline:
                result.error = "timeout"
                return result
            continue
        except Exception:
            result.error = "read_error"
            return result
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            result.error = "response_too_large"
            return result
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            if len(line) > MAX_LINE_BYTES:
                result.error = "line_too_large"
                return result
            if line.endswith(b"\r"):
                line = line[:-1]
            try:
                text = line.decode("utf-8")
            except UnicodeDecodeError:
                result.error = "sse_bad_utf8"
                return result
            if text == "":
                if event_lines:
                    _process_sse_event(event_lines, result, t0)
                    event_lines = []
                    if result.error:
                        return result
                    if result.saw_done:
                        if result.finish_reason is None:
                            result.error = "sse_missing_finish_reason"
                        return result
            else:
                event_lines.append(text)
        if len(buf) > MAX_LINE_BYTES:
            result.error = "line_too_large"
            return result
    if event_lines:
        _process_sse_event(event_lines, result, t0)
    if result.error:
        return result
    if not result.saw_done:
        result.error = "sse_missing_done"
        return result
    if result.finish_reason is None:
        result.error = "sse_missing_finish_reason"
        return result
    return result


# ---------------------------------------------------------------------------
# Request execution
# ---------------------------------------------------------------------------

def _build_payload(manifest: dict, task: dict) -> dict:
    payload = {
        "model": manifest["model"],
        "messages": task["messages"],
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    for k, v in manifest["sampling"].items():
        payload[k] = v
    return payload


def run_request(manifest: dict, task: dict, repetition: int, request_id: str) -> dict:
    """Execute one streaming chat request. Returns record dict."""
    started_utc = datetime.now(timezone.utc).isoformat()
    task_sha = sha256_text(canonical_json(task))
    record = {
        "request_id": request_id,
        "task_id": task["id"],
        "repetition": repetition,
        "started_utc": started_utc,
        "latency_ms": None,
        "first_content_ms": None,
        "first_reasoning_ms": None,
        "content_chunk_count": 0,
        "content_chunk_gaps_ms": [],
        "usage": None,
        "response_model": None,
        "response_upstream": None,
        "finish_reason": None,
        "quality_pass": False,
        "validator": task["validator"]["kind"],
        "error": None,
        "error_kind": None,
        "transport_complete": False,
        "response_sha256": None,
        "response_chars": None,
        "task_sha256": task_sha,
        "censored": False,
    }

    parts = urlsplit(manifest["endpoint"])
    host = parts.hostname
    port = parts.port or (443 if parts.scheme == "https" else 80)
    path = parts.path
    if parts.query:
        path += "?" + parts.query

    api_key = None
    if manifest.get("api_key_env"):
        api_key = os.environ.get(manifest["api_key_env"])
        if not api_key:
            record["error"] = "missing_api_key_env"
            record["error_kind"] = "transport"
            return record

    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "User-Agent": "ods-perf-pilot/1",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = _build_payload(manifest, task)
    body = json.dumps(payload).encode("utf-8")

    t0 = time.perf_counter()
    deadline = t0 + manifest["timeout_s"]

    if parts.scheme == "https":
        ctx = ssl.create_default_context()
        conn = http.client.HTTPSConnection(host, port, timeout=manifest["timeout_s"], context=ctx)
    else:
        conn = http.client.HTTPConnection(host, port, timeout=manifest["timeout_s"])

    watchdog_stop = threading.Event()
    watchdog_sock = {"sock": None}

    def _watchdog() -> None:
        while not watchdog_stop.is_set():
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                s = watchdog_sock["sock"]
                if s is not None:
                    try:
                        s.shutdown(socket.SHUT_RDWR)
                    except Exception:
                        pass
                return
            watchdog_stop.wait(min(remaining, 0.05))

    wd = threading.Thread(target=_watchdog, daemon=True)
    wd.start()

    try:
        try:
            conn.connect()
        except socket.timeout:
            record["error"] = "connect_timeout"
            record["error_kind"] = "transport"
            record["latency_ms"] = round((time.perf_counter() - t0) * 1000.0, 3)
            record["censored"] = True
            return record
        except Exception:
            record["error"] = "connect_error"
            record["error_kind"] = "transport"
            record["latency_ms"] = round((time.perf_counter() - t0) * 1000.0, 3)
            return record

        watchdog_sock["sock"] = conn.sock

        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            record["error"] = "timeout"
            record["error_kind"] = "transport"
            record["latency_ms"] = round((time.perf_counter() - t0) * 1000.0, 3)
            record["censored"] = True
            return record
        try:
            if conn.sock is not None:
                conn.sock.settimeout(remaining)
        except Exception:
            pass

        try:
            conn.request("POST", path, body=body, headers=headers)
            resp = conn.getresponse()
        except socket.timeout:
            record["error"] = "timeout"
            record["error_kind"] = "transport"
            record["latency_ms"] = round((time.perf_counter() - t0) * 1000.0, 3)
            record["censored"] = True
            return record
        except Exception:
            timed_out = time.perf_counter() >= deadline
            record["error"] = "timeout" if timed_out else "request_error"
            record["censored"] = timed_out
            record["error_kind"] = "transport"
            record["latency_ms"] = round((time.perf_counter() - t0) * 1000.0, 3)
            return record

        status = resp.status
        if status != 200:
            record["error"] = f"http_status_{status}"
            record["error_kind"] = "transport"
            record["latency_ms"] = round((time.perf_counter() - t0) * 1000.0, 3)
            return record

        ctype = resp.getheader("Content-Type") or ""
        if "text/event-stream" not in ctype.lower():
            record["error"] = "bad_content_type"
            record["error_kind"] = "transport"
            record["latency_ms"] = round((time.perf_counter() - t0) * 1000.0, 3)
            return record

        upstream = resp.getheader("X-ODS-Upstream")
        if upstream:
            record["response_upstream"] = upstream

        stream = _read_stream(resp, deadline, t0)
        elapsed = (time.perf_counter() - t0) * 1000.0
        record["latency_ms"] = round(elapsed, 3)
        record["first_content_ms"] = round(stream.first_content_ms, 3) if stream.first_content_ms is not None else None
        record["first_reasoning_ms"] = round(stream.first_reasoning_ms, 3) if stream.first_reasoning_ms is not None else None
        record["content_chunk_count"] = stream.content_chunk_count
        record["content_chunk_gaps_ms"] = stream.content_chunk_gaps_ms
        record["usage"] = stream.usage
        record["response_model"] = stream.response_model
        record["finish_reason"] = stream.finish_reason

        if stream.error:
            record["error"] = "timeout" if time.perf_counter() >= deadline else stream.error
            record["error_kind"] = "transport"
            if record["error"] == "timeout":
                record["censored"] = True
            return record

        record["transport_complete"] = True

        if stream.response_model is not None and stream.response_model != manifest["model"]:
            record["error"] = "response_model_mismatch"
            record["error_kind"] = "identity"
            return record

        if stream.finish_reason == "length":
            record["error"] = "finish_reason_length"
            record["error_kind"] = "quality"
            return record

        text = stream.content
        record["response_sha256"] = sha256_text(text)
        record["response_chars"] = len(text)
        ok, verr = validate_answer(task["validator"], text)
        if ok:
            record["quality_pass"] = True
        else:
            record["error"] = verr
            record["error_kind"] = "quality"
        return record
    finally:
        watchdog_stop.set()
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def _percentile(values: list, p: float) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * p
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def summarize(manifest: dict, records: list, elapsed_s: float, interrupted: bool, dispatched: int) -> dict:
    scheduled = len(manifest["tasks"]) * manifest["repetitions"]
    transport_complete = [r for r in records if r.get("transport_complete")]
    censored = [r for r in records if r.get("censored")]
    transport_failures = [r for r in records if r.get("error_kind") == "transport"]
    identity_failures = [r for r in records if r.get("error_kind") == "identity"]
    quality_failures = [r for r in records if r.get("error_kind") == "quality"]
    quality_pass = [r for r in records if r.get("quality_pass")]

    latencies = [r["latency_ms"] for r in transport_complete if r.get("latency_ms") is not None]
    success_latencies = [r["latency_ms"] for r in quality_pass if r.get("latency_ms") is not None]
    slo = manifest["slo_ms"]
    slo_good = [r for r in quality_pass if r["latency_ms"] is not None and r["latency_ms"] <= slo]

    per_task = {}
    for t in manifest["tasks"]:
        tid = t["id"]
        trs = [r for r in records if r["task_id"] == tid]
        tlat = [r["latency_ms"] for r in trs if r.get("transport_complete") and r.get("latency_ms") is not None]
        per_task[tid] = {
            "scheduled": manifest["repetitions"],
            "records": len(trs),
            "quality_pass": sum(1 for r in trs if r.get("quality_pass")),
            "latency_p50_ms": round(_percentile(tlat, 0.5), 3) if tlat else None,
        }

    n = len(latencies)
    p50 = _percentile(latencies, 0.5)
    p95 = _percentile(latencies, 0.95) if n >= 100 else None
    p99 = _percentile(latencies, 0.99) if n >= 1000 else None
    sp50 = _percentile(success_latencies, 0.5)

    return {
        "scope": "engine/gateway pilot; not agent or UI qualification",
        "clock": vars(time.get_clock_info("perf_counter")),
        "runner_sha256": RUNNER_SHA256,
        "python_version": sys.version.split()[0],
        "run_label": manifest["run_label"],
        "target": manifest["target"],
        "layer": manifest["layer"],
        "model": manifest["model"],
        "interrupted": interrupted,
        "scheduled": scheduled,
        "dispatched": dispatched,
        "records": len(records),
        "transport_complete": len(transport_complete),
        "censored": len(censored),
        "quality_pass": len(quality_pass),
        "transport_failures": len(transport_failures),
        "identity_failures": len(identity_failures),
        "quality_failures": len(quality_failures),
        "elapsed_s": round(elapsed_s, 3),
        "validated_tasks_per_s": round(len(quality_pass) / elapsed_s, 6) if elapsed_s > 0 else None,
        "slo_goodput_tasks_per_s": round(len(slo_good) / elapsed_s, 6) if elapsed_s > 0 else None,
        "quality_pass_fraction": round(len(quality_pass) / scheduled, 6) if scheduled else None,
        "latency_p50_ms": round(p50, 3) if p50 is not None else None,
        "latency_p95_ms": round(p95, 3) if p95 is not None else None,
        "latency_p95_label": "insufficient_samples" if p95 is None else "descriptive",
        "latency_p99_ms": round(p99, 3) if p99 is not None else None,
        "latency_p99_label": "insufficient_samples" if p99 is None else "descriptive",
        "successful_task_latency_p50_ms": round(sp50, 3) if sp50 is not None else None,
        "per_task": per_task,
        "metrics_semantics": {
            "latency": "wall time from dispatch to terminal valid protocol completion (DONE+finish_reason); transport failures excluded",
            "successful_task_latency": "latency restricted to quality_pass records; avoids fast-wrong bias",
            "first_content_ms": "observed first nonempty delta.content; not token TTFT",
            "first_reasoning_ms": "observed first nonempty reasoning_content; not token TTFT",
            "content_chunk_count": "SSE content chunks, not tokens",
            "quality_pass_fraction": "denominator is all scheduled requests; all failures and undispatched included",
            "tail_suppression": "p95 requires N>=100, p99 requires N>=1000; reporting policy not statistical proof",
            "capacity": "cannot be concluded from this pilot",
            "confidence": "no confidence interval; synthetic repeated identical cases are not independent quality coverage",
            "dns": "DNS resolution occurs inside connect and is not covered by the socket deadline watchdog",
        },
    }

# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _build_schedule(manifest: dict) -> list:
    rng = random.Random(manifest["schedule_seed"])
    jobs = []
    for t in manifest["tasks"]:
        for rep in range(manifest["repetitions"]):
            jobs.append((t, rep))
    rng.shuffle(jobs)
    return jobs


def run(manifest: dict, out_dir: str) -> dict:
    """Validate and run a pilot. Existing output directories are never reused."""
    manifest = validate_manifest(manifest)
    if manifest.get("api_key_env") and not os.environ.get(manifest["api_key_env"]):
        raise ManifestError("configured credential environment variable is missing or empty")
    if os.path.exists(out_dir):
        raise ManifestError(f"output directory already exists: {out_dir}")
    os.makedirs(out_dir, exist_ok=False)

    # Credential selectors are local execution settings, not benchmark identity.
    # Do not publish or fingerprint even the environment-variable name.
    recorded_manifest = {**manifest, "api_key_env": None}
    manifest_sha = sha256_text(canonical_json(recorded_manifest))
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"manifest": recorded_manifest, "manifest_sha256": manifest_sha}, f, indent=2, sort_keys=True)

    schedule = _build_schedule(manifest)
    records = []
    records_lock = threading.Lock()
    requests_path = os.path.join(out_dir, "requests.jsonl")
    interrupted = False
    t_start = None
    t_end = None
    dispatched = 0

    def _append_record(rec: dict) -> None:
        with records_lock:
            records.append(rec)
            with open(requests_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, sort_keys=True) + "\n")

    def _worker(task: dict, rep: int, rid: str) -> dict:
        try:
            rec = run_request(manifest, task, rep, rid)
        except Exception as e:
            rec = {
                "request_id": rid,
                "task_id": task["id"],
                "repetition": rep,
                "started_utc": datetime.now(timezone.utc).isoformat(),
                "latency_ms": None,
                "first_content_ms": None,
                "first_reasoning_ms": None,
                "content_chunk_count": 0,
                "content_chunk_gaps_ms": [],
                "usage": None,
                "response_model": None,
                "response_upstream": None,
                "finish_reason": None,
                "quality_pass": False,
                "validator": task["validator"]["kind"],
                "error": f"worker_exception:{type(e).__name__}",
                "error_kind": "transport",
                "transport_complete": False,
                "response_sha256": None,
                "response_chars": None,
                "task_sha256": sha256_text(canonical_json(task)),
                "censored": False,
            }
        _append_record(rec)
        return rec

    try:
        with ThreadPoolExecutor(max_workers=manifest["concurrency"]) as pool:
            pending = {}
            idx = 0
            while idx < len(schedule) or pending:
                while idx < len(schedule) and len(pending) < manifest["concurrency"]:
                    task, rep = schedule[idx]
                    rid = f"{task['id']}-r{rep}-{uuid.uuid4().hex[:8]}"
                    if t_start is None:
                        t_start = time.perf_counter()
                    fut = pool.submit(_worker, task, rep, rid)
                    pending[fut] = rid
                    idx += 1
                    dispatched += 1
                if not pending:
                    break
                done, _ = wait(pending.keys(), return_when=FIRST_COMPLETED)
                for fut in done:
                    pending.pop(fut, None)
                    fut.result()
                    t_end = time.perf_counter()
    except BaseException:
        interrupted = True
        raise
    finally:
        if t_start is None:
            t_start = time.perf_counter()
        if t_end is None or interrupted:
            t_end = time.perf_counter()
        elapsed = t_end - t_start
        summary = summarize(manifest, records, elapsed, interrupted, dispatched)
        with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, sort_keys=True)

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="ODS engine/gateway chat transport performance pilot")
    parser.add_argument("--manifest", required=True, help="path to manifest JSON")
    parser.add_argument("--out", required=True, help="new output directory")
    args = parser.parse_args(argv)

    try:
        with open(args.manifest, "r", encoding="utf-8") as f:
            raw = json.load(f, parse_constant=_reject_constant, object_pairs_hook=_unique_object)
    except Exception:
        print("error: cannot read manifest JSON", file=sys.stderr)
        return 2

    try:
        manifest = validate_manifest(raw)
    except ManifestError as e:
        print(f"error: invalid manifest: {e}", file=sys.stderr)
        return 2

    if manifest.get("api_key_env"):
        if not os.environ.get(manifest["api_key_env"]):
            print("error: configured authentication environment variable is missing or empty", file=sys.stderr)
            return 2

    try:
        summary = run(manifest, args.out)
    except ManifestError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130

    print(json.dumps(summary, indent=2, sort_keys=True))
    if summary.get("quality_pass", 0) < summary.get("scheduled", 0):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
