#!/usr/bin/env python3
"""Execute one sha-pinned deterministic verifier over exact run evidence."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

import portal_outcome_evaluation as evaluation


VERIFIER_OPERATION = "pixel-portal-outcome-deterministic-verifier"
VERIFIER_SCHEMA = "https://osmantic.com/pixel/schemas/portal-outcome-verifier-v1.schema.json"
VERIFIER_BOUNDARY = (
    "Private controller-selected verifier definition only. It binds semantic acceptance to independent checks "
    "and grants no worker-selected test, execution, network, external-effect, merge, deployment, publication, "
    "completion, acceptance, or promotion authority."
)
WORKSPACE_BOUNDARY = (
    "Immutable controller-selected checks only. Worker output cannot alter criteria, commands, protected paths, "
    "budgets, network, or pass/fail rules."
)
CHECK_KINDS = {
    "evidence-binds-source-snapshot", "command-exit-zero", "artifact-parses",
    "artifact-language-bounded", "evidence-covers-journey", "workspace-verification-passes",
    "final-reply-exact",
    "research-inline-citations", "research-primary-source-preference",
    "research-citation-entailment", "research-label-stale-or-unknown", "research-inline-provenance",
}
MAX_FORBIDDEN_PHRASES = 64
ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
RELATIVE_RE = re.compile(r"^[A-Za-z0-9._-]{1,255}(?:/[A-Za-z0-9._-]{1,255})*$")
IMMUTABLE_RE = re.compile(r"^[A-Za-z0-9._-]{1,255}(?:/[A-Za-z0-9._-]{1,255})*/?$")
EXECUTABLE_RE = re.compile(r"^/(?:[A-Za-z0-9._+-]+/)*[A-Za-z0-9._+-]+$")


def load_definition(payload: bytes) -> dict[str, Any]:
    value = evaluation.parse_json(payload, "deterministic verifier definition")
    value = evaluation.exact_fields(value, {
        "$schema", "operation", "schemaVersion", "journeyId", "acceptanceCriteria", "checks",
        "forbiddenPhrases", "finalReply", "workspaceVerification", "boundary",
    }, "deterministic verifier definition")
    if (
        value["$schema"] != VERIFIER_SCHEMA or value["operation"] != VERIFIER_OPERATION
        or value["schemaVersion"] != 1 or not isinstance(value["journeyId"], str)
        or value["boundary"] != VERIFIER_BOUNDARY
    ):
        raise evaluation.OutcomeError("deterministic verifier identity is invalid")
    criteria = value["acceptanceCriteria"]
    if (
        not isinstance(criteria, list) or not 1 <= len(criteria) <= 32 or len(set(criteria)) != len(criteria)
        or any(not isinstance(item, str) or not 1 <= len(item) <= 1000 for item in criteria)
    ):
        raise evaluation.OutcomeError("deterministic verifier acceptance criteria are invalid")
    checks = value["checks"]
    if not isinstance(checks, list) or not 1 <= len(checks) <= 32:
        raise evaluation.OutcomeError("deterministic verifier has no bounded checks")
    seen: set[str] = set()
    for index, item in enumerate(checks):
        item = evaluation.exact_fields(item, {"assertionId", "check"}, f"verifier check {index}")
        if (
            not isinstance(item["assertionId"], str) or not isinstance(item["check"], str)
            or item["check"] not in CHECK_KINDS or item["assertionId"] in seen
        ):
            raise evaluation.OutcomeError("deterministic verifier check is unknown or duplicated")
        seen.add(item["assertionId"])
    phrases = value["forbiddenPhrases"]
    if not isinstance(phrases, list) or len(phrases) > MAX_FORBIDDEN_PHRASES or any(
        not isinstance(item, str) or not 1 <= len(item) <= 256 for item in phrases
    ):
        raise evaluation.OutcomeError("deterministic verifier phrase list is invalid")
    final_reply = value["finalReply"]
    if final_reply is not None:
        final_reply = evaluation.exact_fields(
            final_reply, {"sha256", "bytes", "normalization"}, "deterministic verifier final reply",
        )
        evaluation.valid_hash(final_reply["sha256"], "deterministic verifier final reply digest")
        evaluation.integer(final_reply["bytes"], 1, 65536, "deterministic verifier final reply bytes")
        if final_reply["normalization"] != "exact-utf8":
            raise evaluation.OutcomeError("deterministic verifier final reply normalization is invalid")
        if not any(item["check"] == "final-reply-exact" for item in checks):
            raise evaluation.OutcomeError("deterministic verifier final reply is not scored")
    elif any(item["check"] == "final-reply-exact" for item in checks):
        raise evaluation.OutcomeError("deterministic verifier scores an undeclared final reply")
    workspace = value["workspaceVerification"]
    if workspace is not None:
        workspace = evaluation.exact_fields(workspace, {
            "mode", "checks", "immutablePathPrefixes", "maxRuntimeSeconds", "maxOutputBytes", "network", "boundary",
        }, "workspace verification")
        if workspace["mode"] != "independent" or workspace["network"] != "none" or workspace["boundary"] != WORKSPACE_BOUNDARY:
            raise evaluation.OutcomeError("workspace verification boundary is invalid")
        verification_checks = workspace["checks"]
        if not isinstance(verification_checks, list) or not 1 <= len(verification_checks) <= 16:
            raise evaluation.OutcomeError("workspace verification has no bounded checks")
        check_ids: set[str] = set()
        covered: set[int] = set()
        for index, check in enumerate(verification_checks):
            if not isinstance(check, dict) or check.get("kind") not in {"patch-integrity", "command"}:
                raise evaluation.OutcomeError("workspace verification check kind is invalid")
            expected_fields = {"id", "kind", "criterionIndexes"} if check["kind"] == "patch-integrity" else {
                "id", "kind", "criterionIndexes", "workingDirectory", "argv", "timeoutSeconds", "maxOutputBytes",
            }
            check = evaluation.exact_fields(check, expected_fields, f"workspace verification check {index}")
            if not isinstance(check["id"], str) or ID_RE.fullmatch(check["id"]) is None or check["id"] in check_ids:
                raise evaluation.OutcomeError("workspace verification check identity is invalid or duplicated")
            check_ids.add(check["id"])
            indexes = check["criterionIndexes"]
            if (
                not isinstance(indexes, list) or not 1 <= len(indexes) <= 32 or len(set(indexes)) != len(indexes)
                or indexes != sorted(indexes) or any(type(item) is not int or item < 0 or item >= len(criteria) for item in indexes)
            ):
                raise evaluation.OutcomeError("workspace verification criterion mapping is invalid")
            covered.update(indexes)
            if check["kind"] == "command":
                argv = check["argv"]
                if (
                    not isinstance(check["workingDirectory"], str) or RELATIVE_RE.fullmatch(check["workingDirectory"]) is None
                    or not isinstance(argv, list) or not 1 <= len(argv) <= 64
                    or any(not isinstance(arg, str) or not 1 <= len(arg) <= 4096 or "\x00" in arg for arg in argv)
                    or EXECUTABLE_RE.fullmatch(argv[0]) is None
                ):
                    raise evaluation.OutcomeError("workspace verification command is invalid")
                evaluation.integer(check["timeoutSeconds"], 1, 3600, "workspace verification timeout")
                evaluation.integer(check["maxOutputBytes"], 1, 16777216, "workspace verification output ceiling")
        if covered != set(range(len(criteria))):
            raise evaluation.OutcomeError("workspace verification does not cover every acceptance criterion")
        prefixes = workspace["immutablePathPrefixes"]
        if (
            not isinstance(prefixes, list) or len(prefixes) > 64 or len(set(prefixes)) != len(prefixes)
            or prefixes != sorted(prefixes)
            or any(not isinstance(prefix, str) or IMMUTABLE_RE.fullmatch(prefix) is None for prefix in prefixes)
        ):
            raise evaluation.OutcomeError("workspace verification immutable paths are invalid")
        evaluation.integer(workspace["maxRuntimeSeconds"], 1, 3600, "workspace verification aggregate runtime")
        evaluation.integer(workspace["maxOutputBytes"], 1, 16777216, "workspace verification aggregate output")
        command_runtime = sum(check.get("timeoutSeconds", 0) for check in verification_checks)
        command_output = sum(check.get("maxOutputBytes", 0) for check in verification_checks)
        if command_runtime > workspace["maxRuntimeSeconds"] or command_output > workspace["maxOutputBytes"]:
            raise evaluation.OutcomeError("workspace verification command ceilings exceed their aggregate boundary")
    return value


def evidence_by_type(run_dir: Path, evidence: list[dict[str, Any]]) -> dict[str, tuple[dict[str, Any], bytes]]:
    loaded: dict[str, tuple[dict[str, Any], bytes]] = {}
    for item in evidence:
        payload = evaluation.evidence_bytes(
            run_dir / "run.json", item["relativePath"], item["sha256"], item["bytes"],
            f"verifier evidence {item['type']}",
        )
        loaded[item["type"]] = (item, payload)
    return loaded


def run_checks(
    definition: dict[str, Any],
    *,
    journey: dict[str, Any],
    admission: dict[str, Any],
    run_dir: Path,
    evidence: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if definition["journeyId"] != admission["journeyId"]:
        raise evaluation.OutcomeError("deterministic verifier is bound to a different journey")
    declared = {item["assertionId"] for item in definition["checks"]}
    if declared != set(journey.get("assertions", [])):
        raise evaluation.OutcomeError("deterministic verifier does not cover the exact journey assertions")
    loaded = evidence_by_type(run_dir, evidence)
    binding_types = {
        "evidence-binds-source-snapshot": "exact-source",
        "command-exit-zero": "command-exit",
        "artifact-parses": "artifact-digest",
        "artifact-language-bounded": "artifact-digest",
        "evidence-covers-journey": "runtime-environment",
        "workspace-verification-passes": "independent-verifier",
        "final-reply-exact": "command-exit",
        "research-inline-citations": "citation-coverage",
        "research-primary-source-preference": "source-provenance",
        "research-citation-entailment": "citation-coverage",
        "research-label-stale-or-unknown": "observation-time",
        "research-inline-provenance": "source-provenance",
    }
    results = []
    for item in definition["checks"]:
        kind = item["check"]
        status = "fail"
        if kind == "evidence-binds-source-snapshot":
            entry = loaded.get("exact-source")
            if entry is not None:
                value = evaluation.parse_json(entry[1], "exact-source evidence")
                if isinstance(value, dict) and value.get("sourceSnapshotSha256") == admission["bindings"]["sourceSnapshotSha256"]:
                    status = "pass"
        elif kind == "command-exit-zero":
            entry = loaded.get("command-exit")
            if entry is not None:
                value = evaluation.parse_json(entry[1], "command-exit evidence")
                if isinstance(value, dict) and value.get("exitCode") == 0 and isinstance(value.get("command"), str) and value["command"]:
                    status = "pass"
        elif kind == "artifact-parses":
            if artifacts:
                status = "pass"
                for artifact in artifacts:
                    payload = evaluation.evidence_bytes(
                        run_dir / "run.json", artifact["relativePath"], artifact["sha256"], artifact["bytes"],
                        "verifier artifact",
                    )
                    try:
                        text = payload.decode("utf-8")
                    except UnicodeError:
                        status = "fail"
                        break
                    if not text.strip():
                        status = "fail"
                        break
        elif kind == "artifact-language-bounded":
            if artifacts:
                status = "pass"
                phrases = [phrase.lower() for phrase in definition["forbiddenPhrases"]]
                for artifact in artifacts:
                    payload = evaluation.evidence_bytes(
                        run_dir / "run.json", artifact["relativePath"], artifact["sha256"], artifact["bytes"],
                        "verifier artifact",
                    )
                    try:
                        text = payload.decode("utf-8").lower()
                    except UnicodeError:
                        status = "fail"
                        break
                    if any(phrase in text for phrase in phrases):
                        status = "fail"
                        break
        elif kind == "evidence-covers-journey":
            observed = [item["type"] for item in evidence]
            if len(set(observed)) == len(observed) and set(observed) == set(journey.get("requiredEvidence", [])):
                status = "pass"
        elif kind == "workspace-verification-passes":
            entry = loaded.get("independent-verifier")
            if entry is not None:
                value = evaluation.parse_json(entry[1], "independent workspace verification evidence")
                if (
                    isinstance(value, dict) and value.get("status") == "pass"
                    and value.get("workerSelectedChecks") is False and value.get("network") == "none"
                    and value.get("externalEffects") is False
                ):
                    status = "pass"
        elif kind == "final-reply-exact":
            entry = loaded.get("command-exit")
            expected = definition["finalReply"]
            if entry is not None and expected is not None:
                value = evaluation.parse_json(entry[1], "command-exit final reply evidence")
                if (
                    isinstance(value, dict) and value.get("finalReplySha256") == expected["sha256"]
                    and value.get("finalReplyBytes") == expected["bytes"]
                    and value.get("finalReplyNormalization") == "exact-utf8"
                ):
                    status = "pass"
        elif kind == "research-inline-citations":
            entry = loaded.get("citation-coverage")
            if entry is not None:
                value = evaluation.parse_json(entry[1], "research citation coverage evidence")
                if isinstance(value, dict) and value.get("allFindingsCited") is True and value.get("allEvidencePresent") is True:
                    status = "pass"
        elif kind == "research-primary-source-preference":
            entry = loaded.get("source-provenance")
            if entry is not None:
                value = evaluation.parse_json(entry[1], "research source provenance evidence")
                if isinstance(value, dict) and value.get("primarySourcePreferenceVerified") is True:
                    status = "pass"
        elif kind == "research-citation-entailment":
            entry = loaded.get("citation-coverage")
            if entry is not None:
                value = evaluation.parse_json(entry[1], "research citation entailment evidence")
                if isinstance(value, dict) and value.get("semanticEntailmentVerified") is True:
                    status = "pass"
        elif kind == "research-label-stale-or-unknown":
            entry = loaded.get("observation-time")
            if entry is not None:
                value = evaluation.parse_json(entry[1], "research observation-time evidence")
                if isinstance(value, dict) and value.get("freshnessLabelsVerified") is True:
                    status = "pass"
        elif kind == "research-inline-provenance":
            entry = loaded.get("source-provenance")
            if entry is not None:
                value = evaluation.parse_json(entry[1], "research inline provenance evidence")
                if isinstance(value, dict) and value.get("provenanceComplete") is True:
                    status = "pass"
        evidence_entry = loaded.get(binding_types[kind])
        if evidence_entry is None:
            raise evaluation.OutcomeError("deterministic verifier evidence binding is missing")
        results.append({
            "id": item["assertionId"],
            "status": status,
            "evidencePath": evidence_entry[0]["relativePath"],
            "evidenceSha256": evidence_entry[0]["sha256"],
        })
    return results
