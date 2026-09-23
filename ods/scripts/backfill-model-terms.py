#!/usr/bin/env python3
"""Copy reviewed public source observations into the catalog, without legal inference.

This is a maintainer migration, never an installer/network step. It pins only
artifact paths observed in the reviewed inventory. Missing artifacts and terms
remain explicit review issues; source metadata is not a redistribution grant.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parents[1]
COMMIT = re.compile(r"[0-9a-f]{40}")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def source_url(repo, revision):
    return f"https://huggingface.co/{repo}/tree/{revision}"


def pin_url(url, artifact, revision):
    parsed = urlsplit(url)
    parts = parsed.path.strip("/").split("/")
    if (parsed.scheme != "https" or parsed.netloc != "huggingface.co"
            or parsed.query or parsed.fragment or len(parts) < 5
            or parts[2] != "resolve" or "/".join(parts[:2]) != artifact["repo"]
            or unquote(parts[3]) != artifact["revision"]
            or unquote("/".join(parts[4:])) != artifact["path"]
            or not COMMIT.fullmatch(revision)):
        raise ValueError("Artifact observation does not match catalog URL")
    parts[3] = revision
    return urlunsplit(("https", "huggingface.co", "/" + "/".join(parts), "", ""))


def migrate(catalog_bytes, evidence_bytes):
    catalog = json.loads(catalog_bytes)
    evidence = json.loads(evidence_bytes)
    if digest(catalog_bytes) != evidence["catalog_sha256"]:
        raise ValueError("Catalog changed since the reviewed source inventory")
    entries = evidence["entries"]
    if ([m["id"] for m in catalog["models"]] != [e["id"] for e in entries]
            or len({e["id"] for e in entries}) != len(entries)):
        raise ValueError("Model inventory coverage differs from the catalog")
    observations = {}
    for observation in evidence["repositories"]:
        key = (observation.get("repo"), observation.get("revision"))
        if all(key):
            observations[key] = observation
            requested = observation.get("requested_repo")
            if requested:
                observations[(requested, key[1])] = observation
    result = copy.deepcopy(catalog)
    for model, entry in zip(result["models"], entries):
        review = entry["review"]
        repo = review["artifact_repository"]
        revision = review["artifact_reviewed_revision"]
        if not COMMIT.fullmatch(revision):
            raise ValueError("Source observation has no immutable revision")
        publisher = observations[(repo, revision)]
        if publisher.get("private") or publisher["api_response"].get("status") != 200:
            raise ValueError("Artifact metadata was not publicly verified")
        artifacts = entry["artifacts"]
        targets = model.get("gguf_parts") or [{"url": model["gguf_url"]}]
        if len(targets) != len(artifacts):
            raise ValueError("Multipart inventory is incomplete")
        pinned = []
        for target, artifact in zip(targets, artifacts):
            checked_url = pin_url(target["url"], artifact, revision)
            present = artifact.get("path_present_at_reviewed_revision") is True
            pinned.append({
                "url": checked_url if present else target["url"],
                "path": artifact["path"], "observed_present": present,
            })
            # Never give a missing artifact a misleading immutable identity.
            if present:
                target["url"] = checked_url
        if model.get("gguf_parts"):
            model["gguf_url"] = model["gguf_parts"][0]["url"]
        else:
            model["gguf_url"] = targets[0]["url"]

        sources = []
        source_keys = [(repo, revision, "artifact_publisher")]
        source_keys.extend((base["repo"], base["reviewed_revision"], "declared_base")
                           for base in review["declared_base_repositories"]
                           if base.get("reviewed_revision"))
        source_keys.extend((base["repo"], base["revision"], "declared_ancestor")
                           for base in review["reviewed_ancestry"]
                           if base.get("revision"))
        seen = set()
        for source_repo, source_revision, role in source_keys:
            key = (source_repo, source_revision)
            if key in seen:
                continue
            seen.add(key)
            observed = observations[key]
            card = observed.get("card_metadata") or {}
            readme = next((doc for doc in observed.get("documents", [])
                           if doc["path"] == "README.md"
                           and doc["response"].get("status") == 200), None)
            sources.append({
                "role": role, "repository": source_repo,
                "revision": source_revision, "url": source_url(*key),
                "license_id": card.get("license"),
                "license_name": card.get("license_name"),
                "declaration_url": readme["blob_url"] if readme else
                    f"https://huggingface.co/api/models/{source_repo}/revision/{source_revision}",
                "gated": observed.get("gated"),
            })
        terms_documents = copy.deepcopy(review["retrieved_terms_documents"])
        notices = []
        for source in sources:
            observed = observations[(source["repository"], source["revision"])]
            for doc in observed.get("documents", []):
                response = doc["response"]
                if response.get("status") == 200:
                    notices.append({"repository": source["repository"],
                                    "path": doc["path"], "url": doc["blob_url"],
                                    "sha256": response["sha256"]})
        issues = [gap for gap in review["gap_codes"]
                  if gap not in {"CATALOG_FIELDS_ABSENT", "MUTABLE_ARTIFACT_REF"}]
        if not all(a["observed_present"] for a in pinned):
            issues.append("ARTIFACT_IDENTITY_REQUIRES_REPAIR")
        # These fields deliberately preserve unassessed legal questions. A
        # publisher's SPDX tag or an ungated mirror is not legal acceptance.
        model["source_repo"] = repo
        model["source_revision"] = revision
        model["source_url"] = source_url(repo, revision)
        model["license"] = review["artifact_license_declaration"].get("license")
        model["license_url"] = next((doc["url"] for doc in terms_documents
                                     if doc["repo"] == repo), None)
        model["terms"] = {
            "schema_version": 1,
            "evidence_sha256": digest(evidence_bytes),
            "observed_at": evidence["retrieved_at"],
            "review_status": "not_assessed",
            "sources": sources,
            "artifacts": pinned,
            "license_documents": terms_documents,
            "notice_documents": notices,
            "commercial_use": "not_assessed",
            "upstream_acceptance": "required_by_observed_gating" if any(
                s["gated"] not in (None, False) for s in sources) else "not_assessed",
            "issues": list(dict.fromkeys(issues)),
            "note": review.get("specific_note"),
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=ROOT / "config/model-library.json")
    parser.add_argument("--evidence", type=Path, default=ROOT / "docs/MODEL_TERMS_AUDIT.json")
    parser.add_argument("--output", type=Path, required=True,
                        help="Review this new file before replacing the catalog")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; refusing to overwrite")
    migrated = migrate(args.catalog.read_bytes(), args.evidence.read_bytes())
    args.output.write_text(json.dumps(migrated, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(f"Recorded source observations for {len(migrated['models'])} models; legal review remains open")


if __name__ == "__main__":
    main()
