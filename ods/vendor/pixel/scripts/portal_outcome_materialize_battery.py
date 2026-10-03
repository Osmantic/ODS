#!/usr/bin/env python3
"""Materialize the curated comparison battery into exact paired outcome tasks.

The output is private and deterministic for an exact battery, model contract,
inference contract, verifier image digest, architecture, and base timestamp.
No backend is started and no execution authority is granted.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import tarfile
from typing import Any

import portal_outcome_evaluation as evaluation
import portal_outcome_task as outcome_task
import portal_outcome_verifier as verifier_engine


BATTERY_SCHEMA = "https://osmantic.com/pixel/schemas/agent-comparison-task-battery-v1.schema.json"
BATTERY_BOUNDARY = (
    "Reproducible comparison task corpus only. It defines identical work for paired harness evaluation and "
    "grants no execution, provider, external-effect, publication, or acceptance authority."
)
MATERIALIZATION_BOUNDARY = (
    "Private deterministic task materialization only. It grants no model start, inference, tool execution, "
    "provider call, credential, network, external effect, completion, publication, deployment, acceptance, or promotion authority."
)
TASK_RE = re.compile(r"^(?:battery|stress|trial|heldout)-[a-z0-9][a-z0-9-]{2,62}$")
DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
SAFE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9._-]{1,255}$")
TASK_FIELDS = {"taskId", "profile", "axis", "source", "partition", "prompt", "expectReply", "wallSeconds", "workspace", "immutableWorkspace", "scale", "trialProof", "researchFixture", "verify", "rehearsal"}
REQUIRED_TASK_FIELDS = TASK_FIELDS - {"profile", "partition", "immutableWorkspace", "scale", "trialProof", "researchFixture", "rehearsal"}
VERIFY_FIELDS = {"command", "expectStdout"}
SCALE_FIELDS = {"class", "minimumFiles", "minimumBytes", "languages"}
TRIAL_PROOF_FIELDS = {"productJourneyId", "trialJourneyIds", "proofClass"}
REHEARSAL_FIELDS = {"journeyId", "trialJourneyIds", "partition", "proofClass", "fault", "fixture"}
FIXTURE_FIELDS = {"schemaVersion", "operation", "actions", "boundary"}
REHEARSAL_PROOF_CLASS = "deterministic-non-promotional-rehearsal"
FORMAL_PRODUCT_TASK_PROOF_CLASS = "formal-product-path-task"
EXECUTION_PROFILES = frozenset({"assistant", "builder", "controller", "researcher"})
FIXTURE_BOUNDARY = (
    "Deterministic non-promotional comparison rehearsal only; no live provider, credential, network, "
    "external effect, product proof, acceptance, or promotion authority."
)
FIXTURE_TOOL_RELATIVE = Path("scripts/agent_comparison_fixture_tool.py")
CAPABILITIES = [
    "artifact-production", "filesystem-read", "filesystem-write", "local-model", "process-execution", "reasoning",
]
TOOLS = ["debug", "edit", "eval", "hub", "lsp", "read", "search", "shell", "task", "write"]
RESEARCHER_CAPABILITIES = [
    "artifact-production", "filesystem-read", "filesystem-write", "local-model", "process-execution", "public-web", "reasoning",
]
RESEARCHER_TOOLS = ["edit", "public-research", "read", "search", "shell", "write"]
EVALUATION_REGIMES = {
    "matched-budget": {
        "wallMultiplier": 1, "maxIterations": 40, "maxToolCalls": 4000,
        "maxConcurrentSubagents": 1, "modelRequests": 64, "inputTokens": 500000,
        "outputTokens": 65536, "artifactBytes": 16777216, "maxNetworkBytes": 104857600,
        "maxFailures": 8, "noProgressLimit": 4, "maxPids": 1024,
    },
    "maximum-quality": {
        "wallMultiplier": 8, "maxIterations": 128, "maxToolCalls": 32000,
        "maxConcurrentSubagents": 4, "modelRequests": 512, "inputTokens": 4000000,
        "outputTokens": 524288, "artifactBytes": 134217728, "maxNetworkBytes": 838860800,
        "maxFailures": 24, "noProgressLimit": 8, "maxPids": 4096,
    },
}
RESEARCHER_CHECKS = [
    {"assertionId": "inline-citations", "check": "research-inline-citations"},
    {"assertionId": "primary-source-preference", "check": "research-primary-source-preference"},
    {"assertionId": "citation-entailment", "check": "research-citation-entailment"},
    {"assertionId": "label-stale-or-unknown", "check": "research-label-stale-or-unknown"},
    {"assertionId": "inline-provenance", "check": "research-inline-provenance"},
]
PYTHON_INLINE_LAUNCHER = (
    "import base64,sys;n=int(sys.argv.pop(1));s=''.join(sys.argv[1:1+n]);"
    "del sys.argv[1:1+n];exec(compile(base64.b64decode(s),'<pixel-verifier>','exec'))"
)
SAFE_ARG_CHUNK_BYTES = 4096


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"


def effective_partition(task: dict[str, Any]) -> str:
    """Resolve the comparison partition committed for a battery task."""
    return task.get("partition", task.get("rehearsal", {}).get("partition", "tuning"))


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def regime_contract(evaluation_regime: str, wall_seconds: int) -> dict[str, Any]:
    """Return bounded resource mechanics; authority and task scope never vary by regime."""
    if evaluation_regime not in EVALUATION_REGIMES:
        raise evaluation.OutcomeError("battery evaluation regime is invalid")
    evaluation.integer(wall_seconds, 1, 3600, "battery regime wall budget")
    profile = EVALUATION_REGIMES[evaluation_regime]
    return {
        "limits": {
            "maxIterations": profile["maxIterations"], "maxToolCalls": profile["maxToolCalls"],
            "maxConcurrentSubagents": profile["maxConcurrentSubagents"], "maxCpuCores": 4,
            "maxMemoryMiB": 8192, "maxDiskBytes": 10737418240,
            "maxNetworkBytes": profile["maxNetworkBytes"], "maxFailures": profile["maxFailures"],
            "noProgressLimit": profile["noProgressLimit"], "maxPids": profile["maxPids"],
        },
        "budgets": {
            "wallTimeSeconds": wall_seconds * profile["wallMultiplier"], "operatorInterventions": 0,
            "modelRequests": profile["modelRequests"], "inputTokens": profile["inputTokens"],
            "outputTokens": profile["outputTokens"], "artifactBytes": profile["artifactBytes"],
            "externalWrites": 0,
        },
    }


def validate_regime_bindings(
    evaluation_regime: str, admission: dict[str, Any], environment: dict[str, Any], tool_policy: dict[str, Any],
) -> None:
    """Prove a regime label is the exact resource envelope, never an authority alias."""
    if evaluation_regime not in EVALUATION_REGIMES:
        raise evaluation.OutcomeError("battery evaluation regime is invalid")
    multiplier = EVALUATION_REGIMES[evaluation_regime]["wallMultiplier"]
    wall = admission.get("budgets", {}).get("wallTimeSeconds")
    if not isinstance(wall, int) or isinstance(wall, bool) or wall < multiplier or wall % multiplier:
        raise evaluation.OutcomeError("materialized task wall budget differs from its evaluation regime")
    expected = regime_contract(evaluation_regime, wall // multiplier)
    if (
        admission["budgets"] != expected["budgets"] or environment.get("limits") != expected["limits"]
        or tool_policy.get("maximumSubagents") != expected["limits"]["maxConcurrentSubagents"]
        or any(tool_policy.get(field) is not False for field in (
            "hostAccess", "ambientCredentials", "externalEffects", "mergeAuthority", "deployAuthority", "policyMutation",
        ))
        or any(admission.get("authority", {}).values()) or admission["budgets"]["externalWrites"] != 0
    ):
        raise evaluation.OutcomeError("materialized task resources or authority differ from its evaluation regime")


def verifier_argv(command: list[str]) -> list[str]:
    """Compile a battery verifier into the Work command boundary without weakening it.

    Work deliberately rejects control bytes in argv.  Python ``-c`` programs commonly
    contain newlines and tabs, so carry the controller-selected program as bounded base64
    chunks and restore the original user arguments before executing it.  The command is
    still launched directly (never through a shell), and NUL remains impossible.
    """
    if len(command) < 3 or command[0:2] != ["python3", "-c"]:
        raise evaluation.OutcomeError("battery verifier must use an inline python3 program")
    program = command[2].encode("utf-8")
    encoded = base64.b64encode(program).decode("ascii")
    chunks = [encoded[index:index + SAFE_ARG_CHUNK_BYTES] for index in range(0, len(encoded), SAFE_ARG_CHUNK_BYTES)]
    trailing = command[3:]
    argv = ["/usr/bin/python3", "-c", PYTHON_INLINE_LAUNCHER, str(len(chunks)), *chunks, *trailing]
    if (
        len(argv) > 64
        or any(not value or len(value) > SAFE_ARG_CHUNK_BYTES or any(ord(character) < 32 or ord(character) == 127 for character in value) for value in argv)
    ):
        raise evaluation.OutcomeError("battery verifier cannot fit the strict Work argument boundary")
    return argv


def private_write(path: Path, payload: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)


def safe_workspace_path(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 1024 or "\\" in value:
        raise evaluation.OutcomeError("battery workspace path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} or SAFE_COMPONENT_RE.fullmatch(part) is None for part in path.parts):
        raise evaluation.OutcomeError("battery workspace path is invalid")
    return path.as_posix()


def validate_battery(payload: bytes) -> dict[str, Any]:
    value = evaluation.parse_json(payload, "agent comparison task battery")
    value = evaluation.exact_fields(
        value, {"$schema", "schemaVersion", "operation", "provenance", "tasks", "boundary"},
        "agent comparison task battery",
    )
    if (
        value["$schema"] != BATTERY_SCHEMA or value["schemaVersion"] != 1
        or value["operation"] != "pixel-agent-comparison-task-battery"
        or not isinstance(value["provenance"], str) or not value["provenance"]
        or value["boundary"] != BATTERY_BOUNDARY
    ):
        raise evaluation.OutcomeError("agent comparison task battery identity is invalid")
    tasks = value["tasks"]
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= 500:
        raise evaluation.OutcomeError("agent comparison task battery size is invalid")
    try:
        trial_corpus = json.loads((Path(__file__).resolve().parents[1] / "security-evals/portal-user-trial/trial-journeys-v1.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise evaluation.OutcomeError("sanitized user-trial journey corpus is unavailable") from exc
    trial_index = {item.get("id"): item for item in trial_corpus.get("journeys", []) if isinstance(item, dict)}
    if not trial_index or None in trial_index or len(trial_index) != len(trial_corpus.get("journeys", [])):
        raise evaluation.OutcomeError("sanitized user-trial journey corpus has no journeys")
    try:
        product_corpus = json.loads((Path(__file__).resolve().parents[1] / "security-evals/portal-user-journeys/corpus-v1.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise evaluation.OutcomeError("portal product journey corpus is unavailable") from exc
    product_index = {item.get("id"): item for item in product_corpus.get("journeys", []) if isinstance(item, dict)}
    if not product_index or None in product_index or len(product_index) != len(product_corpus.get("journeys", [])):
        raise evaluation.OutcomeError("portal product journey corpus has no unique journeys")
    seen: set[str] = set()
    for index, task in enumerate(tasks):
        if not isinstance(task, dict) or set(task) - TASK_FIELDS or not REQUIRED_TASK_FIELDS.issubset(task):
            raise evaluation.OutcomeError(f"battery task {index} fields are invalid")
        if not isinstance(task["taskId"], str) or TASK_RE.fullmatch(task["taskId"]) is None or task["taskId"] in seen:
            raise evaluation.OutcomeError("battery task identity is invalid or duplicated")
        seen.add(task["taskId"])
        execution_profile = task.get("profile", "builder")
        if execution_profile not in EXECUTION_PROFILES or task.get("rehearsal") is not None and "profile" not in task:
            raise evaluation.OutcomeError("battery task execution profile is missing or invalid")
        if task.get("partition", "tuning") not in {"tuning", "held-out"}:
            raise evaluation.OutcomeError("battery task partition is invalid")
        if (
            not isinstance(task["axis"], str) or not 1 <= len(task["axis"]) <= 64
            or not isinstance(task["source"], str) or not 1 <= len(task["source"]) <= 128
            or not isinstance(task["prompt"], str) or not 1 <= len(task["prompt"].encode("utf-8")) <= 8000
            or task["expectReply"] is not None and (
                not isinstance(task["expectReply"], str) or not 1 <= len(task["expectReply"].encode("utf-8")) <= 65536
            )
        ):
            raise evaluation.OutcomeError("battery task text is invalid")
        evaluation.integer(task["wallSeconds"], 1, 3600, "battery task wall budget")
        workspace = task["workspace"]
        if not isinstance(workspace, dict) or not 1 <= len(workspace) <= 1000:
            raise evaluation.OutcomeError("battery workspace is invalid")
        total = 0
        paths: set[str] = set()
        for relative, text in workspace.items():
            normalized = safe_workspace_path(relative)
            if normalized in paths or not isinstance(text, str):
                raise evaluation.OutcomeError("battery workspace entry is invalid or duplicated")
            paths.add(normalized)
            total += len(text.encode("utf-8"))
        if total > 64 * 1024 * 1024:
            raise evaluation.OutcomeError("battery workspace is oversized")
        immutable_workspace = task.get("immutableWorkspace", [])
        if (
            not isinstance(immutable_workspace, list) or len(immutable_workspace) != len(set(immutable_workspace))
            or any(safe_workspace_path(item) not in paths for item in immutable_workspace)
        ):
            raise evaluation.OutcomeError("battery immutable workspace contract is invalid")
        scale = task.get("scale")
        if scale is not None:
            scale = evaluation.exact_fields(scale, SCALE_FIELDS, "battery task scale")
            languages = scale["languages"]
            if (
                scale["class"] != "repository"
                or not isinstance(scale["minimumFiles"], int) or not 5 <= scale["minimumFiles"] <= 1000
                or not isinstance(scale["minimumBytes"], int) or not 1000 <= scale["minimumBytes"] <= 64 * 1024 * 1024
                or not isinstance(languages, list) or not 1 <= len(languages) <= 8
                or len(set(languages)) != len(languages)
                or any(language not in {"python", "javascript", "typescript", "shell", "json", "yaml", "markdown"} for language in languages)
                or len(workspace) < scale["minimumFiles"] or total < scale["minimumBytes"]
            ):
                raise evaluation.OutcomeError("battery task repository scale is invalid or overstated")
        if execution_profile == "researcher":
            if task["verify"] is not None:
                raise evaluation.OutcomeError("Researcher battery tasks must use the product citation verifier, not a workspace command")
        else:
            verify = evaluation.exact_fields(task["verify"], VERIFY_FIELDS, "battery task verifier")
            command = verify["command"]
            if (
                not isinstance(command, list) or not 2 <= len(command) <= 64 or command[0] != "python3"
                or any(not isinstance(arg, str) or not 1 <= len(arg) <= 65536 or "\x00" in arg for arg in command)
                or not isinstance(verify["expectStdout"], str) or len(verify["expectStdout"].encode("utf-8")) > 65536
            ):
                raise evaluation.OutcomeError("battery verifier command is invalid")
        rehearsal = task.get("rehearsal")
        if rehearsal is not None:
            if "partition" in task:
                raise evaluation.OutcomeError("battery rehearsal partition must not be duplicated at task level")
            rehearsal = evaluation.exact_fields(rehearsal, REHEARSAL_FIELDS, "battery rehearsal")
            if (
                not isinstance(rehearsal["journeyId"], str)
                or not isinstance(rehearsal["trialJourneyIds"], list)
                or not 0 <= len(rehearsal["trialJourneyIds"]) <= 8
                or len(set(rehearsal["trialJourneyIds"])) != len(rehearsal["trialJourneyIds"])
                or any(not isinstance(item, str) or item not in trial_index for item in rehearsal["trialJourneyIds"])
                or rehearsal["partition"] not in {"tuning", "held-out"}
                or rehearsal["proofClass"] != REHEARSAL_PROOF_CLASS
                or rehearsal["fault"] is not None and not isinstance(rehearsal["fault"], str)
            ):
                raise evaluation.OutcomeError("battery rehearsal identity is invalid")
            fixture = evaluation.exact_fields(rehearsal["fixture"], FIXTURE_FIELDS, "battery rehearsal fixture")
            if (
                fixture["schemaVersion"] != 1 or fixture["operation"] != "pixel-agent-comparison-fixture"
                or fixture["boundary"] != FIXTURE_BOUNDARY or not isinstance(fixture["actions"], dict)
                or not 1 <= len(fixture["actions"]) <= 32
            ):
                raise evaluation.OutcomeError("battery rehearsal fixture is invalid")
            journey, _corpus_sha, _journey_sha = evaluation.corpus_contract(Path(__file__).resolve().parents[1], rehearsal["journeyId"])
            if rehearsal["fault"] is not None and rehearsal["fault"] not in journey.get("faults", []):
                raise evaluation.OutcomeError("battery rehearsal fault is not declared by its journey")
            if task["source"] not in {"sanitized-user-trial-rehearsal", "realistic-held-out-rehearsal"}:
                raise evaluation.OutcomeError("battery rehearsal source is not explicitly non-promotional")
            if (rehearsal["partition"] == "held-out") != (task["source"] == "realistic-held-out-rehearsal"):
                raise evaluation.OutcomeError("battery rehearsal source and partition disagree")
        elif task["source"] not in {"curated-workflow", "stress-axis"}:
            raise evaluation.OutcomeError("non-rehearsal battery task has a rehearsal source")
        trial_proof = task.get("trialProof")
        if trial_proof is not None:
            if rehearsal is not None or "profile" not in task:
                raise evaluation.OutcomeError("formal product task cannot be a rehearsal or omit its profile")
            trial_proof = evaluation.exact_fields(trial_proof, TRIAL_PROOF_FIELDS, "battery formal product task")
            trial_journey_ids = trial_proof["trialJourneyIds"]
            product_journey = product_index.get(trial_proof["productJourneyId"])
            if (
                trial_proof["proofClass"] != FORMAL_PRODUCT_TASK_PROOF_CLASS
                or not isinstance(trial_journey_ids, list) or not 1 <= len(trial_journey_ids) <= 8
                or len(set(trial_journey_ids)) != len(trial_journey_ids)
                or product_journey is None or product_journey.get("profile") != execution_profile
                or any(trial_id not in trial_index or trial_index[trial_id].get("profile") != execution_profile for trial_id in trial_journey_ids)
            ):
                raise evaluation.OutcomeError("battery formal product task route or trial binding is invalid")
        research_fixture = task.get("researchFixture")
        if research_fixture is not None:
            if execution_profile != "researcher" or trial_proof is None or rehearsal is not None:
                raise evaluation.OutcomeError("formal research fixture requires one exact Researcher product task")
            outcome_task.validate_research_fixture(canonical_json(research_fixture))
        if execution_profile == "researcher" and (rehearsal is None) == (research_fixture is None):
            raise evaluation.OutcomeError("Researcher battery task must bind exactly one rehearsal or formal research fixture")
    return value


def deterministic_tar(workspace: dict[str, str]) -> bytes:
    files = {safe_workspace_path(name): text.encode("utf-8") for name, text in workspace.items()}
    directories: set[str] = set()
    for relative in files:
        parent = PurePosixPath(relative).parent
        while str(parent) != ".":
            directories.add(parent.as_posix())
            parent = parent.parent
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for relative in sorted(directories):
            info = tarfile.TarInfo(f"{relative}/")
            info.type = tarfile.DIRTYPE
            info.mode, info.uid, info.gid, info.mtime = 0o755, 0, 0, 0
            info.uname = info.gname = ""
            archive.addfile(info)
        for relative in sorted(files):
            payload = files[relative]
            info = tarfile.TarInfo(relative)
            info.size = len(payload)
            info.mode, info.uid, info.gid, info.mtime = 0o644, 0, 0, 0
            info.uname = info.gname = ""
            archive.addfile(info, io.BytesIO(payload))
    return output.getvalue()


def reference(name: str, payload: bytes, media_type: str) -> dict[str, Any]:
    return {"relativePath": name, "sha256": digest(payload), "bytes": len(payload), "mediaType": media_type}


def protected_prefixes(workspace: dict[str, str], immutable_workspace: list[str] | None = None) -> list[str]:
    prefixes = {"source/__pixel_inert__/"}
    for relative in immutable_workspace or []:
        normalized = safe_workspace_path(relative)
        if normalized not in workspace:
            raise evaluation.OutcomeError("battery immutable workspace path is absent")
        prefixes.add(f"source/{normalized}")
    for relative in workspace:
        normalized = safe_workspace_path(relative)
        if normalized == ".env":
            prefixes.add("source/.env")
        if normalized.startswith("secrets/"):
            prefixes.add("source/secrets/")
        if normalized.startswith(".pixel-scenario/"):
            prefixes.add("source/.pixel-scenario/")
    return sorted(prefixes)


def task_workspace(root: Path, battery_task: dict[str, Any]) -> dict[str, str]:
    workspace = dict(battery_task["workspace"])
    rehearsal = battery_task.get("rehearsal")
    if rehearsal is None or battery_task.get("profile", "builder") == "researcher":
        return workspace
    tool_path = root / FIXTURE_TOOL_RELATIVE
    try:
        tool_text = tool_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise evaluation.OutcomeError("comparison rehearsal fixture tool is unavailable") from exc
    injected = {
        ".pixel-scenario/tool.py": tool_text,
        ".pixel-scenario/fixture.json": canonical_json(rehearsal["fixture"]).decode("utf-8"),
    }
    if set(injected) & set(workspace):
        raise evaluation.OutcomeError("battery rehearsal attempts to replace its controller fixture")
    workspace.update(injected)
    return workspace


def materialized_research_fixture(rehearsal: dict[str, Any]) -> dict[str, Any]:
    fixture = rehearsal["fixture"]
    actions = fixture["actions"]
    search = actions.get("research.search")
    if not isinstance(search, dict) or set(search) - {"response", "once"} or search.get("once") is not True:
        raise evaluation.OutcomeError("Researcher rehearsal requires one exact frozen search action")
    response = search.get("response")
    if not isinstance(response, dict) or set(response) != {"observedAt", "results"}:
        raise evaluation.OutcomeError("Researcher rehearsal search response is invalid")
    evaluation.timestamp(response["observedAt"], "Researcher rehearsal observation time")
    results = response["results"]
    if (
        not isinstance(results, list) or not 1 <= len(results) <= 20 or len(set(results)) != len(results)
        or any(not isinstance(item, str) or re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", item) is None for item in results)
    ):
        raise evaluation.OutcomeError("Researcher rehearsal result ladder is invalid")
    sources = []
    expected_actions = {"research.search", *(f"research.fetch.{item}" for item in results)}
    if set(actions) != expected_actions:
        raise evaluation.OutcomeError("Researcher rehearsal actions differ from the exact retrieval ladder")
    for fixture_source_id in results:
        action = actions[f"research.fetch.{fixture_source_id}"]
        if not isinstance(action, dict) or set(action) - {"response", "once", "exitCode", "stderr", "committed"}:
            raise evaluation.OutcomeError("Researcher rehearsal retrieval action is invalid")
        if action.get("once") is not True or not isinstance(action.get("response"), dict):
            raise evaluation.OutcomeError("Researcher rehearsal retrieval is not exactly once")
        result = action["response"]
        title = fixture_source_id.replace("-", " ").replace("_", " ").title()
        if action.get("exitCode", 0) != 0 or "error" in result:
            if action.get("committed") is not False or set(result) != {"error"}:
                raise evaluation.OutcomeError("Researcher rehearsal rejected retrieval is ambiguous")
            retrieval = {"status": "rejected", "reason": "network" if result["error"] == "source-unavailable" else "policy"}
            quality = "primary" if "primary" in fixture_source_id else "unknown"
            published = None
            snippet = "The admitted frozen corpus records this source as unavailable."
        else:
            if set(result) - {"sourceId", "quality", "published", "text"} or set(result) < {"sourceId", "quality", "text"}:
                raise evaluation.OutcomeError("Researcher rehearsal fetched retrieval is invalid")
            if result["sourceId"] != fixture_source_id:
                raise evaluation.OutcomeError("Researcher rehearsal source identity drifted")
            quality = result["quality"]
            published = result.get("published")
            content = result["text"]
            if not isinstance(content, str) or not content:
                raise evaluation.OutcomeError("Researcher rehearsal source content is empty")
            retrieval = {"status": "fetched", "content": content}
            snippet = content[:512]
        sources.append({
            "fixtureSourceId": fixture_source_id, "sourceType": "web", "title": title,
            "snippet": snippet, "quality": quality, "publishedDate": published, "retrieval": retrieval,
        })
    value = {
        "$schema": outcome_task.RESEARCH_FIXTURE_SCHEMA, "schemaVersion": 1,
        "operation": "pixel-portal-outcome-research-fixture", "observedAt": response["observedAt"],
        "sources": sources, "authority": {field: False for field in outcome_task.RESEARCH_FIXTURE_AUTHORITY_FIELDS},
        "boundary": outcome_task.RESEARCH_FIXTURE_BOUNDARY,
    }
    outcome_task.validate_research_fixture(canonical_json(value))
    return value


def materialize(
    *, root: Path, battery_payload: bytes, model_payload: bytes, inference_payload: bytes,
    verifier_image_digest: str, output_root: Path, architecture: str = "amd64",
    base_epoch_ms: int = 1786622400000, profile: str = "builder",
    evaluation_regime: str = "matched-budget", partition: str | None = None,
    mode: str = "combined", sealed_commitment_sha256: str | None = None,
) -> dict[str, Any]:
    battery = validate_battery(battery_payload)
    outcome_task.validate_model_contract(model_payload)
    outcome_task.validate_inference_contract(inference_payload)
    model = evaluation.parse_json(model_payload, "battery shared model contract")
    inference = evaluation.parse_json(inference_payload, "battery shared inference contract")
    if (
        model.get("modelId") != "DeepSeek-V4-Flash-0731"
        or model.get("runtime", {}).get("implementation") != "vllm"
        or model.get("runtime", {}).get("protocol") != "openai-chat-completions-v1"
        or inference.get("request", {}).get("wireApi") != "openai-chat-completions"
    ):
        raise evaluation.OutcomeError("paired battery requires the exact DSV4 Flash 0731 vLLM contract")
    if DIGEST_RE.fullmatch(verifier_image_digest or "") is None:
        raise evaluation.OutcomeError("battery verifier image digest is invalid")
    if architecture not in {"amd64", "arm64"}:
        raise evaluation.OutcomeError("battery architecture is invalid")
    if profile not in EXECUTION_PROFILES:
        raise evaluation.OutcomeError("battery materialization profile is invalid")
    if evaluation_regime not in EVALUATION_REGIMES:
        raise evaluation.OutcomeError("battery evaluation regime is invalid")
    evaluation.integer(base_epoch_ms, 1000000000000, 9999999999999, "battery base epoch")
    if mode not in {"combined", "sealed"}:
        raise evaluation.OutcomeError("battery materialization mode is invalid")
    if partition is not None and partition not in {"tuning", "held-out"}:
        raise evaluation.OutcomeError("battery materialization partition is invalid")
    if mode == "sealed":
        if partition is None:
            raise evaluation.OutcomeError("sealed battery materialization requires an explicit partition")
        if not isinstance(sealed_commitment_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", sealed_commitment_sha256):
            raise evaluation.OutcomeError("sealed battery materialization commitment digest is invalid")
    selected_tasks = [
        task for task in battery["tasks"]
        if task.get("profile", "builder") == profile
        and (partition is None or effective_partition(task) == partition)
    ]
    if not selected_tasks:
        raise evaluation.OutcomeError("battery has no tasks for the selected materialization partition and profile")
    selected_partitions = {effective_partition(task) for task in selected_tasks}
    if partition is None:
        if selected_partitions != {"tuning", "held-out"}:
            raise evaluation.OutcomeError(
                f"battery materialization profile {profile!r} must include at least one tuning and one held-out task; "
                f"selected partitions are {sorted(selected_partitions)}",
            )
    elif selected_partitions != {partition}:
        raise evaluation.OutcomeError(
            f"battery materialization partition {partition!r} selected unexpected tasks; "
            f"selected partitions are {sorted(selected_partitions)}",
        )
    output_root.mkdir(mode=0o700, parents=False, exist_ok=False)
    inventory = []
    for index, battery_task in enumerate(selected_tasks):
        execution_profile = battery_task.get("profile", "builder")
        rehearsal = battery_task.get("rehearsal")
        trial_proof = battery_task.get("trialProof")
        target_profile = execution_profile
        if rehearsal is not None:
            target_journey, _corpus_sha, _journey_sha = evaluation.corpus_contract(root, rehearsal["journeyId"])
            target_profile = target_journey["profile"]
        elif trial_proof is not None:
            target_journey, _corpus_sha, _journey_sha = evaluation.corpus_contract(root, trial_proof["productJourneyId"])
            target_profile = target_journey["profile"]
        profile_fidelity = "exact" if execution_profile == target_profile else "surrogate-rehearsal"
        regime = regime_contract(evaluation_regime, battery_task["wallSeconds"])
        task_dir = output_root / battery_task["taskId"]
        task_dir.mkdir(mode=0o700)
        request_payload = battery_task["prompt"].encode("utf-8") + b"\n"
        workspace = task_workspace(root, battery_task)
        source_payload = deterministic_tar(workspace)
        research_fixture_value = battery_task.get("researchFixture")
        research_fixture_payload = (
            canonical_json(materialized_research_fixture(rehearsal) if rehearsal is not None else research_fixture_value)
            if execution_profile == "researcher" else None
        )
        final_reply = None
        if execution_profile == "researcher":
            if target_profile != "researcher" or (rehearsal is None) == (trial_proof is None):
                raise evaluation.OutcomeError("Researcher battery task must bind an exact Researcher product journey")
            journey_id = rehearsal["journeyId"] if rehearsal is not None else trial_proof["productJourneyId"]
            journey, _corpus_sha, _journey_sha = evaluation.corpus_contract(root, journey_id)
            checks = [dict(item) for item in RESEARCHER_CHECKS]
        else:
            if execution_profile == "assistant":
                if rehearsal is not None or trial_proof is not None:
                    if target_profile != "assistant":
                        raise evaluation.OutcomeError("Assistant battery task differs from its exact Assistant product journey")
                    journey_id = rehearsal["journeyId"] if rehearsal is not None else trial_proof["productJourneyId"]
                else:
                    journey_id = "assistant-harness-challenge"
            else:
                journey_id = trial_proof["productJourneyId"] if trial_proof is not None else "real-harness-challenge"
            journey, _corpus_sha, _journey_sha = evaluation.corpus_contract(root, journey_id)
            checks = ([
                {"assertionId": "artifact-openable", "check": "artifact-parses"},
                {"assertionId": "no-scripted-answer-credit", "check": "workspace-verification-passes"},
                {"assertionId": "independent-completion", "check": "workspace-verification-passes"},
                {"assertionId": "evidence-exact-source", "check": "evidence-binds-source-snapshot"},
            ] if execution_profile == "controller" else [
                {"assertionId": "real-backend-required", "check": "command-exit-zero"},
                {"assertionId": "real-tool-outcome", "check": "workspace-verification-passes"},
                {"assertionId": "no-scripted-answer-credit", "check": "workspace-verification-passes"},
                {"assertionId": "independent-completion", "check": "workspace-verification-passes"},
                {"assertionId": "evidence-exact-source", "check": "evidence-binds-source-snapshot"},
            ])
            if battery_task["expectReply"] is not None:
                reply = battery_task["expectReply"].encode("utf-8")
                final_reply = {"sha256": digest(reply), "bytes": len(reply), "normalization": "exact-utf8"}
                reply_index = next(index for index, item in enumerate(checks) if item["assertionId"] == "no-scripted-answer-credit")
                checks[reply_index] = {"assertionId": "no-scripted-answer-credit", "check": "final-reply-exact"}
        semantic_timeout = min(180, battery_task["wallSeconds"])
        workspace_verification = None if execution_profile == "researcher" else {
            "mode": "independent",
            "checks": [
                {"id": "patch-boundary", "kind": "patch-integrity", "criterionIndexes": [0]},
                {
                    "id": "semantic", "kind": "command", "criterionIndexes": [0],
                    "workingDirectory": "source", "argv": verifier_argv(battery_task["verify"]["command"]),
                    "timeoutSeconds": semantic_timeout, "maxOutputBytes": 262144,
                },
            ],
            "immutablePathPrefixes": protected_prefixes(workspace, battery_task.get("immutableWorkspace")),
            "maxRuntimeSeconds": semantic_timeout, "maxOutputBytes": 262144, "network": "none",
            "boundary": verifier_engine.WORKSPACE_BOUNDARY,
        }
        verifier = {
            "$schema": verifier_engine.VERIFIER_SCHEMA, "operation": verifier_engine.VERIFIER_OPERATION,
            "schemaVersion": 1, "journeyId": journey_id,
            "acceptanceCriteria": [journey["objective"]],
            "checks": checks, "forbiddenPhrases": [], "finalReply": final_reply,
            "workspaceVerification": workspace_verification,
            "boundary": verifier_engine.VERIFIER_BOUNDARY,
        }
        verifier_payload = canonical_json(verifier)
        environment = {
            "$schema": outcome_task.ENVIRONMENT_SCHEMA, "schemaVersion": 1,
            "operation": "pixel-portal-outcome-environment",
            "platform": {"operatingSystem": "linux", "architecture": architecture, "distribution": "debian-12", "locale": "C.UTF-8", "timeZone": "UTC"},
            "isolation": {"workspace": "fresh-disposable-read-write", "controlFiles": "inert", "directNetwork": False, "packageInstallation": False, "inheritedEnvironment": False, "inheritedFileDescriptors": False, "crossRunState": False},
            "limits": dict(regime["limits"]),
            "verifier": {"imageDigest": verifier_image_digest, "allowedExecutables": ["/usr/bin/python3"], "maxChecks": 16, "maxRuntimeSeconds": semantic_timeout, "maxOutputBytes": 262144, "network": "none"},
            "authority": {field: False for field in outcome_task.ENVIRONMENT_AUTHORITY_FIELDS},
            "boundary": outcome_task.ENVIRONMENT_BOUNDARY,
        }
        environment_payload = canonical_json(environment)
        tools = {
            "$schema": outcome_task.TOOL_POLICY_SCHEMA, "schemaVersion": 1,
            "operation": "pixel-portal-outcome-tool-policy", "workspace": "disposable-read-write",
            "tools": RESEARCHER_TOOLS if execution_profile == "researcher" else TOOLS,
            "brokeredServices": ["local-model", "public-research"] if execution_profile == "researcher" else ["local-model"],
            "maximumSubagents": regime["limits"]["maxConcurrentSubagents"],
            "hostAccess": False, "ambientCredentials": False, "externalEffects": False,
            "mergeAuthority": False, "deployAuthority": False, "policyMutation": False,
            "boundary": outcome_task.TOOL_POLICY_BOUNDARY,
        }
        tools_payload = canonical_json(tools)
        names = {
            "request.txt": request_payload, "source.tar": source_payload,
            "environment.json": environment_payload, "tools.json": tools_payload,
            "verifier.json": verifier_payload, "model.json": model_payload, "inference.json": inference_payload,
        }
        if research_fixture_payload is not None:
            names["research-fixture.json"] = research_fixture_payload
        for name, payload in names.items():
            private_write(task_dir / name, payload)
        epoch_ms = base_epoch_ms + index
        suffix = digest(battery_task["taskId"].encode("utf-8"))[:12]
        created_seconds = index % 60
        task = {
            "$schema": outcome_task.TASK_SCHEMA, "schemaVersion": 1, "operation": "pixel-portal-outcome-task",
            "taskId": f"outcometask-{epoch_ms:013d}-{suffix}", "createdAt": f"2026-08-13T12:00:{created_seconds:02d}Z",
            "journeyId": journey_id, "comparisonLane": "same-model-harness",
            "profile": execution_profile, "dataClass": journey["dataClass"],
            "effectBoundary": journey["effect"],
            "scenario": {"kind": "baseline", "fault": None, "seedSha256": digest(canonical_json(battery_task))},
            "bindings": {
                "userRequest": reference("request.txt", request_payload, "text/plain"),
                "sourceSnapshot": reference("source.tar", source_payload, "application/x-tar"),
                "environment": reference("environment.json", environment_payload, "application/json"),
                "toolPolicy": reference("tools.json", tools_payload, "application/json"),
                "verifier": reference("verifier.json", verifier_payload, "application/json"),
                "sharedModelContract": reference("model.json", model_payload, "application/json"),
                "sharedInferenceContract": reference("inference.json", inference_payload, "application/json"),
                "sanitizationEvidence": None,
                "researchFixture": reference("research-fixture.json", research_fixture_payload, "application/json") if research_fixture_payload is not None else None,
            },
            "capabilities": RESEARCHER_CAPABILITIES if execution_profile == "researcher" else CAPABILITIES,
            "dataRoute": "brokered-public" if execution_profile == "researcher" else "local-only",
            "budgets": dict(regime["budgets"]),
            "authority": {field: False for field in outcome_task.AUTHORITY_FIELDS}, "boundary": outcome_task.TASK_BOUNDARY,
        }
        task_payload = canonical_json(task)
        private_write(task_dir / "task.json", task_payload)
        admission = outcome_task.admit_task(root, task_dir / "task.json")
        validate_regime_bindings(evaluation_regime, admission, environment, tools)
        inventory.append({
            "batteryTaskId": battery_task["taskId"], "axis": battery_task["axis"],
            "evaluationRegime": evaluation_regime,
            "executionProfile": execution_profile, "targetProfile": target_profile,
            "profileFidelity": profile_fidelity,
            "partition": effective_partition(battery_task),
            "proofClass": battery_task.get("rehearsal", {}).get("proofClass", battery_task.get("trialProof", {}).get("proofClass", "real-disposable-workspace")),
            "scaleClass": battery_task.get("scale", {}).get("class", "micro"),
            "workspaceFiles": len(workspace),
            "workspaceBytes": sum(len(text.encode("utf-8")) for text in workspace.values()),
            "workspaceLanguages": battery_task.get("scale", {}).get("languages", []),
            "rehearsalJourneyId": battery_task.get("rehearsal", {}).get("journeyId"),
            "formalProductJourneyId": battery_task.get("trialProof", {}).get("productJourneyId"),
            "trialJourneyIds": battery_task.get("rehearsal", {}).get("trialJourneyIds", battery_task.get("trialProof", {}).get("trialJourneyIds", [])),
            "rehearsalFault": battery_task.get("rehearsal", {}).get("fault"),
            "taskRelativePath": f"{battery_task['taskId']}/task.json", "taskSha256": digest(task_payload),
            "taskAdmissionSha256": evaluation.sha256(evaluation.canonical(admission)), "sourceSnapshotSha256": digest(source_payload),
            "verifierSha256": digest(verifier_payload),
            "researchFixtureSha256": digest(research_fixture_payload) if research_fixture_payload is not None else None,
            "expectedReplyRequired": final_reply is not None,
        })
    materialization = {
        "schemaVersion": 1, "operation": "pixel-portal-outcome-battery-materialization",
        "batterySha256": digest(battery_payload), "modelContractSha256": digest(model_payload),
        "inferenceContractSha256": digest(inference_payload), "verifierImageDigest": verifier_image_digest,
        "architecture": architecture, "profile": profile, "evaluationRegime": evaluation_regime,
        "mode": mode, "tasks": inventory, "boundary": MATERIALIZATION_BOUNDARY,
    }
    if mode == "sealed":
        materialization["sealedCommitmentSha256"] = sealed_commitment_sha256
    materialization_payload = canonical_json(materialization)
    private_write(output_root / "materialization.json", materialization_payload)
    return materialization


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--battery", required=True, type=Path)
    parser.add_argument("--model-contract", required=True, type=Path)
    parser.add_argument("--inference-contract", required=True, type=Path)
    parser.add_argument("--verifier-image-digest", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--architecture", choices=["amd64", "arm64"], default="amd64")
    parser.add_argument("--profile", choices=sorted(EXECUTION_PROFILES), default="builder")
    parser.add_argument("--evaluation-regime", choices=sorted(EVALUATION_REGIMES), default="matched-budget")
    parser.add_argument("--partition", choices=["tuning", "held-out"], default=None)
    parser.add_argument("--mode", choices=["combined", "sealed"], default="combined")
    parser.add_argument("--sealed-commitment", default=None)
    args = parser.parse_args()
    value = materialize(
        root=args.root.resolve(strict=True), battery_payload=args.battery.read_bytes(),
        model_payload=args.model_contract.read_bytes(), inference_payload=args.inference_contract.read_bytes(),
        verifier_image_digest=args.verifier_image_digest, output_root=args.output.resolve(),
        architecture=args.architecture, profile=args.profile, evaluation_regime=args.evaluation_regime,
        partition=args.partition, mode=args.mode, sealed_commitment_sha256=args.sealed_commitment,
    )
    print(json.dumps(value, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
