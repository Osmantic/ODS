#!/usr/bin/env python3
"""Validate private portal Assistant custody and emit content-free product evidence.

This module deliberately consumes the private ControlState record rather than the
browser projection or the model's narration.  It emits hashes, classifications,
times, and routes only; conversation text, tool names, arguments, results, paths,
credentials, and plaintext provider identifiers remain private.
"""

from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable

import portal_outcome_evaluation as evaluation
import portal_outcome_orchestrate as common


ASSISTANT_EVIDENCE_BOUNDARY = (
    "Content-free evidence for one exact private Pixel portal Assistant turn. It binds the settled conversation "
    "record to trusted host-side tool receipts and emits no conversation text, tool name, argument, result, path, "
    "credential, private source content, or plaintext provider identifier. It grants no execution, provider, "
    "credential, approval, retry, external-effect, completion, acceptance, publication, or promotion authority."
)
SOURCE_BOUNDARY = (
    "Content-free portal source provenance only. Source kind, observation time, result digest, staleness, and route "
    "are bound to trusted tool custody; source content and plaintext identifiers are omitted."
)
OBSERVATION_BOUNDARY = (
    "Content-free portal observation time only. It proves when a bound projection or broker result was observed; "
    "it does not by itself prove that the assistant labeled stale or unknown state correctly."
)
PRIVACY_BOUNDARY = (
    "Content-free portal routing evidence only. It binds observed tool routes and proves that no unclassified or "
    "direct network surface was credited; it grants no route, disclosure, provider, or execution authority."
)
TYPED_CALL_BOUNDARY = (
    "Content-free typed-call evidence only. Tool and call identities are retained solely as hashes with their "
    "trusted classification; arguments, results, paths, credentials, and private source content are omitted."
)
PROVIDER_BOUNDARY = (
    "Hashed provider-identifier evidence only. It binds a terminal broker result without exposing the provider "
    "identifier and grants no provider, retry, external-effect, or completion authority."
)
JOURNAL_BOUNDARY = (
    "Content-free terminal action-journal evidence only. It binds a succeeded journal head and provider identifier "
    "hash to the exact portal turn; it grants no retry or external-effect authority."
)
EXACT_SOURCE_BOUNDARY = (
    "Content-free exact portal request binding only. It binds the admitted request and retained turn record without "
    "reproducing either private text and grants no execution, completion, or scope authority."
)
CHECKPOINT_BOUNDARY = (
    "Content-free validated conversation checkpoint lineage only. It binds the selected settled turn to its prior "
    "record and exact conversation digest without exporting text, tools, arguments, results, or paths and grants no "
    "execution, retry, completion, acceptance, publication, or promotion authority."
)
INDEPENDENT_BOUNDARY = (
    "Content-free independent workspace verification evidence only. Controller-selected checks ran against a "
    "fresh candidate copy without network; worker output could not select checks or grant execution, external-effect, "
    "merge, deployment, publication, completion, acceptance, or promotion authority."
)

SUPPORTED_ASSISTANT_EVIDENCE_TYPES = frozenset({
    "exact-source", "source-provenance", "observation-time", "privacy-route",
    "typed-call", "provider-identifier", "action-journal", "checkpoint-lineage", "independent-verifier",
})
MODEL_PROXY_RECEIPT_FIELDS = frozenset({
    "schemaVersion", "jobId", "claimId", "planSha256", "qualificationId",
    "qualificationReceiptSha256", "inferencePolicySha256", "modelIdSha256",
    "proxyConfigSha256", "allowedToolsSha256", "startedAt", "modelRequests",
    "inputTokens", "outputTokens", "networkBytes", "deniedRequests", "backendFailures",
    "activeInference", "lastFailureCode", "lastRequestToolCount", "lastRequestToolsSha256",
    "contentStored", "credentialsExposed", "arbitraryNetwork", "externalEffects",
})
MODEL_PROXY_IMMUTABLE_FIELDS = frozenset({
    "schemaVersion", "jobId", "claimId", "planSha256", "qualificationId",
    "qualificationReceiptSha256", "inferencePolicySha256", "modelIdSha256",
    "proxyConfigSha256", "allowedToolsSha256", "startedAt", "contentStored",
    "credentialsExposed", "arbitraryNetwork", "externalEffects",
})
MODEL_PROXY_COUNTER_LIMITS = {
    "modelRequests": 100_000, "inputTokens": 2_000_000_000,
    "outputTokens": 500_000_000, "networkBytes": 10_737_418_240,
    "deniedRequests": 100_000, "backendFailures": 100_000,
}
MODEL_PROXY_JOB_RE = re.compile(r"^work-[0-9]{13}-[a-f0-9]{12}$")
MODEL_PROXY_CLAIM_RE = re.compile(r"^workclaim-[0-9]{13}-[a-f0-9]{12}$")
MODEL_PROXY_QUALIFICATION_RE = re.compile(r"^modelqual-[0-9]{13}-[a-f0-9]{12}$")


def _load_control(root: Path) -> Any:
    path = root / "control" / "server.py"
    spec = importlib.util.spec_from_file_location("pixel_assistant_evidence_control", path)
    if spec is None or spec.loader is None:
        raise evaluation.OutcomeError("Pixel control validator is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _exact_evidence_types(values: Iterable[str]) -> frozenset[str]:
    observed = list(values)
    if (
        len(observed) != len(set(observed))
        or any(not isinstance(item, str) or item not in SUPPORTED_ASSISTANT_EVIDENCE_TYPES for item in observed)
    ):
        raise evaluation.OutcomeError("Assistant evidence request contains an unsupported or duplicate type")
    return frozenset(observed)


def _model_proxy_receipt(value: Any, label: str) -> dict[str, Any]:
    value = evaluation.exact_fields(value, MODEL_PROXY_RECEIPT_FIELDS, label)
    if (
        value["schemaVersion"] != 1
        or not isinstance(value["jobId"], str) or MODEL_PROXY_JOB_RE.fullmatch(value["jobId"]) is None
        or not isinstance(value["claimId"], str) or MODEL_PROXY_CLAIM_RE.fullmatch(value["claimId"]) is None
        or not isinstance(value["qualificationId"], str)
        or MODEL_PROXY_QUALIFICATION_RE.fullmatch(value["qualificationId"]) is None
        or value["contentStored"] is not False or value["credentialsExposed"] is not False
        or value["arbitraryNetwork"] is not False or value["externalEffects"] is not False
        or type(value["activeInference"]) is not bool
    ):
        raise evaluation.OutcomeError(f"{label} identity or privacy boundary is invalid")
    for field in (
        "planSha256", "qualificationReceiptSha256", "modelIdSha256",
        "proxyConfigSha256", "allowedToolsSha256",
    ):
        evaluation.valid_hash(value[field], f"{label} {field}")
    if value["inferencePolicySha256"] is not None:
        evaluation.valid_hash(value["inferencePolicySha256"], f"{label} inferencePolicySha256")
    evaluation.timestamp(value["startedAt"], f"{label} start")
    for field, maximum in MODEL_PROXY_COUNTER_LIMITS.items():
        if type(value[field]) is not int or not 0 <= value[field] <= maximum:
            raise evaluation.OutcomeError(f"{label} {field} is invalid")
    if value["lastFailureCode"] is not None and (
        not isinstance(value["lastFailureCode"], str)
        or re.fullmatch(r"^[a-z0-9][a-z0-9-]{1,63}$", value["lastFailureCode"]) is None
    ):
        raise evaluation.OutcomeError(f"{label} failure code is invalid")
    if (value["lastRequestToolCount"] is None) != (value["lastRequestToolsSha256"] is None):
        raise evaluation.OutcomeError(f"{label} tool accounting is incomplete")
    if value["lastRequestToolCount"] is not None:
        if type(value["lastRequestToolCount"]) is not int or not 0 <= value["lastRequestToolCount"] <= 512:
            raise evaluation.OutcomeError(f"{label} tool count is invalid")
        evaluation.valid_hash(value["lastRequestToolsSha256"], f"{label} tool catalog")
    return value


def validate_model_proxy_pair(
    initial_value: Any, final_value: Any, *, turn: dict[str, Any], expected_model: str,
) -> dict[str, Any]:
    """Reconcile one fresh, dedicated proxy lifetime with one settled Assistant turn."""
    initial = _model_proxy_receipt(initial_value, "initial Assistant model proxy receipt")
    final = _model_proxy_receipt(final_value, "final Assistant model proxy receipt")
    if any(initial[field] != final[field] for field in MODEL_PROXY_IMMUTABLE_FIELDS):
        raise evaluation.OutcomeError("Assistant model proxy identity changed during the turn")
    if (
        any(initial[field] != 0 for field in MODEL_PROXY_COUNTER_LIMITS)
        or initial["activeInference"] is not False or initial["lastFailureCode"] is not None
        or initial["lastRequestToolCount"] is not None or initial["lastRequestToolsSha256"] is not None
    ):
        raise evaluation.OutcomeError("Assistant model proxy was not fresh before the turn")
    if (
        final["modelRequests"] < 1 or final["inputTokens"] < 1 or final["outputTokens"] < 1
        or final["networkBytes"] < 1 or final["deniedRequests"] != 0 or final["backendFailures"] != 0
        or final["activeInference"] is not False or final["lastFailureCode"] is not None
        or final["lastRequestToolCount"] is None or final["lastRequestToolsSha256"] is None
        or final["inferencePolicySha256"] is None
    ):
        raise evaluation.OutcomeError("Assistant model proxy did not settle one clean exact-inference lifetime")
    model_receipt = turn.get("modelReceipt")
    if (
        not isinstance(expected_model, str) or not expected_model
        or final["modelIdSha256"] != hashlib.sha256(expected_model.encode("utf-8")).hexdigest()
        or not isinstance(model_receipt, dict)
        or final["inputTokens"] != model_receipt.get("inputTokens")
        or final["outputTokens"] != model_receipt.get("outputTokens")
    ):
        raise evaluation.OutcomeError("Assistant model proxy accounting differs from the settled launcher turn")
    return {
        "initialReceiptSha256": evaluation.sha256(evaluation.canonical(initial)),
        "finalReceiptSha256": evaluation.sha256(evaluation.canonical(final)),
        "proxyConfigSha256": final["proxyConfigSha256"],
        "qualificationReceiptSha256": final["qualificationReceiptSha256"],
        "inferencePolicySha256": final["inferencePolicySha256"],
        "allowedToolsSha256": final["allowedToolsSha256"],
        "modelRequests": final["modelRequests"],
        "inputTokens": final["inputTokens"], "outputTokens": final["outputTokens"],
        "networkBytes": final["networkBytes"],
    }


def _assistant_envelope(
    value: Any, *, root: Path, expected_provider: str, expected_model: str,
) -> tuple[Any, dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    value = evaluation.exact_fields(value, {
        "schemaVersion", "operation", "conversation", "conversationSha256", "turnId",
        "toolReceiptBundle", "toolReceiptBundleSha256", "actionJournalChains",
        "modelProxyInitialReceipt", "modelProxyFinalReceipt",
        "handoffReceipt", "independentVerification",
        "privacy", "boundary",
    }, "Pixel Assistant private evidence envelope")
    expected_privacy = {
        "conversationTextExported": False, "toolNamesExported": False, "argumentsExported": False,
        "resultsExported": False, "pathsExported": False, "credentialsExported": False,
        "providerIdentifiersExported": False,
    }
    if (
        value["schemaVersion"] != 1 or value["operation"] != "pixel-portal-assistant-private-evidence"
        or value["boundary"] != ASSISTANT_EVIDENCE_BOUNDARY or value["privacy"] != expected_privacy
        or value["handoffReceipt"] is not None
    ):
        raise evaluation.OutcomeError("Pixel Assistant private evidence envelope is invalid or overclaims unsupported proof")
    control = _load_control(root)
    conversation = value["conversation"]
    if not isinstance(conversation, dict) or not isinstance(conversation.get("conversationId"), str):
        raise evaluation.OutcomeError("Pixel Assistant conversation identity is invalid")
    try:
        control.validate_chat_conversation(conversation, conversation["conversationId"])
    except Exception as exc:
        raise evaluation.OutcomeError("Pixel Assistant conversation custody is invalid") from exc
    if value["conversationSha256"] != control.digest(conversation):
        raise evaluation.OutcomeError("Pixel Assistant conversation digest is invalid")
    selected = [turn for turn in conversation["turns"] if turn.get("turnId") == value["turnId"]]
    if len(selected) != 1 or selected[0].get("state") != "succeeded":
        raise evaluation.OutcomeError("Pixel Assistant evidence does not select one succeeded turn")
    turn = selected[0]
    if turn.get("toolFailures") != 0 or not isinstance(turn.get("capabilityReceipt"), dict) or not isinstance(turn.get("brokerReceipt"), dict):
        raise evaluation.OutcomeError("Pixel Assistant turn lacks complete capability or broker accounting")
    model_receipt = turn.get("modelReceipt")
    try:
        control.validate_chat_model_receipt(model_receipt, response_sha256=turn["responseSha256"])
    except Exception as exc:
        raise evaluation.OutcomeError("Pixel Assistant turn lacks exact model usage custody") from exc
    if (
        not isinstance(expected_provider, str) or not expected_provider
        or not isinstance(expected_model, str) or not expected_model
        or model_receipt["providerIdSha256"] != hashlib.sha256(expected_provider.encode("utf-8")).hexdigest()
        or model_receipt["modelIdSha256"] != hashlib.sha256(expected_model.encode("utf-8")).hexdigest()
    ):
        raise evaluation.OutcomeError("Pixel Assistant turn differs from the admitted model identity")
    validate_model_proxy_pair(
        value["modelProxyInitialReceipt"], value["modelProxyFinalReceipt"],
        turn=turn, expected_model=expected_model,
    )
    bundle = value["toolReceiptBundle"]
    bundle_sha = value["toolReceiptBundleSha256"]
    if bundle is None:
        if bundle_sha is not None or turn["toolCalls"] != 0 or turn["brokerReceipt"].get("bundleSha256") is not None:
            raise evaluation.OutcomeError("Pixel Assistant empty tool custody is inconsistent")
    else:
        try:
            control.validate_chat_tool_receipt_bundle(bundle, turn["turnId"])
        except Exception as exc:
            raise evaluation.OutcomeError("Pixel Assistant tool receipt bundle is invalid") from exc
        if (
            not isinstance(bundle_sha, str) or bundle_sha != control.digest(bundle)
            or turn["brokerReceipt"].get("bundleSha256") != bundle_sha
            or len(bundle["receipts"]) > turn["toolCalls"]
        ):
            raise evaluation.OutcomeError("Pixel Assistant tool bundle is not bound to its settled turn")
    if not isinstance(value["actionJournalChains"], list) or len(value["actionJournalChains"]) > 32:
        raise evaluation.OutcomeError("Pixel Assistant action journal inventory is invalid")
    return control, value, turn, bundle


def _journal_heads(chains: list[Any]) -> set[str]:
    event_fields = {
        "schemaVersion", "kind", "actionId", "sequence", "previousEventSha256", "state", "recordedAt",
        "connector", "operation", "proposalSha256", "idempotencyKeySha256", "idempotencyMode",
        "providerTargetSha256", "attempt", "reasonCode", "observationSha256", "retryAllowed", "boundary",
    }
    transitions = {
        "proposed": {"submitting", "failed", "canceled"}, "submitting": {"unknown", "succeeded", "failed"},
        "unknown": {"reconciling"}, "reconciling": {"unknown", "succeeded", "failed"},
        "succeeded": set(), "failed": set(), "canceled": set(),
    }
    boundary = (
        "Content-free append-only custody for one bounded external action. It proves local state transitions and "
        "retry suppression, not provider acceptance, semantic correctness, operator approval, or completion without "
        "a terminal provider-bound observation."
    )
    heads: set[str] = set()
    action_ids: set[str] = set()
    for chain in chains:
        chain = evaluation.exact_fields(chain, {"actionId", "events", "headSha256"}, "Assistant action journal chain")
        if not isinstance(chain["actionId"], str) or chain["actionId"] in action_ids:
            raise evaluation.OutcomeError("Assistant action journal identity is invalid or duplicated")
        action_ids.add(chain["actionId"])
        events = chain["events"]
        if not isinstance(events, list) or not 2 <= len(events) <= 1000:
            raise evaluation.OutcomeError("Assistant action journal chain is incomplete")
        previous = None
        metadata = None
        prior_state = None
        for sequence, event in enumerate(events):
            event = evaluation.exact_fields(event, event_fields, "Assistant action journal event")
            current_metadata = tuple(event[key] for key in (
                "connector", "operation", "proposalSha256", "idempotencyKeySha256",
                "idempotencyMode", "providerTargetSha256", "attempt",
            ))
            if (
                event["schemaVersion"] != 1 or event["kind"] != "pixel-external-action-journal-event"
                or event["actionId"] != chain["actionId"] or event["sequence"] != sequence
                or event["previousEventSha256"] != previous or event["boundary"] != boundary
                or event["state"] not in transitions or event["attempt"] != 1
                or event["retryAllowed"] is not (event["state"] == "proposed")
                or sequence == 0 and event["state"] != "proposed"
                or prior_state is not None and event["state"] not in transitions[prior_state]
                or metadata is not None and current_metadata != metadata
            ):
                raise evaluation.OutcomeError("Assistant action journal chain is invalid")
            for field in ("proposalSha256", "idempotencyKeySha256", "providerTargetSha256"):
                evaluation.valid_hash(event[field], f"Assistant action journal {field}")
            if event["observationSha256"] is not None:
                evaluation.valid_hash(event["observationSha256"], "Assistant action journal observation")
            evaluation.timestamp(event["recordedAt"], "Assistant action journal event time")
            payload = (json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("utf-8")
            previous = hashlib.sha256(payload).hexdigest()
            prior_state = event["state"]
            metadata = current_metadata
        if events[-1]["state"] != "succeeded" or events[-1]["observationSha256"] is None or chain["headSha256"] != previous:
            raise evaluation.OutcomeError("Assistant action journal is not terminal and provider-bound")
        if chain["headSha256"] in heads:
            raise evaluation.OutcomeError("Assistant action journal head is duplicated")
        heads.add(chain["headSha256"])
    return heads


def validate_and_emit(
    *, root: Path, run_dir: Path, assistant_evidence: Any, request_payload: bytes,
    request_sha256: str, required_evidence: Iterable[str], expected_provider: str, expected_model: str,
    source_sha256: str | None = None,
) -> list[dict[str, Any]]:
    """Validate one exact private Assistant turn and emit only requested evidence."""
    required = _exact_evidence_types(required_evidence)
    control, envelope, turn, bundle = _assistant_envelope(
        assistant_evidence, root=root, expected_provider=expected_provider, expected_model=expected_model,
    )
    try:
        request_text = request_payload.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise evaluation.OutcomeError("Assistant admitted request is not strict UTF-8") from exc
    try:
        request_text = control.bounded_multiline_text(
            request_text, "Assistant admitted request", control.MAX_CHAT_MESSAGE_BYTES,
        )
    except Exception as exc:
        raise evaluation.OutcomeError("Assistant admitted request cannot cross the exact portal text boundary") from exc
    if evaluation.sha256(request_payload) != request_sha256 or turn["userText"] != request_text:
        raise evaluation.OutcomeError("Assistant turn differs from the exact admitted request")

    receipts = [] if bundle is None else bundle["receipts"]
    observations = []
    calls = []
    actions = []
    for receipt in receipts:
        outcome = receipt["outcome"]
        if receipt["state"] != "succeeded" or outcome is None:
            raise evaluation.OutcomeError("Assistant proof refuses unsettled or failed tool custody")
        evidence = outcome["evidence"]
        classification = receipt["classification"]
        if classification["capability"] == "unclassified" or classification["route"] in {"unknown", "network-surface"}:
            raise evaluation.OutcomeError("Assistant proof refuses unclassified or direct-network tool use")
        calls.append({
            "toolCallIdSha256": receipt["toolCallIdSha256"], "toolNameSha256": receipt["toolNameSha256"],
            "capability": classification["capability"], "route": classification["route"],
            "effect": classification["effect"], "startedAt": receipt["startedAt"], "finishedAt": receipt["finishedAt"],
            "resultSha256": outcome["resultSha256"],
        })
        if evidence["observation"] is not None:
            observations.append(dict(evidence["observation"]))
        if evidence["action"] is not None:
            actions.append({
                **evidence["action"], "toolCallIdSha256": receipt["toolCallIdSha256"],
                "toolNameSha256": receipt["toolNameSha256"], "brokerState": evidence["status"],
            })

    emitted: list[dict[str, Any]] = []
    if "exact-source" in required:
        if source_sha256 is None:
            raise evaluation.OutcomeError("Assistant exact-source evidence lacks its admitted source binding")
        evaluation.valid_hash(source_sha256, "Assistant admitted source digest")
        payload = evaluation.canonical({
            "schemaVersion": 1, "format": "pixel-assistant-exact-source-v1",
            "sourceSnapshotSha256": source_sha256,
            "userRequestSha256": request_sha256, "turnRecordSha256": turn["recordSha256"],
            "conversationSha256": envelope["conversationSha256"], "exact": True,
            "privateTextIncluded": False, "boundary": EXACT_SOURCE_BOUNDARY,
        })
        emitted.append(common.write_evidence(run_dir, "assistant-exact-source.json", payload, "exact-source"))
    if "checkpoint-lineage" in required:
        payload = evaluation.canonical({
            "schemaVersion": 1, "format": "pixel-assistant-checkpoint-lineage-v1",
            "conversationSha256": envelope["conversationSha256"], "turnRecordSha256": turn["recordSha256"],
            "previousTurnRecordSha256": turn["previousSha256"],
            "turns": len(envelope["conversation"]["turns"]), "lineageValidated": True,
            "privateTextIncluded": False, "boundary": CHECKPOINT_BOUNDARY,
        })
        emitted.append(common.write_evidence(run_dir, "assistant-checkpoint-lineage.json", payload, "checkpoint-lineage"))
    if "independent-verifier" in required:
        independent = evaluation.exact_fields(envelope["independentVerification"], {
            "schemaVersion", "format", "sourceSnapshotSha256", "candidateSha256", "status", "checks", "criteria",
            "network", "workerSelectedChecks", "externalEffects", "immutablePathViolation",
            "aggregateRuntimeMilliseconds", "aggregateOutputBytes", "aggregateWithinBounds", "cleanupVerified", "boundary",
        }, "Assistant independent verification")
        if source_sha256 is None:
            raise evaluation.OutcomeError("Assistant independent verification lacks its admitted source binding")
        evaluation.valid_hash(source_sha256, "Assistant admitted source digest")
        evaluation.valid_hash(independent["candidateSha256"], "Assistant verified candidate digest")
        if (
            independent["schemaVersion"] != 1 or independent["format"] != "pixel-neutral-independent-verification-v1"
            or independent["sourceSnapshotSha256"] != source_sha256 or independent["status"] not in {"pass", "fail"}
            or independent["network"] != "none" or independent["workerSelectedChecks"] is not False
            or independent["externalEffects"] is not False or type(independent["immutablePathViolation"]) is not bool
            or type(independent["aggregateWithinBounds"]) is not bool or type(independent["cleanupVerified"]) is not bool
            or independent["boundary"] != INDEPENDENT_BOUNDARY
            or not isinstance(independent["checks"], list) or not 1 <= len(independent["checks"]) <= 16
            or not isinstance(independent["criteria"], list) or not independent["criteria"]
        ):
            raise evaluation.OutcomeError("Assistant independent verification is invalid or exceeds its authority")
        evaluation.integer(independent["aggregateRuntimeMilliseconds"], 0, 3_600_000, "Assistant verifier runtime")
        evaluation.integer(independent["aggregateOutputBytes"], 0, 16_777_216, "Assistant verifier output")
        emitted.append(common.write_evidence(
            run_dir, "assistant-independent-verification.json", evaluation.canonical(independent), "independent-verifier",
        ))
    if required & {"source-provenance", "observation-time"} and not observations:
        raise evaluation.OutcomeError("Assistant source evidence lacks a trusted observed result")
    if "source-provenance" in required:
        payload = evaluation.canonical({
            "schemaVersion": 1, "format": "pixel-assistant-source-provenance-v1",
            "turnRecordSha256": turn["recordSha256"], "observations": observations,
            "provenanceComplete": True, "privateDataIncluded": False, "boundary": SOURCE_BOUNDARY,
        })
        emitted.append(common.write_evidence(run_dir, "assistant-source-provenance.json", payload, "source-provenance"))
    if "observation-time" in required:
        payload = evaluation.canonical({
            "schemaVersion": 1, "format": "pixel-assistant-observation-time-v1",
            "turnRecordSha256": turn["recordSha256"],
            "observations": [{"sourceKind": item["sourceKind"], "observedAt": item["observedAt"], "stale": item["stale"]} for item in observations],
            "freshnessLabelsVerified": False, "boundary": OBSERVATION_BOUNDARY,
        })
        emitted.append(common.write_evidence(run_dir, "assistant-observation-time.json", payload, "observation-time"))
    if "privacy-route" in required:
        routes = sorted({item["route"] for item in calls})
        if "sanitized-remote" in routes:
            raise evaluation.OutcomeError("Assistant privacy proof requires a separate DLP spillover receipt")
        payload = evaluation.canonical({
            "schemaVersion": 1, "format": "pixel-assistant-privacy-route-v1",
            "turnRecordSha256": turn["recordSha256"], "routes": routes,
            "unclassifiedTools": 0, "directNetworkSurface": False, "privateDataSentRemote": False,
            "boundary": PRIVACY_BOUNDARY,
        })
        emitted.append(common.write_evidence(run_dir, "assistant-privacy-route.json", payload, "privacy-route"))
    if "typed-call" in required:
        if not calls:
            raise evaluation.OutcomeError("Assistant typed-call proof has no trusted call")
        payload = evaluation.canonical({
            "schemaVersion": 1, "format": "pixel-assistant-typed-call-v1",
            "turnRecordSha256": turn["recordSha256"], "calls": calls,
            "argumentsIncluded": False, "resultsIncluded": False, "boundary": TYPED_CALL_BOUNDARY,
        })
        emitted.append(common.write_evidence(run_dir, "assistant-typed-call.json", payload, "typed-call"))
    if required & {"provider-identifier", "action-journal"}:
        terminal = [item for item in actions if item["actionJournalState"] == "succeeded"]
        if not terminal or turn["brokerReceipt"].get("state") != "correlated" or turn["brokerReceipt"].get("externalEffectOccurred") is not True:
            raise evaluation.OutcomeError("Assistant action proof lacks one correlated terminal provider action")
        if any(item["providerIdentifierSha256"] is None or item["actionJournalHeadSha256"] is None for item in terminal):
            raise evaluation.OutcomeError("Assistant terminal action lacks provider or journal identity")
        journal_heads = _journal_heads(envelope["actionJournalChains"])
        if journal_heads != {item["actionJournalHeadSha256"] for item in terminal}:
            raise evaluation.OutcomeError("Assistant trusted action receipt differs from the retained journal chain")
    else:
        if envelope["actionJournalChains"]:
            raise evaluation.OutcomeError("Assistant retained an unexpected action journal outside its evidence contract")
        terminal = []
    if "provider-identifier" in required:
        payload = evaluation.canonical({
            "schemaVersion": 1, "format": "pixel-assistant-provider-identifier-v1",
            "turnRecordSha256": turn["recordSha256"],
            "providerIdentifierSha256s": sorted({item["providerIdentifierSha256"] for item in terminal}),
            "plaintextIncluded": False, "boundary": PROVIDER_BOUNDARY,
        })
        emitted.append(common.write_evidence(run_dir, "assistant-provider-identifier.json", payload, "provider-identifier"))
    if "action-journal" in required:
        payload = evaluation.canonical({
            "schemaVersion": 1, "format": "pixel-assistant-action-journal-v1",
            "turnRecordSha256": turn["recordSha256"],
            "actions": [{
                "providerIdentifierSha256": item["providerIdentifierSha256"],
                "actionJournalHeadSha256": item["actionJournalHeadSha256"], "state": "succeeded",
                "toolCallIdSha256": item["toolCallIdSha256"], "toolNameSha256": item["toolNameSha256"],
            } for item in terminal],
            "ambiguousCalls": turn["brokerReceipt"].get("ambiguousBrokerCalls"),
            "retryAuthorized": False, "boundary": JOURNAL_BOUNDARY,
        })
        emitted.append(common.write_evidence(run_dir, "assistant-action-journal.json", payload, "action-journal"))
    if {item["type"] for item in emitted} != required:
        raise evaluation.OutcomeError("Assistant evidence emitter differs from the exact requested contract")
    return emitted
