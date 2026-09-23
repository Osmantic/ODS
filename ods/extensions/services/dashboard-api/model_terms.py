"""Public model provenance projection; observations are not legal clearance."""
import copy
import hashlib
import json
import re
from urllib.parse import unquote, urlsplit


def https_url(value):
    if not isinstance(value, str):
        return False
    try:
        url = urlsplit(value)
        return url.scheme == "https" and bool(url.hostname) and not url.username and not url.password
    except ValueError:
        return False


def source_document_url(value, repository, revision, path, operation):
    """Bind a public file URL to one exact source and relative file path."""
    if (not https_url(value) or not isinstance(path, str) or not path
            or "\\" in path or any(ord(char) < 32 for char in path)
            or any(part in {"", ".", ".."} for part in path.split("/"))):
        return False
    url = urlsplit(value)
    parts = (url.path[1:] if url.path.startswith("/") else url.path).split("/")
    return (url.netloc == "huggingface.co" and not url.query and not url.fragment
            and len(parts) >= 5 and parts[2] == operation
            and "/".join(parts[:2]) == repository and parts[3] == revision
            and unquote("/".join(parts[4:])) == path)


def valid_conditions(conditions):
    """A completed review must expose readable conditions, not hidden placeholders."""
    return bool(isinstance(conditions, list) and conditions and all(
        isinstance(condition, dict) and all(
            isinstance(condition.get(field), str) and condition[field].strip()
            for field in ("trigger", "requirement", "basis", "license_group")
        ) for condition in conditions
    ))


def validate_terms(model):
    errors = []
    terms = model.get("terms")
    if not isinstance(terms, dict) or terms.get("schema_version") != 1:
        return ["A versioned source and terms record is missing"]
    if not re.fullmatch(r"[0-9a-f]{64}", str(terms.get("evidence_sha256", ""))):
        errors.append("The source evidence fingerprint is missing")
    sources = terms.get("sources")
    if not isinstance(sources, list) or not sources:
        return errors + ["No publisher/source observations are recorded"]
    publishers = [s for s in sources if isinstance(s, dict) and s.get("role") == "artifact_publisher"]
    if len(publishers) != 1:
        errors.append("Exactly one artifact publisher must be recorded")
    elif (publishers[0].get("repository") != model.get("source_repo")
          or publishers[0].get("revision") != model.get("source_revision")):
        errors.append("The publisher identity differs from the catalog source")
    for source in sources:
        if not isinstance(source, dict):
            errors.append("A source observation is invalid")
            continue
        repository, revision = source.get("repository", ""), source.get("revision", "")
        source_root = f"https://huggingface.co/{repository}"
        declaration_urls = {
            f"{source_root}/blob/{revision}/README.md",
            f"https://huggingface.co/api/models/{repository}/revision/{revision}",
        }
        if (not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", str(repository))
                or not re.fullmatch(r"[0-9a-f]{40}", str(source.get("revision", "")))
                or source.get("url") != f"{source_root}/tree/{revision}"
                or source.get("declaration_url") not in declaration_urls
                or source.get("role") not in {"artifact_publisher", "declared_base", "declared_ancestor"}):
            errors.append("A source observation lacks a pinned public identity")
    issues = terms.get("issues")
    if not isinstance(issues, list) or any(not isinstance(issue, str) for issue in issues):
        errors.append("Review issues must be an explicit list")
        issues = []
    artifacts = terms.get("artifacts")
    actual = model.get("gguf_parts") or [{"url": model.get("gguf_url")}]
    if not isinstance(actual, list) or any(not isinstance(a, dict) for a in actual):
        return errors + ["Catalog artifact list is invalid"]
    if (not isinstance(artifacts, list) or any(not isinstance(a, dict) for a in artifacts)
            or [a.get("url") for a in artifacts] != [a.get("url") for a in actual]):
        errors.append("The terms record does not cover the exact catalog artifacts")
    else:
        for artifact in artifacts:
            if not https_url(artifact.get("url")) or type(artifact.get("observed_present")) is not bool:
                errors.append("Artifact observation is invalid")
                continue
            if artifact.get("observed_present"):
                if not source_document_url(artifact["url"], model.get("source_repo"),
                                           model.get("source_revision"), artifact.get("path"), "resolve"):
                    errors.append("Observed artifact is not bound to its reviewed source revision and path")
            elif "ARTIFACT_IDENTITY_REQUIRES_REPAIR" not in issues:
                errors.append("Missing artifact is not identified as requiring repair")
    for key in ("license_documents", "notice_documents"):
        if not isinstance(terms.get(key), list):
            errors.append(f"{key} must explicitly record available documents")
            continue
        for document in terms[key]:
            if (not isinstance(document, dict) or not https_url(document.get("url"))
                    or not re.fullmatch(r"[0-9a-f]{64}", str(document.get("sha256", "")))):
                errors.append("A terms/notice document is missing its source or fingerprint")
                continue
            if key == "notice_documents" and not any(
                isinstance(source, dict) and source.get("repository") == document.get("repository")
                and source_document_url(document["url"], source.get("repository"),
                                        source.get("revision"), document.get("path"), "blob")
                for source in sources
            ):
                errors.append("A notice document is not bound to a recorded source revision and path")
    if terms.get("review_status") not in {"not_assessed", "reviewed"}:
        errors.append("The terms review status is missing")
    if terms.get("commercial_use") not in {"not_assessed", "permitted_with_conditions", "restricted"}:
        errors.append("Commercial-use status must be explicit")
    if terms.get("upstream_acceptance") not in {"not_assessed", "required_by_observed_gating", "required", "not_required"}:
        errors.append("Upstream acceptance status must be explicit")
    if terms.get("review_status") == "reviewed":
        if (not terms.get("license_documents") or not terms.get("notice_documents")
                or any(not isinstance(s, dict) or not s.get("license_id") for s in sources)):
            errors.append("A completed review requires license declarations and retained terms/notices")
        if not valid_conditions(terms.get("conditions")):
            errors.append("A completed review requires explicit readable license conditions")
        if not re.fullmatch(r"[0-9a-f]{64}", str(terms.get("license_review_evidence_sha256", ""))):
            errors.append("A completed review requires its license evidence fingerprint")
    return errors


def project_terms(model):
    """Return only public observations; a missing record is never a clean result."""
    errors = validate_terms(model)
    terms = copy.deepcopy(model.get("terms")) if not errors else None
    binding = {"id": model.get("id"), "artifacts": model.get("gguf_parts") or [{
        "url": model.get("gguf_url"), "sha256": model.get("gguf_sha256"),
        "file": model.get("gguf_file"),
    }], "terms": terms}
    fingerprint = hashlib.sha256(json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"modelId": model.get("id"), "name": model.get("name"),
            "recordValid": not errors, "errors": errors, "terms": terms,
            "termsDigest": fingerprint,
            "releaseReady": bool(terms and terms["review_status"] == "reviewed"
                                 and terms["commercial_use"] != "not_assessed"
                                 and terms["upstream_acceptance"] != "not_assessed"
                                 and not terms["issues"])}


def download_review_error(model, acknowledgement):
    """Require an explicit review of this exact artifact, not implied consent.

    Acknowledging recorded information never changes its review status or
    grants rights. Upstream access restrictions remain enforced by the publisher.
    """
    projection = project_terms(model)
    base = {"modelId": model.get("id"), "termsDigest": projection["termsDigest"]}
    if not projection["recordValid"]:
        return {**base, "status": 412, "code": "model_terms_incomplete",
                "error": "Source and license information is incomplete. Refresh this model's source record before downloading."}
    if not isinstance(acknowledgement, dict) or acknowledgement.get("acknowledged") is not True:
        return {**base, "status": 428, "code": "model_terms_review_required",
                "error": "Review this model's sources and terms before starting its download."}
    if acknowledgement.get("termsDigest") != projection["termsDigest"]:
        return {**base, "status": 409, "code": "model_terms_changed",
                "error": "The model or its terms changed. Review the current information before downloading."}
    if (projection["terms"]["upstream_acceptance"] in {"required", "required_by_observed_gating"}
            and acknowledgement.get("upstreamAccepted") is not True):
        return {**base, "status": 428, "code": "model_upstream_acceptance_required",
                "error": "Complete the publisher's required acceptance and confirm it before downloading."}
    return None


def hub_source_observation(model, *, license_id=None, license_url=None, gated=False, declared_bases=None):
    """Record public Hub declarations without treating a card tag as clearance.

    Arbitrary imports are not automatically assigned curated/legal review. Their
    source card and unverified base declarations remain visible to the owner.
    """
    repository, revision = model["source_repo"], model["source_revision"]
    source = {"role": "artifact_publisher", "repository": repository,
              "revision": revision, "url": f"https://huggingface.co/{repository}/tree/{revision}",
              "declaration_url": f"https://huggingface.co/api/models/{repository}/revision/{revision}",
              "license_id": license_id if isinstance(license_id, str) else None}
    if https_url(license_url):
        source["license_url"] = license_url
    observations = {"source": source, "gated": bool(gated), "declared_bases": declared_bases or []}
    evidence_hash = hashlib.sha256(json.dumps(observations, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    artifacts = model.get("gguf_parts") or [{"url": model["gguf_url"]}]
    return {"schema_version": 1, "evidence_sha256": evidence_hash,
            "sources": [source], "review_status": "not_assessed",
            "commercial_use": "not_assessed",
            "upstream_acceptance": "required_by_observed_gating" if gated else "not_assessed",
            "license_documents": [], "notice_documents": [],
            "declared_base_repositories": declared_bases or [],
            "artifacts": [{"url": item["url"],
                           "path": unquote("/".join(urlsplit(item["url"]).path.split("/")[5:])),
                           "observed_present": True} for item in artifacts],
            "issues": ["COMMUNITY_MODEL_TERMS_NOT_REVIEWED"],
            "note": "Community import: the publisher's declarations are shown, but ODS has not reviewed the full license chain, commercial-use conditions or required notices. Read the publisher's terms; this acknowledgement does not accept them on your behalf or establish permission."}
