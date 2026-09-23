#!/usr/bin/env python3
"""Validate all model source records; --release-ready also requires completed review."""
import argparse
import hashlib
import html
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "extensions/services/dashboard-api"))
from model_terms import project_terms, valid_conditions  # noqa: E402

DOWNLOAD_KEYS = ("source_repo", "source_revision", "gguf_file", "gguf_url", "gguf_sha256", "size_bytes", "quantization")
REVIEW_FIELDS = ("review_status", "commercial_use", "upstream_acceptance", "notice_documents",
                 "conditions", "local_notices", "issues", "note")


def retained_notice_errors(review, repository_root, declaration_urls=()):
    """Verify shipped bytes and their source binding without fetching live terms."""
    notices = review.get("local_notices")
    if not isinstance(notices, list) or not notices:
        return ["A completed review must retain local license/notice content"]
    licenses, notice_documents = review.get("retrieved_terms_documents", []), review.get("notice_documents", [])
    if not isinstance(licenses, list) or not isinstance(notice_documents, list):
        return ["A review must explicitly list its license and notice documents"]
    documents = licenses + notice_documents
    sources = {(d.get("url"), d.get("sha256")) for d in documents if isinstance(d, dict)
               and isinstance(d.get("url"), str) and isinstance(d.get("sha256"), str)}
    errors, contents = [], {}
    allowed = repository_root.resolve() / "ods/config/model-notices"
    for notice in notices:
        if not isinstance(notice, dict):
            errors.append("A local notice record is invalid")
            continue
        relative = notice.get("path")
        if (not isinstance(relative, str) or not relative.startswith("ods/config/model-notices/")
                or "\\" in relative or ":" in relative or any(ord(c) < 32 for c in relative)
                or any(part in {"", ".", ".."} for part in relative.split("/"))):
            errors.append("A local notice path is outside the retained notice directory")
            continue
        if (not isinstance(notice.get("source_url"), str)
                or any(not isinstance(notice.get(key), str)
                       or not re.fullmatch(r"[0-9a-f]{64}", notice[key]) for key in ("sha256", "source_sha256"))):
            errors.append("A local notice fingerprint or source is invalid")
            continue
        path = repository_root / relative
        try:
            path.resolve().relative_to(allowed)
            data = path.read_bytes()
        except (OSError, ValueError):
            errors.append("A local notice is missing or escapes the retained notice directory")
            continue
        if hashlib.sha256(data).hexdigest() != notice.get("sha256"):
            errors.append("Retained notice bytes differ from the reviewed fingerprint")
            continue
        source = (notice.get("source_url"), notice.get("source_sha256"))
        if source not in sources:
            errors.append("A retained notice is not bound to a reviewed source document")
            continue
        extraction = notice.get("extraction")
        if extraction == "identity":
            if notice.get("sha256") != notice.get("source_sha256"):
                errors.append("An identity notice differs from the reviewed source bytes")
            else:
                contents[source] = data
        elif extraction == "html_pre_fragment":
            # The evidence snapshot records extraction from one full response.
            # Ship only the complete license block, excluding website scripts.
            try:
                fragment = data.decode("utf-8")
                blocks = re.findall(r"<pre[^>]*>.*?</pre>", fragment, re.S)
                if (type(notice.get("source_block_count")) is not int
                        or notice["source_block_count"] != 1 or len(blocks) != 1
                        or blocks[0] != fragment or "<script" in fragment.lower()):
                    raise ValueError("Not a single license block")
                contents[source] = data
            except (UnicodeError, ValueError):
                errors.append("A retained source excerpt is not the complete unambiguous license block")
        elif extraction != "html_pre_text":
            errors.append("A retained notice uses an unsupported extraction")
    for notice in notices:
        if not isinstance(notice, dict) or notice.get("extraction") != "html_pre_text":
            continue
        if not all(isinstance(notice.get(key), str) for key in ("source_url", "source_sha256")):
            continue
        source = (notice.get("source_url"), notice.get("source_sha256"))
        try:
            source_bytes = contents[source]
            if hashlib.sha256(source_bytes).hexdigest() != notice.get("source_fragment_sha256", source[1]):
                raise ValueError("Wrong source license excerpt")
            raw = source_bytes.decode("utf-8")
            blocks = re.findall(r"<pre[^>]*>(.*?)</pre>", raw, re.S)
            if len(blocks) != 1:
                raise ValueError("Ambiguous source license block")
            extracted = html.unescape(re.sub(r"<[^>]+>", "", blocks[0])).encode("utf-8")
            if hashlib.sha256(extracted).hexdigest() != notice.get("sha256"):
                raise ValueError("Changed extracted text")
        except (KeyError, UnicodeError, ValueError):
            errors.append("An extracted notice does not match the retained source license block")
    # Model cards establish declarations; separate license and attribution
    # documents must also be distributed as retained bytes. Identical license
    # content reached through several source URLs only needs one retained copy.
    required = licenses + [d for d in notice_documents if isinstance(d, dict)
                           and d.get("url") not in declaration_urls]
    retained_hashes = {source[1] for source in contents}
    if any(not isinstance(d, dict) or not isinstance(d.get("sha256"), str)
           or d["sha256"] not in retained_hashes for d in required):
        errors.append("Retained notices do not cover every reviewed license and attribution document")
    return errors


def load_license_review(path, repository_root):
    data = path.read_bytes()
    fingerprint, entries = parse_evidence(data)
    snapshot = json.loads(data)
    inputs = snapshot.get("input_evidence")
    if not isinstance(inputs, list) or not inputs:
        raise ValueError("A license review must identify its frozen source snapshots")
    for record in inputs:
        if (not isinstance(record, dict) or record.get("path") not in {
                "ods/docs/MODEL_TERMS_AUDIT.json", "ods/docs/MODEL_ARTIFACT_REPAIRS.json"}
                or hashlib.sha256((repository_root / record["path"]).read_bytes()).hexdigest() != record.get("sha256")):
            raise ValueError("A license review input differs from its frozen source snapshot")
    for entry in entries.values():
        review = entry["review"]
        if (not isinstance(entry.get("artifact_identity"), dict)
                or not isinstance(entry.get("sources"), list)
                or not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("source_evidence_sha256", "")))
                or any(field not in review for field in REVIEW_FIELDS)
                or review.get("review_status") != "reviewed"
                or not isinstance(review.get("notice_documents"), list)
                or not valid_conditions(review.get("conditions"))):
            raise ValueError("A license review entry is incomplete")
        if entry["source_evidence_sha256"] not in {item["sha256"] for item in inputs}:
            raise ValueError("A license review entry references an input snapshot absent from input_evidence")
        declarations = [source.get("declaration_url") for source in entry["sources"] if isinstance(source, dict)]
        errors = retained_notice_errors(review, repository_root, declarations)
        if errors:
            raise ValueError(entry["id"] + ": " + "; ".join(errors))
    return fingerprint, entries


def license_review_errors(model, snapshots):
    terms = model.get("terms") if isinstance(model.get("terms"), dict) else {}
    fingerprint = terms.get("license_review_evidence_sha256")
    if not fingerprint:
        return (["A completed license review must reference its reviewed evidence snapshot"]
                if terms.get("review_status") == "reviewed" else [])
    if not isinstance(fingerprint, str) or fingerprint not in snapshots:
        return ["The license review fingerprint differs from the supplied review snapshots"]
    entry = snapshots[fingerprint].get(model["id"])
    if entry is None:
        return ["The model is absent from the supplied license review"]
    errors = []
    if entry.get("source_evidence_sha256") != terms.get("evidence_sha256"):
        errors.append("The license review does not match the model's source evidence")
    artifacts = model.get("gguf_parts") or [{"file": model.get("gguf_file"),
                "url": model.get("gguf_url"), "sha256": model.get("gguf_sha256")}]
    if not isinstance(artifacts, list) or any(not isinstance(a, dict) for a in artifacts):
        return errors + ["Catalog artifacts cannot be bound to the license review"]
    identity = {"repository": model.get("source_repo"), "revision": model.get("source_revision"),
                "artifacts": [{key: a.get(key) for key in ("file", "url", "sha256")} for a in artifacts]}
    if entry.get("artifact_identity") != identity:
        errors.append("Artifact identity differs from the reviewed license chain")
    if entry.get("sources") != terms.get("sources"):
        errors.append("Source ancestry differs from the reviewed license chain")
    for field in REVIEW_FIELDS:
        if terms.get(field) != entry["review"].get(field):
            errors.append("License review field differs from the evidence: " + field)
    if terms.get("license_documents") != entry["review"]["retrieved_terms_documents"]:
        errors.append("License documents differ from the supplied license review")
    return errors


def load_evidence(path):
    """Read the maintainer's observation snapshot only at the CLI boundary."""
    return parse_evidence(path.read_bytes())


def parse_evidence(data):
    """Hash and validate the same immutable byte sequence."""
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


def evidence_errors(model, fingerprint, entries, license_review_bound=False):
    errors = []
    terms = model.get("terms")
    if not isinstance(terms, dict) or terms.get("evidence_sha256") != fingerprint:
        errors.append("The source evidence fingerprint differs from the supplied snapshot")
    entry = entries.get(model["id"])
    if entry is None:
        errors.append("The model is absent from the supplied source evidence")
    elif (not license_review_bound and isinstance(terms, dict)
          and terms.get("license_documents") != entry["review"]["retrieved_terms_documents"]):
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
    parser.add_argument("--review-evidence", type=Path, action="append",
                        help="Factual license review snapshot, distinct from source observations")
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
        reviews = {}
        reviewed_ids = set()
        # Fixture catalogs may supply their own source snapshots without reviews.
        review_paths = args.review_evidence or ([] if args.evidence else [ROOT / "docs/MODEL_LICENSE_REVIEWS.json"])
        for path in review_paths:
            fingerprint, entries = load_license_review(path, ROOT.parent)
            if fingerprint in reviews or reviewed_ids.intersection(entries):
                raise ValueError("License review evidence must be unambiguous for each model")
            reviews[fingerprint] = entries
            reviewed_ids.update(entries)
    except (OSError, ValueError) as error:
        print(json.dumps({"evidenceError": str(error)}))
        return 1
    records = [project_terms(model) for model in models]
    malformed = [r for r in records if not r["recordValid"]]
    unbound = []
    for model in models:
        terms = model.get("terms")
        fingerprint = terms.get("evidence_sha256") if isinstance(terms, dict) else None
        review_errors = license_review_errors(model, reviews)
        review_bound = bool(isinstance(terms, dict) and terms.get("license_review_evidence_sha256") and not review_errors)
        if model["id"] in replacements and fingerprint != replacements[model["id"]]:
            errors = ["The model must reference its supplemental artifact replacement evidence"]
        elif not isinstance(fingerprint, str) or fingerprint not in snapshots:
            errors = ["The source evidence fingerprint differs from the supplied snapshots"]
        else:
            errors = evidence_errors(model, fingerprint, snapshots[fingerprint], review_bound)
        errors.extend(review_errors)
        if errors:
            unbound.append({"modelId": model["id"], "errors": errors})
    pending = [r["modelId"] for r in records if not r["releaseReady"]]
    print(json.dumps({"models": len(models), "invalidRecords": malformed,
                      "evidenceErrors": unbound,
                      "pendingReview": pending}, indent=2))
    return int(bool(malformed) or bool(unbound) or bool(args.release_ready and pending))


if __name__ == "__main__":
    raise SystemExit(main())
