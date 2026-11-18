import json
import os
import sys
import subprocess
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pixel_open_loop as pol
import probe


BASE_URL = "http://127.0.0.1:9"


def _task(tid="t1", expected="ok", required_tools=None):
    task = {
        "id": tid,
        "messages": [{"role": "user", "content": "hello " + tid}],
        "validator": {"kind": "exact", "expected": expected},
    }
    if required_tools is not None:
        task["required_tools"] = required_tools
    return task


def _manifest(tasks=None, concurrency=1, repetitions=1, seed=0):
    if tasks is None:
        tasks = [_task()]
    return {
        "schema_version": 1,
        "base_url": BASE_URL,
        "api_key_env": "TEST_KEY",
        "timeout_s": 1,
        "recovery_timeout_s": 1,
        "tasks": tasks,
        "concurrency": concurrency,
        "repetitions": repetitions,
        "schedule_seed": seed,
    }


def _load_config(rate=20.0, lag=1000, **extra):
    cfg = {"rate_rps": rate, "max_schedule_lag_ms": lag}
    cfg.update(extra)
    return cfg


def _receipt(manifest, *, chat_id, request_id, success=True, quality=True,
             admission_safe=True, censored=False, submission_attempted=True,
             result_state="complete", latency_ms=1.0, task_id=None, native_sessions_dir=None):
    task = manifest["task"]
    return {
        "request_id": request_id,
        "chat_id": chat_id,
        "task_id": task_id if task_id is not None else task["id"],
        "task_sha256": probe.sha256_text(probe.canonical_json(task)),
        "submission_attempted": submission_attempted,
        "censored": censored,
        "admission_safe": admission_safe,
        "success": success,
        "answer_quality_pass": quality,
        "quality_pass": quality,
        "result_state": result_state,
        "task_latency_ms": latency_ms,
    }


class _Runner:
    def __init__(self, behavior=None):
        self.calls = []
        self.lock = threading.Lock()
        self.behavior = behavior or (lambda m, **kw: _receipt(m, **kw))

    def __call__(self, manifest, *, chat_id, request_id, native_sessions_dir=None):
        with self.lock:
            self.calls.append({"manifest": manifest, "chat_id": chat_id,
                               "request_id": request_id})
        return self.behavior(manifest, chat_id=chat_id, request_id=request_id,
                             native_sessions_dir=native_sessions_dir)


class _Base(unittest.TestCase):
    def setUp(self):
        self._env = mock.patch.dict(os.environ, {"TEST_KEY": "synthetic"})
        self._env.start()
        self.addCleanup(self._env.stop)
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def out(self, name="out"):
        return self.root / name

    def read_json(self, path):
        return json.loads(Path(path).read_text())

    def read_jsonl(self, path):
        return [json.loads(line) for line in Path(path).read_text().splitlines() if line]


class TestValidation(_Base):
    def test_missing_credential_rejects_before_output(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(Exception):
                pol.run(_manifest(), _load_config(), self.out(), request_runner=_Runner())
        self.assertFalse(self.out().exists())

    def test_empty_credential_rejects_before_output(self):
        with mock.patch.dict(os.environ, {"TEST_KEY": ""}):
            with self.assertRaises(Exception):
                pol.run(_manifest(), _load_config(), self.out(), request_runner=_Runner())
        self.assertFalse(self.out().exists())

    def test_bad_manifest_rejects_before_output(self):
        bad = _manifest()
        bad["concurrency"] = 0
        with self.assertRaises(Exception):
            pol.run(bad, _load_config(), self.out(), request_runner=_Runner())
        self.assertFalse(self.out().exists())

    def test_validate_manifest_returns_deepcopy(self):
        m = _manifest()
        out = pol.validate_manifest(m)
        out["tasks"][0]["id"] = "mutated"
        self.assertEqual(m["tasks"][0]["id"], "t1")

    def test_run_rejects_existing_child(self):
        self.out().mkdir()
        with self.assertRaises(Exception):
            pol.run(_manifest(), _load_config(), self.out(), request_runner=_Runner())


class TestPlanAndDispatch(_Base):
    def test_plan_written_before_first_call(self):
        seen = {}
        runner = _Runner()

        def behavior(m, **kw):
            plan = self.read_json(self.out() / "plan.json")
            intent = self.read_jsonl(self.out() / "dispatches.jsonl")
            seen["plan"] = plan[0]["request_id"] == kw["request_id"]
            seen["dispatches"] = intent[0]["chat_id"] == kw["chat_id"]
            return _receipt(m, **kw)

        runner.behavior = behavior
        pol.run(_manifest(), _load_config(), self.out(), request_runner=runner)
        self.assertTrue(seen["plan"])
        self.assertTrue(seen["dispatches"])

    def test_plan_has_no_raw_prompts(self):
        pol.run(_manifest(), _load_config(), self.out(), request_runner=_Runner())
        text = (self.out() / "plan.json").read_text()
        self.assertNotIn("hello t1", text)
        self.assertNotIn("synthetic", text)
        self.assertNotIn(BASE_URL, text)

    def test_requests_one_row_per_planned(self):
        pol.run(_manifest(repetitions=3), _load_config(), self.out(),
                request_runner=_Runner())
        rows = self.read_jsonl((self.out() / "requests.jsonl"))
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertIn("receipt", row)
            self.assertIn("offered", row)
            self.assertIn("dispatched", row)

    def test_summary_keys_present(self):
        pol.run(_manifest(), _load_config(), self.out(), request_runner=_Runner())
        summary = self.read_json((self.out() / "summary.json"))
        for key in ("planned", "total_records", "offered", "dispatched",
                    "submission_attempted", "successful_tasks",
                    "answer_quality_pass", "quality_failures",
                    "refusals_by_reason", "unresolved_tasks", "censored",
                    "actual_duration_s", "offered_arrival_span_s",
                    "planned_arrival_span_s", "success_fraction",
                    "goodput_valid_tasks_per_s",
                    "successful_arrival_to_completion_p50_ms",
                    "successful_arrival_to_completion_p95_ms",
                    "admission_stopped", "stop_reason"):
            self.assertIn(key, summary)


class TestSuccessAndQuality(_Base):
    def test_all_success(self):
        pol.run(_manifest(repetitions=2), _load_config(), self.out(),
                request_runner=_Runner())
        summary = self.read_json((self.out() / "summary.json"))
        self.assertEqual(summary["planned"], 2)
        self.assertEqual(summary["successful_tasks"], 2)
        self.assertEqual(summary["unresolved_tasks"], 0)

    def test_quality_fail_does_not_stop_later(self):
        def behavior(m, **kw):
            return _receipt(m, success=False, quality=False, **kw)
        runner = _Runner(behavior)
        pol.run(_manifest(repetitions=3), _load_config(), self.out(),
                request_runner=runner)
        self.assertEqual(len(runner.calls), 3)
        summary = self.read_json((self.out() / "summary.json"))
        self.assertEqual(summary["quality_failures"], 3)
        self.assertFalse(summary["admission_stopped"])

    def test_unsafe_stops_future(self):
        calls = {"n": 0}

        def behavior(m, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return _receipt(m, success=False, quality=False,
                                admission_safe=False, result_state="unknown", **kw)
            return _receipt(m, **kw)

        runner = _Runner(behavior)
        pol.run(_manifest(repetitions=5), _load_config(rate=100.0), self.out(),
                request_runner=runner)
        self.assertEqual(len(runner.calls), 1)
        summary = self.read_json((self.out() / "summary.json"))
        self.assertTrue(summary["admission_stopped"])
        self.assertGreaterEqual(summary["unresolved_tasks"], 1)

    def test_runner_exception_sanitized(self):
        def behavior(m, **kw):
            raise RuntimeError("secret prompt hello t1")
        runner = _Runner(behavior)
        pol.run(_manifest(), _load_config(), self.out(), request_runner=runner)
        self.assertEqual(len(runner.calls), 1)
        text = (self.out() / "requests.jsonl").read_text()
        self.assertNotIn("secret prompt", text)
        self.assertNotIn("hello t1", text)
        rows = self.read_jsonl((self.out() / "requests.jsonl"))
        self.assertIsNone(rows[0]["submission_attempted"])

    def test_task_identity_mismatch_stops(self):
        def behavior(m, **kw):
            return _receipt(m, task_id="other", **kw)
        runner = _Runner(behavior)
        pol.run(_manifest(repetitions=3), _load_config(), self.out(),
                request_runner=runner)
        self.assertEqual(len(runner.calls), 1)
        summary = self.read_json((self.out() / "summary.json"))
        self.assertTrue(summary["admission_stopped"])

    def test_submission_false_not_counted(self):
        def behavior(m, **kw):
            return _receipt(m, submission_attempted=False, success=False,
                            quality=False, result_state="unknown", **kw)
        pol.run(_manifest(), _load_config(), self.out(), request_runner=_Runner(behavior))
        summary = self.read_json((self.out() / "summary.json"))
        self.assertEqual(summary["submission_attempted"], 0)

    def test_censored_excluded_from_latency(self):
        def behavior(m, **kw):
            return _receipt(m, censored=True, success=False, quality=False,
                            result_state="unknown", latency_ms=999.0, **kw)
        pol.run(_manifest(), _load_config(), self.out(), request_runner=_Runner(behavior))
        summary = self.read_json((self.out() / "summary.json"))
        self.assertIsNone(summary["successful_arrival_to_completion_p50_ms"])
        self.assertEqual(summary["censored"], 1)

    def test_p95_null_with_few_samples(self):
        pol.run(_manifest(), _load_config(), self.out(), request_runner=_Runner())
        summary = self.read_json((self.out() / "summary.json"))
        self.assertIsNone(summary["successful_arrival_to_completion_p95_ms"])


class TestConcurrency(_Base):
    def test_concurrency_one_refuses_busy(self):
        gate = threading.Event()
        started = threading.Event()

        def behavior(m, **kw):
            started.set()
            gate.wait(2.0)
            return _receipt(m, **kw)

        runner = _Runner(behavior)
        t = threading.Thread(target=lambda: pol.run(
            _manifest(repetitions=3), _load_config(rate=100.0), self.out(),
            request_runner=runner))
        t.start()
        started.wait(2.0)
        time.sleep(0.05)
        gate.set()
        t.join(5.0)
        self.assertFalse(t.is_alive())
        summary = self.read_json((self.out() / "summary.json"))
        self.assertIn("client_busy", summary["refusals_by_reason"])

    def test_concurrency_two_max_active(self):
        active = {"n": 0, "max": 0}
        lock = threading.Lock()
        gate = threading.Event()

        def behavior(m, **kw):
            with lock:
                active["n"] += 1
                active["max"] = max(active["max"], active["n"])
            gate.wait(2.0)
            with lock:
                active["n"] -= 1
            return _receipt(m, **kw)

        runner = _Runner(behavior)
        t = threading.Thread(target=lambda: pol.run(
            _manifest(repetitions=4, concurrency=2), _load_config(rate=100.0),
            self.out(), request_runner=runner))
        t.start()
        time.sleep(0.1)
        gate.set()
        t.join(5.0)
        self.assertFalse(t.is_alive())
        self.assertLessEqual(active["max"], 2)


class TestScheduling(_Base):
    def test_resource_refusal_and_error_terminate_without_dispatch(self):
        # Child isolation makes a deadlock a bounded failure, not a hung suite.
        for callback, reason in (("lambda: False", "resource_stop"), ("lambda: 1/0", "resource_error")):
            code = f'''from test_pixel_open_loop import *
from test_pixel_open_loop import _manifest, _load_config, _Runner
os.environ['TEST_KEY']='synthetic'
with TemporaryDirectory() as folder:
    runner=_Runner()
    result=pol.run(_manifest(repetitions=3),_load_config(rate=.01),Path(folder)/'out',
                   admission_allowed={callback},request_runner=runner)
    assert not runner.calls
    assert result['planned']==result['total_records']==3
    assert result['stop_reason']=={reason!r}
    assert result['offered']==1
    assert result['offered_arrival_span_s']==0
    print('bounded-refusal-pass')
'''
            process = subprocess.run([sys.executable, '-c', code], cwd=Path(__file__).parent,
                                     capture_output=True, text=True, timeout=5)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertIn('bounded-refusal-pass', process.stdout)

    def test_slow_worker_admission_refuses_late_without_runner(self):
        calls = 0
        def admission():
            nonlocal calls
            calls += 1
            if calls == 2:
                time.sleep(.07)
            return True
        runner = _Runner()
        result = pol.run(_manifest(), _load_config(lag=30), self.out(),
                         admission_allowed=admission, request_runner=runner)
        self.assertFalse(runner.calls)
        self.assertEqual(result['refusals_by_reason'], {'scheduler_late':1})

    def test_interrupt_drains_and_retains_active_receipt(self):
        started = threading.Event()
        def behavior(m, **kw):
            started.set()
            time.sleep(.08)
            return _receipt(m, **kw)
        def admission():
            if threading.current_thread() is threading.main_thread() and started.is_set():
                raise KeyboardInterrupt
            return True
        runner = _Runner(behavior)
        with self.assertRaises(KeyboardInterrupt):
            pol.run(_manifest(repetitions=3), _load_config(rate=20), self.out(),
                    admission_allowed=admission, request_runner=runner)
        rows = self.read_jsonl(self.out()/'requests.jsonl')
        self.assertEqual(len(rows), 3)
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(sum(r['dispatched'] for r in rows), 1)
        summary = self.read_json(self.out()/'summary.json')
        self.assertTrue(summary['interrupted'])
        self.assertEqual(summary['successful_tasks'], 1)

    def test_terminal_write_failure_stops_and_reports_incomplete_evidence(self):
        original = pol._persist
        def write(path, value, **kwargs):
            if path.name == 'requests.jsonl':
                raise OSError('synthetic private exception must not escape')
            return original(path, value, **kwargs)
        runner = _Runner()
        with mock.patch.object(pol, '_persist', side_effect=write), self.assertRaises(pol.RunError) as error:
            pol.run(_manifest(repetitions=3), _load_config(rate=10), self.out(), request_runner=runner)
        self.assertEqual(str(error.exception), 'evidence_incomplete')
        self.assertEqual(len(runner.calls), 1)
        summary = self.read_json(self.out()/'summary.json')
        self.assertFalse(summary['evidence_complete'])
        self.assertEqual(summary['total_records'], 3)
        self.assertEqual(summary['stop_reason'], 'persistence_error')

    def test_latency_includes_observer_work(self):
        def behavior(m, **kw):
            time.sleep(.04)
            return _receipt(m, latency_ms=1, **kw)
        result = pol.run(_manifest(), _load_config(), self.out(), request_runner=_Runner(behavior))
        self.assertGreaterEqual(result['successful_arrival_to_completion_p50_ms'], 35)

    def test_poisson_deterministic_offsets(self):
        cfg = _load_config(rate=50.0, arrival_process="poisson", arrival_seed=7)
        pol.run(_manifest(repetitions=4), cfg, self.out("a"), request_runner=_Runner())
        pol.run(_manifest(repetitions=4), cfg, self.out("b"), request_runner=_Runner())
        a = self.read_json(self.out("a") / "plan.json")
        b = self.read_json(self.out("b") / "plan.json")
        self.assertEqual([r["scheduled_offset_ms"] for r in a],
                         [r["scheduled_offset_ms"] for r in b])
        self.assertNotEqual([r["request_id"] for r in a],
                            [r["request_id"] for r in b])

    def test_early_stop_future_offered_false(self):
        calls = {"n": 0}

        def behavior(m, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return _receipt(m, success=False, quality=False,
                                admission_safe=False, result_state="unknown", **kw)
            return _receipt(m, **kw)

        pol.run(_manifest(repetitions=4), _load_config(rate=100.0), self.out(),
                request_runner=_Runner(behavior))
        rows = self.read_jsonl((self.out() / "requests.jsonl"))
        self.assertEqual(len(rows), 4)
        self.assertFalse(rows[-1]["offered"])
        summary = self.read_json((self.out() / "summary.json"))
        self.assertEqual(summary["planned"], 4)


if __name__ == "__main__":
    unittest.main()
