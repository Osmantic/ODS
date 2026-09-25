#!/usr/bin/env python3
"""Build a content-free, fail-closed coverage ledger for the private owner trial.

The ledger distinguishes deterministic rehearsals from full product-path proof and
records profile substitutions instead of allowing a Builder fixture to stand in
for Assistant, Researcher, or Controller behavior.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

import portal_outcome_evaluation as evaluation
import portal_outcome_materialize_battery as materializer
import portal_outcome_orchestrate as codex_orchestrate
import portal_outcome_pixel_orchestrate as pixel_orchestrate


COVERAGE_SCHEMA = "https://osmantic.com/pixel/schemas/portal-outcome-trial-coverage-v1.schema.json"
COVERAGE_BOUNDARY = (
    "Content-free owner-trial coverage ledger only. It binds private source identity, sanitized journeys, product "
    "routes, and comparison tasks without reproducing private content. Rehearsal, profile matching, and adapter "
    "availability are not product capability proof and grant no execution, provider, external-effect, completion, "
    "acceptance, publication, deployment, or promotion authority."
)
PROFILES = frozenset({"assistant", "builder", "controller", "researcher"})
STATUSES = frozenset({
    "formal-product-proof-ready", "profile-matched-rehearsal-only",
    "surrogate-profile-rehearsal-only", "uncovered",
})
BLOCKERS = frozenset({
    "no-formal-product-proof", "no-rehearsal", "surrogate-profile",
    "pixel-product-adapter-missing", "codex-comparison-adapter-missing",
})


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _profile_set(values: Iterable[str], label: str) -> frozenset[str]:
    observed = list(values)
    if len(observed) != len(set(observed)) or any(value not in PROFILES for value in observed):
        raise evaluation.OutcomeError(f"{label} is invalid")
    return frozenset(observed)


def _private_source(payload: bytes, *, expected_sha256: str, expected_bytes: int, expected_lines: int) -> bool:
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise evaluation.OutcomeError("private owner-trial transcript is not strict UTF-8") from exc
    if _sha(payload) != expected_sha256 or len(payload) != expected_bytes or len(text.splitlines()) != expected_lines:
        raise evaluation.OutcomeError("private owner-trial transcript differs from its exact corpus identity")
    return True


def build_coverage(
    *, trial_payload: bytes, product_corpus_payload: bytes, battery_payload: bytes,
    source_payload: bytes | None = None, pixel_profiles: Iterable[str], codex_profiles: Iterable[str],
) -> dict[str, Any]:
    trial = evaluation.parse_json(trial_payload, "sanitized owner-trial journey corpus")
    trial = evaluation.exact_fields(trial, {
        "$schema", "schemaVersion", "operation", "sourceTranscriptSha256", "sourceTranscriptBytes",
        "provenance", "behavioralContract", "journeys", "privacy", "boundary",
    }, "sanitized owner-trial journey corpus")
    if (
        trial["$schema"] != "https://osmantic.com/pixel/schemas/portal-user-trial-journey-v1.schema.json"
        or trial["schemaVersion"] != 1 or trial["operation"] != "pixel-portal-user-trial-journeys"
    ):
        raise evaluation.OutcomeError("sanitized owner-trial journey corpus identity is invalid")
    evaluation.valid_hash(trial["sourceTranscriptSha256"], "owner-trial transcript")
    expected_bytes = evaluation.integer(trial["sourceTranscriptBytes"], 1, 1073741824, "owner-trial transcript bytes")

    product = evaluation.parse_json(product_corpus_payload, "portal product journey corpus")
    if not isinstance(product, dict) or product.get("schemaVersion") != 1 or not isinstance(product.get("source"), dict):
        raise evaluation.OutcomeError("portal product journey corpus identity is invalid")
    source = product["source"]
    expected_lines = evaluation.integer(source.get("lineCount"), 1, 10000000, "owner-trial transcript lines")
    if source.get("sha256") != trial["sourceTranscriptSha256"]:
        raise evaluation.OutcomeError("sanitized and product journey corpora bind different owner trials")

    battery = materializer.validate_battery(battery_payload)
    pixel = _profile_set(pixel_profiles, "Pixel product adapter profile registry")
    codex = _profile_set(codex_profiles, "Codex comparison adapter profile registry")
    trial_journeys = trial["journeys"]
    product_journeys = product.get("journeys")
    if not isinstance(trial_journeys, list) or not trial_journeys or not isinstance(product_journeys, list):
        raise evaluation.OutcomeError("owner-trial or product journey inventory is invalid")
    trial_index: dict[str, dict[str, Any]] = {}
    for journey in trial_journeys:
        if not isinstance(journey, dict) or not isinstance(journey.get("id"), str) or journey.get("profile") not in PROFILES:
            raise evaluation.OutcomeError("sanitized owner-trial journey identity is invalid")
        if journey["id"] in trial_index:
            raise evaluation.OutcomeError("sanitized owner-trial journey is duplicated")
        trial_index[journey["id"]] = journey
    product_index = {
        journey.get("id"): journey for journey in product_journeys
        if isinstance(journey, dict) and isinstance(journey.get("id"), str)
    }
    if len(product_index) != len(product_journeys):
        raise evaluation.OutcomeError("portal product journey identity is invalid or duplicated")

    mappings: dict[str, list[dict[str, Any]]] = {journey_id: [] for journey_id in trial_index}
    for task in battery["tasks"]:
        rehearsal = task.get("rehearsal")
        trial_proof = task.get("trialProof")
        if rehearsal is None and trial_proof is None:
            continue
        binding = rehearsal if rehearsal is not None else trial_proof
        product_journey = product_index.get(binding["journeyId"] if rehearsal is not None else binding["productJourneyId"])
        if product_journey is None or product_journey.get("profile") not in PROFILES:
            raise evaluation.OutcomeError("battery trial mapping references an unknown product journey")
        execution_profile = task.get("profile")
        if execution_profile not in {"assistant", "builder", "controller", "researcher"}:
            raise evaluation.OutcomeError("battery trial mapping omits its exact execution profile")
        for trial_id in binding["trialJourneyIds"]:
            trial_journey = trial_index.get(trial_id)
            if trial_journey is None:
                raise evaluation.OutcomeError("battery trial mapping references an unknown owner-trial journey")
            mappings[trial_id].append({
                "batteryTaskId": task["taskId"], "partition": rehearsal["partition"] if rehearsal is not None else task.get("partition", "tuning"),
                "proofClass": binding["proofClass"],
                "productJourneyId": rehearsal["journeyId"] if rehearsal is not None else trial_proof["productJourneyId"],
                "executionProfile": execution_profile, "targetProfile": product_journey["profile"],
                "profileMatch": execution_profile == trial_journey["profile"] == product_journey["profile"],
            })

    entries = []
    for trial_id, journey in trial_index.items():
        mapped = sorted(mappings[trial_id], key=lambda item: item["batteryTaskId"])
        formal_tasks = [item["batteryTaskId"] for item in mapped if item["proofClass"] == materializer.FORMAL_PRODUCT_TASK_PROOF_CLASS and item["profileMatch"]]
        rehearsals = [item for item in mapped if item["proofClass"] == materializer.REHEARSAL_PROOF_CLASS]
        exact = [item["batteryTaskId"] for item in rehearsals if item["profileMatch"]]
        surrogate = [item["batteryTaskId"] for item in rehearsals if not item["profileMatch"]]
        # Formal task definitions remain separate from completed proof. A definition names work that must be
        # run through the real product path; only an independently verified campaign receipt may populate proof.
        formal: list[str] = []
        blockers: set[str] = set()
        if not formal:
            blockers.add("no-formal-product-proof")
        if not mapped:
            blockers.add("no-rehearsal")
        if surrogate:
            blockers.add("surrogate-profile")
        if journey["profile"] not in pixel:
            blockers.add("pixel-product-adapter-missing")
        if journey["profile"] not in codex:
            blockers.add("codex-comparison-adapter-missing")
        if formal and journey["profile"] in pixel and journey["profile"] in codex:
            status = "formal-product-proof-ready"
        elif exact:
            status = "profile-matched-rehearsal-only"
        elif mapped:
            status = "surrogate-profile-rehearsal-only"
        else:
            status = "uncovered"
        if status not in STATUSES or any(item not in BLOCKERS for item in blockers):
            raise evaluation.OutcomeError("owner-trial coverage classification is invalid")
        entries.append({
            "trialJourneyId": trial_id, "profile": journey["profile"], "status": status,
            "pixelProductAdapterImplemented": journey["profile"] in pixel,
            "codexComparisonAdapterImplemented": journey["profile"] in codex,
            "formalProductTaskIds": formal_tasks, "formalProductProofTaskIds": formal, "profileMatchedRehearsalTaskIds": exact,
            "surrogateRehearsalTaskIds": surrogate, "mappings": mapped, "blockers": sorted(blockers),
        })

    source_verified = False
    if source_payload is not None:
        source_verified = _private_source(
            source_payload, expected_sha256=trial["sourceTranscriptSha256"],
            expected_bytes=expected_bytes, expected_lines=expected_lines,
        )
    formal_ready = sum(item["status"] == "formal-product-proof-ready" for item in entries)
    formal_defined = sum(bool(item["formalProductTaskIds"]) for item in entries)
    matched = sum(item["status"] == "profile-matched-rehearsal-only" for item in entries)
    surrogate = sum(item["status"] == "surrogate-profile-rehearsal-only" for item in entries)
    uncovered = sum(item["status"] == "uncovered" for item in entries)
    return {
        "$schema": COVERAGE_SCHEMA, "schemaVersion": 1, "operation": "pixel-portal-outcome-trial-coverage",
        "source": {
            "sha256": trial["sourceTranscriptSha256"], "bytes": expected_bytes,
            "lineCount": expected_lines, "verifiedFromOwnerCopy": source_verified,
        },
        "trialCorpusSha256": _sha(trial_payload), "productCorpusSha256": _sha(product_corpus_payload),
        "batterySha256": _sha(battery_payload),
        "implementedProfiles": {"pixelProduct": sorted(pixel), "codexComparison": sorted(codex)},
        "summary": {
            "totalJourneys": len(entries), "formalProductProofReady": formal_ready,
            "formalProductTaskDefined": formal_defined,
            "profileMatchedRehearsalOnly": matched, "surrogateProfileRehearsalOnly": surrogate,
            "uncovered": uncovered, "releaseStatus": "ready" if source_verified and formal_ready == len(entries) else "blocked",
        },
        "journeys": entries, "boundary": COVERAGE_BOUNDARY,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--root", type=Path, default=root)
    parser.add_argument("--source-transcript", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        selected = args.root.resolve(strict=True)
        source_payload = args.source_transcript.resolve(strict=True).read_bytes() if args.source_transcript else None
        result = build_coverage(
            trial_payload=(selected / "security-evals/portal-user-trial/trial-journeys-v1.json").read_bytes(),
            product_corpus_payload=(selected / "security-evals/portal-user-journeys/corpus-v1.json").read_bytes(),
            battery_payload=(selected / "security-evals/agent-comparison/task-battery-v1.json").read_bytes(),
            source_payload=source_payload,
            pixel_profiles=pixel_orchestrate.IMPLEMENTED_PRODUCT_PROFILES,
            codex_profiles=codex_orchestrate.IMPLEMENTED_CODEX_PROFILES,
        )
        evaluation.write_new_private(args.output.resolve(), json.dumps(result, indent=2, ensure_ascii=False).encode("utf-8") + b"\n")
        print(json.dumps(result["summary"], sort_keys=True, separators=(",", ":")))
        return 0 if result["summary"]["releaseStatus"] == "ready" else 3
    except (evaluation.OutcomeError, OSError, UnicodeError) as exc:
        print(f"[pixel] ERROR: {exc}", file=__import__("sys").stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
