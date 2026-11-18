"""Deterministic paired pilot comparison for ODS performance runs.

Compares two run directories produced by probe.py. This module NEVER
qualifies a population or production profile: it only reports paired
pilot evidence for the tested synthetic cases. Stdlib only.

CLI: compare.py --baseline DIR --candidate DIR --out NEWFILE
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any, Optional

import probe

SCHEMA_VERSION = 1
TREATMENT_FIELDS = {"engine_version", "backend", "endpoint", "concurrency", "cache_condition", "ods_commit"}
IMMUTABLE_PROVENANCE = ("engine", "model_revision", "tokenizer_revision", "quantization")


class CompareError(ValueError):
    pass


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-finite JSON constant {name}")


def _unique_object(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ValueError("duplicate JSON key")
        out[k] = v
    return out


def _load_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f, parse_constant=_reject_constant, object_pairs_hook=_unique_object)


def _is_finite_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _is_strict_bool(v: Any) -> bool:
    return isinstance(v, bool)


def _load_run(run_dir: Path) -> dict:
    """Load and structurally validate one run directory. Raises CompareError."""
    if not run_dir.is_dir():
        raise CompareError(f"run directory missing: {run_dir}")
    manifest_path = run_dir / "manifest.json"
    requests_path = run_dir / "requests.jsonl"
    summary_path = run_dir / "summary.json"
    for p in (manifest_path, requests_path, summary_path):
        if not p.is_file():
            raise CompareError(f"missing artifact: {p.name}")

    try:
        wrapper = _load_json(manifest_path)
    except Exception as exc:
        raise CompareError(f"manifest.json unreadable: {exc}") from exc
    if not isinstance(wrapper, dict) or set(wrapper.keys()) != {"manifest", "manifest_sha256"}:
        raise CompareError("manifest.json must have exactly manifest and manifest_sha256")
    manifest = wrapper["manifest"]
    stored_sha = wrapper["manifest_sha256"]
    if not isinstance(stored_sha, str):
        raise CompareError("manifest_sha256 must be string")
    try:
        actual_sha = sha256_text(canonical_json(manifest))
    except (ValueError, TypeError) as exc:
        raise CompareError(f"manifest not canonicalizable: {exc}") from exc
    if actual_sha != stored_sha:
        raise CompareError("manifest_sha256 mismatch (tampered manifest)")

    try:
        probe.validate_manifest(manifest)
    except (ValueError, TypeError, KeyError) as exc:
        raise CompareError("invalid probe manifest") from exc
    for key in ("model", "layer", "endpoint", "cache_condition", "concurrency", "repetitions", "slo_ms", "tasks", "provenance", "sampling"):
        if key not in manifest:
            raise CompareError(f"manifest missing field {key}")
    if not isinstance(manifest["tasks"], list) or not manifest["tasks"]:
        raise CompareError("manifest.tasks must be non-empty list")
    if not isinstance(manifest["provenance"], dict):
        raise CompareError("manifest.provenance must be object")

    try:
        summary = _load_json(summary_path)
    except Exception as exc:
        raise CompareError(f"summary.json unreadable: {exc}") from exc
    if not isinstance(summary, dict):
        raise CompareError("summary.json must be object")

    records = []
    try:
        with open(requests_path, "r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line, parse_constant=_reject_constant, object_pairs_hook=_unique_object)
                except Exception as exc:
                    raise CompareError(f"requests.jsonl line {lineno} invalid: {exc}") from exc
                if not isinstance(rec, dict):
                    raise CompareError(f"requests.jsonl line {lineno} not object")
                records.append(rec)
    except CompareError:
        raise
    except Exception as exc:
        raise CompareError(f"requests.jsonl unreadable: {exc}") from exc

    return {"manifest": manifest, "summary": summary, "records": records, "manifest_sha256": stored_sha}


def _validate_records(run: dict, label: str) -> list:
    """Validate record fields, uniqueness, and totals. Returns records."""
    manifest = run["manifest"]
    summary = run["summary"]
    records = run["records"]
    tasks = manifest["tasks"]
    reps = manifest["repetitions"]
    if not isinstance(reps, int) or isinstance(reps, bool) or reps < 1:
        raise CompareError(f"{label}: repetitions invalid")
    task_ids = []
    for t in tasks:
        if not isinstance(t, dict) or "id" not in t:
            raise CompareError(f"{label}: task missing id")
        task_ids.append(t["id"])
    if len(set(task_ids)) != len(task_ids):
        raise CompareError(f"{label}: duplicate task ids in manifest")

    expected_keys = {(tid, r) for tid in task_ids for r in range(reps)}
    seen = set()
    for rec in records:
        for field in ("task_id", "repetition", "quality_pass", "latency_ms", "censored", "transport_complete", "task_sha256"):
            if field not in rec:
                raise CompareError(f"{label}: record missing field {field}")
        if not isinstance(rec["task_id"], str):
            raise CompareError(f"{label}: task_id must be string")
        if not isinstance(rec["repetition"], int) or isinstance(rec["repetition"], bool):
            raise CompareError(f"{label}: repetition must be int")
        if not _is_strict_bool(rec["quality_pass"]):
            raise CompareError(f"{label}: quality_pass must be strict bool")
        if not _is_strict_bool(rec["censored"]):
            raise CompareError(f"{label}: censored must be strict bool")
        if not _is_strict_bool(rec["transport_complete"]):
            raise CompareError(f"{label}: transport_complete must be strict bool")
        lat = rec["latency_ms"]
        if lat is not None:
            if not _is_finite_number(lat) or lat < 0:
                raise CompareError(f"{label}: latency_ms must be finite nonnegative or null")
        if rec["quality_pass"] and (rec["censored"] or not rec["transport_complete"] or lat is None or rec.get("error") is not None):
            raise CompareError(f"{label}: contradictory successful record")
        if rec["censored"] and rec["transport_complete"]:
            raise CompareError(f"{label}: censored record cannot be complete")
        if rec["transport_complete"] and lat is None:
            raise CompareError(f"{label}: completed record has no latency")
        key = (rec["task_id"], rec["repetition"])
        if key in seen:
            raise CompareError(f"{label}: duplicate record {key}")
        seen.add(key)

    missing = expected_keys - seen
    extra = seen - expected_keys
    if missing:
        raise CompareError(f"{label}: missing records: {sorted(missing)[:5]}")
    if extra:
        raise CompareError(f"{label}: unexpected records: {sorted(extra)[:5]}")

    scheduled = len(task_ids) * reps
    if summary.get("scheduled") != scheduled:
        raise CompareError(f"{label}: summary.scheduled {summary.get('scheduled')} != {scheduled}")
    if summary.get("records") != len(records):
        raise CompareError(f"{label}: summary.records {summary.get('records')} != {len(records)}")
    if summary.get("dispatched") != len(records):
        raise CompareError(f"{label}: summary.dispatched {summary.get('dispatched')} != {len(records)}")
    if summary.get("interrupted") is not False:
        raise CompareError(f"{label}: run was interrupted or status is unknown")
    counts = {
        "quality_pass": sum(r["quality_pass"] for r in records),
        "transport_complete": sum(r["transport_complete"] for r in records),
        "censored": sum(r["censored"] for r in records),
        "transport_failures": sum(r.get("error_kind") == "transport" for r in records),
        "quality_failures": sum(r.get("error_kind") == "quality" for r in records),
        "identity_failures": sum(r.get("error_kind") == "identity" for r in records),
    }
    for name, count in counts.items():
        if type(summary.get(name)) is not int or summary[name] != count:
            raise CompareError(f"{label}: inconsistent summary count: {name}")
    if not _is_finite_number(summary.get("elapsed_s")) or summary["elapsed_s"] <= 0:
        raise CompareError(f"{label}: invalid elapsed time")
    return records


def _task_sha_map(manifest: dict) -> dict:
    out = {}
    for t in manifest["tasks"]:
        out[t["id"]] = sha256_text(canonical_json(t))
    return out


def _check_tasksets(base: dict, cand: dict) -> list:
    reasons = []
    bmap = _task_sha_map(base["manifest"])
    cmap = _task_sha_map(cand["manifest"])
    if set(bmap) != set(cmap):
        reasons.append("taskset_ids_differ")
    for tid in sorted(set(bmap) & set(cmap)):
        if bmap[tid] != cmap[tid]:
            reasons.append(f"task_sha_mismatch:{tid}")
    return reasons


def _check_records_task_sha(run: dict, label: str) -> list:
    reasons = []
    expected = _task_sha_map(run["manifest"])
    for rec in run["records"]:
        tid = rec["task_id"]
        if tid in expected and rec.get("task_sha256") != expected[tid]:
            reasons.append(f"{label}:record_task_sha_mismatch:{tid}")
    return reasons


def _manifest_diff(base: dict, cand: dict) -> dict:
    diffs = {}
    keys = set(base.keys()) | set(cand.keys())
    for k in sorted(keys):
        bv = base.get(k)
        cv = cand.get(k)
        if k == "provenance" and isinstance(bv, dict) and isinstance(cv, dict):
            for pk in sorted(set(bv) | set(cv)):
                if bv.get(pk) != cv.get(pk):
                    diffs[f"provenance.{pk}"] = {"baseline": bv.get(pk), "candidate": cv.get(pk)}
        elif bv != cv:
            diffs[k] = {"baseline": bv, "candidate": cv}
    return diffs


def _classify(base: dict, cand: dict, allow_route_change: bool, treatment_field: Optional[str]) -> dict:
    bm, cm = base["manifest"], cand["manifest"]
    differences = _manifest_diff(bm, cm)
    reasons, unknowns = [], []
    allowed = {"run_label", "api_key_env", "schedule_seed"}
    if allow_route_change:
        allowed.update({"endpoint", "layer"})
    if treatment_field is not None:
        if treatment_field not in TREATMENT_FIELDS:
            reasons.append("invalid_treatment_field")
        else:
            field = "provenance." + treatment_field if treatment_field in {"engine_version", "backend", "ods_commit"} else treatment_field
            allowed.add(field)
            if field not in differences:
                reasons.append("declared_treatment_did_not_change")
    # Task order may differ, but their contents are independently hash checked.
    allowed.add("tasks")
    for field in differences:
        if field not in allowed:
            reasons.append("undeclared_or_incompatible_change:" + field)
    for label, manifest in (("baseline", bm), ("candidate", cm)):
        for field in ("engine", "engine_version", "backend", "model_revision", "tokenizer_revision", "quantization", "ods_commit"):
            if not manifest["provenance"].get(field):
                unknowns.append(label + ":unknown_provenance:" + field)
    return {"reasons": reasons, "unknowns": unknowns}


def _paired_cases(base: dict, cand: dict) -> dict:
    """Build paired case map keyed by (task_id, repetition)."""
    bmap = {(r["task_id"], r["repetition"]): r for r in base["records"]}
    cmap = {(r["task_id"], r["repetition"]): r for r in cand["records"]}
    return bmap, cmap


def _median(values: list) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2.0


def _analyze_pairs(base: dict, cand: dict) -> dict:
    bmap, cmap = _paired_cases(base, cand)
    keys = sorted(set(bmap) & set(cmap))
    transitions = {"pass_pass": 0, "pass_fail": 0, "fail_pass": 0, "fail_fail": 0}
    paired_latency = []
    per_task = {}
    failed_pairs = []
    for key in keys:
        b = bmap[key]
        c = cmap[key]
        bq = b["quality_pass"]
        cq = c["quality_pass"]
        if bq and cq:
            transitions["pass_pass"] += 1
        elif bq and not cq:
            transitions["pass_fail"] += 1
        elif not bq and cq:
            transitions["fail_pass"] += 1
        else:
            transitions["fail_fail"] += 1
        tid = key[0]
        per_task.setdefault(tid, {"paired": 0, "both_pass": 0, "diffs_ms": [], "pct_improvements": []})
        per_task[tid]["paired"] += 1
        if bq and cq and b.get("latency_ms") is not None and c.get("latency_ms") is not None:
            bl = b["latency_ms"]
            cl = c["latency_ms"]
            diff = bl - cl
            per_task[tid]["both_pass"] += 1
            per_task[tid]["diffs_ms"].append(diff)
            if bl > 0:
                pct = (bl - cl) / bl * 100.0
                per_task[tid]["pct_improvements"].append(pct)
            paired_latency.append({"task_id": tid, "repetition": key[1], "baseline_ms": bl, "candidate_ms": cl, "diff_ms": diff})
        if not (bq and cq):
            failed_pairs.append({
                "task_id": tid,
                "repetition": key[1],
                "baseline_quality_pass": bq,
                "candidate_quality_pass": cq,
                "baseline_error": b.get("error"),
                "candidate_error": c.get("error"),
                "baseline_censored": b.get("censored"),
                "candidate_censored": c.get("censored"),
            })

    task_summary = {}
    for tid, data in per_task.items():
        diffs = data["diffs_ms"]
        pcts = data["pct_improvements"]
        task_summary[tid] = {
            "paired": data["paired"],
            "both_quality_pass": data["both_pass"],
            "median_baseline_ms": None,
            "median_candidate_ms": None,
            "median_paired_diff_ms": round(_median(diffs), 3) if diffs else None,
            "median_paired_pct_improvement": round(_median(pcts), 3) if pcts else None,
        }
        if diffs:
            bls = [bmap[(tid, r)]["latency_ms"] for t, r in keys if t == tid and bmap[(tid, r)]["quality_pass"] and cmap[(tid, r)]["quality_pass"]]
            cls = [cmap[(tid, r)]["latency_ms"] for t, r in keys if t == tid and bmap[(tid, r)]["quality_pass"] and cmap[(tid, r)]["quality_pass"]]
            task_summary[tid]["median_baseline_ms"] = round(_median(bls), 3) if bls else None
            task_summary[tid]["median_candidate_ms"] = round(_median(cls), 3) if cls else None

    return {
        "paired_cases": len(keys),
        "quality_transitions": transitions,
        "paired_latency_cases": len(paired_latency),
        "per_task": task_summary,
        "failed_pairs": failed_pairs,
    }


def _slo_goodput(run: dict) -> dict:
    manifest = run["manifest"]
    summary = run["summary"]
    slo = manifest["slo_ms"]
    good = 0
    for rec in run["records"]:
        if rec["quality_pass"] and rec.get("latency_ms") is not None and rec["latency_ms"] <= slo:
            good += 1
    return {
        "slo_ms": slo,
        "slo_goodput_count": good,
        "slo_goodput_tasks_per_s": good / summary["elapsed_s"],
        "validated_tasks_per_s": sum(r["quality_pass"] for r in run["records"]) / summary["elapsed_s"],
    }


def compare(baseline_dir: Path, candidate_dir: Path, allow_route_change: bool = False, treatment_field: Optional[str] = None) -> dict:
    """Run deterministic paired comparison. Returns report dict."""
    reasons = []
    try:
        base = _load_run(baseline_dir)
    except CompareError as exc:
        return _invalid_report([f"baseline:{exc}"])
    try:
        cand = _load_run(candidate_dir)
    except CompareError as exc:
        return _invalid_report([f"candidate:{exc}"])

    try:
        _validate_records(base, "baseline")
    except CompareError as exc:
        reasons.append(str(exc))
    try:
        _validate_records(cand, "candidate")
    except CompareError as exc:
        reasons.append(str(exc))

    if reasons:
        return _invalid_report(reasons)

    reasons.extend(_check_records_task_sha(base, "baseline"))
    reasons.extend(_check_records_task_sha(cand, "candidate"))
    reasons.extend(_check_tasksets(base, cand))

    classification = _classify(base, cand, allow_route_change, treatment_field)
    reasons.extend(classification["reasons"])

    if reasons:
        return _invalid_report(reasons)

    analysis = _analyze_pairs(base, cand)
    transitions = analysis["quality_transitions"]
    regression = transitions["pass_fail"] > 0
    if regression:
        verdict = "fixture_regression_detected"
    else:
        verdict = "no_observed_regression_for_paired_tested_cases"

    manifest_diff = _manifest_diff(base["manifest"], cand["manifest"])

    report = {
        "schema_version": SCHEMA_VERSION,
        "qualification": "inconclusive",
        "qualification_reasons": [
            "synthetic_pilot_only",
            "no_large_agent_quality_suite",
            "no_host_observability",
            "no_confidence_interval",
            "cache_condition_uncontrolled",
        ] + classification["unknowns"],
        "verdict": verdict,
        "regression_veto": regression,
        "baseline_manifest_sha256": base["manifest_sha256"],
        "candidate_manifest_sha256": cand["manifest_sha256"],
        "allow_route_change": allow_route_change,
        "treatment_field": treatment_field,
        "manifest_diff": manifest_diff,
        "paired": analysis,
        "baseline_slo": _slo_goodput(base),
        "candidate_slo": _slo_goodput(cand),
        "sample_limit": {
            "independent_distinct_cases": len(base["manifest"]["tasks"]),
            "repetition_count": base["manifest"]["repetitions"],
            "note": "repetitions are not independent quality coverage; no CI computed",
        },
        "metrics_semantics": {
            "paired_latency": "only cases where both baseline and candidate quality_pass; unpaired excluded",
            "median_paired_pct_improvement": "median of per-case (baseline-candidate)/baseline*100; positive means candidate faster",
            "quality_transitions": "pass_pass/pass_fail/fail_pass/fail_fail over matched (task_id,repetition)",
            "slo_goodput": "quality_pass AND latency<=slo, reported separately from summary",
            "no_mean_subtraction": "unpaired means are never subtracted",
        },
    }
    report["report_sha256"] = sha256_text(canonical_json(report))
    return report


def _invalid_report(reasons: list) -> dict:
    report = {
        "schema_version": SCHEMA_VERSION,
        "qualification": "invalid_comparison",
        "reasons": reasons,
        "verdict": "invalid_comparison",
    }
    report["report_sha256"] = sha256_text(canonical_json(report))
    return report


def _atomic_write(path: Path, data: str) -> None:
    # Exclusive creation avoids overwriting an existing evidence file, including races.
    try:
        with path.open("x", encoding="utf8") as stream:
            stream.write(data)
    except OSError as exc:
        raise CompareError("cannot create new output file") from exc


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic paired pilot comparison for ODS performance runs")
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--allow-route-change", action="store_true")
    parser.add_argument("--treatment-field", default=None, choices=sorted(TREATMENT_FIELDS))
    args = parser.parse_args(argv)

    try:
        report = compare(Path(args.baseline), Path(args.candidate), args.allow_route_change, args.treatment_field)
    except CompareError as exc:
        report = _invalid_report([str(exc)])

    try:
        _atomic_write(Path(args.out), json.dumps(report, indent=2, sort_keys=True) + "\n")
    except CompareError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    compact = {
        "qualification": report.get("qualification"),
        "verdict": report.get("verdict"),
        "regression_veto": report.get("regression_veto"),
        "report_sha256": report.get("report_sha256"),
    }
    if report.get("qualification") == "invalid_comparison":
        compact["reasons"] = report.get("reasons")
        print(json.dumps(compact, sort_keys=True))
        return 2
    print(json.dumps(compact, sort_keys=True))
    return 1 if report.get("regression_veto") else 0


if __name__ == "__main__":
    sys.exit(main())
