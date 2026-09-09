#!/usr/bin/env python3
"""Fail closed on an incomplete or widened Portal dependency lock."""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "config" / "portal-dependency-lock.json"
SCHEMA_PATH = ROOT / "config" / "portal-dependency-lock-v1.schema.json"

GIT_SHA_RE = re.compile(r"^[a-f0-9]{40}$")
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")

EXPECTED_ODS_BASE = "21f4b3a64dd2a2fac1163f446806091c25b6b814"
EXPECTED_PORTAL_CORE = "bd0bbdf8faa3d7f56e930ec89e94e27efa9a0f53"
EXPECTED_HERMES_COMMIT = "2237be355906fbe6065ce1815711eee52b2d646e"
EXPECTED_HERMES_TAG_OBJECT = "9949d0d324a3a06ac238e01dd1c2103dbca09900"
EXPECTED_HERMES_PUBLISHED_AT = "2026-09-07T22:17:01Z"
EXPECTED_HERMES_INDEX = (
    "sha256:63bfb6d732f49a55d453e801057273785cc61e0f6ee43db3fa2f2a79846301b7"
)
EXPECTED_HERMES_BUILDER = (
    "https://github.com/NousResearch/hermes-agent/actions/runs/34166135981/attempts/1"
)
EXPECTED_HERMES_MANIFESTS = {
    "linux/amd64": (
        "sha256:b3190406963c6b51ac955397ecef45346efaae9563ee305108f8eef0a77e267b",
        "sha256:5fc02b8e0b89c3436a203c3261dd7d9e52e339461edb4d2afaaa87dd3f8d66db",
        "sha256:ae6c21ad6159175419b5c83d866c96f94e79f0726c0cf32df0c5918664ead91e",
    ),
    "linux/arm64": (
        "sha256:71b06481e12b7f1ec6a7c90ac629faab7a12ca4a3020566697c3c11b334b9e83",
        "sha256:75548bc637e49c3dfedb68d29c1b838e255472947241e2d3cda8e4c04022e20e",
        "sha256:4651a8d0330e2a1753d42797dbb5b586c4f5a64b1750ff4bc04e1b6a7d2b6e8a",
    ),
}
EXPECTED_SOURCE_INPUTS = {
    3385: (
        EXPECTED_ODS_BASE,
        "53bf63fa256b6bed27764cace170d45aa43c75d1",
    ),
    3818: (
        "58a2d544b8752cee2b42d54001100d9ef2e89ae0",
        "90e74b01c597bec13b12955ad04acb1f0bf68b16",
    ),
}
EXPECTED_EVIDENCE = {
    "PORTAL-CORE-LOCK.json": (
        "ef08d82292ec7a4a707d45d6dafc7259579b472e6f16c75f2b9534b3cafca7cb"
    ),
    "schemas/portal-core-lock-v1.schema.json": (
        "1c54a2089b971ca32e262dcaff33056a13eedfdc47ff715da02a75599f2f8622"
    ),
    "configs/hermes-plugin-coverage-v1.json": (
        "46aa1db4dedea9335a30ba8cbd9bf9a2d637b81c8e95330f96625bfa6866d648"
    ),
    "configs/portal-hermes-runtime-evidence-v1.json": (
        "742d8b9ff2cb2b68d344829e5ac9b135eb29daa0b3ae0ff3b75cba6f9ea10ac7"
    ),
    "hermes-plugin/ods-portal/plugin.yaml": (
        "41277c909689b2b70620de56938be12d8609130851cc0d8299b7ed4eb752070e"
    ),
}
EXPECTED_BLOCKERS = {
    "signed-candidate-required",
    "portal-sbom-required",
    "portal-core-incomplete",
    "subsystem-provenance-incomplete",
    "installed-fleet-qualification-required",
}


class LockError(ValueError):
    """The lock is malformed, widened, or claims evidence it does not have."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise LockError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_lock(path: Path = LOCK_PATH) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise LockError(f"cannot read Portal dependency lock: {exc}") from exc
    if not isinstance(value, dict):
        raise LockError("Portal dependency lock must be a JSON object")
    return value


def _exact(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LockError(f"{label} must be an object")
    actual = set(value)
    if actual != keys:
        raise LockError(
            f"{label} keys differ: missing={sorted(keys - actual)} extra={sorted(actual - keys)}"
        )
    return value


def _list(value: Any, length: int, label: str) -> list[Any]:
    if not isinstance(value, list) or len(value) != length:
        raise LockError(f"{label} must contain exactly {length} entries")
    return value


def _strict_equal(actual: Any, expected: Any) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(
            _strict_equal(actual[key], value) for key, value in expected.items()
        )
    if isinstance(expected, (list, tuple)):
        return len(actual) == len(expected) and all(
            _strict_equal(actual_item, expected_item)
            for actual_item, expected_item in zip(actual, expected, strict=True)
        )
    return actual == expected


def _equal(actual: Any, expected: Any, label: str) -> None:
    if not _strict_equal(actual, expected):
        raise LockError(f"{label} must be {expected!r}, got {actual!r}")


def _matches(value: Any, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise LockError(f"{label} has an invalid format")
    return value


def _timestamp(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise LockError(f"{label} must be a UTC RFC3339 timestamp")
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise LockError(f"{label} must be a UTC RFC3339 timestamp") from exc


def validate(lock: dict[str, Any]) -> None:
    _exact(
        lock,
        {
            "$schema",
            "schemaVersion",
            "observedAt",
            "product",
            "ods",
            "sourceInputs",
            "portalCore",
            "upstreamHermes",
            "qualification",
        },
        "lock",
    )
    _equal(lock["$schema"], "./portal-dependency-lock-v1.schema.json", "lock schema")
    _equal(lock["schemaVersion"], 1, "lock schemaVersion")
    _timestamp(lock["observedAt"], "lock observedAt")
    if not SCHEMA_PATH.is_file():
        raise LockError("Portal dependency lock schema is missing")

    product = _exact(
        lock["product"],
        {
            "subsystem",
            "genericPlaceholder",
            "userNamingSource",
            "securityIdentitySource",
            "canonicalIdsImmutable",
        },
        "product",
    )
    expected_product = {
        "subsystem": "Portal",
        "genericPlaceholder": True,
        "userNamingSource": "hermes-profile-display-name",
        "securityIdentitySource": "hermes-profile-id",
        "canonicalIdsImmutable": True,
    }
    _equal(product, expected_product, "product naming and identity contract")

    ods = _exact(lock["ods"], {"repository", "baseBranch", "baseCommit"}, "ods")
    _equal(ods["repository"], "https://github.com/Osmantic/ODS", "ODS repository")
    _equal(ods["baseBranch"], "main", "ODS base branch")
    _equal(ods["baseCommit"], EXPECTED_ODS_BASE, "ODS base commit")

    sources: dict[int, tuple[str, str]] = {}
    for item in _list(lock["sourceInputs"], 2, "sourceInputs"):
        source = _exact(
            item,
            {
                "repository",
                "pullRequest",
                "baseCommit",
                "headCommit",
                "disposition",
                "integrationState",
            },
            "source input",
        )
        _equal(
            source["repository"],
            "https://github.com/Osmantic/ODS",
            "source input repository",
        )
        pr = source["pullRequest"]
        if not isinstance(pr, int) or isinstance(pr, bool) or pr in sources:
            raise LockError("source input pull requests must be unique integers")
        _equal(source["disposition"], "source-only-untouched", f"PR #{pr} disposition")
        if source["integrationState"] not in {"inventory-in-progress", "mapped"}:
            raise LockError(f"PR #{pr} has an invalid integration state")
        base = _matches(source["baseCommit"], GIT_SHA_RE, f"PR #{pr} base commit")
        head = _matches(source["headCommit"], GIT_SHA_RE, f"PR #{pr} head commit")
        sources[pr] = (base, head)
    _equal(sources, EXPECTED_SOURCE_INPUTS, "superseded source PR identities")

    portal = _exact(
        lock["portalCore"],
        {
            "repository",
            "pullRequest",
            "sourceCommit",
            "sourceState",
            "consumptionMode",
            "runtimeActivated",
            "coreComplete",
            "evidenceFiles",
            "candidateArtifact",
            "signature",
            "sbom",
        },
        "portalCore",
    )
    _equal(portal["repository"], "https://github.com/Osmantic/Pixel", "Portal-core repository")
    _equal(portal["pullRequest"], 239, "Portal-core pull request")
    _equal(portal["sourceCommit"], EXPECTED_PORTAL_CORE, "Portal-core source commit")
    _equal(portal["sourceState"], "reviewed-private-draft", "Portal-core source state")
    _equal(portal["consumptionMode"], "source-lock-only", "Portal-core consumption mode")
    _equal(portal["runtimeActivated"], False, "Portal-core runtime activation")
    _equal(portal["coreComplete"], False, "Portal-core completeness")

    evidence: dict[str, str] = {}
    for item in _list(portal["evidenceFiles"], 5, "Portal-core evidenceFiles"):
        entry = _exact(item, {"path", "sha256"}, "Portal-core evidence file")
        path = entry["path"]
        if not isinstance(path, str) or path in evidence:
            raise LockError("Portal-core evidence paths must be unique strings")
        evidence[path] = _matches(entry["sha256"], SHA256_RE, f"evidence hash for {path}")
    _equal(evidence, EXPECTED_EVIDENCE, "Portal-core evidence file identities")

    artifact = _exact(
        portal["candidateArtifact"],
        {"status", "reference", "digest", "immutable"},
        "Portal-core candidateArtifact",
    )
    _equal(
        artifact,
        {"status": "not-published", "reference": None, "digest": None, "immutable": False},
        "Portal-core candidate artifact",
    )
    signature = _exact(portal["signature"], {"status", "claim"}, "Portal-core signature")
    _equal(signature["status"], "not-verified", "Portal-core signature status")
    if not isinstance(signature["claim"], str) or not signature["claim"]:
        raise LockError("Portal-core signature claim must be non-empty")
    sbom = _exact(portal["sbom"], {"status", "digest"}, "Portal-core sbom")
    _equal(sbom, {"status": "missing", "digest": None}, "Portal-core SBOM")

    hermes = _exact(
        lock["upstreamHermes"],
        {"repository", "release", "sourceVerification", "oci"},
        "upstreamHermes",
    )
    _equal(
        hermes["repository"],
        "https://github.com/NousResearch/hermes-agent",
        "Hermes repository",
    )
    release = _exact(
        hermes["release"],
        {
            "releaseId",
            "tag",
            "packageVersion",
            "publishedAt",
            "draft",
            "prerelease",
            "tagObjectSha",
            "commitSha",
        },
        "Hermes release",
    )
    _equal(release["releaseId"], 384339710, "Hermes release id")
    _equal(release["tag"], "v2026.9.7", "Hermes release tag")
    _equal(release["packageVersion"], "0.21.1", "Hermes package version")
    _timestamp(release["publishedAt"], "Hermes publishedAt")
    _equal(release["publishedAt"], EXPECTED_HERMES_PUBLISHED_AT, "Hermes publishedAt")
    _equal(release["draft"], False, "Hermes release draft flag")
    _equal(release["prerelease"], False, "Hermes prerelease flag")
    _equal(release["tagObjectSha"], EXPECTED_HERMES_TAG_OBJECT, "Hermes tag object")
    _equal(release["commitSha"], EXPECTED_HERMES_COMMIT, "Hermes source commit")

    source_verification = _exact(
        hermes["sourceVerification"],
        {"tagSignature", "commitSignature", "releaseAssetCount"},
        "Hermes sourceVerification",
    )
    _equal(
        source_verification,
        {"tagSignature": "unsigned", "commitSignature": "unsigned", "releaseAssetCount": 0},
        "Hermes source verification",
    )

    oci = _exact(
        hermes["oci"],
        {
            "tagReference",
            "digestReference",
            "indexDigest",
            "manifests",
            "provenance",
            "sbom",
            "signature",
        },
        "Hermes oci",
    )
    _equal(
        oci["tagReference"],
        "docker.io/nousresearch/hermes-agent:v2026.9.7",
        "Hermes OCI tag reference",
    )
    _equal(oci["indexDigest"], EXPECTED_HERMES_INDEX, "Hermes OCI index")
    _equal(
        oci["digestReference"],
        f"docker.io/nousresearch/hermes-agent@{EXPECTED_HERMES_INDEX}",
        "Hermes OCI digest reference",
    )
    manifests: dict[str, tuple[str, str, str]] = {}
    for item in _list(oci["manifests"], 2, "Hermes OCI manifests"):
        manifest = _exact(
            item,
            {"platform", "manifestDigest", "provenanceManifestDigest", "provenanceLayerDigest"},
            "Hermes OCI manifest",
        )
        platform = manifest["platform"]
        if platform not in {"linux/amd64", "linux/arm64"} or platform in manifests:
            raise LockError("Hermes OCI manifests must cover unique linux/amd64 and linux/arm64")
        for key in ("manifestDigest", "provenanceManifestDigest", "provenanceLayerDigest"):
            _matches(manifest[key], DIGEST_RE, f"Hermes {platform} {key}")
        manifests[platform] = (
            manifest["manifestDigest"],
            manifest["provenanceManifestDigest"],
            manifest["provenanceLayerDigest"],
        )
    _equal(manifests, EXPECTED_HERMES_MANIFESTS, "Hermes OCI manifest identities")

    provenance = _exact(
        oci["provenance"],
        {"status", "predicateType", "sourceRepository", "sourceCommit", "builder"},
        "Hermes OCI provenance",
    )
    _equal(provenance["status"], "present-source-commit-bound", "Hermes provenance status")
    _equal(provenance["predicateType"], "https://slsa.dev/provenance/v1", "Hermes predicate")
    _equal(provenance["sourceRepository"], hermes["repository"], "Hermes provenance repository")
    _equal(provenance["sourceCommit"], release["commitSha"], "Hermes provenance source")
    _equal(provenance["builder"], EXPECTED_HERMES_BUILDER, "Hermes provenance builder")
    _equal(
        _exact(oci["sbom"], {"status"}, "Hermes OCI sbom")["status"],
        "not-found-in-index-attestations",
        "Hermes OCI SBOM status",
    )
    oci_signature = _exact(oci["signature"], {"status", "claim"}, "Hermes OCI signature")
    _equal(oci_signature["status"], "not-verified", "Hermes OCI signature status")
    if not isinstance(oci_signature["claim"], str) or not oci_signature["claim"]:
        raise LockError("Hermes OCI signature claim must be non-empty")

    qualification = _exact(
        lock["qualification"],
        {"state", "releaseGreen", "acceptance", "blockers"},
        "qualification",
    )
    _equal(qualification["state"], "blocked", "qualification state")
    _equal(qualification["releaseGreen"], False, "qualification releaseGreen")
    acceptance = _exact(
        qualification["acceptance"],
        {
            "subsystemProvenance",
            "capabilitySuperset",
            "migrationRollback",
            "installedFleet",
            "performance",
        },
        "qualification acceptance",
    )
    _equal(
        acceptance,
        {
            "subsystemProvenance": "in-progress",
            "capabilitySuperset": "not-run",
            "migrationRollback": "not-run",
            "installedFleet": "not-run",
            "performance": "not-run",
        },
        "qualification acceptance state",
    )
    blockers: set[str] = set()
    for item in _list(qualification["blockers"], 5, "qualification blockers"):
        blocker = _exact(item, {"code", "display", "resolution"}, "qualification blocker")
        code = blocker["code"]
        if not isinstance(code, str) or code in blockers:
            raise LockError("qualification blocker codes must be unique strings")
        blockers.add(code)
        if not isinstance(blocker["display"], str) or not blocker["display"].startswith("BLOCKED—"):
            raise LockError(f"qualification blocker {code} must have a visible BLOCKED display")
        if not isinstance(blocker["resolution"], str) or not blocker["resolution"]:
            raise LockError(f"qualification blocker {code} must name its resolution")
    _equal(blockers, EXPECTED_BLOCKERS, "qualification blocker set")


def check(path: Path = LOCK_PATH) -> list[str]:
    try:
        validate(load_lock(path))
    except LockError as exc:
        return [str(exc)]
    return []


def main() -> int:
    errors = check()
    if errors:
        for error in errors:
            print(f"[FAIL] {error}", file=sys.stderr)
        return 1
    print("[PASS] Portal dependency lock is exact and remains honestly blocked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
