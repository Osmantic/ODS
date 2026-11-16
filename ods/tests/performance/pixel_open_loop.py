"""Bounded fixed/Poisson arrivals through the real Portal task adapter.

One fresh user turn per task; synthetic fixtures may inject request_runner only
through Python. No queue, retries, or thread cancellation. An unsafe result stops
new admissions; external resource coordination must cancel already-running work
by its persisted exact identity. Workers drain through the adapter's deadlines
(which cannot bound OS DNS resolution). This is not a capacity qualification.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
import threading
import time
import uuid

import open_loop
import pixel_adapter
import probe


class RunError(RuntimeError):
    """Sanitized coordinator/persistence failure; evidence may be incomplete."""


def _single(manifest, task):
    return {**{k: manifest[k] for k in (
        "schema_version", "base_url", "api_key_env", "timeout_s", "recovery_timeout_s")}, "task": task}


def validate_manifest(value):
    keys = {"schema_version", "base_url", "api_key_env", "timeout_s", "recovery_timeout_s",
            "tasks", "concurrency", "repetitions", "schedule_seed"}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("invalid_manifest")
    for key, low, high in (("concurrency", 1, 8), ("repetitions", 1, 100), ("schedule_seed", 0, 2**32-1)):
        if type(value[key]) is not int or not low <= value[key] <= high:
            raise ValueError("invalid_load_bound")
    tasks = value["tasks"]
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= 100 or len(tasks)*value["repetitions"] > 1000:
        raise ValueError("invalid_task_count")
    seen = set()
    for task in tasks:
        pixel_adapter.validate_manifest(_single(value, task))
        if task["id"] in seen or len(task["messages"]) != 1 or task["messages"][0]["role"] != "user":
            raise ValueError("fresh_unique_user_tasks_required")
        seen.add(task["id"])
    return json.loads(json.dumps(value, allow_nan=False))


def _hash(value):
    return probe.sha256_text(probe.canonical_json(value))


def _persist(path, value, *, append=False):
    with path.open("a" if append else "x", encoding="utf-8") as handle:
        handle.write(json.dumps(value, sort_keys=True, allow_nan=False)+"\n")
        handle.flush()
        os.fsync(handle.fileno())


def _record(entry, now_ms, reason=None, offered=True):
    return {**{k: v for k, v in entry.items() if k != "task"},
            "decision_offset_ms": now_ms, "dispatch_offset_ms": None,
            "completion_offset_ms": None, "arrival_to_completion_ms": None,
            "offered": offered, "dispatched": False, "submission_attempted": False,
            "outcome": "refused", "refusal_reason": reason, "receipt": None}


def _summary(config, plan, records, elapsed, stop, interrupted, sources):
    dispatched = [r for r in records if r["dispatched"]]
    successful = [r for r in dispatched if r["submission_attempted"] is True
                  and (r["receipt"] or {}).get("result_state") == "complete"
                  and all((r["receipt"] or {}).get(k) is v for k, v in (
                      ("success", True), ("quality_pass", True), ("censored", False), ("admission_safe", True)))]
    latencies = [r["arrival_to_completion_ms"] for r in successful]
    refusals = {}
    for r in records:
        if r["refusal_reason"]:
            refusals[r["refusal_reason"]] = refusals.get(r["refusal_reason"], 0)+1
    receipts = [r["receipt"] or {} for r in dispatched]
    return {"schema_version": 1, "scope": "bounded synthetic agent study; no capacity qualification",
            "source_sha256": sources, "python_version": sys.version.split()[0],
            "load_config": config, "planned": len(plan), "total_records": len(records),
            "offered": sum(r["offered"] for r in records), "dispatched": len(dispatched),
            "submission_attempted": sum(r["submission_attempted"] is True for r in records),
            "submission_unknown": sum(r["submission_attempted"] is None for r in records),
            "successful_tasks": len(successful), "answer_quality_pass": sum(r.get("answer_quality_pass") is True for r in receipts),
            "quality_failures": sum(r.get("result_state") == "complete" and r.get("quality_pass") is False for r in receipts),
            "unresolved_tasks": sum(r.get("admission_safe") is not True for r in receipts),
            "censored": sum(r.get("censored") is True for r in receipts), "refusals_by_reason": refusals,
            "actual_duration_s": elapsed, "planned_arrival_span_s": plan[-1]["scheduled_offset_ms"]/1000,
            "offered_arrival_span_s": max((r["scheduled_offset_ms"]/1000 for r in records if r["offered"]), default=0),
            "success_fraction": len(successful)/len(plan),
            "goodput_valid_tasks_per_s": len(successful)/elapsed if elapsed else None,
            "successful_arrival_to_completion_p50_ms": probe._percentile(latencies, .5),
            "successful_arrival_to_completion_p95_ms": probe._percentile(latencies, .95) if len(latencies) >= 100 else None,
            "admission_stopped": stop["reason"] is not None, "stop_reason": stop["reason"], "interrupted": interrupted,
            "metrics_semantics": {"arrival_span": "start to last offered/planned offset, including first Poisson gap",
                "goodput": "correct successful tasks / elapsed including wait, drain and native observation",
                "success_fraction": "successful / all planned, including never-offered after stop",
                "latency": "scheduled arrival to worker completion, including native observation; not browser latency",
                "p95": "null below 100 successful observations; descriptive otherwise"}}


def run(manifest, load_config, out_dir, *, admission_allowed=None, native_sessions_dir=None, request_runner=None):
    manifest = validate_manifest(manifest)
    config = open_loop.validate_load_config(load_config)
    if not os.environ.get(manifest["api_key_env"]):
        raise ValueError("missing_credential")
    sources = {name: probe.sha256_hex(Path(__file__).with_name(name).read_bytes()) for name in (
        "pixel_open_loop.py", "pixel_adapter.py", "native_receipt_join.py", "open_loop.py", "probe.py")}
    plan = [{"task": e["task"], "task_id": e["task"]["id"], "task_sha256": _hash(e["task"]),
             "request_id": uuid.uuid4().hex, "chat_id": uuid.uuid4().hex,
             "repetition": e["repetition"], "scheduled_offset_ms": e["scheduled_offset_ms"]}
            for e in open_loop.build_arrival_schedule(manifest, config)]
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=False)
    public_manifest = {k: manifest[k] for k in ("schema_version", "timeout_s", "recovery_timeout_s",
                                               "concurrency", "repetitions", "schedule_seed")}
    public_manifest["tasks"] = [{"id": t["id"], "task_sha256": _hash(t),
                                 "required_tools": t.get("required_tools", [])} for t in manifest["tasks"]]
    _persist(directory/"manifest.json", {"manifest": public_manifest, "manifest_sha256": _hash(public_manifest)})
    _persist(directory/"load_config.json", config)
    _persist(directory/"plan.json", [{k: v for k, v in e.items() if k != "task"} for e in plan])
    for name in ("requests.jsonl", "dispatches.jsonl"):
        (directory/name).touch(exist_ok=False)
    runner = request_runner or pixel_adapter.run
    lock = threading.RLock()
    stopped = threading.Event()
    state = {"reason": None, "offset_ms": None}
    records, pending, authorized = {}, {}, set()
    io_failed = threading.Event()
    interrupted, fatal = False, False
    start = time.perf_counter()

    def now():
        return (time.perf_counter()-start)*1000

    def latch(reason):
        with lock:
            if state["reason"] is None:
                state.update(reason=reason, offset_ms=now())
            stopped.set()

    def offered(entry):
        limit = state["offset_ms"] if stopped.is_set() else now()
        return entry["scheduled_offset_ms"] <= limit

    def admission():
        with lock:
            if not stopped.is_set() and admission_allowed is not None:
                try:
                    if admission_allowed() is not True:
                        latch("resource_stop")
                except Exception:
                    latch("resource_error")
            return not stopped.is_set()

    def save(record):
        if record["request_id"] in records:
            latch("duplicate_record")
            raise RunError("duplicate_record")
        records[record["request_id"]] = record
        try:
            _persist(directory/"requests.jsonl", record, append=True)
        except Exception:
            io_failed.set()
            latch("persistence_error")
            raise RunError("persistence_error") from None

    def worker(entry, decision):
        record = _record(entry, decision)
        try:
            with lock:
                if not admission():
                    return _record(entry, decision, state["reason"], offered(entry))
                if now()-entry["scheduled_offset_ms"] > config["max_schedule_lag_ms"]:
                    return _record(entry, decision, "scheduler_late")
                _persist(directory/"dispatches.jsonl", {
                    **{k: v for k, v in entry.items() if k != "task"}, "intent_offset_ms": now()}, append=True)
                # Slow persistence/callback must not convert a missed arrival into a late burst.
                if now()-entry["scheduled_offset_ms"] > config["max_schedule_lag_ms"]:
                    return _record(entry, decision, "scheduler_late")
                authorized.add(entry["request_id"])
                record.update(dispatched=True, submission_attempted=None, outcome="unknown",
                              dispatch_offset_ms=now())
            # Authorization linearizes against the latch; the lock never spans model work.
            receipt = runner(_single(manifest, entry["task"]), request_id=entry["request_id"],
                             chat_id=entry["chat_id"], native_sessions_dir=native_sessions_dir)
            if not isinstance(receipt, dict) or any(receipt.get(k) != entry[k] for k in (
                    "request_id", "chat_id", "task_id", "task_sha256")):
                record["receipt"] = {"error": "runner_identity_mismatch", "admission_safe": False}
                latch("runner_identity_mismatch")
            else:
                record.update(receipt=receipt, submission_attempted=receipt.get("submission_attempted"),
                              outcome="completed" if receipt.get("admission_safe") is True else "unknown")
                if receipt.get("admission_safe") is not True:
                    latch("unresolved_stop")
        except BaseException:
            record["receipt"] = {"error": "worker_error", "admission_safe": False}
            if record["dispatched"]:
                latch("worker_error")
            else:
                record.update(refusal_reason="persistence_error")
                io_failed.set()
                latch("persistence_error")
        record["completion_offset_ms"] = now()
        if record["dispatched"]:
            record["arrival_to_completion_ms"] = record["completion_offset_ms"]-entry["scheduled_offset_ms"]
        return record

    def harvest():
        for future, entry in list(pending.items()):
            if not future.done():
                continue
            del pending[future]
            try:
                record = future.result()
            except BaseException:
                latch("worker_error")
                record = _record(entry, now())
                record.update(dispatched=entry["request_id"] in authorized, submission_attempted=None,
                              outcome="unknown", receipt={"error": "worker_error", "admission_safe": False})
            save(record)

    pool = ThreadPoolExecutor(max_workers=manifest["concurrency"])
    try:
        for entry in plan:
            while now() < entry["scheduled_offset_ms"] and not stopped.is_set():
                harvest()
                stopped.wait(min(.05, max(0, (entry["scheduled_offset_ms"]-now())/1000)))
            harvest()
            decision = now()
            if not admission():
                save(_record(entry, decision, state["reason"], offered(entry)))
            elif now()-entry["scheduled_offset_ms"] > config["max_schedule_lag_ms"]:
                save(_record(entry, decision, "scheduler_late"))
            elif len(pending) >= manifest["concurrency"]:
                save(_record(entry, decision, "client_busy"))
            else:
                pending[pool.submit(worker, entry, decision)] = entry
    except KeyboardInterrupt:
        interrupted = True
        latch("interrupted")
    except Exception:
        fatal = True
        latch("coordinator_error")
    finally:
        # Latch before draining on interrupt/error; never call Future.cancel and
        # pretend that a live request stopped. Do not abandon any worker receipt.
        while pending:
            try:
                harvest()
                if pending:
                    time.sleep(.01)
            except KeyboardInterrupt:
                interrupted = True
                latch("interrupted")
            except RunError:
                fatal = True
        pool.shutdown(wait=True)
        for entry in plan:
            if entry["request_id"] not in records:
                try:
                    save(_record(entry, now(), state["reason"] or "coordinator_error", offered(entry)))
                except RunError:
                    fatal = True
    elapsed = time.perf_counter()-start
    summary = _summary(config, plan, list(records.values()), elapsed, state, interrupted, sources)
    summary["evidence_complete"] = not (fatal or io_failed.is_set())
    try:
        _persist(directory/"summary.json", summary)
    except Exception:
        raise RunError("evidence_incomplete") from None
    if interrupted:
        raise KeyboardInterrupt
    if not summary["evidence_complete"]:
        raise RunError("evidence_incomplete")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--load-config", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--native-sessions-dir")
    args = parser.parse_args(argv)
    try:
        summary = run(pixel_adapter.strict_json(Path(args.manifest).read_text(encoding="utf-8")),
                      pixel_adapter.strict_json(Path(args.load_config).read_text(encoding="utf-8")), args.out,
                      native_sessions_dir=args.native_sessions_dir)
    except KeyboardInterrupt:
        return 130
    except Exception:
        print("agent_load_failed; inspect persisted identities and evidence", file=sys.stderr)
        return 2
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["successful_tasks"] == summary["planned"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
