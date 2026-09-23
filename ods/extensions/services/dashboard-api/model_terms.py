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
    parts = url.path.removeprefix("/").split("/")
    return (url.netloc == "huggingface.co" and not url.query and not url.fragment
            and len(parts) >= 5 and parts[2] == operation
            and "/".join(parts[:2]) == repository and parts[3] == revision
            and unquote("/".join(parts[4:])) == path)


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
