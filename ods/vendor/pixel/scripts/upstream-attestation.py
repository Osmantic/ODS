#!/usr/bin/env python3
"""Build or verify a signed, identity-bound OpenClaw qualification attestation."""

from __future__ import annotations

import argparse
import importlib.util
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
REMOTE_REF_POLICY = ROOT / "security-evals" / "assurance" / "remote-ref-policy.json"
_REVIEWED_SPEC = importlib.util.spec_from_file_location(
    "pixel_reviewed_policy", ROOT / "security-evals" / "assurance" / "reviewed_policy.py",
)
if _REVIEWED_SPEC is None or _REVIEWED_SPEC.loader is None:
    raise RuntimeError("could not load the shared reviewed-blob policy module")
REVIEWED_POLICY = importlib.util.module_from_spec(_REVIEWED_SPEC)
_REVIEWED_SPEC.loader.exec_module(REVIEWED_POLICY)
NAMESPACE = "pixel-upstream-qualification"
MAX_EVIDENCE_BYTES = 32 * 1024 * 1024
EXACT_GIT_OBJECT = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


class AttestationError(RuntimeError):
    pass


def canonical(value: object, *, newline: bool = True) -> bytes:
    suffix = "\n" if newline else ""
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + suffix).encode()


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def read_regular_bytes(path: Path) -> tuple[Path, bytes]:
    path = Path(os.path.abspath(path))
    details = path.lstat()
    if not stat.S_ISREG(details.st_mode) or path.is_symlink():
        raise AttestationError(f"evidence input is not a regular non-symlink file: {path}")
    if details.st_size > MAX_EVIDENCE_BYTES:
        raise AttestationError(f"evidence input exceeds {MAX_EVIDENCE_BYTES} bytes: {path.name}")
    return path, path.read_bytes()


def read_regular_json(path: Path) -> tuple[dict[str, object], bytes]:
    path, contents = read_regular_bytes(path)
    value = json.loads(contents)
    if not isinstance(value, dict):
        raise AttestationError(f"evidence input is not a JSON object: {path.name}")
    return value, contents


def manifest_hashes(manifest: dict[str, object]) -> tuple[str, str]:
    return sha256(canonical(manifest, newline=False)), sha256(canonical(manifest, newline=True))


def require_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise AttestationError(f"{label} is not an exact SHA-256")
    return value


def validate_reviewed_policy_inventory(
    remote_audit: dict[str, object], expected_policy: dict[str, object]
) -> None:
    """Verify the remote-ref audit binds the exact reviewed-blob policy and inventory.

    The remote-ref policy must carry the recomputed reviewed-blob semantic digest, and the
    audit must report the exact acknowledged active-lineage fixture inventory. A forged,
    missing, extra, drifted, or unacknowledged record fails.
    """
    bound = expected_policy.get("reviewedBlobsSha256")
    if not isinstance(bound, str) or not re.fullmatch(r"[0-9a-f]{64}", bound):
        raise AttestationError("remote-ref policy reviewed-blob binding is invalid")
    try:
        bound_digest = REVIEWED_POLICY.policy_sha256()
        reviewed = REVIEWED_POLICY.load()
    except REVIEWED_POLICY.ReviewedPolicyError as exc:
        raise AttestationError(f"reviewed policy is invalid: {exc}") from exc
    if bound != bound_digest:
        raise AttestationError("remote-ref policy reviewed-blob binding is invalid")
    policy = remote_audit.get("policy")
    if not isinstance(policy, dict) or policy.get("reviewedBlobsSha256") != bound:
        raise AttestationError("remote-ref audit reviewed-blob policy binding is invalid")
    expected = {entry["blobSha1"]: entry for entry in REVIEWED_POLICY.acknowledged_entries(reviewed)}
    fixtures = remote_audit.get("acknowledgedFixtures")
    if not isinstance(fixtures, list):
        raise AttestationError("acknowledged fixture inventory is invalid")
    observed: set[str] = set()
    for record in fixtures:
        if (
            not isinstance(record, dict)
            or set(record) != {"object", "path", "auditSourceLabels", "payloadSha256"}
        ):
            raise AttestationError("acknowledged fixture inventory is invalid")
        object_id = record.get("object")
        if (
            not isinstance(object_id, str)
            or not re.fullmatch(r"[0-9a-f]{40}", object_id)
            or object_id in observed
        ):
            raise AttestationError("acknowledged fixture inventory is invalid")
        entry = expected.get(object_id)
        if (
            entry is None
            or record.get("path") != entry["path"]
            or record.get("auditSourceLabels") != entry["auditSourceLabels"]
            or record.get("payloadSha256") != entry["payloadSha256"]
        ):
            raise AttestationError("acknowledged fixture inventory is forged or drifted")
        observed.add(object_id)
    if observed != set(expected):
        raise AttestationError("acknowledged fixture inventory is incomplete")


def validate_remote_ref_audit(remote_audit: dict[str, object], current_commit: str) -> None:
    if remote_audit.get("schemaVersion") != 2 or remote_audit.get("secretValuesEmitted") is not False:
        raise AttestationError("remote-ref audit contract failed")
    if remote_audit.get("status") != "pass":
        raise AttestationError("active Pixel remote-ref release gate did not pass")
    policy = remote_audit.get("policy")
    try:
        remote_ref_text = REMOTE_REF_POLICY.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise AttestationError("cannot read the remote-ref policy") from exc
    try:
        expected_policy = REVIEWED_POLICY.strict_json_loads(remote_ref_text)
    except REVIEWED_POLICY.ReviewedPolicyError as exc:
        raise AttestationError(f"remote-ref policy is malformed: {exc}") from exc
    if (
        not isinstance(policy, dict)
        or policy.get("repository") != expected_policy.get("repository")
        or policy.get("lineageRootCommit") != expected_policy.get("lineageRootCommit")
        or policy.get("sha256") != sha256(canonical(expected_policy, newline=False))
    ):
        raise AttestationError("remote-ref audit policy identity is invalid")
    validate_reviewed_policy_inventory(remote_audit, expected_policy)
    if str(remote_audit.get("remoteIdentity", "")).casefold() != str(expected_policy["repository"]).casefold():
        raise AttestationError("remote-ref audit remote identity is invalid")
    gate = remote_audit.get("releaseGate")
    if not isinstance(gate, dict) or gate.get("status") != "pass":
        raise AttestationError("active Pixel remote-ref release gate did not pass")
    if gate.get("sourceHead") != current_commit or gate.get("sourceHeadAdvertised") is not True:
        raise AttestationError("remote-ref audit does not bind the advertised candidate commit")
    active_refs = gate.get("refs")
    if not isinstance(active_refs, list) or not active_refs:
        raise AttestationError("remote-ref audit has no active Pixel refs")
    active_names: set[str] = set()
    active_commits: set[str] = set()
    for record in active_refs:
        if not isinstance(record, dict):
            raise AttestationError("active Pixel remote-ref inventory is invalid")
        ref, object_id, commit = record.get("ref"), record.get("object"), record.get("commit")
        if (
            not isinstance(ref, str) or ref in active_names
            or not isinstance(object_id, str) or not EXACT_GIT_OBJECT.fullmatch(object_id)
            or not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit)
        ):
            raise AttestationError("active Pixel remote-ref inventory is invalid")
        active_names.add(ref)
        active_commits.add(commit)
    if current_commit not in active_commits:
        raise AttestationError("active Pixel remote-ref inventory omits the candidate commit")
    history = gate.get("history")
    if (
        not isinstance(history, dict)
        or history.get("findings") != []
        or history.get("skippedLargeObjects") != []
        or gate.get("policyViolations") != []
        or gate.get("blockers") != []
    ):
        raise AttestationError("active Pixel remote-ref audit has findings, skipped content, or policy violations")
    legacy = remote_audit.get("legacyHistory")
    if not isinstance(legacy, dict) or legacy.get("status") != "reported":
        raise AttestationError("legacy remote history was not reported")
    legacy_history = legacy.get("history")
    legacy_refs = legacy.get("refs")
    retired_refs = legacy.get("retiredPolicyRefs")
    if (
        not isinstance(legacy_refs, list)
        or not isinstance(retired_refs, list)
        or not isinstance(legacy_history, dict)
        or not isinstance(legacy_history.get("findings"), list)
        or not isinstance(legacy_history.get("skippedLargeObjects"), list)
    ):
        raise AttestationError("legacy remote history report is incomplete")
    expected_legacy = {entry["ref"]: entry for entry in expected_policy["legacyRefs"]}
    observed_legacy: set[str] = set()
    for record in legacy_refs:
        if not isinstance(record, dict):
            raise AttestationError("legacy remote-ref inventory is invalid")
        ref = record.get("ref")
        expected = expected_legacy.get(ref)
        if (
            not isinstance(ref, str) or ref in observed_legacy or expected is None
            or record.get("object") != expected["object"]
            or record.get("reason") != expected["reason"]
            or not isinstance(record.get("commit"), str)
            or not re.fullmatch(r"[0-9a-f]{40}", record["commit"])
        ):
            raise AttestationError("legacy remote-ref inventory is invalid")
        observed_legacy.add(ref)
    if (
        any(not isinstance(ref, str) for ref in retired_refs)
        or len(retired_refs) != len(set(retired_refs))
        or observed_legacy & set(retired_refs)
        or observed_legacy | set(retired_refs) != set(expected_legacy)
        or active_names & set(expected_legacy)
    ):
        raise AttestationError("legacy remote-ref inventory is incomplete or overlaps active Pixel refs")
    if remote_audit.get("remoteRefCount") != len(active_refs) + len(legacy_refs):
        raise AttestationError("remote-ref audit count is inconsistent")


def package_identity(manifest: dict[str, object]) -> list[dict[str, str]]:
    plugins = manifest["openclawPlugins"]
    packages = manifest["openclawPluginPackages"]
    assert isinstance(plugins, dict) and isinstance(packages, dict)
    records = [
        {"name": "openclaw", "version": str(manifest["openclaw"]), **manifest["openclawPackage"]},
        {"name": "@openclaw/discord", "version": str(plugins["@openclaw/discord"]), **packages["discord"]},
        {"name": "@openclaw/searxng-plugin", "version": str(plugins["@openclaw/searxng-plugin"]), **packages["searxng"]},
        {"name": "@openclaw/llama-cpp-provider", "version": str(plugins["@openclaw/llama-cpp-provider"]), **packages["llamaCpp"]},
    ]
    for record in records:
        require_sha256(record.get("sha256"), f"{record['name']} package hash")
        if not str(record.get("integrity", "")).startswith("sha512-"):
            raise AttestationError(f"{record['name']} package has no npm integrity")
    return records


def validate_inputs(
    manifest: dict[str, object],
    source: dict[str, object],
    contract: dict[str, object],
    runtime: dict[str, object],
    assurance: dict[str, object],
    canary: dict[str, object],
    current_commit: str,
    current_tree: str,
) -> dict[str, object]:
    if not re.fullmatch(r"[0-9a-f]{40}", current_commit):
        raise AttestationError("source commit is not exact")
    if not re.fullmatch(r"[0-9a-f]{40}", current_tree):
        raise AttestationError("source tree is not exact")
    intake = manifest.get("upstreamIntake")
    if not isinstance(intake, dict):
        raise AttestationError("release manifest has no prepared upstream candidate")
    compact_hash, newline_hash = manifest_hashes(manifest)
    source_record = source.get("source")
    if not isinstance(source_record, dict) or source_record.get("commit") != current_commit or source_record.get("clean") is not True:
        raise AttestationError("source provenance does not bind the clean candidate commit")
    if source_record.get("tree") != current_tree:
        raise AttestationError("source provenance does not bind the candidate tree")
    source_release = source.get("release")
    if not isinstance(source_release, dict) or source_release.get("pixel") != manifest.get("pixel") or source_release.get("openclaw") != manifest.get("openclaw"):
        raise AttestationError("source provenance refers to another release")
    source_catalog = source.get("attackCatalog")
    if not isinstance(source_catalog, dict):
        raise AttestationError("source provenance has no attack catalog")
    catalog_sha256 = require_sha256(source_catalog.get("sha256"), "source attack catalog hash")
    if contract.get("status") != "compatible" or contract.get("blockers") not in ([], None):
        raise AttestationError("contract diff has an undispositioned blocker")
    if contract.get("sourceCommit") != intake.get("sourceCommit"):
        raise AttestationError("contract diff refers to a different intake source")
    if contract.get("candidateManifestSha256") != compact_hash:
        raise AttestationError("contract diff refers to a different candidate manifest")
    if runtime.get("status") != "pass" or runtime.get("sourceCommit") != current_commit:
        raise AttestationError("runtime matrix does not pass for the candidate commit")
    if runtime.get("intakeSourceCommit") != intake.get("sourceCommit") or runtime.get("candidate") != manifest.get("openclaw"):
        raise AttestationError("runtime matrix refers to another candidate")
    lanes = runtime.get("lanes")
    if not isinstance(lanes, list):
        raise AttestationError("runtime matrix has no lanes")
    observed_lanes = {
        (item.get("os", {}).get("id"), item.get("os", {}).get("version"), item.get("mode"))
        for item in lanes if isinstance(item, dict)
    }
    required_lanes = {("ubuntu", "24.04", "quick"), ("debian", "12", "quick"), ("ubuntu", "24.04", "systemd")}
    if len(lanes) != 3 or observed_lanes != required_lanes or any(item.get("status") != "pass" for item in lanes):
        raise AttestationError("runtime matrix does not contain the exact required passing lanes")
    if {item.get("candidateManifestSha256") for item in lanes} != {newline_hash}:
        raise AttestationError("runtime lanes disagree with the candidate manifest")
    binding_values = {item.get("evidenceBindingSha256") for item in lanes}
    if len(binding_values) != 1:
        raise AttestationError("runtime lanes do not share one evidence identity")
    runtime_binding = require_sha256(next(iter(binding_values)), "runtime evidence binding")
    if assurance.get("status") != "pass" or assurance.get("sourceCommit") != current_commit:
        raise AttestationError("full assurance does not pass for the candidate commit")
    if assurance.get("sourceTree") != current_tree or assurance.get("releaseManifestSha256") != newline_hash:
        raise AttestationError("full assurance refers to another source tree or manifest")
    if assurance.get("attackCatalogSha256") != catalog_sha256:
        raise AttestationError("full assurance refers to another attack catalog")
    if not isinstance(assurance.get("consecutivePasses"), int) or assurance["consecutivePasses"] < 2:
        raise AttestationError("two consecutive full assurance passes are required")
    passes = assurance.get("passes")
    if not isinstance(passes, list) or len(passes) != assurance["consecutivePasses"]:
        raise AttestationError("full assurance pass records are incomplete")
    for number, record in enumerate(passes, 1):
        if not isinstance(record, dict) or record.get("pass") != number or record.get("status") != "pass":
            raise AttestationError("full assurance pass records are invalid")
        if not isinstance(record.get("pressureIterations"), int) or record["pressureIterations"] < 1:
            raise AttestationError("full assurance pressure record is invalid")
        for field in ("testLogSha256", "pressureLogSha256", "pressureReportSha256"):
            require_sha256(record.get(field), f"assurance {field}")
    artifacts = assurance.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != {
        "source-manifest.json", "source-audit.json", "remote-ref-audit.json", "evidence-secret-scan.log"
    }:
        raise AttestationError("full assurance artifact index is incomplete")
    for name, record in artifacts.items():
        if not isinstance(record, dict) or not isinstance(record.get("bytes"), int) or record["bytes"] < 1:
            raise AttestationError(f"full assurance artifact is invalid: {name}")
        require_sha256(record.get("sha256"), f"full assurance artifact hash: {name}")
    if canary.get("status") != "pass" or canary.get("sourceCommit") != current_commit:
        raise AttestationError("canary does not pass for the candidate commit")
    if canary.get("candidateManifestSha256") != newline_hash or canary.get("isolated") is not True:
        raise AttestationError("canary identity or isolation is invalid")
    if canary.get("rollbackRehearsed") is not True or canary.get("postRollbackHealthy") is not True:
        raise AttestationError("canary rollback was not proven healthy")
    observation = canary.get("observation")
    if not isinstance(observation, dict) or not isinstance(observation.get("seconds"), int) or observation["seconds"] < 1800:
        raise AttestationError("canary observation window is shorter than 30 minutes")
    if observation.get("minutes") != observation["seconds"] // 60 or not isinstance(observation.get("healthChecks"), int) or observation["healthChecks"] < 2:
        raise AttestationError("canary observation record is invalid")
    synthetic = canary.get("syntheticChecks")
    if not isinstance(synthetic, dict) or not synthetic or any(value != "pass" for value in synthetic.values()):
        raise AttestationError("canary synthetic capability checks did not all pass")
    return {
        "schemaVersion": 1,
        "operation": "pixel-upstream-qualification-attestation",
        "decision": "promote",
        "sourceCommit": current_commit,
        "sourceTree": current_tree,
        "intakeSourceCommit": intake.get("sourceCommit"),
        "pixel": manifest.get("pixel"),
        "candidateOpenClaw": manifest.get("openclaw"),
        "candidateManifestSha256": newline_hash,
        "candidateManifestCompactSha256": compact_hash,
        "packages": package_identity(manifest),
        "runtimeEvidenceBindingSha256": runtime_binding,
        "exceptions": [],
    }


def git_output(*arguments: str) -> str:
    result = subprocess.run(["git", *arguments], cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        raise AttestationError(result.stderr.strip() or "git failed")
    return result.stdout.strip()


def safe_output(path: Path) -> Path:
    if not path.is_absolute():
        raise AttestationError("attestation output must be absolute")
    path = path.resolve(strict=False)
    try:
        path.relative_to(ROOT)
    except ValueError:
        pass
    else:
        raise AttestationError("attestation output must be outside the source repository")
    if not path.parent.is_dir() or path.exists() or Path(f"{path}.sig").exists():
        raise AttestationError("attestation output parent must exist and outputs must be new")
    return path


def require_regular_input(path: Path, label: str, maximum_bytes: int = 1024 * 1024) -> Path:
    path = Path(os.path.abspath(path))
    details = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(details.st_mode) or details.st_size > maximum_bytes:
        raise AttestationError(f"{label} is not a safe regular file")
    return path


def write_new(path: Path, payload: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def build(args: argparse.Namespace) -> dict[str, object]:
    if git_output("status", "--porcelain=v1", "--untracked-files=all"):
        raise AttestationError("refusing to attest a dirty worktree")
    contract_check = subprocess.run(
        ["node", "scripts/check-release-contract.mjs"], cwd=ROOT, capture_output=True, text=True
    )
    if contract_check.returncode:
        raise AttestationError(contract_check.stderr.strip() or contract_check.stdout.strip() or "release contract failed")
    manifest, _ = read_regular_json(ROOT / "RELEASE-MANIFEST.json")
    inputs: dict[str, tuple[dict[str, object], bytes, Path]] = {}
    for label, supplied in (
        ("sourceManifest", args.source_manifest),
        ("contractDiff", args.contract_diff),
        ("runtimeMatrix", args.runtime_matrix),
        ("assuranceSummary", args.assurance_summary),
        ("canarySummary", args.canary_summary),
    ):
        value, contents = read_regular_json(supplied)
        inputs[label] = (value, contents, supplied.resolve())
    names = [path.name for _, _, path in inputs.values()]
    if len(names) != len(set(names)):
        raise AttestationError("evidence inputs must have unique file names")
    attestation = validate_inputs(
        manifest,
        inputs["sourceManifest"][0],
        inputs["contractDiff"][0],
        inputs["runtimeMatrix"][0],
        inputs["assuranceSummary"][0],
        inputs["canarySummary"][0],
        git_output("rev-parse", "HEAD"),
        git_output("rev-parse", "HEAD^{tree}"),
    )
    evidence_index = {
        label: {"file": path.name, "bytes": len(contents), "sha256": sha256(contents)}
        for label, (_, contents, path) in inputs.items()
    }
    assurance_artifacts = inputs["assuranceSummary"][0]["artifacts"]
    assert isinstance(assurance_artifacts, dict)
    source_artifact = assurance_artifacts["source-manifest.json"]
    assert isinstance(source_artifact, dict)
    source_payload = inputs["sourceManifest"][1]
    if len(source_payload) != source_artifact["bytes"] or sha256(source_payload) != source_artifact["sha256"]:
        raise AttestationError("source manifest differs from the full assurance artifact")
    assurance_root = inputs["assuranceSummary"][2].parent
    artifact_values: dict[str, dict[str, object]] = {}
    for name in ("source-audit.json", "remote-ref-audit.json", "evidence-secret-scan.log"):
        path, contents = read_regular_bytes(assurance_root / name)
        record = assurance_artifacts[name]
        assert isinstance(record, dict)
        if len(contents) != record["bytes"] or sha256(contents) != record["sha256"]:
            raise AttestationError(f"full assurance artifact differs from its summary: {name}")
        evidence_index[f"assurance:{name}"] = {
            "file": path.name,
            "bytes": len(contents),
            "sha256": sha256(contents),
        }
        if name.endswith(".json"):
            value = json.loads(contents)
            if not isinstance(value, dict):
                raise AttestationError(f"full assurance artifact is not a JSON object: {name}")
            artifact_values[name] = value
        elif contents.strip() != b"Secret policy check passed.":
            raise AttestationError("retained-evidence secret scan did not pass")
    source_audit = artifact_values["source-audit.json"]
    if source_audit.get("secretValuesEmitted") is not False:
        raise AttestationError("source audit secret-emission contract failed")
    for section in ("current", "history", "dependencies"):
        value = source_audit.get(section)
        if not isinstance(value, dict) or value.get("findings") != []:
            raise AttestationError(f"source audit has findings: {section}")
    if source_audit["history"].get("scanned") is not True:
        raise AttestationError("source history was not audited")
    if source_audit["current"].get("skippedLargeFiles") != [] or source_audit["history"].get("skippedLargeObjects") != []:
        raise AttestationError("source audit skipped content")
    validate_remote_ref_audit(artifact_values["remote-ref-audit.json"], git_output("rev-parse", "HEAD"))
    if len({record["file"] for record in evidence_index.values()}) != len(evidence_index):
        raise AttestationError("evidence inputs must have unique file names")
    attestation["evidence"] = evidence_index
    output = safe_output(args.output)
    signing_key = require_regular_input(args.signing_key, "release signing key")
    write_new(output, canonical(attestation))
    result = subprocess.run(
        ["ssh-keygen", "-Y", "sign", "-f", str(signing_key), "-n", NAMESPACE, str(output)],
        capture_output=True,
        text=True,
    )
    if result.returncode or not Path(f"{output}.sig").is_file():
        output.unlink(missing_ok=True)
        Path(f"{output}.sig").unlink(missing_ok=True)
        raise AttestationError(result.stderr.strip() or "attestation signing failed")
    Path(f"{output}.sig").chmod(0o600)
    return {"attestation": str(output), "signature": f"{output}.sig", "sha256": sha256(output.read_bytes())}


def verify(args: argparse.Namespace) -> dict[str, object]:
    attestation, contents = read_regular_json(args.attestation)
    if not re.fullmatch(r"[A-Za-z0-9._@+-]{3,128}", args.identity):
        raise AttestationError("release signer identity is invalid")
    signature = require_regular_input(args.signature, "detached signature")
    allowed = require_regular_input(args.allowed_signers, "allowed signers file")
    process = subprocess.run(
        ["ssh-keygen", "-Y", "verify", "-f", str(allowed), "-I", args.identity, "-n", NAMESPACE, "-s", str(signature)],
        input=contents,
        capture_output=True,
    )
    if process.returncode:
        raise AttestationError("detached attestation signature is invalid or untrusted")
    if attestation.get("operation") != "pixel-upstream-qualification-attestation" or attestation.get("decision") != "promote":
        raise AttestationError("attestation does not contain a promotion decision")
    evidence = attestation.get("evidence")
    expected_evidence = {
        "sourceManifest", "contractDiff", "runtimeMatrix", "assuranceSummary", "canarySummary",
        "assurance:source-audit.json", "assurance:remote-ref-audit.json", "assurance:evidence-secret-scan.log",
    }
    if not isinstance(evidence, dict) or set(evidence) != expected_evidence:
        raise AttestationError("attestation has no evidence index")
    evidence_root = args.evidence_dir.resolve()
    for label, record in evidence.items():
        if not isinstance(record, dict) or Path(str(record.get("file", ""))).name != record.get("file"):
            raise AttestationError(f"unsafe evidence record: {label}")
        path = evidence_root / record["file"]
        _, payload = read_regular_bytes(path)
        if len(payload) != record.get("bytes") or sha256(payload) != record.get("sha256"):
            raise AttestationError(f"evidence differs from signed record: {label}")
    return {"status": "verified", "identity": args.identity, "sha256": sha256(contents), "sourceCommit": attestation.get("sourceCommit")}


def write_json(path: Path, value: dict[str, object]) -> None:
    temporary = path.with_name(f"{path.name}.promote-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def promote(args: argparse.Namespace) -> dict[str, object]:
    if args.confirm is not True:
        raise AttestationError("promotion requires --confirm")
    if git_output("status", "--porcelain=v1", "--untracked-files=all"):
        raise AttestationError("promotion requires a clean candidate checkout")
    parsed_reference = urlparse(args.evidence_reference)
    if parsed_reference.scheme != "https" or not parsed_reference.netloc or parsed_reference.username or parsed_reference.password:
        raise AttestationError("evidence reference must be a credential-free permanent HTTPS URL")
    verified = verify(args)
    current_commit = git_output("rev-parse", "HEAD")
    if verified.get("sourceCommit") != current_commit:
        raise AttestationError("signed attestation refers to a different source commit")
    attestation, attestation_contents = read_regular_json(args.attestation)
    manifest_path = ROOT / "RELEASE-MANIFEST.json"
    compatibility_path = ROOT / "OPENCLAW-COMPATIBILITY.json"
    manifest, original_manifest = read_regular_json(manifest_path)
    compatibility, original_compatibility = read_regular_json(compatibility_path)
    _, manifest_sha = manifest_hashes(manifest)
    if attestation.get("candidateManifestSha256") != manifest_sha:
        raise AttestationError("signed attestation refers to a different release manifest")
    combinations = compatibility.get("combinations")
    if not isinstance(combinations, list):
        raise AttestationError("compatibility matrix is invalid")
    matches = [
        item for item in combinations
        if isinstance(item, dict)
        and item.get("pixel") == manifest.get("pixel")
        and item.get("openclaw") == manifest.get("openclaw")
        and item.get("plugins") == manifest.get("openclawPlugins")
    ]
    if len(matches) != 1 or matches[0].get("status") != "candidate":
        raise AttestationError("exact manifest combination is not in Candidate state")
    supported = [item for item in combinations if isinstance(item, dict) and item.get("status") == "supported"]
    if len(supported) != 1:
        raise AttestationError("compatibility matrix must contain exactly one Supported combination")
    for item in supported:
        item["status"] = "retired"
    candidate = matches[0]
    candidate["status"] = "supported"
    candidate["qualifiedAt"] = datetime.now(timezone.utc).date().isoformat()
    candidate["evidence"] = {
        "sourceCommit": current_commit,
        "liveAudit": args.evidence_reference,
        "attestationSha256": sha256(attestation_contents),
    }
    manifest.pop("upstreamIntake", None)
    try:
        write_json(manifest_path, manifest)
        write_json(compatibility_path, compatibility)
        subprocess.run(["node", "scripts/generate-release-files.mjs", "--write"], cwd=ROOT, check=True)
        subprocess.run(["node", "scripts/check-release-contract.mjs"], cwd=ROOT, check=True)
    except BaseException:
        manifest_path.write_bytes(original_manifest)
        compatibility_path.write_bytes(original_compatibility)
        subprocess.run(["node", "scripts/generate-release-files.mjs", "--write"], cwd=ROOT, check=False)
        raise
    return {
        "status": "promoted",
        "sourceCommit": current_commit,
        "openclaw": manifest["openclaw"],
        "attestationSha256": sha256(attestation_contents),
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_parser = subparsers.add_parser("build")
    build_parser.add_argument("--source-manifest", required=True, type=Path)
    build_parser.add_argument("--contract-diff", required=True, type=Path)
    build_parser.add_argument("--runtime-matrix", required=True, type=Path)
    build_parser.add_argument("--assurance-summary", required=True, type=Path)
    build_parser.add_argument("--canary-summary", required=True, type=Path)
    build_parser.add_argument("--output", required=True, type=Path)
    build_parser.add_argument("--signing-key", required=True, type=Path)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--attestation", required=True, type=Path)
    verify_parser.add_argument("--signature", required=True, type=Path)
    verify_parser.add_argument("--allowed-signers", required=True, type=Path)
    verify_parser.add_argument("--identity", required=True)
    verify_parser.add_argument("--evidence-dir", required=True, type=Path)
    promote_parser = subparsers.add_parser("promote")
    promote_parser.add_argument("--attestation", required=True, type=Path)
    promote_parser.add_argument("--signature", required=True, type=Path)
    promote_parser.add_argument("--allowed-signers", required=True, type=Path)
    promote_parser.add_argument("--identity", required=True)
    promote_parser.add_argument("--evidence-dir", required=True, type=Path)
    promote_parser.add_argument("--evidence-reference", required=True)
    promote_parser.add_argument("--confirm", action="store_true")
    return parser.parse_args()


def main() -> int:
    try:
        args = parse_arguments()
        result = build(args) if args.command == "build" else promote(args) if args.command == "promote" else verify(args)
    except (AttestationError, AssertionError, KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        print(f"upstream attestation failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
