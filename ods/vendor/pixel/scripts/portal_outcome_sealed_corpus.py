#!/usr/bin/env python3
"""Sealed-corpus lifecycle for the formal matched-harness campaign.

A trusted owner/custodian splits a full battery into a tuning-visible root and a
separately custodied owner-private reveal root.  The two roots are distinct
absolute paths with neither nested in the other.  The tuning-visible root holds
the tuning corpus and a content-free opaque commitment; it never contains or
references any held-out task byte, id, profile, axis, source, size, or filename.
The reveal root (owner/guardian custody only) holds an exact reveal manifest and
opaque ordinal task files.  Tuning materialization, the tuning campaign, the
compatibility review, and the authoritative freeze consume only the
tuning-visible root and must never receive, derive, enumerate, stat, or open the
reveal root.  A post-freeze guardian materializer independently verifies the raw
reveal manifest and task bytes against the commitment, writes an immutable
reveal receipt plus a separately materialized held-out root, and the held-out
campaign then validates the receipt and held-out materialization without ever
opening the raw reveal bundle.

This module grants no model start, inference, tool execution, provider call,
credential, network, external effect, held-out disclosure, completion,
publication, deployment, acceptance, or promotion authority.

See SEALED-CORPUS-CONTRACT.md for the formal custody and disclosure contract.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Any

import portal_outcome_evaluation as evaluation
import portal_outcome_materialize_battery as materializer


COMMITMENT_SCHEMA = "https://osmantic.com/pixel/schemas/pixel-sealed-corpus-commitment-v1.schema.json"
REVEAL_SCHEMA = "https://osmantic.com/pixel/schemas/pixel-sealed-corpus-reveal-v1.schema.json"
FREEZE_SCHEMA = "https://osmantic.com/pixel/schemas/pixel-sealed-corpus-freeze-v1.schema.json"
RECEIPT_SCHEMA = "https://osmantic.com/pixel/schemas/pixel-sealed-corpus-reveal-receipt-v1.schema.json"
BATTERY_SCHEMA = "https://osmantic.com/pixel/schemas/agent-comparison-task-battery-v1.schema.json"
SET_IDENTITY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
TASK_ID_RE = re.compile(r"^(?:battery|stress|trial|heldout)-[a-z0-9][a-z0-9-]{2,62}$")
ORDINAL_RE = re.compile(r"^\d{4}$")
MAX_TASKS = 500
MAX_RECORD_BYTES = 2 * 1024 * 1024
MAX_TASK_FILE_BYTES = 96 * 1024 * 1024
# v1 operational cap: a sane aggregate/input bound (1 GiB) rather than the
# theoretical 500 * 96 MiB = 48 GiB read-into-memory ceiling.  Per-task 96 MiB
# remains, but the aggregate and any single input corpus are bounded to 1 GiB.
MAX_AGGREGATE_BYTES = 1024 * 1024 * 1024
MAX_BATTERY_BYTES = MAX_AGGREGATE_BYTES
MAX_CORPUS_BYTES = MAX_AGGREGATE_BYTES

COMMITMENT_BOUNDARY = (
    "Content-free sealed held-out corpus commitment only. It cryptographically binds the owner-private reveal "
    "manifest and task-set aggregates, bounded counts, total bytes, and schema identity but contains no prompt, "
    "workspace, expected reply, verifier command or program, research fixture, rehearsal fixture or action, task "
    "id, profile, axis, source, per-task size, or filename, and no semantically reconstructive held-out content. "
    "Secrecy derives from owner custody and absolute-root file separation, never from hashing alone."
)
REVEAL_BOUNDARY = (
    "Owner-private sealed held-out corpus reveal manifest only. It maps opaque ordinal task files to exact "
    "digests and sizes and grants no execution, model, provider, credential, network, external effect, held-out "
    "disclosure, acceptance, publication, deployment, or promotion authority."
)
FREEZE_BOUNDARY = (
    "Content-free immutable pre-reveal tuning freeze only. It binds the exact content-free commitment, the exact "
    "campaign/candidate/source/model/inference/pair/preflight identity, and every completed tuning result before "
    "any held-out task byte is disclosed. It authorizes at most one local owner-private held-out materialization "
    "for this exact campaign/candidate and grants no execution, retuning, model, provider, credential, network, "
    "external effect, held-out disclosure, acceptance, publication, deployment, or promotion authority."
)
RECEIPT_BOUNDARY = (
    "Owner-private immutable held-out reveal/materialization receipt only. It binds the exact commitment, freeze, "
    "campaign/candidate identity, reveal manifest, and separately materialized held-out root. It records the "
    "owner-private consumption/settlement of the single authorized held-out materialization and grants no "
    "execution, model, provider, credential, network, external effect, held-out disclosure, acceptance, "
    "publication, deployment, promotion, or further materialization authority."
)
AUTHORITY = {
    "reveal": False, "tuning": False, "retuning": False, "disclosure": False,
    "execution": False, "promotion": False,
}
FREEZE_AUTHORITY = {
    "execution": False, "retuning": False, "heldOutDisclosure": False,
    "heldOutMaterialization": True, "completion": False, "promotion": False,
}
RECEIPT_AUTHORITY = {
    "execution": False, "retuning": False, "heldOutDisclosure": False,
    "heldOutMaterialization": False, "completion": False, "promotion": False,
}

# Explicit crash hooks for guardian settlement testing.  A hook raising an
# exception simulates a process crash at a named window so idempotent retry can
# be proven to converge.  Hooks are never active in production paths.
_guardian_hooks: dict[str, Any] = {}


def _fire_hook(name: str) -> None:
    hook = _guardian_hooks.get(name)
    if hook is not None:
        hook()
CANDIDATE_FIELDS = frozenset({
    "candidateId", "candidateSourceArchiveSha256", "materializationSha256", "modelContractSha256",
    "inferenceContractSha256", "pairConfigSha256", "preflightSha256", "verifierImageDigest",
    "architecture", "profile", "evaluationRegime", "runtimeCondition",
})
TUNING_RESULT_FIELDS = frozenset({
    "batteryTaskId", "taskSha256", "status", "attempt", "comparisonSha256", "classification",
})
REVEAL_TASK_FIELDS = frozenset({"file", "sha256", "bytes"})

COMMITMENT_FIELDS = frozenset({
    "$schema", "schemaVersion", "operation", "batterySchema", "revealSchema", "setIdentity",
    "tuningTaskCount", "tuningCorpusSha256", "tuningSourceTaskSetSha256",
    "heldOutTaskCount", "heldOutTaskSetSha256",
    "heldOutTotalBytes", "revealManifestSha256", "boundary", "authority",
})
REVEAL_FIELDS = frozenset({
    "$schema", "schemaVersion", "operation", "batterySchema", "setIdentity",
    "heldOutTaskCount", "heldOutTotalBytes", "heldOutTaskSetSha256", "tasks", "boundary", "authority",
})
FREEZE_FIELDS = frozenset({
    "$schema", "schemaVersion", "operation", "campaignId", "materializationSha256", "pairConfigSha256",
    "preflightSha256", "candidateSourceArchiveSha256", "architecture", "modelContractSha256",
    "inferenceContractSha256", "verifierImageDigest", "profile", "evaluationRegime", "runtimeCondition",
    "candidate", "candidateSha256", "sealedCommitmentSha256", "tuningTaskCount",
    "tuningCorpusSha256", "tuningSourceTaskSetSha256", "tuningMaterializedTaskSetSha256",
    "tuningEvidenceSetSha256", "tuningTasks", "heldOutTaskSetSha256",
    "heldOutTaskCount", "heldOutTotalBytes", "revealManifestSha256", "heldOutTaskBytesOpened",
    "authority", "boundary",
})
RECEIPT_FIELDS = frozenset({
    "$schema", "schemaVersion", "operation", "commitmentSha256", "freezeSha256", "campaignId",
    "candidateSha256", "revealManifestSha256", "heldOutMaterializationSha256", "authority", "boundary",
})


def _canonical(value: Any) -> bytes:
    return evaluation.canonical(value)


def _sha256(payload: bytes) -> str:
    return evaluation.sha256(payload)


def _private_directory(path: Path, label: str) -> Path:
    try:
        info = path.lstat()
        actual = path.resolve(strict=True)
    except OSError as exc:
        raise evaluation.OutcomeError(f"{label} is unavailable") from exc
    if actual != path or not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or os.name != "nt" and (
        info.st_uid != os.geteuid() or info.st_mode & 0o077
    ):
        raise evaluation.OutcomeError(f"{label} is linked or not an owner-private directory")
    return path


def _new_private_directory(path: Path, label: str) -> Path:
    if not path.is_absolute() or path.exists():
        raise evaluation.OutcomeError(f"{label} must be a new absolute path")
    evaluation.private_parent(path)
    path.mkdir(mode=0o700, parents=False)
    if os.name != "nt":
        path.chmod(0o700)
    return _private_directory(path, label)


def _validate_candidate(candidate: Any) -> dict[str, Any]:
    value = evaluation.exact_fields(candidate, CANDIDATE_FIELDS, "sealed corpus candidate identity")
    if not isinstance(value["candidateId"], str) or len(value["candidateId"]) < 9:
        raise evaluation.OutcomeError("sealed corpus candidate id is invalid")
    for field in ("candidateSourceArchiveSha256", "materializationSha256", "modelContractSha256",
                  "inferenceContractSha256", "pairConfigSha256", "preflightSha256"):
        evaluation.valid_hash(value[field], f"sealed corpus candidate {field}")
    if (
        not isinstance(value["verifierImageDigest"], str)
        or re.fullmatch(r"sha256:[a-f0-9]{64}", value["verifierImageDigest"]) is None
        or value["architecture"] not in {"amd64", "arm64"} or value["profile"] not in {
            "assistant", "builder", "controller", "researcher",
        } or value["evaluationRegime"] not in materializer.EVALUATION_REGIMES or value["runtimeCondition"] not in {
            "cold-first-request", "warm-neutral-probe",
        }
    ):
        raise evaluation.OutcomeError("sealed corpus candidate architecture, profile, regime, runtime, or verifier is invalid")
    return value


def _validate_commitment(value: Any) -> dict[str, Any]:
    value = evaluation.exact_fields(value, COMMITMENT_FIELDS, "sealed corpus commitment")
    if (
        value["$schema"] != COMMITMENT_SCHEMA or value["schemaVersion"] != 1
        or value["operation"] != "pixel-sealed-corpus-commitment"
        or value["batterySchema"] != BATTERY_SCHEMA or value["revealSchema"] != REVEAL_SCHEMA
        or value["boundary"] != COMMITMENT_BOUNDARY
        or not isinstance(value["setIdentity"], str) or SET_IDENTITY_RE.fullmatch(value["setIdentity"]) is None
    ):
        raise evaluation.OutcomeError("sealed corpus commitment identity is invalid")
    evaluation.integer(value["tuningTaskCount"], 1, MAX_TASKS, "sealed corpus commitment tuning count")
    evaluation.integer(value["heldOutTaskCount"], 1, MAX_TASKS, "sealed corpus commitment held-out count")
    evaluation.valid_hash(value["tuningCorpusSha256"], "sealed corpus commitment tuning corpus")
    evaluation.valid_hash(value["tuningSourceTaskSetSha256"], "sealed corpus commitment tuning source set")
    evaluation.valid_hash(value["heldOutTaskSetSha256"], "sealed corpus commitment held-out set")
    evaluation.valid_hash(value["revealManifestSha256"], "sealed corpus commitment reveal manifest")
    evaluation.integer(value["heldOutTotalBytes"], 1, MAX_AGGREGATE_BYTES, "sealed corpus commitment held-out bytes")
    if value["authority"] != AUTHORITY:
        raise evaluation.OutcomeError("sealed corpus commitment authority is invalid")
    return value


def _validate_reveal(value: Any) -> dict[str, Any]:
    value = evaluation.exact_fields(value, REVEAL_FIELDS, "sealed corpus reveal manifest")
    if (
        value["$schema"] != REVEAL_SCHEMA or value["schemaVersion"] != 1
        or value["operation"] != "pixel-sealed-corpus-reveal"
        or value["batterySchema"] != BATTERY_SCHEMA or value["boundary"] != REVEAL_BOUNDARY
        or not isinstance(value["setIdentity"], str) or SET_IDENTITY_RE.fullmatch(value["setIdentity"]) is None
        or value["authority"] != AUTHORITY
    ):
        raise evaluation.OutcomeError("sealed corpus reveal manifest identity is invalid")
    evaluation.integer(value["heldOutTaskCount"], 1, MAX_TASKS, "sealed corpus reveal held-out count")
    evaluation.integer(value["heldOutTotalBytes"], 1, MAX_AGGREGATE_BYTES, "sealed corpus reveal held-out bytes")
    evaluation.valid_hash(value["heldOutTaskSetSha256"], "sealed corpus reveal held-out set")
    tasks = value["tasks"]
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= MAX_TASKS or len(tasks) != value["heldOutTaskCount"]:
        raise evaluation.OutcomeError("sealed corpus reveal manifest task set is invalid")
    seen: set[str] = set()
    for index, item in enumerate(tasks):
        if not isinstance(item, dict) or set(item) != REVEAL_TASK_FIELDS:
            raise evaluation.OutcomeError(f"sealed corpus reveal manifest task {index} is invalid")
        ordinal = item["file"]
        if not isinstance(ordinal, str) or ORDINAL_RE.fullmatch(ordinal) is None or ordinal in seen:
            raise evaluation.OutcomeError(f"sealed corpus reveal manifest task {index} filename is invalid or duplicated")
        seen.add(ordinal)
        evaluation.valid_hash(item["sha256"], f"sealed corpus reveal manifest task {index} digest")
        evaluation.integer(item["bytes"], 1, MAX_TASK_FILE_BYTES, f"sealed corpus reveal manifest task {index} bytes")
    return value


def _tuning_materialized_set_sha(tuning_tasks: list[dict[str, Any]]) -> str:
    """Canonical hash over the materialized task digests in the freeze tuning list."""
    return _sha256(_canonical(sorted(item["taskSha256"] for item in tuning_tasks)))


def _tuning_evidence_set_sha(tuning_tasks: list[dict[str, Any]]) -> str:
    """Canonical full tuning-evidence hash: ids, task hashes, attempts, comparison hashes, status, classification."""
    items = sorted(({
        "batteryTaskId": item["batteryTaskId"], "taskSha256": item["taskSha256"],
        "attempt": item["attempt"], "comparisonSha256": item["comparisonSha256"],
        "status": item["status"], "classification": item["classification"],
    } for item in tuning_tasks), key=lambda item: item["batteryTaskId"])
    return _sha256(_canonical(items))


def _validate_freeze(value: Any) -> dict[str, Any]:
    value = evaluation.exact_fields(value, FREEZE_FIELDS, "sealed corpus freeze")
    if (
        value["$schema"] != FREEZE_SCHEMA or value["schemaVersion"] != 1
        or value["operation"] != "pixel-sealed-corpus-tuning-freeze"
        or value["boundary"] != FREEZE_BOUNDARY or value["authority"] != FREEZE_AUTHORITY
        or value["heldOutTaskBytesOpened"] is not False
    ):
        raise evaluation.OutcomeError("sealed corpus freeze identity is invalid")
    for field in ("materializationSha256", "pairConfigSha256", "preflightSha256",
                  "candidateSourceArchiveSha256", "modelContractSha256", "inferenceContractSha256",
                  "sealedCommitmentSha256", "tuningCorpusSha256", "tuningSourceTaskSetSha256",
                  "tuningMaterializedTaskSetSha256", "tuningEvidenceSetSha256",
                  "heldOutTaskSetSha256", "revealManifestSha256"):
        evaluation.valid_hash(value[field], f"sealed corpus freeze {field}")
    if (
        not isinstance(value["campaignId"], str) or not value["campaignId"]
        or value["architecture"] not in {"amd64", "arm64"}
        or value["profile"] not in {"assistant", "builder", "controller", "researcher"}
        or value["evaluationRegime"] not in materializer.EVALUATION_REGIMES
        or value["runtimeCondition"] not in {"cold-first-request", "warm-neutral-probe"}
        or re.fullmatch(r"sha256:[a-f0-9]{64}", value["verifierImageDigest"] or "") is None
    ):
        raise evaluation.OutcomeError("sealed corpus freeze campaign, architecture, profile, regime, runtime, or verifier is invalid")
    candidate = _validate_candidate(value["candidate"])
    if value["candidateSha256"] != _sha256(_canonical(candidate)):
        raise evaluation.OutcomeError("sealed corpus freeze candidate digest is invalid")
    if (
        value["materializationSha256"] != candidate["materializationSha256"]
        or value["pairConfigSha256"] != candidate["pairConfigSha256"]
        or value["preflightSha256"] != candidate["preflightSha256"]
        or value["candidateSourceArchiveSha256"] != candidate["candidateSourceArchiveSha256"]
        or value["architecture"] != candidate["architecture"]
        or value["modelContractSha256"] != candidate["modelContractSha256"]
        or value["inferenceContractSha256"] != candidate["inferenceContractSha256"]
        or value["verifierImageDigest"] != candidate["verifierImageDigest"]
        or value["profile"] != candidate["profile"]
        or value["evaluationRegime"] != candidate["evaluationRegime"]
        or value["runtimeCondition"] != candidate["runtimeCondition"]
    ):
        raise evaluation.OutcomeError("sealed corpus freeze top-level identity differs from its candidate binding")
    evaluation.integer(value["tuningTaskCount"], 1, MAX_TASKS, "sealed corpus freeze tuning count")
    evaluation.integer(value["heldOutTaskCount"], 1, MAX_TASKS, "sealed corpus freeze held-out count")
    evaluation.integer(value["heldOutTotalBytes"], 1, MAX_AGGREGATE_BYTES, "sealed corpus freeze held-out bytes")
    results = value["tuningTasks"]
    if not isinstance(results, list) or len(results) != value["tuningTaskCount"] or not results:
        raise evaluation.OutcomeError("sealed corpus freeze tuning results are invalid")
    seen: set[str] = set()
    keys: list[str] = []
    for index, item in enumerate(results):
        if not isinstance(item, dict) or set(item) != TUNING_RESULT_FIELDS:
            raise evaluation.OutcomeError(f"sealed corpus freeze tuning result {index} is invalid")
        task_id = item["batteryTaskId"]
        if not isinstance(task_id, str) or TASK_ID_RE.fullmatch(task_id) is None or task_id in seen:
            raise evaluation.OutcomeError("sealed corpus freeze tuning result identity is invalid or duplicated")
        seen.add(task_id)
        keys.append(task_id)
        evaluation.valid_hash(item["taskSha256"], f"sealed corpus freeze tuning result {index} task digest")
        if item["status"] != "pass" or not isinstance(item["attempt"], int) or not 1 <= item["attempt"] <= 999:
            raise evaluation.OutcomeError(f"sealed corpus freeze tuning result {index} is not a passing baseline")
        evaluation.valid_hash(item["comparisonSha256"], f"sealed corpus freeze tuning result {index} comparison")
        if item["classification"] not in {
            "parity", "pixel-regression", "capability-blocking", "reference-failure", "unexplained-delta", "safety-failure",
        }:
            raise evaluation.OutcomeError(f"sealed corpus freeze tuning result {index} classification is invalid")
    if keys != sorted(keys):
        raise evaluation.OutcomeError("sealed corpus freeze tuning results must be canonically sorted by task id")
    if value["tuningMaterializedTaskSetSha256"] != _tuning_materialized_set_sha(results):
        raise evaluation.OutcomeError("sealed corpus freeze materialized task set digest is inconsistent with its tuning results")
    if value["tuningEvidenceSetSha256"] != _tuning_evidence_set_sha(results):
        raise evaluation.OutcomeError("sealed corpus freeze evidence set digest is inconsistent with its tuning results")
    return value


def _validate_receipt(value: Any) -> dict[str, Any]:
    value = evaluation.exact_fields(value, RECEIPT_FIELDS, "sealed corpus reveal receipt")
    if (
        value["$schema"] != RECEIPT_SCHEMA or value["schemaVersion"] != 1
        or value["operation"] != "pixel-sealed-corpus-reveal-receipt"
        or value["boundary"] != RECEIPT_BOUNDARY or value["authority"] != RECEIPT_AUTHORITY
        or not isinstance(value["campaignId"], str) or not value["campaignId"]
    ):
        raise evaluation.OutcomeError("sealed corpus reveal receipt identity is invalid")
    for field in ("commitmentSha256", "freezeSha256", "candidateSha256", "revealManifestSha256",
                  "heldOutMaterializationSha256"):
        evaluation.valid_hash(value[field], f"sealed corpus reveal receipt {field}")
    return value


def _load_record(path: Path, label: str, validator, limit: int = MAX_RECORD_BYTES) -> tuple[dict[str, Any], bytes]:
    value, raw = evaluation.read_json(path, label, private=True)
    if len(raw) > limit:
        raise evaluation.OutcomeError(f"{label} is oversized")
    value = validator(value)
    return value, raw


def _build_reveal(heldout: list[dict[str, Any]], reveal_root: Path, set_identity: str) -> tuple[dict[str, Any], bytes, str, int]:
    heldout = sorted(heldout, key=lambda task: task["taskId"])
    manifest_tasks: list[dict[str, Any]] = []
    payload_hashes: list[str] = []
    total_bytes = 0
    for index, task in enumerate(heldout):
        payload = _canonical(task)
        size = len(payload)
        if size > MAX_TASK_FILE_BYTES:
            raise evaluation.OutcomeError("sealed reveal task is oversized")
        total_bytes += size
        if total_bytes > MAX_AGGREGATE_BYTES:
            raise evaluation.OutcomeError("sealed reveal aggregate bytes are oversized")
        digest = _sha256(payload)
        ordinal = f"{index + 1:04d}"
        evaluation.write_new_private(reveal_root / f"{ordinal}.task", payload)
        manifest_tasks.append({"file": ordinal, "sha256": digest, "bytes": size})
        payload_hashes.append(digest)
    heldout_set_sha = _sha256(_canonical(sorted(payload_hashes)))
    manifest = {
        "$schema": REVEAL_SCHEMA, "schemaVersion": 1, "operation": "pixel-sealed-corpus-reveal",
        "batterySchema": BATTERY_SCHEMA, "setIdentity": set_identity,
        "heldOutTaskCount": len(heldout), "heldOutTotalBytes": total_bytes,
        "heldOutTaskSetSha256": heldout_set_sha, "tasks": manifest_tasks,
        "boundary": REVEAL_BOUNDARY, "authority": dict(AUTHORITY),
    }
    manifest_payload = _canonical(manifest)
    if len(manifest_payload) > MAX_RECORD_BYTES:
        raise evaluation.OutcomeError("sealed reveal manifest is oversized")
    evaluation.write_new_private(reveal_root / "manifest.json", manifest_payload)
    return manifest, manifest_payload, heldout_set_sha, total_bytes


def _safe_remove_created_root(path: Path) -> None:
    """Remove only an exact owner-private root this call created; never rmtree a substituted path."""
    import shutil
    try:
        if not path.is_absolute():
            return
        info = path.lstat()
        if path.resolve(strict=True) != path or not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            return
        if os.name != "nt" and (info.st_uid != os.geteuid() or info.st_mode & 0o077):
            return
    except OSError:
        return
    try:
        shutil.rmtree(path)
    except OSError:
        pass


def _build_commitment(tuning: list[dict[str, Any]], heldout_set_sha: str, heldout_count: int,
                      heldout_total_bytes: int, manifest_sha: str, set_identity: str,
                      tuning_corpus_sha: str) -> dict[str, Any]:
    tuning_source_set_sha = _sha256(_canonical(sorted(_sha256(_canonical(task)) for task in tuning)))
    return {
        "$schema": COMMITMENT_SCHEMA, "schemaVersion": 1, "operation": "pixel-sealed-corpus-commitment",
        "batterySchema": BATTERY_SCHEMA, "revealSchema": REVEAL_SCHEMA, "setIdentity": set_identity,
        "tuningTaskCount": len(tuning), "tuningCorpusSha256": tuning_corpus_sha,
        "tuningSourceTaskSetSha256": tuning_source_set_sha,
        "heldOutTaskCount": heldout_count, "heldOutTaskSetSha256": heldout_set_sha,
        "heldOutTotalBytes": heldout_total_bytes, "revealManifestSha256": manifest_sha,
        "boundary": COMMITMENT_BOUNDARY, "authority": dict(AUTHORITY),
    }


def split_battery(battery_payload: bytes, tuning_root: Path, reveal_root: Path,
                  set_identity: str = "held-out-v1") -> dict[str, Any]:
    """Split a full battery into a tuning-visible root and a separately custodied reveal root."""
    if not isinstance(set_identity, str) or SET_IDENTITY_RE.fullmatch(set_identity) is None:
        raise evaluation.OutcomeError("sealed corpus set identity is invalid")
    if tuning_root == reveal_root or not tuning_root.is_absolute() or not reveal_root.is_absolute():
        raise evaluation.OutcomeError("sealed tuning and reveal roots must be distinct absolute paths")
    if reveal_root in tuning_root.parents or tuning_root in reveal_root.parents:
        raise evaluation.OutcomeError("sealed tuning and reveal roots must not be nested")
    battery = materializer.validate_battery(battery_payload)
    tuning = [task for task in battery["tasks"] if materializer.effective_partition(task) == "tuning"]
    heldout = [task for task in battery["tasks"] if materializer.effective_partition(task) == "held-out"]
    if not tuning or not heldout:
        raise evaluation.OutcomeError("sealed split requires at least one tuning and one held-out task")
    created: list[Path] = []
    try:
        created.append(_new_private_directory(tuning_root, "sealed tuning root"))
        created.append(_new_private_directory(reveal_root, "sealed reveal root"))
        tuning_battery = dict(battery)
        tuning_battery["tasks"] = tuning
        tuning_payload = _canonical(tuning_battery)
        if len(tuning_payload) > MAX_CORPUS_BYTES:
            raise evaluation.OutcomeError("sealed tuning corpus is oversized")
        evaluation.write_new_private(tuning_root / "tuning.json", tuning_payload)
        tuning_corpus_sha = _sha256(tuning_payload)

        manifest, manifest_payload, heldout_set_sha, heldout_total_bytes = _build_reveal(heldout, reveal_root, set_identity)
        commitment = _build_commitment(tuning, heldout_set_sha, len(heldout), heldout_total_bytes,
                                       _sha256(manifest_payload), set_identity, tuning_corpus_sha)
        commitment_payload = _canonical(commitment)
        evaluation.write_new_private(tuning_root / "commitment.json", commitment_payload)
        return {
            "commitment": commitment,
            "commitmentSha256": _sha256(commitment_payload),
            "tuningTaskCount": len(tuning), "heldOutTaskCount": len(heldout),
            "tuningCorpusSha256": commitment["tuningCorpusSha256"],
            "tuningSourceTaskSetSha256": commitment["tuningSourceTaskSetSha256"],
            "heldOutTaskSetSha256": commitment["heldOutTaskSetSha256"],
            "heldOutTotalBytes": commitment["heldOutTotalBytes"],
            "revealManifestSha256": commitment["revealManifestSha256"],
            "tuningSha256": tuning_corpus_sha,
            "revealSha256": _sha256(manifest_payload),
            "tuningRoot": tuning_root, "revealRoot": reveal_root,
        }
    except BaseException:
        for root in created:
            _safe_remove_created_root(root)
        raise


def _read_commitment(tuning_root: Path) -> tuple[dict[str, Any], bytes]:
    _private_directory(tuning_root, "sealed tuning root")
    return _load_record(tuning_root / "commitment.json", "private sealed corpus commitment", _validate_commitment)


def _read_freeze(freeze_path: Path) -> tuple[dict[str, Any], bytes]:
    if not freeze_path.is_absolute():
        raise evaluation.OutcomeError("sealed corpus freeze path must be absolute")
    if not freeze_path.exists():
        raise evaluation.OutcomeError("held-out reveal requires an exact pre-disclosure tuning freeze")
    return _load_record(freeze_path, "private sealed corpus freeze", _validate_freeze)


def _read_receipt(receipt_path: Path) -> tuple[dict[str, Any], bytes]:
    if not receipt_path.is_absolute():
        raise evaluation.OutcomeError("sealed corpus reveal receipt path must be absolute")
    return _load_record(receipt_path, "private sealed corpus reveal receipt", _validate_receipt)


def candidate_identity(identity: dict[str, Any], materialization: dict[str, Any] | None = None) -> dict[str, Any]:
    """Derive the candidate identity solely from the campaign's authoritative identity.

    The identity carries every field needed to recompute the candidate deterministically,
    including the exact source archive hash and architecture.  `materialization` is accepted
    for backward compatibility but is not required; no source semantics are fabricated.
    """
    seed = _canonical({
        "candidateSourceArchiveSha256": identity["candidateSourceArchiveSha256"],
        "architecture": identity["architecture"],
        "materializationSha256": identity["materializationSha256"],
        "pairConfigSha256": identity["pairConfigSha256"],
        "preflightSha256": identity["preflightSha256"],
        "modelContractSha256": identity["modelContractSha256"],
        "inferenceContractSha256": identity["inferenceContractSha256"],
        "verifierImageDigest": identity["verifierImageDigest"],
        "profile": identity["profile"],
        "evaluationRegime": identity["evaluationRegime"],
        "runtimeCondition": identity["runtimeCondition"],
    })
    return {
        "candidateId": f"candidate-{_sha256(seed)[:24]}",
        "candidateSourceArchiveSha256": identity["candidateSourceArchiveSha256"],
        "materializationSha256": identity["materializationSha256"],
        "modelContractSha256": identity["modelContractSha256"],
        "inferenceContractSha256": identity["inferenceContractSha256"],
        "pairConfigSha256": identity["pairConfigSha256"],
        "preflightSha256": identity["preflightSha256"],
        "verifierImageDigest": identity["verifierImageDigest"],
        "architecture": identity["architecture"],
        "profile": identity["profile"],
        "evaluationRegime": identity["evaluationRegime"],
        "runtimeCondition": identity["runtimeCondition"],
    }


def _validate_commitment_tuning(commitment: dict[str, Any], tuning_payload: bytes) -> None:
    """Prove the exact tuning.json bytes and source task set/count match the commitment."""
    if _sha256(tuning_payload) != commitment["tuningCorpusSha256"]:
        raise evaluation.OutcomeError("tuning corpus bytes differ from the exact commitment tuning corpus")
    tuning = evaluation.parse_json(tuning_payload, "sealed tuning corpus")
    tuning = materializer.validate_battery(_canonical(tuning))
    source_tasks = [task for task in tuning["tasks"] if materializer.effective_partition(task) == "tuning"]
    if len(source_tasks) != commitment["tuningTaskCount"]:
        raise evaluation.OutcomeError("tuning corpus source task count differs from the commitment")
    source_set_sha = _sha256(_canonical(sorted(_sha256(_canonical(task)) for task in source_tasks)))
    if source_set_sha != commitment["tuningSourceTaskSetSha256"]:
        raise evaluation.OutcomeError("tuning corpus source task set differs from the commitment")


def build_freeze(
    *, identity: dict[str, Any], materialization: dict[str, Any], commitment: dict[str, Any],
    commitment_raw: bytes, tuning_results: list[dict[str, Any]],
) -> tuple[dict[str, Any], bytes]:
    """Build the single authoritative content-free freeze from campaign-validated tuning evidence."""
    candidate = candidate_identity(identity, materialization)
    tuning_results = sorted(tuning_results, key=lambda item: item["batteryTaskId"])
    tuning_inventory = {
        item["batteryTaskId"]: item["taskSha256"]
        for item in materialization["tasks"] if item["partition"] == "tuning"
    }
    if len(tuning_results) != len(tuning_inventory) or len(tuning_inventory) != commitment["tuningTaskCount"]:
        raise evaluation.OutcomeError("cannot freeze until every committed tuning task has an exact passing result")
    for result in tuning_results:
        if result["batteryTaskId"] not in tuning_inventory or tuning_inventory[result["batteryTaskId"]] != result["taskSha256"]:
            raise evaluation.OutcomeError("freeze tuning result does not match the exact materialized tuning inventory")
    freeze_value = {
        "$schema": FREEZE_SCHEMA, "schemaVersion": 1, "operation": "pixel-sealed-corpus-tuning-freeze",
        "campaignId": identity["campaignId"], "materializationSha256": identity["materializationSha256"],
        "pairConfigSha256": identity["pairConfigSha256"], "preflightSha256": identity["preflightSha256"],
        "candidateSourceArchiveSha256": identity["candidateSourceArchiveSha256"],
        "architecture": identity["architecture"], "modelContractSha256": identity["modelContractSha256"],
        "inferenceContractSha256": identity["inferenceContractSha256"],
        "verifierImageDigest": identity["verifierImageDigest"], "profile": identity["profile"],
        "evaluationRegime": identity["evaluationRegime"], "runtimeCondition": identity["runtimeCondition"],
        "candidate": candidate, "candidateSha256": _sha256(_canonical(candidate)),
        "sealedCommitmentSha256": _sha256(commitment_raw),
        "tuningTaskCount": commitment["tuningTaskCount"],
        "tuningCorpusSha256": commitment["tuningCorpusSha256"],
        "tuningSourceTaskSetSha256": commitment["tuningSourceTaskSetSha256"],
        "tuningMaterializedTaskSetSha256": _tuning_materialized_set_sha(tuning_results),
        "tuningEvidenceSetSha256": _tuning_evidence_set_sha(tuning_results),
        "tuningTasks": tuning_results,
        "heldOutTaskSetSha256": commitment["heldOutTaskSetSha256"],
        "heldOutTaskCount": commitment["heldOutTaskCount"],
        "heldOutTotalBytes": commitment["heldOutTotalBytes"],
        "revealManifestSha256": commitment["revealManifestSha256"],
        "heldOutTaskBytesOpened": False,
        "authority": dict(FREEZE_AUTHORITY), "boundary": FREEZE_BOUNDARY,
    }
    _validate_freeze(freeze_value)
    return freeze_value, _canonical(freeze_value)


def freeze_binds(freeze: dict[str, Any], commitment: dict[str, Any], commitment_raw: bytes,
                 candidate: dict[str, Any]) -> None:
    """Reject a freeze that does not bind the exact commitment and candidate."""
    if (
        freeze["sealedCommitmentSha256"] != _sha256(commitment_raw)
        or freeze["heldOutTaskSetSha256"] != commitment["heldOutTaskSetSha256"]
        or freeze["heldOutTaskCount"] != commitment["heldOutTaskCount"]
        or freeze["heldOutTotalBytes"] != commitment["heldOutTotalBytes"]
        or freeze["revealManifestSha256"] != commitment["revealManifestSha256"]
        or freeze["tuningCorpusSha256"] != commitment["tuningCorpusSha256"]
        or freeze["tuningSourceTaskSetSha256"] != commitment["tuningSourceTaskSetSha256"]
        or freeze["candidateSha256"] != _sha256(_canonical(candidate))
    ):
        raise evaluation.OutcomeError("tuning freeze does not bind this exact commitment and candidate")


def _verify_reveal(reveal_root: Path, commitment: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Verify the raw reveal manifest and opaque task files against the commitment (guardian only)."""
    _private_directory(reveal_root, "sealed reveal root")
    manifest, manifest_raw = _load_record(reveal_root / "manifest.json", "private sealed corpus reveal manifest", _validate_reveal)
    if _sha256(manifest_raw) != commitment["revealManifestSha256"]:
        raise evaluation.OutcomeError("held-out reveal manifest differs from its commitment")
    expected_files = {"manifest.json"} | {f"{item['file']}.task" for item in manifest["tasks"]}
    if {entry.name for entry in reveal_root.iterdir()} != expected_files:
        raise evaluation.OutcomeError("held-out reveal root contains an unexpected, extra, or missing file")
    if (
        manifest["heldOutTaskCount"] != commitment["heldOutTaskCount"]
        or manifest["heldOutTaskSetSha256"] != commitment["heldOutTaskSetSha256"]
        or manifest["heldOutTotalBytes"] != commitment["heldOutTotalBytes"]
    ):
        raise evaluation.OutcomeError("held-out reveal manifest aggregate differs from its commitment")
    tasks: list[dict[str, Any]] = []
    payload_hashes: list[str] = []
    total_bytes = 0
    for item in manifest["tasks"]:
        ordinal = item["file"]
        path = reveal_root / f"{ordinal}.task"
        if path.parent != reveal_root or ORDINAL_RE.fullmatch(path.name[:4]) is None:
            raise evaluation.OutcomeError("held-out reveal task filename traversal is rejected")
        payload = evaluation.read_bytes(path, limit=MAX_TASK_FILE_BYTES, private=True)
        if len(payload) != item["bytes"] or _sha256(payload) != item["sha256"]:
            raise evaluation.OutcomeError("held-out reveal task file does not match its manifest digest and size")
        total_bytes += len(payload)
        if total_bytes > MAX_AGGREGATE_BYTES:
            raise evaluation.OutcomeError("held-out reveal aggregate bytes are oversized")
        payload_hashes.append(_sha256(payload))
        task = evaluation.parse_json(payload, "held-out reveal task")
        if not isinstance(task, dict):
            raise evaluation.OutcomeError("held-out reveal task is not an object")
        tasks.append(task)
    if _sha256(_canonical(sorted(payload_hashes))) != commitment["heldOutTaskSetSha256"]:
        raise evaluation.OutcomeError("held-out reveal task set digest differs from its commitment")
    if len(tasks) != commitment["heldOutTaskCount"]:
        raise evaluation.OutcomeError("held-out reveal task count differs from its commitment")
    return tasks, manifest


def _read_freeze_chain(tuning_root: Path, freeze_path: Path, candidate: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], bytes]:
    commitment, commitment_raw = _read_commitment(tuning_root)
    freeze, _freeze_raw = _read_freeze(freeze_path)
    freeze_binds(freeze, commitment, commitment_raw, candidate)
    return freeze, commitment, commitment_raw


def _authorize_guardian_freeze(
    *, tuning_root: Path, freeze_path: Path, root: Path, tuning_materialization_root: Path,
    tuning_output_root: Path, pair_configuration_path: Path, runtime_condition: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], bytes]:
    """Evidence-derived guardian authorization, executed before any held-out byte is opened.

    Reload and validate the original sealed tuning materialization, the original tuning
    campaign output, the exact pair configuration and its preflight, and recompute every
    completed tuning pair through the real unmodified ``_valid_pair`` path.  The stored
    freeze must be at the exact original tuning campaign freeze location and be
    canonical-equal to the recomputed authoritative freeze.  Fail closed on any missing
    or tampered original artifact, campaign identity, pair record, preflight/config
    binding, or freeze.
    """
    import portal_outcome_battery_campaign as campaign

    commitment, commitment_raw = _read_commitment(tuning_root)
    if (
        not isinstance(tuning_output_root, Path) or not tuning_output_root.is_absolute()
        or not isinstance(tuning_materialization_root, Path) or not tuning_materialization_root.is_absolute()
        or not isinstance(freeze_path, Path) or not freeze_path.is_absolute()
        or freeze_path != tuning_output_root / campaign.FREEZE_FILE
    ):
        raise evaluation.OutcomeError("guardian freeze must be the original tuning campaign freeze location")
    if not isinstance(pair_configuration_path, Path) or not pair_configuration_path.is_absolute():
        raise evaluation.OutcomeError("guardian pair configuration path must be an absolute path")
    # Tighten path custody: the original tuning output root must be an absolute canonical
    # owner-private directory before campaign/freeze/pair evidence is read below.
    _private_directory(tuning_output_root, "private original tuning campaign output root")
    configuration, configuration_raw = campaign.pair_runner.load_configuration_record(pair_configuration_path)
    preflight_path = campaign.pair_runner._private_existing(
        campaign.pair_runner._absolute(configuration["preflightPath"], "pair preflight path"),
        "private pair preflight",
    )
    preflight, preflight_raw = campaign.pair_runner.load_preflight(preflight_path)
    pair_config_sha256 = _sha256(configuration_raw)
    preflight_sha256 = _sha256(preflight_raw)
    identity, expected_freeze = campaign._recompute_tuning_freeze(
        root=root, tuning_materialization_root=tuning_materialization_root,
        tuning_output_root=tuning_output_root, pair_config_sha256=pair_config_sha256,
        preflight_sha256=preflight_sha256, configuration=configuration, preflight=preflight,
        runtime_condition=runtime_condition, commitment=commitment, commitment_raw=commitment_raw,
    )
    campaign_observed, _raw = evaluation.read_json(
        tuning_output_root / "campaign.json", "private battery campaign identity", private=True,
    )
    if campaign_observed != identity:
        raise evaluation.OutcomeError("guardian original tuning campaign identity differs from the recomputed campaign")
    freeze, _freeze_raw = _read_freeze(freeze_path)
    if _canonical(freeze) != _canonical(expected_freeze):
        raise evaluation.OutcomeError("guardian freeze differs from the recomputed original tuning campaign freeze")
    candidate = _validate_candidate(freeze["candidate"])
    freeze_binds(freeze, commitment, commitment_raw, candidate)
    if (
        preflight["modelContractSha256"] != candidate["modelContractSha256"]
        or preflight["inferenceContractSha256"] != candidate["inferenceContractSha256"]
        or preflight["profile"] != candidate["profile"]
    ):
        raise evaluation.OutcomeError("guardian preflight/model/inference/profile binding differs from the frozen candidate")
    return freeze, candidate, commitment, commitment_raw


def _validate_guardian_inputs(
    *, freeze: dict[str, Any], candidate: dict[str, Any], model_payload: bytes,
    inference_payload: bytes, verifier_image_digest: str, architecture: str,
    profile: str, evaluation_regime: str, runtime_condition: str,
) -> None:
    """Reject any guardian-supplied candidate/input that differs from the frozen candidate before reveal."""
    if _sha256(model_payload) != candidate["modelContractSha256"]:
        raise evaluation.OutcomeError("guardian model payload differs from the frozen candidate model contract")
    if _sha256(inference_payload) != candidate["inferenceContractSha256"]:
        raise evaluation.OutcomeError("guardian inference payload differs from the frozen candidate inference contract")
    if verifier_image_digest != candidate["verifierImageDigest"]:
        raise evaluation.OutcomeError("guardian verifier image digest differs from the frozen candidate")
    if architecture != candidate["architecture"]:
        raise evaluation.OutcomeError("guardian architecture differs from the frozen candidate")
    if profile != candidate["profile"]:
        raise evaluation.OutcomeError("guardian profile differs from the frozen candidate")
    if evaluation_regime != candidate["evaluationRegime"]:
        raise evaluation.OutcomeError("guardian evaluation regime differs from the frozen candidate")
    if runtime_condition != candidate["runtimeCondition"]:
        raise evaluation.OutcomeError("guardian runtime condition differs from the frozen candidate")


def _settle_staged_materialization(staging: Path, output_root: Path) -> None:
    """Atomically rename a staged owner-private materialization into place; never overwrite."""
    if output_root.exists():
        raise evaluation.OutcomeError("held-out materialization output already exists")
    evaluation.private_parent(output_root)
    try:
        os.rename(staging, output_root)
    except OSError as exc:
        raise evaluation.OutcomeError("held-out materialization settlement failed") from exc


def guardian_materialize_heldout(
    *, reveal_root: Path, tuning_root: Path, freeze_path: Path, receipt_path: Path, root: Path,
    model_payload: bytes, inference_payload: bytes, verifier_image_digest: str, output_root: Path,
    tuning_materialization_root: Path, tuning_output_root: Path, pair_configuration_path: Path,
    runtime_condition: str = "cold-first-request",
    architecture: str = "amd64", base_epoch_ms: int = 1786622400000, profile: str = "builder",
    evaluation_regime: str = "matched-budget",
) -> dict[str, Any]:
    """Guardian-only held-out materialization with crash-safe idempotent settlement.

    The raw manifest/task bytes are verified against the commitment, then the held-out
    materialization is built in an owner-private staging root and atomically settled into
    ``output_root``.  A receipt is written only after the materialization is exact.  On
    retry, any preexisting exact materialization and/or receipt is reused; a substituted
    or mismatched artifact is never overwritten and instead rejects.
    """
    freeze, candidate, commitment, commitment_raw = _authorize_guardian_freeze(
        tuning_root=tuning_root, freeze_path=freeze_path, root=root,
        tuning_materialization_root=tuning_materialization_root,
        tuning_output_root=tuning_output_root, pair_configuration_path=pair_configuration_path,
        runtime_condition=runtime_condition,
    )
    _validate_guardian_inputs(
        freeze=freeze, candidate=candidate, model_payload=model_payload,
        inference_payload=inference_payload, verifier_image_digest=verifier_image_digest,
        architecture=architecture, profile=profile, evaluation_regime=evaluation_regime,
        runtime_condition=runtime_condition,
    )
    tasks, manifest = _verify_reveal(reveal_root, commitment)
    if manifest["setIdentity"] != commitment["setIdentity"]:
        raise evaluation.OutcomeError("sealed corpus reveal set identity differs from its commitment")
    heldout = sorted(tasks, key=lambda task: task["taskId"])
    heldout_battery = {
        "$schema": BATTERY_SCHEMA, "schemaVersion": 1, "operation": "pixel-agent-comparison-task-battery",
        "provenance": "sealed-owner-reveal", "tasks": heldout, "boundary": materializer.BATTERY_BOUNDARY,
    }
    materializer.validate_battery(_canonical(heldout_battery))
    staging = output_root.parent / f".{output_root.name}.staging.{os.getpid()}.{secrets.token_hex(8)}"
    try:
        heldout_mat = materializer.materialize(
            root=root, battery_payload=_canonical(heldout_battery), model_payload=model_payload,
            inference_payload=inference_payload, verifier_image_digest=verifier_image_digest,
            output_root=staging, architecture=architecture, base_epoch_ms=base_epoch_ms,
            profile=profile, evaluation_regime=evaluation_regime, partition="held-out",
            mode="sealed", sealed_commitment_sha256=_sha256(commitment_raw),
        )
        staging_mat_raw = (staging / "materialization.json").read_bytes()
        heldout_materialization_sha = _sha256(staging_mat_raw)

        # Reload the staged materialization through the normal strict materialization
        # loader/validator; do not trust only the returned Python dict.  Validate its
        # task files/admissions and require the held-out partition exactly.  The returned
        # object must canonical-equal the reloaded authoritative object, which is used
        # exclusively thereafter, and the reloaded object must bind the exact frozen
        # candidate and commitment before any settlement or receipt write.
        import portal_outcome_battery_campaign as campaign
        reloaded_mat, _reloaded_sha = campaign.load_materialization(staging, root)
        campaign._validate_materialization_partition(staging, root, reloaded_mat, "held-out")
        if any(item["partition"] != "held-out" for item in reloaded_mat["tasks"]):
            raise evaluation.OutcomeError("held-out materialization contains a non-held-out task")
        if _canonical(heldout_mat) != _canonical(reloaded_mat):
            raise evaluation.OutcomeError(
                "held-out materialization return value differs from its staged authoritative file",
            )
        heldout_mat = reloaded_mat
        validate_materialization_binding(
            materialization=heldout_mat, commitment=commitment, commitment_raw=commitment_raw,
            candidate=candidate, expected_task_count=commitment["heldOutTaskCount"],
            expected_battery_sha256=_sha256(_canonical(heldout_battery)),
        )

        _fire_hook("before_materialization_settlement")
        if output_root.exists():
            existing_raw = evaluation.read_bytes(
                output_root / "materialization.json", limit=evaluation.MAX_JSON_BYTES, private=True,
            )
            if _sha256(existing_raw) != heldout_materialization_sha:
                raise evaluation.OutcomeError(
                    "preexisting held-out materialization differs from the exact commitment/freeze/candidate materialization",
                )
            _safe_remove_created_root(staging)
        else:
            _settle_staged_materialization(staging, output_root)
        _fire_hook("after_materialization_settlement")

        receipt_value = {
            "$schema": RECEIPT_SCHEMA, "schemaVersion": 1, "operation": "pixel-sealed-corpus-reveal-receipt",
            "commitmentSha256": _sha256(commitment_raw), "freezeSha256": _sha256(_canonical(freeze)),
            "campaignId": freeze["campaignId"], "candidateSha256": freeze["candidateSha256"],
            "revealManifestSha256": commitment["revealManifestSha256"],
            "heldOutMaterializationSha256": heldout_materialization_sha,
            "authority": dict(RECEIPT_AUTHORITY), "boundary": RECEIPT_BOUNDARY,
        }
        receipt_payload = _canonical(receipt_value)
        if receipt_path.exists():
            existing_receipt, existing_receipt_raw = _read_receipt(receipt_path)
            if _sha256(existing_receipt_raw) != _sha256(receipt_payload):
                raise evaluation.OutcomeError(
                    "preexisting reveal receipt differs from the exact commitment/freeze/candidate materialization",
                )
        else:
            _fire_hook("before_receipt")
            evaluation.write_new_private(receipt_path, receipt_payload)
        _fire_hook("after_receipt")
        return {
            "heldOutMaterialization": heldout_mat,
            "heldOutMaterializationSha256": heldout_materialization_sha,
            "receipt": receipt_value, "receiptSha256": _sha256(receipt_payload),
            "revealManifestSha256": commitment["revealManifestSha256"],
            "commitmentSha256": _sha256(commitment_raw), "freezeSha256": _sha256(_canonical(freeze)),
        }
    except BaseException:
        _safe_remove_created_root(staging)
        raise


def validate_materialization_binding(
    *,
    materialization: dict[str, Any],
    commitment: dict[str, Any],
    commitment_raw: bytes,
    candidate: dict[str, Any],
    expected_task_count: int,
    expected_battery_sha256: str | None = None,
) -> None:
    """Receipt-independent sealed materialization binding validator.

    Requires the materialization to bind the exact sealed commitment, the frozen
    candidate's model/inference/verifier/architecture/profile/evaluation-regime,
    and the exact committed task count.  When the caller knows the exact built
    battery bytes (guardian path) it also requires the exact battery digest.
    Shared by the guardian held-out settlement path and the campaign-side
    receipt+materialization validation so the two paths cannot drift.
    """
    if (
        materialization["mode"] != "sealed"
        or materialization["sealedCommitmentSha256"] != _sha256(commitment_raw)
        or (expected_battery_sha256 is not None and materialization["batterySha256"] != expected_battery_sha256)
        or materialization["modelContractSha256"] != candidate["modelContractSha256"]
        or materialization["inferenceContractSha256"] != candidate["inferenceContractSha256"]
        or materialization["verifierImageDigest"] != candidate["verifierImageDigest"]
        or materialization["architecture"] != candidate["architecture"]
        or materialization["profile"] != candidate["profile"]
        or materialization["evaluationRegime"] != candidate["evaluationRegime"]
        or len(materialization["tasks"]) != expected_task_count
    ):
        raise evaluation.OutcomeError("sealed materialization does not bind the exact frozen candidate and commitment")


def validate_heldout_materialization(
    *, materialization: dict[str, Any], materialization_raw: bytes, freeze: dict[str, Any],
    commitment: dict[str, Any], commitment_raw: bytes, receipt_path: Path,
) -> dict[str, Any]:
    """Campaign-side validation of the guardian-emitted held-out materialization and reveal receipt."""
    receipt, _receipt_raw = _read_receipt(receipt_path)
    if (
        receipt["commitmentSha256"] != _sha256(commitment_raw)
        or receipt["freezeSha256"] != _sha256(_canonical(freeze))
        or receipt["revealManifestSha256"] != commitment["revealManifestSha256"]
        or receipt["candidateSha256"] != freeze["candidateSha256"]
        or receipt["campaignId"] != freeze["campaignId"]
        or receipt["heldOutMaterializationSha256"] != _sha256(materialization_raw)
    ):
        raise evaluation.OutcomeError("reveal receipt does not bind this exact freeze, commitment, and held-out materialization")
    validate_materialization_binding(
        materialization=materialization, commitment=commitment, commitment_raw=commitment_raw,
        candidate=freeze["candidate"], expected_task_count=commitment["heldOutTaskCount"],
    )
    return receipt


def sealed_tuning_materialize(
    *, tuning_root: Path, root: Path, model_payload: bytes, inference_payload: bytes,
    verifier_image_digest: str, output_root: Path, architecture: str = "amd64",
    base_epoch_ms: int = 1786622400000, profile: str = "builder", evaluation_regime: str = "matched-budget",
) -> dict[str, Any]:
    """Materialize tuning tasks only from the tuning-visible root; never touches the reveal root."""
    _private_directory(tuning_root, "sealed tuning root")
    commitment, commitment_raw = _read_commitment(tuning_root)
    tuning_payload = evaluation.read_bytes(tuning_root / "tuning.json", limit=MAX_CORPUS_BYTES, private=True)
    _validate_commitment_tuning(commitment, tuning_payload)
    return materializer.materialize(
        root=root, battery_payload=tuning_payload, model_payload=model_payload,
        inference_payload=inference_payload, verifier_image_digest=verifier_image_digest,
        output_root=output_root, architecture=architecture, base_epoch_ms=base_epoch_ms,
        profile=profile, evaluation_regime=evaluation_regime, partition="tuning",
        mode="sealed", sealed_commitment_sha256=_sha256(commitment_raw),
    )


def _tuning_materialize_summary(result: dict[str, Any]) -> dict[str, Any]:
    """Content-free identity/summary for a sealed tuning materialization.

    Carries only the exact materialization identity, opaque aggregates, and
    task-set hashes; never emits any corpus, prompt, workspace, task id,
    profile, axis, source, filename, or held-out byte.
    """
    return {
        "schemaVersion": 1, "operation": "pixel-portal-outcome-sealed-tuning-materialization",
        "mode": result["mode"], "batterySha256": result["batterySha256"],
        "sealedCommitmentSha256": result["sealedCommitmentSha256"],
        "modelContractSha256": result["modelContractSha256"],
        "inferenceContractSha256": result["inferenceContractSha256"],
        "verifierImageDigest": result["verifierImageDigest"],
        "architecture": result["architecture"], "profile": result["profile"],
        "evaluationRegime": result["evaluationRegime"],
        "tuningTaskCount": len(result["tasks"]),
        "tuningTaskSetSha256": _sha256(_canonical(sorted(item["taskSha256"] for item in result["tasks"]))),
        "boundary": "Content-free sealed tuning materialization identity/summary only; no corpus bytes.",
    }


def validate_post_freeze(
    *, tuning_root: Path, freeze_path: Path, receipt_path: Path,
    heldout_materialization_root: Path | None = None,
) -> dict[str, Any]:
    """Operator seam: validate the post-freeze guardian chain without executing anything."""
    commitment, commitment_raw = _read_commitment(tuning_root)
    freeze, _freeze_raw = _read_freeze(freeze_path)
    candidate = _validate_candidate(freeze["candidate"])
    freeze_binds(freeze, commitment, commitment_raw, candidate)
    receipt, _receipt_raw = _read_receipt(receipt_path)
    if heldout_materialization_root is not None:
        _private_directory(heldout_materialization_root, "private held-out materialization root")
        materialization_raw = evaluation.read_bytes(
            heldout_materialization_root / "materialization.json",
            limit=evaluation.MAX_JSON_BYTES, private=True,
        )
        materialization = evaluation.parse_json(materialization_raw, "private held-out materialization")
        validate_heldout_materialization(
            materialization=materialization, materialization_raw=materialization_raw,
            freeze=freeze, commitment=commitment, commitment_raw=commitment_raw, receipt_path=receipt_path,
        )
    elif (
        receipt["commitmentSha256"] != _sha256(commitment_raw)
        or receipt["freezeSha256"] != _sha256(_canonical(freeze))
        or receipt["campaignId"] != freeze["campaignId"]
        or receipt["candidateSha256"] != freeze["candidateSha256"]
        or receipt["revealManifestSha256"] != commitment["revealManifestSha256"]
    ):
        raise evaluation.OutcomeError("reveal receipt does not bind this exact freeze and commitment")
    return {
        "commitmentSha256": _sha256(commitment_raw), "freezeSha256": _sha256(_canonical(freeze)),
        "campaignId": freeze["campaignId"], "candidateSha256": freeze["candidateSha256"],
        "revealManifestSha256": commitment["revealManifestSha256"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=(
            "Operational sealed-corpus CLI sequence (each step is a separate command):\n"
            "  1) split --battery --tuning-root --reveal-root\n"
            "  2) tuning-materialize --tuning-root --model --inference --verifier-image-digest --output-root\n"
            "  3) battery_campaign --partition tuning --sealed-tuning-root ...\n"
            "  4) battery_campaign --partition tuning --freeze-tuning --sealed-tuning-root ...\n"
            "  5) guardian-materialize --reveal-root --tuning-root --freeze --receipt --model --inference\n"
            "     --verifier-image-digest --output-root --tuning-materialization-root --tuning-output-root\n"
            "     --pair-configuration-path ...\n"
            "  6) battery_campaign --partition held-out --sealed-tuning-root --sealed-reveal-receipt\n"
            "     --sealed-tuning-materialization-root --sealed-tuning-output-root ...\n"
            "  7) validate --tuning-root --freeze --receipt --heldout-materialization-root\n"
            "The final formal held-out corpus is separately custodied and is not created by these tests."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    split = sub.add_parser("split")
    split.add_argument("--battery", required=True, type=Path)
    split.add_argument("--tuning-root", required=True, type=Path)
    split.add_argument("--reveal-root", required=True, type=Path)
    split.add_argument("--set-identity", default="held-out-v1")

    guardian = sub.add_parser("guardian-materialize")
    guardian.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    guardian.add_argument("--reveal-root", required=True, type=Path)
    guardian.add_argument("--tuning-root", required=True, type=Path)
    guardian.add_argument("--freeze", required=True, type=Path)
    guardian.add_argument("--receipt", required=True, type=Path)
    guardian.add_argument("--model", required=True, type=Path)
    guardian.add_argument("--inference", required=True, type=Path)
    guardian.add_argument("--verifier-image-digest", required=True)
    guardian.add_argument("--output-root", required=True, type=Path)
    guardian.add_argument("--architecture", choices=("amd64", "arm64"), default="amd64")
    guardian.add_argument("--profile", choices=("assistant", "builder", "controller", "researcher"), default="builder")
    guardian.add_argument("--evaluation-regime", choices=sorted(materializer.EVALUATION_REGIMES), default="matched-budget")
    guardian.add_argument("--runtime-condition", choices=("cold-first-request", "warm-neutral-probe"), default="cold-first-request")
    guardian.add_argument("--tuning-materialization-root", required=True, type=Path)
    guardian.add_argument("--tuning-output-root", required=True, type=Path)
    guardian.add_argument("--pair-configuration-path", required=True, type=Path)
    guardian.add_argument("--base-epoch-ms", type=int, default=1786622400000)

    tuning_materialize = sub.add_parser("tuning-materialize")
    tuning_materialize.add_argument("--tuning-root", required=True, type=Path)
    tuning_materialize.add_argument("--model", required=True, type=Path)
    tuning_materialize.add_argument("--inference", required=True, type=Path)
    tuning_materialize.add_argument("--verifier-image-digest", required=True)
    tuning_materialize.add_argument("--output-root", required=True, type=Path)
    tuning_materialize.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    tuning_materialize.add_argument("--architecture", choices=("amd64", "arm64"), default="amd64")
    tuning_materialize.add_argument("--profile", choices=("assistant", "builder", "controller", "researcher"), default="builder")
    tuning_materialize.add_argument("--evaluation-regime", choices=sorted(materializer.EVALUATION_REGIMES), default="matched-budget")
    tuning_materialize.add_argument("--base-epoch-ms", type=int, default=1786622400000)

    validate = sub.add_parser("validate")
    validate.add_argument("--tuning-root", required=True, type=Path)
    validate.add_argument("--freeze", required=True, type=Path)
    validate.add_argument("--receipt", required=True, type=Path)
    validate.add_argument("--heldout-materialization-root", type=Path)

    args = parser.parse_args()
    try:
        if args.command == "split":
            battery_payload = evaluation.read_bytes(
                Path(os.path.abspath(args.battery)), limit=MAX_BATTERY_BYTES, private=True,
            )
            result = split_battery(
                battery_payload,
                Path(os.path.abspath(args.tuning_root)),
                Path(os.path.abspath(args.reveal_root)),
                args.set_identity,
            )
            print(json.dumps({key: value for key, value in result.items() if key not in {"commitment", "tuningRoot", "revealRoot"}},
                             sort_keys=True, separators=(",", ":")))
            return 0
        if args.command == "guardian-materialize":
            model_payload = evaluation.read_bytes(
                Path(os.path.abspath(args.model)), limit=MAX_CORPUS_BYTES, private=True,
            )
            inference_payload = evaluation.read_bytes(
                Path(os.path.abspath(args.inference)), limit=MAX_CORPUS_BYTES, private=True,
            )
            result = guardian_materialize_heldout(
                reveal_root=Path(os.path.abspath(args.reveal_root)),
                tuning_root=Path(os.path.abspath(args.tuning_root)),
                freeze_path=Path(os.path.abspath(args.freeze)),
                receipt_path=Path(os.path.abspath(args.receipt)),
                root=Path(os.path.abspath(args.root)), model_payload=model_payload,
                inference_payload=inference_payload,
                verifier_image_digest=args.verifier_image_digest,
                output_root=Path(os.path.abspath(args.output_root)),
                tuning_materialization_root=Path(os.path.abspath(args.tuning_materialization_root)),
                tuning_output_root=Path(os.path.abspath(args.tuning_output_root)),
                pair_configuration_path=Path(os.path.abspath(args.pair_configuration_path)),
                runtime_condition=args.runtime_condition,
                architecture=args.architecture, base_epoch_ms=args.base_epoch_ms,
                profile=args.profile, evaluation_regime=args.evaluation_regime,
            )
            print(json.dumps({key: value for key, value in result.items() if key != "heldOutMaterialization"},
                             sort_keys=True, separators=(",", ":")))
            return 0
        if args.command == "tuning-materialize":
            model_payload = evaluation.read_bytes(
                Path(os.path.abspath(args.model)), limit=MAX_CORPUS_BYTES, private=True,
            )
            inference_payload = evaluation.read_bytes(
                Path(os.path.abspath(args.inference)), limit=MAX_CORPUS_BYTES, private=True,
            )
            result = sealed_tuning_materialize(
                tuning_root=Path(os.path.abspath(args.tuning_root)),
                root=Path(os.path.abspath(args.root)), model_payload=model_payload,
                inference_payload=inference_payload,
                verifier_image_digest=args.verifier_image_digest,
                output_root=Path(os.path.abspath(args.output_root)),
                architecture=args.architecture, base_epoch_ms=args.base_epoch_ms,
                profile=args.profile, evaluation_regime=args.evaluation_regime,
            )
            print(json.dumps(_tuning_materialize_summary(result), sort_keys=True, separators=(",", ":")))
            return 0
        if args.command == "validate":
            result = validate_post_freeze(
                tuning_root=Path(os.path.abspath(args.tuning_root)),
                freeze_path=Path(os.path.abspath(args.freeze)),
                receipt_path=Path(os.path.abspath(args.receipt)),
                heldout_materialization_root=(
                    Path(os.path.abspath(args.heldout_materialization_root))
                    if args.heldout_materialization_root is not None else None
                ),
            )
            print(json.dumps(result, sort_keys=True, separators=(",", ":")))
            return 0
        raise evaluation.OutcomeError("sealed corpus command is invalid")
    except (evaluation.OutcomeError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"[pixel] ERROR: {exc}", file=os.sys.stderr)
        return 2



if __name__ == "__main__":
    raise SystemExit(main())
