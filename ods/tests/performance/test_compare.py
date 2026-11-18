import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compare  # noqa: E402


def _sha(obj):
    return compare.sha256_text(compare.canonical_json(obj))


def _task(tid):
    return {"id": tid, "messages": [{"role": "user", "content": "p-" + tid}], "validator": {"kind": "exact", "expected": "e-" + tid}}


def _manifest(tasks, reps=3, model="m1", slo=1000, sampling=None, prov=None):
    if sampling is None:
        sampling = {"temperature": 0.0, "top_p": 1.0}
    if prov is None:
        prov = {"engine": "e1", "model_revision": "r1", "tokenizer_revision": "t1", "quantization": "q1"}
    prov = {**{k: "fixture" for k in compare.probe.PROVENANCE_KEYS}, **prov}
    return {
        "schema_version": 1, "run_label": "fixture", "target": "test", "timeout_s": 60,
        "model": model,
        "layer": "engine",
        "endpoint": "http://127.0.0.1:9/v1/chat/completions",
        "cache_condition": "uncontrolled",
        "concurrency": 1,
        "repetitions": reps,
        "slo_ms": slo,
        "tasks": tasks,
        "provenance": prov,
        "sampling": sampling,
    }


def _records(manifest, latencies, quality=None, censored=None, transport=None):
    """latencies: dict (tid,rep)->ms or None. quality: dict->bool default True."""
    tshas = {t["id"]: _sha(t) for t in manifest["tasks"]}
    recs = []
    for t in manifest["tasks"]:
        for r in range(manifest["repetitions"]):
            key = (t["id"], r)
            q = True if quality is None else quality.get(key, True)
            c = False if censored is None else censored.get(key, False)
            tr = not c if transport is None else transport.get(key, not c)
            recs.append({
                "request_id": f"{t['id']}-{r}",
                "task_id": t["id"],
                "repetition": r,
                "quality_pass": q,
                "latency_ms": latencies.get(key),
                "censored": c,
                "transport_complete": tr,
                "task_sha256": tshas[t["id"]],
                "error_kind": None if q else ("transport" if c else "quality"),
                "error": None if q else "bad",
            })
    return recs


def _summary(manifest, records, quality_pass=True, censored=False, transport_failures=0, quality_failures=0):
    return {
        "scheduled": len(manifest["tasks"]) * manifest["repetitions"],
        "dispatched": len(records),
        "records": len(records),
        "interrupted": False,
        "quality_pass": sum(r["quality_pass"] for r in records),
        "transport_complete": sum(r["transport_complete"] for r in records),
        "censored": sum(r["censored"] for r in records),
        "transport_failures": sum(r["error_kind"] == "transport" for r in records),
        "quality_failures": sum(r["error_kind"] == "quality" for r in records),
        "identity_failures": 0,
        "elapsed_s": 1.0,
        "validated_tasks_per_s": 1.0,
        "slo_goodput_tasks_per_s": 1.0,
    }


def _write_run(root, manifest, records, summary):
    d = Path(root)
    d.mkdir(parents=True, exist_ok=True)
    wrapper = {"manifest": manifest, "manifest_sha256": _sha(manifest)}
    (d / "manifest.json").write_text(json.dumps(wrapper), encoding="utf-8")
    with open(d / "requests.jsonl", "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")
    (d / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    return d


def _build(root, name, manifest, latencies, **kw):
    recs = _records(manifest, latencies, quality=kw.pop("quality", None), censored=kw.pop("censored", None), transport=kw.pop("transport", None))
    summ = _summary(manifest, recs, **kw)
    return _write_run(Path(root) / name, manifest, recs, summ)


class CompareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.tasks = [_task("a"), _task("b")]
        self.manifest = _manifest(self.tasks)
        self.lat = {(t["id"], r): 100.0 for t in self.tasks for r in range(3)}

    def tearDown(self):
        self.tmp.cleanup()

    def test_identical_inconclusive_no_regression(self):
        b = _build(self.root, "b", self.manifest, self.lat)
        c = _build(self.root, "c", self.manifest, self.lat)
        rep = compare.compare(b, c)
        self.assertEqual(rep["qualification"], "inconclusive")
        self.assertFalse(rep["regression_veto"])
        self.assertEqual(rep["verdict"], "no_observed_regression_for_paired_tested_cases")
        self.assertEqual(rep["paired"]["quality_transitions"]["pass_pass"], 6)

    def test_faster_wrong_candidate_regression_veto(self):
        b = _build(self.root, "b", self.manifest, self.lat)
        fast = {(t["id"], r): 10.0 for t in self.tasks for r in range(3)}
        q = {("a", 0): False}
        c = _build(self.root, "c", self.manifest, fast, quality=q, quality_failures=1)
        rep = compare.compare(b, c)
        self.assertTrue(rep["regression_veto"])
        self.assertEqual(rep["verdict"], "fixture_regression_detected")
        self.assertEqual(rep["paired"]["quality_transitions"]["pass_fail"], 1)

    def test_timeout_candidate_in_failed_pairs(self):
        b = _build(self.root, "b", self.manifest, self.lat)
        lat = dict(self.lat)
        lat[("a", 1)] = None
        q = {("a", 1): False}
        cen = {("a", 1): True}
        c = _build(self.root, "c", self.manifest, lat, quality=q, censored=cen, quality_failures=1)
        rep = compare.compare(b, c)
        self.assertTrue(rep["regression_veto"])
        failed = rep["paired"]["failed_pairs"]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["task_id"], "a")
        self.assertEqual(failed[0]["repetition"], 1)
        self.assertTrue(failed[0]["candidate_censored"])

    def test_earlier_fail_later_pass_medians_use_later_pairs(self):
        b = _build(self.root, "b", self.manifest, self.lat)
        lat = dict(self.lat)
        lat[("a", 0)] = 50.0
        lat[("a", 1)] = 60.0
        lat[("a", 2)] = 70.0
        q = {("a", 0): False}
        c = _build(self.root, "c", self.manifest, lat, quality=q, quality_failures=1)
        rep = compare.compare(b, c)
        pt = rep["paired"]["per_task"]["a"]
        self.assertEqual(pt["both_quality_pass"], 2)
        self.assertEqual(pt["median_paired_diff_ms"], 35.0)
        self.assertEqual(pt["median_baseline_ms"], 100.0)
        self.assertEqual(pt["median_candidate_ms"], 65.0)

    def test_duplicate_missing_and_tampered_invalid(self):
        b = _build(self.root, "b", self.manifest, self.lat)
        c = _build(self.root, "c", self.manifest, self.lat)
        # duplicate
        with open(c / "requests.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(_records(self.manifest, self.lat)[0]) + "\n")
        rep = compare.compare(b, c)
        self.assertEqual(rep["qualification"], "invalid_comparison")
        # missing
        c2 = _build(self.root, "c2", self.manifest, self.lat)
        lines = (c2 / "requests.jsonl").read_text(encoding="utf-8").splitlines()
        (c2 / "requests.jsonl").write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
        rep = compare.compare(b, c2)
        self.assertEqual(rep["qualification"], "invalid_comparison")
        # tampered manifest
        c3 = _build(self.root, "c3", self.manifest, self.lat)
        w = json.loads((c3 / "manifest.json").read_text(encoding="utf-8"))
        w["manifest"]["model"] = "tampered"
        (c3 / "manifest.json").write_text(json.dumps(w), encoding="utf-8")
        rep = compare.compare(b, c3)
        self.assertEqual(rep["qualification"], "invalid_comparison")

    def test_changed_fields_invalid(self):
        b = _build(self.root, "b", self.manifest, self.lat)
        variants = [
            _manifest(self.tasks, model="m2"),
            _manifest(self.tasks, sampling={"temperature": 0.5, "top_p": 1.0}),
            _manifest(self.tasks, slo=2000),
            _manifest(self.tasks, prov={"engine": "e1", "model_revision": "r1", "tokenizer_revision": "t1", "quantization": "q2"}),
        ]
        for i, m in enumerate(variants):
            c = _build(self.root, f"c{i}", m, self.lat)
            rep = compare.compare(b, c)
            self.assertEqual(rep["qualification"], "invalid_comparison", msg=str(i))

    def test_unknown_revision_preserves_pilot_inconclusive(self):
        prov = {"engine": "e1", "model_revision": None, "tokenizer_revision": None, "quantization": "q1"}
        m = _manifest(self.tasks, prov=prov)
        b = _build(self.root, "b", m, self.lat)
        c = _build(self.root, "c", m, self.lat)
        rep = compare.compare(b, c)
        self.assertEqual(rep["qualification"], "inconclusive")
        self.assertIn("paired", rep)
        self.assertIn("verdict", rep)
        self.assertFalse(rep["regression_veto"])

    def test_quality_pass_with_censored_invalid_ordinary_censored_valid(self):
        b = _build(self.root, "b", self.manifest, self.lat)
        # quality_pass true but censored true -> invalid
        lat = dict(self.lat)
        lat[("a", 0)] = None
        cen = {("a", 0): True}
        c = _build(self.root, "c", self.manifest, lat, censored=cen)
        rep = compare.compare(b, c)
        self.assertEqual(rep["qualification"], "invalid_comparison")
        # ordinary censored failure valid
        q = {("a", 0): False}
        c2 = _build(self.root, "c2", self.manifest, lat, quality=q, censored=cen, quality_failures=1)
        rep = compare.compare(b, c2)
        self.assertNotEqual(rep["qualification"], "invalid_comparison")
        self.assertTrue(rep["regression_veto"])


if __name__ == "__main__":
    unittest.main()
