#!/usr/bin/env python3
"""Validate all model source records; --release-ready also requires completed review."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "extensions/services/dashboard-api"))
from model_terms import project_terms  # noqa: E402

DOWNLOAD_KEYS = ("source_repo", "source_revision", "gguf_file", "gguf_url", "gguf_sha256", "size_bytes", "quantization")


def load_evidence(path):
    """Read the maintainer's observation snapshot only at the CLI boundary."""
    data = path.read_bytes()
    evidence = json.loads(data)
    if not isinstance(evidence, dict) or evidence.get("schema_version") != 1:
        raise ValueError("The source evidence must have schema version 1")
    entries = evidence.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("The source evidence must contain a nonempty entries list")
    indexed = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not entry["id"].strip():
            raise ValueError("Each evidence entry must have a nonempty identity")
        if entry["id"] in indexed:
            raise ValueError("Evidence identities must be unique")
        review = entry.get("review")
        documents = review.get("retrieved_terms_documents") if isinstance(review, dict) else None
        if not isinstance(documents, list) or any(not isinstance(doc, dict) for doc in documents):
            raise ValueError("Each evidence entry must explicitly list retrieved terms documents")
        if "verified_download" in entry:
            verified = entry["verified_download"]
            if (not isinstance(verified, dict)
                    or any(not isinstance(verified.get(key), str) or not verified[key].strip()
                           for key in DOWNLOAD_KEYS if key != "size_bytes")
                    or type(verified.get("size_bytes")) is not int or verified["size_bytes"] <= 0
                    or not re.fullmatch(r"[0-9a-f]{40}", verified["source_revision"])
                    or not re.fullmatch(r"[0-9a-f]{64}", verified["gguf_sha256"])):
                raise ValueError("Verified artifact replacements must specify the complete immutable download identity")
        indexed[entry["id"]] = entry
    return hashlib.sha256(data).hexdigest(), indexed


def evidence_errors(model, fingerprint, entries):
    errors = []
    terms = model.get("terms")
    if not isinstance(terms, dict) or terms.get("evidence_sha256") != fingerprint:
        errors.append("The source evidence fingerprint differs from the supplied snapshot")
    entry = entries.get(model["id"])
    if entry is None:
        errors.append("The model is absent from the supplied source evidence")
    elif isinstance(terms, dict) and terms.get("license_documents") != entry["review"]["retrieved_terms_documents"]:
        # Preserve external publisher license links, but require the exact
        # reviewed records (including publisher, URL and content fingerprint).
        errors.append("License documents differ from the supplied source evidence")
    if entry is not None and "verified_download" in entry:
        verified = entry["verified_download"]
        if any(model.get(key) != verified[key] for key in DOWNLOAD_KEYS):
            errors.append("Download identity differs from the verified artifact replacement")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=ROOT / "config/model-library.json")
    parser.add_argument("--evidence", type=Path, action="append",
                        help="Reviewed snapshot; repeat for supplemental observations")
    parser.add_argument("--release-ready", action="store_true")
    args = parser.parse_args()
    try:
        catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
        models = catalog.get("models") if isinstance(catalog, dict) else None
        if not isinstance(models, list) or not models:
            raise ValueError("The model catalog must contain a nonempty models list")
        identities = []
        for model in models:
            if not isinstance(model, dict) or not isinstance(model.get("id"), str) or not model["id"].strip():
                raise ValueError("Each model must have a nonempty identity")
            identities.append(model["id"])
        if len(set(identities)) != len(identities):
            raise ValueError("Model identities must be unique")
    except (OSError, ValueError) as error:
        print(json.dumps({"catalogError": str(error)}))
        return 1
    try:
        evidence_paths = args.evidence or [ROOT / "docs/MODEL_TERMS_AUDIT.json",
                                          ROOT / "docs/MODEL_ARTIFACT_REPAIRS.json"]
        snapshots = {}
        replacements = {}
        for path in evidence_paths:
            fingerprint, entries = load_evidence(path)
            if fingerprint in snapshots:
                raise ValueError("Source evidence snapshots must be distinct")
            for identity, entry in entries.items():
                if "verified_download" in entry:
                    if identity in replacements:
                        raise ValueError("Artifact replacements must have one unambiguous evidence snapshot")
                    replacements[identity] = fingerprint
            snapshots[fingerprint] = entries
    except (OSError, ValueError) as error:
        print(json.dumps({"evidenceError": str(error)}))
        return 1
    records = [project_terms(model) for model in models]
    malformed = [r for r in records if not r["recordValid"]]
    unbound = []
    for model in models:
        terms = model.get("terms")
        fingerprint = terms.get("evidence_sha256") if isinstance(terms, dict) else None
        if model["id"] in replacements and fingerprint != replacements[model["id"]]:
            errors = ["The model must reference its supplemental artifact replacement evidence"]
        elif fingerprint not in snapshots:
            errors = ["The source evidence fingerprint differs from the supplied snapshots"]
        else:
            errors = evidence_errors(model, fingerprint, snapshots[fingerprint])
        if errors:
            unbound.append({"modelId": model["id"], "errors": errors})
    pending = [r["modelId"] for r in records if not r["releaseReady"]]
    print(json.dumps({"models": len(models), "invalidRecords": malformed,
                      "evidenceErrors": unbound,
                      "pendingReview": pending}, indent=2))
    return int(bool(malformed) or bool(unbound) or bool(args.release_ready and pending))


if __name__ == "__main__":
    raise SystemExit(main())
