from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
from project_jobs import ProjectJobs


@unittest.skipUnless(os.name == "posix", "private POSIX broker state")
class ProjectJobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "state"
        self.jobs = ProjectJobs(self.root)
        self.request = {"project": "example", "sourceSha256": "b" * 64,
                        "image": "sha256:" + "c" * 64, "outputDirectory": "out"}

    def create(self):
        return self.jobs.create("a" * 64, self.request)[0]

    def test_concurrent_replay_creates_one_job_and_one_claim(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.jobs.create("a" * 64, self.request), range(16)))
        self.assertEqual(sum(created for _, created in results), 1)
        self.assertEqual(len({job for job, _ in results}), 1)
        job = results[0][0]
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(lambda _: self.jobs.claim(job), range(16))), 1)

    def test_restart_observes_running_job_without_reclaim(self):
        job = self.create()
        self.assertTrue(self.jobs.claim(job))
        restarted = ProjectJobs(self.root)
        self.assertEqual(restarted.observe(job)["state"], "running")
        self.assertFalse(restarted.claim(job))
        self.assertEqual(restarted.create("a" * 64, self.request), (job, False))

    def test_changed_input_cannot_reuse_request_key(self):
        self.create()
        with self.assertRaises(ValueError):
            self.jobs.create("a" * 64, {**self.request, "sourceSha256": "c" * 64})

    def test_cancelled_queue_never_starts(self):
        job = self.create()
        self.assertEqual(self.jobs.request_cancel(job)["state"], "cancelled")
        self.assertFalse(self.jobs.claim(job))

    def test_running_cancellation_waits_for_real_stop_result(self):
        job = self.create()
        self.jobs.claim(job)
        self.assertEqual(self.jobs.request_cancel(job)["state"], "running")
        self.jobs.record_stage(job, "acquire", {"status": "cancelled", "exitCode": 137})
        self.assertEqual(self.jobs.observe(job)["state"], "cancelled")

    def test_failure_stops_stages_and_success_requires_all_steps(self):
        job = self.create()
        self.jobs.claim(job)
        with self.assertRaises(ValueError):
            self.jobs.complete(job, {"sha256": "d" * 64, "files": 1})
        self.jobs.record_stage(job, "acquire", {"status": "failed", "exitCode": 1})
        with self.assertRaises(ValueError):
            self.jobs.record_stage(job, "test", {"status": "succeeded", "exitCode": 0})
        self.assertEqual(self.jobs.observe(job)["state"], "failed")

    def test_completed_job_is_persistent_and_not_reexecuted(self):
        job = self.create()
        self.jobs.claim(job)
        for stage in ("acquire", "test", "build"):
            self.jobs.record_stage(job, stage, {"status": "succeeded", "exitCode": 0})
        output = {"sha256": "d" * 64, "files": 1,
                  "relativeDirectory": "example/ods-builds/" + job.removeprefix("ods-project-") + "/site"}
        self.jobs.complete(job, output)
        self.assertEqual(ProjectJobs(self.root).observe(job)["output"], output)
        self.assertFalse(self.jobs.claim(job))

    def test_recovery_only_records_confirmed_exit_and_keeps_missing_unknown(self):
        job = self.create()
        self.jobs.claim(job)
        observed = []
        def unknown(image, job_id, stage):
            observed.append(stage)
            return {"status": "unconfirmed", "exitCode": None, "evidence": "unavailable"}
        self.assertEqual(self.jobs.reconcile(job, unknown)["job"]["steps"], [])
        self.assertEqual(self.jobs.observe(job)["state"], "running")
        def finished(image, job_id, stage):
            observed.append(stage)
            return {"status": "succeeded", "exitCode": 0, "evidence": "docker-state", "outputUnavailable": True}
        row = self.jobs.reconcile(job, finished)["job"]
        self.assertEqual([s["stage"] for s in row["steps"]], ["acquire"])
        self.assertTrue(row["steps"][0]["outputUnavailable"])
        self.assertEqual(observed, ["acquire", "acquire"])
        self.assertFalse(ProjectJobs(self.root).claim(job))
