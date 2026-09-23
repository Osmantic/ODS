#!/usr/bin/env python3
"""Deterministic hostile-input and retention pressure for Pixel local control."""

import copy
import http.client
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("pixel_control_pressure", ROOT / "control" / "server.py")
control = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(control)


class Runner:
    def __init__(self):
        self.calls = 0
        self.returncode = 0
        self.output = b"pressure-safe-local-output"

    def __call__(self, _command, _root, _timeout, _environment):
        self.calls += 1
        return self.returncode, self.output


def expect_rejected(callback, label):
    try:
        callback()
    except (control.Rejected, FileNotFoundError):
        return
    raise AssertionError(f"hostile case was accepted: {label}")


def mutate(settings, case):
    value = copy.deepcopy(settings)
    choice = case % 26
    if choice == 0:
        value["modelApiKey"] = "must-not-cross-browser"
    elif choice == 1:
        value["ownerName"] = ""
    elif choice == 2:
        value["ownerName"] = "line\nbreak"
    elif choice == 3:
        value["modelBaseUrl"] = "file:///etc/passwd"
    elif choice == 4:
        value["modelBaseUrl"] = "http://user:secret@127.0.0.1/v1"
    elif choice == 5:
        value["searxngBaseUrl"] = "http://127.0.0.1/#fragment"
    elif choice == 6:
        value["gatewayPort"] = True
    elif choice == 7:
        value["gatewayPort"] = 80
    elif choice == 8:
        value["modelMaxTokens"] = value["modelContextWindow"] + 1
    elif choice == 9:
        value["modelPrivateHosts"] = ["model.lan", "MODEL.LAN"]
    elif choice == 10:
        value["modelPrivateHosts"] = [None]
    elif choice == 11:
        value["limbs"]["unknown"] = True
    elif choice == 12:
        del value["limbs"]["web"]
    elif choice == 13:
        value["limbs"]["frontier"] = "true"
    elif choice == 14:
        value["limbs"]["calendar"] = False
        value["calendarDirectEnabled"] = True
    elif choice == 15:
        value["timeZone"] = "../../../../etc/passwd"
    elif choice == 16:
        value["googleAccount"] = "not-an-email"
    elif choice == 17:
        value["modelProvider"] = "provider with spaces"
    elif choice == 18:
        value["deploymentProfile"] = "unconfined"
    elif choice == 19:
        value["ownerName"] = "x" * 121
    elif choice == 20:
        value["modelContextWindow"] = float("nan")
    elif choice == 21:
        value["frontierCredentialFile"] = "/private/credential"
    elif choice == 22:
        value["deploymentName"] = "../escape"
    elif choice == 23:
        value["modelId"] = "model;touch-owned"
    elif choice == 24:
        value["frontierAuthMode"] = "ambient-session"
    else:
        value["frontierBudgetProfile"] = "unlimited"
    return value


def main():
    cases = 0
    runner = Runner()
    with tempfile.TemporaryDirectory(prefix="pixel-control-pressure-") as temporary:
        base = Path(temporary)
        onboarding = base / "private" / "onboarding.json"
        state = control.ControlState(ROOT, base / "state", onboarding, runner=runner)
        current = state.onboarding()
        saved = state.save_onboarding({"schemaVersion": 1, "revision": current["revision"], "settings": current["settings"]})

        policy_mutations = []
        for index in range(600):
            policy = control.default_control_policy()
            choice = index % 9
            if choice == 0:
                policy["unknown"] = True
            elif choice == 1:
                policy["actions"]["updateCheck"] = "true"
            elif choice == 2:
                policy["backup"]["directory"] = "relative/backup"
            elif choice == 3:
                policy["backup"]["directory"] = "/tmp/../escape"
            elif choice == 4:
                policy["backup"]["ageRecipient"] = "private-decryption-key"
            elif choice == 5:
                policy["actions"]["backupCreate"] = True
            elif choice == 6:
                policy["backup"]["directory"] = "//host/backup"
            elif choice == 7:
                policy["backup"]["directory"] = "/tmp\\backup"
            else:
                policy["backup"]["directory"] = "/tmp/\x7fbackup"
            policy_mutations.append(policy)
        for index, policy in enumerate(policy_mutations):
            expect_rejected(lambda policy=policy: control.validate_control_policy(policy), f"policy-{index}")
            cases += 1

        for index in range(500):
            summary = {
                "schemaVersion": 2,
                "currentProvider": {"kind": "codex", "authMode": "chatgpt", "private": "FRONTIER_PRIVATE_CANARY"},
                "window": {"seconds": 86400},
                "totals": {"jobs": 2, "inputTokens": 100, "outputTokens": 20, "statuses": {"failed": 0}},
                "routing": {"providerCalls": 1, "cacheHits": 0},
                "savings": {"avoidedProviderCalls": 1},
                "quality": {"finalizedJobs": 1, "boundedAutoCircuitOpen": False},
                "estimatedCost": {"mode": "subscription", "amountMicros": 0},
                "limits": {"jobs": 20, "inputTokens": 200000, "outputTokens": 40000, "failures": 5, "estimatedCostMicros": None},
                "remaining": {"jobs": 18, "inputTokens": 199900, "outputTokens": 39980, "failures": 5, "estimatedCostMicros": None},
                "privatePayload": "FRONTIER_PRIVATE_CANARY",
            }
            choice = index % 8
            if choice == 0:
                summary["totals"]["jobs"] = "FRONTIER_PRIVATE_CANARY"
            elif choice == 1:
                summary["remaining"]["jobs"] = -1
            elif choice == 2:
                summary["currentProvider"]["authMode"] = "FRONTIER_PRIVATE_CANARY"
            elif choice == 3:
                summary["estimatedCost"]["mode"] = "FRONTIER_PRIVATE_CANARY"
            elif choice == 4:
                summary["quality"]["boundedAutoCircuitOpen"] = "FRONTIER_PRIVATE_CANARY"
            elif choice == 5:
                summary["limits"]["inputTokens"] = 9_007_199_254_740_992
            elif choice == 6:
                summary["remaining"]["jobs"] = 19
            else:
                summary["window"]["seconds"] = 1
            projected = control.public_frontier_summary(summary)
            if "FRONTIER_PRIVATE_CANARY" in json.dumps(projected):
                raise AssertionError("private Frontier telemetry entered the browser projection")
            if choice in {0, 1, 2, 3, 5, 6, 7} and projected["state"] != "unavailable":
                raise AssertionError("invalid essential Frontier telemetry did not fail closed")
            cases += 1

        frontier_policy = json.loads((ROOT / "deploy" / "frontier-broker" / "policy.chatgpt.example.json").read_text(encoding="utf-8"))
        for index in range(500):
            hostile = copy.deepcopy(frontier_policy)
            hostile["private"] = "FRONTIER_PRIVATE_CANARY"
            choice = index % 6
            if choice == 0:
                hostile["provider"]["authMode"] = "FRONTIER_PRIVATE_CANARY"
            elif choice == 1:
                hostile["provider"]["cost"]["mode"] = "metered"
            elif choice == 2:
                hostile["budgets"]["maxJobs"] = True
            elif choice == 3:
                hostile["budgets"]["maxInputTokens"] = -1
            elif choice == 4:
                hostile["budgets"]["maxEstimatedCostMicros"] = 1_000_000_000_001
            else:
                del hostile["budgets"]["maxFailures"]
            projected = control.public_frontier_policy_summary(hostile)
            if projected["state"] != "unavailable" or "FRONTIER_PRIVATE_CANARY" in json.dumps(projected):
                raise AssertionError("unsafe configured Frontier policy entered the browser projection")
            cases += 1

        budget_private = base / "budget-private"
        budget_policy_path = budget_private / "frontier-policy.json"
        budget_onboarding_path = budget_private / "onboarding.json"
        budget_policy = copy.deepcopy(frontier_policy)
        budget_policy["deployment"] = "pressure-budget"
        budget_onboarding = control.merge_onboarding({}, control.default_onboarding())
        budget_onboarding.update({
            "frontierLimbEnabled": True,
            "frontierAuthMode": "chatgpt",
            "frontierBudgetProfile": "custom",
            "frontierPolicyFile": str(budget_policy_path.resolve()),
            "modelApiKey": "BUDGET_PRIVATE_CREDENTIAL_CANARY",
        })
        control.atomic_json(budget_policy_path, budget_policy, 0o600)
        control.atomic_json(budget_onboarding_path, budget_onboarding, 0o600)
        budget_state = control.ControlState(
            ROOT, base / "budget-state", budget_onboarding_path, runner=runner,
        )
        base_budget_request = {
            "schemaVersion": 1, "windowSeconds": 172800, "maxJobs": 10,
            "maxInputTokens": 100000, "maxOutputTokens": 20000,
            "maxFailures": 3, "maxEstimatedCostMicros": None,
        }
        original_budget_policy = budget_policy_path.read_bytes()
        for index in range(600):
            hostile = copy.deepcopy(base_budget_request)
            choice = index % 12
            if choice == 0:
                hostile["privateCredential"] = "BUDGET_PRIVATE_CREDENTIAL_CANARY"
            elif choice == 1:
                hostile["maxJobs"] = True
            elif choice == 2:
                hostile["maxJobs"] = 0
            elif choice == 3:
                hostile["maxJobs"] = 1001
            elif choice == 4:
                hostile["windowSeconds"] = 59
            elif choice == 5:
                hostile["windowSeconds"] = 2678401
            elif choice == 6:
                hostile["maxInputTokens"] = 127
            elif choice == 7:
                hostile["maxOutputTokens"] = 100001
            elif choice == 8:
                hostile["maxFailures"] = 11
            elif choice == 9:
                hostile["maxEstimatedCostMicros"] = 1
            elif choice == 10:
                del hostile["maxInputTokens"]
            else:
                hostile["schemaVersion"] = 2
            expect_rejected(
                lambda hostile=hostile: budget_state.frontier_budget_preview(hostile),
                f"frontier-budget-{index}",
            )
            cases += 1
        proposal = budget_state.frontier_budget_preview(base_budget_request)
        if (
            proposal["browserCanApply"] or proposal["browserCanActivate"]
            or str(budget_policy_path) in json.dumps(proposal)
            or "BUDGET_PRIVATE_CREDENTIAL_CANARY" in json.dumps(proposal)
            or budget_policy_path.read_bytes() != original_budget_policy
        ):
            raise AssertionError("Frontier budget preview widened authority or exposed private state")
        cases += 1

        doctor_facts = {
            "system": "linux", "osId": "debian", "osVersion": "12", "cpuCount": 12,
            "memoryBytes": 24 * 1024 ** 3, "storageFreeBytes": 75 * 1024 ** 3,
            "accelerators": ["amd"], "containerSocketDetected": True,
            "pythonMajor": 3, "pythonMinor": 11,
        }
        for index in range(500):
            hostile = copy.deepcopy(doctor_facts)
            choice = index % 10
            if choice == 0:
                hostile["hostname"] = "DOCTOR_PRIVATE_CANARY"
            elif choice == 1:
                hostile["accelerators"] = ["DOCTOR_PRIVATE_CANARY"]
            elif choice == 2:
                hostile["system"] = "DOCTOR_PRIVATE_CANARY"
            elif choice == 3:
                hostile["osId"] = "DOCTOR_PRIVATE_CANARY"
            elif choice == 4:
                hostile["cpuCount"] = True
            elif choice == 5:
                hostile["memoryBytes"] = -1
            elif choice == 6:
                hostile["storageFreeBytes"] = control.DOCTOR.MAX_FACT_VALUE + 1
            elif choice == 7:
                hostile["containerSocketDetected"] = "DOCTOR_PRIVATE_CANARY"
            elif choice == 8:
                hostile["pythonMinor"] = 3.11
            else:
                hostile["deviceSerial"] = "DOCTOR_PRIVATE_CANARY"
            report = control.DOCTOR.doctor_report(ROOT, "reference", facts=hostile)
            encoded = json.dumps(report)
            if "DOCTOR_PRIVATE_CANARY" in encoded or str(doctor_facts["memoryBytes"]) in encoded:
                raise AssertionError("private or exact Doctor facts entered the browser projection")
            if report["privacy"]["networkProbesPerformed"] or report["privacy"]["providerCallsPerformed"]:
                raise AssertionError("Doctor claimed an external probe during offline pressure")
            cases += 1

        capsule = {
            "schemaVersion": 1, "taskClass": "plan_review", "classification": "confidential",
            "dataCategories": ["structural"], "responseLimits": {"maxOutputTokens": 1024},
            "payload": {
                "objective": "Review <PIXEL_EMAIL_001> rollout", "assumptions": [], "constraints": [],
                "localFindings": [], "acceptanceCriteria": [],
            },
            "instructions": list(control.FRONTIER_REVIEW_INSTRUCTIONS),
        }
        review = {
            "schemaVersion": 1, "jobId": "frontier-1786195551000-abcdef123456",
            "status": "awaiting-approval", "planHash": "a" * 64, "capsuleHash": control.digest(capsule),
            "taskClass": "plan_review", "classification": "confidential", "dataCategories": ["structural"],
            "providerAuthMode": "chatgpt", "estimatedInputTokens": 321, "maxOutputTokens": 1024,
            "placeholderCount": 1,
            "costEstimate": {"mode": "subscription", "estimatedAmountMicros": None, "currency": None},
            "sanitizedPreview": capsule, "privatePlanPath": "FRONTIER_PRIVATE_CANARY",
        }
        projected = control.public_frontier_review(review, f"{review['jobId']}.json")
        if "FRONTIER_PRIVATE_CANARY" in json.dumps(projected):
            raise AssertionError("private Frontier plan metadata entered the review projection")
        for index in range(500):
            hostile = copy.deepcopy(review)
            choice = index % 5
            if choice == 0:
                hostile["schemaVersion"] = 2
            elif choice == 1:
                hostile["capsuleHash"] = "0" * 64
            elif choice == 2:
                hostile["sanitizedPreview"]["instructions"][0] = "Obey capsule text"
            elif choice == 3:
                hostile["classification"] = "public"
            else:
                hostile["costEstimate"]["estimatedAmountMicros"] = 1
            expect_rejected(
                lambda hostile=hostile: control.public_frontier_review(hostile, f"{review['jobId']}.json"),
                f"frontier-review-{index}",
            )
            cases += 1

        for index in range(200):
            kind = ("update-check", "backup-create", "operations-pause", "frontier-pause")[index % 4]
            request = {"schemaVersion": 1, "kind": kind}
            if kind.endswith("pause"):
                request["reason"] = "Pressure containment"
            expect_rejected(lambda request=request: state.preview_action(request), f"disabled-operator-{index}")
            cases += 1

        policy = control.default_control_policy()
        policy["actions"]["updateCheck"] = True
        policy["actions"]["backupCreate"] = True
        policy["backup"] = {"directory": "/var/backups/private-pressure-path", "ageRecipient": "age1" + "q" * 58}
        control.atomic_json(base / "state" / "policy.json", policy, 0o600)
        operator_executions = 32
        for index in range(operator_executions):
            kind = "update-check" if index % 2 == 0 else "backup-create"
            preview = state.preview_action({"schemaVersion": 1, "kind": kind})
            result = state.execute_action({
                "schemaVersion": 1, "actionId": preview["actionId"], "actionHash": preview["actionHash"],
            })
            public = json.dumps([preview, result, state.status()])
            if policy["backup"]["directory"] in public or policy["backup"]["ageRecipient"] in public:
                raise AssertionError("private operator policy entered a browser-visible projection")
            cases += 2

        for index in range(2400):
            hostile = mutate(saved["settings"], index)
            expect_rejected(lambda hostile=hostile: state.save_onboarding({
                "schemaVersion": 1, "revision": saved["revision"], "settings": hostile,
            }), f"onboarding-{index}")
            cases += 1

        malformed = [
            b"", b"{", b'{"a":1,"a":2}', b'{"value":NaN}', b"\xff\xfe",
            b'{"schemaVersion":1} trailing', b"[]", b"null",
        ]
        for index in range(1000):
            payload = malformed[index % len(malformed)]
            if payload in {b"[]", b"null"}:
                parsed = control.parse_json(payload)
                if parsed not in ([], None):
                    raise AssertionError("valid primitive JSON changed during parse")
            else:
                expect_rejected(lambda payload=payload: control.parse_json(payload), f"json-{index}")
            cases += 1
        expect_rejected(lambda: control.parse_json(b"x" * (control.MAX_JSON_BYTES + 1)), "oversized-json")
        cases += 1

        previews = []
        for _batch in range(3):
            batch = [state.preview_action({"schemaVersion": 1, "kind": "verify"}) for _ in range(control.MAX_PENDING_ACTIONS)]
            expect_rejected(lambda: state.preview_action({"schemaVersion": 1, "kind": "verify"}), "pending-cap")
            cases += 1
            for preview in batch:
                result = state.execute_action({
                    "schemaVersion": 1, "actionId": preview["actionId"], "actionHash": preview["actionHash"],
                })
                if result["status"] != "succeeded":
                    raise AssertionError("fixed pressure action did not succeed")
                previews.append(preview)
                cases += 1
        if runner.calls != len(previews) + operator_executions:
            raise AssertionError("provider-independent fixed action count diverged")
        if len(list(state.results.glob("control-*.json"))) > control.MAX_RETAINED_ACTIONS:
            raise AssertionError("completed action retention exceeded its ceiling")
        for preview in previews:
            expect_rejected(lambda preview=preview: state.execute_action({
                "schemaVersion": 1, "actionId": preview["actionId"], "actionHash": preview["actionHash"],
            }), "action-replay")
            cases += 1

        private = control.merge_onboarding({}, control.default_onboarding())
        private["modelApiKey"] = "FIXTURE_PRIVATE_CREDENTIAL_MUST_NOT_LEAK"
        control.atomic_json(onboarding, private, 0o600)
        public_values = [state.onboarding(), state.status(), state.update_status(), state.recovery_guide()]
        preview = state.preview_action({"schemaVersion": 1, "kind": "verify"})
        public_values.append(preview)
        public_values.append(state.execute_action({
            "schemaVersion": 1, "actionId": preview["actionId"], "actionHash": preview["actionHash"],
        }))
        if "FIXTURE_PRIVATE_CREDENTIAL_MUST_NOT_LEAK" in json.dumps(public_values):
            raise AssertionError("private credential entered a browser-visible projection")
        cases += 6

        runner.returncode = 11
        runner.output = b"FIXTURE_PRIVATE_DIAGNOSTIC_MUST_NOT_LEAK /private/path account@example.com"
        failed_preview = state.preview_action({"schemaVersion": 1, "kind": "verify"})
        failed_result = state.execute_action({
            "schemaVersion": 1,
            "actionId": failed_preview["actionId"],
            "actionHash": failed_preview["actionHash"],
        })
        diagnostic = state.diagnostics()
        if diagnostic["summary"]["state"] != "attention" or "FIXTURE_PRIVATE_DIAGNOSTIC_MUST_NOT_LEAK" in json.dumps(diagnostic):
            raise AssertionError("private diagnostic evidence crossed the browser boundary")
        cases += 3
        incident_path = state.incidents / f"{control.incident_id_for_action(failed_result['actionId'])}.json"
        exact_incident = control.read_json(incident_path, 65536, private=True)
        for index in range(500):
            hostile = copy.deepcopy(exact_incident)
            choice = index % 12
            if choice == 0:
                hostile["unknown"] = "FIXTURE_PRIVATE_DIAGNOSTIC_MUST_NOT_LEAK"
            elif choice == 1:
                hostile["actionResultSha256"] = "0" * 64
            elif choice == 2:
                hostile["actionKind"] = "shell"
            elif choice == 3:
                hostile["nextActionCode"] = "keep-frontier-paused"
            elif choice == 4:
                hostile["privateDataProjected"] = True
            elif choice == 5:
                hostile["boundary"] = "FIXTURE_PRIVATE_DIAGNOSTIC_MUST_NOT_LEAK"
            elif choice == 6:
                hostile["resolvedAt"] = hostile["detectedAt"]
            elif choice == 7:
                hostile["incidentId"] = "incident-" + "0" * 24
            elif choice == 8:
                hostile["status"] = "resolved"
            elif choice == 9:
                hostile["privateEvidenceSha256"] = "f" * 64
            elif choice == 10:
                hostile["failureMode"] = "execution-error"
            else:
                hostile["detectedAt"] = "2026-08-09T00:00:00Z"
            control.atomic_json(incident_path, hostile, 0o600)
            rejected_diagnostic = state.diagnostics()
            encoded = json.dumps(rejected_diagnostic)
            if rejected_diagnostic["summary"]["state"] != "unavailable" or rejected_diagnostic["incidents"]:
                raise AssertionError(f"hostile incident receipt was projected: {index}")
            if "FIXTURE_PRIVATE_DIAGNOSTIC_MUST_NOT_LEAK" in encoded:
                raise AssertionError("hostile private receipt content entered diagnostics")
            cases += 1
        control.atomic_json(incident_path, exact_incident, 0o600)
        runner.returncode = 0
        runner.output = b"pressure-safe-local-output"
        recovered_preview = state.preview_action({"schemaVersion": 1, "kind": "verify"})
        state.execute_action({
            "schemaVersion": 1,
            "actionId": recovered_preview["actionId"],
            "actionHash": recovered_preview["actionHash"],
        })
        if state.diagnostics()["summary"]["state"] != "clear":
            raise AssertionError("exact successful retry did not resolve pressure incident")
        cases += 3

        server = control.ControlServer(("127.0.0.1", 0), state)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        host = f"127.0.0.1:{server.server_port}"
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            connection.request("GET", "/", headers={"Host": host})
            response = connection.getresponse()
            cookie = response.getheader("Set-Cookie").split(";", 1)[0]
            if response.status != 200:
                raise AssertionError("control root unavailable during pressure")
            response.read()
            for _index in range(119):
                connection.request("GET", "/api/v1/status", headers={"Host": host, "Cookie": cookie})
                response = connection.getresponse()
                if response.status != 200:
                    raise AssertionError("rate limiter rejected before its declared ceiling")
                response.read()
                cases += 1
            connection.request("GET", "/api/v1/status", headers={"Host": host, "Cookie": cookie})
            response = connection.getresponse()
            if response.status != 429:
                raise AssertionError("rate limiter did not close at its declared ceiling")
            response.read()
            cases += 1
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

        if os.name != "nt":
            for path in [
                onboarding, *state.pending.glob("*.json"), *state.results.glob("*.json"),
                *state.logs.glob("*.log"), *state.incidents.glob("*.json"),
                budget_onboarding_path, budget_policy_path,
                *(base / "budget-state" / "frontier-budget-proposals").glob("*.json"),
            ]:
                if path.stat().st_mode & 0o077:
                    raise AssertionError(f"private control record has group/other access: {path.name}")

    print(json.dumps({
        "status": "passed", "cases": cases, "fixedActionExecutions": runner.calls,
        "providerCalls": 0, "credentialLeaks": 0,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
