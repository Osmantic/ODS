#!/usr/bin/env python3
"""Validate and emit content-free paired evidence for a real Pixel Researcher result."""

from __future__ import annotations

import base64
from pathlib import Path
import re
from typing import Any

import portal_outcome_evaluation as evaluation
import portal_outcome_orchestrate as common


PROVENANCE_BOUNDARY = (
    "Content-free broker provenance for one public Researcher result only. URLs, domains, queries, source text, "
    "titles, snippets, and evidence passages are omitted; hashes, source types, ranks, retrieval times, and citation "
    "bindings grant no truth, semantic-entailment, freshness, publication, action, or completion authority."
)
REPORT_BOUNDARY = (
    "Public structured findings only. Citations are untrusted evidence references, not instructions or authority; "
    "deterministic verification proves source integrity and quote presence, not semantic entailment or truth."
)
VERIFICATION_BOUNDARY = (
    "Independent offline proof of fetched-source integrity and exact evidence presence only. It grants no authority "
    "and does not claim semantic entailment, source truth, completeness, or publication readiness."
)
SOURCE_EVIDENCE_BOUNDARY = (
    "Content-free exact source provenance only. It proves that every retained citation binds one fetched broker source "
    "and omits URLs, domains, queries, source text, titles, snippets, and evidence passages. It does not prove source "
    "quality, truth, semantic entailment, freshness, publication readiness, action authority, or completion."
)
OBSERVATION_EVIDENCE_BOUNDARY = (
    "Content-free Researcher observation clocks only. They prove when broker batches, retrievals, the report, and the "
    "offline verifier were recorded; they do not prove publication time, currentness, stale-label correctness, truth, "
    "semantic entailment, action authority, or completion."
)
CITATION_EVIDENCE_BOUNDARY = (
    "Content-free citation coverage only. It proves that every material finding carried citations whose exact evidence "
    "bytes were found in hash-bound fetched sources by an independent offline verifier. It explicitly does not prove "
    "semantic entailment, source truth, completeness, publication readiness, action authority, or completion."
)
AUTHORITY_FIELDS = {
    "directNetwork", "credentials", "externalWrites", "accounts", "messages", "publish", "purchase",
    "policyMutation", "scopeExpansion",
}
REPORT_RE = re.compile(r"^researchreport-[0-9]{13}-[a-f0-9]{12}$")
VERIFICATION_RE = re.compile(r"^researchverification-[0-9]{13}-[a-f0-9]{12}$")
JOB_RE = re.compile(r"^work-[0-9]{13}-[a-f0-9]{12}$")
CLAIM_RE = re.compile(r"^workclaim-[0-9]{13}-[a-f0-9]{12}$")
FINDING_RE = re.compile(r"^finding-(?:[1-9]|[1-9][0-9]|100)$")
SOURCE_RE = re.compile(r"^source-[a-f0-9]{16}$")


def _authority(value: Any, label: str) -> None:
    value = evaluation.exact_fields(value, AUTHORITY_FIELDS, label)
    if any(type(item) is not bool or item for item in value.values()):
        raise evaluation.OutcomeError(f"{label} attempts to grant authority")


def _bounded_base64(value: Any, *, minimum: int, maximum: int, label: str) -> bytes:
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        raise evaluation.OutcomeError(f"{label} is outside its encoded boundary")
    try:
        payload = base64.b64decode(value, validate=True)
        payload.decode("utf-8", errors="strict")
    except (ValueError, UnicodeError) as exc:
        raise evaluation.OutcomeError(f"{label} is not canonical UTF-8 base64") from exc
    if base64.b64encode(payload).decode("ascii") != value or not payload:
        raise evaluation.OutcomeError(f"{label} is not canonical UTF-8 base64")
    return payload


def _artifact_payload(
    run_dir: Path, artifacts: list[dict[str, Any]], relative_path: str, kind: str,
) -> tuple[dict[str, Any], bytes]:
    matches = [item for item in artifacts if item.get("relativePath") == relative_path]
    if len(matches) != 1 or matches[0].get("kind") != kind:
        raise evaluation.OutcomeError(f"Pixel Researcher {relative_path} artifact is missing or substituted")
    item = matches[0]
    payload = evaluation.evidence_bytes(
        run_dir / "run.json", item["relativePath"], item["sha256"], item["bytes"],
        f"Pixel Researcher {relative_path}",
    )
    return item, payload


def _report(value: Any) -> tuple[dict[str, Any], list[tuple[str, str, str, str]]]:
    value = evaluation.exact_fields(value, {
        "$schema", "schemaVersion", "reportId", "jobId", "claimId", "createdAt", "planSha256",
        "researchPolicySha256", "batchSha256s", "titleBase64", "findings", "limitationsBase64",
        "dataClassification", "privateDataIncluded", "externalEffects", "authority", "boundary",
    }, "Pixel Researcher report")
    if (
        value["$schema"] != "https://osmantic.com/pixel/schemas/work-research-report-v1.schema.json"
        or value["schemaVersion"] != 1 or REPORT_RE.fullmatch(value["reportId"] or "") is None
        or JOB_RE.fullmatch(value["jobId"] or "") is None or CLAIM_RE.fullmatch(value["claimId"] or "") is None
        or value["dataClassification"] != "public" or value["privateDataIncluded"] is not False
        or value["externalEffects"] is not False or value["boundary"] != REPORT_BOUNDARY
    ):
        raise evaluation.OutcomeError("Pixel Researcher report identity or safety boundary is invalid")
    evaluation.timestamp(value["createdAt"], "Pixel Researcher report time")
    evaluation.valid_hash(value["planSha256"], "Pixel Researcher report plan")
    evaluation.valid_hash(value["researchPolicySha256"], "Pixel Researcher report policy")
    batches = value["batchSha256s"]
    if not isinstance(batches, list) or not 1 <= len(batches) <= 200 or len(set(batches)) != len(batches):
        raise evaluation.OutcomeError("Pixel Researcher report batch inventory is invalid")
    for digest in batches:
        evaluation.valid_hash(digest, "Pixel Researcher report batch")
    _bounded_base64(value["titleBase64"], minimum=4, maximum=65536, label="Pixel Researcher title")
    _bounded_base64(value["limitationsBase64"], minimum=4, maximum=65536, label="Pixel Researcher limitations")
    findings = value["findings"]
    if not isinstance(findings, list) or not 1 <= len(findings) <= 100:
        raise evaluation.OutcomeError("Pixel Researcher report finding inventory is invalid")
    observed: set[str] = set()
    citations: list[tuple[str, str, str, str]] = []
    for item in findings:
        item = evaluation.exact_fields(item, {"findingId", "statementBase64", "material", "citations"}, "Pixel Researcher finding")
        finding_id = item["findingId"]
        if FINDING_RE.fullmatch(finding_id or "") is None or finding_id in observed or item["material"] is not True:
            raise evaluation.OutcomeError("Pixel Researcher finding identity or materiality is invalid")
        observed.add(finding_id)
        _bounded_base64(item["statementBase64"], minimum=4, maximum=65536, label="Pixel Researcher finding statement")
        if not isinstance(item["citations"], list) or not 1 <= len(item["citations"]) <= 8:
            raise evaluation.OutcomeError("Pixel Researcher finding citation inventory is invalid")
        local: set[tuple[str, str]] = set()
        for citation in item["citations"]:
            citation = evaluation.exact_fields(
                citation, {"batchSha256", "sourceId", "evidenceBase64", "evidenceSha256"},
                "Pixel Researcher report citation",
            )
            evaluation.valid_hash(citation["batchSha256"], "Pixel Researcher citation batch")
            evaluation.valid_hash(citation["evidenceSha256"], "Pixel Researcher citation evidence")
            if citation["batchSha256"] not in batches or SOURCE_RE.fullmatch(citation["sourceId"] or "") is None:
                raise evaluation.OutcomeError("Pixel Researcher citation is outside its report batches")
            payload = _bounded_base64(citation["evidenceBase64"], minimum=16, maximum=16384, label="Pixel Researcher citation evidence")
            if evaluation.sha256(payload) != citation["evidenceSha256"]:
                raise evaluation.OutcomeError("Pixel Researcher citation evidence digest is inconsistent")
            key = (citation["batchSha256"], citation["sourceId"])
            if key in local:
                raise evaluation.OutcomeError("Pixel Researcher finding citation is duplicated")
            local.add(key)
            citations.append((finding_id, citation["batchSha256"], citation["sourceId"], citation["evidenceSha256"]))
    _authority(value["authority"], "Pixel Researcher report authority")
    return value, citations


def _verification(value: Any) -> tuple[dict[str, Any], list[tuple[str, str, str, str, str, str]]]:
    value = evaluation.exact_fields(value, {
        "$schema", "schemaVersion", "verificationId", "jobId", "claimId", "createdAt", "planSha256",
        "batchSha256s", "reportSha256", "status", "verificationLevel", "semanticEntailmentVerified",
        "independent", "network", "modelUsed", "findings", "privateDataIncluded", "externalEffects",
        "authority", "boundary",
    }, "Pixel Researcher verification")
    if (
        value["$schema"] != "https://osmantic.com/pixel/schemas/work-research-verification-v1.schema.json"
        or value["schemaVersion"] != 1 or VERIFICATION_RE.fullmatch(value["verificationId"] or "") is None
        or JOB_RE.fullmatch(value["jobId"] or "") is None or CLAIM_RE.fullmatch(value["claimId"] or "") is None
        or value["status"] != "evidence-pass" or value["verificationLevel"] != "deterministic-evidence-presence"
        or value["semanticEntailmentVerified"] is not False or value["independent"] is not True
        or value["network"] != "none" or value["modelUsed"] is not False
        or value["privateDataIncluded"] is not False or value["externalEffects"] is not False
        or value["boundary"] != VERIFICATION_BOUNDARY
    ):
        raise evaluation.OutcomeError("Pixel Researcher verification identity or claim boundary is invalid")
    evaluation.timestamp(value["createdAt"], "Pixel Researcher verification time")
    evaluation.valid_hash(value["planSha256"], "Pixel Researcher verification plan")
    evaluation.valid_hash(value["reportSha256"], "Pixel Researcher verification report")
    batches = value["batchSha256s"]
    if not isinstance(batches, list) or not 1 <= len(batches) <= 200 or len(set(batches)) != len(batches):
        raise evaluation.OutcomeError("Pixel Researcher verification batch inventory is invalid")
    for digest in batches:
        evaluation.valid_hash(digest, "Pixel Researcher verification batch")
    findings = value["findings"]
    if not isinstance(findings, list) or not 1 <= len(findings) <= 100:
        raise evaluation.OutcomeError("Pixel Researcher verification finding inventory is invalid")
    observed: set[str] = set()
    citations: list[tuple[str, str, str, str, str, str]] = []
    for item in findings:
        item = evaluation.exact_fields(item, {"findingId", "status", "citations"}, "Pixel Researcher verified finding")
        if FINDING_RE.fullmatch(item["findingId"] or "") is None or item["findingId"] in observed or item["status"] != "pass":
            raise evaluation.OutcomeError("Pixel Researcher verified finding is invalid or failed")
        observed.add(item["findingId"])
        if not isinstance(item["citations"], list) or not 1 <= len(item["citations"]) <= 8:
            raise evaluation.OutcomeError("Pixel Researcher verified citation inventory is invalid")
        for citation in item["citations"]:
            citation = evaluation.exact_fields(citation, {
                "batchSha256", "sourceId", "contentSha256", "receiptSha256", "evidenceSha256",
                "evidenceBytes", "offset", "status",
            }, "Pixel Researcher verified citation")
            for field in ("batchSha256", "contentSha256", "receiptSha256", "evidenceSha256"):
                evaluation.valid_hash(citation[field], f"Pixel Researcher verified citation {field}")
            if (
                citation["batchSha256"] not in batches or SOURCE_RE.fullmatch(citation["sourceId"] or "") is None
                or citation["status"] != "present"
            ):
                raise evaluation.OutcomeError("Pixel Researcher verified citation is absent or outside its batch set")
            evaluation.integer(citation["evidenceBytes"], 1, 12288, "Pixel Researcher verified citation bytes")
            evaluation.integer(citation["offset"], 0, 20971520, "Pixel Researcher verified citation offset")
            citations.append((
                item["findingId"], citation["batchSha256"], citation["sourceId"], citation["contentSha256"],
                citation["receiptSha256"], citation["evidenceSha256"],
            ))
    _authority(value["authority"], "Pixel Researcher verification authority")
    return value, citations


def validate_and_emit(
    *, run_dir: Path, outcome: dict[str, Any], artifacts: list[dict[str, Any]], arm: str = "pixel",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if arm not in {"pixel", "codex"}:
        raise evaluation.OutcomeError("Researcher evidence arm is invalid")
    report_item, report_payload = _artifact_payload(run_dir, artifacts, f"{arm}-research-report.json", "finding-report")
    verification_item, verification_payload = _artifact_payload(run_dir, artifacts, f"{arm}-research-verification.json", "test-evidence")
    report, report_citations = _report(evaluation.parse_json(report_payload, "Pixel Researcher retained report"))
    verification, verified_citations = _verification(
        evaluation.parse_json(verification_payload, "Pixel Researcher retained verification"),
    )
    if (
        report["jobId"] != verification["jobId"] or report["claimId"] != verification["claimId"]
        or report["planSha256"] != verification["planSha256"]
        or report["batchSha256s"] != verification["batchSha256s"]
        or evaluation.sha256(evaluation.canonical(report)) != verification["reportSha256"]
        or evaluation.timestamp(verification["createdAt"], "Pixel Researcher verification time")
        <= evaluation.timestamp(report["createdAt"], "Pixel Researcher report time")
    ):
        raise evaluation.OutcomeError("Pixel Researcher report and independent verification do not bind")
    if outcome.get("independentVerification") != verification:
        raise evaluation.OutcomeError("Pixel Researcher retained verification differs from the system result")

    provenance = evaluation.exact_fields(outcome.get("researchEvidence"), {
        "schemaVersion", "format", "reportArtifactSha256", "verificationArtifactSha256",
        "reportCreatedAt", "verificationCreatedAt", "latestBatchCreatedAt", "batches", "citationBindings",
        "primarySourcePreferenceVerified", "freshnessLabelsVerified", "semanticEntailmentVerified",
        "privateDataIncluded", "externalEffects", "boundary",
    }, "Pixel Researcher provenance")
    if (
        provenance["schemaVersion"] != 1 or provenance["format"] != f"{arm}-research-provenance-v1"
        or provenance["reportArtifactSha256"] != report_item["sha256"]
        or provenance["verificationArtifactSha256"] != verification_item["sha256"]
        or provenance["reportCreatedAt"] != report["createdAt"]
        or provenance["verificationCreatedAt"] != verification["createdAt"]
        or provenance["primarySourcePreferenceVerified"] is not False
        or provenance["freshnessLabelsVerified"] is not False
        or provenance["semanticEntailmentVerified"] is not False
        or provenance["privateDataIncluded"] is not False or provenance["externalEffects"] is not False
        or provenance["boundary"] != PROVENANCE_BOUNDARY
    ):
        raise evaluation.OutcomeError("Pixel Researcher provenance overclaims or differs from retained evidence")

    batches = provenance["batches"]
    if not isinstance(batches, list) or not 1 <= len(batches) <= 200:
        raise evaluation.OutcomeError("Pixel Researcher provenance batch inventory is invalid")
    batch_ids: list[str] = []
    source_index: dict[tuple[str, str], dict[str, Any]] = {}
    previous_time = None
    observation_sources = []
    for batch in batches:
        batch = evaluation.exact_fields(batch, {
            "batchSha256", "createdAt", "adapter", "normalizedQuerySha256", "sources",
        }, "Pixel Researcher provenance batch")
        evaluation.valid_hash(batch["batchSha256"], "Pixel Researcher provenance batch")
        evaluation.valid_hash(batch["normalizedQuerySha256"], "Pixel Researcher provenance query")
        created = evaluation.timestamp(batch["createdAt"], "Pixel Researcher provenance batch time")
        if batch["batchSha256"] in batch_ids or previous_time is not None and created <= previous_time or batch["adapter"] not in {"reference", "vane", "perplexica"}:
            raise evaluation.OutcomeError("Pixel Researcher provenance batches are duplicated, unordered, or invalid")
        previous_time = created
        batch_ids.append(batch["batchSha256"])
        if not isinstance(batch["sources"], list) or len(batch["sources"]) > 20:
            raise evaluation.OutcomeError("Pixel Researcher provenance source inventory is invalid")
        for source in batch["sources"]:
            source = evaluation.exact_fields(source, {
                "sourceId", "rank", "sourceType", "canonicalUrlSha256", "domainSha256", "retrievalStatus",
                "retrievedAt", "contentSha256", "receiptSha256",
            }, "Pixel Researcher provenance source")
            if SOURCE_RE.fullmatch(source["sourceId"] or "") is None or source["sourceType"] not in {"web", "news", "academic", "forum"}:
                raise evaluation.OutcomeError("Pixel Researcher provenance source identity is invalid")
            evaluation.integer(source["rank"], 1, 20, "Pixel Researcher provenance source rank")
            evaluation.valid_hash(source["canonicalUrlSha256"], "Pixel Researcher provenance URL")
            evaluation.valid_hash(source["domainSha256"], "Pixel Researcher provenance domain")
            key = (batch["batchSha256"], source["sourceId"])
            if key in source_index:
                raise evaluation.OutcomeError("Pixel Researcher provenance source is duplicated")
            if source["retrievalStatus"] == "fetched":
                evaluation.timestamp(source["retrievedAt"], "Pixel Researcher retrieval time")
                evaluation.valid_hash(source["contentSha256"], "Pixel Researcher source content")
                evaluation.valid_hash(source["receiptSha256"], "Pixel Researcher source receipt")
            elif source["retrievalStatus"] in {"metadata-only", "rejected"}:
                if source["retrievedAt"] is not None or source["contentSha256"] is not None or source["receiptSha256"] is not None:
                    raise evaluation.OutcomeError("Pixel Researcher unfetched source carries retrieval claims")
            else:
                raise evaluation.OutcomeError("Pixel Researcher provenance retrieval status is invalid")
            source_index[key] = source
            observation_sources.append({
                "batchSha256": batch["batchSha256"], "sourceId": source["sourceId"],
                "retrievalStatus": source["retrievalStatus"], "retrievedAt": source["retrievedAt"],
            })
    if batch_ids != report["batchSha256s"] or provenance["latestBatchCreatedAt"] != batches[-1]["createdAt"]:
        raise evaluation.OutcomeError("Pixel Researcher provenance batch set differs from the report")

    bindings = provenance["citationBindings"]
    if not isinstance(bindings, list) or not 1 <= len(bindings) <= 800:
        raise evaluation.OutcomeError("Pixel Researcher provenance citation inventory is invalid")
    observed_bindings = []
    for binding in bindings:
        binding = evaluation.exact_fields(binding, {
            "findingId", "batchSha256", "sourceId", "contentSha256", "receiptSha256", "evidenceSha256", "status",
        }, "Pixel Researcher provenance citation")
        for field in ("batchSha256", "contentSha256", "receiptSha256", "evidenceSha256"):
            evaluation.valid_hash(binding[field], f"Pixel Researcher provenance citation {field}")
        source = source_index.get((binding["batchSha256"], binding["sourceId"]))
        if (
            FINDING_RE.fullmatch(binding["findingId"] or "") is None or binding["status"] != "present"
            or source is None or source["retrievalStatus"] != "fetched"
            or source["contentSha256"] != binding["contentSha256"]
            or source["receiptSha256"] != binding["receiptSha256"]
        ):
            raise evaluation.OutcomeError("Pixel Researcher provenance citation does not bind a fetched source")
        observed_bindings.append((
            binding["findingId"], binding["batchSha256"], binding["sourceId"], binding["contentSha256"],
            binding["receiptSha256"], binding["evidenceSha256"],
        ))
    if observed_bindings != verified_citations:
        raise evaluation.OutcomeError("Pixel Researcher provenance citations differ from independent verification")
    if [(item[0], item[1], item[2], item[5]) for item in verified_citations] != report_citations:
        raise evaluation.OutcomeError("Pixel Researcher report citations differ from verified evidence")

    source_payload = evaluation.canonical({
        "schemaVersion": 1, "format": "pixel-research-source-provenance-v1",
        "reportArtifactSha256": report_item["sha256"], "verificationArtifactSha256": verification_item["sha256"],
        "batches": batches, "citationBindings": bindings, "provenanceComplete": True,
        "primarySourcePreferenceVerified": False, "privateDataIncluded": False, "externalEffects": False,
        "boundary": SOURCE_EVIDENCE_BOUNDARY,
    })
    observation_payload = evaluation.canonical({
        "schemaVersion": 1, "format": "pixel-research-observation-time-v1",
        "reportCreatedAt": report["createdAt"], "verificationCreatedAt": verification["createdAt"],
        "latestBatchCreatedAt": provenance["latestBatchCreatedAt"],
        "batches": [{"batchSha256": item["batchSha256"], "createdAt": item["createdAt"]} for item in batches],
        "sources": observation_sources, "freshnessLabelsVerified": False,
        "boundary": OBSERVATION_EVIDENCE_BOUNDARY,
    })
    finding_count = len(report["findings"])
    citation_count = len(report_citations)
    citation_payload = evaluation.canonical({
        "schemaVersion": 1, "format": "pixel-research-citation-coverage-v1",
        "reportSha256": verification["reportSha256"], "reportArtifactSha256": report_item["sha256"],
        "verificationArtifactSha256": verification_item["sha256"], "findingCount": finding_count,
        "citationCount": citation_count, "allFindingsCited": citation_count >= finding_count,
        "allEvidencePresent": True, "semanticEntailmentVerified": False, "independent": True,
        "boundary": CITATION_EVIDENCE_BOUNDARY,
    })
    evidence = [
        common.write_evidence(run_dir, "research-source-provenance.json", source_payload, "source-provenance"),
        common.write_evidence(run_dir, "research-observation-time.json", observation_payload, "observation-time"),
        common.write_evidence(run_dir, "research-citation-coverage.json", citation_payload, "citation-coverage"),
    ]
    independent = common.write_evidence(
        run_dir, "research-independent-verification.json", evaluation.canonical(verification), "independent-verifier",
    )
    evidence.append(independent)
    return evidence, independent
