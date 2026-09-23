#!/usr/bin/env python3
"""Check PB-013 legacy recipe evidence coverage and drift, not legal clearance.

No network, image pull or code from the recorded upstream projects is executed.
Existing records outside this explicitly audited batch retain their own format.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlparse

LEGACY_RECIPES = tuple("""aider anythingllm audiocraft bark baserow chromadb continue
crewai dify flowise forge frigate gaia gitea immich invokeai jan jupyter label-studio
langflow librechat localai milvus miniflux ntfy ollama open-interpreter paperless-ngx
piper-audio rvc sillytavern text-generation-webui weaviate xtts""".split())
REVISION_SCOPES = {
    "configured_version_reference", "reviewed_current_source_only",
    "image_declared_revision", "npm_declared_gitHead",
    "reviewed_source_reference_only",
}


def runtime_files(directory: Path) -> dict[str, str]:
    """Include overlays, disabled compose, build inputs, config and manifests."""
    return {
        path.relative_to(directory).as_posix(): hashlib.sha256(
            path.read_bytes().replace(b"\r\n", b"\n")
        ).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file() and path.name != "upstream.json" and path.suffix.lower() != ".md"
    }


def https_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc) and not parsed.username


def validate_record(directory: Path) -> list[str]:
    errors: list[str] = []
    try:
        record = json.loads((directory / "upstream.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"missing or invalid upstream.json: {exc}"]
    if not isinstance(record, dict):
        return ["upstream.json must be an object"]
    for field in ("repository", "source_url", "license_url"):
        if not https_url(record.get(field)):
            errors.append(f"{field} must be an HTTPS evidence URL")
    if record.get("schema_version") != "ods.upstream.v1":
        errors.append("unexpected provenance schema")
    revision = record.get("source_revision", "")
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        errors.append("source_revision must be a reviewed full Git commit")
    elif record.get("source_url") != f"{record.get('repository')}/tree/{revision}":
        errors.append("source_url does not match repository/revision")
    if record.get("source_revision_scope") not in REVISION_SCOPES:
        errors.append("source revision scope must distinguish release, current source and publisher claims")
    for field in ("license", "license_scope", "notes", "provenance_checked_at"):
        if not isinstance(record.get(field), str) or not record[field].strip():
            errors.append(f"{field} is required")
    if record.get("review_status") != "provenance_recorded_with_gaps":
        errors.append("this backfill cannot claim completed license clearance")
    gaps = record.get("gaps")
    if not isinstance(gaps, list) or not gaps or any(not isinstance(gap, str) or not gap.strip() for gap in gaps):
        errors.append("specific unresolved provenance/terms gaps must remain explicit")
    documents = record.get("license_documents")
    if not isinstance(documents, list) or not documents:
        errors.append("license document evidence is required")
    else:
        for document in documents:
            if (not isinstance(document, dict) or not https_url(document.get("url"))
                    or not re.fullmatch(r"[0-9a-f]{64}", str(document.get("sha256", "")))):
                errors.append("license document needs an evidence URL and SHA-256")
    notices = record.get("notice_urls")
    if not isinstance(notices, list) or any(not https_url(url) for url in notices):
        errors.append("notice_urls must be an explicit list of HTTPS evidence URLs")
    if not (directory / "NOTICE.md").is_file():
        errors.append("recipe attribution/provenance NOTICE.md is missing")
    terms = record.get("model_terms")
    if not isinstance(terms, dict) or not terms.get("status") or not terms.get("summary") or not isinstance(terms.get("sources"), list):
        errors.append("model terms must be assessed separately from application code")
    deployment = record.get("deployment")
    if not isinstance(deployment, dict):
        errors.append("deployment observation is required")
        return errors
    observed_images = deployment.get("images")
    if not isinstance(observed_images, list) or not observed_images:
        errors.append("configured image/base observations are required")
    else:
        expected_images = []
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            if path.name.startswith("compose.") and (".yaml" in path.name or ".yml" in path.name):
                for match in re.finditer(r"^\s*image:\s*([^#\n]+)", path.read_text(), re.MULTILINE):
                    expected_images.append((path.relative_to(directory).as_posix(), match[1].strip().strip("\"'")))
            elif path.name.startswith("Dockerfile"):
                for match in re.finditer(r"^FROM\s+(?:--platform=\S+\s+)?(\S+)", path.read_text(), re.MULTILINE | re.IGNORECASE):
                    expected_images.append((path.relative_to(directory).as_posix(), match[1]))
        actual_images = [(item.get("file"), item.get("image")) for item in observed_images if isinstance(item, dict)]
        if sorted(actual_images, key=str) != sorted(expected_images, key=str):
            errors.append("image observations do not match literal compose/build inputs")
        application_images = [item.get("image") for item in observed_images if isinstance(item, dict) and item.get("service") == directory.name]
        if application_images and record.get("image") not in application_images:
            errors.append("top-level image does not match the application service")
    if deployment.get("hash_normalization") != "CRLF_to_LF":
        errors.append("recipe hash normalization must be explicit")
    file_records = deployment.get("recipe_files")
    if not isinstance(file_records, list) or any(not isinstance(item, dict) for item in file_records):
        errors.append("recipe_files must inventory the reviewed local inputs")
    else:
        expected = {item.get("path"): item.get("sha256") for item in file_records}
        if len(expected) != len(file_records):
            errors.append("duplicate recipe input path")
        if expected != runtime_files(directory):
            errors.append("recipe inputs changed; review provenance and refresh their hashes")
    if record.get("source_revision_scope") == "image_declared_revision":
        variants = record.get("image_observation", {}).get("variants", [])
        if not variants or any(item.get("labels", {}).get("org.opencontainers.image.revision") != revision for item in variants):
            errors.append("image-declared revision must agree with every observed platform label")
    artifact = record.get("package_artifact")
    if artifact and (not https_url(artifact.get("artifact", {}).get("url"))
                     or not re.fullmatch(r"[0-9a-f]{64}", artifact.get("artifact", {}).get("sha256", ""))):
        errors.append("exact package artifact needs a public URL and SHA-256")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    library = args.project_dir / "extensions/library/services"
    errors = []
    for name in LEGACY_RECIPES:
        errors.extend(f"{name}: {error}" for error in validate_record(library / name))
    for error in errors:
        print(f"ERROR {error}")
    print(f"Legacy recipe provenance: {len(LEGACY_RECIPES)} records, {len(errors)} errors. "
          "Coverage/drift check only; recorded legal and source-binding gaps remain open.")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
