#!/usr/bin/env python3
"""Disposable loopback fixture for the sealed chat-to-Deep-Work portal journey."""

import argparse
import importlib.util
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("pixel_control_handoff_fixture", ROOT / "control" / "server.py")
CONTROL = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(CONTROL)


def fixture_state(private_root: Path):
    state = CONTROL.ControlState(ROOT, private_root / "state", private_root / "onboarding.json")
    observed = CONTROL.iso(state.now())
    authority = {
        "grantsExecution": False, "grantsScheduling": False, "grantsLease": False,
        "grantsServiceActivation": False, "grantsExternalEffects": False,
        "grantsCompletion": False, "grantsScopeExpansion": False,
    }
    state.chat = lambda: {
        "schemaVersion": 1, "state": "ready", "activeHandle": None,
        "conversations": [{
            "handle": "chatview-" + "a" * 24, "title": "Repair the client release",
            "createdAt": observed, "updatedAt": observed, "state": "active",
            "turns": [{
                "taskHandle": "chattask-" + "b" * 24, "createdAt": observed,
                "finishedAt": observed, "userText": "Repair the client release and prove every change.",
                "state": "succeeded", "phase": "completed",
                "assistantText": "I inspected the request and prepared it for a durable, independently verified work plan.",
                "toolCalls": 2, "toolFailures": 0,
                "capability": {
                    "state": "complete-local", "routes": ["local-only"], "effects": ["read-only"],
                    "autonomousWithinPolicy": True, "brokerReceiptRequired": False,
                    "brokerReceiptSatisfied": True, "approvalRequired": False,
                    "externalEffectOccurred": False, "ambiguousBrokerCalls": 0,
                    "unclassifiedReportedTools": 0, "toolNamesExposed": False,
                },
                "activity": [
                    {"code": "accepted", "at": observed},
                    {"code": "agent-started", "at": observed},
                    {"code": "response-verified", "at": observed},
                ],
                "handoff": {
                    "receiptHandle": "handoffview-" + "c" * 24, "createdAt": observed,
                    "state": "draft-created", "targetState": "retained",
                    "draftHandle": "workdraftview-" + "d" * 24, "milestoneCount": 2,
                    "profiles": ["builder", "scout"], "dataClassification": "internal",
                    "exactBinding": "settled-turn-record-and-goal-declaration", "authority": authority,
                },
            }, {
                "taskHandle": "chattask-" + "9" * 24, "createdAt": observed,
                "finishedAt": observed, "userText": "Audit the next client release and prove every repair.",
                "state": "succeeded", "phase": "completed",
                "assistantText": "The request is bounded and ready to become a durable work plan.",
                "toolCalls": 1, "toolFailures": 0,
                "capability": {
                    "state": "complete-local", "routes": ["local-only"], "effects": ["read-only"],
                    "autonomousWithinPolicy": True, "brokerReceiptRequired": False,
                    "brokerReceiptSatisfied": True, "approvalRequired": False,
                    "externalEffectOccurred": False, "ambiguousBrokerCalls": 0,
                    "unclassifiedReportedTools": 0, "toolNamesExposed": False,
                },
                "activity": [
                    {"code": "accepted", "at": observed},
                    {"code": "agent-started", "at": observed},
                    {"code": "response-verified", "at": observed},
                ],
                "handoff": None,
            }],
        }],
        "limits": {"maxConversations": 32, "maxTurnsPerConversation": 200, "maxMessageCharacters": 32768},
        "privacy": {
            "reviewTokenRequired": True, "credentialsExposed": False, "pathsExposed": False,
            "rawLauncherOutputExposed": False, "genericCommandSurface": False,
        },
        "boundary": CONTROL.CHAT_PROJECTION_BOUNDARY,
    }
    state.work_authoring = lambda: {
        "schemaVersion": 1, "state": "ready", "revision": "e" * 64,
        "profiles": [{"kind": kind, "enabled": True} for kind in ("inspect", "build", "research", "analyze-data")],
        "inputs": [{
            "handle": "workinput-" + "f" * 24, "label": "Local repository 1",
            "kind": "repository-snapshot", "classification": "internal", "bytes": 1048576,
            "datasetCount": 0, "datasetFormats": [],
        }],
        "limits": {"maxMilestones": 16, "maxCriteriaPerMilestone": 8, "draftsUsed": 1, "maxDrafts": 4},
        "privacy": {
            "pathsExposed": False, "hashesExposed": False, "credentialsExposed": False,
            "genericCommandSurface": False, "browserCanAdmitInputs": False,
            "browserCanExecute": False, "browserCanSchedule": False, "browserCanExpandBoundary": False,
        },
        "boundary": CONTROL.WORK_AUTHORING_BOUNDARY,
    }
    budgets = {
        "maxRuntimeSeconds": 3600, "maxIterations": 8, "maxToolCalls": 200,
        "maxConcurrentSubagents": 2, "maxModelRequests": 40, "maxInputTokens": 200000,
        "maxOutputTokens": 40000, "maxCpuCores": 4, "maxMemoryMiB": 8192,
        "maxDiskBytes": 10737418240, "maxArtifactBytes": 1073741824,
        "maxNetworkBytes": 0, "maxFailures": 4, "noProgressLimit": 3,
    }
    state.work_draft_reviews = lambda: {
        "schemaVersion": 1, "state": "ready", "revision": "e" * 64,
        "drafts": [{
            "handle": "workdraftview-" + "d" * 24, "createdAt": observed,
            "reviewSha256": "1" * 64, "objective": "Repair the client release and prove every change.",
            "dataClassification": "internal", "milestoneCount": 2, "preparationState": "draft",
            "canPrepare": True, "launchPackage": None,
            "milestones": [
                {
                    "number": 1, "profile": "scout",
                    "objective": "Inspect the exact source and establish the failure baseline",
                    "doneWhen": ["Every material defect has reproducible evidence"], "dependsOn": [],
                    "inputCount": 1, "effort": "standard", "budgets": budgets,
                    "capabilities": {"workspace": "read-only-workspace", "tools": ["read", "search"], "brokeredServices": ["local-model"], "modelRoute": "local-only"},
                    "verification": "independent-report-verification", "externalEffects": False,
                },
                {
                    "number": 2, "profile": "builder", "objective": "Patch and verify every confirmed defect",
                    "doneWhen": ["All exact-source release checks pass", "Independent verification finds no open release blocker"],
                    "dependsOn": [1], "inputCount": 1, "effort": "deep", "budgets": budgets,
                    "capabilities": {"workspace": "disposable-read-write", "tools": ["read", "search", "patch", "test"], "brokeredServices": ["local-model"], "modelRoute": "local-only"},
                    "verification": "patch-integrity-and-semantic-review", "externalEffects": False,
                },
            ],
            "authority": {
                "grantsExecution": False, "grantsLease": False, "grantsRetry": False,
                "grantsScheduling": False, "grantsCredentials": False, "grantsScopeExpansion": False,
                "grantsExternalEffects": False, "grantsCompletion": False,
            },
        }],
        "launch": {"state": "ready", "preparedUsed": 0, "maxPrepared": 4},
        "service": {"state": "disabled", "renderedUsed": 0, "maxRendered": 0},
        "privacy": {
            "pathsExposed": False, "inputIdentitiesExposed": False, "credentialsExposed": False,
            "genericCommandSurface": False, "grantsExecution": False, "grantsScheduling": False,
            "grantsExternalEffects": False, "grantsCompletion": False,
        },
        "boundary": CONTROL.WORK_DRAFT_REVIEW_BOUNDARY,
    }
    status = state.status()
    status["operatorActions"].update({"deepWorkPause": True, "deepWorkResume": False, "deepWorkCancel": True})
    status["reviewViews"]["deepWorkSemanticReviews"] = True
    state.status = lambda: status
    permissions = state.permissions()
    for setting in permissions["settings"]:
        if setting["kind"] in {"deep-work-pause", "deep-work-resume", "deep-work-cancel"}:
            setting["mode"] = "always-ask"
            setting["availableModes"] = ["always-ask", "auto-within-policy", "never-allow"] if setting["kind"] == "deep-work-pause" else ["always-ask", "never-allow"]
            setting["privatePolicyEnabled"] = True
            setting["autoEligible"] = setting["kind"] == "deep-work-pause"
    state.permissions = lambda: permissions
    state.deep_work_status = lambda: {
        "schemaVersion": 1, "state": "busy", "generatedAt": observed,
        "summary": {"activeSessions": 1, "attentionSessions": 0, "artifactCount": 2, "degradedServices": 0},
        "goal": {
            "state": "running", "updatedAt": observed,
            "progress": {"milestonesTotal": 2, "milestonesCompleted": 1, "jobsStarted": 2, "failures": 0},
            "usage": {"runtimeSeconds": 420, "modelRequests": 18, "inputTokens": 24000, "outputTokens": 4200, "networkBytes": 0, "artifactBytes": 8192, "failures": 0},
            "budgets": {
                "accounting": "settled-plus-active-observed",
                "used": {"jobs": 2, "runtimeSeconds": 420, "modelRequests": 18, "inputTokens": 24000, "outputTokens": 4200, "networkBytes": 0, "artifactBytes": 8192, "failures": 0},
                "limits": {"jobs": 4, "runtimeSeconds": 3600, "modelRequests": 80, "inputTokens": 200000, "outputTokens": 40000, "networkBytes": 0, "artifactBytes": 1073741824, "failures": 4},
                "remaining": {"jobs": 2, "runtimeSeconds": 3180, "modelRequests": 62, "inputTokens": 176000, "outputTokens": 35800, "networkBytes": 0, "artifactBytes": 1073733632, "failures": 4},
            },
            "continuity": {"checkpointSequence": 9, "restartSafe": True, "completionRequiresIndependentVerification": True, "progressModel": "durable-events", "watchdogRole": "liveness-only"},
            "nextAction": "recover-or-continue-child",
        },
        "sessions": [{
            "current": True, "mode": "builder", "state": "verifying",
            "startedAt": observed, "updatedAt": observed,
            "progress": {"criteriaTotal": 3, "criteriaPassing": 2, "criteriaFailing": 1, "iteration": 3, "maxIterations": 8, "noProgressCount": 0},
            "usage": {"runtimeSeconds": 180, "modelRequests": 8, "inputTokens": 10000, "outputTokens": 1800, "networkBytes": 0, "artifactBytes": 8192, "failures": 0},
            "artifacts": {"count": 2, "totalBytes": 8192, "kinds": [{"kind": "patch", "count": 1}, {"kind": "test-evidence", "count": 1}]},
            "verification": "pending",
            "capability": {"state": "authorized", "toolCount": 2, "singleUseCalls": True, "networkAccess": False, "externalEffects": False},
            "boundaryState": {"state": "within-authority", "requestedExpansion": "none"},
            "activity": [{"at": observed, "category": "verification", "summaryCode": "verification-started", "outcome": "pending"}],
            "controls": {"browserCanPause": False, "browserCanCancel": False, "browserCanResume": False, "browserCanExpandBoundary": False, "browserCanOpenArtifacts": False},
        }],
        "services": [
            {"id": "controller", "state": "ready", "observedAt": observed},
            {"id": "worker", "state": "ready", "observedAt": observed},
            {"id": "verifier", "state": "busy", "observedAt": observed},
            {"id": "capability-adapter", "state": "busy", "observedAt": observed},
        ],
        "controls": {"browserCanPause": False, "browserCanCancel": False, "browserCanResume": False, "browserCanExpandBoundary": False, "browserCanOpenArtifacts": False},
        "privacy": {"objectiveText": False, "promptText": False, "toolArguments": False, "artifactNames": False, "paths": False, "hashes": False, "credentials": False, "providerContent": False},
        "boundary": CONTROL.WORK_OPERATOR_BOUNDARY,
    }
    state.deep_work_semantic_review = lambda: {
        "schemaVersion": 1, "status": "waiting-authority", "profile": "scout",
        "dataClassification": "internal", "objective": "Confirm the release repair is supported by exact evidence.",
        "acceptanceCriteria": ["Every claimed repair has an independently reproduced check."],
        "reviewSha256": "6" * 64,
        "content": {
            "title": "Client release repair evidence", "overview": "One repair is ready for owner review.",
            "methodology": "Pixel reopened the retained source and reran the declared release check.",
            "findings": [{
                "statement": "The repaired release check passed against the retained source.", "material": True,
                "evidence": [{"kind": "local-quote", "reference": "project:test-evidence/release.txt", "excerpt": "release gate passed", "bytes": 19}],
            }],
            "limitations": "This deterministic check does not establish every product behavior.", "artifacts": [],
        },
        "verification": {"status": "evidence-pass", "method": "deterministic-local-evidence-presence", "independent": True, "semanticAccuracyVerified": False, "checkedItems": 1, "failedItems": 0, "explanation": "The exact retained evidence was reopened; final judgment remains with the owner."},
        "privacy": {"privateContentIncluded": True, "relativeEvidenceReferencesIncluded": True, "hostAbsolutePathsIncluded": False, "credentialsIncluded": False, "contentLeavesHost": False},
        "authority": {"grantsExecution": False, "grantsLease": False, "grantsReplay": False, "grantsAcceptance": False, "grantsCompletion": False, "grantsPublication": False, "grantsDeployment": False, "grantsExternalEffects": False, "grantsScopeExpansion": False},
        "boundary": CONTROL.WORK_SEMANTIC_REVIEW_BOUNDARY,
    }
    def request_action(value):
        if not isinstance(value, dict):
            raise CONTROL.PublicRejected("fixture accepts only exact inline Deep Work journeys")
        kind = value.get("kind")
        if kind in {"deep-work-pause", "deep-work-cancel"}:
            spec = CONTROL.ACTION_SPECS[kind]
            preview = {
                "schemaVersion": 1, "actionId": "control-1786550000000-" + ("7" if kind == "deep-work-pause" else "8") * 12,
                "kind": kind, "label": spec["label"], "effect": spec["effect"],
                "expiresAt": CONTROL.iso(state.now() + CONTROL.timedelta(minutes=5)),
                "actionHash": ("7" if kind == "deep-work-pause" else "8") * 64,
                "requiresExactConfirmation": True,
            }
            return {"schemaVersion": 1, "state": "approval-required", "preview": preview}
        if kind != "deep-work-draft" or value.get("chatTaskHandle") != "chattask-" + "9" * 24:
            raise CONTROL.PublicRejected("fixture accepts only exact inline Deep Work journeys")
        preview = {
            "schemaVersion": 1, "actionId": "control-1786550000000-" + "4" * 12,
            "kind": "deep-work-draft", "label": "Deep Work inert draft",
            "effect": "Create one private inert two-milestone draft and bind it to the exact settled chat task. No work will start.",
            "expiresAt": CONTROL.iso(state.now() + CONTROL.timedelta(minutes=5)),
            "actionHash": "5" * 64, "requiresExactConfirmation": True,
        }
        return {"schemaVersion": 1, "state": "approval-required", "preview": preview}
    state.request_action = request_action
    def cancel_action(value):
        expected = {
            "control-1786550000000-" + "4" * 12: "5" * 64,
            "control-1786550000000-" + "7" * 12: "7" * 64,
            "control-1786550000000-" + "8" * 12: "8" * 64,
        }
        if (
            not isinstance(value, dict) or set(value) != {"schemaVersion", "actionId", "actionHash"}
            or value.get("schemaVersion") != 1 or expected.get(value.get("actionId")) != value.get("actionHash")
        ):
            raise CONTROL.PublicRejected("fixture denial must bind one exact inline proposal")
        return {"schemaVersion": 1, "actionId": value["actionId"], "state": "denied"}
    state.cancel_action = cancel_action
    return state


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8768)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="pixel-handoff-ui-") as directory:
        server = CONTROL.ControlServer(
            ("127.0.0.1", args.port), fixture_state(Path(directory)), review_token="R" * 43,
        )
        print(f"http://127.0.0.1:{args.port}/#review={'R' * 43}", flush=True)
        try:
            server.serve_forever(poll_interval=0.05)
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
