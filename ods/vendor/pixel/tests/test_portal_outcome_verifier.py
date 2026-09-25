import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "pixel_test_portal_outcome_verifier", ROOT / "scripts/portal_outcome_verifier.py",
)
verifier = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(verifier)


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class PortalOutcomeVerifierTests(unittest.TestCase):
    def setUp(self):
        corpus = json.loads((ROOT / "security-evals/portal-user-journeys/corpus-v1.json").read_text(encoding="utf-8"))
        self.journey = next(item for item in corpus["journeys"] if item["id"] == "bounded-security-scanner")
        self.admission = {
            "journeyId": self.journey["id"],
            "bindings": {"sourceSnapshotSha256": "3" * 64},
        }
        self.definition = {
            "$schema": verifier.VERIFIER_SCHEMA,
            "operation": verifier.VERIFIER_OPERATION, "schemaVersion": 1,
            "journeyId": self.journey["id"],
            "acceptanceCriteria": ["Return measured bounded findings"],
            "checks": [
                {"assertionId": "evidence-exact-source", "check": "evidence-binds-source-snapshot"},
                {"assertionId": "real-tool-outcome", "check": "command-exit-zero"},
                {"assertionId": "artifact-openable", "check": "artifact-parses"},
                {"assertionId": "bounded-finding-language", "check": "artifact-language-bounded"},
            ],
            "forbiddenPhrases": ["guaranteed to be fully secure", "no vulnerabilities exist"],
            "finalReply": None,
            "workspaceVerification": None,
            "boundary": verifier.VERIFIER_BOUNDARY,
        }

    def fixture(self, parent: Path, *, artifact_text=b"# Findings\nmeasured coverage: 41 of 44 checks\n"):
        run_dir = parent / "run"
        (run_dir / "evidence").mkdir(parents=True)
        payloads = {
            "exact-source": ("evidence/source.json", b'{"sourceSnapshotSha256":"' + b"3" * 64 + b'"}\n'),
            "runtime-environment": ("evidence/environment.json", b'{"imageDigest":"sha256:fixture"}\n'),
            "command-exit": ("evidence/command-exit.json", b'{"exitCode":0,"command":"scanner --measure"}\n'),
            "artifact-digest": ("evidence/artifacts.json", b'{"artifacts":1}\n'),
        }
        evidence = []
        for kind, (relative, payload) in payloads.items():
            (run_dir / relative).write_bytes(payload)
            evidence.append({"type": kind, "relativePath": relative, "sha256": digest(payload), "bytes": len(payload)})
        (run_dir / "artifact.md").write_bytes(artifact_text)
        artifacts = [{"kind": "finding-report", "relativePath": "artifact.md", "sha256": digest(artifact_text), "bytes": len(artifact_text)}]
        return run_dir, evidence, artifacts

    def test_exact_evidence_yields_all_pass_with_per_check_bindings(self):
        with tempfile.TemporaryDirectory() as temporary:
            run_dir, evidence, artifacts = self.fixture(Path(temporary))
            definition = verifier.load_definition(json.dumps(self.definition).encode("utf-8"))
            results = verifier.run_checks(
                definition, journey=self.journey, admission=self.admission,
                run_dir=run_dir, evidence=evidence, artifacts=artifacts,
            )
            self.assertEqual({item["id"] for item in results}, set(self.journey["assertions"]))
            self.assertTrue(all(item["status"] == "pass" for item in results))
            by_id = {item["id"]: item for item in results}
            self.assertEqual(by_id["evidence-exact-source"]["evidencePath"], "evidence/source.json")
            self.assertEqual(by_id["real-tool-outcome"]["evidencePath"], "evidence/command-exit.json")
            self.assertEqual(by_id["artifact-openable"]["evidencePath"], "evidence/artifacts.json")

    def test_forbidden_language_nonzero_exit_and_source_drift_fail_the_assertions(self):
        with tempfile.TemporaryDirectory() as temporary:
            run_dir, evidence, artifacts = self.fixture(
                Path(temporary), artifact_text=b"This system is guaranteed to be fully secure.\n",
            )
            definition = verifier.load_definition(json.dumps(self.definition).encode("utf-8"))
            results = verifier.run_checks(
                definition, journey=self.journey, admission=self.admission,
                run_dir=run_dir, evidence=evidence, artifacts=artifacts,
            )
            by_id = {item["id"]: item["status"] for item in results}
            self.assertEqual(by_id["bounded-finding-language"], "fail")
            self.assertEqual(by_id["artifact-openable"], "pass")

        with tempfile.TemporaryDirectory() as temporary:
            run_dir, evidence, artifacts = self.fixture(Path(temporary))
            drifted = json.loads(json.dumps(self.admission))
            drifted["bindings"]["sourceSnapshotSha256"] = "f" * 64
            definition = verifier.load_definition(json.dumps(self.definition).encode("utf-8"))
            results = verifier.run_checks(
                definition, journey=self.journey, admission=drifted,
                run_dir=run_dir, evidence=evidence, artifacts=artifacts,
            )
            by_id = {item["id"]: item["status"] for item in results}
            self.assertEqual(by_id["evidence-exact-source"], "fail")

    def test_tampered_evidence_unknown_checks_and_partial_coverage_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            run_dir, evidence, artifacts = self.fixture(Path(temporary))
            (run_dir / "evidence/command-exit.json").write_bytes(b'{"exitCode":1,"command":"scanner"}\n')
            definition = verifier.load_definition(json.dumps(self.definition).encode("utf-8"))
            with self.assertRaisesRegex(verifier.evaluation.OutcomeError, "exact size and digest"):
                verifier.run_checks(
                    definition, journey=self.journey, admission=self.admission,
                    run_dir=run_dir, evidence=evidence, artifacts=artifacts,
                )

    def test_workspace_verification_is_immutable_bounded_and_covers_every_criterion(self):
        value = json.loads(json.dumps(self.definition))
        value["acceptanceCriteria"] = ["Implementation works", "Protected controls remain unchanged"]
        value["workspaceVerification"] = {
            "mode": "independent",
            "checks": [
                {"id": "patch-boundary", "kind": "patch-integrity", "criterionIndexes": [1]},
                {
                    "id": "semantic", "kind": "command", "criterionIndexes": [0],
                    "workingDirectory": "source", "argv": ["/usr/bin/python3", "-c", "print('OK')"],
                    "timeoutSeconds": 30, "maxOutputBytes": 65536,
                },
            ],
            "immutablePathPrefixes": ["source/__pixel_inert__/"],
            "maxRuntimeSeconds": 30, "maxOutputBytes": 65536, "network": "none",
            "boundary": verifier.WORKSPACE_BOUNDARY,
        }
        loaded = verifier.load_definition(json.dumps(value).encode("utf-8"))
        self.assertEqual(loaded["workspaceVerification"]["checks"][1]["argv"][0], "/usr/bin/python3")
        uncovered = json.loads(json.dumps(value))
        uncovered["workspaceVerification"]["checks"] = uncovered["workspaceVerification"]["checks"][:1]
        with self.assertRaisesRegex(verifier.evaluation.OutcomeError, "every acceptance criterion"):
            verifier.load_definition(json.dumps(uncovered).encode("utf-8"))
        relative = json.loads(json.dumps(value))
        relative["workspaceVerification"]["checks"][1]["argv"][0] = "python3"
        with self.assertRaisesRegex(verifier.evaluation.OutcomeError, "command is invalid"):
            verifier.load_definition(json.dumps(relative).encode("utf-8"))

        unknown = json.loads(json.dumps(self.definition))
        unknown["checks"][0]["check"] = "trust-the-backend"
        with self.assertRaisesRegex(verifier.evaluation.OutcomeError, "unknown or duplicated"):
            verifier.load_definition(json.dumps(unknown).encode("utf-8"))

        partial = json.loads(json.dumps(self.definition))
        partial["checks"] = partial["checks"][:2]
        definition = verifier.load_definition(json.dumps(partial).encode("utf-8"))
        with tempfile.TemporaryDirectory() as temporary:
            run_dir, evidence, artifacts = self.fixture(Path(temporary))
            with self.assertRaisesRegex(verifier.evaluation.OutcomeError, "exact journey assertions"):
                verifier.run_checks(
                    definition, journey=self.journey, admission=self.admission,
                    run_dir=run_dir, evidence=evidence, artifacts=artifacts,
                )

    def test_exact_final_reply_is_controller_scored_from_content_free_command_evidence(self):
        expected = b"DONE"
        value = json.loads(json.dumps(self.definition))
        value["finalReply"] = {"sha256": digest(expected), "bytes": len(expected), "normalization": "exact-utf8"}
        value["checks"][1]["check"] = "final-reply-exact"
        definition = verifier.load_definition(json.dumps(value).encode("utf-8"))
        with tempfile.TemporaryDirectory() as temporary:
            run_dir, evidence, artifacts = self.fixture(Path(temporary))
            command = json.dumps({
                "exitCode": 0, "command": "agent",
                "finalReplySha256": digest(expected), "finalReplyBytes": len(expected),
                "finalReplyNormalization": "exact-utf8",
            }, sort_keys=True).encode("utf-8")
            path = run_dir / "evidence/command-exit.json"
            path.write_bytes(command)
            entry = next(item for item in evidence if item["type"] == "command-exit")
            entry.update(sha256=digest(command), bytes=len(command))
            results = verifier.run_checks(
                definition, journey=self.journey, admission=self.admission,
                run_dir=run_dir, evidence=evidence, artifacts=artifacts,
            )
            self.assertEqual(next(item for item in results if item["id"] == "real-tool-outcome")["status"], "pass")
            wrong = json.loads(command)
            wrong["finalReplySha256"] = "0" * 64
            wrong_payload = json.dumps(wrong, sort_keys=True).encode("utf-8")
            path.write_bytes(wrong_payload)
            entry.update(sha256=digest(wrong_payload), bytes=len(wrong_payload))
            results = verifier.run_checks(
                definition, journey=self.journey, admission=self.admission,
                run_dir=run_dir, evidence=evidence, artifacts=artifacts,
            )
            self.assertEqual(next(item for item in results if item["id"] == "real-tool-outcome")["status"], "fail")

        wrong_journey = json.loads(json.dumps(self.definition))
        wrong_journey["journeyId"] = "fresh-owner-briefing"
        definition = verifier.load_definition(json.dumps(wrong_journey).encode("utf-8"))
        with tempfile.TemporaryDirectory() as temporary:
            run_dir, evidence, artifacts = self.fixture(Path(temporary))
            with self.assertRaisesRegex(verifier.evaluation.OutcomeError, "different journey"):
                verifier.run_checks(
                    definition, journey=self.journey, admission=self.admission,
                    run_dir=run_dir, evidence=evidence, artifacts=artifacts,
                )


if __name__ == "__main__":
    unittest.main()
