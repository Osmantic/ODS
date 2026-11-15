"""ODS open-loop chat transport load adapter (bounded engine/gateway study).

Scope: bounded engine/gateway load study; no production capacity qualification.
Stdlib only. Reuses probe.py validation, scheduling, request execution, and
hashing helpers. Adds a precomputed fixed or seeded Poisson arrival schedule with explicit
client-side admission refusals (client_busy / scheduler_late / admission_stop /
admission_error). Never cancels running threads; drains in-flight work using
the existing run_request timeout.

CLI: open_loop.py --manifest M --load-config L --out NEW_DIRECTORY
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import probe  # noqa: E402


with open(__file__, "rb") as _source:
    RUNNER_SHA256 = probe.sha256_hex(_source.read())

LOAD_CONFIG_KEYS = {"rate_rps", "max_schedule_lag_ms"}
OPTIONAL_LOAD_CONFIG_KEYS = {"arrival_process", "arrival_seed"}
MAX_ARRIVAL_SPAN_S = 600.0
MAX_PLANNED = probe.MAX_TOTAL_REQUESTS
MAX_SEED = 2**32 - 1
SCOPE = "bounded engine/gateway load study; no production capacity qualification"


class LoadConfigError(ValueError):
    pass


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise LoadConfigError(msg)


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and v not in (float("inf"), float("-inf"))


def validate_load_config(cfg: Any) -> dict:
    _require(isinstance(cfg, dict), "load config must be object")
    unknown = set(cfg.keys()) - LOAD_CONFIG_KEYS - OPTIONAL_LOAD_CONFIG_KEYS
    _require(not unknown, f"unknown load config fields: {sorted(unknown)}")
    _require(LOAD_CONFIG_KEYS <= set(cfg.keys()), "load config must have rate_rps and max_schedule_lag_ms")
    rate = cfg["rate_rps"]
    _require(_is_number(rate) and 0.01 <= rate <= 100.0, "rate_rps must be number 0.01..100")
    lag = cfg["max_schedule_lag_ms"]
    _require(isinstance(lag, int) and not isinstance(lag, bool) and 1 <= lag <= 1000,
             "max_schedule_lag_ms must be int 1..1000")
    process = cfg.get("arrival_process", "fixed")
    _require(process in ("fixed", "poisson"), "arrival_process must be 'fixed' or 'poisson'")
    normalized = {"rate_rps": float(rate), "max_schedule_lag_ms": int(lag)}
    if process == "fixed":
        _require("arrival_seed" not in cfg, "arrival_seed is only valid with arrival_process='poisson'")
        return normalized
    _require("arrival_seed" in cfg, "arrival_process='poisson' requires arrival_seed")
    seed = cfg["arrival_seed"]
    _require(isinstance(seed, int) and not isinstance(seed, bool) and 0 <= seed <= MAX_SEED,
             f"arrival_seed must be int 0..{MAX_SEED}")
    normalized["arrival_process"] = "poisson"
    normalized["arrival_seed"] = int(seed)
    return normalized


def _arrival_offsets_ms(planned: int, load_config: dict) -> list:
    """Return planned arrival offsets (ms) for the configured arrival process.

    Fixed: offsets are i * 1000/rate_rps for i in 0..planned-1.
    Poisson: offsets are cumulative exponential gaps drawn from an isolated
    random.Random(seed).expovariate(rate_rps). The first arrival is also an
    exponential gap (no forced initial arrival at t=0). The schedule is never
    truncated, rescaled, resampled, or dropped to fit the span bound; if the
    realized span exceeds MAX_ARRIVAL_SPAN_S the whole config is rejected.
    """
    rate = load_config["rate_rps"]
    if load_config.get("arrival_process", "fixed") == "poisson":
        rng = random.Random(load_config["arrival_seed"])
        offsets = []
        cumulative_s = 0.0
        for _ in range(planned):
            cumulative_s += rng.expovariate(rate)
            offsets.append(cumulative_s * 1000.0)
        return offsets
    interval_ms = 1000.0 / rate
    return [i * interval_ms for i in range(planned)]


def build_arrival_schedule(manifest: dict, load_config: dict) -> list:
    """Precompute arrival offsets (ms) for every planned request.

    Uses probe._build_schedule for deterministic task/repetition ordering, then
    assigns offsets per the configured arrival process. Returns list of dicts
    with task, repetition, request_id, scheduled_offset_ms.
    """
    jobs = probe._build_schedule(manifest)
    planned = len(jobs)
    _require(planned <= MAX_PLANNED, f"planned requests {planned} exceeds {MAX_PLANNED}")
    offsets = _arrival_offsets_ms(planned, load_config)
    span_s = (offsets[-1] / 1000.0) if offsets else 0.0
    _require(span_s <= MAX_ARRIVAL_SPAN_S, f"arrival span {span_s:.3f}s exceeds {MAX_ARRIVAL_SPAN_S}s")
    schedule = []
    for (task, rep), offset_ms in zip(jobs, offsets):
        rid = f"{task['id']}-r{rep}-{uuid.uuid4().hex[:8]}"
        schedule.append({
            "task": task,
            "repetition": rep,
            "request_id": rid,
            "scheduled_offset_ms": round(offset_ms, 3),
        })
    return schedule


def _refusal_record(entry: dict, reason: str, decision_offset_ms: Optional[float],
                    dispatch_offset_ms: Optional[float], schedule_lag_ms: Optional[float]) -> dict:
    task = entry["task"]
    return {
        "request_id": entry["request_id"],
        "task_id": task["id"],
        "repetition": entry["repetition"],
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "latency_ms": None,
        "first_content_ms": None,
        "first_reasoning_ms": None,
        "content_chunk_count": 0,
        "content_chunk_gaps_ms": [],
        "usage": None,
        "engine_timings": None,
        "response_model": None,
        "response_upstream": None,
        "finish_reason": None,
        "quality_pass": False,
        "validator": task["validator"]["kind"],
        "error": reason,
        "error_kind": "admission",
        "transport_complete": False,
        "response_sha256": None,
        "response_chars": None,
        "task_sha256": probe.sha256_text(probe.canonical_json(task)),
        "censored": False,
        "scheduled_offset_ms": entry["scheduled_offset_ms"],
        "decision_offset_ms": round(decision_offset_ms, 3) if decision_offset_ms is not None else None,
        "dispatch_offset_ms": round(dispatch_offset_ms, 3) if dispatch_offset_ms is not None else None,
        "schedule_lag_ms": round(schedule_lag_ms, 3) if schedule_lag_ms is not None else None,
        "arrival_to_completion_ms": None,
    }


def _augment(rec: dict, entry: dict, dispatch_offset_ms: float, schedule_lag_ms: float) -> dict:
    rec["scheduled_offset_ms"] = entry["scheduled_offset_ms"]
    rec["decision_offset_ms"] = round(dispatch_offset_ms, 3)
    rec["dispatch_offset_ms"] = round(dispatch_offset_ms, 3)
    rec["schedule_lag_ms"] = round(schedule_lag_ms, 3)
    lat = rec.get("latency_ms")
    if lat is not None:
        rec["arrival_to_completion_ms"] = round(schedule_lag_ms + lat, 3)
    else:
        rec["arrival_to_completion_ms"] = None
    return rec


def _percentile(values: list, p: float) -> Optional[float]:
    return probe._percentile(values, p)


def summarize(manifest: dict, load_config: dict, records: list, elapsed_s: float,
              offered_span_s: float, admission_stopped: bool, admission_error: bool) -> dict:
    planned = len(manifest["tasks"]) * manifest["repetitions"]
    dispatched = sum(1 for r in records if r.get("error_kind") != "admission")
    refusals = [r for r in records if r.get("error_kind") == "admission"]
    refusals_by_reason: dict = {}
    for r in refusals:
        k = r.get("error") or "unknown"
        refusals_by_reason[k] = refusals_by_reason.get(k, 0) + 1
    observed = [r for r in records if r.get("error_kind") != "admission"]
    quality_pass = [r for r in observed if r.get("quality_pass")]
    transport_failures = [r for r in observed if r.get("error_kind") == "transport"]
    identity_failures = [r for r in observed if r.get("error_kind") == "identity"]
    quality_failures = [r for r in observed if r.get("error_kind") == "quality"]
    success_latencies = [r["arrival_to_completion_ms"] for r in quality_pass
                         if r.get("arrival_to_completion_ms") is not None]
    # Scheduler lag max must include observed late refusals (scheduler_late) but
    # ignore future-cancelled refusals (admission_stop / admission_error) whose
    # schedule_lag_ms is None because they were never observed as late.
    lags = [r["schedule_lag_ms"] for r in records if r.get("schedule_lag_ms") is not None]
    n = len(success_latencies)
    p50 = _percentile(success_latencies, 0.5)
    p95 = _percentile(success_latencies, 0.95) if n >= 100 else None
    goodput = (len(quality_pass) / elapsed_s) if elapsed_s > 0 else None
    success_fraction = (len(quality_pass) / planned) if planned else None
    return {
        "scope": SCOPE,
        "clock": vars(time.get_clock_info("perf_counter")),
        "runner_sha256": RUNNER_SHA256,
        "probe_runner_sha256": probe.RUNNER_SHA256,
        "python_version": sys.version.split()[0],
        "run_label": manifest["run_label"],
        "target": manifest["target"],
        "layer": manifest["layer"],
        "model": manifest["model"],
        "load_config": load_config,
        "planned": planned,
        "total_records": len(records),
        "dispatched": dispatched,
        "refusals": len(refusals),
        "refusals_by_reason": refusals_by_reason,
        "observed_results": len(observed),
        "transport_complete": sum(r.get("transport_complete") is True for r in observed),
        "internal_failures": sum(r.get("error_kind") == "internal" for r in observed),
        "successful_tasks": len(quality_pass),
        "transport_completions": len(observed) - len(transport_failures),
        "success_fraction": round(success_fraction, 6) if success_fraction is not None else None,
        "goodput_valid_tasks_per_s": round(goodput, 6) if goodput is not None else None,
        "transport_failures": len(transport_failures),
        "quality_failures": len(quality_failures),
        "identity_failures": len(identity_failures),
        "scheduler_lag_max_ms": round(max(lags), 3) if lags else None,
        "arrival_to_completion_p50_ms": round(p50, 3) if p50 is not None else None,
        "arrival_to_completion_p95_ms": round(p95, 3) if p95 is not None else None,
        "arrival_to_completion_p95_label": "insufficient_samples" if p95 is None else "descriptive",
        "actual_duration_s": round(elapsed_s, 3),
        "offered_arrivals_span_s": round(offered_span_s, 3),
        "admission_stopped": admission_stopped,
        "admission_error": admission_error,
        "metrics_semantics": {
            "arrival_process": "fixed offsets or Poisson cumulative exponential gaps; rate_rps is nominal intensity, not observed realized rate or capacity",
            "goodput": "quality_pass / elapsed_s (includes drain); not divided by planned",
            "success_fraction": "quality_pass / planned (planned is denominator)",
            "arrival_to_completion": "scheduled arrival to observed worker completion; includes handoff and request time",
            "scheduler_lag": "dispatch_offset_ms - scheduled_offset_ms; positive means late",
            "refusals": "client-side admission refusals; not server capacity signals",
            "tail_suppression": "p95 requires N>=100 successful samples",
            "dns": "DNS resolution occurs inside connect and is not covered by the socket deadline watchdog",
            "capacity": "cannot be concluded from this study",
            "confidence": "no confidence interval; synthetic repeated identical cases are not independent quality coverage",
        },
    }


def run(manifest: dict, load_config: dict, out_dir: str, admission_allowed=None) -> dict:
    manifest = probe.validate_manifest(manifest)
    load_config = validate_load_config(load_config)
    if manifest.get("api_key_env") and not os.environ.get(manifest["api_key_env"]):
        raise probe.ManifestError("configured credential environment variable is missing or empty")
    # Validate arrival span BEFORE creating out_dir so invalid configs leave no output.
    schedule = build_arrival_schedule(manifest, load_config)
    offered_span_s = (schedule[-1]["scheduled_offset_ms"] / 1000.0) if schedule else 0.0
    if os.path.exists(out_dir):
        raise probe.ManifestError(f"output directory already exists: {out_dir}")
    os.makedirs(out_dir, exist_ok=False)

    recorded_manifest = {**manifest, "api_key_env": None}
    manifest_sha = probe.sha256_text(probe.canonical_json(recorded_manifest))
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"manifest": recorded_manifest, "manifest_sha256": manifest_sha}, f, indent=2, sort_keys=True)
    with open(os.path.join(out_dir, "load_config.json"), "w", encoding="utf-8") as f:
        json.dump({"load_config": load_config,
                   "load_config_sha256": probe.sha256_text(probe.canonical_json(load_config))},
                  f, indent=2, sort_keys=True)

    records = []
    records_lock = threading.Lock()
    requests_path = os.path.join(out_dir, "requests.jsonl")
    with open(requests_path, "x", encoding="utf-8"):
        pass

    admission_stopped = False
    admission_error = False
    interrupted = False
    callback_exc: Optional[BaseException] = None
    t_start = None
    t_end = None
    max_lag_ms = float(load_config["max_schedule_lag_ms"])

    def _append_record(rec: dict) -> None:
        with records_lock:
            records.append(rec)
            with open(requests_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, sort_keys=True) + "\n")

    def _worker(entry: dict, decision_offset_ms: float) -> dict:
        dispatch_offset_ms = (time.perf_counter() - t_start) * 1000.0
        schedule_lag_ms = max(0.0, dispatch_offset_ms - entry["scheduled_offset_ms"])
        if schedule_lag_ms > max_lag_ms:
            rec = _refusal_record(entry, "scheduler_late", dispatch_offset_ms, None, schedule_lag_ms)
            _append_record(rec)
            return rec
        try:
            rec = probe.run_request(manifest, entry["task"], entry["repetition"], entry["request_id"])
        except Exception:
            # Sanitized static category; do not leak arbitrary type names.
            rec = {
                "request_id": entry["request_id"],
                "task_id": entry["task"]["id"],
                "repetition": entry["repetition"],
                "started_utc": datetime.now(timezone.utc).isoformat(),
                "latency_ms": None,
                "first_content_ms": None,
                "first_reasoning_ms": None,
                "content_chunk_count": 0,
                "content_chunk_gaps_ms": [],
                "usage": None,
                "engine_timings": None,
                "response_model": None,
                "response_upstream": None,
                "finish_reason": None,
                "quality_pass": False,
                "validator": entry["task"]["validator"]["kind"],
                "error": "worker_unexpected",
                "error_kind": "internal",
                "transport_complete": False,
                "response_sha256": None,
                "response_chars": None,
                "task_sha256": probe.sha256_text(probe.canonical_json(entry["task"])),
                "censored": False,
            }
        completed_offset_ms = (time.perf_counter() - t_start) * 1000.0
        _augment(rec, entry, dispatch_offset_ms, schedule_lag_ms)
        rec["decision_offset_ms"] = round(decision_offset_ms, 3)
        rec["completion_offset_ms"] = round(completed_offset_ms, 3)
        rec["arrival_to_completion_ms"] = round(completed_offset_ms - entry["scheduled_offset_ms"], 3)
        _append_record(rec)
        return rec

    try:
        with ThreadPoolExecutor(max_workers=manifest["concurrency"]) as pool:
            pending = set()
            t_start = time.perf_counter()
            for entry in schedule:
                if admission_stopped:
                    reason = "admission_error" if admission_error else "admission_stop"
                    _append_record(_refusal_record(entry, reason, (time.perf_counter()-t_start)*1000, None, None))
                    continue
                deadline = t_start + entry["scheduled_offset_ms"] / 1000.0
                while time.perf_counter() < deadline:
                    time.sleep(min(.05, max(0.0, deadline - time.perf_counter())))
                # Completion never moves the next arrival deadline.
                for future in list(pending):
                    if future.done():
                        pending.remove(future)
                        future.result()
                if admission_allowed is not None:
                    try:
                        if admission_allowed() is not True:
                            admission_stopped = True
                    except Exception as error:
                        admission_error = admission_stopped = True
                        callback_exc = error
                now_ms = (time.perf_counter() - t_start) * 1000.0
                lag_ms = max(0.0, now_ms - entry["scheduled_offset_ms"])
                if admission_stopped:
                    reason = "admission_error" if admission_error else "admission_stop"
                    _append_record(_refusal_record(entry, reason, now_ms, None, None))
                elif lag_ms > max_lag_ms:
                    _append_record(_refusal_record(entry, "scheduler_late", now_ms, None, lag_ms))
                elif len(pending) >= manifest["concurrency"]:
                    _append_record(_refusal_record(entry, "client_busy", now_ms, None, lag_ms))
                else:
                    pending.add(pool.submit(_worker, entry, now_ms))
            # The executor never receives more than concurrency unfinished jobs.
            # Threads are drained, never declared cancelled while still running.
            for future in pending:
                future.result()
    except BaseException as e:
        interrupted = True
        callback_exc = e
    finally:
        if t_start is None:
            t_start = time.perf_counter()
        recorded_ids = {r["request_id"] for r in records}
        for entry in schedule:
            if entry["request_id"] in recorded_ids:
                continue
            _append_record(_refusal_record(entry, "admission_error", (time.perf_counter()-t_start)*1000, None, None))
        # t_end MUST be read AFTER schedule/drain complete.
        t_end = time.perf_counter()
        elapsed = t_end - t_start
        summary = summarize(manifest, load_config, records, elapsed, offered_span_s,
                            admission_stopped, admission_error)
        summary["interrupted"] = interrupted
        with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, sort_keys=True)

    if callback_exc is not None:
        raise callback_exc
    return summary


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="ODS open-loop chat transport load adapter")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--load-config", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    try:
        with open(args.manifest, "r", encoding="utf-8") as f:
            raw = json.load(f, parse_constant=probe._reject_constant, object_pairs_hook=probe._unique_object)
    except Exception:
        print("error: cannot read manifest JSON", file=sys.stderr)
        return 2
    try:
        manifest = probe.validate_manifest(raw)
    except probe.ManifestError as e:
        print(f"error: invalid manifest: {e}", file=sys.stderr)
        return 2

    try:
        with open(args.load_config, "r", encoding="utf-8") as f:
            raw_cfg = json.load(f, parse_constant=probe._reject_constant, object_pairs_hook=probe._unique_object)
    except Exception:
        print("error: cannot read load config JSON", file=sys.stderr)
        return 2
    try:
        load_config = validate_load_config(raw_cfg)
    except LoadConfigError as e:
        print(f"error: invalid load config: {e}", file=sys.stderr)
        return 2

    if manifest.get("api_key_env") and not os.environ.get(manifest["api_key_env"]):
        print("error: configured authentication environment variable is missing or empty", file=sys.stderr)
        return 2

    try:
        summary = run(manifest, load_config, args.out)
    except probe.ManifestError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except LoadConfigError as e:
        print(f"error: invalid load config: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130

    print(json.dumps(summary, indent=2, sort_keys=True))
    if summary.get("successful_tasks", 0) < summary.get("planned", 0):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
