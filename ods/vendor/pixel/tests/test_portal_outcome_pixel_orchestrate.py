import base64
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import portal_outcome_materialize_battery as materializer
import portal_outcome_pair as pair
import portal_outcome_pixel_orchestrate as pixel
from tests.test_portal_outcome_materialize_battery import contracts, payload
from tests.test_control_server import RecordingRunner, chat_meta, control_module
from tests.test_portal_outcome_assistant_evidence import model_proxy_receipts


RUN_ID = "outcomerun-1786622400100-abcdefabcdef"
DIMENSIONS = ("outcome-completeness", "correctness", "artifact-quality", "recovery", "operator-effort", "latency", "resource-use")


def interaction(source, **counts):
    observed = {"source": source, **counts}
    return {
        "schemaVersion": 1, "mode": "single-admission-noninteractive", "source": source,
        "observationSha256": pixel.evaluation.sha256(pixel.evaluation.canonical(observed)),
        "operatorInputsAfterAdmission": counts.get("operatorInputsAfterAdmission", 0),
        "operatorAttentionRequests": counts.get("operatorAttentionRequests", 0),
        "approvalRequests": counts.get("approvalRequests", 0),
        "scopeExpansionRequests": counts.get("scopeExpansionRequests", 0),
        "safetyBlocks": counts.get("safetyBlocks", 0), "interruptions": counts.get("interruptions", 0),
        "complete": True, "workerSelfReported": False, "boundary": pixel.INTERACTION_BOUNDARY,
    }


class FakePixelSystem:
    def __init__(self, model_payload, inference_payload):
        self.model_sha = pixel.evaluation.sha256(model_payload)
        self.inference_sha = pixel.evaluation.sha256(inference_payload)
        self.calls = []

    def qualify_runtime(
        self, *, run_id, admission, model_contract, inference_contract,
        model_contract_payload, inference_contract_payload, model_contract_sha256,
        inference_contract_sha256, run_dir,
    ):
        self.calls.append("qualify")
        self.definition_task_id = admission["taskId"]
        self.assert_model_payload = model_contract_payload
        self.assert_inference_payload = inference_contract_payload
        self.assert_model_sha256 = model_contract_sha256
        self.assert_inference_sha256 = inference_contract_sha256
        return {
            "schemaVersion": 1, "operation": "pixel-portal-outcome-runtime", "runId": run_id,
            "profile": admission["profile"],
            "modelId": model_contract["modelId"], "modelContractSha256": self.model_sha,
            "inferenceContractSha256": self.inference_sha, "workPolicySha256": "8" * 64,
            "environmentSha256": "7" * 64,
            "runtimeEnvironmentSha256": "6" * 64,
            "harnessContractSha256": "9" * 64, "runnerImageDigest": "sha256:" + "e" * 64,
            "verifierImageDigest": "sha256:" + "d" * 64,
            "backendImageDigest": model_contract["runtime"]["imageDigest"],
            "modelArtifactSha256": model_contract["artifact"]["sha256"], "launchBundleSha256": "c" * 64,
            "qualificationReceiptSha256": "f" * 64,
            "backendFresh": True,
            "runnerFresh": True, "realBackend": True, "realTools": True, "crossRunStateObserved": False,
            "qwenProductModel": False, "boundary": pixel.PIXEL_RUNTIME_BOUNDARY,
        }

    def run_pixel(self, **options):
        self.calls.append("run")
        definition = options["verifier_definition"]
        job_id = "work-1786622400000-abcdef123456"
        claim_id = "workclaim-1786622400000-abcdef123456"
        candidate = "a" * 64
        checks = [
            {"id": "patch-boundary", "kind": "patch-integrity", "criterionIndexes": [0], "status": "pass", "candidateSha256": candidate, "evidenceSha256": "b" * 64, "changes": 1, "files": 2, "bytes": 10},
            {"id": "semantic", "kind": "command", "criterionIndexes": [0], "status": "pass", "candidateSha256": candidate, "evidenceSha256": "c" * 64, "runtimeMilliseconds": 10, "exitCode": 0, "signal": None, "timedOut": False, "outputLimitExceeded": False, "spawnFailed": False, "stdoutBytes": 10, "stdoutSha256": "d" * 64, "stderrBytes": 0, "stderrSha256": "f" * 64},
        ]
        self.last_verification = {
            "schemaVersion": 1, "format": "pixel-independent-verification-v1", "jobId": job_id,
            "claimId": claim_id, "planSha256": "1" * 64, "patchSha256": "2" * 64,
            "candidateSha256": candidate, "status": "pass", "checks": checks,
            "criteria": [{"index": 0, "status": "pass", "checkIds": ["patch-boundary", "semantic"]}],
            "network": "none", "workerSelectedChecks": False, "externalEffects": False,
            "boundary": pixel.WORK_VERIFICATION_BOUNDARY,
        }
        self.assert_definition = definition
        return {
            "exitCode": 0, "latencyMs": 1500, "finalMessage": "Done",
            "usage": {"modelRequests": 3, "inputTokens": 1000, "outputTokens": 200, "networkBytes": 4096, "toolCalls": 5},
            "authority": {"sourceMutation": False, "merge": False, "deploy": False, "externalEffects": False},
            "interaction": interaction("pixel-builder-checkpoint-v1"),
            "independentVerification": self.last_verification,
            "artifacts": [{"kind": "patch", "relativePath": "pixel-work-patch.json", "payload": b'{"patch":true}\n'}],
        }

    def runtime_control(self, *, run_id, condition):
        self.calls.append("control")
        warm = condition == "warm-neutral-probe"
        return {
            "schemaVersion": 1, "operation": "pixel-portal-outcome-runtime-control",
            "runId": run_id, "condition": condition, "status": "ready",
            "warmupRequestSha256": "7" * 64 if warm else None,
            "warmupResponseSha256": "8" * 64 if warm else None,
            "requestsBefore": 0, "requestsAfter": 1 if warm else 0,
            "warmupModelRequests": 1 if warm else 0,
            "warmupInputTokens": 9 if warm else 0, "warmupOutputTokens": 1 if warm else 0,
            "measuredUsageIncludesWarmup": False, "promptCachePolicy": "empty-at-run-start",
            "crossRunStateObserved": False,
            "authority": {
                "grantsTaskExecution": False, "grantsToolUse": False, "grantsProviderCall": False,
                "grantsCredentialUse": False, "grantsExternalEffect": False, "grantsCompletion": False,
            },
            "boundary": pixel.evaluation.RUNTIME_CONTROL_BOUNDARY,
        }

    def teardown_runtime(self, *, run_id, runtime):
        self.calls.append("teardown")
        self.teardown_run_id = run_id
        self.teardown_receipt = runtime


class FakeControllerPixelSystem(FakePixelSystem):
    def run_pixel(self, **options):
        result = super().run_pixel(**options)
        result["interaction"] = interaction("pixel-controller-ledger-v1")
        checkpoint_hashes = [str(index) * 64 for index in range(1, 5)]
        parent_lineage = [
            {
                "sequence": index, "state": state, "checkpointSha256": checkpoint_hashes[index],
                "previousCheckpointSha256": None if index == 0 else checkpoint_hashes[index - 1],
            }
            for index, state in enumerate(("ready", "running", "ready", "completed"))
        ]
        bundle_hashes = ["a" * 64, "b" * 64]
        custody_lineage = [
            {"sequence": 0, "purpose": "initial", "bundleSha256": bundle_hashes[0], "previousBundleSha256": None, "leaseIteration": 1},
            {"sequence": 1, "purpose": "admission", "bundleSha256": bundle_hashes[1], "previousBundleSha256": bundle_hashes[0], "leaseIteration": 1},
        ]
        controller = {
            "schemaVersion": 1, "format": "pixel-controller-evidence-v1", "status": "pass",
            "goalId": "workgoal-1786622400000-abcdef123456", "goalSha256": "c" * 64,
            "objectiveSha256": "d" * 64, "sourceSnapshotSha256": options["source_reference"]["sha256"],
            "modelContractSha256": self.model_sha, "inferenceContractSha256": self.inference_sha,
            "parent": {
                "state": "completed", "checkpointCount": 4, "headCheckpointSha256": checkpoint_hashes[-1],
                "milestonesTotal": 1, "milestonesCompleted": 1, "jobsStarted": 1, "lineage": parent_lineage,
            },
            "child": {
                "profile": "builder", "jobId": "work-1786622400000-abcdef123456", "jobSha256": "e" * 64,
                "planSha256": "f" * 64, "state": "completed", "checkpointCount": 4,
                "headCheckpointSha256": "1" * 64, "iterations": 1,
                "verificationEvidenceSha256": pixel.evaluation.sha256(pixel.evaluation.canonical(result["independentVerification"])),
            },
            "custody": {"recordCount": 2, "headBundleSha256": bundle_hashes[-1], "exactLeaseOnly": True, "lineage": custody_lineage},
            "usage": {
                "runtimeSeconds": 2, "modelRequests": 3, "inputTokens": 1000, "outputTokens": 200,
                "networkBytes": 4096, "artifactBytes": 100, "failures": 0, "toolCalls": 5, "latencyMs": 1500,
            },
            "independentVerification": {
                "status": "pass", "evidenceSha256": pixel.evaluation.sha256(pixel.evaluation.canonical(result["independentVerification"])),
                "workerSelectedChecks": False, "externalEffects": False,
            },
            "cleanup": {"preparedRunsDiscarded": 1, "expectedPreparedRuns": 1, "activeChild": False, "ephemeralPreparationComplete": True},
            "privacy": {"directNetwork": False, "credentials": False, "privateDataSentRemote": False, "externalEffects": False},
            "authority": {
                "grantsExecution": False, "grantsReplay": False, "grantsLease": False, "grantsScopeExpansion": False,
                "grantsExternalEffects": False, "grantsCompletion": False, "grantsPublication": False,
                "grantsDeployment": False, "grantsAcceptance": False, "grantsPromotion": False,
            },
            "boundary": pixel.controller_evidence.CONTROLLER_EVIDENCE_BOUNDARY,
        }
        result["controllerEvidence"] = controller
        result["artifacts"].append({
            "kind": "test-evidence", "relativePath": "pixel-controller-evidence.json",
            "payload": json.dumps(controller, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n",
        })
        return result


class FakeResearchPixelSystem(FakePixelSystem):
    def run_pixel(self, **options):
        self.calls.append("run")
        batch_sha = "1" * 64
        source_id = "source-0123456789abcdef"
        content_sha = "2" * 64
        receipt_sha = "3" * 64
        evidence_sha = "4" * 64
        authority = {
            "directNetwork": False, "credentials": False, "externalWrites": False, "accounts": False,
            "messages": False, "publish": False, "purchase": False, "policyMutation": False,
            "scopeExpansion": False,
        }
        report = {
            "$schema": "https://osmantic.com/pixel/schemas/work-research-report-v1.schema.json",
            "schemaVersion": 1, "reportId": "researchreport-1786622402000-abcdef123456",
            "jobId": "work-1786622400000-abcdef123456", "claimId": "workclaim-1786622400000-abcdef123456",
            "createdAt": "2026-08-13T12:00:02Z", "planSha256": "5" * 64,
            "researchPolicySha256": "6" * 64, "batchSha256s": [batch_sha],
            "titleBase64": base64.b64encode(b"Bounded public result").decode("ascii"),
            "findings": [{
                "findingId": "finding-1", "statementBase64": base64.b64encode(b"The retained source contains the cited evidence.").decode("ascii"),
                "material": True, "citations": [{
                    "batchSha256": batch_sha, "sourceId": source_id,
                    "evidenceBase64": base64.b64encode(b"Exact public evidence passage").decode("ascii"),
                    "evidenceSha256": pixel.evaluation.sha256(b"Exact public evidence passage"),
                }],
            }],
            "limitationsBase64": base64.b64encode(b"Semantic entailment and freshness labels remain unverified.").decode("ascii"),
            "dataClassification": "public", "privateDataIncluded": False, "externalEffects": False,
            "authority": authority, "boundary": pixel.research_evidence.REPORT_BOUNDARY,
        }
        report_evidence_sha = report["findings"][0]["citations"][0]["evidenceSha256"]
        verification = {
            "$schema": "https://osmantic.com/pixel/schemas/work-research-verification-v1.schema.json",
            "schemaVersion": 1, "verificationId": "researchverification-1786622403000-abcdef123456",
            "jobId": report["jobId"], "claimId": report["claimId"], "createdAt": "2026-08-13T12:00:03Z",
            "planSha256": report["planSha256"], "batchSha256s": [batch_sha],
            "reportSha256": pixel.evaluation.sha256(pixel.evaluation.canonical(report)),
            "status": "evidence-pass", "verificationLevel": "deterministic-evidence-presence",
            "semanticEntailmentVerified": False, "independent": True, "network": "none", "modelUsed": False,
            "findings": [{
                "findingId": "finding-1", "status": "pass", "citations": [{
                    "batchSha256": batch_sha, "sourceId": source_id, "contentSha256": content_sha,
                    "receiptSha256": receipt_sha, "evidenceSha256": report_evidence_sha,
                    "evidenceBytes": len(b"Exact public evidence passage"), "offset": 10, "status": "present",
                }],
            }],
            "privateDataIncluded": False, "externalEffects": False, "authority": authority,
            "boundary": pixel.research_evidence.VERIFICATION_BOUNDARY,
        }
        report_payload = pixel.evaluation.canonical(report) + b"\n"
        verification_payload = pixel.evaluation.canonical(verification) + b"\n"
        provenance = {
            "schemaVersion": 1, "format": "pixel-research-provenance-v1",
            "reportArtifactSha256": pixel.evaluation.sha256(report_payload),
            "verificationArtifactSha256": pixel.evaluation.sha256(verification_payload),
            "reportCreatedAt": report["createdAt"], "verificationCreatedAt": verification["createdAt"],
            "latestBatchCreatedAt": "2026-08-13T12:00:01Z",
            "batches": [{
                "batchSha256": batch_sha, "createdAt": "2026-08-13T12:00:01Z", "adapter": "reference",
                "normalizedQuerySha256": "7" * 64, "sources": [{
                    "sourceId": source_id, "rank": 1, "sourceType": "academic",
                    "canonicalUrlSha256": "8" * 64, "domainSha256": "9" * 64,
                    "retrievalStatus": "fetched", "retrievedAt": "2026-08-13T12:00:01Z",
                    "contentSha256": content_sha, "receiptSha256": receipt_sha,
                }],
            }],
            "citationBindings": [{
                "findingId": "finding-1", "batchSha256": batch_sha, "sourceId": source_id,
                "contentSha256": content_sha, "receiptSha256": receipt_sha,
                "evidenceSha256": report_evidence_sha, "status": "present",
            }],
            "primarySourcePreferenceVerified": False, "freshnessLabelsVerified": False,
            "semanticEntailmentVerified": False, "privateDataIncluded": False, "externalEffects": False,
            "boundary": pixel.research_evidence.PROVENANCE_BOUNDARY,
        }
        return {
            "exitCode": 0, "latencyMs": 2500, "finalMessage": "Bounded public result",
            "usage": {"modelRequests": 4, "inputTokens": 1200, "outputTokens": 300, "networkBytes": 8192, "toolCalls": 7},
            "authority": {"sourceMutation": False, "merge": False, "deploy": False, "externalEffects": False},
            "interaction": interaction("pixel-researcher-lifecycle-v1"),
            "independentVerification": verification, "researchEvidence": provenance,
            "artifacts": [
                {"kind": "finding-report", "relativePath": "pixel-research-report.json", "payload": report_payload},
                {"kind": "test-evidence", "relativePath": "pixel-research-verification.json", "payload": verification_payload},
                {"kind": "test-evidence", "relativePath": "pixel-research-evidence.json", "payload": b'{"worker":"bounded"}\n'},
            ],
        }


class FakeAssistantPixelSystem(FakePixelSystem):
    def __init__(self, model_payload, inference_payload):
        super().__init__(model_payload, inference_payload)
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)

    def run_pixel(self, **options):
        self.calls.append("run")
        onboarding = self.base / "private" / "onboarding.json"
        binary = self.base / "bin" / "openclaw"
        binary.parent.mkdir(mode=0o700, parents=True)
        binary.write_text("#!/bin/sh\n", encoding="utf-8")
        binary.chmod(0o700)
        home = self.base / "openclaw-home"; home.mkdir(mode=0o700)
        control_module.atomic_json(home / "openclaw.json", {"gateway": {"mode": "local"}}, 0o600)
        configured = control_module.merge_onboarding({}, control_module.default_onboarding())
        configured.update({
            "openclawBin": str(binary), "openclawHome": str(home), "agentId": "pixel",
            "modelProvider": "local", "modelId": "DeepSeek-V4-Flash-0731", "modelName": "DSV4 Flash 0731",
        })
        control_module.atomic_json(onboarding, configured, 0o600)
        output = json.dumps({
            "status": "ok", "result": {
                "payloads": [{"text": "The read-only audit continued and the optional staging approval is inline."}],
                "meta": chat_meta(
                    {"calls": 0, "failures": 0, "tools": []},
                    provider="local", model="DeepSeek-V4-Flash-0731",
                ),
            },
        }).encode()
        state = control_module.ControlState(
            ROOT, self.base / "state", onboarding, runner=RecordingRunner(output=output),
        )
        request_payload = options["request_payload"]
        state.chat_turn({
            "schemaVersion": 1,
            "requestId": "chatreq-" + pixel.evaluation.sha256(request_payload)[:32],
            "conversationHandle": None, "message": request_payload.decode("utf-8", errors="strict"),
        })
        conversation = json.loads(next(state.chat_conversations.glob("conversation-*.json")).read_text(encoding="utf-8"))
        turn = conversation["turns"][0]
        initial, final = model_proxy_receipts(
            model="DeepSeek-V4-Flash-0731", input_tokens=turn["modelReceipt"]["inputTokens"],
            output_tokens=turn["modelReceipt"]["outputTokens"], tool_count=0,
        )
        source_sha256 = options["source_reference"]["sha256"]
        independent = {
            "schemaVersion": 1, "format": "pixel-neutral-independent-verification-v1",
            "sourceSnapshotSha256": source_sha256, "candidateSha256": "a" * 64, "status": "pass",
            "checks": [
                {"id": "patch-boundary", "kind": "patch-integrity", "status": "pass"},
                {"id": "semantic", "kind": "command", "status": "pass"},
            ],
            "criteria": [{"index": 0, "status": "pass", "checkIds": ["patch-boundary", "semantic"]}],
            "network": "none", "workerSelectedChecks": False, "externalEffects": False,
            "immutablePathViolation": False, "aggregateRuntimeMilliseconds": 10,
            "aggregateOutputBytes": 12, "aggregateWithinBounds": True, "cleanupVerified": True,
            "boundary": pixel.assistant_evidence.INDEPENDENT_BOUNDARY,
        }
        envelope = {
            "schemaVersion": 1, "operation": "pixel-portal-assistant-private-evidence",
            "conversation": conversation, "conversationSha256": control_module.digest(conversation),
            "turnId": turn["turnId"], "toolReceiptBundle": None, "toolReceiptBundleSha256": None,
            "actionJournalChains": [], "modelProxyInitialReceipt": initial, "modelProxyFinalReceipt": final,
            "handoffReceipt": None, "independentVerification": independent,
            "privacy": {
                "conversationTextExported": False, "toolNamesExported": False, "argumentsExported": False,
                "resultsExported": False, "pathsExported": False, "credentialsExported": False,
                "providerIdentifiersExported": False,
            },
            "boundary": pixel.assistant_evidence.ASSISTANT_EVIDENCE_BOUNDARY,
        }
        artifact = json.dumps({
            "stageStatus": "approval-required", "staged": False, "continuedReadOnly": True,
            "approval": {"action": "stage-source", "consequence": "controlled staging", "scope": "repository snapshot only", "options": ["approve-once", "decline"]},
            "findings": [{"id": "F-1", "severity": "medium", "summary": "Dependency pin is stale"}],
            "nextAction": "Review the inline approval if staging is still desired.",
        }, sort_keys=True).encode() + b"\n"
        return {
            "exitCode": 0, "latencyMs": 1500, "finalMessage": turn["assistantText"],
            "usage": {"modelRequests": final["modelRequests"], "inputTokens": final["inputTokens"],
                      "outputTokens": final["outputTokens"], "networkBytes": final["networkBytes"], "toolCalls": 0},
            "authority": {"sourceMutation": False, "merge": False, "deploy": False, "externalEffects": False},
            "interaction": interaction("pixel-assistant-conversation-v1"),
            "independentVerification": independent, "assistantEvidence": envelope,
            "artifacts": [{"kind": "finding-report", "relativePath": "workspace/audit.json", "payload": artifact}],
        }

    def teardown_runtime(self, *, run_id, runtime):
        super().teardown_runtime(run_id=run_id, runtime=runtime)
        self.temporary.cleanup()


def dimensions(evidence, _artifacts):
    item = next(value for value in evidence if value["type"] == "independent-verifier")
    return [{"id": name, "score": 4, "evidencePath": item["relativePath"], "evidenceSha256": item["sha256"]} for name in DIMENSIONS]


class PortalOutcomePixelOrchestrateTests(unittest.TestCase):
    def task_bundle(self, parent):
        original = json.loads((ROOT / "security-evals/agent-comparison/task-battery-v1.json").read_text(encoding="utf-8"))
        first = original["tasks"][0]
        heldout = next(
            item for item in original["tasks"]
            if item.get("profile", "builder") == "builder"
            and item.get("partition", item.get("rehearsal", {}).get("partition", "tuning")) == "held-out"
        )
        original["tasks"] = [first, heldout]
        model, inference = contracts()
        output = parent / "battery"
        materializer.materialize(
            root=ROOT, battery_payload=payload(original), model_payload=model, inference_payload=inference,
            verifier_image_digest="sha256:" + "e" * 64, output_root=output,
        )
        return output / original["tasks"][0]["taskId"] / "task.json", model, inference

    def research_task_bundle(self, parent):
        task_path, model, inference = self.task_bundle(parent)
        task = json.loads(task_path.read_text(encoding="utf-8"))
        tools = {
            "$schema": pixel.outcome_task.TOOL_POLICY_SCHEMA, "schemaVersion": 1,
            "operation": "pixel-portal-outcome-tool-policy", "workspace": "disposable-read-write",
            "tools": ["edit", "public-research", "read", "search", "shell", "write"],
            "brokeredServices": ["local-model", "public-research"], "maximumSubagents": 1,
            "hostAccess": False, "ambientCredentials": False, "externalEffects": False,
            "mergeAuthority": False, "deployAuthority": False, "policyMutation": False,
            "boundary": pixel.outcome_task.TOOL_POLICY_BOUNDARY,
        }
        checks = [
            {"assertionId": "inline-citations", "check": "research-inline-citations"},
            {"assertionId": "primary-source-preference", "check": "research-primary-source-preference"},
            {"assertionId": "citation-entailment", "check": "research-citation-entailment"},
            {"assertionId": "label-stale-or-unknown", "check": "research-label-stale-or-unknown"},
            {"assertionId": "inline-provenance", "check": "research-inline-provenance"},
        ]
        verifier = {
            "$schema": pixel.verifier_engine.VERIFIER_SCHEMA, "operation": pixel.verifier_engine.VERIFIER_OPERATION,
            "schemaVersion": 1, "journeyId": "cited-current-research",
            "acceptanceCriteria": ["Return findings with independently checked public-source citations."],
            "checks": checks, "forbiddenPhrases": [], "finalReply": None, "workspaceVerification": None,
            "boundary": pixel.verifier_engine.VERIFIER_BOUNDARY,
        }
        for key, name, value in (("toolPolicy", "tools.json", tools), ("verifier", "verifier.json", verifier)):
            payload_value = pixel.evaluation.canonical(value) + b"\n"
            (task_path.parent / name).write_bytes(payload_value)
            task["bindings"][key] = {
                "relativePath": name, "sha256": pixel.evaluation.sha256(payload_value),
                "bytes": len(payload_value), "mediaType": "application/json",
            }
        research_fixture = {
            "$schema": pixel.outcome_task.RESEARCH_FIXTURE_SCHEMA,
            "schemaVersion": 1, "operation": "pixel-portal-outcome-research-fixture",
            "observedAt": "2026-08-13T12:00:00Z",
            "sources": [{
                "fixtureSourceId": "source-1", "sourceType": "academic",
                "title": "Bounded public evidence", "snippet": "Exact public evidence passage",
                "quality": "other", "publishedDate": "2026-08-13",
                "retrieval": {"status": "fetched", "content": "Exact public evidence passage"},
            }],
            "authority": {field: False for field in pixel.outcome_task.RESEARCH_FIXTURE_AUTHORITY_FIELDS},
            "boundary": pixel.outcome_task.RESEARCH_FIXTURE_BOUNDARY,
        }
        research_fixture_payload = pixel.evaluation.canonical(research_fixture) + b"\n"
        (task_path.parent / "research-fixture.json").write_bytes(research_fixture_payload)
        task["bindings"]["researchFixture"] = {
            "relativePath": "research-fixture.json", "sha256": pixel.evaluation.sha256(research_fixture_payload),
            "bytes": len(research_fixture_payload), "mediaType": "application/json",
        }
        task["journeyId"] = "cited-current-research"
        task["profile"] = "researcher"
        task["effectBoundary"] = "read-only"
        task["capabilities"] = [
            "artifact-production", "filesystem-read", "filesystem-write", "local-model",
            "process-execution", "public-web", "reasoning",
        ]
        task["dataRoute"] = "brokered-public"
        task_path.write_bytes(pixel.evaluation.canonical(task) + b"\n")
        return task_path, model, inference

    def assistant_task_bundle(self, parent):
        original = json.loads((ROOT / "security-evals/agent-comparison/task-battery-v1.json").read_text(encoding="utf-8"))
        selected = next(item for item in original["tasks"] if item["taskId"] == "battery-repo-config-precedence")
        tuning = next(
            item for item in original["tasks"]
            if item.get("profile", "builder") == "assistant"
            and item.get("partition", item.get("rehearsal", {}).get("partition", "tuning")) == "tuning"
        )
        original["tasks"] = [selected, tuning]
        model, inference = contracts()
        output = parent / "assistant-battery"
        materializer.materialize(
            root=ROOT, battery_payload=payload(original), model_payload=model, inference_payload=inference,
            verifier_image_digest="sha256:" + "e" * 64, output_root=output, profile="assistant",
        )
        return output / selected["taskId"] / "task.json", model, inference

    def controller_task_bundle(self, parent):
        original = json.loads((ROOT / "security-evals/agent-comparison/task-battery-v1.json").read_text(encoding="utf-8"))
        selected = next(item for item in original["tasks"] if item["taskId"] == "battery-trial-controller-harness-analysis")
        heldout = next(
            item for item in original["tasks"]
            if item.get("profile", "builder") == "controller"
            and item.get("partition", item.get("rehearsal", {}).get("partition", "tuning")) == "held-out"
        )
        original["tasks"] = [selected, heldout]
        model, inference = contracts()
        output = parent / "controller-battery"
        materializer.materialize(
            root=ROOT, battery_payload=payload(original), model_payload=model, inference_payload=inference,
            verifier_image_digest="sha256:" + "e" * 64, output_root=output, profile="controller",
        )
        return output / selected["taskId"] / "task.json", model, inference

    def test_full_pixel_composition_produces_common_independently_verified_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            task_path, model, inference = self.task_bundle(parent)
            run_dir = (parent / "run").resolve()
            run_dir.mkdir(mode=0o700)
            system = FakePixelSystem(model, inference)
            times = iter(["2026-08-13T12:00:00Z", "2026-08-13T12:00:02Z"])
            record = pixel.orchestrate_pixel_run(
                system, root=ROOT, task_path=task_path, run_dir=run_dir, run_id=RUN_ID,
                runtime_condition="cold-first-request",
                dimensions=dimensions, clock=lambda: next(times),
            )
            self.assertEqual(record["backend"], "pixel")
            self.assertTrue(record["execution"]["realBackend"])
            self.assertTrue(record["execution"]["realTools"])
            self.assertTrue(all(item["status"] == "pass" for item in record["assertions"]))
            self.assertEqual(system.calls, ["qualify", "control", "run", "teardown"])
            self.assertTrue((run_dir / "run.json").is_file())
            self.assertTrue((run_dir / "evidence/independent-verification.json").is_file())

    def test_malformed_verifier_receipt_fails_and_still_tears_down(self):
        class Malformed(FakePixelSystem):
            def run_pixel(self, **options):
                result = super().run_pixel(**options)
                result["independentVerification"]["workerSelectedChecks"] = True
                return result

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            task_path, model, inference = self.task_bundle(parent)
            run_dir = (parent / "run").resolve()
            run_dir.mkdir(mode=0o700)
            system = Malformed(model, inference)
            with self.assertRaisesRegex(pixel.evaluation.OutcomeError, "identity or boundary"):
                pixel.orchestrate_pixel_run(
                    system, root=ROOT, task_path=task_path, run_dir=run_dir, run_id=RUN_ID,
                    runtime_condition="cold-first-request",
                    dimensions=dimensions, clock=lambda: "2026-08-13T12:00:00Z",
                )
            self.assertEqual(system.calls[-1], "teardown")

    def test_researcher_product_path_emits_exact_provenance_without_overclaiming_quality(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            task_path, model, inference = self.research_task_bundle(parent)
            run_dir = (parent / "research-run").resolve()
            run_dir.mkdir(mode=0o700)
            system = FakeResearchPixelSystem(model, inference)
            times = iter(["2026-08-13T12:00:00Z", "2026-08-13T12:00:04Z"])
            record = pixel.orchestrate_pixel_run(
                system, root=ROOT, task_path=task_path, run_dir=run_dir, run_id=RUN_ID,
                runtime_condition="warm-neutral-probe",
                dimensions=dimensions, clock=lambda: next(times),
            )
            self.assertEqual([item["type"] for item in record["evidence"]], [
                "source-provenance", "observation-time", "citation-coverage", "independent-verifier",
            ])
            statuses = {item["id"]: item["status"] for item in record["assertions"]}
            self.assertEqual(statuses["inline-citations"], "pass")
            self.assertEqual(statuses["inline-provenance"], "pass")
            self.assertEqual(statuses["primary-source-preference"], "fail")
            self.assertEqual(statuses["citation-entailment"], "fail")
            self.assertEqual(statuses["label-stale-or-unknown"], "fail")
            self.assertTrue((run_dir / "evidence/research-source-provenance.json").is_file())
            self.assertNotIn("example.org", (run_dir / "evidence/research-source-provenance.json").read_text(encoding="utf-8"))

    def test_assistant_product_path_emits_snapshot_bound_lineage_route_and_verifier_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            task_path, model, inference = self.assistant_task_bundle(parent)
            run_dir = (parent / "assistant-run").resolve(); run_dir.mkdir(mode=0o700)
            system = FakeAssistantPixelSystem(model, inference)
            times = iter(["2026-08-13T12:00:00Z", "2026-08-13T12:00:02Z"])
            record = pixel.orchestrate_pixel_run(
                system, root=ROOT, task_path=task_path, run_dir=run_dir, run_id=RUN_ID,
                runtime_condition="cold-first-request", dimensions=dimensions, clock=lambda: next(times),
            )
            self.assertEqual(record["execution"]["status"], "completed")
            self.assertEqual({item["type"] for item in record["evidence"]}, {
                "exact-source", "runtime-environment", "command-exit", "checkpoint-lineage", "privacy-route", "independent-verifier",
            })
            self.assertTrue(all(item["status"] == "pass" for item in record["assertions"]))
            exact = json.loads((run_dir / "evidence/assistant-exact-source.json").read_text(encoding="utf-8"))
            task = json.loads(task_path.read_text(encoding="utf-8"))
            self.assertEqual(exact["sourceSnapshotSha256"], task["bindings"]["sourceSnapshot"]["sha256"])
            self.assertEqual(system.calls, ["qualify", "control", "run", "teardown"])

    def test_controller_product_path_requires_real_builder_custody_before_emitting_exact_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            task_path, model, inference = self.controller_task_bundle(parent)
            run_dir = (parent / "controller-run").resolve(); run_dir.mkdir(mode=0o700)
            system = FakeControllerPixelSystem(model, inference)
            times = iter(["2026-08-13T12:00:00Z", "2026-08-13T12:00:02Z"])
            record = pixel.orchestrate_pixel_run(
                system, root=ROOT, task_path=task_path, run_dir=run_dir, run_id=RUN_ID,
                runtime_condition="cold-first-request", dimensions=dimensions, clock=lambda: next(times),
            )
            self.assertEqual({item["type"] for item in record["evidence"]}, {
                "exact-source", "runtime-environment", "artifact-digest", "independent-verifier",
            })
            self.assertTrue(all(item["status"] == "pass" for item in record["assertions"]))
            self.assertTrue((run_dir / "pixel-controller-evidence.json").is_file())
            self.assertEqual(system.calls, ["qualify", "control", "run", "teardown"])

    def test_controller_custody_tamper_fails_closed_and_tears_down(self):
        class TamperedController(FakeControllerPixelSystem):
            def run_pixel(self, **options):
                result = super().run_pixel(**options)
                result["controllerEvidence"]["custody"]["exactLeaseOnly"] = False
                return result

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            task_path, model, inference = self.controller_task_bundle(parent)
            run_dir = (parent / "controller-tamper").resolve(); run_dir.mkdir(mode=0o700)
            system = TamperedController(model, inference)
            with self.assertRaisesRegex(pixel.evaluation.OutcomeError, "custody"):
                pixel.orchestrate_pixel_run(
                    system, root=ROOT, task_path=task_path, run_dir=run_dir, run_id=RUN_ID,
                    runtime_condition="cold-first-request", dimensions=dimensions,
                    clock=lambda: "2026-08-13T12:00:00Z",
                )
            self.assertEqual(system.calls[-1], "teardown")

    def test_researcher_tampered_provenance_fails_and_still_tears_down(self):
        class TamperedResearch(FakeResearchPixelSystem):
            def run_pixel(self, **options):
                result = super().run_pixel(**options)
                result["researchEvidence"]["citationBindings"][0]["contentSha256"] = "a" * 64
                return result

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            task_path, model, inference = self.research_task_bundle(parent)
            run_dir = (parent / "tampered-research-run").resolve()
            run_dir.mkdir(mode=0o700)
            system = TamperedResearch(model, inference)
            with self.assertRaisesRegex(
                pixel.evaluation.OutcomeError,
                "provenance citation does not bind a fetched source",
            ):
                pixel.orchestrate_pixel_run(
                    system, root=ROOT, task_path=task_path, run_dir=run_dir, run_id=RUN_ID,
                    runtime_condition="cold-first-request",
                    dimensions=dimensions, clock=lambda: "2026-08-13T12:00:00Z",
                )
            self.assertEqual(system.calls, ["qualify", "control", "run", "teardown"])
            self.assertFalse((run_dir / "run.json").exists())

    def test_shared_research_collector_accepts_codex_names_but_not_cross_arm_substitution(self):
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary).resolve()
            system = FakeResearchPixelSystem(b"{}", b"{}")
            outcome = system.run_pixel()
            outcome["researchEvidence"]["format"] = "codex-research-provenance-v1"
            artifacts = []
            for item in outcome["artifacts"]:
                relative = item["relativePath"].replace("pixel-research-", "codex-research-")
                target = run_dir / relative
                target.write_bytes(item["payload"])
                artifacts.append({
                    "kind": item["kind"], "relativePath": relative,
                    "sha256": pixel.evaluation.sha256(item["payload"]), "bytes": len(item["payload"]),
                })
            evidence, independent = pixel.research_evidence.validate_and_emit(
                run_dir=run_dir, outcome=outcome, artifacts=artifacts, arm="codex",
            )
            self.assertEqual([item["type"] for item in evidence], [
                "source-provenance", "observation-time", "citation-coverage", "independent-verifier",
            ])
            self.assertEqual(independent["type"], "independent-verifier")
            with self.assertRaisesRegex(pixel.evaluation.OutcomeError, "artifact is missing or substituted"):
                pixel.research_evidence.validate_and_emit(
                    run_dir=run_dir, outcome=outcome, artifacts=artifacts, arm="pixel",
                )

    def test_preflight_runtime_drift_stops_before_task_and_still_tears_down(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            task_path, model, inference = self.task_bundle(parent)
            run_dir = (parent / "run").resolve()
            run_dir.mkdir(mode=0o700)
            system = FakePixelSystem(model, inference)
            bound = pair._PreflightBoundPixelSystem(system, {"workPolicySha256": "0" * 64})
            with self.assertRaisesRegex(pixel.evaluation.OutcomeError, "differs from its exact read-only preflight"):
                pixel.orchestrate_pixel_run(
                    bound, root=ROOT, task_path=task_path, run_dir=run_dir, run_id=RUN_ID,
                    runtime_condition="cold-first-request",
                    dimensions=dimensions, clock=lambda: "2026-08-13T12:00:00Z",
                )
            self.assertEqual(system.calls, ["qualify", "teardown"])
            self.assertFalse((run_dir / "run.json").exists())


if __name__ == "__main__":
    unittest.main()
