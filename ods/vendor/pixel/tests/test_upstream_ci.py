import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class UpstreamCiTests(unittest.TestCase):
    def workflow(self, name: str) -> str:
        return (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")

    def test_discovery_is_read_only_and_cannot_promote(self):
        workflow = self.workflow("upstream-discovery.yml")
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn("./pixel upstream check", workflow)
        for forbidden in ("upstream prepare", "upstream promote", "pixel apply", "git push"):
            self.assertNotIn(forbidden, workflow)

    def test_candidate_work_is_gated_and_covers_supported_hosts(self):
        workflow = self.workflow("upstream-compatibility.yml")
        self.assertIn("upstreamIntake", workflow)
        self.assertIn("needs.candidate.outputs.prepared == 'true'", workflow)
        self.assertIn("Candidate / Ubuntu 24.04", workflow)
        self.assertIn("Candidate / Debian 12", workflow)
        self.assertIn("run-upstream-container-compatibility.sh", workflow)
        self.assertIn("--trusted-base-commit", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertNotIn("container: ${{ needs.candidate.outputs.base_image }}", workflow)
        self.assertIn("OpenClaw candidate compatibility gate", workflow)

    def test_systemd_release_lane_is_explicit_and_private(self):
        workflow = self.workflow("upstream-compatibility.yml")
        self.assertIn("inputs.run_systemd_vm", workflow)
        self.assertIn("runs-on: [self-hosted, linux, x64, pixel-systemd-vm]", workflow)
        self.assertIn("scripts/run-upstream-runtime-matrix.sh", workflow)

    def test_assurance_runner_requires_two_clean_identity_bound_passes(self):
        runner = (ROOT / "scripts" / "run-upstream-assurance.sh").read_text(encoding="utf-8")
        self.assertIn("At least two consecutive passes are required", runner)
        self.assertIn("EVIDENCE_BINDING", runner)
        self.assertIn("audit_remote_refs.py", runner)
        self.assertIn("source_audit_rc", runner)
        self.assertIn("(.current.findings | length) == 0", runner)
        self.assertIn("(.history.skippedLargeObjects | length) == 0", runner)
        self.assertIn('"releaseGateStatus"', runner)
        self.assertIn('"legacyFindingCount"', runner)
        self.assertIn("./pixel test", runner)
        self.assertIn("./pixel pressure", runner)
        self.assertIn("Assurance mutated the source worktree", runner)

    def test_assurance_runner_consumes_source_audit_schema_v2(self):
        runner = (ROOT / "scripts" / "run-upstream-assurance.sh").read_text(encoding="utf-8")
        self.assertIn(".schemaVersion == 2 and .secretValuesEmitted == false", runner)
        self.assertIn("(.history.findings | length) == 0", runner)
        self.assertIn("(.policyViolations | length) == 0", runner)
        self.assertIn(".reviewedPolicy.schemaVersion == 2", runner)
        self.assertIn('.reviewedPolicy.repository == "Osmantic/Pixel"', runner)
        self.assertIn('.reviewedPolicy.sha256 | test("^[0-9a-f]{64}$")', runner)
        self.assertIn(".reviewedPolicy.reviewedBlobCount > 0", runner)
        self.assertIn(".reviewedPolicy.acknowledgedBlobCount > 0", runner)
        self.assertIn(
            "(.history.acknowledgedFixtures | length) == .reviewedPolicy.acknowledgedBlobCount",
            runner,
        )
        # Acknowledged fixture evidence must be preserved, never required empty.
        self.assertNotIn("(.history.acknowledgedFixtures | length) == 0", runner)
        self.assertIn('"reviewedPolicySha256"', runner)
        self.assertIn('"acknowledgedFixtureCount"', runner)
        self.assertIn('source_audit["reviewedPolicy"]["sha256"]', runner)
        self.assertIn('len(source_audit["history"]["acknowledgedFixtures"])', runner)

    def test_assurance_jq_gate_accepts_v2_and_rejects_blockers(self):
        import re as _re
        import subprocess as _subprocess
        runner = (ROOT / "scripts" / "run-upstream-assurance.sh").read_text(encoding="utf-8")
        match = _re.search(r"jq -e '\n(.*?)\n' \"", runner, _re.S)
        self.assertIsNotNone(match)
        program = match.group(1)
        base = {
            "schemaVersion": 2,
            "secretValuesEmitted": False,
            "history": {
                "scanned": True,
                "findings": [],
                "skippedLargeObjects": [],
                "acknowledgedFixtures": [{"object": "a" * 40}],
            },
            "current": {"findings": [], "skippedLargeFiles": []},
            "dependencies": {"findings": []},
            "policyViolations": [],
            "reviewedPolicy": {
                "schemaVersion": 2,
                "repository": "Osmantic/Pixel",
                "sha256": "b" * 64,
                "reviewedBlobCount": 5,
                "acknowledgedBlobCount": 1,
            },
        }
        clean = dict(base)
        clean["history"] = dict(base["history"])
        with tempfile.TemporaryDirectory() as t:
            clean_path = Path(t) / "clean.json"
            clean_path.write_text(json.dumps(clean), encoding="utf-8")
            result = _subprocess.run(["jq", "-e", program, str(clean_path)], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            rejected = {
                "policy violation": dict(clean, policyViolations=["oops"]),
                "blocking history": dict(clean, **{"history": dict(clean["history"], findings=[{"object": "x"}])}),
                "v1 schema": dict(clean, schemaVersion=1),
                "wrong repository": dict(clean, **{"reviewedPolicy": dict(clean["reviewedPolicy"], repository="other/repo")}),
                "inconsistent fixtures": dict(clean, **{"history": dict(clean["history"], acknowledgedFixtures=[{"object": "a" * 40}, {"object": "c" * 40}])}),
            }
            for label, doc in rejected.items():
                path = Path(t) / f"{label.replace(' ', '-')}.json"
                path.write_text(json.dumps(doc), encoding="utf-8")
                result = _subprocess.run(["jq", "-e", program, str(path)], capture_output=True)
                self.assertNotEqual(result.returncode, 0, f"jq gate did not reject: {label}")

    def test_canary_requires_isolation_observation_and_rollback(self):
        runner = (ROOT / "scripts" / "run-upstream-canary.sh").read_text(encoding="utf-8")
        self.assertIn("--systemd-only", runner)
        self.assertIn("observation_seconds=1800", runner)
        self.assertIn(".isolated == true", runner)
        self.assertIn(".rollbackRehearsed == true", runner)
        self.assertIn(".postRollbackHealthy == true", runner)
        self.assertIn('(.syntheticChecks | all(.[]; . == "pass"))', runner)
        self.assertNotIn('all(.syntheticChecks[] == "pass")', runner)

    def test_runtime_guests_use_manifest_pinned_immutable_fingerprints(self):
        runner = (ROOT / "scripts" / "run-upstream-runtime-matrix.sh").read_text(encoding="utf-8")
        self.assertIn(".qualificationImages[$key].fingerprint", runner)
        self.assertIn("incus image copy \"images:$fingerprint\"", runner)
        self.assertIn("Pinned Incus image identity mismatch", runner)
        self.assertNotIn("images:ubuntu/24.04", runner)
        self.assertNotIn("images:debian/12", runner)

    def test_runtime_guest_lifecycle_is_bounded_and_fail_closed(self):
        runner = (ROOT / "scripts" / "run-upstream-runtime-matrix.sh").read_text(encoding="utf-8")
        self.assertIn('timeout 180 incus launch "$image" "$instance"', runner)
        self.assertIn('timeout 60 incus delete --force "$instance"', runner)
        self.assertIn("qualification infrastructure is unhealthy", runner)

    def test_prepared_status_keeps_git_porcelain_leading_column(self):
        runner = (ROOT / "scripts" / "upstream.mjs").read_text(encoding="utf-8")
        self.assertIn('"status", "--porcelain=v1", "--untracked-files=all"', runner)
        self.assertIn(".trimEnd();", runner)


if __name__ == "__main__":
    unittest.main()
