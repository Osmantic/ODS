import importlib.util
import hashlib
import json
from argparse import Namespace
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("upstream_attestation", ROOT / "scripts" / "upstream-attestation.py")
ATTEST = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(ATTEST)


def acknowledged_fixtures():
    """Exact acknowledged active-lineage fixture inventory from the reviewed policy."""
    reviewed = ATTEST.REVIEWED_POLICY.load()
    return [
        {
            "object": entry["blobSha1"],
            "path": entry["path"],
            "auditSourceLabels": entry["auditSourceLabels"],
            "payloadSha256": entry["payloadSha256"],
        }
        for entry in ATTEST.REVIEWED_POLICY.acknowledged_entries(reviewed)
    ]


def review_policy_fields(policy):
    return {"reviewedBlobsSha256": policy["reviewedBlobsSha256"]}


class UpstreamAttestationTests(unittest.TestCase):
    def inputs(self):
        commit = "a" * 40
        intake_commit = "b" * 40
        manifest = json.loads((ROOT / "RELEASE-MANIFEST.json").read_text(encoding="utf-8"))
        manifest["upstreamIntake"] = {"channel": "extended-stable", "sourceCommit": intake_commit}
        compact_hash, newline_hash = ATTEST.manifest_hashes(manifest)
        tree = "c" * 40
        catalog_sha256 = "e" * 64
        source = {
            "source": {"commit": commit, "tree": tree, "clean": True},
            "release": {"pixel": manifest["pixel"], "openclaw": manifest["openclaw"]},
            "attackCatalog": {"sha256": catalog_sha256},
        }
        contract = {
            "status": "compatible",
            "blockers": [],
            "sourceCommit": intake_commit,
            "candidateManifestSha256": compact_hash,
        }
        lanes = [
            {"os": {"id": "ubuntu", "version": "24.04"}, "mode": "quick"},
            {"os": {"id": "debian", "version": "12"}, "mode": "quick"},
            {"os": {"id": "ubuntu", "version": "24.04"}, "mode": "systemd"},
        ]
        for lane in lanes:
            lane.update({
                "status": "pass",
                "candidateManifestSha256": newline_hash,
                "evidenceBindingSha256": "d" * 64,
            })
        runtime = {
            "status": "pass",
            "sourceCommit": commit,
            "intakeSourceCommit": intake_commit,
            "candidate": manifest["openclaw"],
            "lanes": lanes,
        }
        assurance = {
            "status": "pass",
            "sourceCommit": commit,
            "sourceTree": tree,
            "releaseManifestSha256": newline_hash,
            "attackCatalogSha256": catalog_sha256,
            "consecutivePasses": 2,
            "passes": [
                {
                    "pass": number,
                    "status": "pass",
                    "pressureIterations": 2,
                    "testLogSha256": "1" * 64,
                    "pressureLogSha256": "2" * 64,
                    "pressureReportSha256": "3" * 64,
                }
                for number in (1, 2)
            ],
            "artifacts": {
                name: {"bytes": 1, "sha256": "4" * 64}
                for name in (
                    "source-manifest.json",
                    "source-audit.json",
                    "remote-ref-audit.json",
                    "evidence-secret-scan.log",
                )
            },
        }
        canary = {
            "status": "pass",
            "sourceCommit": commit,
            "candidateManifestSha256": newline_hash,
            "isolated": True,
            "rollbackRehearsed": True,
            "postRollbackHealthy": True,
            "syntheticChecks": {"gateway": "pass", "workspaceToolTurn": "pass"},
            "observation": {"seconds": 1800, "minutes": 30, "healthChecks": 2},
        }
        return manifest, source, contract, runtime, assurance, canary, commit, tree

    def test_exact_complete_evidence_can_be_attested(self):
        result = ATTEST.validate_inputs(*self.inputs())
        self.assertEqual(result["decision"], "promote")
        self.assertEqual(result["exceptions"], [])
        self.assertEqual(result["runtimeEvidenceBindingSha256"], "d" * 64)
        self.assertEqual(len(result["packages"]), 4)

    def test_short_canary_and_runtime_identity_mismatch_are_rejected(self):
        inputs = list(self.inputs())
        inputs[5]["observation"]["seconds"] = 1799
        inputs[5]["observation"]["minutes"] = 29
        with self.assertRaisesRegex(ATTEST.AttestationError, "shorter than 30"):
            ATTEST.validate_inputs(*inputs)

        inputs = list(self.inputs())
        inputs[3]["lanes"][1]["candidateManifestSha256"] = "e" * 64
        with self.assertRaisesRegex(ATTEST.AttestationError, "candidate manifest"):
            ATTEST.validate_inputs(*inputs)

    def test_contract_blocker_cannot_be_signed_away(self):
        inputs = list(self.inputs())
        inputs[2]["status"] = "blocked"
        inputs[2]["blockers"] = [{"category": "gateway-auth"}]
        with self.assertRaisesRegex(ATTEST.AttestationError, "undispositioned blocker"):
            ATTEST.validate_inputs(*inputs)

    def test_remote_ref_attestation_allows_reported_legacy_findings_only(self):
        policy = json.loads(ATTEST.REMOTE_REF_POLICY.read_text(encoding="utf-8"))
        commit = "a" * 40
        report = {
            "schemaVersion": 2,
            "secretValuesEmitted": False,
            "status": "pass",
            "remoteIdentity": policy["repository"],
            "policy": {
                "repository": policy["repository"],
                "lineageRootCommit": policy["lineageRootCommit"],
                "reviewedBlobsSha256": policy["reviewedBlobsSha256"],
                "sha256": ATTEST.sha256(ATTEST.canonical(policy, newline=False)),
            },
            "acknowledgedFixtures": acknowledged_fixtures(),
            "releaseGate": {
                "status": "pass",
                "sourceHead": commit,
                "sourceHeadAdvertised": True,
                "refs": [{"ref": "refs/heads/candidate", "object": commit, "commit": commit}],
                "history": {"findings": [], "skippedLargeObjects": []},
                "policyViolations": [],
                "blockers": [],
            },
            "legacyHistory": {
                "status": "reported",
                "refs": [{
                    "ref": policy["legacyRefs"][0]["ref"],
                    "object": policy["legacyRefs"][0]["object"],
                    "commit": "b" * 40,
                    "reason": policy["legacyRefs"][0]["reason"],
                }],
                "retiredPolicyRefs": [entry["ref"] for entry in policy["legacyRefs"][1:]],
                "history": {
                    "findings": [{"object": "c" * 40, "path": "old-agent.json", "labels": ["discord-token"]}],
                    "skippedLargeObjects": [],
                },
            },
        }
        report["remoteRefCount"] = len(report["releaseGate"]["refs"]) + len(report["legacyHistory"]["refs"])
        ATTEST.validate_remote_ref_audit(report, commit)
        report["releaseGate"]["history"]["findings"] = [
            {"object": "d" * 40, "path": "pixel.json", "labels": ["gateway-token"]},
        ]
        with self.assertRaisesRegex(ATTEST.AttestationError, "active Pixel remote-ref audit"):
            ATTEST.validate_remote_ref_audit(report, commit)

    def test_remote_ref_attestation_rejects_policy_or_source_identity_drift(self):
        policy = json.loads(ATTEST.REMOTE_REF_POLICY.read_text(encoding="utf-8"))
        commit = "a" * 40
        base = {
            "schemaVersion": 2,
            "secretValuesEmitted": False,
            "status": "pass",
            "remoteIdentity": policy["repository"],
            "remoteRefCount": 1,
            "policy": {
                "repository": policy["repository"],
                "lineageRootCommit": policy["lineageRootCommit"],
                "reviewedBlobsSha256": policy["reviewedBlobsSha256"],
                "sha256": ATTEST.sha256(ATTEST.canonical(policy, newline=False)),
            },
            "acknowledgedFixtures": acknowledged_fixtures(),
            "releaseGate": {
                "status": "pass", "sourceHead": commit, "sourceHeadAdvertised": True,
                "refs": [{"ref": "refs/heads/candidate", "object": commit, "commit": commit}],
                "history": {"findings": [], "skippedLargeObjects": []},
                "policyViolations": [], "blockers": [],
            },
            "legacyHistory": {
                "status": "reported", "refs": [],
                "retiredPolicyRefs": [entry["ref"] for entry in policy["legacyRefs"]],
                "history": {"findings": [], "skippedLargeObjects": []},
            },
        }
        drifted = json.loads(json.dumps(base))
        drifted["policy"]["lineageRootCommit"] = "f" * 40
        with self.assertRaisesRegex(ATTEST.AttestationError, "policy identity"):
            ATTEST.validate_remote_ref_audit(drifted, commit)
        with self.assertRaisesRegex(ATTEST.AttestationError, "advertised candidate commit"):
            ATTEST.validate_remote_ref_audit(base, "e" * 40)
        wrong_remote = json.loads(json.dumps(base))
        wrong_remote["remoteIdentity"] = "substitute/repository"
        with self.assertRaisesRegex(ATTEST.AttestationError, "remote identity"):
            ATTEST.validate_remote_ref_audit(wrong_remote, commit)
        missing_legacy = json.loads(json.dumps(base))
        missing_legacy["legacyHistory"]["retiredPolicyRefs"].pop()
        with self.assertRaisesRegex(ATTEST.AttestationError, "legacy remote-ref inventory"):
            ATTEST.validate_remote_ref_audit(missing_legacy, commit)
        missing_candidate = json.loads(json.dumps(base))
        missing_candidate["releaseGate"]["refs"][0]["commit"] = "d" * 40
        with self.assertRaisesRegex(ATTEST.AttestationError, "omits the candidate commit"):
            ATTEST.validate_remote_ref_audit(missing_candidate, commit)

    def test_acknowledged_fixture_inventory_is_exact(self):
        policy = json.loads(ATTEST.REMOTE_REF_POLICY.read_text(encoding="utf-8"))
        commit = "a" * 40
        base = {
            "schemaVersion": 2,
            "secretValuesEmitted": False,
            "status": "pass",
            "remoteIdentity": policy["repository"],
            "remoteRefCount": 1,
            "policy": {
                "repository": policy["repository"],
                "lineageRootCommit": policy["lineageRootCommit"],
                "reviewedBlobsSha256": policy["reviewedBlobsSha256"],
                "sha256": ATTEST.sha256(ATTEST.canonical(policy, newline=False)),
            },
            "acknowledgedFixtures": acknowledged_fixtures(),
            "releaseGate": {
                "status": "pass", "sourceHead": commit, "sourceHeadAdvertised": True,
                "refs": [{"ref": "refs/heads/candidate", "object": commit, "commit": commit}],
                "history": {"findings": [], "skippedLargeObjects": []},
                "policyViolations": [], "blockers": [],
            },
            "legacyHistory": {
                "status": "reported", "refs": [],
                "retiredPolicyRefs": [entry["ref"] for entry in policy["legacyRefs"]],
                "history": {"findings": [], "skippedLargeObjects": []},
            },
        }
        ATTEST.validate_remote_ref_audit(base, commit)
        forged = json.loads(json.dumps(base))
        forged["acknowledgedFixtures"][0]["object"] = "f" * 40
        with self.assertRaisesRegex(ATTEST.AttestationError, "forged or drifted"):
            ATTEST.validate_remote_ref_audit(forged, commit)
        missing = json.loads(json.dumps(base))
        missing["acknowledgedFixtures"].pop()
        with self.assertRaisesRegex(ATTEST.AttestationError, "incomplete"):
            ATTEST.validate_remote_ref_audit(missing, commit)
        extra = json.loads(json.dumps(base))
        extra["acknowledgedFixtures"].append(dict(extra["acknowledgedFixtures"][0]))
        with self.assertRaisesRegex(ATTEST.AttestationError, "invalid"):
            ATTEST.validate_remote_ref_audit(extra, commit)
        drifted = json.loads(json.dumps(base))
        drifted["acknowledgedFixtures"][0]["path"] = "substituted/path"
        with self.assertRaisesRegex(ATTEST.AttestationError, "forged or drifted"):
            ATTEST.validate_remote_ref_audit(drifted, commit)

    def test_promotion_is_explicit(self):
        with self.assertRaisesRegex(ATTEST.AttestationError, "requires --confirm"):
            ATTEST.promote(Namespace(confirm=False))

    @unittest.skipUnless(shutil.which("ssh-keygen"), "OpenSSH signing is required")
    def test_detached_signature_and_every_indexed_file_are_verified(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            records = {
                "sourceManifest": "source-manifest.json",
                "contractDiff": "contract-diff.json",
                "runtimeMatrix": "runtime-matrix.json",
                "assuranceSummary": "assurance-summary.json",
                "canarySummary": "canary-summary.json",
                "assurance:source-audit.json": "source-audit.json",
                "assurance:remote-ref-audit.json": "remote-ref-audit.json",
                "assurance:evidence-secret-scan.log": "evidence-secret-scan.log",
            }
            evidence = {}
            for number, (label, name) in enumerate(records.items(), 1):
                path = root / name
                payload = (
                    (json.dumps({"record": number}, sort_keys=True) + "\n").encode()
                    if name.endswith(".json") else b"Secret policy check passed.\n"
                )
                path.write_bytes(payload)
                evidence[label] = {"file": name, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
            attestation = root / "attestation.json"
            attestation.write_bytes(ATTEST.canonical({
                "operation": "pixel-upstream-qualification-attestation",
                "decision": "promote",
                "sourceCommit": "a" * 40,
                "evidence": evidence,
            }))
            key = root / "release-key"
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
            allowed = root / "allowed-signers"
            allowed.write_text(f"osmantic-pixel-release {key.with_suffix('.pub').read_text(encoding='utf-8')}", encoding="utf-8")
            subprocess.run(
                ["ssh-keygen", "-Y", "sign", "-f", str(key), "-n", ATTEST.NAMESPACE, str(attestation)],
                check=True,
                capture_output=True,
            )
            arguments = Namespace(
                attestation=attestation,
                signature=Path(f"{attestation}.sig"),
                allowed_signers=allowed,
                identity="osmantic-pixel-release",
                evidence_dir=root,
            )
            result = ATTEST.verify(arguments)
            self.assertEqual(result["status"], "verified")
            (root / "source-audit.json").write_text('{"record":999}\n', encoding="utf-8")
            with self.assertRaisesRegex(ATTEST.AttestationError, "evidence differs"):
                ATTEST.verify(arguments)


    def test_acknowledged_fixture_extra_key_fails(self):
        policy = json.loads(ATTEST.REMOTE_REF_POLICY.read_text(encoding="utf-8"))
        commit = "a" * 40
        base = {
            "schemaVersion": 2,
            "secretValuesEmitted": False,
            "status": "pass",
            "remoteIdentity": policy["repository"],
            "remoteRefCount": 1,
            "policy": {
                "repository": policy["repository"],
                "lineageRootCommit": policy["lineageRootCommit"],
                "reviewedBlobsSha256": policy["reviewedBlobsSha256"],
                "sha256": ATTEST.sha256(ATTEST.canonical(policy, newline=False)),
            },
            "acknowledgedFixtures": acknowledged_fixtures(),
            "releaseGate": {
                "status": "pass", "sourceHead": commit, "sourceHeadAdvertised": True,
                "refs": [{"ref": "refs/heads/candidate", "object": commit, "commit": commit}],
                "history": {"findings": [], "skippedLargeObjects": []},
                "policyViolations": [], "blockers": [],
            },
            "legacyHistory": {
                "status": "reported", "refs": [],
                "retiredPolicyRefs": [entry["ref"] for entry in policy["legacyRefs"]],
                "history": {"findings": [], "skippedLargeObjects": []},
            },
        }
        ATTEST.validate_remote_ref_audit(base, commit)
        extra_key = json.loads(json.dumps(base))
        extra_key["acknowledgedFixtures"][0]["extra"] = "x"
        with self.assertRaisesRegex(ATTEST.AttestationError, "inventory is invalid"):
            ATTEST.validate_remote_ref_audit(extra_key, commit)
        missing_key = json.loads(json.dumps(base))
        missing_key["acknowledgedFixtures"][0].pop("payloadSha256")
        with self.assertRaisesRegex(ATTEST.AttestationError, "inventory is invalid"):
            ATTEST.validate_remote_ref_audit(missing_key, commit)

    def test_reviewed_policy_errors_are_fail_closed_attestation_errors(self):
        with mock.patch.object(
            ATTEST.REVIEWED_POLICY, "policy_sha256",
            side_effect=ATTEST.REVIEWED_POLICY.ReviewedPolicyError("boom"),
        ):
            with self.assertRaisesRegex(ATTEST.AttestationError, "reviewed policy is invalid"):
                ATTEST.validate_reviewed_policy_inventory(
                    {"policy": {}}, {"reviewedBlobsSha256": "0" * 64},
                )

    def test_remote_ref_attestation_fails_closed_on_unreadable_policy(self):
        with tempfile.TemporaryDirectory() as t:
            report = {"schemaVersion": 2, "secretValuesEmitted": False, "status": "pass"}
            missing = Path(t) / "missing.json"
            with mock.patch.object(ATTEST, "REMOTE_REF_POLICY", missing):
                with self.assertRaises(ATTEST.AttestationError):
                    ATTEST.validate_remote_ref_audit(report, "a" * 40)
            bad = Path(t) / "bad.json"
            bad.write_bytes(b"\xff\xfe not utf-8")
            with mock.patch.object(ATTEST, "REMOTE_REF_POLICY", bad):
                with self.assertRaises(ATTEST.AttestationError):
                    ATTEST.validate_remote_ref_audit(report, "a" * 40)

if __name__ == "__main__":
    unittest.main()
